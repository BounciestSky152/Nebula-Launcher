"""Sidebar and top bar (GOG-first layout, modelled on GOG Galaxy 2.0)."""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPen,
                           QPixmap, QRadialGradient)
from PySide6.QtWidgets import (QButtonGroup, QCompleter, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from nebula import APP_NAME
from nebula.core.utils import xdg_open
from nebula.ui import icons, theme
from nebula.ui.widgets.common import Avatar, Spinner, icon_button, label


LAVENDER = QColor(183, 156, 255)
STATS_ENABLED = False  # the statistics page is switched off for now
NODE_X = 12  # x of the orbit line the sidebar items hang on
ITEM_TEXT, ITEM_TEXT_ON = "#b4aed6", "#ffffff"
ITEM_ICON, ITEM_ICON_ON = "#8f88bd", "#e2d6ff"
# Super Nova (the light theme): dark, readable labels on a pearly sidebar; the colour is in the painting around them
NOVA = [QColor(255, 61, 139), QColor(255, 134, 46), QColor(255, 182, 0), QColor(20, 184, 166), QColor(138, 80, 255),
        QColor(40, 140, 250)]  # hot pink, tangerine, gold, turquoise, violet, blue
NOVA_TEXT, NOVA_ICON, NOVA_DIM = "#3a2a5c", "#8a4fd8", QColor(104, 86, 140)
NOVA_PILL = [QColor(214, 22, 122), QColor(138, 47, 230), QColor(64, 92, 240)]  # deep enough for white text
SIDEBAR_NOVA_QSS = """
QPushButton#SidebarTool, QPushButton#SidebarAdd {
    background: rgba(255, 255, 255, 0.75);
    border: 1px solid rgba(232, 51, 138, 0.24);
}
QPushButton#SidebarTool:hover, QPushButton#SidebarAdd:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 61, 139, 0.16), stop:1 rgba(255, 182, 0, 0.16));
    border-color: rgba(232, 51, 138, 0.6);
}
"""


def _sparkle(p: QPainter, c: QPointF, r: float, color: QColor) -> None:
    """A four-pointed star."""
    path = QPainterPath()
    path.moveTo(c.x(), c.y() - r)
    for dx, dy in ((0.22, -0.22), (1, 0), (0.22, 0.22), (0, 1), (-0.22, 0.22), (-1, 0), (-0.22, -0.22), (0, -1)):
        path.lineTo(c.x() + dx * r, c.y() + dy * r)
    p.fillPath(path, color)


class SectionHeader(QWidget):
    """Sidebar section title, HUD style: a sparkle, wide-tracked caps and a line fading into space."""

    def __init__(self, text: str):
        super().__init__()
        self._text = text.upper()
        self.indent = 0  # set by OrbitPanel
        self.collapsed = False
        self.on_toggle = None  # called with the new collapsed state
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to fold or unfold")

    def mousePressEvent(self, _e):  # noqa: N802
        self.collapsed = not self.collapsed
        self.update()
        if self.on_toggle:
            self.on_toggle(self.collapsed)

    def setText(self, text: str) -> None:  # noqa: N802
        self._text = text.upper()
        self.update()

    def text(self) -> str:
        return self._text

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = self.height() - 9
        x0 = 8 if theme.classic() else NODE_X + self.indent
        light = theme.light()
        ink = NOVA_DIM if light else QColor(141, 134, 186)
        if not theme.classic():  # Super Nova gives every section a sparkle of its own colour
            _sparkle(p, QPointF(x0, y - 3.5), 4.2, NOVA[sum(map(ord, self._text)) % len(NOVA)] if light else LAVENDER)
        f = theme.font("display", 7.6)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 2.4)
        p.setFont(f)
        p.setPen(ink)
        p.drawText(QPointF(x0 + 10, y), self._text)
        x = x0 + 10 + p.fontMetrics().horizontalAdvance(self._text) + 8
        line = QLinearGradient(x, 0, self.width() - 14, 0)
        line.setColorAt(0, QColor(255, 61, 139, 110) if light else QColor(183, 156, 255, 90))
        line.setColorAt(1, QColor(138, 80, 255, 0) if light else QColor(183, 156, 255, 0))
        if not theme.classic():
            p.fillRect(QRectF(x, y - 3.5, max(0, self.width() - 34 - x), 1), line)
        # fold arrow at the right
        ax, ay = self.width() - 22, y - 3.5
        arrow = QPainterPath()
        if self.collapsed:  # pointing right
            arrow.moveTo(ax - 2, ay - 4)
            arrow.lineTo(ax + 2.5, ay)
            arrow.lineTo(ax - 2, ay + 4)
        else:  # pointing down
            arrow.moveTo(ax - 4, ay - 2)
            arrow.lineTo(ax, ay + 2.5)
            arrow.lineTo(ax + 4, ay - 2)
        p.setPen(QPen(ink, 1.4))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(arrow)


class BrandMark(QWidget):
    """The sidebar logo: a ringed planet and the app's name in starlight lettering."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(64)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        light = theme.light()  # Super Nova: a hot planet with a golden ring, the name in magenta, orange and gold
        c = QPointF(34, 32)
        halo = QRadialGradient(c, 26)
        halo.setColorAt(0, QColor(255, 61, 139, 80) if light else QColor(140, 100, 255, 70))
        halo.setColorAt(1, QColor(255, 182, 0, 0) if light else QColor(140, 100, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(c, 26, 26)
        ring = QPen(QColor(255, 150, 20, 220) if light else QColor(205, 190, 255, 170), 1.6)
        p.save()  # back half of the ring, behind the planet
        p.translate(c)
        p.rotate(-22)
        p.setPen(ring)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(-19, -6, 38, 12), 0, 180 * 16)
        p.restore()
        body = QLinearGradient(c.x() - 10, c.y() - 10, c.x() + 10, c.y() + 10)
        for at, dark, nova in ((0, QColor(196, 168, 255), QColor(255, 214, 90)), (0.55, QColor(122, 82, 214), NOVA[0]),
                               (1, QColor(46, 70, 160), NOVA[4])):
            body.setColorAt(at, nova if light else dark)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(body)
        p.drawEllipse(c, 10.5, 10.5)
        p.save()  # front half of the ring
        p.translate(c)
        p.rotate(-22)
        p.setPen(ring)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(-19, -6, 38, 12), 180 * 16, 180 * 16)
        p.restore()
        _sparkle(p, QPointF(52, 15), 3, QColor(255, 160, 0) if light else QColor(255, 255, 255, 220))
        size, name = 15.0, APP_NAME.upper()
        while True:  # the name in the biggest lettering that fits beside the planet
            f = theme.font("display", size)
            f.setWeight(QFont.Weight.Black)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, size * 0.2)
            if size <= 9 or QFontMetricsF(f).horizontalAdvance(name) <= self.width() - 74:
                break
            size -= 0.5
        path = QPainterPath()
        path.addText(QPointF(62, 33), f, name)
        if light:
            g = QLinearGradient(62, 16, 62 + QFontMetricsF(f).horizontalAdvance(name), 34)
            g.setColorAt(0, QColor(214, 21, 127))
            g.setColorAt(0.55, QColor(240, 83, 28))
            g.setColorAt(1, QColor(214, 128, 0))
            p.fillPath(path.translated(0, 1), QColor(120, 30, 90, 40))  # a soft shadow keeps it crisp on pearl
        else:
            g = QLinearGradient(62, 16, 200, 34)
            g.setColorAt(0, QColor(255, 255, 255))
            g.setColorAt(0.55, QColor(206, 188, 255))
            g.setColorAt(1, QColor(126, 206, 255))
        p.fillPath(path, g)
        f = theme.font("sidebar", 6.4)
        f.setWeight(QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 3.2)
        p.setFont(f)
        p.setPen(NOVA_DIM if light else QColor(141, 134, 186))
        p.drawText(QPointF(63, 47), "GOG LAUNCHER")


class OrbitPanel(QWidget):
    """Holds the sidebar items and bends them along an orbit: each item hangs off a node on a faint curved
    line, so the list reads as a constellation instead of a straight column."""

    BULGE = 26  # how far the middle of the orbit swings out
    WOBBLE = 7  # how far single items drift off it, so the column never lines up

    def _offset(self, y: float) -> float:
        if theme.classic():
            return 0.0
        t = min(1.0, max(0.0, y / max(1, self.height())))
        return self.BULGE * math.sin(math.pi * t) + self.WOBBLE * math.sin(y / 23.0) * math.sin(y / 61.0 + 1.3)

    def _cy(self, w: QWidget) -> float:
        return w.mapTo(self, QPoint(0, w.height() // 2)).y()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        QTimer.singleShot(0, self.arrange)

    def arrange(self) -> None:
        for w in self.findChildren(QWidget):
            if isinstance(w, (SidebarButton, SectionHeader)) and w.isVisible():
                indent = int(round(self._offset(self._cy(w))))
                if indent != w.indent:
                    w.indent = indent
                    if isinstance(w, SidebarButton):
                        w.layout().setContentsMargins(26 + indent, 0, 12, 0)
                    w.update()
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        if theme.classic():
            return
        nodes = sorted((self._cy(w) - (3.5 if isinstance(w, SectionHeader) else 0), w)
                       for w in self.findChildren(QWidget)
                       if isinstance(w, (SidebarButton, SectionHeader)) and w.isVisible())
        if len(nodes) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pts = [QPointF(NODE_X + w.indent + 0.5, y) for y, w in nodes]
        path = QPainterPath(pts[0])
        for a, b in zip(pts, pts[1:]):  # smooth through the nodes
            mid = (a.y() + b.y()) / 2
            path.cubicTo(QPointF(a.x(), mid), QPointF(b.x(), mid), b)
        light = theme.light()
        line = QLinearGradient(0, pts[0].y(), 0, pts[-1].y())
        for at, dark, nova in ((0, QColor(183, 156, 255, 110), QColor(255, 61, 139, 150)),
                               (0.5, QColor(140, 170, 255, 70), QColor(255, 134, 46, 120)),
                               (1, QColor(110, 200, 255, 25), QColor(138, 80, 255, 70))):
            line.setColorAt(at, nova if light else dark)
        p.setPen(QPen(QBrush(line), 1.3 if light else 1.1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
        for i, (pt, (_y, w)) in enumerate(zip(pts, nodes)):
            if isinstance(w, SidebarButton) and not w.isChecked():
                p.setBrush(NOVA[i % len(NOVA)] if light else QColor(200, 185, 255, 150))  # Super Nova: every colour
                p.drawEllipse(pt, 2.1 if light else 1.7, 2.1 if light else 1.7)


class SidebarButton(QPushButton):
    def __init__(self, text: str, icon_name: str | None = None, pixmap=None):
        super().__init__()
        self.setObjectName("SidebarButton")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(26, 0, 12, 0)
        self.indent = 0  # set by OrbitPanel
        lay.setSpacing(10)
        self.ic = QLabel()
        self.ic.setFixedSize(18, 18)
        self.ic.setScaledContents(False)
        self._icon_name = icon_name
        self._pixmap = pixmap
        self._badge = 0
        self.text_lbl = QLabel(text)
        self.count = QLabel("")
        self.count.setStyleSheet(f"color:{theme.TEXT_FAINT}; font-size: 8pt;")
        self.spinner = Spinner(12)
        self.spinner.hide()
        for w in (self.ic, self.text_lbl, self.count, self.spinner):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            w.setStyleSheet(w.styleSheet() + "background: transparent;")
        # the count keeps its room; a long name gives way instead
        self.count.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.text_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        lay.addWidget(self.ic)
        lay.addWidget(self.text_lbl, 1)
        lay.addWidget(self.spinner)
        lay.addWidget(self.count)
        self.toggled.connect(lambda _c: self._paint_icon())
        self._paint_icon()

    def _paint_icon(self) -> None:
        on, light = self.isChecked(), theme.light()
        self._light = light  # the theme the label and icon are coloured for
        if self._pixmap is not None:
            self.ic.setPixmap(self._pixmap)
        elif self._icon_name:
            colour = ("#ffffff" if on else NOVA_ICON) if light else ITEM_ICON_ON if on else ITEM_ICON
            self.ic.setPixmap(icons.pixmap(self._icon_name, 18, colour))
        text = ("#ffffff" if on else NOVA_TEXT) if light else ITEM_TEXT_ON if on else ITEM_TEXT
        self.text_lbl.setStyleSheet(f"background: transparent; color: {text}; font-weight: {800 if on else 700};")
        self._style_count()

    def _style_count(self) -> None:
        on, light = self.isChecked(), theme.light()
        if self._badge:  # a small pill
            bg, fg = ((("white" if on else "rgba(232, 51, 138, 0.14)"), "#b0105f") if light
                      else ("rgba(183, 156, 255, 0.24)", "#ece6ff"))
            self.count.setStyleSheet(f"background: {bg}; color: {fg}; border-radius: 8px; font-size: 7.5pt; "
                                     "font-weight: 700; padding: 0 5px;")
        else:  # on Super Nova's selected pill the count is white too
            self.count.setStyleSheet(f"color:{'rgba(255, 255, 255, 0.82)' if light and on else theme.TEXT_FAINT}; "
                                     "font-size: 8pt; background: transparent;")

    def changeEvent(self, e):  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.StyleChange and getattr(self, "_light", None) not in (None, theme.light()):
            self._paint_icon()  # the theme switched

    def enterEvent(self, e):  # noqa: N802
        super().enterEvent(e)
        self.update()

    def leaveEvent(self, e):  # noqa: N802
        super().leaveEvent(e)
        self.update()

    def paintEvent(self, e):  # noqa: N802
        if theme.classic():
            return super().paintEvent(e)
        on, hover = self.isChecked(), self.underMouse() or (self.hasFocus() and theme.HANDHELD)
        if not (on or hover):
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(NODE_X + 8.5 + self.indent, 2.5, -10.5, -2.5)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        if theme.light():
            return self._paint_nova(p, r, path, on)
        if not on:
            p.fillPath(path, QColor(255, 255, 255, 12))
            p.setPen(QPen(QColor(255, 255, 255, 18), 1))
            p.drawPath(path)
            return
        fill = QLinearGradient(r.left(), 0, r.right(), 0)
        fill.setColorAt(0, QColor(124, 92, 255, 115))
        fill.setColorAt(0.65, QColor(72, 110, 255, 38))
        fill.setColorAt(1, QColor(72, 160, 255, 6))
        p.fillPath(path, fill)
        edge = QLinearGradient(r.left(), 0, r.right(), 0)
        edge.setColorAt(0, QColor(205, 185, 255, 170))
        edge.setColorAt(1, QColor(205, 185, 255, 0))
        p.setPen(QPen(QBrush(edge), 1))
        p.drawPath(path)
        star = QPointF(NODE_X + self.indent, r.center().y())  # the item's node on the orbit lights up
        glow = QRadialGradient(star, 7)
        glow.setColorAt(0, QColor(210, 190, 255, 200))
        glow.setColorAt(1, QColor(210, 190, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(star, 7, 7)
        _sparkle(p, star, 3.2, QColor(255, 255, 255))

    def _paint_nova(self, p: QPainter, r: QRectF, path: QPainterPath, on: bool) -> None:
        """Super Nova: a soft pink hover, and a vivid magenta-violet-blue pill with a golden star when selected."""
        if not on:
            p.fillPath(path, QColor(255, 61, 139, 22))
            p.setPen(QPen(QColor(255, 61, 139, 60), 1))
            p.drawPath(path)
            return
        fill = QLinearGradient(r.left(), 0, r.right(), 0)
        for i, c in enumerate(NOVA_PILL):
            fill.setColorAt(i / (len(NOVA_PILL) - 1), c)
        p.fillPath(path, fill)
        shine = QLinearGradient(0, r.top(), 0, r.bottom())  # a glossy top half
        shine.setColorAt(0, QColor(255, 255, 255, 70))
        shine.setColorAt(0.5, QColor(255, 255, 255, 0))
        p.fillPath(path, shine)
        edge = QLinearGradient(r.left(), 0, r.right(), 0)
        edge.setColorAt(0, QColor(255, 196, 64, 230))
        edge.setColorAt(1, QColor(255, 196, 64, 0))
        p.setPen(QPen(QBrush(edge), 1))
        p.drawPath(path)
        star = QPointF(NODE_X + self.indent, r.center().y())
        glow = QRadialGradient(star, 8)
        glow.setColorAt(0, QColor(255, 196, 40, 240))
        glow.setColorAt(0.5, QColor(255, 134, 46, 120))
        glow.setColorAt(1, QColor(255, 61, 139, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(star, 8, 8)
        _sparkle(p, star, 3.6, QColor(255, 255, 255))

    def set_count(self, n) -> None:
        self.count.setText(str(n) if n else "")

    def set_badge(self, n: int) -> None:
        self._badge = n
        if n:  # a small pill, not a full-height block
            self.count.setText(str(n))
            self.count.setFixedHeight(16)
            self.count.setMinimumWidth(18)
            self.count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.count.setText("")
            self.count.setMinimumSize(0, 0)
            self.count.setMaximumHeight(16777215)
        self._style_count()

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
        self.setFixedWidth(256)
        self._bg: QPixmap | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # brand header
        brand = QPushButton()
        brand.setObjectName("SidebarBrand")
        brand.setCursor(Qt.CursorShape.PointingHandCursor)
        brand.setMinimumHeight(66)
        brand.setToolTip("Home")
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(0, 2, 0, 0)
        bl.addWidget(BrandMark())
        brand.clicked.connect(lambda: self.win.navigate("home"))
        outer.addWidget(brand)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.viewport().setAutoFillBackground(False)
        inner = self.orbit = OrbitPanel()
        inner.setStyleSheet("background: transparent;")
        self.lay = QVBoxLayout(inner)
        self.lay.setContentsMargins(0, 0, 0, 10)
        self.lay.setSpacing(2)
        area.setWidget(inner)
        outer.addWidget(area, 1)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, SidebarButton] = {}
        self._build()
        # Galaxy's "Add games & friends" button
        self.tools = QHBoxLayout()  # filled by dock_tools() once the top bar exists
        self.tools.setContentsMargins(12, 8, 12, 0)
        self.tools.setSpacing(6)
        outer.addLayout(self.tools)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(12, 8, 12, 14)
        add = self.add_btn = QPushButton("  Add games && friends")
        add.setObjectName("SidebarAdd")
        add.setIcon(icons.icon("plus", theme.TEXT))
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self._add_menu)
        bottom.addWidget(add, 1)
        outer.addLayout(bottom)
        bus = services.bus
        bus.library_changed.connect(self._schedule_counts)
        bus.game_changed.connect(lambda _k: self._schedule_counts())
        bus.jobs_changed.connect(self._update_downloads)
        bus.sync_state.connect(self._sync_state)
        bus.integration_changed.connect(lambda _i: self.update_counts())
        bus.integration_changed.connect(lambda i: self.check_wishlist() if i == "gog" else None)
        QTimer.singleShot(1500, self.check_wishlist)
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.setInterval(300)
        self._count_timer.timeout.connect(self.update_counts)
        self.update_counts()
        self._light = False
        self._restyle()


    def changeEvent(self, e):  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.StyleChange and hasattr(self, "_light"):
            self._restyle()

    def _restyle(self) -> None:
        """Follow the theme: Super Nova has a pearly backdrop and tool buttons."""
        if self._light != theme.light():
            self._light = theme.light()
            self._bg = None
            self.setStyleSheet(SIDEBAR_NOVA_QSS if self._light else "")
            self.add_btn.setIcon(icons.icon("plus", theme.TEXT))

    # deep-space backdrop (Super Nova: frosted pearl): drawn once per size, nothing animates here
    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._bg = None

    def paintEvent(self, _e):  # noqa: N802
        if theme.classic():
            from PySide6.QtWidgets import QStyle, QStyleOption

            opt = QStyleOption()
            opt.initFrom(self)
            self.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget, opt, QPainter(self), self)
            return
        if self._bg is None:
            self._bg = self._render_bg()
        QPainter(self).drawPixmap(0, 0, self._bg)

    def _render_bg(self) -> QPixmap:
        w, h = max(1, self.width()), max(1, self.height())
        dpr = self.devicePixelRatioF() or 1.0
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        if theme.light():
            return self._render_nova_bg(pm, w, h)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        sky = QLinearGradient(0, 0, 0, h)
        sky.setColorAt(0, QColor(10, 9, 20))
        sky.setColorAt(1, QColor(7, 7, 12))
        p.fillRect(QRectF(0, 0, w, h), sky)
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy, r, col in ((w * 0.15, 40, 220, QColor(123, 79, 196, 46)),
                               (w * 1.0, h * 0.72, 260, QColor(60, 90, 190, 26))):
            glow = QRadialGradient(QPointF(cx, cy), r)
            glow.setColorAt(0, col)
            glow.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
            p.setBrush(glow)
            p.drawEllipse(QPointF(cx, cy), r, r)
        rnd = random.Random(11)
        for _ in range(int(w * h / 2200)):
            mag = rnd.random() ** 3
            p.setBrush(QColor(225, 225, 255, int(40 + mag * 150)))
            r = 0.4 + mag * 0.9
            p.drawEllipse(QPointF(rnd.uniform(0, w), rnd.uniform(0, h)), r, r)
        edge = QLinearGradient(0, 0, 0, h)  # a faint lit edge towards the content
        edge.setColorAt(0, QColor(150, 120, 230, 90))
        edge.setColorAt(0.5, QColor(150, 120, 230, 25))
        edge.setColorAt(1, QColor(150, 120, 230, 0))
        p.fillRect(QRectF(w - 1, 0, 1, h), edge)
        p.end()
        return pm

    @staticmethod
    def _render_nova_bg(pm: QPixmap, w: int, h: int) -> QPixmap:
        """Frosted pearl over the bright sky, with a soft glow of every colour of the supernova."""
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frost = QLinearGradient(0, 0, 0, h)
        frost.setColorAt(0, QColor(255, 252, 255, 228))
        frost.setColorAt(0.5, QColor(250, 245, 255, 214))
        frost.setColorAt(1, QColor(255, 247, 242, 228))
        p.fillRect(QRectF(0, 0, w, h), frost)
        p.setPen(Qt.PenStyle.NoPen)
        for cx, cy, r, col in ((w * 0.12, 30, 210, QColor(255, 61, 139, 50)), (w * 0.95, 80, 170, QColor(255, 182, 0, 50)),
                               (0, h * 0.5, 240, QColor(138, 80, 255, 34)), (w, h * 0.78, 230, QColor(20, 184, 166, 34)),
                               (w * 0.3, h, 230, QColor(255, 134, 46, 40))):
            glow = QRadialGradient(QPointF(cx, cy), r)
            glow.setColorAt(0, col)
            glow.setColorAt(1, QColor(col.red(), col.green(), col.blue(), 0))
            p.setBrush(glow)
            p.drawEllipse(QPointF(cx, cy), r, r)
        rnd = random.Random(11)
        for _ in range(int(w * h / 3000)):  # tiny coloured stars
            mag = rnd.random() ** 3
            c = QColor(rnd.choice(NOVA))
            c.setAlpha(int(50 + mag * 150))
            p.setBrush(c)
            r = 0.4 + mag * 0.8
            p.drawEllipse(QPointF(rnd.uniform(0, w), rnd.uniform(0, h)), r, r)
        edge = QLinearGradient(0, 0, 0, h)  # the lit edge towards the content, in supernova colours
        edge.setColorAt(0, QColor(255, 61, 139, 150))
        edge.setColorAt(0.4, QColor(255, 134, 46, 90))
        edge.setColorAt(0.75, QColor(138, 80, 255, 60))
        edge.setColorAt(1, QColor(20, 184, 166, 40))
        p.fillRect(QRectF(w - 1.5, 0, 1.5, h), edge)
        p.end()
        return pm

    def _section(self, text: str) -> SectionHeader:
        lb = SectionHeader(text)
        box = QWidget()
        box.setStyleSheet("background: transparent;")
        self._target = QVBoxLayout(box)
        self._target.setContentsMargins(0, 0, 0, 0)
        self._target.setSpacing(2)
        self.lay.addWidget(lb)
        self.lay.addWidget(box)
        folded = set(self.s.settings.get("sidebar.collapsed") or [])
        lb.collapsed = text.upper() in folded
        box.setVisible(not lb.collapsed)

        def toggle(collapsed: bool, name=text.upper(), box=box) -> None:
            box.setVisible(not collapsed)
            now = set(self.s.settings.get("sidebar.collapsed") or [])
            (now.add if collapsed else now.discard)(name)
            self.s.settings.set("sidebar.collapsed", sorted(now))
            QTimer.singleShot(0, self.orbit.arrange)

        lb.on_toggle = toggle
        return lb

    def _add(self, key: str, text: str, icon_name: str | None = None, pixmap=None) -> SidebarButton:
        b = SidebarButton(text, icon_name, pixmap)
        self.group.addButton(b)
        self.buttons[key] = b
        b.clicked.connect(lambda: self._clicked(key))
        self._target.addWidget(b)
        return b

    def _build(self) -> None:
        gog = self.s.integrations.get("gog")
        self._section("GOG.COM")
        self._add("home", "Home", "home")
        self._add("store", "Store", "store")
        self._add("wishlist", "Wishlist", "star")
        if gog:
            self._add("library:source:gog", "My GOG games", pixmap=icons.badge(gog.letter, gog.color, 18))
        self._section("Library")
        self._add("library:owned", "All games", "library")
        self._add("library:installed", "Installed", "installed")
        self._add("library:favorites", "Favourites", "heart")
        self._add("library:updates", "Updates", "download")
        self.platforms_header = self._section("Other platforms")
        for integ in self.s.integrations.values():
            if integ.id != "gog":
                self._add(f"library:source:{integ.id}", integ.name, pixmap=icons.badge(integ.letter, integ.color, 18))
        self.views_header = self._section("Views")
        self.views_box = QVBoxLayout()
        self.views_box.setSpacing(2)
        self._target.addLayout(self.views_box)
        self._add("library:hidden", "Hidden games", "eye-off")
        self._section("Activity")
        self._add("downloads", "Downloads", "download")
        if STATS_ENABLED:
            self._add("stats", "Statistics", "chart")
        self.lay.addStretch(1)
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

    def dock_tools(self, topbar) -> None:
        """Settings, downloads and sync sit at the bottom left, above "Add games & friends"."""
        for w, tip in ((topbar.settings_btn, "Settings (Ctrl+,)"), (topbar.dl, "Downloads"),
                       (topbar.sync, "Sync all platforms (F5)")):
            w.setObjectName("SidebarTool")
            w.setToolTip(tip)
            w.setMinimumHeight(34)
            self.tools.addWidget(w, 1)
        self.tools.addWidget(topbar.sync_spinner)
        topbar.sync_lbl.setParent(self)  # its text becomes the sync button's tooltip
        topbar.sync_lbl.hide()

    def check_wishlist(self) -> None:
        """Hide the Wishlist entry when GOG doesn't share the wishlist (or nobody is signed in)."""
        gog = self.s.integrations.get("gog")
        btn = self.buttons.get("wishlist")
        if gog is None or btn is None:
            return
        if not gog.connected():
            btn.setVisible(False)
            return

        def done(_w):
            try:
                btn.setVisible(gog.wishlist_available())
            except RuntimeError:  # the sidebar was rebuilt meanwhile
                pass

        from nebula.core import tasks

        tasks.run_async(gog.wishlist, True, on_done=done, on_error=lambda _e: btn.setVisible(False))

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
        any_platform = False
        for integ in self.s.integrations.values():
            b = self.buttons.get(f"library:source:{integ.id}")
            if not b:
                continue
            b.set_count(c.get(integ.id, 0))
            visible = enabled.get(integ.id, True) and (integ.id == "gog" or integ.connected() or c.get(integ.id, 0) > 0)
            b.setVisible(visible)
            any_platform |= visible and integ.id != "gog"
        self.platforms_header.setVisible(any_platform)
        self.views_header.setVisible(bool(self.s.settings.get("library.custom_views")) or bool(c.get("hidden")))
        from nebula.core.library import LibraryFilter

        for i, v in enumerate(self.s.settings.get("library.custom_views") or []):
            b = self.buttons.get(f"library:view:{i}")
            if b:
                b.set_count(len(self.s.library.query(LibraryFilter.from_dict(v.get("filter", {})))))
        self._update_downloads()

    def _update_downloads(self) -> None:
        self.buttons["downloads"].set_badge(len(self.s.jobs.active_jobs()))

    def _sync_state(self, iid: str, on: bool) -> None:
        b = self.buttons.get(f"library:source:{iid}")
        if b:
            b.set_syncing(on)

    def _add_menu(self) -> None:
        m = QMenu(self)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        gog = self.s.integrations.get("gog")
        if gog and not gog.connected():
            m.addAction(icons.icon("user"), "Sign in with GOG…", self.ctl.gog_login)
        m.addAction(icons.icon("plus"), "Add game manually…", self.ctl.add_game)
        m.addAction(icons.icon("folder"), "Scan folder for installed games…", self.ctl.scan_folder)
        m.addAction(icons.icon("link"), "Connect other platforms…",
                    lambda: self.win.navigate("settings", "Integrations"))
        m.addAction(icons.icon("users"), "Add friends on GOG.COM",
                    lambda: xdg_open("https://www.gog.com/en/account/friends"))
        m.addSeparator()
        m.addAction(icons.icon("proton"), "Download GE-Proton…", self.ctl.proton_download)
        btn = self.sender()
        m.exec(btn.mapToGlobal(btn.rect().topLeft()) - QPoint(0, m.sizeHint().height()))


class TopBar(QWidget):
    search_submitted = Signal(str)

    def __init__(self, services, ctl, window):
        super().__init__()
        self.s = services
        self.ctl = ctl
        self.win = window
        self.setObjectName("TopBar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedHeight(58)
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
        self.search.setPlaceholderText("Search your games and GOG.COM")
        self.search.setToolTip("Search your library and the GOG store (Ctrl+F)")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(380)
        self._search_icon = self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self.search.returnPressed.connect(lambda: self.search_submitted.emit(self.search.text().strip()))
        self._titles = QStringListModel()
        comp = QCompleter(self._titles, self)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setMaxVisibleItems(10)
        comp.popup().setObjectName("SearchPopup")  # a separate window: styled explicitly for every theme
        comp.activated.connect(self._completed)
        self.search.setCompleter(comp)
        lay.addWidget(self.search)
        self.sync_spinner = Spinner(16)
        self.sync_spinner.hide()
        self.sync_lbl = label("", "Faint")
        lay.addStretch(1)
        self.sync = icon_button("refresh", "Sync all platforms (F5)")
        self.sync.clicked.connect(lambda: self.s.library.refresh())
        self.dl = QPushButton()
        self.dl.setObjectName("IconButton")
        self.dl.setIcon(icons.icon("download"))
        self.dl.setIconSize(QSize(18, 18))
        self.dl.setToolTip("Downloads")
        self.dl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dl.clicked.connect(lambda: self.win.navigate("downloads"))
        self.friends = QPushButton()
        self.friends.setObjectName("IconButton")
        self.friends.setIcon(icons.icon("users"))
        self.friends.setIconSize(QSize(18, 18))
        self.friends.setCheckable(True)
        self.friends.setToolTip("Friends")
        self.friends.setCursor(Qt.CursorShape.PointingHandCursor)
        self.friends.clicked.connect(window.toggle_friends)
        lay.addSpacing(6)
        # GOG identity: avatar menu when signed in, otherwise a sign-in button
        self.sign_in = QPushButton("Sign in with GOG")
        self.sign_in.setObjectName("GogSignIn")
        self.sign_in.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sign_in.clicked.connect(ctl.gog_login)
        lay.addWidget(self.sign_in)
        self.avatar_btn = QPushButton()
        self.avatar_btn.setObjectName("AvatarButton")
        self.avatar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.avatar_btn.setMinimumHeight(36)
        al = self._avatar_lay = QHBoxLayout(self.avatar_btn)
        al.setContentsMargins(3, 2, 10, 2)
        al.setSpacing(8)
        self.avatar = Avatar(30)
        self.avatar_name = label("", None)
        self.avatar_name.setStyleSheet("background: transparent; font-weight: 600;")
        for w in (self.avatar, self.avatar_name):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        al.addWidget(self.avatar)
        al.addWidget(self.avatar_name)
        self.avatar_btn.clicked.connect(self._profile_menu)
        lay.addWidget(self.avatar_btn)
        lay.addWidget(self.friends)  # right of the user, like Galaxy
        self.settings_btn = icon_button("settings", "Settings (Ctrl+,)", size=19)
        self.settings_btn.clicked.connect(lambda: self.win.navigate("settings"))
        # sync, downloads and settings live in the sidebar's tool row (Sidebar.dock_tools)
        services.bus.sync_state.connect(self._sync)
        services.bus.library_changed.connect(self._update_titles)
        services.bus.jobs_changed.connect(self._downloads)
        services.bus.job_progress.connect(lambda _j: self._downloads())
        services.bus.friends_changed.connect(self._friends)
        services.bus.integration_changed.connect(lambda _i: self.refresh_profile())
        self._update_titles()
        self.refresh_profile()
        self._light = False
        self._restyle()

    def changeEvent(self, e):  # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.Type.StyleChange and hasattr(self, "_light"):
            self._restyle()

    def _restyle(self) -> None:
        """Follow the theme: icons in its colours (Super Nova's are dark on light glass, the friends one pink)."""
        if self._light != theme.light():
            self._light = theme.light()
            for b, name in ((self.back, "back"), (self.fwd, "forward"), (self.sync, "refresh"),
                            (self.settings_btn, "settings")):
                b.setIcon(icons.icon(name, theme.TEXT_DIM))
            self._search_icon.setIcon(icons.icon("search", theme.TEXT_DIM))
            self.friends.setIcon(icons.icon("users", theme.TEXT_DIM, theme.ACCENT_PRESSED if self._light else None))
            self._downloads()

    def paintEvent(self, e):  # noqa: N802
        super().paintEvent(e)
        if theme.light():  # Super Nova: a thin band of the supernova's colours along the bottom edge
            p = QPainter(self)
            band = QLinearGradient(0, 0, self.width(), 0)
            for i, c in enumerate(NOVA[:5]):
                band.setColorAt(i / 4, QColor(c.red(), c.green(), c.blue(), 150))
            p.fillRect(QRectF(0, self.height() - 1.5, self.width(), 1.5), band)

    def refresh_profile(self) -> None:
        gog = self.s.integrations.get("gog")
        signed_in = bool(gog and gog.connected())
        self.sign_in.setVisible(not signed_in)
        self.avatar_btn.setVisible(signed_in)
        if signed_in:
            name = gog.account_name() or "GOG user"
            self.avatar_name.setText(name if len(name) <= 20 else name[:19] + "…")
            # a push button does not size itself to a child layout
            self.avatar_btn.setFixedWidth(self._avatar_lay.sizeHint().width())
            self.avatar.set_avatar(gog.avatar_url(), name, theme.SUCCESS)
            self.avatar_btn.setToolTip(f"Signed in to GOG as {name}")

    def _profile_menu(self) -> None:
        gog = self.s.integrations.get("gog")
        m = QMenu(self)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if gog and gog.connected():
            name = gog.account_name()
            head = m.addAction(f"Signed in as {name}")
            head.setEnabled(False)
            m.addSeparator()
            m.addAction(icons.icon("user"), "My GOG profile", lambda: xdg_open(f"https://www.gog.com/u/{name}"))
            if gog.wishlist_available():
                m.addAction(icons.icon("star"), "Wishlist", lambda: self.win.navigate("wishlist"))
            m.addAction(icons.icon("external"), "GOG account settings",
                        lambda: xdg_open("https://www.gog.com/en/account/settings"))
            m.addSeparator()
            m.addAction(icons.icon("logout"), "Sign out of GOG…", self.ctl.gog_logout)
        else:
            m.addAction(icons.icon("user"), "Sign in with GOG…", self.ctl.gog_login)
        m.addSeparator()
        m.addAction(icons.icon("power"), "Quit Super Nova", self.win.quit_app)
        m.exec(self.avatar_btn.mapToGlobal(self.avatar_btn.rect().bottomLeft()))

    def _sync(self, *_a) -> None:
        syncing = self.s.library.syncing
        if syncing:
            self.sync_spinner.start()
            names = [self.s.integrations[i].name for i in syncing if i in self.s.integrations]
            self.sync_lbl.setText("Syncing " + ", ".join(names))
            self.sync.setToolTip(self.sync_lbl.text())
        else:
            self.sync_spinner.stop()
            self.sync_lbl.setText("")
            self.sync.setToolTip("Sync all platforms (F5)")

    def _update_titles(self) -> None:
        self._titles.setStringList(sorted({e.title for e in self.s.library.all_entries() if not e.user.hidden},
                                          key=str.lower))

    def _completed(self, title: str) -> None:
        e = next((e for e in self.s.library.all_entries() if e.title == title), None)
        if e:
            self.win.navigate("game", e.key)
            QTimer.singleShot(0, self.search.clear)

    def _downloads(self) -> None:
        jobs = self.s.jobs.active_jobs()
        if jobs:
            running = [j for j in jobs if j.state == "running"]
            pct = int(sum(j.progress for j in jobs) * 100 / len(jobs))
            self.dl.setText(f" {pct}%" if running else f" {len(jobs)}")
            self.dl.setIcon(icons.icon("download", theme.ACCENT_HOVER))
        else:
            self.dl.setText("")
            self.dl.setIcon(icons.icon("download", theme.TEXT_DIM))

    def _friends(self) -> None:
        n = self.s.friends.online_count()
        self.friends.setText(f" {n}" if n else "")
