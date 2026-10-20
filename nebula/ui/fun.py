"""Fun extras (Settings > Appearance): easter eggs and little celebrations."""
from __future__ import annotations

import random

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QApplication

KONAMI = (Qt.Key.Key_Up, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
          Qt.Key.Key_Left, Qt.Key.Key_Right, "B", "A")
PAD = {Qt.Key.Key_B: "B", Qt.Key.Key_Escape: "B", Qt.Key.Key_A: "A", Qt.Key.Key_Return: "A", Qt.Key.Key_Enter: "A"}
MILESTONES = (10, 25, 50, 100, 250, 500, 1000)  # hours in one game


class Fun(QObject):
    """The Konami code (keyboard or controller), secret search words and play-time milestones."""

    def __init__(self, window):
        super().__init__(window)
        self.win, self.s = window, window.s
        self._keys: list = []
        self._played: dict[str, int] = {}  # uid -> minutes played before this session
        self.words = {
            "hyperspace": self._warp, "warp": self._warp, "let it snow": lambda: self.win.starfield.snow(60),
            "fireworks": self._party, "party": self._party,
            "42": lambda: self.win.toast("42", "The answer to life, the universe and everything."),
            "konami": lambda: self.win.toast("Psst…", "↑ ↑ ↓ ↓ ← → ← → B A"),
            "do a barrel roll": lambda: (self._warp(), self.win.toast("Barrel roll!", "Wheee.")),
        }
        window.topbar.search.returnPressed.connect(self._search)
        self.s.bus.game_started.connect(self._started)
        self.s.bus.game_stopped.connect(self._stopped)
        self.install()

    def on(self) -> bool:
        return bool(self.s.settings.get("appearance.fun"))

    def install(self) -> None:
        """(Re)install the key watcher: Qt asks the newest application filter first, and handheld navigation
        swallows the arrow keys."""
        app = QApplication.instance()
        app.removeEventFilter(self)
        app.installEventFilter(self)

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() == QEvent.Type.KeyPress and not e.isAutoRepeat() \
                and obj is (QApplication.focusWidget() or self.win):  # once per press, not per parent
            self._keys = (self._keys + [PAD.get(e.key(), e.key())])[-len(KONAMI):]
            if tuple(self._keys) == KONAMI and self.on():
                self._keys = []
                QTimer.singleShot(0, self.konami)
        return False

    def konami(self) -> None:
        self._warp()
        QTimer.singleShot(1400, lambda: self.win.starfield.party(30))
        self.win.toast("Cheat code accepted", random.choice(
            ("+30 lives. Use them wisely.", "Hyperdrive unlocked.", "The nebula approves.")))

    def _warp(self) -> None:
        self.win.starfield.warp()

    def _party(self) -> None:
        self.win.starfield.fireworks(8)
        self.win.starfield.party(20)

    def _search(self) -> None:
        action = self.words.get(self.win.topbar.search.text().strip().lower())
        if action and self.on():
            action()

    def _started(self, uid: str) -> None:
        e = self.s.library.entry_for_uid(uid)
        if e is not None:
            self._played[uid] = e.playtime_minutes

    def _stopped(self, uid: str, seconds: int) -> None:
        e = self.s.library.entry_for_uid(uid)
        if e is None or not self.on():
            return
        after = e.playtime_minutes
        before = self._played.pop(uid, after - seconds // 60)
        hit = [h for h in MILESTONES if before < h * 60 <= after]
        if hit:
            self.win.celebrate(f"{hit[-1]} hours in {e.title}!", random.choice(
                ("That's a lot of space travel.", "Achievement unlocked: dedication.", "The stars salute you.")))
