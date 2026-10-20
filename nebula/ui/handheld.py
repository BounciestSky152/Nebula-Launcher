"""Handheld mode: fullscreen, larger targets and gamepad navigation (Steam Deck, ROG Ally, Legion Go...)."""
from __future__ import annotations

import glob
import logging
import os
import struct
import threading

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QAbstractButton, QApplication

from nebula.core import tasks

log = logging.getLogger(__name__)

def send_key(key: int) -> None:
    target = QApplication.activePopupWidget() or QApplication.activeModalWidget() or QApplication.focusWidget() \
        or QApplication.activeWindow()
    if target is None:
        return
    for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
        QApplication.postEvent(target, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier))


# joydev button numbers per driver (xpad / hid-playstation), mapped to the keys the UI understands:
# confirm = Enter, back = Escape, details = F2, menu = F10
PROFILES = {
    "xbox": {0: Qt.Key.Key_Return, 1: Qt.Key.Key_Escape, 3: Qt.Key.Key_F2, 7: Qt.Key.Key_F10, 6: Qt.Key.Key_F10,
             4: Qt.Key.Key_PageUp, 5: Qt.Key.Key_PageDown},
    "playstation": {0: Qt.Key.Key_Return, 1: Qt.Key.Key_Escape, 2: Qt.Key.Key_F2, 9: Qt.Key.Key_F10,
                    8: Qt.Key.Key_F10, 4: Qt.Key.Key_PageUp, 5: Qt.Key.Key_PageDown},
}

# evdev button codes, by role; used whenever the driver tells us what each button is
CODE_KEYS = {
    0x130: Qt.Key.Key_Return,  # BTN_SOUTH (bottom face button)
    0x131: Qt.Key.Key_Escape,  # BTN_EAST (right)
    0x133: Qt.Key.Key_F2,  # BTN_NORTH / BTN_X
    0x134: Qt.Key.Key_F2,  # BTN_WEST / BTN_Y
    0x13A: Qt.Key.Key_F10,  # BTN_SELECT
    0x13B: Qt.Key.Key_F10,  # BTN_START
    0x136: Qt.Key.Key_PageUp,  # BTN_TL (left bumper): previous tab
    0x137: Qt.Key.Key_PageDown,  # BTN_TR (right bumper): next tab
}
# evdev axis codes: left stick and D-pad hat move the selection; triggers and the right stick do nothing
AXIS_KEYS = {
    0x00: (Qt.Key.Key_Left, Qt.Key.Key_Right), 0x10: (Qt.Key.Key_Left, Qt.Key.Key_Right),  # ABS_X, ABS_HAT0X
    0x01: (Qt.Key.Key_Up, Qt.Key.Key_Down), 0x11: (Qt.Key.Key_Up, Qt.Key.Key_Down),  # ABS_Y, ABS_HAT0Y
}


def axis_map(fd: int) -> dict[int, tuple]:
    """joydev axis number -> (negative key, positive key), from the driver's axis codes (JSIOCGAXMAP);
    pads number their axes differently, so fixed numbers would put the D-pad or triggers in the wrong place."""
    import fcntl

    count = bytearray(1)
    codes = bytearray(64)
    try:
        fcntl.ioctl(fd, 0x80016A11, count)  # JSIOCGAXES
        fcntl.ioctl(fd, 0x80406A32, codes)  # JSIOCGAXMAP
    except OSError:
        return {}
    return {i: AXIS_KEYS[c] for i, c in enumerate(codes[:count[0]]) if c in AXIS_KEYS}


def button_map(fd: int) -> dict[int, int]:
    """joydev button number -> UI key, from the driver's button codes (JSIOCGBTNMAP). Empty when the pad
    doesn't use the standard gamepad codes; the name-based profile is used then."""
    import array
    import fcntl

    count = bytearray(1)
    codes = array.array("H", [0] * 512)
    try:
        fcntl.ioctl(fd, 0x80016A12, count)  # JSIOCGBUTTONS
        fcntl.ioctl(fd, 0x80006A34 + (len(codes) * 2 << 16), codes)  # JSIOCGBTNMAP(len)
    except OSError:
        return {}
    found = {i: CODE_KEYS[c] for i, c in enumerate(codes[:count[0]]) if c in CODE_KEYS}
    return found if 0x130 in codes[:count[0]] else {}


def device_name(fd: int) -> str:
    import fcntl

    buf = bytearray(128)
    try:
        fcntl.ioctl(fd, 0x80006A13 + (len(buf) << 16), buf)  # JSIOCGNAME(len)
    except OSError:
        return ""
    return buf.split(b"\0", 1)[0].decode(errors="replace")


def profile_for(name: str) -> str:
    n = name.lower()
    if any(s in n for s in ("dualsense", "dualshock", "playstation", "sony")):
        return "playstation"
    return "xbox"  # Xbox pads, the Steam Deck and most others use the Xbox layout


class Gamepad:
    """Reads /dev/input/js* (Linux joystick API, no extra packages) and turns the pad into key presses.
    Xbox and DualSense/DualShock layouts are told apart by device name; controllers plugged in later are
    picked up. ``on_kind`` is called (on the GUI thread) with "xbox" or "playstation" when a pad is used."""

    AXES = {0: (Qt.Key.Key_Left, Qt.Key.Key_Right), 1: (Qt.Key.Key_Up, Qt.Key.Key_Down),
            6: (Qt.Key.Key_Left, Qt.Key.Key_Right), 7: (Qt.Key.Key_Up, Qt.Key.Key_Down)}

    def __init__(self, on_kind=None):
        self.on_kind = on_kind
        self.kind = "xbox"
        self._stop = threading.Event()
        self._open: set[str] = set()

    def start(self) -> None:
        self.stop()
        self._stop = threading.Event()
        self._open = set()
        threading.Thread(target=self._watch, args=(self._stop,), daemon=True, name="gamepad-watch").start()

    def stop(self) -> None:
        self._stop.set()

    def _watch(self, stop: threading.Event) -> None:
        while not stop.is_set():
            for dev in sorted(glob.glob("/dev/input/js*")):
                if dev not in self._open:
                    self._open.add(dev)
                    threading.Thread(target=self._read, args=(dev, stop), daemon=True, name=f"gamepad-{dev}").start()
            stop.wait(2.0)

    def _read(self, dev: str, stop: threading.Event) -> None:
        try:
            fd = os.open(dev, os.O_RDONLY)
        except OSError as exc:
            log.info("gamepad %s unavailable: %s", dev, exc)
            self._open.discard(dev)
            return
        name = device_name(fd)
        kind = profile_for(name)
        buttons = button_map(fd) or PROFILES[kind]
        axes = axis_map(fd) or self.AXES
        log.info("gamepad %s: %s (%s layout)", dev, name or "unknown", kind)
        held: dict[int, int] = {}
        try:
            while not stop.is_set():
                data = os.read(fd, 8)
                if len(data) < 8:
                    break
                _t, value, etype, number = struct.unpack("IhBB", data)
                if etype & 0x80:  # initial state burst
                    continue
                key = None
                if etype == 1 and value == 1:
                    key = buttons.get(number)
                elif etype == 2 and number in axes:
                    direction = -1 if value < -16000 else 1 if value > 16000 else 0
                    if direction and held.get(number) != direction:
                        key = axes[number][0 if direction < 0 else 1]
                    held[number] = direction
                if key is not None:
                    if kind != self.kind:
                        self.kind = kind
                        if self.on_kind:
                            tasks.on_main(lambda k=kind: self.on_kind(k))
                    tasks.on_main(lambda k=key: send_key(k))
        except OSError:
            pass
        finally:
            os.close(fd)
            self._open.discard(dev)  # unplugged: picked up again by the watcher when it comes back


class Navigation(QObject):
    """Up/down/left/right move between buttons (Qt only does that for lists); Escape goes back."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.KeyPress:
            return False
        key = e.key()
        if key == Qt.Key.Key_F10 and QApplication.activeModalWidget() is None:
            self.win.navigate("settings")
            return True
        if key == Qt.Key.Key_Escape and QApplication.activeModalWidget() is None \
                and QApplication.activePopupWidget() is None and obj is QApplication.focusWidget() \
                and self.win.stack.currentWidget() is not self.win.pages.get("handheld"):
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
