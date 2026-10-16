"""Achievements cache and the friends list (aggregated across platforms)."""
from __future__ import annotations

import logging
import time

from PySide6.QtCore import QObject, QTimer

from nebula.core import tasks
from nebula.core.models import Achievement, Friend
from nebula.integrations.base import ACHIEVEMENTS, FRIENDS

log = logging.getLogger(__name__)

ACH_TTL = 6 * 3600


class AchievementService:
    def __init__(self, services):
        self.s = services

    def cached(self, uid: str) -> list[Achievement] | None:
        got = self.s.db.get_achievements(uid)
        return got[0] if got else None

    def fetch(self, uid: str, force: bool = False) -> list[Achievement] | None:
        got = self.s.db.get_achievements(uid)
        if got and not force and time.time() - got[1] < ACH_TTL:
            return got[0]
        game = self.s.library.game(uid)
        if game is None:
            return None
        integ = self.s.integrations.get(game.source)
        if not integ or not integ.supports(ACHIEVEMENTS):
            return None
        items = integ.achievements(game)
        if items is None:
            return got[0] if got else None
        self.s.db.save_achievements(uid, items)
        return items

    def refresh_and_diff(self, uid: str) -> list[Achievement]:
        previous = self.cached(uid)
        after = self.fetch(uid, force=True) or []
        if previous is None:
            return []  # first snapshot: don't announce historical unlocks
        before = {a.id for a in previous if a.unlocked}
        return [a for a in after if a.unlocked and a.id not in before]

    def warm(self, uids: list[str]) -> None:
        """Fetch achievements for recently played games in the background (for stats)."""
        for uid in uids:
            try:
                self.fetch(uid)
            except Exception as exc:  # noqa: BLE001
                log.debug("achievements for %s failed: %s", uid, exc)


class FriendsService(QObject):
    def __init__(self, services):
        super().__init__()
        self.s = services
        self.friends: list[Friend] = []
        self.loading = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self._apply_interval()

    def _apply_interval(self) -> None:
        minutes = max(1, int(self.s.settings.get("friends.poll_minutes") or 2))
        self.timer.start(minutes * 60 * 1000)

    def refresh(self) -> None:
        if self.loading:
            return
        self.loading = True
        tasks.run_async(self._gather, on_done=self._done, on_error=lambda _e: setattr(self, "loading", False))

    def _gather(self) -> list[Friend]:
        out: list[Friend] = []
        for integ in self.s.integrations.values():
            if integ.supports(FRIENDS) and integ.enabled() and integ.connected():
                try:
                    out += integ.friends()
                except Exception as exc:  # noqa: BLE001
                    log.info("friends from %s failed: %s", integ.id, exc)
        return out

    def _done(self, friends: list[Friend]) -> None:
        self.loading = False
        old = {(f.source, f.id): f for f in self.friends}
        if self.friends and self.s.settings.get("friends.notify_playing"):
            for f in friends:
                prev = old.get((f.source, f.id))
                if f.playing and prev is not None and prev.playing != f.playing:
                    self.s.bus.notify.emit(f.name, f"is now playing {f.playing}")
        order = {"playing": 0, "online": 1, "away": 2, "busy": 2, "unknown": 3, "offline": 4}
        friends.sort(key=lambda f: (order.get(f.status, 5), f.name.lower()))
        self.friends = friends
        self.s.bus.friends_changed.emit()

    def online_count(self) -> int:
        return sum(1 for f in self.friends if f.status in ("online", "playing", "away", "busy"))
