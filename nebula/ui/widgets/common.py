"""Reusable widgets."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPropertyAnimation, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout, QPushButton,
                               QSizePolicy, QToolButton, QVBoxLayout, QWidget, QWidgetItem)

from nebula.core.images import images
from nebula.ui import icons, theme


def label(text: str = "", obj: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lb = QLabel(text)
    if obj:
        lb.setObjectName(obj)
    lb.setWordWrap(wrap)
    if selectable:
        lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lb


def icon_button(name: str, tooltip: str = "", checkable: bool = False, size: int = 18,
                color: str = theme.TEXT_DIM) -> QPushButton:
    b = QPushButton()
    b.setObjectName("IconButton")
    b.setIcon(icons.icon(name, color))
    b.setIconSize(QSize(size, size))
    b.setToolTip(tooltip)
    b.setCheckable(checkable)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def button(text: str, icon_name: str | None = None, obj: str | None = None, primary: bool = False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setObjectName("Primary")
    elif obj:
        b.setObjectName(obj)
    if icon_name:
        b.setIcon(icons.icon(icon_name, "white" if primary else theme.TEXT_DIM, theme.TEXT if not primary else "white"))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def card(layout: QLayout | None = None) -> QFrame:
    f = QFrame()
    f.setObjectName("Card")
    if layout is not None:
        f.setLayout(layout)
    return f


def hline() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet(f"background:{theme.BORDER};")
    return f


def clear_layout(layout: QLayout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def placeholder_pixmap(size: QSize, title: str, radius: float = 6) -> QPixmap:
    pm = QPixmap(size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    h = sum(ord(c) for c in title or "x")
    c1 = QColor.fromHsl(h % 360, 70, 70)
    c2 = QColor.fromHsl((h * 7) % 360, 90, 35)
    grad = QLinearGradient(0, 0, size.width(), size.height())
    grad.setColorAt(0, c1)
    grad.setColorAt(1, c2)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size.width(), size.height()), radius, radius)
    p.fillPath(path, grad)
    f = QFont()
    f.setBold(True)
    f.setPixelSize(max(11, size.width() // 9))
    p.setFont(f)
    p.setPen(QColor(255, 255, 255, 230))
    p.drawText(QRect(10, 10, size.width() - 20, size.height() - 20),
               Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, title)
    p.end()
    return pm


class RemoteImage(QWidget):
    """Displays the first loadable image of a URL list (cover-cropped, rounded corners)."""

    def __init__(self, size: QSize | None = None, radius: float = 6, mode: str = "cover", parent=None):
        super().__init__(parent)
        self._urls: list[str] = []
        self._title = ""
        self._pm: QPixmap | None = None
        self.radius = radius
        self.mode = mode
        if size:
            self.setFixedSize(size)

    def set_source(self, urls: list[str] | str, title: str = "") -> None:
        if isinstance(urls, str):
            urls = [urls] if urls else []
        if urls == self._urls and title == self._title and self._pm is not None:
            return
        self._urls = list(urls)
        self._title = title
        self._pm = None
        self._load()
        self.update()

    def _load(self) -> None:
        if not self._urls:
            return
        dpr = self.devicePixelRatioF() or 1.0
        target = QSize(int(self.width() * dpr), int(self.height() * dpr))
        urls = list(self._urls)

        def got(pm):
            if pm is not None and urls == self._urls:
                self._pm = pm
                self.update()

        pm = images().get(urls, target, got, mode=self.mode)
        if pm is not None:
            self._pm = pm

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        if self._urls and (self._pm is None or abs(self._pm.width() - self.width() * self.devicePixelRatioF()) > 4):
            self._load()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, self.radius, self.radius)
        p.setClipPath(path)
        if self._pm is not None and not self._pm.isNull():
            if self.mode == "fit":
                pm = self._pm.scaled(self.size() * self.devicePixelRatioF(), Qt.AspectRatioMode.KeepAspectRatio,
                                     Qt.TransformationMode.SmoothTransformation)
                pm.setDevicePixelRatio(self.devicePixelRatioF())
                w, h = pm.width() / pm.devicePixelRatio(), pm.height() / pm.devicePixelRatio()
                p.drawPixmap(QRectF((r.width() - w) / 2, (r.height() - h) / 2, w, h), pm, QRectF(pm.rect()))
            else:
                p.drawPixmap(r, self._pm, QRectF(self._pm.rect()))
        else:
            p.drawPixmap(self.rect(), placeholder_pixmap(self.size(), self._title, self.radius))


class HeroBanner(RemoteImage):
    """Wide background image fading into the page background."""

    def __init__(self, height: int = 380, parent=None):
        super().__init__(None, radius=0, parent=parent)
        self.setFixedHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def paintEvent(self, e):  # noqa: N802
        p = QPainter(self)
        r = QRectF(self.rect())
        if self._pm is not None and not self._pm.isNull():
            pm = self._pm
            pr = pm.width() / max(1, pm.height())
            target_ratio = r.width() / max(1, r.height())
            if pr > target_ratio:
                sw = pm.height() * target_ratio
                src = QRectF((pm.width() - sw) / 2, 0, sw, pm.height())
            else:
                sh = pm.width() / target_ratio
                src = QRectF(0, 0, pm.width(), sh)
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            p.drawPixmap(r, pm, src)
        else:
            h = sum(ord(c) for c in self._title or "x")
            g = QLinearGradient(0, 0, r.width(), r.height())
            g.setColorAt(0, QColor.fromHsl(h % 360, 60, 40))
            g.setColorAt(1, QColor(theme.BG))
            p.fillRect(r, g)
        fade = QLinearGradient(0, 0, 0, r.height())
        bg = QColor(theme.BG)
        fade.setColorAt(0.0, QColor(bg.red(), bg.green(), bg.blue(), 40))
        fade.setColorAt(0.55, QColor(bg.red(), bg.green(), bg.blue(), 120))
        fade.setColorAt(1.0, bg)
        p.fillRect(r, fade)
        side = QLinearGradient(0, 0, r.width(), 0)
        side.setColorAt(0, QColor(bg.red(), bg.green(), bg.blue(), 170))
        side.setColorAt(0.5, QColor(bg.red(), bg.green(), bg.blue(), 0))
        p.fillRect(r, side)

    def _load(self) -> None:
        if not self._urls:
            return
        urls = list(self._urls)

        def got(pm):
            if pm is not None and urls == self._urls:
                self._pm = pm
                self.update()

        pm = images().get(urls, None, got)
        if pm is not None:
            self._pm = pm


class PlatformBadges(QWidget):
    def __init__(self, size: int = 16, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, str]] = []
        self._size = size
        self.setFixedHeight(size)

    def set_sources(self, sources: list[str], integrations: dict) -> None:
        self._items = []
        for s in sources:
            integ = integrations.get(s)
            if integ:
                self._items.append((integ.letter, integ.color))
        self.setFixedWidth(max(1, len(self._items) * (self._size + 4)))
        self.setToolTip(", ".join(integrations[s].name for s in sources if s in integrations))
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        x = 0
        for letter, color in self._items:
            p.drawPixmap(x, 0, icons.badge(letter, color, self._size))
            x += self._size + 4


class StarRating(QWidget):
    changed = Signal(int)

    def __init__(self, size: int = 18, editable: bool = True, parent=None):
        super().__init__(parent)
        self._value = 0
        self._hover = -1
        self._size = size
        self.editable = editable
        self.setFixedSize(size * 5 + 4 * 4, size)
        self.setMouseTracking(True)
        if editable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip("Your rating")

    def value(self) -> int:
        return self._value

    def set_value(self, v: int) -> None:
        self._value = int(v or 0)
        self.update()

    def _star_at(self, x: float) -> int:
        return min(4, max(0, int(x // (self._size + 4))))

    def mouseMoveEvent(self, e):  # noqa: N802
        if self.editable:
            self._hover = self._star_at(e.position().x())
            self.update()

    def leaveEvent(self, _e):  # noqa: N802
        self._hover = -1
        self.update()

    def mousePressEvent(self, e):  # noqa: N802
        if not self.editable:
            return
        v = self._star_at(e.position().x()) + 1
        self._value = 0 if v == self._value else v
        self.changed.emit(self._value)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        shown = self._hover + 1 if self._hover >= 0 else self._value
        for i in range(5):
            name = "star-filled" if i < shown else "star"
            color = theme.WARNING if i < shown else theme.TEXT_FAINT
            p.drawPixmap(i * (self._size + 4), 0, icons.pixmap(name, self._size, color))


class Avatar(QWidget):
    def __init__(self, size: int = 32, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._pm: QPixmap | None = None
        self._name = ""
        self._status_color: str | None = None
        self._url = ""

    def set_avatar(self, url: str, name: str = "", status_color: str | None = None) -> None:
        self._name = name
        self._status_color = status_color
        if url != self._url:
            self._url = url
            self._pm = None
            if url:
                dpr = self.devicePixelRatioF() or 1.0
                target = QSize(int(self.width() * dpr), int(self.height() * dpr))

                def got(pm, u=url):
                    if u == self._url:
                        self._pm = pm
                        self.update()

                pm = images().get([url], target, got)
                if pm is not None:
                    self._pm = pm
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addEllipse(r)
        p.save()
        p.setClipPath(path)
        if self._pm is not None and not self._pm.isNull():
            p.drawPixmap(r, self._pm, QRectF(self._pm.rect()))
        else:
            h = sum(ord(c) for c in self._name or "?")
            p.fillPath(path, QColor.fromHsl(h % 360, 80, 60))
            f = QFont()
            f.setBold(True)
            f.setPixelSize(int(self.height() * 0.45))
            p.setFont(f)
            p.setPen(QColor("white"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, (self._name or "?")[:1].upper())
        p.restore()
        if self._status_color:
            d = max(8, self.width() * 0.3)
            dot = QRectF(self.width() - d, self.height() - d, d, d)
            p.setPen(QPen(QColor(theme.SIDEBAR), 2))
            p.setBrush(QColor(self._status_color))
            p.drawEllipse(dot)


class Spinner(QWidget):
    def __init__(self, size: int = 18, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.setInterval(30)

    def start(self) -> None:
        self._timer.start()
        self.show()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _tick(self) -> None:
        self._angle = (self._angle + 12) % 360
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(theme.ACCENT), max(2, self.width() // 8))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        m = pen.width()
        p.drawArc(QRectF(m, m, self.width() - 2 * m, self.height() - 2 * m), -self._angle * 16, 270 * 16)


class Sparkline(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[float] = []
        self.setMinimumHeight(36)

    def set_values(self, values) -> None:
        self.values = list(values)
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        if len(self.values) < 2:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height() - 2
        top = max(self.values) or 1
        step = w / (len(self.values) - 1)
        path = QPainterPath()
        path.moveTo(0, h)
        for i, v in enumerate(self.values):
            path.lineTo(i * step, h - (v / top) * (h - 4))
        line = QPainterPath(path)
        path.lineTo(w, h)
        path.closeSubpath()
        c = QColor(theme.ACCENT)
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 110))
        g.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
        p.fillPath(path, g)
        p.setPen(QPen(c, 1.6))
        p.drawPath(line)


class BarChart(QWidget):
    """Vertical bar chart: list of (label, value)."""

    def __init__(self, unit: str = "h", parent=None):
        super().__init__(parent)
        self.data: list[tuple[str, float]] = []
        self.unit = unit
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self._hover = -1

    def set_data(self, data) -> None:
        self.data = list(data)
        self.update()

    def mouseMoveEvent(self, e):  # noqa: N802
        if not self.data:
            return
        slot = self.width() / len(self.data)
        self._hover = int(e.position().x() // slot)
        self.update()

    def leaveEvent(self, _e):  # noqa: N802
        self._hover = -1
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        label_h = 20
        chart_h = h - label_h - 18
        top = max(v for _, v in self.data) or 1
        slot = w / len(self.data)
        bar_w = min(38, slot * 0.6)
        f = p.font()
        f.setPointSizeF(8)
        p.setFont(f)
        p.setPen(QPen(QColor(theme.BORDER), 1))
        p.drawLine(0, int(18 + chart_h), w, int(18 + chart_h))
        for i, (lab, v) in enumerate(self.data):
            x = i * slot + (slot - bar_w) / 2
            bh = (v / top) * chart_h if top else 0
            r = QRectF(x, 18 + chart_h - bh, bar_w, max(bh, 2 if v else 0))
            color = QColor(theme.ACCENT_HOVER if i == self._hover else theme.ACCENT)
            path = QPainterPath()
            path.addRoundedRect(r, 3, 3)
            p.fillPath(path, color)
            p.setPen(QColor(theme.TEXT_FAINT))
            p.drawText(QRectF(i * slot, h - label_h, slot, label_h), Qt.AlignmentFlag.AlignCenter, lab)
            if i == self._hover or (v and len(self.data) <= 8):
                p.setPen(QColor(theme.TEXT if i == self._hover else theme.TEXT_DIM))
                txt = f"{v:.1f}{self.unit}" if v < 10 else f"{v:.0f}{self.unit}"
                p.drawText(QRectF(i * slot - 10, r.top() - 17, slot + 20, 16), Qt.AlignmentFlag.AlignCenter, txt)


class HBarChart(QWidget):
    """Horizontal bars with labels: list of (label, value, display_text)."""

    def __init__(self, color: str = theme.ACCENT, parent=None):
        super().__init__(parent)
        self.data: list[tuple[str, float, str]] = []
        self.color = color

    def set_data(self, data) -> None:
        self.data = list(data)
        self.setFixedHeight(max(30, len(self.data) * 30))
        self.update()

    def paintEvent(self, _e):  # noqa: N802
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        label_w = min(220, int(w * 0.38))
        val_w = 70
        top = max(v for _, v, _ in self.data) or 1
        for i, (lab, v, txt) in enumerate(self.data):
            y = i * 30
            p.setPen(QColor(theme.TEXT_DIM))
            fm = p.fontMetrics()
            p.drawText(QRectF(0, y, label_w - 10, 24), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       fm.elidedText(lab, Qt.TextElideMode.ElideRight, label_w - 10))
            bw = (w - label_w - val_w) * (v / top)
            path = QPainterPath()
            path.addRoundedRect(QRectF(label_w, y + 6, max(bw, 3), 12), 4, 4)
            p.fillPath(path, QColor(self.color))
            p.setPen(QColor(theme.TEXT))
            p.drawText(QRectF(w - val_w, y, val_w, 24), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, txt)


class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items: list = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def addWidget(self, w):  # noqa: N802
        self.addChildWidget(w)
        self.addItem(QWidgetItem(w))

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._layout(rect, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _layout(self, rect, test):
        x, y, line_h = rect.x(), rect.y(), 0
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + self._spacing
            if nx - self._spacing > rect.right() and line_h > 0:
                x = rect.x()
                y += line_h + self._spacing
                nx = x + hint.width() + self._spacing
                line_h = 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


class Toast(QFrame):
    def __init__(self, parent: QWidget, title: str, body: str, kind: str = "info"):
        super().__init__(parent)
        self.setObjectName("Card")
        color = {"info": theme.ACCENT, "error": theme.DANGER, "success": theme.SUCCESS}.get(kind, theme.ACCENT)
        self.setStyleSheet(f"#Card {{ border-left: 3px solid {color}; background: {theme.SURFACE2}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        t = label(title)
        t.setStyleSheet("font-weight:600;")
        lay.addWidget(t)
        if body:
            b = label(body, "Subtle", wrap=True)
            lay.addWidget(b)
        self.setFixedWidth(340)
        self.adjustSize()
        eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(eff)
        self._anim = QPropertyAnimation(eff, b"opacity", self)
        self._anim.setDuration(250)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        QTimer.singleShot(5500 if kind != "error" else 9000, self._fade)

    def _fade(self) -> None:
        self._anim.setDirection(QPropertyAnimation.Direction.Backward)
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()

    def mousePressEvent(self, _e):  # noqa: N802
        self.deleteLater()


class ToastHost:
    def __init__(self, window: QWidget):
        self.window = window
        self.toasts: list[Toast] = []

    def show(self, title: str, body: str = "", kind: str = "info") -> None:
        t = Toast(self.window, title, body, kind)
        t.destroyed.connect(lambda *_: self._remove(t))
        self.toasts.append(t)
        t.show()
        t.raise_()
        self.relayout()

    def _remove(self, t) -> None:
        if t in self.toasts:
            self.toasts.remove(t)
        self.relayout()

    def relayout(self) -> None:
        try:
            y = self.window.height() - 20
        except RuntimeError:  # window already destroyed
            return
        for t in reversed(self.toasts[-4:]):
            try:
                t.adjustSize()
                y -= t.height()
                t.move(self.window.width() - t.width() - 20, y)
                y -= 10
            except RuntimeError:
                continue


def tool_button(name: str, tooltip: str = "") -> QToolButton:
    b = QToolButton()
    b.setObjectName("IconButton")
    b.setIcon(icons.icon(name))
    b.setIconSize(QSize(18, 18))
    b.setToolTip(tooltip)
    b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def hbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0), stretch_end: bool = False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    if stretch_end:
        lay.addStretch(1)
    return lay


def vbox(*widgets, spacing: int = 8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def status_color(status: str) -> str:
    return {"playing": theme.SUCCESS, "online": theme.INFO, "away": theme.WARNING, "busy": theme.DANGER}.get(
        status, theme.TEXT_FAINT)

