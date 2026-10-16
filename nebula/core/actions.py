"""User-facing operations that run as jobs in the download queue."""
from __future__ import annotations

import copy
import logging

from PySide6.QtCore import QTimer

from nebula.core import shortcuts, tasks
from nebula.core.models import Game
from nebula.core.utils import xdg_open
from nebula.integrations.base import UPDATE, InstallRequest
from nebula.runners import manager as runner_manager

log = logging.getLogger(__name__)

HANDOFF_SOURCES = ("steam", "lutris")


class Actions:
    def __init__(self, services):
        self.s = services

    @property
    def bus(self):
        return self.s.bus

    def _job(self, game: Game, kind: str, title: str, fn, done_msg: str = "", request: InstallRequest | None = None):
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
            on_success=ok, on_failure=fail,
        )

    def _poll_source(self, source: str) -> None:
        for delay in (5000, 30000, 90000):
            QTimer.singleShot(delay, lambda: self.s.library.refresh([source], enrich=False))

    # -------------------------------------------------------------- games
    def install(self, game: Game, request: InstallRequest):
        return self._job(game, "install", f"Install {game.title}",
                         lambda integ, g, ctx: integ.install(g, request, ctx),
                         "Installation complete" if game.source not in HANDOFF_SOURCES else "", request)

    def update(self, game: Game, version: str = ""):
        label = "Rolling back" if version else "Updating"
        return self._job(game, "update", f"{label} {game.title}",
                         lambda integ, g, ctx: integ.update(g, ctx, version),
                         "Version changed" if version else "Update complete")

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
            found: list[str] = []
            for integ in self.s.integrations.values():
                if not integ.enabled() or not integ.connected():
                    continue
                games = [g for g in self.s.library.games_for_source(integ.id) if g.installed]
                if not games:
                    continue
                try:
                    found += integ.check_updates(games)
                except Exception as exc:  # noqa: BLE001
                    log.info("update check for %s failed: %s", integ.id, exc)
                for g in games:
                    self.s.db.save_game(g)
            return found

        def done(found: list[str]):
            self.s.library.load()
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
                    if self.s.launcher.is_running(uid) or self.s.jobs.active_for(uid):
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
