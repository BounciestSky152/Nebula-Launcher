"""Launching games, tracking play sessions and post-session hooks (cloud saves, achievements)."""
from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, QTimer

from nebula import paths
from nebula.core import process, tasks
from nebula.core.utils import xdg_open
from nebula.integrations.base import CLOUD_SAVES

log = logging.getLogger(__name__)

DELEGATED = ("Steam client", "Lutris", "Heroic Games Launcher")
MIN_SESSION = 20  # seconds


@dataclass
class RunState:
    uid: str
    key: str
    title: str
    start: float
    proc: subprocess.Popen | None = None
    seen: float = 0.0
    grace_until: float = 0.0
    external: bool = False
    pids: list = field(default_factory=list)


class GameLauncher(QObject):
    def __init__(self, services):
        super().__init__()
        self.s = services
        self.running: dict[str, RunState] = {}
        self.preparing: dict[str, str] = {}
        self.conflict_resolver = None  # set by the UI: fn(game, result, callback(choice))
        self._scanning = False
        self._suppress: dict[str, float] = {}
        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.poll)
        self.timer.start()

    @property
    def bus(self):
        return self.s.bus

    def is_running(self, uid: str) -> bool:
        return uid in self.running

    def entry_running(self, entry) -> RunState | None:
        for g in entry.games:
            if g.uid in self.running:
                return self.running[g.uid]
        return None

    def entry_preparing(self, entry) -> str:
        for g in entry.games:
            if g.uid in self.preparing:
                return self.preparing[g.uid]
        return ""

    # ------------------------------------------------------------ launch
    def launch(self, uid: str, task_id: str = "", cloud_mode: str = "auto") -> None:
        game = self.s.library.game(uid)
        if game is None:
            return
        if uid in self.running:
            self.bus.notify.emit(game.title, "The game is already running")
            return
        if uid in self.preparing:
            return
        self._set_preparing(uid, "Preparing…")
        tasks.run_async(self._prepare, game, task_id, cloud_mode,
                        on_done=lambda res: self._prepared(game, task_id, res),
                        on_error=lambda exc: self._failed(game, exc))

    def _set_preparing(self, uid: str, text: str) -> None:
        if text:
            self.preparing[uid] = text
        else:
            self.preparing.pop(uid, None)
        self.bus.game_preparing.emit(uid, text)

    def _cloud_enabled(self, game, integ) -> bool:
        if not integ.supports(CLOUD_SAVES) or not self.s.settings.get("cloud.enabled"):
            return False
        cfg = self.s.db.get_config(game.uid)
        return cfg.get("cloud_saves", True) is not False

    def _prepare(self, game, task_id: str, cloud_mode: str):
        integ = self.s.integration(game.source)

        def status(text: str):
            tasks.on_main(lambda: self._set_preparing(game.uid, text))

        integ.before_launch(game, status)
        if cloud_mode != "skip" and self._cloud_enabled(game, integ) and self.s.settings.get("cloud.sync_on_launch"):
            status("Syncing cloud saves…")
            res = integ.cloud_sync(game, cloud_mode, "launch")
            if res.action == "conflict":
                return ("conflict", res)
            if res.action == "error":
                tasks.on_main(lambda: self.bus.error.emit("Cloud saves", res.message))
        status("Starting…")
        return ("ok", integ.prepare_launch(game, task_id))

    def _prepared(self, game, task_id: str, result) -> None:
        kind, payload = result
        if kind == "conflict":
            def resume(choice: str):
                self._set_preparing(game.uid, "")
                if choice in ("upload", "download", "skip"):
                    self.launch(game.uid, task_id, cloud_mode=choice)

            if self.conflict_resolver:
                self.conflict_resolver(game, payload, resume)
            else:
                resume("skip")
            return
        try:
            self._spawn(game, payload)
        except Exception as exc:  # noqa: BLE001
            self._failed(game, exc)

    def _failed(self, game, exc: BaseException) -> None:
        self._set_preparing(game.uid, "")
        log.error("Launching %s failed: %s", game.title, exc)
        self.bus.error.emit(f"Could not start {game.title}", str(exc))

    def log_file(self, uid: str):
        return paths.logs_dir() / (uid.replace(":", "_").replace("/", "_") + ".log")

    def _spawn(self, game, spec) -> None:
        proc = None
        if spec.uri:
            if not xdg_open(spec.uri):
                raise RuntimeError(f"Could not open {spec.uri}")
        else:
            logf = open(self.log_file(game.uid), "w")
            logf.write(f"# Nebula launch log - {time.ctime()}\n# {spec.description}\n# cwd: {spec.cwd}\n")
            env_line = " ".join(f"{k}={shlex.quote(v)}" for k, v in sorted(spec.env.items()))
            logf.write(f"# {env_line} {shlex.join(spec.argv)}\n\n")
            logf.flush()
            env = os.environ.copy()
            env.update(spec.env)
            cwd = spec.cwd if spec.cwd and os.path.isdir(spec.cwd) else None
            try:
                proc = subprocess.Popen(spec.argv, cwd=cwd, env=env, stdout=logf, stderr=subprocess.STDOUT,
                                        stdin=subprocess.DEVNULL, start_new_session=True)
            except FileNotFoundError as exc:
                raise RuntimeError(f"Executable not found: {exc.filename}") from None
            finally:
                logf.close()
        now = time.time()
        delegated = bool(spec.uri) or spec.description in DELEGATED
        entry = self.s.library.entry_for_uid(game.uid)
        # A platform client (Steam, Lutris...) may outlive the game, so delegated launches are
        # tracked purely by process detection instead of by the process we spawned.
        state = RunState(uid=game.uid, key=entry.key if entry else game.key, title=game.title, start=now,
                         proc=None if delegated else proc, seen=now, grace_until=now + (150 if delegated else 20))
        self.running[game.uid] = state
        self._set_preparing(game.uid, "")
        self.s.db.log_activity("launch", f"Started playing {game.title}", game.uid)
        self.bus.game_started.emit(game.uid)
        integ = self.s.integration(game.source)
        tasks.run_async(integ.on_session_start, game)

    def stop(self, uid: str) -> None:
        st = self.running.get(uid)
        if not st:
            return
        if st.proc and st.proc.poll() is None:
            try:
                os.killpg(st.proc.pid, signal.SIGTERM)
            except OSError:
                pass
        for pid in st.pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        game = self.s.library.game(uid)
        if game and game.platform == "windows" and game.source in ("gog", "local", "epic"):
            tasks.run_async(self.s.compat.kill_prefix, uid)
        self._suppress[uid] = time.time() + 30

    # -------------------------------------------------------------- poll
    def poll(self) -> None:
        if self._scanning:
            return
        hints: dict[str, dict] = {}
        lib = self.s.library
        for uid in self.running:
            g = lib.game(uid)
            if g:
                hints[uid] = self.s.integration(g.source).process_hints(g)
        if self.s.settings.get("library.detect_external_sessions"):
            for g in list(lib.games.values()):
                if g.installed and g.uid not in hints:
                    integ = self.s.integrations.get(g.source)
                    if integ:
                        hints[g.uid] = integ.process_hints(g)
        if not hints:
            return
        self._scanning = True
        tasks.run_async(lambda: process.match(process.snapshot(), hints), on_done=self._apply_scan,
                        on_error=lambda _e: setattr(self, "_scanning", False))

    def _apply_scan(self, found: dict[str, list[int]]) -> None:
        self._scanning = False
        now = time.time()
        for uid, st in list(self.running.items()):
            alive = st.proc is not None and st.proc.poll() is None
            if uid in found:
                first = not st.pids and st.proc is None and not st.external
                st.seen = now
                st.pids = found[uid]
                if first:
                    self.bus.game_detected.emit(uid)
            elif alive:
                st.seen = now
            else:
                if now < st.grace_until:
                    continue
                if now - st.seen > 6:
                    self._end(st)
        if self.s.settings.get("library.detect_external_sessions"):
            for uid, pids in found.items():
                if uid in self.running or uid in self.preparing or self._suppress.get(uid, 0) > now:
                    continue
                g = self.s.library.game(uid)
                if not g:
                    continue
                entry = self.s.library.entry_for_uid(uid)
                self.running[uid] = RunState(uid=uid, key=entry.key if entry else g.key, title=g.title, start=now,
                                             seen=now, grace_until=now, external=True, pids=pids)
                self.bus.game_started.emit(uid)

    def _end(self, st: RunState) -> None:
        self.running.pop(st.uid, None)
        end = max(st.seen, st.start)
        duration = end - st.start
        if duration >= MIN_SESSION:
            self.s.db.add_session(st.uid, st.key, st.start, end)
            self.s.db.log_activity("session", f"Played {st.title} for {int(duration // 60)} min", st.uid)
        self.s.library.rebuild()
        self.bus.game_changed.emit(st.key)
        self.bus.game_stopped.emit(st.uid, int(duration))
        game = self.s.library.game(st.uid)
        if game and duration >= MIN_SESSION:
            tasks.run_async(self._after_session, game, st.start, end)

    def _after_session(self, game, start: float, end: float) -> None:
        integ = self.s.integration(game.source)
        try:
            integ.on_session_end(game, start, end)
        except Exception as exc:  # noqa: BLE001
            log.debug("on_session_end failed: %s", exc)
        if self._cloud_enabled(game, integ) and self.s.settings.get("cloud.sync_on_exit"):
            res = integ.cloud_sync(game, "auto", "exit")
            if res.action == "conflict":
                tasks.on_main(lambda: self.bus.error.emit(
                    "Cloud save conflict", f"{game.title}: open the game page › Cloud saves to choose which saves to keep."))
            elif res.action in ("upload", "download"):
                tasks.on_main(lambda: self.bus.notify.emit(game.title, res.message))
        try:
            new = self.s.achievements.refresh_and_diff(game.uid)
        except Exception as exc:  # noqa: BLE001
            log.debug("achievement refresh failed: %s", exc)
            new = []
        for a in new[:5]:
            tasks.on_main(lambda a=a: self.bus.notify.emit("Achievement unlocked", f"{game.title}: {a.name}"))
        if new:
            tasks.on_main(lambda: (self.s.library.rebuild(), self.bus.achievements_changed.emit(game.uid)))
