"""Slim banner under the top bar that reports Super Nova self-updates."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from nebula.core import self_update as su
from nebula.core.utils import xdg_open
from nebula.ui import icons, theme
from nebula.ui.widgets.common import Spinner, button, label


class UpdateBanner(QFrame):
    def __init__(self, services, window):
        super().__init__()
        self.s = services
        self.win = window
        self.setObjectName("UpdateBanner")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 7, 14, 7)
        lay.setSpacing(10)
        self.icon = QLabel()
        self.icon.setPixmap(icons.pixmap("download", 18, theme.ACCENT_HOVER))
        self.spinner = Spinner(16)
        self.spinner.hide()
        self.text = label("", wrap=True)
        lay.addWidget(self.icon)
        lay.addWidget(self.spinner)
        lay.addWidget(self.text, 1)
        self.primary = button("", primary=True)
        self.primary.clicked.connect(self._primary)
        self.secondary = button("")
        self.secondary.clicked.connect(self._secondary)
        lay.addWidget(self.primary)
        lay.addWidget(self.secondary)
        services.self_update.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        upd = self.s.self_update
        st = upd.status
        show = st in (su.AVAILABLE, su.WAITING, su.INSTALLING, su.INSTALLED)
        self.setVisible(show)
        if not show:
            return
        busy = st == su.INSTALLING
        self.spinner.setVisible(busy)
        if busy:
            self.spinner.start()
        else:
            self.spinner.stop()
        self.icon.setVisible(not busy)
        self.primary.show()
        self.secondary.show()
        if st == su.AVAILABLE:
            self.text.setText(f"<b>Super Nova update available</b> — {upd.message}")
            manual = upd.mode() == "manual"
            self.primary.setText("View on GitHub" if manual else "Update now")
            self.secondary.setText("Details")
        elif st == su.WAITING:
            self.text.setText(f"<b>Super Nova update ready</b> — {upd.message}")
            self.primary.setText("Update now")
            self.secondary.hide()
        elif st == su.INSTALLING:
            self.text.setText(f"<b>Updating Super Nova</b> — {upd.message}")
            self.primary.hide()
            self.secondary.hide()
        else:  # INSTALLED
            n = upd.seconds_to_restart()
            if n >= 0:
                self.text.setText(f"<b>Super Nova was updated.</b> Restarting in {n} s…")
                self.secondary.setText("Later")
            else:
                self.text.setText(f"<b>Super Nova was updated.</b> {upd.message}")
                self.secondary.hide()
            self.primary.setText("Restart now")

    def _primary(self) -> None:
        upd = self.s.self_update
        if upd.status == su.AVAILABLE and upd.mode() == "manual":
            xdg_open(f"https://github.com/{self.s.settings.get('updates.repo')}")
        elif upd.status in (su.AVAILABLE, su.WAITING):
            upd.install(force=True)
        elif upd.status == su.INSTALLED:
            upd.restart_now()

    def _secondary(self) -> None:
        upd = self.s.self_update
        if upd.status == su.INSTALLED:
            upd.cancel_restart()
        else:
            self.win.navigate("settings", "Updates")
