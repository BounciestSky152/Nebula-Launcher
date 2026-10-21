"""Settings for Big Picture mode, built for a controller: a column of sections on the left and, beside it,
large rows the D-pad walks through. Left/right change a value, A toggles, opens or runs it, B goes back."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from nebula import __version__
from nebula.ui.pages.base import Page
from nebula.ui.pages.handheld import BASE_HEIGHT, PROMPTS, Keyboard, Prompts, scroll_box, shorten
from nebula.ui.pages.settings import LANGUAGES
from nebula.ui.widgets.common import label

COUNTRIES = ("US", "GB", "DE", "FR", "PL", "ES", "IT", "NL", "SE", "CA", "AU", "BR", "JP", "RU", "CN")
CURRENCIES = ("USD", "EUR", "GBP", "PLN", "CAD", "AUD", "BRL", "JPY", "RUB", "CNY", "SEK")


def _text(x) -> str:
    return x() if callable(x) else x


def speed(kbps: int) -> str:
    if not kbps:
        return "Unlimited"
    return f"{kbps / 1024:g} MB/s" if kbps >= 1024 else f"{kbps} KB/s"


def minutes(m: int) -> str:
    return f"{m // 60} h" if m >= 60 and m % 60 == 0 else f"{m} min"


class Switch(QWidget):
    """An on/off pill; the row it sits in handles the input."""

    def __init__(self):
        super().__init__()
        self.on = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_on(self, on: bool) -> None:
        self.on = on
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#8c62d4") if self.on else QColor(255, 255, 255, 38))
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = r.height() - 6
        p.setBrush(QColor("#ffffff") if self.on else QColor("#b9b3d6"))
        p.drawEllipse(QRectF(r.right() - d - 3 if self.on else r.left() + 3, r.top() + 3, d, d))


class Row(QFrame):
    """One setting: its name and a short explanation on the left, its value on the right."""

    def __init__(self, page, title, desc=""):
        super().__init__()
        self.page, self.st = page, page.s.settings
        self._title, self._desc = title, desc
        self.setObjectName("HHRow")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setProperty("ownNav", True)  # the page moves between rows, not the generic navigation
        self.installEventFilter(page)
        lay = QHBoxLayout(self)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title, self.desc = QLabel(), QLabel()
        self.title.setObjectName("HHRowTitle")
        self.desc.setObjectName("HHRowDesc")
        self.desc.setWordWrap(True)
        text.addWidget(self.title)
        text.addWidget(self.desc)
        lay.addLayout(text, 1)
        self.value = QLabel()
        self.value.setObjectName("HHRowValue")
        lay.addWidget(self.value)

    def refresh(self) -> None:
        self.title.setText(_text(self._title))
        desc = _text(self._desc)
        self.desc.setText(desc)
        self.desc.setVisible(bool(desc))

    def step(self, direction: int) -> bool:
        """Left/right; False when the row has nothing to change."""
        return False

    def activate(self) -> None:
        pass

    def mousePressEvent(self, _e):  # noqa: N802
        self.setFocus()
        self.activate()


class ToggleRow(Row):
    def __init__(self, page, key, title, desc=""):
        super().__init__(page, title, desc)
        self.key = key
        self.switch = Switch()
        self.layout().addWidget(self.switch)
        self.value.hide()

    def refresh(self) -> None:
        super().refresh()
        self.switch.set_on(bool(self.st.get(self.key)))

    def activate(self) -> None:
        self.st.set(self.key, not self.st.get(self.key))
        self.refresh()

    def step(self, direction: int) -> bool:
        self.st.set(self.key, direction > 0)  # left: off, right: on
        self.refresh()
        return True


class ChoiceRow(Row):
    """‹ value ›: left/right go through the options (wrapping, unless they are amounts)."""

    def __init__(self, page, key, title, options, desc="", wrap=True):
        super().__init__(page, title, desc)
        self.key, self.options, self.wrap = key, list(options), wrap

    def refresh(self) -> None:
        super().refresh()
        cur = self.st.get(self.key)
        self.value.setText(f"‹   {next((t for v, t in self.options if v == cur), str(cur))}   ›")

    def step(self, direction: int) -> bool:
        values = [v for v, _t in self.options]
        cur = self.st.get(self.key)
        if cur in values:
            i = values.index(cur) + direction
            i = i % len(values) if self.wrap else max(0, min(len(values) - 1, i))
        else:  # set to something else elsewhere: the nearest option that way
            try:
                ahead = [j for j, v in enumerate(values) if (v > cur if direction > 0 else v < cur)]
            except TypeError:
                ahead = []
            i = (ahead[0] if direction > 0 else ahead[-1]) if ahead else 0
        self.st.set(self.key, values[i])
        self.refresh()
        return True

    def activate(self) -> None:
        self.step(1)


class ActionRow(Row):
    """A button; with ``confirm`` the first A only asks for a second one (sign out, ...)."""

    def __init__(self, page, title, button, fn, desc="", confirm=""):
        super().__init__(page, title, desc)
        self.button, self.fn, self.confirm = button, fn, confirm
        self._armed = 0.0
        self.value.setObjectName("HHRowButton")

    def refresh(self) -> None:
        super().refresh()
        self._armed = 0.0
        self.value.setText(_text(self.button))

    def activate(self) -> None:
        if _text(self.confirm) and time.monotonic() - self._armed > 3:
            self._armed = time.monotonic()
            self.value.setText(_text(self.confirm))
            QTimer.singleShot(3000, lambda: self.refresh() if time.monotonic() - self._armed >= 2.9 else None)
            return
        self._armed = 0.0
        self.fn()
        self.refresh()


class TextRow(Row):
    """Text typed with the on-screen keyboard."""

    def __init__(self, page, key, title, desc="", secret=False, default=""):
        super().__init__(page, title, desc)
        self.key, self.secret, self.default = key, secret, default

    def refresh(self) -> None:
        super().refresh()
        v = str(self.st.get(self.key) or "")
        shown = "•" * min(len(v), 12) if self.secret and v else (v or "Not set")
        self.value.setText(f"{shorten(shown, 34)}   ✎")

    def activate(self) -> None:
        def done(text: str) -> None:
            self.st.set(self.key, text.strip() or self.default)
            self.refresh()

        self.page.edit_text(_text(self._title), str(self.st.get(self.key) or ""), self.secret, done)


class HandheldSettingsPage(Page):
    name = "Settings"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.scale = 1.0
        self.index = 0
        self.editor = None
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        self.heading = label("Settings", "PageTitle")
        self.crumb = label("", "Subtle")
        head.addWidget(self.heading)
        head.addSpacing(18)
        head.addWidget(self.crumb, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        lay.addLayout(head)
        body = QHBoxLayout()
        self.nav = QWidget()
        nav = QVBoxLayout(self.nav)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(6)
        body.addWidget(self.nav)
        self.panel = QFrame()
        self.panel.setObjectName("HHPanel")
        pl = QVBoxLayout(self.panel)
        self.section_title = QLabel()
        self.section_title.setObjectName("HHSection")
        pl.addWidget(self.section_title)
        self.stack = QStackedWidget()
        pl.addWidget(self.stack, 1)
        body.addWidget(self.panel, 1)
        lay.addLayout(body, 1)
        self.prompts = Prompts()
        lay.addWidget(self.prompts)

        self.sections = self._sections()
        self.cats: list[QPushButton] = []
        self.areas = []
        for i, (title, rows) in enumerate(self.sections):
            b = QPushButton(title)
            b.setObjectName("HHCat")
            b.setCheckable(True)
            b.setProperty("ownNav", True)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.installEventFilter(self)
            b.clicked.connect(lambda _c=False, i=i: (self._select(i), self._enter()))
            nav.addWidget(b)
            self.cats.append(b)
            area, box = scroll_box()
            box.setSpacing(8)
            for r in rows:
                box.addWidget(r)
            box.addStretch(1)
            self.stack.addWidget(area)
            self.areas.append(area)
        nav.addStretch(1)
        self.set_controller("xbox")
        self._select(0)
        services.settings.on_change(lambda key, _v: self._changed(key))
        services.self_update.changed.connect(lambda *_: self._refresh() if self.isVisible() else None)
        services.bus.integration_changed.connect(lambda _i: self._refresh() if self.isVisible() else None)

    # ------------------------------------------------------------ content
    def _sections(self) -> list:
        st, s = self.s.settings, self.s

        def gog():
            return s.integrations.get("gog")

        def signed_in() -> bool:
            return bool(gog() and gog().connected())

        def account():
            if signed_in():
                self.ctl.gog_logout()
            else:
                self.ctl.gog_login()

        def check_games():
            s.actions.check_updates(on_done=lambda found: self.win.toast(
                "Updates", f"{len(found)} update(s) found" if found else "All games are up to date"))

        def update_status() -> str:
            return s.self_update.message or "Not checked yet this session"

        return [
            ("Big Picture", [
                ChoiceRow(self, "general.launch_behaviour", "When a game starts", [
                    ("keep", "Keep Super Nova open"), ("minimize", "Minimise Super Nova"), ("hide", "Hide to tray")]),
                ToggleRow(self, "appearance.fun", "Fun extras", "Launch warp, seasonal skies and a few secrets"),
                ToggleRow(self, "appearance.starfield", "Starry sky"),
                ToggleRow(self, "appearance.animate_stars", "Animated stars", "Pauses while a game is running"),
                ChoiceRow(self, "appearance.theme", "Theme", [("nebula", "Nebula"), ("supernova", "Super Nova (light)"),
                                                              ("classic", "Classic")],
                          "Big Picture always uses the dark sky; Super Nova lights up the desktop layout"),
                ActionRow(self, "Exit Big Picture", "Exit", lambda: st.set("handheld.mode", "off"),
                          "Back to the desktop layout (F11 does the same)"),
            ]),
            ("Account", [
                ActionRow(self, "GOG account", lambda: "Sign out" if signed_in() else "Sign in", account,
                          lambda: f"Signed in as {gog().account_name() or 'GOG user'}" if signed_in()
                          else "Sign in to sync your library, cloud saves, achievements and friends",
                          confirm=lambda: "Press A again to sign out" if signed_in() else ""),
                ToggleRow(self, "gog.presence", "Online status", "Show friends that you're online and what you play"),
                ToggleRow(self, "gog.sync_playtime", "Report play time to GOG", "Shown on your GOG profile"),
                ToggleRow(self, "friends.notify_playing", "Friend notifications",
                          "Tell me when a friend starts playing"),
                ChoiceRow(self, "friends.poll_minutes", "Refresh friends every",
                          [(m, minutes(m)) for m in (1, 2, 3, 5, 10, 15, 30, 60)], wrap=False),
                ChoiceRow(self, "gog.country", "Store country", [(c, c) for c in COUNTRIES]),
                ChoiceRow(self, "gog.currency", "Store currency", [(c, c) for c in CURRENCIES]),
            ]),
            ("Downloads", [
                TextRow(self, "library.install_dir", "Install games to"),
                ChoiceRow(self, "downloads.speed_limit_kbps", "Speed limit",
                          [(k, speed(k)) for k in (0, 512, 1024, 2048, 5120, 10240, 25600, 51200, 102400)],
                          wrap=False),
                ChoiceRow(self, "downloads.concurrent_jobs", "Simultaneous installs",
                          [(n, str(n)) for n in (1, 2, 3, 4)], wrap=False),
                ToggleRow(self, "downloads.prefer_native", "Prefer native Linux builds",
                          "When a game offers one"),
                ToggleRow(self, "downloads.check_updates_on_start", "Look for game updates on start-up"),
                ToggleRow(self, "downloads.auto_update", "Install game updates automatically"),
                ActionRow(self, "Game updates", "Check now", check_games),
            ]),
            ("Compatibility", [
                ChoiceRow(self, "compat.default_runner", "Windows games use",
                          [("proton", "Proton (recommended)"), ("wine", "Wine")]),
                ToggleRow(self, "compat.gamemode", "GameMode", "Feral GameMode while playing, when installed"),
                ToggleRow(self, "compat.mangohud", "MangoHud", "Performance overlay"),
                ToggleRow(self, "compat.dxvk_hud", "FPS counter", "DXVK's frame-rate display"),
                ToggleRow(self, "compat.gamescope", "Gamescope", "Run games inside gamescope"),
                ToggleRow(self, "compat.esync", "Esync"),
                ToggleRow(self, "compat.fsync", "Fsync", "Needs a futex2-capable kernel"),
                ToggleRow(self, "compat.wayland", "Native Wayland", "GE-Proton 9 and newer"),
                ToggleRow(self, "compat.nvapi", "NVAPI / DLSS", "NVIDIA graphics cards"),
            ]),
            ("Cloud saves", [
                ToggleRow(self, "cloud.enabled", "Cloud saves", "Synchronise GOG cloud saves"),
                ToggleRow(self, "cloud.sync_on_launch", "Download before playing", "Fetch newer saves first"),
                ToggleRow(self, "cloud.sync_on_exit", "Upload after playing"),
                ChoiceRow(self, "cloud.conflict", "When saves conflict",
                          [("ask", "Ask me"), ("newest", "Keep the newest")]),
            ]),
            ("System", [
                ToggleRow(self, "general.autostart", "Start Super Nova when I log in"),
                ToggleRow(self, "general.start_minimized", "Start minimised"),
                ToggleRow(self, "general.show_tray", "Tray icon"),
                ToggleRow(self, "general.close_to_tray", "Keep running in the tray when closed"),
                ToggleRow(self, "general.notifications", "Desktop notifications"),
                ChoiceRow(self, "general.language", "Game language", LANGUAGES),
            ]),
            ("Updates", [
                ActionRow(self, lambda: f"Super Nova {__version__}", "Check now",
                          lambda: s.self_update.check(manual=True), update_status),
                ToggleRow(self, "updates.auto_check", "Look for new versions"),
                ChoiceRow(self, "updates.interval_minutes", "Check every",
                          [(m, minutes(m)) for m in (15, 30, 60, 180, 360, 720, 1440)], wrap=False),
                ToggleRow(self, "updates.auto_install", "Install updates automatically"),
                ToggleRow(self, "updates.auto_restart", "Restart after updating", "Only when no game is running"),
                TextRow(self, "updates.repo", "GitHub repository"),
                TextRow(self, "updates.branch", "Branch", default="main"),
                TextRow(self, "updates.github_token", "GitHub token", "Only for a private repository",
                        secret=True),
            ]),
            ("About", [
                ActionRow(self, f"Super Nova {__version__}", "All settings",
                          lambda: self.win.navigate("settings", "full"),
                          "Every option, in the desktop layout (integrations, Proton versions...)"),
            ]),
        ]

    def _rows(self) -> list:
        return [r for r in self.sections[self.index][1] if r.isVisibleTo(self)]

    def _refresh(self) -> None:
        for _t, rows in self.sections:
            for r in rows:
                r.refresh()

    def _changed(self, key: str) -> None:
        for _t, rows in self.sections:
            for r in rows:
                if getattr(r, "key", None) == key:
                    r.refresh()

    # --------------------------------------------------------- navigation
    def _select(self, i: int) -> None:
        self.index = i % len(self.sections)
        for n, b in enumerate(self.cats):
            b.setChecked(n == self.index)
        self.stack.setCurrentIndex(self.index)
        self.areas[self.index].verticalScrollBar().setValue(0)
        title = self.sections[self.index][0]
        self.section_title.setText(title)
        self.crumb.setText(f"›  {title}")

    def _enter(self) -> None:
        rows = self._rows()
        if rows:
            self._focus_row(rows[0])

    def _focus_row(self, row) -> None:
        row.setFocus()
        self.areas[self.index].ensureWidgetVisible(row, 0, int(40 * self.scale))

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.KeyPress:
            return False
        key = e.key()
        in_rows = isinstance(obj, Row)
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown) and (in_rows or obj in self.cats):
            self._select(self.index + (1 if key == Qt.Key.Key_PageDown else -1))
            self._enter() if in_rows else self.cats[self.index].setFocus()
            return True
        if obj in self.cats:
            if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                i = max(0, min(len(self.cats) - 1, self.index + (1 if key == Qt.Key.Key_Down else -1)))
                self._select(i)
                self.cats[i].setFocus()
            elif key in (Qt.Key.Key_Right, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._enter()
            elif key == Qt.Key.Key_Escape:
                self.win.go_back()
            return key not in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab)
        if not in_rows:
            return False
        rows = self._rows()
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and obj in rows:
            i = rows.index(obj) + (1 if key == Qt.Key.Key_Down else -1)
            if 0 <= i < len(rows):
                self._focus_row(rows[i])
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if not obj.step(1 if key == Qt.Key.Key_Right else -1) and key == Qt.Key.Key_Left:
                self.cats[self.index].setFocus()  # nothing to change here: back to the sections
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            obj.activate()
        elif key == Qt.Key.Key_Escape:
            self.cats[self.index].setFocus()
        else:
            return False
        return True

    def on_show(self, arg=None) -> None:
        self.close_editor()
        self._refresh()
        QTimer.singleShot(0, self.cats[self.index].setFocus)

    def set_controller(self, kind: str) -> None:
        items = PROMPTS.get(kind, PROMPTS["xbox"])
        sh = ("L1", "R1") if kind == "playstation" else ("LB", "RB")
        self.prompts.set_items(((sh[0], "#8c84b8", ""), (sh[1], "#8c84b8", "Section"), ("◀▶", "#8c84b8", "Change"),
                                items[0], items[2]))

    # ------------------------------------------------------------- typing
    def edit_text(self, title: str, text: str, secret: bool, done) -> None:
        """A full-screen editor: the text above the on-screen keyboard, so it stays visible while typing."""
        if self.editor is None:
            self.editor = QFrame(self)
            self.editor.setObjectName("HHEditor")
            ev = QVBoxLayout(self.editor)
            ev.addStretch(1)
            self.edit_title = QLabel()
            self.edit_title.setObjectName("HHSection")
            self.edit = QLineEdit()
            self.edit.setObjectName("HHEdit")
            self.edit.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
            self.edit.returnPressed.connect(lambda: self.keyboard.submit_key.click())
            self.keyboard = Keyboard(self.edit, self._submit_edit, self.editor, submit="Done")
            self.keyboard.on_close = self.close_editor
            for w in (self.edit_title, self.edit):
                ev.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)
            ev.addSpacing(16)
            ev.addWidget(self.keyboard, 0, Qt.AlignmentFlag.AlignHCenter)
            ev.addStretch(1)
            self._style_editor()
        self._edit_done = done
        self._edit_from = QApplication.focusWidget()
        self.edit_title.setText(title)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
        self.edit.setText(text)
        self.editor.setGeometry(self.rect())
        self.editor.show()
        self.editor.raise_()
        self.keyboard.show()
        r, c = self.keyboard.pos
        self.keyboard.cells[r][c].setFocus()

    def _submit_edit(self, text: str) -> None:
        done = self._edit_done
        self.close_editor()
        done(text)

    def close_editor(self) -> None:
        if self.editor is None or not self.editor.isVisible():
            return
        self.editor.hide()
        back = self._edit_from
        if isinstance(back, Row) and back.isVisible():
            back.setFocus()
        else:
            self.cats[self.index].setFocus()

    # ------------------------------------------------------------ scaling
    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self.editor is not None:
            self.editor.setGeometry(self.rect())
        s = max(0.8, min(2.4, self.height() / BASE_HEIGHT))
        if abs(s - self.scale) > 0.02 or not getattr(self, "_styled", False):
            self.scale, self._styled = s, True
            self._apply_scale()

    def _apply_scale(self) -> None:
        s = self.scale
        self.layout().setContentsMargins(int(48 * s), int(26 * s), int(48 * s), int(16 * s))
        self.layout().setSpacing(int(16 * s))
        self.layout().itemAt(1).layout().setSpacing(int(22 * s))
        self.nav.setFixedWidth(int(270 * s))
        self.panel.layout().setContentsMargins(int(24 * s), int(18 * s), int(24 * s), int(18 * s))
        self.heading.setStyleSheet(f"font-size: {30 * s:.1f}pt; background: transparent;")
        self.crumb.setStyleSheet(f"font-size: {11 * s:.1f}pt; background: transparent;")
        self.setStyleSheet(
            f"#HHCat {{ font-size: {14 * s:.1f}pt; font-weight: 700; text-align: left; color: #b9b3d6;"
            f" padding: {12 * s:.0f}px {18 * s:.0f}px; border-radius: {10 * s:.0f}px; background: transparent;"
            " border: 2px solid transparent; }"
            " #HHCat:checked { background: rgba(140, 98, 212, 0.28); color: white; }"
            " #HHCat:focus { background: rgba(140, 98, 212, 0.55); border-color: #b79cff; color: white; }"
            f" #HHPanel {{ background: rgba(12, 9, 28, 0.62); border: 1px solid rgba(183, 156, 255, 0.16);"
            f" border-radius: {16 * s:.0f}px; }}"
            f" #HHRow {{ background: rgba(255, 255, 255, 0.04); border-radius: {10 * s:.0f}px;"
            " border: 2px solid transparent; }"
            " #HHRow:focus { background: rgba(140, 98, 212, 0.32); border-color: #b79cff; }"
            f" QLabel#HHRowTitle {{ font-size: {13.5 * s:.1f}pt; font-weight: 700; color: white;"
            " background: transparent; }"
            f" QLabel#HHRowDesc {{ font-size: {10 * s:.1f}pt; color: #9a93c4; background: transparent; }}"
            f" QLabel#HHRowValue {{ font-size: {12.5 * s:.1f}pt; font-weight: 700; color: #e6e1ff;"
            " background: transparent; }"
            f" QLabel#HHSection {{ font-size: {19 * s:.1f}pt; font-weight: 800; color: white;"
            f" background: transparent; padding-bottom: {6 * s:.0f}px; }}")
        for _t, rows in self.sections:
            for r in rows:
                r.layout().setContentsMargins(int(18 * s), int(12 * s), int(18 * s), int(12 * s))
                if isinstance(r, ToggleRow):
                    r.switch.setFixedSize(int(56 * s), int(30 * s))
                if isinstance(r, ActionRow):  # on the label itself: the app's sheet would win over the page's
                    r.value.setStyleSheet(
                        f"font-size: {12 * s:.1f}pt; font-weight: 800; color: #0b1a0b; background: #5fb85f;"
                        f" padding: {6 * s:.0f}px {18 * s:.0f}px; border-radius: {8 * s:.0f}px;")
        self.prompts.set_scale(s)
        self._style_editor()

    def _style_editor(self) -> None:
        if self.editor is None:
            return
        s = self.scale
        self.editor.setStyleSheet(
            "#HHEditor { background: rgba(6, 5, 16, 0.88); }"
            f" QLineEdit#HHEdit {{ font-size: {16 * s:.1f}pt; padding: {10 * s:.0f}px {16 * s:.0f}px;"
            f" border-radius: {12 * s:.0f}px; background: rgba(255, 255, 255, 0.08); color: white;"
            " border: 2px solid #b79cff; }")
        self.edit.setFixedWidth(int(820 * s))
        self.keyboard.set_scale(s)
