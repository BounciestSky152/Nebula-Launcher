"""User-facing operations that run as jobs in the download queue."""
from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QTimer

from nebula.core import shortcuts, tasks
from nebula.core.models import Game
from nebula.core.utils import xdg_open
from nebula.integrations.base import UPDATE, VERSIONS, InstallRequest
from nebula.runners import manager as runner_manager

log = logging.getLogger(__name__)

HANDOFF_SOURCES = ("steam", "lutris")

# kind of version change -> (job title prefix, in-progress verb, done verb, done message without a label)
CHANGES = {
    "update": ("Updating", "Updating", "Updated", "Update complete"),
    "rollback": ("Rolling back", "Rolling back", "Rolled back", "Version changed"),
    "switch": ("Switching channel for", "Switching", "Switched", "Channel changed"),
    "change": ("Changing version of", "Changing version", "Version changed", "Version changed"),
}


class Actions:
    def __init__(self, services):
        self.s = services

    @property
    def bus(self):
        return self.s.bus

    def _job(self, game: Game, kind: str, title: str, fn, done_msg: str = "", request: InstallRequest | None = None,
             verb: str = "", verb_done: str = ""):
        integ = self.s.integration(game.source)
        work = copy.deepcopy(game)

        def ok(result):
            g = result if isinstance(result, Game) else work
            if game.source in HANDOFF_SOURCES:
                self._poll_source(game.source)
            else:
                self.s.library.update_game(g)
            if done_msg:
                self.bus.notify.emit(game.title, done_msg)
            if request and request.shortcut and g.installed:
                entry = self.s.library.entry_for_uid(g.uid)
                urls = entry.cover_urls() if entry else g.covers()
                tasks.run_async(shortcuts.create, g.uid, g.title, ([g.icon] if g.icon else []) + urls)
            self.s.db.log_activity(kind, f"{title}", g.uid)

        def fail(err: str):
            self.bus.error.emit(f"{title} failed", err)

        return self.s.jobs.submit(
            title=game.title, kind=kind, uid=game.uid,
            func=lambda ctx: fn(integ, work, ctx),
            can_pause=game.source in ("gog",) and kind != "uninstall",
            on_success=ok, on_failure=fail, verb=verb, verb_done=verb_done,
        )

    def _poll_source(self, source: str) -> None:
        for delay in (5000, 30000, 90000):
            QTimer.singleShot(delay, lambda: self.s.library.refresh([source], enrich=False))

    # -------------------------------------------------------------- games
    def install(self, game: Game, request: InstallRequest):
        return self._job(game, "install", f"Install {game.title}",
                         lambda integ, g, ctx: integ.install(g, request, ctx),
                         "Installation complete" if game.source not in HANDOFF_SOURCES else "", request)

    def busy_reason(self, game: Game) -> str:
        """Why the installed files can't be changed right now ("" = they can)."""
        if self.s.launcher.is_running(game.uid) or game.uid in self.s.launcher.preparing:
            return "Close the game first."
        if self.s.jobs.active_for(game.uid):
            return "Wait for the current download for this game to finish."
        return ""

    def update(self, game: Game, version: str = "", change: str = "", label: str = ""):
        """Update, roll back or switch channel. ``version`` as in Integration.update; ``change`` is a CHANGES key
        and ``label`` the target shown in the queue ("1.0" -> "Rolling back to 1.0"). None when the game is busy."""
        reason = self.busy_reason(game)
        if reason:
            self.bus.error.emit(game.title, reason)
            return None
        change = change or ("change" if version else "update")
        prefix, ing, ed, default_done = CHANGES[change]
        verb = f"{ing} to {label}" if label else ing
        done = f"{ed} to {label}" if label else default_done
        return self._job(game, "update", f"{prefix} {game.title}",
                         lambda integ, g, ctx: integ.update(g, ctx, version),
                         done, verb=verb, verb_done=done)

    def set_version_pin(self, game: Game, pinned: bool) -> None:
        """Turn "stay on this version" on or off and recompute the update flag without touching the network."""
        self.s.db.update_config(game.uid, pin_version=bool(pinned))
        g = self.s.library.game(game.uid)
        if g is None:
            return
        if pinned:
            g.update_available = False
        else:
            integ = self.s.integration(g.source)
            try:
                info = integ.version_info(g, fetch=False) if integ.supports(VERSIONS) else None
            except Exception as exc:  # noqa: BLE001
                log.info("version info for %s failed: %s", g.uid, exc)
                info = None
            g.update_available = bool(info and info.update_available)
        self.s.library.update_game(g)  # saves and emits game_changed (PLAY/UPDATE button, version card)

    def apply_version_info(self, uid: str, build_id: str, info) -> bool:
        """Store the update flag / version name from a live version_info() fetch (GUI thread). Results for an
        older build, stale or failed results and games with a running job are ignored. True when it changed."""
        g = self.s.library.game(uid)
        if (not g or not g.installed or g.build_id != build_id or info is None or info.error or info.stale
                or self.s.jobs.active_for(uid)):
            return False
        changed = False
        pinned = bool(self.s.db.get_config(uid).get("pin_version"))  # may have changed during the fetch
        flag = info.update_available and not pinned
        if g.update_available != flag:
            g.update_available = flag
            changed = True
        v = info.installed.version
        if v and g.version != v:
            g.version = v
            changed = True
        if changed:
            self.s.library.update_game(g)
        return changed

    def repair(self, game: Game):
        return self._job(game, "repair", f"Verify {game.title}",
                         lambda integ, g, ctx: integ.repair(g, ctx), "Files verified and repaired")

    def install_dlcs(self, game: Game, dlc_ids: list[str]):
        return self._job(game, "dlc", f"DLC for {game.title}",
                         lambda integ, g, ctx: integ.install_dlcs(g, dlc_ids, ctx), "DLC installed")

    def uninstall(self, game: Game, delete_prefix: bool = False):
        def run(integ, g, ctx):
            result = integ.uninstall(g, ctx)
            if delete_prefix:
                ctx.stage("Removing Proton prefix")
                self.s.compat.delete_prefix(g.uid)
            shortcuts.remove(g.uid)
            return result

        job = self._job(game, "uninstall", f"Uninstall {game.title}", run,
                        "Uninstalled" if game.source not in HANDOFF_SOURCES else "")
        if game.source == "local":
            job.on_success = lambda _r: self.s.library.refresh(["local"], enrich=False)
        return job

    def check_updates(self, auto_update: bool | None = None, on_done=None) -> None:
        auto = self.s.settings.get("downloads.auto_update") if auto_update is None else auto_update

        def work():
            results: list[tuple[str, str, bool, str]] = []  # uid, build it was checked for, flag, version
            for integ in self.s.integrations.values():
                if not integ.enabled() or not integ.connected():
                    continue
                # check copies: a job finishing meanwhile replaces the library's objects, and its result wins
                games = [copy.deepcopy(g) for g in self.s.library.games_for_source(integ.id) if g.installed]
                if not games:
                    continue
                builds = {g.uid: g.build_id for g in games}
                try:
                    integ.check_updates(games)
                except Exception as exc:  # noqa: BLE001
                    log.info("update check for %s failed: %s", integ.id, exc)
                results += [(g.uid, builds[g.uid], g.update_available, g.version) for g in games]
            return results

        def done(results: list[tuple[str, str, bool, str]]):
            found: list[str] = []
            for uid, build_id, flag, version in results:
                g = self.s.library.game(uid)
                if not g or not g.installed or g.build_id != build_id or self.s.jobs.active_for(uid):
                    continue  # installed, updated or rolled back while we were checking
                flag = flag and not self.s.db.get_config(uid).get("pin_version")  # pinned meanwhile
                if g.update_available != flag or (version and g.version != version):
                    g.update_available = flag
                    g.version = version or g.version
                    self.s.library.update_game(g, emit=False)
                if flag:
                    found.append(uid)
            self.bus.library_changed.emit()
            if found:
                titles = [self.s.library.game(u).title for u in found if self.s.library.game(u)]
                self.bus.notify.emit("Updates available", ", ".join(titles[:4]) + ("…" if len(titles) > 4 else ""))
            if auto:
                for uid in found:
                    g = self.s.library.game(uid)
                    integ = self.s.integrations.get(g.source) if g else None
                    if not g or not integ or not integ.supports(UPDATE) or g.source in HANDOFF_SOURCES:
                        continue
                    if self.busy_reason(g):
                        continue
                    if self.s.db.get_config(uid).get("auto_update", True) is False:
                        continue
                    self.update(g)
            if on_done:
                on_done(found)

        tasks.run_async(work, on_done=done)

    # ------------------------------------------------------------- import
    def import_folder(self, path: str, on_done=None) -> None:
        def work():
            out = []
            for integ in self.s.integrations.values():
                if integ.supports("import"):
                    out += integ.import_path(path)
            return out

        def done(games: list[Game]):
            for g in games:
                self.s.library.update_game(g, emit=False)
            self.bus.library_changed.emit()
            if on_done:
                on_done(games)

        tasks.run_async(work, on_done=done, on_error=lambda e: self.bus.error.emit("Import failed", str(e)))

    def add_custom_game(self, data: dict) -> str:
        local = self.s.integrations["local"]
        gid = local.save_entry(data)
        self.s.library.refresh(["local"], enrich=False)
        return gid

    # ------------------------------------------------------------ helpers
    def open_folder(self, game: Game) -> None:
        if game.install_path:
            xdg_open(game.install_path)

    def create_shortcut(self, game: Game, on_done=None) -> None:
        entry = self.s.library.entry_for_uid(game.uid)
        urls = ([game.icon] if game.icon else []) + (entry.cover_urls() if entry else game.covers())
        tasks.run_async(shortcuts.create, game.uid, game.title, urls, on_done=on_done,
                        on_error=lambda e: self.bus.error.emit("Shortcut", str(e)))

    # ------------------------------------------------------------ runners
    def install_ge_proton(self, release: dict):
        fake = Game("runner", release["tag"], release["tag"])

        def ok(_tag):
            self.s.compat.refresh()
            self.bus.runners_changed.emit()
            self.bus.notify.emit("Proton", f"{release['tag']} installed")

        return self.s.jobs.submit(title=release["tag"], kind="runner", uid=fake.uid,
                                  func=lambda ctx: runner_manager.install_ge_proton(ctx, release), can_pause=False,
                                  on_success=ok, on_failure=lambda e: self.bus.error.emit("Proton download failed", e))

    def install_umu(self):
        def ok(_p):
            self.s.compat.refresh()
            self.bus.runners_changed.emit()
            self.bus.notify.emit("umu-launcher", "Installed - Proton games now run inside the Steam Runtime")

        return self.s.jobs.submit(title="umu-launcher", kind="runner", uid="runner:umu",
                                  func=runner_manager.install_umu, can_pause=False, on_success=ok,
                                  on_failure=lambda e: self.bus.error.emit("umu-launcher download failed", e))
