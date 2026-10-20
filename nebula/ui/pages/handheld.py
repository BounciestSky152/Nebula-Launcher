"""Big Picture style screen for handheld mode: tabs switched with the bumpers, the focused game's artwork
behind everything, shelves of large covers, a full library grid and controller button prompts."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from nebula.core.utils import playtime_text
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import Avatar, HeroBanner, label
from nebula.ui.widgets.gamegrid import ENTRY_ROLE, CoverGrid, CoverRow

PROMPTS = {  # button glyphs as printed on each controller; anything that isn't PlayStation uses Xbox
    "xbox": (("A", "#5fb85f", "Play"), ("Y", "#e0b400", "Details"), ("B", "#d9534f", "Back"),
             ("☰", "#8c84b8", "Settings")),
    "playstation": (("✕", "#7fa7e0", "Play"), ("△", "#4fc2a8", "Details"), ("○", "#e0707a", "Back"),
                    ("≡", "#8c84b8", "Settings")),
}
TABS = ("Home", "Library")


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
    SHELVES = (("continue", "Recent games"), ("installed", "Installed"), ("updates", "Updates available"))

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
        self.clock = label("", "SectionTitle")
        top.addWidget(self.avatar)
        top.addWidget(self.user)
        top.addSpacing(16)
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
        # Library: every game in a grid
        self.grid = CoverGrid(services, 170)
        self._controller_view(self.grid)
        self.grid.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs.addWidget(self.grid)

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

    # -------------------------------------------------------------- data
    def on_show(self, arg=None) -> None:
        lib = self.s.library
        everything = sorted((e for e in lib.all_entries() if not e.user.hidden), key=lambda e: e.title.lower())
        lists = {"continue": lib.recently_played(20), "installed": [e for e in everything if e.installed],
                 "updates": [e for e in everything if e.update_available]}
        for (key, _t), (head, row) in zip(self.SHELVES, self.rows):
            row.set_entries(lists[key])
            head.setVisible(bool(lists[key]))
            row.setVisible(bool(lists[key]))
        if not lists["continue"] and not lists["installed"]:
            self.rows[0][1].set_entries(everything[:20])  # nothing played yet: show the library
            self.rows[0][0].setText("YOUR GAMES")
            self.rows[0][0].setVisible(bool(everything))
            self.rows[0][1].setVisible(bool(everything))
        self.grid.set_entries(everything)
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
        if self.tabs.currentIndex() == 1:
            return [self.grid]
        return [r for _h, r in self.rows if r.isVisible()]

    def _set_tab(self, i: int, focus: bool = True) -> None:
        i %= len(TABS)
        self.tabs.setCurrentIndex(i)
        for n, t in enumerate(self.tab_labels):
            on = n == i
            t.setStyleSheet("background: transparent; font-size: 13pt; letter-spacing: 2px; padding: 4px 10px; "
                            + ("color: #ffffff; font-weight: 800; border-bottom: 3px solid #b79cff;" if on
                               else "color: #8d86ba; font-weight: 600; border-bottom: 3px solid transparent;"))
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
        if view is not self.grid:
            self.area.ensureWidgetVisible(view, 0, 40)
        self._show(idx)

    def _show(self, idx) -> None:
        e = idx.data(ENTRY_ROLE) if idx is not None and idx.isValid() else None
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
        if e.type() != QEvent.Type.KeyPress or obj not in self._views():
            return False
        key = e.key()
        idx = obj.currentIndex()
        entry = idx.data(ENTRY_ROLE) if idx.isValid() else None
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
