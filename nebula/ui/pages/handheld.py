"""Controller-first home for handheld mode: big cover rows, a large title for the focused game and button prompts."""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from nebula.core.utils import playtime_text
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import label
from nebula.ui.widgets.gamegrid import ENTRY_ROLE, CoverRow

PROMPTS = (("A", "#5fb85f", "Play"), ("Y", "#e0b400", "Details"), ("B", "#d9534f", "Back"), ("☰", "#8c84b8", "Settings"))


def _glyph(letter: str, color: str, size: int = 26) -> QPixmap:
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QRectF(0, 0, pm.width(), pm.height()))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(pm.height() * 0.55))
    p.setFont(f)
    p.setPen(QColor("#111111"))
    p.drawText(QRectF(0, 0, pm.width(), pm.height()), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


class HandheldPage(Page):
    name = "Handheld"
    ROWS = (("continue", "Continue playing"), ("installed", "Installed"), ("all", "All games"))

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(48, 26, 48, 18)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("NEBULA", "PageTitle"))
        head.addStretch(1)
        self.clock = label("", "SectionTitle")
        head.addWidget(self.clock)
        lay.addLayout(head)
        self.title = label("", "PageTitle")
        self.title.setStyleSheet("font-size: 26pt;")
        self.sub = label("", "Subtle")
        lay.addWidget(self.title)
        lay.addWidget(self.sub)
        self.area = QScrollArea()  # rows scroll so the focused one is always on screen
        self.area.setWidgetResizable(True)
        self.area.setFrameShape(QFrame.Shape.NoFrame)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        rows_lay = QVBoxLayout(inner)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        rows_lay.setSpacing(8)
        self.area.setWidget(inner)
        lay.addWidget(self.area, 1)
        self.rows: list[tuple[QLabel, CoverRow]] = []
        for _key, text in self.ROWS:
            head_lbl = label(text.upper(), "SidebarSection")
            row = CoverRow(services, 170)
            row.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            row.installEventFilter(self)
            row.selectionModel().currentChanged.connect(lambda cur, _p: self._show(cur))
            row.play_entry.connect(ctl.primary_action)
            rows_lay.addWidget(head_lbl)
            rows_lay.addWidget(row)
            self.rows.append((head_lbl, row))
        rows_lay.addStretch(1)
        foot = QHBoxLayout()
        foot.addStretch(1)
        for letter, color, text in PROMPTS:
            g = QLabel()
            g.setPixmap(_glyph(letter, color))
            foot.addWidget(g)
            foot.addWidget(label(text, "Subtle"))
            foot.addSpacing(18)
        lay.addLayout(foot)
        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(20_000)
        self._tick()

    def _tick(self) -> None:
        self.clock.setText(time.strftime("%H:%M"))

    def on_show(self, arg=None) -> None:
        lib = self.s.library
        everything = sorted((e for e in lib.all_entries() if not e.user.hidden), key=lambda e: e.title.lower())
        lists = {"continue": lib.recently_played(20), "installed": [e for e in everything if e.installed],
                 "all": everything}
        first = None
        for (key, _t), (head_lbl, row) in zip(self.ROWS, self.rows):
            row.set_entries(lists[key])
            visible = bool(lists[key])
            head_lbl.setVisible(visible)
            row.setVisible(visible)
            if visible and first is None:
                first = row
        if first is not None:
            QTimer.singleShot(0, lambda: self._focus(first, 0))
        else:
            self.title.setText("No games yet")
            self.sub.setText("Sign in with GOG or add games from the desktop mode.")

    def _focus(self, row: CoverRow, column: int) -> None:
        row.setFocus()
        idx = row.model().index(min(column, row.model().rowCount() - 1), 0)
        row.setCurrentIndex(idx)
        row.scrollTo(idx)
        self.area.ensureWidgetVisible(row, 0, 40)
        self._show(idx)

    def _show(self, idx) -> None:
        e = idx.data(ENTRY_ROLE) if idx is not None and idx.isValid() else None
        if e is None:
            return
        self.title.setText(e.title)
        parts = ["Installed" if e.installed else "Not installed"]
        if e.playtime_minutes:
            parts.append(f"{playtime_text(e.playtime_minutes)} played")
        self.sub.setText("  •  ".join(parts))

    def eventFilter(self, obj, e):  # noqa: N802
        if e.type() != QEvent.Type.KeyPress or not isinstance(obj, CoverRow):
            return False
        key = e.key()
        idx = obj.currentIndex()
        entry = idx.data(ENTRY_ROLE) if idx.isValid() else None
        if key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            visible = [r for _h, r in self.rows if r.isVisible()]
            i = visible.index(obj) + (1 if key == Qt.Key.Key_Down else -1)
            if 0 <= i < len(visible):
                self._focus(visible[i], idx.row() if idx.isValid() else 0)
            return True
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and entry is not None:
            self.ctl.primary_action(entry.key)
            return True
        if key == Qt.Key.Key_F2 and entry is not None:
            self.win.navigate("game", entry.key)
            return True
        return key == Qt.Key.Key_Escape  # this is the root screen
