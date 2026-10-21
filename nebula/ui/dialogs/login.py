"""GOG sign-in dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLineEdit, QStackedWidget, QVBoxLayout, QWidget

from nebula.core import tasks
from nebula.core.utils import xdg_open
from nebula.ui import theme
from nebula.ui.widgets.common import Spinner, button, label

try:  # QtWebEngine is optional (PySide6-Addons)
    from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore

    HAVE_WEBENGINE = True
except Exception:  # noqa: BLE001
    HAVE_WEBENGINE = False


class GogLoginDialog(QDialog):
    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.s = services
        self.gog = services.integrations["gog"]
        self.setWindowTitle("Sign in to GOG")
        self.resize(520 if not HAVE_WEBENGINE else 480, 300 if not HAVE_WEBENGINE else 680)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(12)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.status = label("", "Subtle", wrap=True)
        self.spinner = Spinner(18)
        self.spinner.hide()
        row = QHBoxLayout()
        row.addWidget(self.spinner)
        row.addWidget(self.status, 1)
        lay.addLayout(row)

        if HAVE_WEBENGINE:
            self.web = QWebEngineView()
            self.web.urlChanged.connect(self._url_changed)
            self.web.load(QUrl(self.gog.login_url))
            self.stack.addWidget(self.web)
        else:
            page = QWidget()
            pl = QVBoxLayout(page)
            pl.setContentsMargins(0, 0, 0, 0)
            pl.setSpacing(10)
            pl.addWidget(label("Sign in to GOG", "SectionTitle"))
            pl.addWidget(label(
                "1. Click <b>Open GOG login</b> and sign in with your browser.<br>"
                "2. After signing in you land on a mostly blank page whose address starts with "
                "<code>https://embed.gog.com/on_login_success</code>.<br>"
                "3. Copy that full address from the address bar and paste it below.", wrap=True))
            open_btn = button("Open GOG login", "external")
            open_btn.clicked.connect(lambda: xdg_open(self.gog.login_url))
            pl.addWidget(open_btn, 0, Qt.AlignmentFlag.AlignLeft)
            self.code = QLineEdit()
            self.code.setPlaceholderText("https://embed.gog.com/on_login_success?origin=client&code=…")
            pl.addWidget(self.code)
            go = button("Sign in", primary=True)
            go.clicked.connect(lambda: self._submit(self.code.text()))
            self.code.returnPressed.connect(lambda: self._submit(self.code.text()))
            pl.addWidget(go, 0, Qt.AlignmentFlag.AlignRight)
            pl.addWidget(label("Tip: install PySide6-Addons to sign in inside Super Nova.", "Faint"))
            pl.addStretch(1)
            self.stack.addWidget(page)

    def _url_changed(self, url: QUrl) -> None:
        text = url.toString()
        if text.startswith(self.gog.success_prefix) and "code=" in text:
            self.web.hide()
            self._submit(text)

    def _submit(self, text: str) -> None:
        if not text.strip():
            return
        self.spinner.start()
        self.status.setText("Signing in…")
        tasks.run_async(self.gog.connect, code=text, on_done=self._ok, on_error=self._err)

    def _ok(self, _r) -> None:
        self.spinner.stop()
        self.s.bus.integration_changed.emit("gog")
        self.s.library.refresh(["gog"])
        self.accept()

    def _err(self, exc) -> None:
        self.spinner.stop()
        self.status.setText(f"<span style='color:{theme.DANGER}'>Sign-in failed: {exc}</span>")
        if HAVE_WEBENGINE:
            self.web.show()
            self.web.load(QUrl(self.gog.login_url))
