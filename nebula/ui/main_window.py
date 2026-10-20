"""Main application window."""
from __future__ import annotations

import logging
import shutil
import subprocess

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QMenu, QStackedWidget, QSystemTrayIcon, \
    QVBoxLayout, QWidget

from nebula import APP_NAME
from nebula.core import autostart
from nebula.ui import handheld as handheld_mod
from nebula.ui import icons, theme
from nebula.ui.controller import UiController
from nebula.ui.friends import FriendsPanel
from nebula.ui.pages.downloads import DownloadsPage
from nebula.ui.pages.game import GamePage
from nebula.ui.pages.handheld import HandheldGamePage, HandheldPage, HandheldProductPage
from nebula.ui.pages.home import HomePage
from nebula.ui.pages.library import LibraryPage
from nebula.ui.pages.settings import SettingsPage
from nebula.ui.pages.stats import StatsPage
from nebula.ui.pages.store import ProductPage, StorePage
from nebula.ui.pages.welcome import WelcomePage
from nebula.ui.shell import Sidebar, TopBar
from nebula.ui.widgets.common import ToastHost
from nebula.ui.widgets.starfield import Starfield
from nebula.ui.widgets.update_banner import UpdateBanner

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, services):
        super().__init__()
        self.s = services
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1000, 640)
        self.ctl = UiController(services, self)
        self.history: list[tuple[str, object]] = []
        self.hist_index = -1
        self._quitting = False

        central = Starfield()
        central.setObjectName("Central")
        self.starfield = central
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.sidebar = Sidebar(services, self.ctl, self)
        root.addWidget(self.sidebar)
        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(0)
        self.topbar = TopBar(services, self.ctl, self)
        self.sidebar.dock_tools(self.topbar)
        self.topbar.search_submitted.connect(self._search)
        mid.addWidget(self.topbar)
        self.update_banner = UpdateBanner(services, self)
        mid.addWidget(self.update_banner)
        self.stack = QStackedWidget()
        self.stack.setObjectName("Pages")
        mid.addWidget(self.stack, 1)
        root.addLayout(mid, 1)
        self.friends = FriendsPanel(services)
        self.friends.setVisible(bool(services.settings.get("window.friends_open")))
        root.addWidget(self.friends)
        self.setCentralWidget(central)

        self.pages = {
            "home": HomePage(services, self.ctl, self),
            "library": LibraryPage(services, self.ctl, self),
            "game": GamePage(services, self.ctl, self),
            "downloads": DownloadsPage(services, self.ctl, self),
            "store": StorePage(services, self.ctl, self),
            "wishlist": StorePage(services, self.ctl, self, wishlist_mode=True),
            "product": ProductPage(services, self.ctl, self),
            "stats": StatsPage(services, self.ctl, self),
            "settings": SettingsPage(services, self.ctl, self),
            "welcome": WelcomePage(services, self.ctl, self),
            "handheld": HandheldPage(services, self.ctl, self),
            "handheld_game": HandheldGamePage(services, self.ctl, self),
            "handheld_product": HandheldProductPage(services, self.ctl, self),
        }
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.toasts = ToastHost(self)
        self._setup_tray()
        self._shortcuts()
        bus = services.bus
        bus.notify.connect(self._notify)
        bus.error.connect(self._error)
        bus.navigate.connect(self.navigate)
        bus.game_started.connect(self._game_started)
        bus.game_stopped.connect(self._game_stopped)
        bus.game_detected.connect(self._game_detected)
        from nebula.ui.fun import Fun

        self.fun = Fun(self)  # after the window's own game handlers, so the sky is awake for celebrations
        services.settings.on_change(self._setting_changed)
        self.apply_appearance()
        geo = services.settings.get("window.geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        self.topbar.friends.setChecked(self.friends.isVisible())

    # --------------------------------------------------------- navigation
    def navigate(self, page: str, arg=None, record: bool = True) -> None:
        if page == "friends":
            self.toggle_friends()
            return
        if hasattr(self, "_nav"):
            self._nav.close_keyboard()
        w = self.pages.get(page)
        if w is None:
            return
        if record:
            self.history = self.history[: self.hist_index + 1]
            if not self.history or self.history[-1] != (page, arg):
                self.history.append((page, arg))
            self.hist_index = len(self.history) - 1
        self._apply_chrome(page)
        self.stack.setCurrentWidget(w)
        w.on_show(arg)
        self.sidebar.select(page, arg)
        self.topbar.back.setEnabled(self.hist_index > 0)
        self.topbar.fwd.setEnabled(self.hist_index < len(self.history) - 1)

    def start_page(self) -> None:
        """Galaxy-style start: ask to sign in with GOG first, otherwise open Home."""
        gog = self.s.integrations.get("gog")
        if theme.HANDHELD:
            self.navigate("handheld")
        elif gog and not gog.connected() and not self.s.db.kv_get("ui.welcome_dismissed"):
            self.navigate("welcome", record=False)
        else:
            self.navigate("home")

    def apply_appearance(self) -> None:
        st = self.s.settings
        fonts = (st.get("appearance.font_ui") or "", st.get("appearance.font_display") or "",
                 st.get("appearance.font_sidebar") or "")
        mode = "classic" if st.get("appearance.theme") == "classic" else "nebula"
        handheld = st.get("handheld.mode") == "on"
        if handheld != theme.HANDHELD:
            theme.HANDHELD = handheld
            theme.MODE = ""  # force the style sheet to be rebuilt below
            self._set_handheld(handheld)
        self.starfield.set_classic(mode == "classic")
        if fonts != (theme.FONTS["ui"], theme.FONTS["display"], theme.FONTS["sidebar"]) or mode != theme.MODE:
            theme.set_fonts(QApplication.instance(), *fonts, mode=mode)
            self.sidebar.orbit.arrange()
            for w in self.sidebar.findChildren(QWidget) + [self.sidebar]:
                w.update()
        self.starfield.set_stars(bool(st.get("appearance.starfield")))
        running = bool(self.s.launcher.running)
        self.starfield.set_fun(bool(st.get("appearance.fun")))
        self.starfield.set_animated(bool(st.get("appearance.animate_stars")) and not running)

    def _setting_changed(self, key: str, value) -> None:
        if key.startswith("appearance.") or key == "handheld.mode":
            self.apply_appearance()
        elif key == "general.autostart":
            autostart.apply(bool(value))

    def _apply_chrome(self, page: str) -> None:
        """Sidebar, top bar and friends panel: hidden on the welcome screen and everywhere in handheld mode."""
        chrome = page not in ("welcome", "handheld") and not theme.HANDHELD
        self.sidebar.setVisible(chrome)
        self.topbar.setVisible(chrome)
        friends = chrome and bool(self.s.settings.get("window.friends_open"))
        self.friends.setVisible(friends)
        self.topbar.friends.setChecked(friends)

    def _current_page(self) -> str:
        w = self.stack.currentWidget()
        return next((k for k, p in self.pages.items() if p is w), "home")

    def _set_handheld(self, on: bool) -> None:
        if not hasattr(self, "_gamepad"):
            self._gamepad = handheld_mod.Gamepad(on_kind=self.pages["handheld"].set_controller)
            self._nav = handheld_mod.Navigation(self)
        app = QApplication.instance()
        self.pages["settings"].set_handheld(on)
        if on:
            self._gamepad.start()
            app.installEventFilter(self._nav)
            if hasattr(self, "fun"):
                self.fun.install()  # still sees the arrows navigation swallows (Konami code)
            if self.isVisible():
                self.showFullScreen()
                self.navigate("handheld")
            else:
                self._apply_chrome(self._current_page())
        else:
            self._nav.close_keyboard()
            self._gamepad.stop()
            app.removeEventFilter(self._nav)
            if self.isFullScreen():
                self.showNormal()
            # forget the handheld screen in back/forward history, then bring the desktop chrome back
            self.history = [h for h in self.history if not h[0].startswith("handheld")]
            self.hist_index = len(self.history) - 1
            if self._current_page().startswith("handheld"):
                self.navigate("home")
            else:
                self._apply_chrome(self._current_page())

    def go_back(self) -> None:
        if self.hist_index > 0:
            self.hist_index -= 1
            self.navigate(*self.history[self.hist_index], record=False)

    def go_forward(self) -> None:
        if self.hist_index < len(self.history) - 1:
            self.hist_index += 1
            self.navigate(*self.history[self.hist_index], record=False)

    def toggle_friends(self) -> None:
        vis = not self.friends.isVisible()
        self.friends.setVisible(vis)
        self.topbar.friends.setChecked(vis)
        self.s.settings.set("window.friends_open", vis)
        if vis:
            self.s.friends.refresh()

    def _search(self, text: str) -> None:
        if not text:
            return
        if self.stack.currentWidget() in (self.pages["store"], self.pages["product"]):
            self.navigate("store", text)
            return
        lib = self.pages["library"]
        self.navigate("library", {"title": f"Search: {text}", "filter": {"text": text, "hidden": "show"}})
        lib.search.setText(text)

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+F"), self, activated=lambda: self.topbar.search.setFocus())
        QShortcut(QKeySequence("Alt+Left"), self, activated=self.go_back)
        QShortcut(QKeySequence("Alt+Right"), self, activated=self.go_forward)
        QShortcut(QKeySequence(Qt.Key.Key_Back), self, activated=self.go_back)
        QShortcut(QKeySequence("F5"), self, activated=lambda: self.s.library.refresh())
        QShortcut(QKeySequence("Ctrl+R"), self, activated=lambda: self.s.library.refresh())
        QShortcut(QKeySequence("Ctrl+,"), self, activated=lambda: self.navigate("settings"))
        QShortcut(QKeySequence("Ctrl+1"), self, activated=lambda: self.navigate("home"))
        QShortcut(QKeySequence("Ctrl+2"), self, activated=lambda: self.navigate("library", "owned"))
        QShortcut(QKeySequence("Ctrl+3"), self, activated=lambda: self.navigate("library", "installed"))
        QShortcut(QKeySequence("Ctrl+4"), self, activated=lambda: self.navigate("store"))
        QShortcut(QKeySequence("Ctrl+5"), self, activated=lambda: self.navigate("downloads"))
        QShortcut(QKeySequence("Ctrl+Q"), self, activated=self.quit_app)
        QShortcut(QKeySequence("F11"), self, activated=lambda: self.s.settings.set(
            "handheld.mode", "off" if theme.HANDHELD else "on"))  # leave (or enter) handheld mode

    # ------------------------------------------------------ notifications
    def celebrate(self, title: str, body: str = "") -> None:
        if self.s.settings.get("appearance.fun"):
            self.starfield.fireworks(5)
        self.toast(title, body)

    def toast(self, title: str, body: str = "", kind: str = "info") -> None:
        self.toasts.show(title, body, kind)

    def _notify(self, title: str, body: str) -> None:
        if self.isVisible() and self.isActiveWindow():
            self.toast(title, body)
        elif self.s.settings.get("general.notifications"):
            self._desktop_notify(title, body)
            if self.isVisible():
                self.toast(title, body)

    def _error(self, title: str, body: str) -> None:
        if title.startswith("Could not start") and "Proton" in body:
            self.ctl._no_runner(body)  # noqa: SLF001
            return
        self.toast(title, body, "error")

    def _desktop_notify(self, title: str, body: str) -> None:
        if self.tray and self.tray.isVisible() and QSystemTrayIcon.supportsMessages():
            self.tray.showMessage(title, body, icons.app_icon(), 5000)
        elif shutil.which("notify-send"):
            subprocess.Popen(["notify-send", "-a", APP_NAME, title, body], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self.toasts.relayout()

    # -------------------------------------------------------- game events
    def _game_started(self, uid: str) -> None:
        st = self.s.launcher.running.get(uid)
        if st and not st.external and self.s.settings.get("appearance.fun"):
            self.starfield.warp()  # jump to hyperspace while the game loads, then rest the sky
            QTimer.singleShot(1500, self.apply_appearance)
        else:
            self.apply_appearance()  # stop animating the sky while a game runs
        behaviour = self.s.settings.get("general.launch_behaviour")
        if st and not st.external:
            if behaviour == "minimize":
                self.showMinimized()
            elif behaviour == "hide" and self.tray and self.tray.isVisible():
                self.hide()
        self._rebuild_tray_menu()

    def _game_detected(self, uid: str) -> None:
        """A game started through Steam (or another client) is up: in Big Picture, step aside so it isn't
        stuck behind the full-screen launcher; it comes back when the game exits."""
        if theme.HANDHELD and self.isVisible() and not self.isMinimized():
            self._stepped_aside = uid
            self.showMinimized()

    def _game_stopped(self, uid: str, seconds: int) -> None:
        self.apply_appearance()
        if getattr(self, "_stepped_aside", "") == uid:
            self._stepped_aside = ""
            self.show_window()
        if self.s.settings.get("general.launch_behaviour") in ("minimize", "hide") and not self.isVisible():
            self.show_window()
        self._rebuild_tray_menu()

    # --------------------------------------------------------------- tray
    def _setup_tray(self) -> None:
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(icons.app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        self.tray.activated.connect(lambda reason: self.toggle_window()
                                    if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray_menu = QMenu()
        self.tray.setContextMenu(self.tray_menu)
        self._rebuild_tray_menu()
        self.tray.setVisible(bool(self.s.settings.get("general.show_tray")))
        self.s.settings.on_change(lambda k, v: self.tray.setVisible(bool(v)) if k == "general.show_tray" else None)

    def _rebuild_tray_menu(self) -> None:
        if not self.tray:
            return
        m = self.tray_menu
        m.clear()
        m.addAction(icons.icon("nebula"), f"Show {APP_NAME}", self.show_window)
        m.addSeparator()
        running = list(self.s.launcher.running.values())
        for st in running:
            act = QAction(icons.icon("stop"), f"Stop {st.title}", m)
            act.triggered.connect(lambda _c=False, u=st.uid: self.s.launcher.stop(u))
            m.addAction(act)
        recent = self.s.library.recently_played(6)
        if recent:
            m.addSection("Recent games")
            for e in recent:
                if e.installed:
                    m.addAction(icons.icon("play"), e.title, lambda e=e: self.ctl.play(e))
        m.addSeparator()
        jobs = self.s.jobs.active_jobs()
        if jobs:
            paused = any(j.state == "paused" for j in jobs)
            m.addAction(icons.icon("play" if paused else "pause"),
                        "Resume downloads" if paused else "Pause downloads",
                        self.s.jobs.resume_all if paused else self.s.jobs.pause_all)
        m.addAction(icons.icon("power"), "Quit", self.quit_app)

    def show_window(self) -> None:
        self.show()
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def toggle_window(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide()
        else:
            self.show_window()

    def closeEvent(self, e):  # noqa: N802
        self.s.settings.set("window.geometry", bytes(self.saveGeometry().toBase64()).decode())
        if not self._quitting and self.tray and self.tray.isVisible() and self.s.settings.get("general.close_to_tray"):
            e.ignore()
            self.hide()
            if not self.s.db.kv_get("ui.tray_hint_shown"):
                self.s.db.kv_set("ui.tray_hint_shown", True)
                self._desktop_notify(APP_NAME, "Nebula keeps running in the tray. Use Quit to exit.")
            return
        self.quit_app()

    def quit_app(self) -> None:
        if self.s.jobs.active_jobs() and not self._quitting:
            from PySide6.QtWidgets import QMessageBox

            if QMessageBox.question(self, "Quit", "Downloads are in progress. Quit anyway? They can be resumed "
                                                  "later.") != QMessageBox.StandardButton.Yes:
                return
        self._quitting = True
        self.s.settings.set("window.geometry", bytes(self.saveGeometry().toBase64()).decode())
        if self.tray:
            self.tray.hide()
        QApplication.instance().quit()
