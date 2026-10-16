"""Library page: cover grid or list with Galaxy-style filters, sorting and saved views."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QHBoxLayout, QLineEdit, QMenu, QSlider, QStackedWidget, QVBoxLayout,
                               QWidget)

from nebula.core.library import SORT_LABELS, LibraryFilter
from nebula.ui import icons
from nebula.ui.dialogs.misc import TextInputDialog
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import FlowLayout, button, clear_layout, icon_button, label, tool_button
from nebula.ui.widgets.gamegrid import CoverGrid, EntryTable

PRESETS = {
    "owned": ("Owned games", {}),
    "installed": ("Installed", {"installed": "installed"}),
    "favorites": ("Favourites", {"favorites": True}),
    "hidden": ("Hidden games", {"hidden": "only"}),
    "updates": ("Updates available", {"updates": True, "installed": "installed"}),
}


class LibraryPage(Page):
    name = "Library"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.base = LibraryFilter()
        self.flt = LibraryFilter()
        self.view_title = "Owned games"
        self.view_index: int | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 22, 32, 0)
        lay.setSpacing(12)
        head = QHBoxLayout()
        self.title_lbl = label("Owned games", "PageTitle")
        head.addWidget(self.title_lbl)
        self.count_lbl = label("", "Subtle")
        head.addWidget(self.count_lbl, 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        self.sync_btn = icon_button("refresh", "Sync all platforms")
        self.sync_btn.clicked.connect(lambda: self.s.library.refresh())
        head.addWidget(self.sync_btn)
        lay.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by title, tag or developer")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(320)
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._text_changed)
        bar.addWidget(self.search)
        self.filter_btn = tool_button("filter", "Filters")
        self.filter_btn.setText(" Filters")
        self.filter_btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.filter_menu = QMenu(self)
        self.filter_menu.aboutToShow.connect(self._build_filter_menu)
        self.filter_btn.setMenu(self.filter_menu)
        bar.addWidget(self.filter_btn)
        bar.addSpacing(6)
        bar.addWidget(label("Sort", "Faint"))
        self.sort = QComboBox()
        for k, v in SORT_LABELS.items():
            self.sort.addItem(v, k)
        self.sort.setCurrentIndex(max(0, self.sort.findData(self.s.settings.get("library.sort"))))
        self.sort.currentIndexChanged.connect(self._sort_changed)
        bar.addWidget(self.sort)
        self.desc_btn = icon_button("sort", "Reverse order", checkable=True)
        self.desc_btn.setChecked(bool(self.s.settings.get("library.sort_desc")))
        self.desc_btn.toggled.connect(self._sort_changed)
        bar.addWidget(self.desc_btn)
        bar.addStretch(1)
        self.save_view = button("Save view", "plus")
        self.save_view.clicked.connect(self._save_view)
        bar.addWidget(self.save_view)
        self.delete_view = button("Delete view", "trash")
        self.delete_view.clicked.connect(self._delete_view)
        bar.addWidget(self.delete_view)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(110, 260)
        self.size_slider.setValue(int(self.s.settings.get("library.cover_size") or 170))
        self.size_slider.setFixedWidth(110)
        self.size_slider.setToolTip("Cover size")
        self.size_slider.valueChanged.connect(self._cover_size)
        bar.addWidget(self.size_slider)
        self.grid_btn = icon_button("grid", "Grid view", checkable=True)
        self.list_btn = icon_button("list", "List view", checkable=True)
        self.grid_btn.clicked.connect(lambda: self._set_view("grid"))
        self.list_btn.clicked.connect(lambda: self._set_view("list"))
        bar.addWidget(self.grid_btn)
        bar.addWidget(self.list_btn)
        lay.addLayout(bar)

        self.chips_host = QWidget()
        self.chips = FlowLayout(self.chips_host, 6)
        lay.addWidget(self.chips_host)

        self.stack = QStackedWidget()
        self.grid = CoverGrid(services, int(self.s.settings.get("library.cover_size") or 170))
        self.table = EntryTable(services)
        self.table.set_visible_columns(self.s.settings.get("library.list_columns"))
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.addStretch(1)
        self.empty_lbl = label("No games match these filters.", "Subtle")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        el.addWidget(self.empty_lbl)
        self.empty_btn = button("Clear filters")
        self.empty_btn.clicked.connect(self._clear)
        el.addWidget(self.empty_btn, 0, Qt.AlignmentFlag.AlignCenter)
        el.addStretch(2)
        self.stack.addWidget(self.grid)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty)
        lay.addWidget(self.stack, 1)
        for v in (self.grid, self.table):
            v.open_entry.connect(lambda k: self.win.navigate("game", k))
            v.context_entry.connect(lambda k, pos: ctl.entry_menu(k).exec(pos))
        self.grid.play_entry.connect(ctl.primary_action)
        self.table.sort_requested.connect(self._header_sort)
        self._view = self.s.settings.get("library.view") or "grid"
        self._pending = False
        services.bus.library_changed.connect(self._schedule)
        services.bus.game_changed.connect(self._entry_changed)
        services.bus.jobs_changed.connect(lambda: self.grid.viewport().update())
        services.bus.job_progress.connect(lambda _j: self.grid.viewport().update() if self.isVisible() else None)
        services.bus.game_started.connect(lambda _u: self.grid.viewport().update())
        services.bus.game_stopped.connect(lambda *_: self._schedule())
        services.bus.sync_state.connect(lambda *_: self.sync_btn.setEnabled(not self.s.library.syncing))
        self._apply_view_buttons()

    # -------------------------------------------------------- navigation
    def on_show(self, arg=None) -> None:
        if arg is not None:
            self.view_index = None
            if isinstance(arg, str) and arg in PRESETS:
                title, f = PRESETS[arg]
            elif isinstance(arg, str) and arg.startswith("source:"):
                src = arg.split(":", 1)[1]
                integ = self.s.integrations.get(src)
                title, f = (integ.name if integ else src), {"sources": [src]}
            elif isinstance(arg, str) and arg.startswith("view:"):
                self.view_index = int(arg.split(":", 1)[1])
                views = self.s.settings.get("library.custom_views") or []
                v = views[self.view_index] if self.view_index < len(views) else {"name": "View", "filter": {}}
                title, f = v["name"], v["filter"]
            elif isinstance(arg, dict):
                title, f = arg.get("title", "Library"), arg.get("filter", {})
            else:
                title, f = PRESETS["owned"]
            self.view_title = title
            self.base = LibraryFilter.from_dict(f)
            self.flt = LibraryFilter.from_dict(f)
            self.flt.sort = self.sort.currentData()
            self.flt.desc = self.desc_btn.isChecked()
            self.search.blockSignals(True)
            self.search.setText(self.flt.text)
            self.search.blockSignals(False)
        self.title_lbl.setText(self.view_title)
        self.delete_view.setVisible(self.view_index is not None)
        self.refresh()

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            QTimer.singleShot(200, self.refresh)

    def _entry_changed(self, key: str) -> None:
        e = self.s.library.entry(key)
        if e is None:
            self._schedule()
            return
        self.grid.refresh_entry(e)
        self._schedule()

    def refresh(self) -> None:
        self._pending = False
        if not self.isVisible():
            return
        entries = self.s.library.query(self.flt)
        self.count_lbl.setText(f"  {len(entries)} game{'s' if len(entries) != 1 else ''}")
        if self._view == "grid":
            self.grid.set_entries(entries)
        else:
            self.table.set_entries(entries)
        if not entries:
            self.empty_lbl.setText("No games match these filters." if self.s.library.entries else
                                   "Your library is empty — connect a platform or add a game.")
            self.stack.setCurrentWidget(self.empty)
        else:
            self.stack.setCurrentWidget(self.grid if self._view == "grid" else self.table)
        self._chips()

    # ----------------------------------------------------------- toolbar
    def _text_changed(self, text: str) -> None:
        self.flt.text = text
        self._schedule()

    def set_search(self, text: str) -> None:
        self.search.setText(text)
        self.search.setFocus()

    def _sort_changed(self, *_a) -> None:
        self.flt.sort = self.sort.currentData()
        self.flt.desc = self.desc_btn.isChecked()
        self.s.settings.set("library.sort", self.flt.sort)
        self.s.settings.set("library.sort_desc", self.flt.desc)
        self.refresh()

    def _header_sort(self, key: str) -> None:
        if self.sort.currentData() == key:
            self.desc_btn.setChecked(not self.desc_btn.isChecked())
        else:
            self.sort.setCurrentIndex(self.sort.findData(key))

    def _cover_size(self, v: int) -> None:
        self.grid.set_cover_width(v)
        self.s.settings.set("library.cover_size", v)

    def _set_view(self, view: str) -> None:
        self._view = view
        self.s.settings.set("library.view", view)
        self._apply_view_buttons()
        self.refresh()

    def _apply_view_buttons(self) -> None:
        self.grid_btn.setChecked(self._view == "grid")
        self.list_btn.setChecked(self._view == "list")
        self.size_slider.setVisible(self._view == "grid")

    def _build_filter_menu(self) -> None:
        m = self.filter_menu
        m.clear()
        f = self.flt

        def toggle_list(attr, value, on):
            lst = list(getattr(f, attr))
            if on and value not in lst:
                lst.append(value)
            elif not on and value in lst:
                lst.remove(value)
            setattr(f, attr, lst)
            self.refresh()

        def radio(menu, attr, options):
            for val, text in options:
                a = menu.addAction(text)
                a.setCheckable(True)
                a.setChecked(getattr(f, attr) == val)
                a.triggered.connect(lambda _c=False, v=val: (setattr(f, attr, v), self.refresh()))

        plat = m.addMenu(icons.icon("gamepad"), "Platforms")
        for integ in self.s.integrations.values():
            if not integ.enabled():
                continue
            a = plat.addAction(integ.name)
            a.setCheckable(True)
            a.setChecked(integ.id in f.sources)
            a.toggled.connect(lambda on, v=integ.id: toggle_list("sources", v, on))
        inst = m.addMenu(icons.icon("installed"), "Installation")
        radio(inst, "installed", [("all", "All games"), ("installed", "Installed"), ("not_installed", "Not installed")])
        os_menu = m.addMenu(icons.icon("monitor"), "Operating system")
        for val, text in (("linux", "Linux native"), ("windows", "Windows (Proton)")):
            a = os_menu.addAction(text)
            a.setCheckable(True)
            a.setChecked(val in f.os)
            a.toggled.connect(lambda on, v=val: toggle_list("os", v, on))
        genres = m.addMenu(icons.icon("layers"), "Genres")
        for g in self.s.library.all_genres()[:40]:
            a = genres.addAction(g)
            a.setCheckable(True)
            a.setChecked(g in f.genres)
            a.toggled.connect(lambda on, v=g: toggle_list("genres", v, on))
        tags = m.addMenu(icons.icon("tag"), "Tags")
        all_tags = self.s.library.all_tags()
        if not all_tags:
            tags.addAction("No tags yet (right-click a game › Tags)").setEnabled(False)
        for t in all_tags:
            a = tags.addAction(t)
            a.setCheckable(True)
            a.setChecked(t in f.tags)
            a.toggled.connect(lambda on, v=t: toggle_list("tags", v, on))
        played = m.addMenu(icons.icon("clock"), "Play status")
        radio(played, "played", [("all", "All"), ("played", "Played"), ("unplayed", "Never played")])
        hidden = m.addMenu(icons.icon("eye-off"), "Hidden games")
        radio(hidden, "hidden", [("hide", "Don't show"), ("show", "Show"), ("only", "Only hidden")])
        m.addSeparator()
        for attr, text in (("favorites", "Favourites only"), ("updates", "Updates available")):
            a = m.addAction(text)
            a.setCheckable(True)
            a.setChecked(getattr(f, attr))
            a.toggled.connect(lambda on, at=attr: (setattr(f, at, on), self.refresh()))
        m.addSeparator()
        m.addAction("Clear all filters", self._clear)

    def _chips(self) -> None:
        clear_layout(self.chips)
        f, base = self.flt, self.base
        chips = []
        for s in f.sources:
            if s not in base.sources:
                integ = self.s.integrations.get(s)
                chips.append((integ.name if integ else s, lambda s=s: f.sources.remove(s)))
        if f.installed != base.installed:
            chips.append(({"installed": "Installed", "not_installed": "Not installed"}.get(f.installed, "All"),
                          lambda: setattr(f, "installed", base.installed)))
        for o in f.os:
            chips.append(("Linux native" if o == "linux" else "Windows", lambda o=o: f.os.remove(o)))
        for g in f.genres:
            chips.append((g, lambda g=g: f.genres.remove(g)))
        for t in f.tags:
            chips.append(("#" + t, lambda t=t: f.tags.remove(t)))
        if f.played != base.played:
            chips.append(("Played" if f.played == "played" else "Never played", lambda: setattr(f, "played", "all")))
        if f.hidden != base.hidden:
            chips.append(({"show": "Incl. hidden", "only": "Hidden only"}.get(f.hidden, ""),
                          lambda: setattr(f, "hidden", base.hidden)))
        if f.favorites and not base.favorites:
            chips.append(("Favourites", lambda: setattr(f, "favorites", False)))
        if f.updates and not base.updates:
            chips.append(("Updates", lambda: setattr(f, "updates", False)))
        for text, undo in chips:
            b = button(text + "  ✕", obj="Chip")
            b.clicked.connect(lambda _c=False, u=undo: (u(), self.refresh()))
            self.chips.addWidget(b)
        self.chips_host.setVisible(bool(chips))
        self.filter_btn.setText(f" Filters ({len(chips)})" if chips else " Filters")

    def _clear(self) -> None:
        sort, desc = self.flt.sort, self.flt.desc
        self.flt = LibraryFilter.from_dict(self.base.to_dict())
        self.flt.sort, self.flt.desc = sort, desc
        self.search.clear()
        self.refresh()

    def _save_view(self) -> None:
        dlg = TextInputDialog("Save view", "Name for this view (shown in the sidebar)", "", self)
        if dlg.exec() and dlg.value():
            views = self.s.settings.get("library.custom_views") or []
            views.append({"name": dlg.value(), "filter": self.flt.to_dict()})
            self.s.settings.set("library.custom_views", views)
            self.win.sidebar.rebuild_views()
            self.win.navigate("library", f"view:{len(views) - 1}")

    def _delete_view(self) -> None:
        if self.view_index is None:
            return
        views = self.s.settings.get("library.custom_views") or []
        if self.view_index < len(views):
            views.pop(self.view_index)
            self.s.settings.set("library.custom_views", views)
        self.win.sidebar.rebuild_views()
        self.win.navigate("library", "owned")
