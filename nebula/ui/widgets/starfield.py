"""Animated starfield painted behind the whole window.

The sky (nebula glow + a few thousand stars) is rendered once per window size
into a pixmap. Animation is deliberately cheap: a handful of stars twinkle and
the occasional shooting star crosses the sky, and only the few pixels around
them are repainted, so translucent pages on top are not redrawn every frame.

Fun extras live here too: one-off effects (a warp jump, fireworks, snow, a
party sky), seasonal skies and the rare UFO or comet. An effect raises the
frame rate only while it runs; afterwards the sky drops back to the idle rate.
"""
from __future__ import annotations

import datetime
import math
import random

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QRadialGradient, QRegion
from PySide6.QtWidgets import QWidget

SKY_TOP = QColor(6, 6, 16)
SKY_BOTTOM = QColor(12, 8, 26)
STAR_COLORS = [QColor(255, 255, 255), QColor(200, 215, 255), QColor(255, 236, 214), QColor(220, 200, 255)]
SPARK_HUES = [268, 300, 330, 45, 190, 140]  # purple, magenta, pink, gold, cyan, green
TWINKLERS = 45
FPS = 8


def season_for(day: datetime.date) -> str | None:
    """The seasonal sky for ``day``: "halloween", "winter", "newyear" or None."""
    if day.month == 10 and day.day >= 24:
        return "halloween"
    if day.month == 12 and day.day <= 26:
        return "winter"
    if (day.month, day.day) in ((12, 31), (1, 1)):
        return "newyear"
    return None


def _smooth(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


class _Effect:
    """Something running in the sky for a while.

    ``step`` advances it (False once it is over), ``paint`` draws it and ``region``
    is what to repaint, unless ``full`` asks for the whole sky every frame.
    """
    name = ""
    fps = FPS
    z = 1  # paint order
    full = False

    def __init__(self, rnd: random.Random, seconds: float):
        self.rnd = random.Random(rnd.random())
        self.t = 0.0
        self.dur = seconds

    def step(self, dt: float, sky: Starfield) -> bool:
        self.t += dt
        return self.t < self.dur

    def paint(self, p: QPainter, sky: Starfield) -> None:
        pass

    def region(self) -> QRegion:
        return QRegion()


class _Warp(_Effect):
    """Hyperspace jump: stars stretch into streaks racing out of the centre, then the sky settles."""
    name, fps, z, full = "warp", 30, 4, True

    def __init__(self, rnd: random.Random, seconds: float):
        super().__init__(rnd, max(0.4, seconds))
        r = self.rnd
        # angle, distance from the centre (fraction of the half diagonal), speed, colour
        self.streaks = [[r.uniform(0, math.tau), r.uniform(0.03, 1.0), r.uniform(0.6, 1.4), r.choice(STAR_COLORS)]
                        for _ in range(240)]

    def power(self) -> float:
        """0 -> 1 -> 0: a quick build-up, the jump, then a slower settle."""
        k = self.t / self.dur
        return _smooth(k / 0.3 if k < 0.3 else 1.0 if k < 0.55 else (1 - k) / 0.45)

    def step(self, dt, sky):
        self.t += dt
        v = self.power()
        for s in self.streaks:
            s[1] *= 1 + dt * (0.3 + 4.5 * v) * s[2]  # perspective: the nearer the edge, the faster
            if s[1] > 1.1:
                s[0], s[1] = self.rnd.uniform(0, math.tau), self.rnd.uniform(0.03, 0.2)
        return self.t < self.dur

    def paint(self, p, sky):
        v = self.power()
        w, h = sky.width(), sky.height()
        cx, cy = w / 2, h / 2
        half = math.hypot(cx, cy)
        p.fillRect(QRectF(0, 0, w, h), QColor(2, 2, 10, int(140 * v)))  # the old sky falls away
        glow = QRadialGradient(QPointF(cx, cy), half * 0.4)
        glow.setColorAt(0, QColor(183, 156, 255, int(60 * v)))
        glow.setColorAt(1, QColor(140, 98, 212, 0))
        p.fillRect(QRectF(0, 0, w, h), glow)
        for ang, d, speed, col in self.streaks:
            alpha = int(255 * min(1.0, d * 3) * min(1.0, v * 2))
            if alpha < 4:
                continue
            r1 = d * half
            r0 = r1 * (1 - min(0.8, 0.65 * v * speed))
            c = QColor(col)
            c.setAlpha(alpha)
            p.setPen(QPen(c, 0.6 + 1.6 * d, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            ca, sa = math.cos(ang), math.sin(ang)
            p.drawLine(QPointF(cx + ca * r0, cy + sa * r0), QPointF(cx + ca * r1, cy + sa * r1))


class _Fireworks(_Effect):
    """Rockets climb from the bottom edge and burst into coloured sparks that fall and fade."""
    name, fps, z = "fireworks", 30, 3

    def __init__(self, rnd: random.Random, seconds: float):
        super().__init__(rnd, seconds)
        self.next = 0.0
        self.rockets: list[list] = []  # x, y, vx, vy, fuse, hue
        self.bursts: list[list] = []  # x, y, age, hue, sparks [x, y, vx, vy, life, life0, hue]

    def step(self, dt, sky):
        self.t += dt
        w, h = sky.width(), sky.height()
        scale = max(0.6, min(w, h) / 700)
        r = self.rnd
        if self.t < self.dur and self.t >= self.next:
            self.rockets.append([r.uniform(0.15, 0.85) * w, h + 4.0, r.uniform(-30, 30) * scale,
                                 -h * r.uniform(0.95, 1.15), r.uniform(0.75, 1.15), r.choice(SPARK_HUES)])
            self.next = self.t + r.uniform(0.3, 0.8)
        for rk in self.rockets:
            rk[0] += rk[2] * dt
            rk[1] += rk[3] * dt
            rk[3] += h * 0.5 * dt
            rk[4] -= dt
        for rk in [rk for rk in self.rockets if rk[4] <= 0]:
            self.rockets.remove(rk)
            self.bursts.append(self._burst(rk[0], rk[1], rk[5], scale))
        drag = max(0.0, 1 - 1.8 * dt)
        for b in self.bursts:
            b[2] += dt
            for s in b[4]:
                s[2] *= drag
                s[3] = s[3] * drag + 70 * scale * dt
                s[0] += s[2] * dt
                s[1] += s[3] * dt
                s[4] -= dt
            b[4] = [s for s in b[4] if s[4] > 0]
        self.bursts = [b for b in self.bursts if b[4]]
        return self.t < self.dur or bool(self.rockets or self.bursts)

    def _burst(self, x: float, y: float, hue: int, scale: float) -> list:
        r = self.rnd
        ring = r.random() < 0.3
        n = r.randint(36, 56)
        sparks = []
        for i in range(n):
            a = i / n * math.tau + r.uniform(-0.05, 0.05) if ring else r.uniform(0, math.tau)
            v = (190 if ring else 20 + 170 * math.sqrt(r.random())) * scale  # sqrt: an even disc, not a clump
            life = r.uniform(1.1, 1.8)
            sparks.append([x, y, math.cos(a) * v, math.sin(a) * v, life, life, (hue + r.randint(-14, 14)) % 360])
        return [x, y, 0.0, hue, sparks]

    def paint(self, p, sky):
        for x, y, age, hue, sparks in self.bursts:
            if age < 0.3:  # the bang
                g = QRadialGradient(QPointF(x, y), 46)
                g.setColorAt(0, QColor.fromHsv(hue, 60, 255, int(110 * (1 - age / 0.3))))
                g.setColorAt(1, QColor.fromHsv(hue, 60, 255, 0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(QPointF(x, y), 46, 46)
            for i, (sx, sy, vx, vy, life, life0, shue) in enumerate(sparks):
                f = life / life0
                if f < 0.35:
                    f *= 0.6 + 0.4 * math.sin(life * 50 + i)  # crackle as they die out
                p.setPen(QPen(QColor.fromHsv(shue, 140, 255, max(0, int(255 * f))), 1.7,
                              Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(sx - vx * 0.05, sy - vy * 0.05), QPointF(sx, sy))
        for x, y, vx, vy, _fuse, _hue in self.rockets:
            tail = QLinearGradient(QPointF(x, y), QPointF(x - vx * 0.08, y - vy * 0.08))
            tail.setColorAt(0, QColor(255, 236, 200, 230))
            tail.setColorAt(1, QColor(255, 180, 120, 0))
            p.setPen(QPen(tail, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(x, y), QPointF(x - vx * 0.08, y - vy * 0.08))

    def region(self):
        reg = QRegion()
        for x, y, vx, vy, _fuse, _hue in self.rockets:
            box = QRectF(QPointF(x, y), QPointF(x - vx * 0.08, y - vy * 0.08)).normalized()
            reg += box.adjusted(-4, -4, 4, 4).toAlignedRect()
        for x, y, age, _hue, sparks in self.bursts:
            xs, ys = [s[0] for s in sparks], [s[1] for s in sparks]
            box = QRectF(QPointF(min(xs), min(ys)), QPointF(max(xs), max(ys))).adjusted(-16, -16, 16, 16)
            if age < 0.3:
                box = box.united(QRectF(x - 48, y - 48, 96, 96))
            reg += box.toAlignedRect()
        return reg


class _Snow(_Effect):
    """Soft flakes drifting down; it fades in, and fades out instead of stopping dead."""
    name, fps, z = "snow", 20, 2
    FADE = 1.5

    def __init__(self, rnd: random.Random, seconds: float | None, sky: Starfield, light: bool = False):
        super().__init__(rnd, math.inf if seconds is None else seconds)
        w, h = sky.width(), sky.height()
        count = max(40, min(80, w * h // 18000))
        self.flakes = [self._flake(w, self.rnd.uniform(0, h)) for _ in range(count // 2 if light else count)]
        self.sprites = [self._sprite(r, sky.devicePixelRatioF() or 1.0) for r in (2.0, 3.0, 4.2)]
        self.alpha = 0.0

    def _flake(self, w: int, y: float) -> list:
        r = self.rnd
        size = r.choice((0, 0, 0, 1, 1, 2))  # mostly small ones
        # x, y, sprite, fall speed, sway phase, sway speed
        return [r.uniform(0, w), y, size, r.uniform(18, 34) * (1 + size * 0.35), r.uniform(0, math.tau), r.uniform(6, 16)]

    @staticmethod
    def _sprite(radius: float, dpr: float) -> QPixmap:
        """A soft round flake, rendered once."""
        size = math.ceil(radius * 2)
        pm = QPixmap(math.ceil(size * dpr), math.ceil(size * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        q = QPainter(pm)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QRadialGradient(QPointF(size / 2, size / 2), size / 2)
        g.setColorAt(0, QColor(255, 255, 255, 235))
        g.setColorAt(0.45, QColor(235, 240, 255, 150))
        g.setColorAt(1, QColor(220, 230, 255, 0))
        q.setPen(Qt.PenStyle.NoPen)
        q.setBrush(g)
        q.drawEllipse(QRectF(0, 0, size, size))
        q.end()
        return pm

    def extend(self, seconds: float | None) -> None:
        self.dur = math.inf if seconds is None else self.t + seconds

    def stop(self) -> None:
        self.dur = min(self.dur, self.t)

    def step(self, dt, sky):
        self.t += dt
        running = self.t < self.dur
        self.alpha = max(0.0, min(1.0, self.alpha + (dt if running else -dt) / self.FADE))
        w, h = sky.width(), sky.height()
        for f in self.flakes:
            f[1] += f[3] * dt
            f[0] += math.sin(self.t * 0.9 + f[4]) * f[5] * dt
            if f[1] > h + 6:
                f[:] = self._flake(w, -6.0)
            elif not -6 < f[0] < w + 6:
                f[0] %= max(1, w)
        return running or self.alpha > 0

    def paint(self, p, sky):
        p.setOpacity(self.alpha)
        for x, y, size, *_ in self.flakes:
            pm = self.sprites[size]
            s = pm.deviceIndependentSize().width() / 2
            p.drawPixmap(QPointF(x - s, y - s), pm)

    def region(self):
        reg = QRegion()
        for x, y, size, *_ in self.flakes:
            s = int(self.sprites[size].deviceIndependentSize().width() / 2) + 2
            reg += QRect(int(x) - s, int(y) - s, s * 2, s * 2)
        return reg


class _Party(_Effect):
    """The nebula cycles through the colour wheel; only a tiny cloud layer is re-tinted, never the stars."""
    name, fps, z, full = "party", 12, 0, True
    FADE = 1.5

    def paint(self, p, sky):
        a = _smooth(min(self.t / self.FADE, (self.dur - self.t) / self.FADE))
        w, h = sky.width(), sky.height()
        layer = QPixmap(max(1, w // 8), max(1, h // 8))  # soft clouds survive the upscale fine
        layer.fill(Qt.GlobalColor.transparent)
        q = QPainter(layer)
        q.setRenderHint(QPainter.RenderHint.Antialiasing)
        q.scale(layer.width() / max(1, w), layer.height() / max(1, h))
        q.setPen(Qt.PenStyle.NoPen)
        for i, (cx, cy, r, hue) in enumerate(sky.clouds):
            c = QColor.fromHsl(int(hue + self.t * 40 + i * 51) % 360, 210, 120, int(95 * a))
            g = QRadialGradient(QPointF(cx, cy), r)
            g.setColorAt(0, c)
            g.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
            q.setBrush(g)
            q.drawEllipse(QPointF(cx, cy), r, r)
        q.end()
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
        p.drawPixmap(QRectF(0, 0, w, h), layer, QRectF(layer.rect()))


class _Flyer(_Effect):
    """One visitor crossing the sky: a shooting star or, with fun extras, a UFO, a comet or bats."""
    SPEC = {"star": (FPS, 1.0), "ufo": (24, 9.0), "comet": (15, 12.0), "bat": (24, 5.0)}  # fps, seconds

    def __init__(self, kind: str, rnd: random.Random, w: int, h: int, hue: int | None = None):
        fps, seconds = self.SPEC[kind]
        super().__init__(rnd, seconds)
        self.name, self.fps, self.hue = kind, fps, hue
        r = self.rnd
        if kind == "star":
            x0, y0 = r.uniform(0.2, 0.95) * w, r.uniform(0.0, 0.35) * h
            length = r.uniform(0.18, 0.32) * w
            ang = r.uniform(math.radians(200), math.radians(235))
            # angle in the lower-left quadrant: moves left (cos < 0) and down (-sin > 0 in screen space)
            self.path = (x0, y0, math.cos(ang) * length, -math.sin(ang) * length)
        elif kind == "comet":
            # a slow drift over part of the sky; it fades in and out rather than crossing the window
            self.path = (r.uniform(0.2, 0.8) * w, r.uniform(0.08, 0.4) * h,
                         r.choice((-1, 1)) * 0.3 * w, r.uniform(0.02, 0.12) * h)
        else:
            sign = r.choice((-1, 1))
            self.path = (-40.0 if sign > 0 else w + 40.0, r.uniform(0.1, 0.45 if kind == "ufo" else 0.6) * h,
                         sign * (w + 80.0), r.uniform(-0.1, 0.1) * h)
        # a bat sometimes brings friends: offset behind the leader, wing phase
        self.bats = [(0.0, 0.0, 0.0)] + [(r.uniform(18, 40), r.uniform(-16, 16), r.uniform(0, math.tau))
                                         for _ in range(r.randint(0, 2) if kind == "bat" else 0)]

    def step(self, dt, sky):
        self.t += dt
        return self.t <= self.dur + (0.2 if self.name == "star" else 0)

    def _pos(self) -> tuple[float, float, float]:
        """Position now, and the progress 0..1 along the path."""
        x0, y0, dx, dy = self.path
        k = min(1.0, self.t / self.dur)
        if self.name == "ufo":
            k += 0.15 * math.sin(k * math.tau)  # zips in, hovers for a look around, zips off
            return x0 + dx * k, y0 + dy * k + 5 * math.sin(self.t * 2.4), k
        if self.name == "bat":
            return x0 + dx * k, y0 + dy * k + 7 * math.sin(self.t * 4.5), k
        return x0 + dx * k, y0 + dy * k, k

    def paint(self, p, sky):
        x, y, k = self._pos()
        if self.name == "star":
            self._paint_star(p, k)
        elif self.name == "ufo":
            self._paint_ufo(p, x, y, k)
        elif self.name == "comet":
            self._paint_comet(p, x, y)
        else:
            sign = 1 if self.path[2] > 0 else -1
            for ox, oy, phase in self.bats:
                _paint_bat(p, x - sign * ox, y + oy, math.sin(self.t * math.tau * 5 + phase), 1.0 - ox / 120)

    def _paint_star(self, p: QPainter, k: float) -> None:
        if not 0 <= self.t / self.dur <= 1:
            return
        x0, y0, dx, dy = self.path
        hx, hy = x0 + dx * k, y0 + dy * k
        col = QColor(255, 255, 255) if self.hue is None else QColor.fromHsv(self.hue, 120, 255)
        tail = QLinearGradient(QPointF(hx, hy), QPointF(hx - dx * 0.18, hy - dy * 0.18))
        col.setAlpha(int(230 * (1 - k * 0.6)))
        tail.setColorAt(0, col)
        col.setAlpha(0)
        tail.setColorAt(1, col)
        p.setPen(QPen(tail, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(hx, hy), QPointF(hx - dx * 0.18, hy - dy * 0.18))

    def _paint_ufo(self, p: QPainter, x: float, y: float, k: float) -> None:
        p.translate(x, y)
        p.setPen(Qt.PenStyle.NoPen)
        hover = max(0.0, 1 - abs(k - 0.5) / 0.12)
        if hover:  # a faint tractor beam while it hovers
            beam = QLinearGradient(0, 2, 0, 58)
            beam.setColorAt(0, QColor(150, 255, 200, int(70 * hover)))
            beam.setColorAt(1, QColor(150, 255, 200, 0))
            p.setBrush(beam)
            p.drawPolygon([QPointF(-5, 2), QPointF(5, 2), QPointF(17, 58), QPointF(-17, 58)])
        p.rotate(7 * math.sin(self.t * 2.4 + 0.8))
        dome = QRadialGradient(QPointF(-1.5, -6), 7)
        dome.setColorAt(0, QColor(215, 245, 255, 220))
        dome.setColorAt(1, QColor(120, 200, 255, 90))
        p.setBrush(dome)
        p.drawEllipse(QRectF(-6, -9, 12, 10))
        body = QLinearGradient(0, -3.5, 0, 3.5)
        body.setColorAt(0, QColor(214, 214, 232))
        body.setColorAt(1, QColor(86, 84, 118))
        p.setBrush(body)
        p.drawEllipse(QRectF(-14, -3.5, 28, 7))
        lights = (QColor(255, 96, 96), QColor(255, 220, 90), QColor(120, 255, 170))
        step = int(self.t * 6)  # the lights chase round the rim
        for i in range(5):
            c = QColor(lights[i % 3])
            c.setAlpha(255 if (step + i) % 3 == 0 else 70)
            p.setBrush(c)
            p.drawEllipse(QPointF(-10 + i * 5, 0.8), 1.3, 1.3)

    def _paint_comet(self, p: QPainter, x: float, y: float) -> None:
        a = _smooth(min(self.t / 2, (self.dur - self.t) / 2))
        dx, dy = self.path[2], self.path[3]
        n = math.hypot(dx, dy) or 1.0
        ux, uy = dx / n, dy / n  # direction of travel; the tails stream the other way
        nx, ny = -uy, ux
        length = 190
        tx, ty = x - ux * length, y - uy * length
        dust = QPainterPath(QPointF(x + nx * 2, y + ny * 2))
        dust.quadTo(QPointF(x - ux * length * 0.5 + nx * 9, y - uy * length * 0.5 + ny * 9),
                    QPointF(tx + nx * 22, ty + ny * 22))
        dust.lineTo(QPointF(tx - nx * 6, ty - ny * 6))
        dust.lineTo(QPointF(x - nx * 2, y - ny * 2))
        dust.closeSubpath()
        g = QLinearGradient(QPointF(x, y), QPointF(tx, ty))
        g.setColorAt(0, QColor(225, 235, 255, int(150 * a)))
        g.setColorAt(1, QColor(170, 150, 255, 0))
        p.fillPath(dust, g)
        ion = QLinearGradient(QPointF(x, y), QPointF(x - ux * length * 1.2, y - uy * length * 1.2))
        ion.setColorAt(0, QColor(150, 210, 255, int(200 * a)))
        ion.setColorAt(1, QColor(150, 210, 255, 0))
        p.setPen(QPen(ion, 1.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(x, y), QPointF(x - ux * length * 1.2, y - uy * length * 1.2))
        coma = QRadialGradient(QPointF(x, y), 12)
        coma.setColorAt(0, QColor(240, 248, 255, int(220 * a)))
        coma.setColorAt(0.4, QColor(170, 210, 255, int(90 * a)))
        coma.setColorAt(1, QColor(170, 210, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(coma)
        p.drawEllipse(QPointF(x, y), 12, 12)

    def region(self):
        x, y, _k = self._pos()
        if self.name == "star":
            x0, y0, dx, dy = self.path
            box = QRectF(QPointF(x0, y0), QPointF(x0 + dx, y0 + dy)).normalized().adjusted(-6, -6, 6, 6)
        elif self.name == "ufo":
            box = QRectF(x - 24, y - 18, 48, 80)
        elif self.name == "comet":
            dx, dy = self.path[2], self.path[3]
            n = math.hypot(dx, dy) or 1.0
            tail = QPointF(x - dx / n * 230, y - dy / n * 230)
            box = QRectF(QPointF(x, y), tail).normalized().adjusted(-28, -28, 28, 28)
        else:
            box = QRectF(x - 60, y - 30, 120, 60)
        return QRegion(box.toAlignedRect())


def _paint_bat(p: QPainter, x: float, y: float, flap: float, size: float) -> None:
    """A little bat silhouette with a moonlit rim; ``flap`` -1..1 moves the wing tips."""
    path = QPainterPath()
    tip = -1 - 5 * flap
    for side in (-1, 1):
        path.moveTo(0, -1)
        path.quadTo(side * 4, -3 - 3 * flap, side * 10, tip)
        path.quadTo(side * 8.5, tip + 3, side * 7, tip / 3 + 2.5)  # scalloped trailing edge
        path.quadTo(side * 5.5, 1.5, side * 4.5, 2.5)
        path.quadTo(side * 3, 1.5, side * 1.5, 3)
        path.closeSubpath()
    path.addEllipse(QPointF(0, 1), 1.8, 3)
    path.addPolygon([QPointF(-1.5, -1), QPointF(-1.1, -3.6), QPointF(-0.3, -1.6), QPointF(0.3, -1.6),
                     QPointF(1.1, -3.6), QPointF(1.5, -1)])  # ears
    p.save()
    p.translate(x, y)
    p.scale(size * 1.3, size * 1.3)
    pen = QPen(QColor(255, 150, 60, 150), 1)
    pen.setCosmetic(True)
    p.setPen(pen)
    p.setBrush(QColor(16, 8, 18))
    p.drawPath(path.simplified())
    p.restore()


class Starfield(QWidget):
    def __init__(self, parent=None, seed: int = 7):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._seed = seed
        self._rnd = random.Random(seed)
        self._sky: QPixmap | None = None
        self._twinkle: list[tuple[float, float, float, float, QColor]] = []  # x, y, radius, phase, colour
        self.clouds: list[tuple[float, float, float, int]] = []  # x, y, radius, hue of the nebula clouds
        self._frame = 0
        self._t = 0.0  # seconds of animated sky so far
        self._effects: list[_Effect] = []
        self._dirty = QRegion()  # last frame's effect pixels, repainted once more to erase them
        self._next_shot = 5.0
        self._next_event = 60.0  # the next seasonal snow shower or fireworks
        self._day_check = 0.0
        self._today = datetime.date.today()
        self.season = season_for(self._today)
        self._season_pinned = False
        self.animate = True
        self.show_stars = True
        self.classic = False  # the original, brighter sky with flared stars
        self.fun = True
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
            self._stop_effects()
            self.update()

    def set_stars(self, on: bool) -> None:
        """Plain dark background when ``on`` is False."""
        self.show_stars = on
        self._sky = None
        if not on:
            self._stop_effects()
        self.update()

    def set_classic(self, on: bool) -> None:
        if on != self.classic:
            self.classic = on
            self._sky = None
            self.update()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._sky = None

    def _spooky(self) -> bool:
        return self.fun and self.season == "halloween"

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
        self.clouds = []
        if not self.show_stars:
            p.end()
            self._sky = pm
            return
        rnd = random.Random(self._seed)
        # soft nebula clouds in GOG-ish purples and blues
        for i in range(7):
            cx, cy = rnd.uniform(-0.1, 1.1) * w, rnd.uniform(-0.1, 1.1) * h
            r = rnd.uniform(0.25, 0.6) * max(w, h)
            hue = rnd.choice([268, 275, 285, 230, 300, 210])
            g = QRadialGradient(QPointF(cx, cy), r)
            c = (QColor.fromHsl(hue, 180, 90, rnd.randint(18, 38)) if self.classic
                 else QColor.fromHsl(hue, 90, 70, rnd.randint(6, 14)))
            if self._spooky() and i % 2 == 0:  # a pumpkin glow in some of the clouds
                c = QColor.fromHsl(26, 200, 80, c.alpha() + 12)
            self.clouds.append((cx, cy, r, hue))
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
        p.setPen(Qt.PenStyle.NoPen)
        for x, y, r, phase, col in self._twinkle:
            a = 0.5 + 0.5 * math.sin(self._t * 1.7 + phase)
            c = QColor(col)
            c.setAlpha(int(40 + 215 * a))
            p.setBrush(c)
            p.drawEllipse(QPointF(x, y), r * (0.7 + 0.5 * a), r * (0.7 + 0.5 * a))
        for fx in sorted(self._effects, key=lambda f: f.z):
            p.save()
            fx.paint(p, self)
            p.restore()

    # ---------------------------------------------------------- fun extras
    def set_fun(self, on: bool) -> None:
        """Fun extras: effects, seasonal skies and rare visitors. Off also ends whatever is running."""
        if on == self.fun:
            return
        spooky = self._spooky()
        self.fun = on
        if on:
            self._next_event = self._t + self._rnd.uniform(20, 90)
            self._new_day(datetime.date.today())
        else:
            self._stop_effects()
        if self._spooky() != spooky:
            self._sky = None
            self.update()

    def set_season(self, season: str | None) -> None:
        """Pin a seasonal sky (None: the normal one); until then it follows the calendar."""
        self._season_pinned = True
        self._apply_season(season)

    def _apply_season(self, season: str | None) -> None:
        if season == self.season:
            return
        spooky = self._spooky()
        self.season = season
        self._next_event = self._t + self._rnd.uniform(20, 90)
        if self._spooky() != spooky:
            self._sky = None
            self.update()

    def _new_day(self, day: datetime.date) -> None:
        """Follow the calendar; the launcher being open at midnight on New Year earns some fireworks."""
        if day == self._today:
            return
        new_year = (day.month, day.day) == (1, 1) and self._today == day - datetime.timedelta(days=1)
        self._today = day
        if not self._season_pinned:
            self._apply_season(season_for(day))
        if new_year:
            self.fireworks(20.0)

    def warp(self, seconds: float = 1.4) -> None:
        """Hyperspace jump: the stars stretch into streaks from the centre, then settle."""
        self._start(_Warp(self._rnd, seconds))

    def fireworks(self, seconds: float = 6.0) -> None:
        """Rockets bursting into coloured sparks for ``seconds``."""
        self._start(_Fireworks(self._rnd, seconds))

    def snow(self, seconds: float | None = None) -> None:
        """Snowfall for ``seconds``; None snows until ``snow(0)``."""
        running = self._find("snow")
        if seconds is not None and seconds <= 0:
            if running:
                running.stop()
        elif running:
            running.extend(seconds)
        else:
            self._start(_Snow(self._rnd, seconds, self))

    def party(self, seconds: float = 20.0) -> None:
        """The nebula cycles through the colour wheel and shooting stars come thick and fast."""
        self._start(_Party(self._rnd, seconds))
        self._next_shot = min(self._next_shot, self._t + 0.5)

    def _live(self) -> bool:
        return self.animate and self.show_stars and self.isVisible() and not self.window().isMinimized()

    def _find(self, name: str) -> _Effect | None:
        return next((fx for fx in self._effects if fx.name == name), None)

    def _start(self, fx: _Effect) -> None:
        """Run ``fx`` in place of any running one of its kind; a no-op on a still or plain sky."""
        if not (self.fun and self._live()):
            return
        self._effects = [f for f in self._effects if f.name != fx.name] + [fx]
        self._pace()

    def _stop_effects(self) -> None:
        if self._effects:
            self._effects = []
            self.update()
        self._dirty = QRegion()
        self._pace()

    def _pace(self) -> None:
        """Frame rate of the busiest running effect, else the cheap idle rate."""
        interval = min((1000 // fx.fps for fx in self._effects), default=1000 // FPS)
        if self.timer.interval() != interval:
            self.timer.setInterval(interval)

    def _schedule(self) -> None:
        """Shooting-star slots, and with fun extras the rare visitors and seasonal showers."""
        w, h = self.width(), self.height()
        party = self._find("party") is not None
        flying = sum(isinstance(fx, _Flyer) for fx in self._effects)
        if self._t >= self._next_shot and flying < (3 if party else 1):
            kind = "star"
            roll = self._rnd.random()
            if self.fun and not party:
                if roll < 1 / 25:
                    kind = self._rnd.choice(("ufo", "comet"))
                elif self.season == "halloween" and roll < 0.4:
                    kind = "bat"
            self._effects.append(_Flyer(kind, self._rnd, w, h, self._rnd.randrange(360) if party else None))
            self._next_shot = self._t + (self._rnd.uniform(0.5, 1.6) if party else self._rnd.uniform(15, 40))
        if not self.fun:
            return
        if self._t >= self._day_check:
            self._day_check = self._t + 60
            self._new_day(datetime.date.today())
        if self.season in ("winter", "newyear") and self._t >= self._next_event:
            if self.season == "winter":  # a light shower now and then, never constant
                secs = self._rnd.uniform(60, 120)
                if not self._find("snow"):
                    self._start(_Snow(self._rnd, secs, self, light=True))
                self._next_event = self._t + secs + self._rnd.uniform(8 * 60, 12 * 60)
            else:
                self.fireworks(self._rnd.uniform(5, 8))
                self._next_event = self._t + self._rnd.uniform(3 * 60, 6 * 60)

    # ---------------------------------------------------------- animation
    def _tick(self) -> None:
        if not self._live():
            if self._effects:
                self._stop_effects()
            return
        dt = self.timer.interval() / 1000
        self._frame += 1
        self._t += dt
        region = QRegion()
        for x, y, r, _phase, _c in self._twinkle:
            s = int(r * 2 + 4)
            region += QRect(int(x) - s, int(y) - s, s * 2, s * 2)
        self._schedule()
        full = False
        for fx in list(self._effects):
            alive = fx.step(dt, self)
            full = full or fx.full
            region += fx.region()
            if not alive:
                self._effects.remove(fx)
        self._pace()
        if full:
            self.update()
        else:
            self.update(region.united(self._dirty))
        self._dirty = region
