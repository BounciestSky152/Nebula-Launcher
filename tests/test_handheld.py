"""Handheld mode: the settings page driven with a controller (keys as the gamepad sends them)."""
import sys
import time

import pytest


@pytest.fixture()
def errors(monkeypatch):
    caught = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: caught.append((t, v)))
    return caught


def pump(app, seconds=0.25):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def test_settings_with_a_controller(qapp, services, errors):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QSpinBox, QTabBar

    from nebula.ui import theme
    from nebula.ui.handheld import form_rows, send_key
    from nebula.ui.main_window import MainWindow

    theme.apply(qapp)
    services.settings.set("handheld.mode", "on")
    w = MainWindow(services)
    w.show()
    w.activateWindow()
    w.start_page()  # Big Picture home
    pump(qapp)
    page = w.pages["settings"]

    def press(key, seconds=0.15):
        send_key(key)
        pump(qapp, seconds)

    def focused():
        return QApplication.focusWidget()

    w.navigate("settings", "full")  # the desktop settings page, reached from Big Picture settings > About
    pump(qapp)
    assert w.stack.currentWidget() is page and page.prompts.isVisible()
    assert isinstance(focused(), QTabBar)

    press(Qt.Key.Key_PageDown)  # RB: next section, focus on its first control
    assert page.tabs.currentIndex() == 1
    assert page.tabs.currentWidget().isAncestorOf(focused())
    press(Qt.Key.Key_PageUp)
    assert page.tabs.currentIndex() == 0

    # down walks every control of the section in reading order, without ever changing a value
    before = dict(services.settings._data)
    order = [x for row in form_rows(page) for x in row]
    assert order[0] is page.tabs.tabBar()
    w._nav.focus(order[0])
    seen = [focused()]
    for _ in range(len(order) - 1):
        press(Qt.Key.Key_Down, 0.05)
        seen.append(focused())
    assert seen == order
    press(Qt.Key.Key_Down, 0.05)  # the last control stays put
    assert focused() is order[-1]
    assert dict(services.settings._data) == before

    # left/right change a choice and a number
    combo = next(x for x in order if isinstance(x, QComboBox) and x.findData("minimize") >= 0)
    w._nav.focus(combo)
    pump(qapp, 0.05)
    start = combo.currentIndex()
    press(Qt.Key.Key_Right)
    assert combo.currentIndex() == start + 1
    assert services.settings.get("general.launch_behaviour") == combo.currentData()
    press(Qt.Key.Key_Left)
    assert combo.currentIndex() == start
    spin = next(x for x in order if isinstance(x, QSpinBox))
    w._nav.focus(spin)
    pump(qapp, 0.05)
    value = spin.value()
    press(Qt.Key.Key_Right)
    assert spin.value() > value

    # A on a text field types with the on-screen keyboard; B closes it without leaving the page
    page.tabs.setCurrentIndex(page.tabs.count() - 2)  # Updates
    pump(qapp)
    edit = next(x for row in form_rows(page) for x in row
                if isinstance(x, QLineEdit) and x.text() == services.settings.get("updates.branch"))
    w._nav.focus(edit)
    pump(qapp, 0.05)
    press(Qt.Key.Key_Return)
    kb = w._nav.keyboard
    assert kb is not None and kb.isVisible() and kb.isAncestorOf(focused())
    press(Qt.Key.Key_Right)  # q -> w
    press(Qt.Key.Key_Return)
    assert edit.text().endswith("w")
    kb.submit_key.click()
    pump(qapp)
    assert not kb.isVisible() and focused() is edit
    assert services.settings.get("updates.branch").endswith("w")
    press(Qt.Key.Key_Return)
    assert kb.isVisible()
    press(Qt.Key.Key_Escape)
    assert not kb.isVisible() and w.stack.currentWidget() is page

    press(Qt.Key.Key_Escape)  # B leaves the settings
    assert w.stack.currentWidget() is w.pages["handheld"]
    services.settings.set("handheld.mode", "off")
    pump(qapp)
    w.close()
    assert not errors


def test_keyboard_layers(qapp):
    from PySide6.QtWidgets import QLineEdit, QWidget

    from nebula.ui.pages.handheld import Keyboard

    host = QWidget()
    edit = QLineEdit(host)
    kb = Keyboard(edit, lambda _t: None, host, submit="Done")
    kb.cells[1][0].click()  # q
    kb.shift_key.click()
    kb.cells[1][0].click()  # Q
    kb.symbol_key.click()
    kb.cells[1][6].click()  # &
    kb.cells[2][4].click()  # /
    assert edit.text() == "qQ&/"
    assert kb.cells[1][6].text() == "&&"  # shown as a literal ampersand
    assert kb.submit_key.text() == "Done"


def test_big_picture_settings(qapp, services, errors):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from nebula.ui import theme
    from nebula.ui.handheld import send_key
    from nebula.ui.main_window import MainWindow
    from nebula.ui.pages.handheld_settings import ChoiceRow, TextRow, ToggleRow

    theme.apply(qapp)
    services.settings.set("handheld.mode", "on")
    w = MainWindow(services)
    w.show()
    w.activateWindow()
    w.start_page()
    pump(qapp)
    for other in QApplication.topLevelWidgets():  # windows and dialogs left over by earlier tests take the keys
        if other is not w and other.isVisible():
            other.hide()
    QApplication.setActiveWindow(w)
    pump(qapp)
    page = w.pages["handheld_settings"]

    def press(key, seconds=0.12):
        send_key(key)
        pump(qapp, seconds)

    def focused():
        return QApplication.focusWidget()

    press(Qt.Key.Key_F10)  # Start opens the controller settings, on the section list
    assert w.stack.currentWidget() is page and focused() is page.cats[0]
    press(Qt.Key.Key_Down)
    assert page.index == 1 and focused() is page.cats[1]
    press(Qt.Key.Key_PageUp)  # LB / RB jump between sections
    assert page.index == 0

    press(Qt.Key.Key_Right)  # into the rows
    rows = page._rows()
    assert focused() is rows[0]
    toggle = next(r for r in rows if isinstance(r, ToggleRow) and r.key == "appearance.starfield")
    while focused() is not toggle:
        press(Qt.Key.Key_Down, 0.05)
    before = services.settings.get("appearance.starfield")
    press(Qt.Key.Key_Return)  # A flips a switch
    assert services.settings.get("appearance.starfield") is (not before) and toggle.switch.on is (not before)
    press(Qt.Key.Key_Return)
    choice = rows[0]
    assert isinstance(choice, ChoiceRow)
    while focused() is not choice:
        press(Qt.Key.Key_Up, 0.05)
    start = services.settings.get("general.launch_behaviour")
    press(Qt.Key.Key_Right)  # left/right change a choice
    assert services.settings.get("general.launch_behaviour") != start
    press(Qt.Key.Key_Left)
    assert services.settings.get("general.launch_behaviour") == start

    # text: A opens the editor with the keyboard; Done saves, B cancels
    page._select(next(i for i, (t, _r) in enumerate(page.sections) if t == "Updates"))
    branch = next(r for r in page._rows() if isinstance(r, TextRow) and r.key == "updates.branch")
    page._focus_row(branch)
    pump(qapp, 0.05)
    press(Qt.Key.Key_Return)
    assert page.editor.isVisible() and page.keyboard.isAncestorOf(focused())
    assert page.edit.text() == services.settings.get("updates.branch")
    press(Qt.Key.Key_Return)  # types the focused key ("q")
    page.keyboard.submit_key.click()
    pump(qapp)
    assert not page.editor.isVisible() and focused() is branch
    assert services.settings.get("updates.branch").endswith("q")
    press(Qt.Key.Key_Return)
    press(Qt.Key.Key_Return)
    press(Qt.Key.Key_Escape)  # B: closes without saving
    assert not page.editor.isVisible() and focused() is branch
    assert services.settings.get("updates.branch").endswith("q") and not services.settings.get(
        "updates.branch").endswith("qq")

    press(Qt.Key.Key_Escape)  # B: rows -> sections -> back to Big Picture
    assert focused() is page.cats[page.index]
    press(Qt.Key.Key_Escape)
    assert w.stack.currentWidget() is w.pages["handheld"]
    services.settings.set("handheld.mode", "off")
    pump(qapp)
    w.close()
    assert not errors
