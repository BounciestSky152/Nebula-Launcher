"""Animated starfield painted behind the whole window.

The sky (nebula glow + a few thousand stars) is rendered once per window size
into a pixmap. Animation is deliberately cheap: a handful of stars twinkle and
the occasional shooting star crosses the sky, and only the few pixels around
them are repainted, so translucent pages on top are not redrawn every frame.
"""
from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap, QRadialGradient, QRegion
from PySide6.QtWidgets import QWidget

SKY_TOP = QColor(6, 6, 16)
SKY_BOTTOM = QColor(12, 8, 26)
STAR_COLORS = [QColor(255, 255, 255), QColor(200, 215, 255), QColor(255, 236, 214), QColor(220, 200, 255)]
TWINKLERS = 45
FPS = 8


class Starfield(QWidget):
    def __init__(self, parent=None, seed: int = 7):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._seed = seed
        self._sky: QPixmap | None = None
        self._twinkle: list[tuple[float, float, float, float, QColor]] = []  # x, y, radius, phase, colour
        self._frame = 0
        self._shooting: tuple | None = None  # (x0, y0, dx, dy, start_frame, frames)
        self._next_shot = 40
        self.animate = True
        self.show_stars = True
        self.classic = False  # the original, brighter sky with flared stars
        self.timer = QTimer(self)
        self.timer.setInterval(1000 // FPS)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ---------------------------------------------------------- rendering
    def set_animated(self, on: bool) -> None:
        self.animate = on
        if on:
            self.timer.start()
        else:
            self.timer.stop()
            self.update()

    def set_stars(self, on: bool) -> None:
        """Plain dark background when ``on`` is False."""
        self.show_stars = on
        self._sky = None
        self.update()

    def set_classic(self, on: bool) -> None:
        if on != self.classic:
            self.classic = on
            self._sky = None
            self.update()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._sky = None

    def _render_sky(self) -> None:
        w, h = max(1, self.width()), max(1, self.height())
        dpr = self.devicePixelRatioF() or 1.0
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, SKY_TOP)
        bg.setColorAt(1, SKY_BOTTOM)
        p.fillRect(QRectF(0, 0, w, h), bg)
        self._twinkle = []
        if not self.show_stars:
            p.end()
            self._sky = pm
            return
        rnd = random.Random(self._seed)
        # soft nebula clouds in GOG-ish purples and blues
        for _ in range(7):
            cx, cy = rnd.uniform(-0.1, 1.1) * w, rnd.uniform(-0.1, 1.1) * h
            r = rnd.uniform(0.25, 0.6) * max(w, h)
            hue = rnd.choice([268, 275, 285, 230, 300, 210])
            g = QRadialGradient(QPointF(cx, cy), r)
            c = (QColor.fromHsl(hue, 180, 90, rnd.randint(18, 38)) if self.classic
                 else QColor.fromHsl(hue, 90, 70, rnd.randint(6, 14)))
            g.setColorAt(0, c)
            g.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(g)
            p.drawEllipse(QPointF(cx, cy), r, r)
        # a faint milky band
        band = QLinearGradient(0, h * 0.15, w, h * 0.85)
        band.setColorAt(0.0, QColor(150, 140, 255, 0))
        band.setColorAt(0.5, QColor(170, 150, 255, 14) if self.classic else QColor(200, 200, 230, 6))
        band.setColorAt(1.0, QColor(150, 140, 255, 0))
        p.fillRect(QRectF(0, 0, w, h), band)
        # stars
        count = int(w * h / 1800)
        self._twinkle = []
        for i in range(count):
            x, y = rnd.uniform(0, w), rnd.uniform(0, h)
            mag = rnd.random() ** 3  # many faint, few bright
            radius = 0.35 + mag * 1.4
            col = QColor(rnd.choice(STAR_COLORS))
            col.setAlpha(int(70 + mag * 185))
            p.setBrush(col)
            p.drawEllipse(QPointF(x, y), radius, radius)
            if mag > 0.9:
                glow = QRadialGradient(QPointF(x, y), radius * 4)
                gc = QColor(col)
                gc.setAlpha(35)
                glow.setColorAt(0, gc)
                gc.setAlpha(0)
                glow.setColorAt(1, gc)
                p.setBrush(glow)
                p.drawEllipse(QPointF(x, y), radius * 4, radius * 4)
                if self.classic:  # the original cross flares
                    p.setPen(QPen(QColor(255, 255, 255, 70), 0.6))
                    p.drawLine(QPointF(x - radius * 5, y), QPointF(x + radius * 5, y))
                    p.drawLine(QPointF(x, y - radius * 5), QPointF(x, y + radius * 5))
                    p.setPen(Qt.PenStyle.NoPen)
            if len(self._twinkle) < TWINKLERS and 0.25 < mag < 0.8 and i % 3 == 0:
                self._twinkle.append((x, y, radius + 0.4, rnd.uniform(0, math.tau), QColor(rnd.choice(STAR_COLORS))))
        p.end()
        self._sky = pm

    def paintEvent(self, e):  # noqa: N802
        if self._sky is None or self._sky.deviceIndependentSize().toSize() != self.size():
            self._render_sky()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.drawPixmap(0, 0, self._sky)
        if not self.animate or not self.show_stars:
            return
        t = self._frame / FPS
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, r, phase, col in self._twinkle:
            a = 0.5 + 0.5 * math.sin(t * 1.7 + phase)
            c = QColor(col)
            c.setAlpha(int(40 + 215 * a))
            p.setBrush(c)
            p.drawEllipse(QPointF(x, y), r * (0.7 + 0.5 * a), r * (0.7 + 0.5 * a))
        if self._shooting:
            x0, y0, dx, dy, start, frames = self._shooting
            k = (self._frame - start) / frames
            if 0 <= k <= 1:
                hx, hy = x0 + dx * k, y0 + dy * k
                tail = QLinearGradient(QPointF(hx, hy), QPointF(hx - dx * 0.18, hy - dy * 0.18))
                tail.setColorAt(0, QColor(255, 255, 255, int(230 * (1 - k * 0.6))))
                tail.setColorAt(1, QColor(255, 255, 255, 0))
                p.setPen(QPen(tail, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(hx, hy), QPointF(hx - dx * 0.18, hy - dy * 0.18))

    # ---------------------------------------------------------- animation
    def _tick(self) -> None:
        if not self.animate or not self.show_stars or not self.isVisible() or self.window().isMinimized():
            return
        self._frame += 1
        region = QRegion()
        for x, y, r, _phase, _c in self._twinkle:
            s = int(r * 2 + 4)
            region += QRect(int(x) - s, int(y) - s, s * 2, s * 2)
        if self._shooting is None and self._frame >= self._next_shot:
            rnd = random.Random(self._frame)
            w, h = self.width(), self.height()
            x0, y0 = rnd.uniform(0.2, 0.95) * w, rnd.uniform(0.0, 0.35) * h
            length = rnd.uniform(0.18, 0.32) * w
            ang = rnd.uniform(math.radians(200), math.radians(235))
            # angle in the lower-left quadrant: moves left (cos < 0) and down (-sin > 0 in screen space)
            self._shooting = (x0, y0, math.cos(ang) * length, -math.sin(ang) * length, self._frame, FPS)
        if self._shooting:
            x0, y0, dx, dy, start, frames = self._shooting
            box = QRectF(QPointF(x0, y0), QPointF(x0 + dx, y0 + dy)).normalized().adjusted(-6, -6, 6, 6)
            region += box.toAlignedRect()
            if self._frame - start > frames + 1:
                self._shooting = None
                self._next_shot = self._frame + random.Random(self._frame).randint(FPS * 15, FPS * 40)
        self.update(region)
