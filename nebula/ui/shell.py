"""Sidebar and top bar."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (QButtonGroup, QCompleter, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from nebula.core.utils import xdg_open
from nebula.ui import icons, theme
from nebula.ui.widgets.common import Avatar, Spinner, icon_button, label


class SidebarButton(QPushButton):
    def __init__(self, text: str, icon_name: str | None = None, pixmap=None):
        super().__init__()
        self.setObjectName("SidebarButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 12, 0)
        lay.setSpacing(10)
        self.ic = QLabel()
        self.ic.setFixedSize(18, 18)
        self.ic.setScaledContents(False)
        self._icon_name = icon_name
        self._pixmap = pixmap
        self.text_lbl = QLabel(text)
        self.count = QLabel("")
        self.count.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size: 8pt;")
        self.spinner = Spinner(12)
        self.spinner.hide()
        for w in (self.ic, self.text_lbl, self.count, self.spinner):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            w.setStyleSheet(w.styleSheet() + "background: transparent;")
        lay.addWidget(self.ic)
        lay.addWidget(self.text_lbl, 1)
        lay.addWidget(self.spinner)
        lay.addWidget(self.count)
        self.toggled.connect(lambda _c: self._paint_icon())
        self._paint_icon()

    def _paint_icon(self) -> None:
        if self._pixmap is not None:
            self.ic.setPixmap(self._pixmap)
        elif self._icon_name:
            self.ic.setPixmap(icons.pixmap(self._icon_name, 18, theme.TEXT if self.isChecked() else theme.TEXT_DIM))
        self.text_lbl.setStyleSheet(f"background: transparent; color: {theme.TEXT if self.isChecked() else theme.TEXT_DIM};")

    def set_count(self, n) -> None:
        self.count.setText(str(n) if n else "")

    def set_badge(self, n: int) -> None:
        if n:
            self.count.setText(f" {n} ")
            self.count.setStyleSheet(f"background:{theme.ACCENT}; color:white; border-radius:7px; font-size:8pt; "
                                     "font-weight:700; padding: 0 3px;")
        else:
            self.count.setText("")
            self.count.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size: 8pt; background: transparent;")

    def set_syncing(self, on: bool) -> None:
        if on:
            self.spinner.start()
        else:
            self.spinner.stop()


class Sidebar(QWidget):
    def __init__(self, services, ctl, window):
        super().__init__()
        self.s = services
        self.ctl = ctl
        self.win = window
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(236)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # profile
        prof = QPushButton()
        prof.setObjectName("SidebarButton")
        prof.setCursor(Qt.CursorShape.PointingHandCursor)
        prof.setMinimumHeight(64)
        pl = QHBoxLayout(prof)
        pl.setContentsMargins(14, 10, 10, 10)
        self.avatar = Avatar(38)
        pl.addWidget(self.avatar)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.name = label("Nebula", "ProfileName")
        self.status = label("", "ProfileStatus")
        col.addWidget(self.name)
        col.addWidget(self.status)
        pl.addLayout(col, 1)
        for w in (self.avatar, self.name, self.status):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        prof.clicked.connect(self._profile_menu)
        self.profile_btn = prof
        outer.addWidget(prof)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Sidebar")
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(0, 4, 0, 10)
        self.lay.setSpacing(2)
        area.setWidget(inner)
        outer.addWidget(area, 1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, SidebarButton] = {}
        self._build()
        # bottom actions
        bottom = QHBoxLayout()
        bottom.setContentsMargins(10, 8, 10, 12)
        add = QPushButton(" Add games")
        add.setIcon(icons.icon("plus", theme.TEXT))
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self._add_menu)
        bottom.addWidget(add, 1)
        gear = icon_button("settings", "Settings", size=20)
        gear.clicked.connect(lambda: self.win.navigate("settings"))
        bottom.addWidget(gear)
        outer.addLayout(bottom)
        bus = services.bus
        bus.library_changed.connect(self._schedule_counts)
        bus.game_changed.connect(lambda _k: self._schedule_counts())
        bus.jobs_changed.connect(self._update_downloads)
        bus.sync_state.connect(self._sync_state)
        bus.integration_changed.connect(lambda _i: self.refresh_profile())
        bus.friends_changed.connect(self._update_friends)
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(300)
        self._count_timer.timeout.connect(self.update_counts)
        self.refresh_profile()
        self.update_counts()

    def _section(self, text: str) -> None:
        self.lay.addWidget(label(text.upper(), "SidebarSection"))

    def _add(self, key: str, text: str, icon_name: str | None = None, pixmap=None) -> SidebarButton:
        b = SidebarButton(text, icon_name, pixmap)
        self.group.addButton(b)
        self.buttons[key] = b
        b.clicked.connect(lambda: self._clicked(key))
        self.lay.addWidget(b)
        return b

    def _build(self) -> None:
        self._section("Games")
        self._add("home", "Recent", "recent")
        self._add("library:owned", "Owned games", "library")
        self._add("library:installed", "Installed", "installed")
        self._add("library:favorites", "Favourites", "heart")
        self._add("library:updates", "Updates", "download")
        self._section("Platforms")
        for integ in self.s.integrations.values():
            self._add(f"library:source:{integ.id}", integ.name, pixmap=icons.badge(integ.letter, integ.color, 18))
        self.views_header = label("VIEWS", "SidebarSection")
        self.lay.addWidget(self.views_header)
        self.views_box = QVBoxLayout()
        self.views_box.setSpacing(2)
        self.lay.addLayout(self.views_box)
        self._add("library:hidden", "Hidden games", "eye-off")
        self._section("GOG.COM")
        self._add("store", "Store", "store")
        self._add("wishlist", "Wishlist", "star")
        self._section("Activity")
        self._add("friends", "Friends", "users")
        self._add("downloads", "Downloads", "download")
        self._add("stats", "Statistics", "chart")
        self.lay.addStretch(1)
        self.buttons["friends"].setCheckable(False)
        self.rebuild_views()

    def rebuild_views(self) -> None:
        while self.views_box.count():
            it = self.views_box.takeAt(0)
            w = it.widget()
            if w:
                key = next((k for k, b in self.buttons.items() if b is w), None)
                if key:
                    self.buttons.pop(key)
                self.group.removeButton(w)
                w.deleteLater()
        views = self.s.settings.get("library.custom_views") or []
        for i, v in enumerate(views):
            b = SidebarButton(v.get("name", f"View {i + 1}"), "filter")
            key = f"library:view:{i}"
            self.group.addButton(b)
            self.buttons[key] = b
            b.clicked.connect(lambda _c=False, k=key: self._clicked(k))
            self.views_box.addWidget(b)
        self.update_counts()

    def _clicked(self, key: str) -> None:
        if key == "friends":
            self.win.toggle_friends()
            return
        if key.startswith("library:"):
            self.win.navigate("library", key.split(":", 1)[1])
        else:
            self.win.navigate(key)

    def select(self, page: str, arg=None) -> None:
        key = page
        if page == "library" and isinstance(arg, str):
            key = f"library:{arg}"
        b = self.buttons.get(key)
        if b:
            b.setChecked(True)
        else:
            self.group.setExclusive(False)
            for btn in self.group.buttons():
                btn.setChecked(False)
            self.group.setExclusive(True)

    def _schedule_counts(self) -> None:
        self._count_timer.start()

    def update_counts(self) -> None:
        c = self.s.library.counts()
        self.buttons["library:owned"].set_count(c.get("owned", 0))
        self.buttons["library:installed"].set_count(c.get("installed", 0))
        self.buttons["library:favorites"].set_count(c.get("favorites", 0))
        self.buttons["library:updates"].set_badge(c.get("updates", 0))
        self.buttons["library:updates"].setVisible(bool(c.get("updates")))
        self.buttons["library:favorites"].setVisible(bool(c.get("favorites")))
        self.buttons["library:hidden"].set_count(c.get("hidden", 0))
        self.buttons["library:hidden"].setVisible(bool(c.get("hidden")))
        enabled = self.s.settings.get("integrations.enabled") or {}
        for integ in self.s.integrations.values():
            b = self.buttons.get(f"library:source:{integ.id}")
            if b:
                b.set_count(c.get(integ.id, 0))
                b.setVisible(enabled.get(integ.id, True) and (integ.connected() or c.get(integ.id, 0) > 0
                                                             or integ.id in ("gog", "local")))
        self.views_header.setVisible(bool(self.s.settings.get("library.custom_views")) or bool(c.get("hidden")))
        from nebula.core.library import LibraryFilter

        for i, v in enumerate(self.s.settings.get("library.custom_views") or []):
            b = self.buttons.get(f"library:view:{i}")
            if b:
                b.set_count(len(self.s.library.query(LibraryFilter.from_dict(v.get("filter", {})))))
        self._update_downloads()
        self._update_friends()

    def _update_downloads(self) -> None:
        self.buttons["downloads"].set_badge(len(self.s.jobs.active_jobs()))

    def _update_friends(self) -> None:
        self.buttons["friends"].set_count(self.s.friends.online_count() or "")

    def _sync_state(self, iid: str, on: bool) -> None:
        b = self.buttons.get(f"library:source:{iid}")
        if b:
            b.set_syncing(on)

    def refresh_profile(self) -> None:
        gog = self.s.integrations.get("gog")
        if gog and gog.connected():
            name = gog.account_name() or "GOG user"
            self.name.setText(name)
            self.status.setText("Online • GOG")
            self.avatar.set_avatar(gog.avatar_url(), name, theme.SUCCESS)
        else:
            steam = self.s.integrations.get("steam")
            name = (steam.account_name() if steam and steam.available() else "") or "Nebula"
            self.name.setText(name)
            self.status.setText("Click to sign in to GOG")
            self.avatar.set_avatar("", name)
        self.update_counts()

    def _profile_menu(self) -> None:
        m = QMenu(self)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        gog = self.s.integrations.get("gog")
        if gog and gog.connected():
            m.addAction(icons.icon("external"), "View GOG profile",
                        lambda: xdg_open(f"https://www.gog.com/u/{gog.account_name()}"))
            m.addAction(icons.icon("logout"), "Sign out of GOG", lambda: self.win.navigate("settings", "Integrations"))
        else:
            m.addAction(icons.icon("user"), "Sign in to GOG", self.ctl.gog_login)
        m.addAction(icons.icon("link"), "Connect platforms…", lambda: self.win.navigate("settings", "Integrations"))
        m.addSeparator()
        m.addAction(icons.icon("settings"), "Settings", lambda: self.win.navigate("settings"))
        m.addAction(icons.icon("power"), "Quit Nebula", self.win.quit_app)
        m.exec(self.profile_btn.mapToGlobal(self.profile_btn.rect().bottomLeft()))

    def _add_menu(self) -> None:
        m = QMenu(self)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        m.addAction(icons.icon("plus"), "Add game manually…", self.ctl.add_game)
        m.addAction(icons.icon("folder"), "Scan folder for installed games…", self.ctl.scan_folder)
        m.addAction(icons.icon("link"), "Connect gaming platforms…",
                    lambda: self.win.navigate("settings", "Integrations"))
        m.addAction(icons.icon("proton"), "Download GE-Proton…", self.ctl.proton_download)
        btn = self.sender()
        m.exec(btn.mapToGlobal(btn.rect().topLeft()) - QPoint(0, m.sizeHint().height()))


class TopBar(QWidget):
    search_submitted = Signal(str)

    def __init__(self, services, window):
        super().__init__()
        self.s = services
        self.win = window
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 8, 16, 8)
        lay.setSpacing(6)
        self.back = icon_button("back", "Back (Alt+Left)")
        self.fwd = icon_button("forward", "Forward (Alt+Right)")
        self.back.clicked.connect(window.go_back)
        self.fwd.clicked.connect(window.go_forward)
        lay.addWidget(self.back)
        lay.addWidget(self.fwd)
        lay.addSpacing(8)
        self.search = QLineEdit()
        self.search.setObjectName("SearchBox")
        self.search.setPlaceholderText("Search your games and the store  (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.returnPressed.connect(lambda: self.search_submitted.emit(self.search.text().strip()))
        self._titles = QStringListModel()
        comp = QCompleter(self._titles, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setMaxVisibleItems(10)
        comp.activated.connect(self._completed)
        self.search.setCompleter(comp)
        lay.addWidget(self.search)
        lay.addStretch(1)
        self.sync_spinner = Spinner(16)
        self.sync_spinner.hide()
        self.sync_lbl = label("", "Faint")
        lay.addWidget(self.sync_spinner)
        lay.addWidget(self.sync_lbl)
        self.sync = icon_button("refresh", "Sync all platforms (F5)")
        self.sync.clicked.connect(lambda: self.s.library.refresh())
        lay.addWidget(self.sync)
        self.dl = QPushButton()
        self.dl.setObjectName("IconButton")
        self.dl.setIcon(icons.icon("download"))
        self.dl.setIconSize(QSize(18, 18))
        self.dl.setToolTip("Downloads")
        self.dl.clicked.connect(lambda: self.win.navigate("downloads"))
        lay.addWidget(self.dl)
        self.friends = QPushButton()
        self.friends.setObjectName("IconButton")
        self.friends.setIcon(icons.icon("users"))
        self.friends.setIconSize(QSize(18, 18))
        self.friends.setCheckable(True)
        self.friends.setToolTip("Friends")
        self.friends.clicked.connect(window.toggle_friends)
        lay.addWidget(self.friends)
        services.bus.sync_state.connect(self._sync)
        services.bus.library_changed.connect(self._update_titles)
        services.bus.jobs_changed.connect(self._downloads)
        services.bus.job_progress.connect(lambda _j: self._downloads())
        services.bus.friends_changed.connect(self._friends)
        self._update_titles()

    def _sync(self, *_a) -> None:
        syncing = self.s.library.syncing
        if syncing:
            self.sync_spinner.start()
            names = [self.s.integrations[i].name for i in syncing if i in self.s.integrations]
            self.sync_lbl.setText("Syncing " + ", ".join(names))
        else:
            self.sync_spinner.stop()
            self.sync_lbl.setText("")

    def _update_titles(self) -> None:
        self._titles.setStringList(sorted({e.title for e in self.s.library.all_entries() if not e.user.hidden},
                                          key=str.lower))

    def _completed(self, title: str) -> None:
        e = next((e for e in self.s.library.all_entries() if e.title == title), None)
        if e:
            self.win.navigate("game", e.key)
            QTimer.singleShot(0, self.search.clear)

    def _downloads(self) -> None:
        jobs = [j for j in self.s.jobs.active_jobs()]
        if jobs:
            running = [j for j in jobs if j.state == "running"]
            pct = int(sum(j.progress for j in jobs) * 100 / len(jobs))
            self.dl.setText(f" {pct}%" if running else f" {len(jobs)}")
            self.dl.setIcon(icons.icon("download", theme.ACCENT_HOVER))
        else:
            self.dl.setText("")
            self.dl.setIcon(icons.icon("download"))

    def _friends(self) -> None:
        n = self.s.friends.online_count()
        self.friends.setText(f" {n}" if n else "")
