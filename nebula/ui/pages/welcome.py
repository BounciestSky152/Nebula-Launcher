"""First-run screen: sign in with GOG (like GOG Galaxy's start-up login)."""
from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QFontMetricsF, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QVBoxLayout

from nebula import APP_NAME
from nebula.ui import icons, theme
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import button, card, label


class NovaTitle(QLabel):
    """A centred one-line title that Super Nova lights up in violet, magenta and flame (deep enough to read)."""

    def paintEvent(self, e):  # noqa: N802
        if not theme.light():
            return super().paintEvent(e)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fm = QFontMetricsF(self.font())
        w = fm.horizontalAdvance(self.text()) - self.font().letterSpacing()  # no tracking after the last letter
        x, y = (self.width() - w) / 2, (self.height() + fm.ascent() - fm.descent()) / 2
        path = QPainterPath()
        path.addText(QPointF(x, y), self.font(), self.text())
        p.fillPath(path, theme.gradient(x, 0, x + w, 0, ("#7a2cf0", "#d6157f", "#d9480f")))


class WelcomePage(Page):
    name = "Welcome"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        outer = QVBoxLayout(self)
        outer.addStretch(1)
        box = card()
        box.setFixedWidth(520)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(44, 40, 44, 36)
        lay.setSpacing(14)
        logo = QLabel()
        logo.setPixmap(icons.app_icon().pixmap(84, 84))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo.setFixedHeight(88)
        lay.addWidget(logo)
        title = NovaTitle(APP_NAME.upper())
        title.setObjectName("Brand")
        title.setStyleSheet("font-size: 26pt; font-weight: 800; letter-spacing: 8px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)
        sub = label("YOUR GOG LIBRARY, ON LINUX", "BrandSub")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(sub)
        lay.addSpacing(8)
        text = label("Sign in to see your GOG library, achievements and friends.", "Subtle", wrap=True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(text)
        inner = 520 - 88
        lay.addSpacing(10)
        sign = button("Sign in with GOG", primary=True)
        sign.setMinimumHeight(44)
        sign.setStyleSheet("font-size: 12pt;")
        sign.clicked.connect(self._sign_in)
        lay.addWidget(sign)
        skip = button("Continue without signing in", obj="Link")
        skip.clicked.connect(self._skip)
        lay.addWidget(skip, 0, Qt.AlignmentFlag.AlignCenter)
        note = label("Steam, Epic Games and Lutris can be added later in Settings.", "Faint", wrap=True)
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(note)
        # wrapped labels in a centred, fixed-width card need their height spelled out
        for w in (text, note):
            w.ensurePolished()
            w.setMinimumHeight(w.heightForWidth(inner) + 4)
        outer.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch(2)

    def _sign_in(self) -> None:
        if self.ctl.gog_login():
            self._skip()

    def _skip(self) -> None:
        self.s.db.kv_set("ui.welcome_dismissed", True)
        self.win.navigate("home")
