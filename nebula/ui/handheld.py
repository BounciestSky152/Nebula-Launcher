"""Handheld mode: fullscreen, larger targets and gamepad navigation (Steam Deck, ROG Ally, Legion Go...)."""
from __future__ import annotations

import glob
import logging
import os
import struct
import threading

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QAbstractButton, QAbstractItemView, QAbstractScrollArea, QAbstractSpinBox,
                               QApplication, QComboBox, QLineEdit, QScrollArea, QScrollBar, QTabBar, QTabWidget,
                               QWidget)

from nebula.core import tasks

log = logging.getLogger(__name__)

def send_key(key: int) -> None:
    top = QApplication.activePopupWidget() or QApplication.activeModalWidget()
    focus = QApplication.focusWidget()
    # the focused control inside an open popup (a dropdown's list) or dialog, so the pad can drive it too
    if focus is not None and (top is None or focus is top or top.isAncestorOf(focus)):
        target = focus
    else:
        target = top or QApplication.activeWindow()
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


def form_root(w):
    """The nearest ancestor that asked for controller form navigation (the "controllerForm" property)."""
    while w is not None:
        if w.property("controllerForm"):
            return w
        w = w.parentWidget()
    return None


def form_rows(root) -> list[list]:
    """root's visible, focusable controls in reading order, grouped into rows (top to bottom, left to right)."""
    items = []
    for w in root.findChildren(QWidget):
        if not (w.isVisible() and w.isEnabled() and w.focusPolicy().value & Qt.FocusPolicy.TabFocus.value) \
                or w.focusProxy() is not None or w.property("ownNav") or isinstance(w, QScrollBar) \
                or (isinstance(w, QAbstractScrollArea) and not isinstance(w, QAbstractItemView)) \
                or isinstance(w.parentWidget(), (QAbstractSpinBox, QComboBox)):
            continue
        items.append((QRect(w.mapTo(root, QPoint(0, 0)), w.size()), w))
    items.sort(key=lambda it: (it[0].center().y(), it[0].x()))
    rows: list[list] = []
    for r, w in items:
        if rows and abs(rows[-1][0][0].center().y() - r.center().y()) <= 12:
            rows[-1].append((r, w))
        else:
            rows.append([(r, w)])
    return [[w for _r, w in sorted(row, key=lambda it: it[0].x())] for row in rows]


def spin_step(w: QAbstractSpinBox) -> int:
    """Steps per press for a spin box, so 0..1,000,000 doesn't take a million presses."""
    span = (w.maximum() - w.minimum()) / max(1, w.singleStep()) if hasattr(w, "maximum") else 0
    return next((n for n in (10000, 5000, 1000, 500, 100, 50, 10, 5) if span / n >= 40), 1)


class Navigation(QObject):
    """Up/down/left/right move between buttons (Qt only does that for lists); Escape goes back.
    Pages with the "controllerForm" property (settings) are driven as a form: up/down go through the
    controls in reading order, left/right change a choice or number, A opens a choice or types into a text
    field with the on-screen keyboard, and the bumpers switch between its tabs."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window
        self.keyboard = None

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.KeyPress:
            return False
        key = e.key()
        if key == Qt.Key.Key_F10 and QApplication.activeModalWidget() is None:
            self.win.navigate("settings")
            return True
        w = QApplication.focusWidget()
        if key == Qt.Key.Key_Escape and QApplication.activeModalWidget() is None \
                and QApplication.activePopupWidget() is None and obj is w and not w.property("ownNav") \
                and self.win.stack.currentWidget() is not self.win.pages.get("handheld"):
            self.win.go_back()
            return True
        if QApplication.activeModalWidget() is None and QApplication.activePopupWidget() is None:
            page = self.win.stack.currentWidget()
            if (w is None or w is self.win) and obj is self.win and page is not None \
                    and page.property("controllerForm") and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                self.focus_first(page)  # nothing focused (a rebuilt section): start at the top
                return True
            root = form_root(w) if obj is w and w is not None and not w.property("ownNav") else None
            if root is not None and self._form_key(w, root, key, e.spontaneous()):
                return True
        if isinstance(w, QAbstractButton) and obj is w and not w.property("ownNav") and key in (Qt.Key.Key_Up, Qt.Key.Key_Down,
                                                                     Qt.Key.Key_Left, Qt.Key.Key_Right):
            w.window().focusNextPrevChild(key in (Qt.Key.Key_Down, Qt.Key.Key_Right))
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and isinstance(w, QAbstractButton) and obj is w:
            w.click()
            return True
        return False

    # ------------------------------------------------------------- forms
    def focus(self, w) -> None:
        w.setFocus(Qt.FocusReason.OtherFocusReason)
        p = w.parentWidget()
        while p is not None and not isinstance(p, QScrollArea):
            p = p.parentWidget()
        if p is not None:
            p.ensureWidgetVisible(w, 0, 60)

    def focus_first(self, root, skip_tabs: bool = False) -> None:
        flat = [x for row in form_rows(root) for x in row if not (skip_tabs and isinstance(x, QTabBar))]
        if flat:
            self.focus(flat[0])

    def _form_key(self, w, root, key, physical: bool) -> bool:
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            tabs = w.parentWidget()
            while tabs is not None and not isinstance(tabs, QTabWidget):
                tabs = tabs.parentWidget()
            tabs = tabs or root.findChild(QTabWidget)
            if tabs is None or tabs.count() == 0:
                return False
            tabs.setCurrentIndex((tabs.currentIndex() + (1 if key == Qt.Key.Key_PageDown else -1)) % tabs.count())
            QTimer.singleShot(0, lambda: self.focus_first(tabs.currentWidget()) if tabs.currentWidget() else None)
            return True
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            if isinstance(w, QAbstractItemView) and w.model() is not None:
                n, row = w.model().rowCount(), w.currentIndex().row()
                if n and not (row <= 0 if key == Qt.Key.Key_Up else row >= n - 1):
                    return False  # moving inside the list; its ends lead out of it
            flat = [x for r in form_rows(root) for x in r]
            if w not in flat:
                if flat:
                    self.focus(flat[0])
                return True
            j = flat.index(w) + (1 if key == Qt.Key.Key_Down else -1)
            if 0 <= j < len(flat):
                self.focus(flat[j])
            return True
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 1 if key == Qt.Key.Key_Right else -1
            if isinstance(w, QComboBox):
                if 0 <= w.currentIndex() + step < w.count():
                    w.setCurrentIndex(w.currentIndex() + step)
                return True
            if isinstance(w, QAbstractSpinBox):
                w.stepBy(step * spin_step(w))
                return True
            if isinstance(w, (QLineEdit, QTabBar, QAbstractItemView)):
                return False  # cursor, tabs and lists handle left/right themselves
            row = next((r for r in form_rows(root) if w in r), [])
            if w in row and 0 <= row.index(w) + step < len(row):
                self.focus(row[row.index(w) + step])  # buttons side by side
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if isinstance(w, QComboBox):
                w.showPopup()
                return True
            if isinstance(w, QTabBar):
                QTimer.singleShot(0, lambda: self.focus_first(root, skip_tabs=True))
                return True
            if isinstance(w, QLineEdit) and not w.isReadOnly() and not physical:
                self.type_into(w)  # the pad's A; Return on a real keyboard just confirms the text
                return True
        return False

    def type_into(self, edit: QLineEdit) -> None:
        """Open the on-screen keyboard for a text field; "Done" saves it like pressing Return would."""
        from nebula.ui.pages.handheld import Keyboard

        def done(_text):
            edit.editingFinished.emit()
            edit.setFocus()

        if self.keyboard is None:
            self.keyboard = Keyboard(edit, done, self.win, submit="Done")
        self.keyboard.set_target(edit, done, "Done")
        self.keyboard.set_scale(max(0.8, min(2.4, self.win.height() / 800)))
        self.keyboard.open()
        field = QRect(edit.mapTo(self.win, QPoint(0, 0)), edit.size())
        if self.keyboard.geometry().intersects(field):  # keep the text visible while typing
            self.keyboard.move(self.keyboard.x(), int(self.win.height() * 0.06))

    def close_keyboard(self) -> None:
        """Called whenever the page changes or handheld mode ends, so the keyboard never outlives its form."""
        if self.keyboard is not None and self.keyboard.isVisible():
            self.keyboard.hide()
