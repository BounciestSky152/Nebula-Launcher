"""Built-in line icons (24x24, stroke based) rendered from inline SVG."""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PySide6.QtSvg import QSvgRenderer

from nebula.ui import theme

ICONS: dict[str, str] = {
    "home": '<path d="M4 11.5l8-6.5 8 6.5"/><path d="M6 10.5V20h12v-9.5"/><path d="M12 12.6l.9 2 2 .9-2 .9-.9 2-.9-2-2-.9 2-.9z"/>',
    "recent": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "grid": '<rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/>'
            '<rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/>'
            '<circle cx="4.5" cy="18" r="1"/>',
    "library": '<rect x="4" y="5.5" width="11" height="14.5" rx="1.5"/><path d="M8 3h10.5A1.5 1.5 0 0 1 20 4.5V17"/><circle cx="9.5" cy="12.7" r="2.4"/><path d="M5.6 15.3c2.6-1.3 5.2-3.2 7.7-5.6"/>',
    "installed": '<circle cx="12" cy="12" r="8.5"/><path d="M8 12.3l2.7 2.7 5.5-5.5"/><circle cx="19" cy="5" r="1.2"/>',
    "download": '<path d="M12 3.5v10"/><path d="M8 10l4 4 4-4"/><ellipse cx="12" cy="18.6" rx="7.5" ry="2.4"/>',
    "store": '<path d="M5.5 8.5h13l-1.2 11.5H6.7z"/><path d="M9 8.5V7a3 3 0 0 1 6 0v1.5"/><path d="M2.5 14.5c4.5 2.2 14.5 2.2 19 0"/>',
    "heart": '<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/>'
             '<path d="M18 14a6 6 0 0 1 3.5 6"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "chart": '<path d="M5 20v-6M10 20v-9M15 20v-5M3 20h18"/><path d="M18.5 3.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "play": '<path d="M7 4.5v15l12-7.5z" fill="currentColor"/>',
    "pause": '<path d="M8 5v14M16 5v14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5" fill="currentColor"/>',
    "more": '<circle cx="5" cy="12" r="1.3" fill="currentColor"/><circle cx="12" cy="12" r="1.3" fill="currentColor"/>'
            '<circle cx="19" cy="12" r="1.3" fill="currentColor"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8"/><path d="M4 3v5h5"/><path d="M4 13a8 8 0 0 0 14.5 4.5L20 16"/><path d="M20 21v-5h-5"/><circle cx="12" cy="12" r="1.8"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "cloud": '<path d="M7 18a4.5 4.5 0 0 1-.5-9A6 6 0 0 1 18 9.5a4 4 0 0 1-.5 8.5z"/>',
    "star": '<path d="M11 3l1.8 5.2L18 10l-5.2 1.8L11 17l-1.8-5.2L4 10l5.2-1.8z"/><path d="M18.5 15.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7z"/>',
    "star-filled": '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8l-5.2 2.8 1-5.8-4.3-4.1 5.9-.8z" '
                   'fill="currentColor"/>',
    "back": '<path d="M15 5l-7 7 7 7"/>',
    "forward": '<path d="M9 5l7 7-7 7"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-up": '<path d="M6 15l6-6 6 6"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "wrench": '<path d="M14.5 6.5a4 4 0 0 0 5 5L12 19a2.1 2.1 0 0 1-3-3l7.5-7.5"/><path d="M14.5 6.5L17 4l3 3-2.5 2.5"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "tag": '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
    "eye-off": '<path d="M3 3l18 18"/><path d="M10.5 6.2A9.6 9.6 0 0 1 12 6c5 0 9 6 9 6a15 15 0 0 1-2.6 3.2"/>'
               '<path d="M6.3 7.6C4.2 9.2 3 12 3 12s4 6 9 6a8.5 8.5 0 0 0 3.6-.8"/>',
    "eye": '<path d="M3 12s4-6 9-6 9 6 9 6-4 6-9 6-9-6-9-6z"/><circle cx="12" cy="12" r="2.5"/>',
    "filter": '<path d="M4 5h16l-6 7.5V19l-4 2v-8.5z"/>',
    "sort": '<path d="M7 4v16M3 16l4 4 4-4M17 20V4M13 8l4-4 4 4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "trophy": '<path d="M8 4h8v5a4 4 0 0 1-8 0z"/><path d="M8 6H4.5a3.5 3.5 0 0 0 3.8 4M16 6h3.5a3.5 3.5 0 0 1-3.8 4"/>'
              '<path d="M12 13v4M8 20h8M9.5 17h5"/>',
    "gamepad": '<path d="M6.5 8h11a4 4 0 0 1 4 4.5l-.6 4a2.5 2.5 0 0 1-4.4 1.2L15 16H9l-1.5 1.7a2.5 2.5 0 0 1-4.4-1.2l-.6-4'
               'A4 4 0 0 1 6.5 8z"/><path d="M8 10.5v3M6.5 12h3"/><circle cx="16" cy="11" r=".8" fill="currentColor"/>'
               '<circle cx="17.5" cy="13" r=".8" fill="currentColor"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><circle cx="12" cy="7.5" r=".8" fill="currentColor"/>',
    "link": '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11.5 6.8"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 '
            '0 0 5.7 5.7l1.5-1.5"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "shield": '<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6z"/><path d="M8.5 12l2.5 2.5 4.5-4.5"/>',
    "history": '<path d="M3.5 12a8.5 8.5 0 1 0 2.5-6"/><path d="M3 4v4h4"/><path d="M12 8v4l3 2"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12 15h5"/>',
    "wine": '<path d="M8 3h8l-.5 6a3.5 3.5 0 0 1-7 0z"/><path d="M12 12.5V20M8 21h8"/>',
    "power": '<path d="M12 3v8"/><path d="M6.3 6.6a8 8 0 1 0 11.4 0"/>',
    "image": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="10" r="1.8"/><path d="M21 16l-5-5-8 8"/>',
    "shortcut": '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M10 14l4-4M10 10h4v4"/>',
    "logout": '<path d="M15 4h4v16h-4"/><path d="M10 8l-4 4 4 4M6 12h10"/>',
    "bell": '<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z"/><path d="M10 20a2 2 0 0 0 4 0"/>',
    "puzzle": '<path d="M5 8h3a2 2 0 1 1 4 0h3v3a2 2 0 1 1 0 4v3H5v-3a2 2 0 1 0 0-4z"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "monitor": '<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>',
    "linux": '<path d="M12 3c-2 0-3 1.8-3 4v3c0 1.5-2.5 4-2.5 7 0 2 1.5 3.5 5.5 3.5s5.5-1.5 5.5-3.5c0-3-2.5-5.5-2.5-7V7'
             'c0-2.2-1-4-3-4z"/><circle cx="10.6" cy="7.5" r=".7" fill="currentColor"/><circle cx="13.4" cy="7.5" r=".7" '
             'fill="currentColor"/>',
    "windows": '<path d="M4 6l7-1v6.5H4zM13 4.7L20 4v7.5h-7zM4 12.5h7V19l-7-1zM13 12.5h7V20l-7-.8z" fill="currentColor"/>',
    "apple": '<path d="M16.5 12.6c0-2 1.6-3 1.7-3-1-1.4-2.4-1.6-2.9-1.6-1.2-.1-2.4.7-3 .7s-1.6-.7-2.6-.7A4 4 0 0 0 6.3 '
             '10c-1.4 2.5-.4 6.2 1 8.2.7 1 1.5 2 2.5 2s1.4-.6 2.5-.6 1.5.6 2.6.6 1.7-1 2.4-2a8 8 0 0 0 1-2.2'
             'c-.1 0-1.8-.7-1.8-3.4zM14.7 6.4c.5-.7.9-1.6.8-2.4-.8 0-1.7.5-2.3 1.2-.5.6-.9 1.5-.8 2.4.9 0 1.8-.5 2.3-1.2z" '
             'fill="currentColor"/>',
    "proton": '<circle cx="12" cy="12" r="2.2" fill="currentColor"/><ellipse cx="12" cy="12" rx="9" ry="3.6"/>'
              '<ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(60 12 12)"/>'
              '<ellipse cx="12" cy="12" rx="9" ry="3.6" transform="rotate(-60 12 12)"/>',
    "nebula": '<circle cx="12" cy="12" r="3.2" fill="currentColor"/><path d="M3 15c3-6 15-12 18-6"/>'
              '<path d="M21 9c-3 6-15 12-18 6"/>',
}


def _svg(name: str, color: str, stroke: float = 1.8, opacity: float = 1.0) -> bytes:
    body = ICONS.get(name, ICONS["info"]).replace("currentColor", color)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-opacity="{opacity}" stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">'
            f'{body}</svg>').encode()


@lru_cache(maxsize=1024)
def pixmap(name: str, size: int = 18, color: str = theme.TEXT_DIM, ratio: float = 2.0) -> QPixmap:
    px = int(size * ratio)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    # a faint wide stroke underneath gives the line icons a starlight glow
    QSvgRenderer(QByteArray(_svg(name, color, 4.2, 0.18))).render(p, QRectF(0, 0, px, px))
    QSvgRenderer(QByteArray(_svg(name, color))).render(p, QRectF(0, 0, px, px))
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def icon(name: str, color: str = theme.TEXT_DIM, active: str | None = None, size: int = 18) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, size, color), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, size, active or theme.TEXT), QIcon.Mode.Active, QIcon.State.Off)
    ic.addPixmap(pixmap(name, size, active or theme.TEXT), QIcon.Mode.Normal, QIcon.State.On)
    ic.addPixmap(pixmap(name, size, theme.TEXT_FAINT), QIcon.Mode.Disabled, QIcon.State.Off)
    return ic


@lru_cache(maxsize=64)
def badge(letter: str, color: str, size: int = 16, ratio: float = 2.0) -> QPixmap:
    """Small rounded platform badge with a letter (no third-party logos)."""
    px = int(size * ratio)
    pm = QPixmap(px, px)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, px, px), px * 0.28, px * 0.28)
    p.fillPath(path, QColor(color))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(int(px * 0.62))
    p.setFont(f)
    c = QColor(color)
    p.setPen(QColor("#111111") if c.lightness() > 150 else QColor("white"))
    p.drawText(QRectF(0, 0, px, px), Qt.AlignmentFlag.AlignCenter, letter)
    p.end()
    pm.setDevicePixelRatio(ratio)
    return pm


def app_icon() -> QIcon:
    ic = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
        p.fillPath(path, QColor(theme.ACCENT))
        renderer = QSvgRenderer(QByteArray(_svg("nebula", "#ffffff", 1.6)))
        m = size * 0.16
        renderer.render(p, QRectF(m, m, size - 2 * m, size - 2 * m))
        p.end()
        ic.addPixmap(pm)
    return ic


def qsize(n: int) -> QSize:
    return QSize(n, n)
