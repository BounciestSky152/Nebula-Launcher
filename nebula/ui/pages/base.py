from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QVBoxLayout, QWidget


class Page(QWidget):
    """Base class for main-window pages."""

    name = ""

    def __init__(self, services, ctl, window):
        super().__init__()
        self.setObjectName("Page")
        self.s = services
        self.ctl = ctl
        self.win = window
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def on_show(self, arg=None) -> None:  # called whenever navigated to
        pass

    def title(self) -> str:
        return self.name


def scroll_page(page: QWidget, margins=(32, 26, 32, 32), spacing: int = 18) -> QVBoxLayout:
    """Give ``page`` a vertical scroll area and return the content layout."""
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    content.setObjectName("ContentArea")
    area.setWidget(content)
    outer.addWidget(area)
    lay = QVBoxLayout(content)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    page.scroll_area = area
    return lay
