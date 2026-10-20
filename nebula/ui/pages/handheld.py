"""Big Picture style screen for handheld mode: tabs switched with the bumpers, the focused game's artwork
behind everything, shelves of large covers, a full library grid and controller button prompts."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QStackedWidget,
                               QVBoxLayout, QWidget)

from nebula.core import tasks
from nebula.core.utils import playtime_text
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import Avatar, HeroBanner, label
from nebula.ui.pages.store import ITEM_ROLE, StoreRow
from nebula.ui.widgets.gamegrid import ENTRY_ROLE, CoverGrid, CoverRow

PROMPTS = {  # button glyphs as printed on each controller; anything that isn't PlayStation uses Xbox
    "xbox": (("A", "#5fb85f", "Play"), ("Y", "#e0b400", "Details"), ("B", "#d9534f", "Back"),
             ("☰", "#8c84b8", "Settings")),
    "playstation": (("✕", "#7fa7e0", "Play"), ("△", "#4fc2a8", "Details"), ("○", "#e0707a", "Back"),
                    ("≡", "#8c84b8", "Settings")),
}
TABS = ("Home", "Library", "Store")


def battery() -> tuple[int, bool] | None:
    """(percent, charging) of the first battery, or None on machines without one."""
    from pathlib import Path

    for bat in sorted(Path("/sys/class/power_supply").glob("BAT*")):
        try:
            pct = int((bat / "capacity").read_text().strip())
            status = (bat / "status").read_text().strip().lower()
        except (OSError, ValueError):
            continue
        return pct, status in ("charging", "full")
    return None


def battery_icon(pct: int, charging: bool) -> QPixmap:
    ratio = 2.0
    w, h = 30, 15
    pm = QPixmap(int(w * ratio), int(h * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(ratio, ratio)
    p.setPen(QColor("#d8d2f5"))
    p.drawRoundedRect(QRectF(0.5, 0.5, w - 4, h - 1), 3, 3)
    p.fillRect(QRectF(w - 3, h / 2 - 3, 2.5, 6), QColor("#d8d2f5"))
    fill = QColor("#5fb85f") if charging or pct > 20 else QColor("#d9534f")
    p.fillRect(QRectF(2.5, 2.5, (w - 8) * max(0, min(100, pct)) / 100, h - 5), fill)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def _glyph(text: str, color: str, size: int = 26, wide: bool = False) -> QPixmap:
    ratio = 2.0
    w = size * (1.7 if wide else 1)
    pm = QPixmap(int(w * ratio), int(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    r = QRectF(0, 0, pm.width(), pm.height())
    p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(pm.height() * (0.42 if wide else 0.55)))
    p.setFont(f)
    p.setPen(QColor("#111111"))
    p.drawText(r, Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


class HandheldPage(Page):
    name = "Handheld"
    SHELVES = (("continue", "Recent games"), ("updates", "Updates available"))

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        # the focused game's artwork fills the top of the screen, behind everything else
        self.hero = HeroBanner(500, self)
        self.hero.lower()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 22, 48, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.tab_labels: list[QLabel] = []
        top.addWidget(self._shoulder("LB"))
        for name in TABS:
            t = label(name.upper(), None)
            t.setStyleSheet("background: transparent;")
            top.addWidget(t)
            self.tab_labels.append(t)
        top.addWidget(self._shoulder("RB"))
        top.addStretch(1)
        self.avatar = Avatar(30)
        self.user = label("", "Subtle")
        self.bat_icon = QLabel()
        self.bat_text = label("", "Subtle")
        self.clock = label("", "SectionTitle")
        top.addWidget(self.avatar)
        top.addWidget(self.user)
        top.addSpacing(16)
        top.addWidget(self.bat_icon)
        top.addWidget(self.bat_text)
        top.addSpacing(10)
        top.addWidget(self.clock)
        lay.addLayout(top)

        self.title = label("", "PageTitle")
        self.title.setStyleSheet("font-size: 30pt; background: transparent;")
        self.sub = label("", "Subtle")
        self.sub.setStyleSheet("background: transparent; font-size: 12pt;")
        lay.addSpacing(110)  # leave the artwork room to breathe
        lay.addWidget(self.title)
        lay.addWidget(self.sub)

        self.tabs = QStackedWidget()
        lay.addWidget(self.tabs, 1)
        # Home: shelves of large covers that scroll so the focused one stays on screen
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        rows_lay = QVBoxLayout(inner)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(6)
        self.area.setWidget(inner)
        self.rows: list[tuple[QLabel, CoverRow]] = []
        for _key, text in self.SHELVES:
            head = label(text.upper(), "SidebarSection")
            row = CoverRow(services, 190)
            self._controller_view(row)
            row.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            rows_lay.addWidget(head)
            rows_lay.addWidget(row)
            self.rows.append((head, row))
        rows_lay.addStretch(1)
        self.tabs.addWidget(self.area)
        # Library: sections (All games / Installed) above a grid; up from the grid's top row reaches them
        lib = QWidget()
        lib.setStyleSheet("background: transparent;")
        ll = QVBoxLayout(lib)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)
        chips = QHBoxLayout()
        self.sections: list[tuple[str, QPushButton]] = []
        for key, text in (("all", "All games"), ("installed", "Installed")):
            b = QPushButton(text)
            b.setObjectName("Chip")
            b.setCheckable(True)
            b.setProperty("ownNav", True)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.installEventFilter(self)
            b.clicked.connect(lambda _c=False, k=key: self._set_section(k))
            chips.addWidget(b)
            self.sections.append((key, b))
        chips.addStretch(1)
        ll.addLayout(chips)
        self.grid = CoverGrid(services, 170)
        self._controller_view(self.grid)
        self.grid.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ll.addWidget(self.grid, 1)
        self.section = "all"
        self.tabs.addWidget(lib)
        # Store: GOG shelves
        self.owned: set[str] = set()  # read by the store tiles ("IN LIBRARY" / "WISHLISTED" tags)
        self.wishlist: set[str] = set()
        self.store_area = QScrollArea()
        self.store_area.setWidgetResizable(True)
        self.store_area.setFrameShape(QFrame.Shape.NoFrame)
        self.store_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.store_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.store_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        sinner = QWidget()
        sinner.setStyleSheet("background: transparent;")
        sl = QVBoxLayout(sinner)
        sl.setContentsMargins(0, 0, 0, 0)
        sl.setSpacing(6)
        self.store_area.setWidget(sinner)
        self.store_rows: list[tuple[QLabel, StoreRow]] = []
        for text in ("On sale", "Trending", "Your wishlist"):
            head = label(text.upper(), "SidebarSection")
            row = StoreRow(self)
            self._controller_view(row)
            row.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sl.addWidget(head)
            sl.addWidget(row)
            head.hide()
            row.hide()
            self.store_rows.append((head, row))
        self.store_note = label("", "Subtle", wrap=True)
        sl.addWidget(self.store_note)
        sl.addStretch(1)
        self._store_loaded = False
        self.tabs.addWidget(self.store_area)

        foot = QHBoxLayout()
        foot.addWidget(label("F11  Exit Big Picture", "Faint"))
        foot.addStretch(1)
        self.prompts: list[tuple[QLabel, QLabel]] = []
        for _i in range(4):
            g, t = QLabel(), label("", "Subtle")
            foot.addWidget(g)
            foot.addWidget(t)
            foot.addSpacing(18)
            self.prompts.append((g, t))
        lay.addLayout(foot)
        self.set_controller("xbox")
        self._set_tab(0, focus=False)
        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(20_000)
        self._tick()

    # ------------------------------------------------------------ pieces
    def _shoulder(self, text: str) -> QLabel:
        lb = QLabel()
        lb.setPixmap(_glyph(text, "#8c84b8", 20, wide=True))
        return lb

    def _controller_view(self, view) -> None:
        view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        view.installEventFilter(self)
        view.selectionModel().currentChanged.connect(lambda cur, _p: self._show(cur))

    def set_controller(self, kind: str) -> None:
        """Show the prompts of the controller in use (PlayStation layout, otherwise Xbox)."""
        for (g, t), (glyph, color, text) in zip(self.prompts, PROMPTS.get(kind, PROMPTS["xbox"])):
            g.setPixmap(_glyph(glyph, color))
            t.setText(text)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self.hero.setGeometry(0, 0, self.width(), min(self.hero.height(), int(self.height() * 0.62)))

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M"))
        b = battery()
        self.bat_icon.setVisible(b is not None)
        self.bat_text.setVisible(b is not None)
        if b is not None:
            self.bat_icon.setPixmap(battery_icon(*b))
            self.bat_text.setText(f"{b[0]}%")

    # -------------------------------------------------------------- data
    def on_show(self, arg=None) -> None:
        lib = self.s.library
        everything = sorted((e for e in lib.all_entries() if not e.user.hidden), key=lambda e: e.title.lower())
        self._everything = everything
        lists = {"continue": lib.recently_played(20), "updates": [e for e in everything if e.update_available]}
        for (key, _t), (head, row) in zip(self.SHELVES, self.rows):
            row.set_entries(lists[key])
            head.setVisible(bool(lists[key]))
            row.setVisible(bool(lists[key]))
        if not lists["continue"]:
            self.rows[0][1].set_entries(everything[:20])  # nothing played yet: show the library
            self.rows[0][0].setText("YOUR GAMES")
            self.rows[0][0].setVisible(bool(everything))
            self.rows[0][1].setVisible(bool(everything))
        self._set_section(self.section, focus=False)
        gog = self.s.integrations.get("gog")
        name = gog.account_name() if gog and gog.connected() else ""
        self.user.setText(name)
        self.avatar.setVisible(bool(name))
        if name:
            self.avatar.set_avatar(gog.avatar_url(), name, "#5fb85f")
        if not everything:
            self.title.setText("No games yet")
            self.sub.setText("Sign in with GOG, or press F11 and add games in the desktop view.")
        QTimer.singleShot(0, lambda: self._set_tab(self.tabs.currentIndex()))

    def _views(self) -> list:
        tab = self.tabs.currentIndex()
        if tab == 1:
            return [self.grid]
        rows = self.store_rows if tab == 2 else self.rows
        return [r for _h, r in rows if r.isVisible()]

    def _set_section(self, key: str, focus: bool = True) -> None:
        self.section = key
        items = getattr(self, "_everything", [])
        self.grid.set_entries([e for e in items if e.installed] if key == "installed" else items)
        for k, b in self.sections:
            b.setChecked(k == key)
        if focus:
            dict(self.sections)[key].setFocus()

    def _load_store(self) -> None:
        gog = self.s.integrations.get("gog")
        if self._store_loaded or gog is None:
            return
        self._store_loaded = True
        self.store_note.setText("Loading the GOG store…")

        def work():
            owned = gog.owned_ids() if gog.connected() else set()
            wish = gog.wishlist() if gog.connected() else set()
            sale = gog.catalog(discounted=True, order="desc:trending", limit=24).get("products", [])
            trending = gog.catalog(order="desc:trending", limit=24).get("products", [])
            wished = gog.wishlist_products(sorted(wish)[:48]) if wish and gog.wishlist_available() else []
            return owned, wish, (sale, trending, wished)

        def done(res):
            self.owned, self.wishlist, lists = res
            for (head, row), items in zip(self.store_rows, lists):
                row.model_.set_items(items)
                head.setVisible(bool(items))
                row.setVisible(bool(items))
            self.store_note.setText("" if any(lists) else "The GOG store has nothing to show right now.")
            if self.tabs.currentIndex() == 2:
                self._set_tab(2)

        def failed(exc):
            self._store_loaded = False
            self.store_note.setText(f"Couldn't load the GOG store ({exc}).")

        tasks.run_async(work, on_done=done, on_error=failed)

    def _set_tab(self, i: int, focus: bool = True) -> None:
        i %= len(TABS)
        self.tabs.setCurrentIndex(i)
        for n, t in enumerate(self.tab_labels):
            on = n == i
            t.setStyleSheet("background: transparent; font-size: 13pt; letter-spacing: 2px; padding: 4px 10px; "
                            + ("color: #ffffff; font-weight: 800; border-bottom: 3px solid #b79cff;" if on
                               else "color: #8d86ba; font-weight: 600; border-bottom: 3px solid transparent;"))
        if i == 2:
            self._load_store()
        views = self._views()
        if focus and views:
            self._focus(views[0], 0)

    def _focus(self, view, column: int) -> None:
        if view.model().rowCount() == 0:
            return
        view.setFocus()
        idx = view.model().index(min(column, view.model().rowCount() - 1), 0)
        view.setCurrentIndex(idx)
        view.scrollTo(idx)
        if isinstance(view, StoreRow):
            self.store_area.ensureWidgetVisible(view, 0, 40)
        elif view is not self.grid:
            self.area.ensureWidgetVisible(view, 0, 40)
        self._show(idx)

    def _show(self, idx) -> None:
        if idx is None or not idx.isValid():
            return
        item = idx.data(ITEM_ROLE) if isinstance(idx.model(), type(self.store_rows[0][1].model_)) else None
        if item:
            price = item.get("price") or {}
            self.title.setText(item.get("title", ""))
            owned = str(item.get("id")) in self.owned
            self.sub.setText("In your library" if owned else "  •  ".join(
                x for x in (price.get("discount"), price.get("final")) if x))
            self.hero.set_source([item.get("coverHorizontal", "")], item.get("title", ""))
            return
        e = idx.data(ENTRY_ROLE)
        if e is None:
            return
        self.title.setText(e.title)
        parts = ["Installed" if e.installed else "Not installed"]
        if e.update_available:
            parts.append("Update available")
        if e.playtime_minutes:
            parts.append(f"{playtime_text(e.playtime_minutes)} played")
        self.sub.setText("  •  ".join(parts))
        self.hero.set_source(e.hero_urls(), e.title)

    # ----------------------------------------------------------- control
    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.KeyPress:
            return False
        key = e.key()
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown) and (obj in self._views()
                                                                  or obj in dict(self.sections).values()):
            self._set_tab(self.tabs.currentIndex() + (1 if key == Qt.Key.Key_PageDown else -1))
            return True
        chips = [b for _k, b in self.sections]
        if obj in chips:
            i = chips.index(obj)
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                j = max(0, min(len(chips) - 1, i + (1 if key == Qt.Key.Key_Right else -1)))
                self._set_section(self.sections[j][0])
            elif key == Qt.Key.Key_Down:
                self._focus(self.grid, 0)
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                obj.click()
            elif key == Qt.Key.Key_Escape:
                self._set_tab(0)
            return True
        if obj not in self._views():
            return False
        idx = obj.currentIndex()
        if isinstance(obj, StoreRow):
            item = idx.data(ITEM_ROLE) if idx.isValid() else None
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2) and item:
                self.win.navigate("product", item)
                return True
        entry = idx.data(ENTRY_ROLE) if idx.isValid() and not isinstance(obj, StoreRow) else None
        if key == Qt.Key.Key_Up and obj is self.grid and (not idx.isValid() or self.grid.visualRect(idx).top()
                                                          <= self.grid.visualRect(self.grid.model().index(0, 0)).top()):
            dict(self.sections)[self.section].setFocus()  # top row: up to the section buttons
            return True
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self._set_tab(self.tabs.currentIndex() + (1 if key == Qt.Key.Key_PageDown else -1))
            return True
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and obj is not self.grid:
            views = self._views()
            i = views.index(obj) + (1 if key == Qt.Key.Key_Down else -1)
            if 0 <= i < len(views):
                self._focus(views[i], idx.row() if idx.isValid() else 0)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and entry is not None:
            self.ctl.primary_action(entry.key)
            return True
        if key == Qt.Key.Key_F2 and entry is not None:
            self.win.navigate("game", entry.key)
            return True
        if key == Qt.Key.Key_Escape:
            if self.tabs.currentIndex() != 0:
                self._set_tab(0)
            return True  # this is the root screen
        return False
