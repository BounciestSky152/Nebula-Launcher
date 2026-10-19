"""Handheld mode: fullscreen, larger targets and gamepad navigation (Steam Deck, ROG Ally, Legion Go...)."""
from __future__ import annotations

import glob
import logging
import os
import struct
import threading
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractButton, QApplication

from nebula.core import tasks

log = logging.getLogger(__name__)

# DMI product names of common handheld PCs
HANDHELDS = {"jupiter", "galileo", "rc71l", "rc72la", "83e1", "83l3", "83n6", "g1617-01", "g1618-04"}


def detect() -> bool:
    try:
        name = Path("/sys/devices/virtual/dmi/id/product_name").read_text().strip().lower()
    except OSError:
        return False
    return name in HANDHELDS or name.startswith("rog ally")


def send_key(key: int) -> None:
    target = QApplication.activePopupWidget() or QApplication.activeModalWidget() or QApplication.focusWidget() \
        or QApplication.activeWindow()
    if target is None:
        return
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.postEvent(target, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))


class Gamepad:
    """Reads /dev/input/js* (Linux joystick API, no extra packages) and turns the pad into key presses:
    D-pad / left stick = arrows, A = Enter, B = Escape (back)."""

    BUTTONS = {0: Qt.Key.Key_Return, 1: Qt.Key.Key_Escape}
    AXES = {0: (Qt.Key.Key_Left, Qt.Key.Key_Right), 1: (Qt.Key.Key_Up, Qt.Key.Key_Down),
            6: (Qt.Key.Key_Left, Qt.Key.Key_Right), 7: (Qt.Key.Key_Up, Qt.Key.Key_Down)}

    def __init__(self):
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        self.stop()
        self._stop = threading.Event()
        for dev in sorted(glob.glob("/dev/input/js*")):
            t = threading.Thread(target=self._read, args=(dev, self._stop), daemon=True, name=f"gamepad-{dev}")
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        self._threads = []

    def _read(self, dev: str, stop: threading.Event) -> None:
        try:
            fd = os.open(dev, os.O_RDONLY)
        except OSError as exc:
            log.info("gamepad %s unavailable: %s", dev, exc)
            return
        held: dict[int, int] = {}
        try:
            while not stop.is_set():
                data = os.read(fd, 8)
                if len(data) < 8:
                    break
                _t, value, kind, number = struct.unpack("IhBB", data)
                if kind & 0x80:  # initial state burst
                    continue
                key = None
                if kind == 1 and value == 1:
                    key = self.BUTTONS.get(number)
                elif kind == 2 and number in self.AXES:
                    direction = -1 if value < -16000 else 1 if value > 16000 else 0
                    if direction and held.get(number) != direction:
                        key = self.AXES[number][0 if direction < 0 else 1]
                    held[number] = direction
                if key is not None:
                    tasks.on_main(lambda k=key: send_key(k))
        except OSError:
            pass
        finally:
            os.close(fd)


class Navigation(QObject):
    """Up/down/left/right move between buttons (Qt only does that for lists); Escape goes back."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.KeyPress:
            return False
        key = e.key()
        if key == Qt.Key.Key_Escape and QApplication.activeModalWidget() is None \
                and QApplication.activePopupWidget() is None and obj is QApplication.focusWidget():
            self.win.go_back()
            return True
        w = QApplication.focusWidget()
        if isinstance(w, QAbstractButton) and obj is w and key in (Qt.Key.Key_Up, Qt.Key.Key_Down,
                                                                     Qt.Key.Key_Left, Qt.Key.Key_Right):
            w.window().focusNextPrevChild(key in (Qt.Key.Key_Down, Qt.Key.Key_Right))
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and isinstance(w, QAbstractButton) and obj is w:
            w.click()
            return True
        return False
