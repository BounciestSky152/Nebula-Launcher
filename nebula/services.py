"""Service container wiring settings, storage, integrations and background services."""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer

from nebula.core import tasks
from nebula.core.actions import Actions
from nebula.core.db import Database
from nebula.core.events import get_bus
from nebula.core.jobs import JobManager
from nebula.core.launcher import GameLauncher
from nebula.core.library import LibraryManager
from nebula.core.settings import Settings
from nebula.core.social import AchievementService, FriendsService
from nebula.integrations import create_all
from nebula.integrations.base import Integration
from nebula.runners.compat import CompatLayer

log = logging.getLogger(__name__)

LOCAL_SOURCES = ("steam", "epic", "lutris", "local")


class Services:
    def __init__(self, settings: Settings | None = None, db: Database | None = None):
        self.settings = settings or Settings()
        self.db = db or Database()
        self.bus = get_bus()
        self.compat = CompatLayer(self.settings, self.db)
        self.jobs = JobManager(self.settings, self.bus)
        self.integrations: dict[str, Integration] = {}
        self.library = LibraryManager(self)
        self.integrations = create_all(self)
        self.achievements = AchievementService(self)
        self.launcher = GameLauncher(self)
        self.friends = FriendsService(self)
        self.actions = Actions(self)
        self._timers: list[QTimer] = []

    def integration(self, source: str) -> Integration:
        return self.integrations[source]

    def gog(self):
        return self.integrations.get("gog")

    def _every(self, minutes: float, fn) -> None:
        t = QTimer()
        t.setInterval(int(minutes * 60 * 1000))
        t.timeout.connect(fn)
        t.start()
        self._timers.append(t)

    def start(self) -> None:
        self.library.load()
        self.library.refresh()
        QTimer.singleShot(3000, self.friends.refresh)
        if self.settings.get("downloads.check_updates_on_start"):
            QTimer.singleShot(25000, lambda: self.actions.check_updates())
        gog = self.gog()
        if gog and gog.connected():
            tasks.run_async(gog.refresh_account, on_done=lambda _i: self.bus.integration_changed.emit("gog"))
        recent = [g.uid for e in self.library.recently_played(12) for g in e.games]
        tasks.run_async(self.achievements.warm, recent, on_done=lambda _r: self.library.rebuild())
        self._every(3, lambda: self.library.refresh([s for s in LOCAL_SOURCES if s in self.integrations], enrich=False))
        self._every(6 * 60, lambda: self.actions.check_updates())
        self._every(5, lambda: gog and tasks.run_async(gog.heartbeat))
        self._every(60, lambda: self.library.refresh(["gog"]))

    def shutdown(self) -> None:
        for t in self._timers:
            t.stop()
        self.launcher.timer.stop()
        self.friends.timer.stop()
        self.jobs.shutdown()
        gog = self.gog()
        if gog:
            try:
                gog.go_offline()
            except Exception:  # noqa: BLE001
                pass
        tasks.shutdown()
