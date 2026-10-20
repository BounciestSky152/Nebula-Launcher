"""Big Picture style screens for handheld mode: tabs switched with the bumpers, the focused game's artwork
behind everything, shelves of large covers, a library with sections, a GOG store and full-screen game cards.
Everything is sized from the screen height, so it scales from a 7" handheld to a TV."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QSpacerItem,
                               QStackedWidget, QVBoxLayout, QWidget)

from nebula.core import tasks
from nebula.core.utils import human_size, playtime_text, relative_time
from nebula.ui.pages.base import Page
from nebula.ui.pages.store import ITEM_ROLE, StoreModel, StoreRow
from nebula.ui.widgets.common import Avatar, HeroBanner, label
from nebula.ui.widgets.gamegrid import ENTRY_ROLE, CoverGrid, CoverRow

PROMPTS = {  # button glyphs as printed on each controller; anything that isn't PlayStation uses Xbox
    "xbox": (("A", "#5fb85f", "Select"), ("Y", "#e0b400", "Quick play"), ("B", "#d9534f", "Back"),
             ("☰", "#8c84b8", "Settings")),
    "playstation": (("✕", "#7fa7e0", "Select"), ("△", "#4fc2a8", "Quick play"), ("○", "#e0707a", "Back"),
                    ("≡", "#8c84b8", "Settings")),
}
TABS = ("Home", "Library", "Store")
SECTIONS = (("all", "All games"), ("installed", "Installed"), ("updates", "Updates"))
BASE_HEIGHT = 800  # the layout is designed at 1280x800 (Steam Deck) and scaled from there


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


def battery_icon(pct: int, charging: bool, scale: float = 1.0) -> QPixmap:
    ratio = 2.0
    w, h = 30 * scale, 15 * scale
    pm = QPixmap(int(w * ratio), int(h * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(ratio * scale, ratio * scale)
    p.setPen(QColor("#d8d2f5"))
    p.drawRoundedRect(QRectF(0.5, 0.5, 26, 14), 3, 3)
    p.fillRect(QRectF(27, 4.5, 2.5, 6), QColor("#d8d2f5"))
    fill = QColor("#5fb85f") if charging or pct > 20 else QColor("#d9534f")
    p.fillRect(QRectF(2.5, 2.5, 22 * max(0, min(100, pct)) / 100, 10), fill)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def glyph(text: str, color: str, size: float = 26, wide: bool = False) -> QPixmap:
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


def scroll_box() -> tuple[QScrollArea, QVBoxLayout]:
    """A transparent, scrollbar-free area; the focused shelf is scrolled into view by the page."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    inner = QWidget()
    inner.setStyleSheet("background: transparent;")
    lay = QVBoxLayout(inner)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    area.setWidget(inner)
    return area, lay


class Prompts(QWidget):
    """The footer: an exit hint for the keyboard and the controller's button prompts."""

    def __init__(self, items=PROMPTS["xbox"]):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.hint = label("F11  Exit Big Picture", "Faint")
        lay.addWidget(self.hint)
        lay.addStretch(1)
        self.items = list(items)
        self.cells: list[tuple[QLabel, QLabel]] = []
        for _i in range(4):
            g, t = QLabel(), label("", "Subtle")
            lay.addWidget(g)
            lay.addWidget(t)
            lay.addSpacing(18)
            self.cells.append((g, t))
        self.scale = 1.0
        self.set_items(items)

    def set_items(self, items) -> None:
        self.items = list(items)
        for (g, t), (text, color, what) in zip(self.cells, self.items):
            g.setPixmap(glyph(text, color, 26 * self.scale))
            t.setText(what)
            t.setStyleSheet(f"font-size: {11 * self.scale:.1f}pt;")
        self.hint.setStyleSheet(f"font-size: {10 * self.scale:.1f}pt;")

    def set_scale(self, s: float) -> None:
        self.scale = s
        self.set_items(self.items)


class HandheldPage(Page):
    name = "Handheld"
    SHELVES = (("continue", "Recent games"), ("added", "Recently added"))
    STORE = (("featured", "Featured"), ("sale", "On sale"), ("trending", "Trending"), ("new", "New releases"),
             ("wish", "Your wishlist"))

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.scale = 1.0
        self.kind = "xbox"
        # the focused game's artwork fills the top of the screen, behind everything else
        self.hero = HeroBanner(500, self)
        self.hero.lower()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 22, 48, 16)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(6)
        self.shoulders = [QLabel(), QLabel()]
        self.tab_labels: list[QLabel] = []
        top.addWidget(self.shoulders[0])
        for name in TABS:
            t = label(name.upper(), None)
            top.addWidget(t)
            self.tab_labels.append(t)
        top.addWidget(self.shoulders[1])
        top.addStretch(1)
        self.avatar = Avatar(30)
        self.user = label("", "Subtle")
        self.bat_icon = QLabel()
        self.bat_text = label("", "Subtle")
        self.clock = label("", "SectionTitle")
        for w in (self.avatar, self.user):
            top.addWidget(w)
        top.addSpacing(16)
        top.addWidget(self.bat_icon)
        top.addWidget(self.bat_text)
        top.addSpacing(10)
        top.addWidget(self.clock)
        lay.addLayout(top)

        self.gap = QSpacerItem(0, 110, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        lay.addItem(self.gap)  # leaves the artwork room to breathe
        self.title = label("", "PageTitle")
        self.sub = label("", "Subtle")
        self.sub.setWordWrap(True)
        self.extra = label("", "Faint")
        for w in (self.title, self.sub, self.extra):
            lay.addWidget(w)

        self.tabs = QStackedWidget()
        lay.addWidget(self.tabs, 1)
        # Home: shelves of large covers
        self.area, home = scroll_box()
        self.rows: list[tuple[QLabel, CoverRow]] = []
        for _key, text in self.SHELVES:
            head = label(text.upper(), "SidebarSection")
            row = CoverRow(services, 190)
            self._controller_view(row)
            row.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            home.addWidget(head)
            home.addWidget(row)
            self.rows.append((head, row))
        home.addStretch(1)
        self.tabs.addWidget(self.area)
        # Library: sections above a grid; up from the grid's top row reaches them
        lib = QWidget()
        lib.setStyleSheet("background: transparent;")
        ll = QVBoxLayout(lib)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(10)
        chips = QHBoxLayout()
        chips.setSpacing(10)
        self.sections: list[tuple[str, QPushButton]] = []
        for key, text in SECTIONS:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setProperty("ownNav", True)
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
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
        # Store: GOG shelves; the first one has wide feature tiles
        self.owned: set[str] = set()  # read by the store tiles ("IN LIBRARY" / "WISHLISTED" tags)
        self.wishlist: set[str] = set()
        self.store_area, store = scroll_box()
        self.store_rows: list[tuple[QLabel, StoreRow]] = []
        for _key, text in self.STORE:
            head = label(text.upper(), "SidebarSection")
            row = StoreRow(self)
            self._controller_view(row)
            row.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            store.addWidget(head)
            store.addWidget(row)
            head.hide()
            row.hide()
            self.store_rows.append((head, row))
        self.store_note = label("", "Subtle", wrap=True)
        store.addWidget(self.store_note)
        store.addStretch(1)
        self._store_loaded = False
        self.tabs.addWidget(self.store_area)

        self.prompts = Prompts()
        lay.addWidget(self.prompts)
        self._set_tab(0, focus=False)
        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(20_000)
        self._tick()

    # ------------------------------------------------------------ pieces
    def _controller_view(self, view) -> None:
        view.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        view.installEventFilter(self)
        view.selectionModel().currentChanged.connect(lambda cur, _p: self._show(cur))

    def set_controller(self, kind: str) -> None:
        """Show the prompts of the controller in use (PlayStation layout, otherwise Xbox)."""
        self.kind = kind
        self.prompts.set_items(PROMPTS.get(kind, PROMPTS["xbox"]))
        card = self.win.pages.get("handheld_game") if hasattr(self.win, "pages") else None
        if card is not None:
            card.set_controller(kind)

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        s = max(0.8, min(2.4, self.height() / BASE_HEIGHT))
        self.hero.setFixedHeight(int(self.height() * 0.62))
        self.hero.setGeometry(0, 0, self.width(), self.hero.height())
        if abs(s - self.scale) > 0.02:
            self.scale = s
            self._apply_scale()

    def _apply_scale(self) -> None:
        s = self.scale
        m = int(48 * s)
        self.layout().setContentsMargins(m, int(22 * s), m, int(16 * s))
        self.gap.changeSize(0, int(110 * s), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.layout().invalidate()
        self.title.setStyleSheet(f"font-size: {30 * s:.1f}pt; background: transparent;")
        self.sub.setStyleSheet(f"font-size: {12.5 * s:.1f}pt; background: transparent;")
        self.extra.setStyleSheet(f"font-size: {10.5 * s:.1f}pt; background: transparent;")
        for lb in (self.user, self.bat_text):
            lb.setStyleSheet(f"font-size: {11 * s:.1f}pt;")
        self.clock.setStyleSheet(f"font-size: {14 * s:.1f}pt;")
        self.avatar.setFixedSize(int(30 * s), int(30 * s))
        for lb, text in zip(self.shoulders, ("LB", "RB")):
            lb.setPixmap(glyph(text, "#8c84b8", 20 * s, wide=True))
        for _k, b in self.sections:
            b.setStyleSheet(
                f"QPushButton {{ font-size: {13 * s:.1f}pt; font-weight: 700; padding: {6 * s:.0f}px {18 * s:.0f}px;"
                f" border-radius: {16 * s:.0f}px; background: rgba(255,255,255,0.07); color: #c9c3e8;"
                " border: 2px solid transparent; }"
                " QPushButton:checked { background: rgba(140,98,212,0.55); color: white; }"
                " QPushButton:focus { border: 2px solid #b79cff; }")
        for _h, row in self.rows:
            row.delegate.cover_width = int(190 * s)
            row.setFixedHeight(row.delegate.sizeHint(None, None).height() + 14)
            row.doItemsLayout()
        self.grid.set_cover_width(int(170 * s))
        for view in [r for _h, r in self.rows] + [r for _h, r in self.store_rows] + [self.grid]:
            f = view.font()  # captions under the covers are painted with the view's font
            f.setPointSizeF(10 * s)
            view.setFont(f)
        for head, _r in self.rows + self.store_rows:
            head.setStyleSheet(f"font-size: {9 * s:.1f}pt;")
        for i, (_h, row) in enumerate(self.store_rows):
            d = row.itemDelegate()
            d.set_width(int((460 if i == 0 else 300) * s))
            hint = d.sizeHint(None, None)
            row.setFixedHeight(hint.height() + 16)
            row.setGridSize(hint)
        self.prompts.set_scale(s)
        self._set_tab(self.tabs.currentIndex(), focus=False)
        self._tick()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M"))
        b = battery()
        self.bat_icon.setVisible(b is not None)
        self.bat_text.setVisible(b is not None)
        if b is not None:
            self.bat_icon.setPixmap(battery_icon(*b, scale=self.scale))
            self.bat_text.setText(f"{b[0]}%")

    # -------------------------------------------------------------- data
    def on_show(self, arg=None) -> None:
        lib = self.s.library
        everything = sorted((e for e in lib.all_entries() if not e.user.hidden), key=lambda e: e.title.lower())
        self._everything = everything
        lists = {"continue": lib.recently_played(20), "added": lib.recently_added(20)}
        if not lists["continue"]:
            lists["continue"] = everything[:20]  # nothing played yet: show the library
        for (key, _t), (head, row) in zip(self.SHELVES, self.rows):
            row.set_entries(lists[key])
            head.setVisible(bool(lists[key]))
            row.setVisible(bool(lists[key]))
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

    def _set_tab(self, i: int, focus: bool = True) -> None:
        i %= len(TABS)
        self.tabs.setCurrentIndex(i)
        s = self.scale
        for n, t in enumerate(self.tab_labels):
            on = n == i
            t.setStyleSheet(f"background: transparent; font-size: {14 * s:.1f}pt; letter-spacing: 2px; "
                            f"padding: {4 * s:.0f}px {12 * s:.0f}px; "
                            + ("color: #ffffff; font-weight: 800; border-bottom: 3px solid #b79cff;" if on
                               else "color: #8d86ba; font-weight: 600; border-bottom: 3px solid transparent;"))
        if i == 2:
            self._load_store()
        views = self._views()
        if focus and views:
            self._focus(views[0], 0)

    def _set_section(self, key: str, focus: bool = True) -> None:
        self.section = key
        items = getattr(self, "_everything", [])
        pick = {"installed": lambda e: e.installed, "updates": lambda e: e.update_available}.get(key)
        self.grid.set_entries([e for e in items if pick(e)] if pick else items)
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
            sale = gog.catalog(discounted=True, order="desc:trending", limit=30).get("products", [])
            trending = gog.catalog(order="desc:trending", limit=24).get("products", [])
            new = gog.catalog(order="desc:releaseDate", limit=24).get("products", [])
            wished = gog.wishlist_products(sorted(wish)[:48]) if wish and gog.wishlist_available() else []
            featured = [p for p in sale if p.get("coverHorizontal")][:8]
            return owned, wish, (featured, sale[8:] or sale, trending, new, wished)

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
        if isinstance(idx.model(), StoreModel):
            self._show_product(idx.data(ITEM_ROLE) or {})
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
        self.extra.setText("  •  ".join(x for x in (", ".join(e.genres[:3]), (e.release_date or "")[:4]) if x))
        self.hero.set_source(e.hero_urls(), e.title)

    def _show_product(self, item: dict) -> None:
        price = item.get("price") or {}
        pid = str(item.get("id"))
        self.title.setText(item.get("title", ""))
        if pid in self.owned:
            first = "In your library"
        elif price.get("discount"):
            first = f"{price['discount']}  {price.get('final', '')}   (was {price.get('base', '')})"
        else:
            first = price.get("final") or ""
        systems = {"windows": "Windows (Proton)", "linux": "Linux native", "osx": "macOS"}
        osl = " / ".join(systems[o] for o in item.get("operatingSystems", []) or [] if o in systems)
        self.sub.setText("  •  ".join(x for x in (first, osl, "On your wishlist" if pid in self.wishlist else "")
                                      if x))
        genres = [g.get("name", "") for g in item.get("genres", []) or [] if isinstance(g, dict)]
        released = (item.get("releaseDate") or "").replace(".", "-")[:10]
        self.extra.setText("  •  ".join(x for x in (", ".join(item.get("developers", []) or []),
                                                    ", ".join(genres[:3]), released and f"Released {released}")
                                       if x))
        self.hero.set_source([item.get("coverHorizontal", "")], item.get("title", ""))

    # ----------------------------------------------------------- control
    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.KeyPress:
            return False
        key = e.key()
        chips = [b for _k, b in self.sections]
        if key in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown) and (obj in self._views() or obj in chips):
            self._set_tab(self.tabs.currentIndex() + (1 if key == Qt.Key.Key_PageDown else -1))
            return True
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
            return key != Qt.Key.Key_Up
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
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down) and obj is not self.grid:
            views = self._views()
            i = views.index(obj) + (1 if key == Qt.Key.Key_Down else -1)
            if 0 <= i < len(views):
                self._focus(views[i], idx.row() if idx.isValid() else 0)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and entry is not None:
            self.win.navigate("handheld_game", entry.key)  # the game card
            return True
        if key == Qt.Key.Key_F2 and entry is not None:
            self.ctl.primary_action(entry.key)  # quick play
            return True
        if key == Qt.Key.Key_Escape:
            if self.tabs.currentIndex() != 0:
                self._set_tab(0)
            return True  # this is the root screen
        return False


class HandheldGamePage(Page):
    """A game's card in handheld mode: its artwork, the essentials and a few large buttons."""

    name = "Game"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.key = ""
        self.scale = 1.0
        self.hero = HeroBanner(500, self)
        self.hero.lower()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(64, 40, 64, 16)
        lay.setSpacing(12)
        self.gap = QSpacerItem(0, 150, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        lay.addItem(self.gap)
        self.title = label("", "PageTitle")
        self.title.setWordWrap(True)
        self.meta = label("", "Subtle")
        lay.addWidget(self.title)
        lay.addWidget(self.meta)
        self.stats = QHBoxLayout()
        self.stats.setSpacing(36)
        self.stat_cells: list[tuple[QLabel, QLabel]] = []
        for _i in range(4):
            col = QVBoxLayout()
            col.setSpacing(2)
            k, v = label("", "StatLabel"), label("", "StatValue")
            col.addWidget(k)
            col.addWidget(v)
            self.stats.addLayout(col)
            self.stat_cells.append((k, v))
        self.stats.addStretch(1)
        lay.addLayout(self.stats)
        buttons = QHBoxLayout()
        buttons.setSpacing(14)
        self.play = QPushButton()
        self.fav = QPushButton()
        self.more = QPushButton("Full details")
        for b in (self.play, self.fav, self.more):
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            buttons.addWidget(b)
        buttons.addStretch(1)
        self.play.clicked.connect(lambda: self.ctl.primary_action(self.key))
        self.fav.clicked.connect(lambda: self.ctl.toggle_favorite(self.key))
        self.more.clicked.connect(lambda: self.win.navigate("game", self.key))
        lay.addLayout(buttons)
        self.desc = label("", "Subtle", wrap=True)
        self.desc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.desc, 1)
        self.prompts = Prompts((("A", "#5fb85f", "Select"), ("B", "#d9534f", "Back")))
        lay.addWidget(self.prompts)
        bus = services.bus
        bus.game_changed.connect(lambda k: self._refresh() if k == self.key and self.isVisible() else None)
        bus.jobs_changed.connect(lambda: self._refresh() if self.isVisible() else None)
        bus.game_started.connect(lambda _u: self._refresh() if self.isVisible() else None)
        bus.game_stopped.connect(lambda *_: self._refresh() if self.isVisible() else None)

    def set_controller(self, kind: str) -> None:
        items = PROMPTS.get(kind, PROMPTS["xbox"])
        self.prompts.set_items((items[0], items[2]))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        s = max(0.8, min(2.4, self.height() / BASE_HEIGHT))
        self.hero.setFixedHeight(int(self.height() * 0.7))
        self.hero.setGeometry(0, 0, self.width(), self.hero.height())
        if abs(s - self.scale) > 0.02:
            self.scale = s
            self._apply_scale()

    def _apply_scale(self) -> None:
        s = self.scale
        self.layout().setContentsMargins(int(64 * s), int(40 * s), int(64 * s), int(16 * s))
        self.gap.changeSize(0, int(150 * s), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.layout().invalidate()
        self.title.setStyleSheet(f"font-size: {34 * s:.1f}pt; background: transparent;")
        self.meta.setStyleSheet(f"font-size: {12.5 * s:.1f}pt; background: transparent;")
        self.desc.setStyleSheet(f"font-size: {11.5 * s:.1f}pt; background: transparent;")
        for k, v in self.stat_cells:
            k.setStyleSheet(f"font-size: {9 * s:.1f}pt;")
            v.setStyleSheet(f"font-size: {17 * s:.1f}pt;")
        base = (f"font-size: {14 * s:.1f}pt; font-weight: 800; padding: {12 * s:.0f}px {30 * s:.0f}px;"
                f" border-radius: {10 * s:.0f}px; border: 3px solid transparent;")
        self.play.setStyleSheet(f"QPushButton {{ {base} background: #5fb85f; color: #0b1a0b; }}"
                                " QPushButton:focus { border-color: #ffffff; }")
        for b in (self.fav, self.more):
            b.setStyleSheet(f"QPushButton {{ {base} background: rgba(255,255,255,0.10); color: white; }}"
                            " QPushButton:focus { border-color: #b79cff; background: rgba(140,98,212,0.45); }")
        self.prompts.set_scale(s)

    def on_show(self, arg=None) -> None:
        if arg:
            self.key = arg
        self._refresh()
        QTimer.singleShot(0, self.play.setFocus)

    def _refresh(self) -> None:
        e = self.s.library.entry(self.key)
        if e is None:
            return
        self.hero.set_source(e.hero_urls(), e.title)
        self.title.setText(e.title)
        names = [self.s.integrations[s].name for s in e.sources if s in self.s.integrations]
        self.meta.setText("  •  ".join(x for x in (" + ".join(names), ", ".join(e.genres[:3]),
                                                   (e.release_date or "")[:4]) if x))
        unlocked, total = e.achievements
        cells = (("TIME PLAYED", playtime_text(e.playtime_minutes) if e.playtime_minutes else "—"),
                 ("LAST PLAYED", relative_time(e.last_played) if e.last_played else "Never"),
                 ("ACHIEVEMENTS", f"{unlocked} / {total}" if total else "—"),
                 ("SIZE", human_size(e.install_size) if e.installed and e.install_size else "Not installed"))
        for (k, v), (key, value) in zip(self.stat_cells, cells):
            k.setText(key)
            v.setText(value)
        state = self.ctl.game_state(e)
        self.play.setText({"running": "■  STOP", "preparing": "STARTING…", "job": "DOWNLOADING…",
                           "update": "⬇  UPDATE" if self.ctl.update_target(e) else "▶  PLAY",
                           "installed": "▶  PLAY", "not_installed": "⬇  INSTALL"}[state])
        self.play.setEnabled(state not in ("preparing", "job"))
        self.fav.setText("♥  Favourite" if e.user.favorite else "♡  Favourite")
        desc = next((g.description for g in e.games if g.description), "")
        self.desc.setText(desc[:600] + ("…" if len(desc) > 600 else ""))
