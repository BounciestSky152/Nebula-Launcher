"""Application entry point."""
from __future__ import annotations

import argparse
import getpass
import logging
import logging.handlers
import os
import signal
import sys

from nebula import APP_ID, APP_NAME, __version__, paths

SOCKET_NAME = f"nebula-{getpass.getuser()}"


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    fh = logging.handlers.RotatingFileHandler(paths.logs_dir() / "nebula.log", maxBytes=2_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.setLevel(level if debug else logging.WARNING)
    root.addHandler(sh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _parse(argv):
    p = argparse.ArgumentParser(prog="nebula", description=f"{APP_NAME} - a unified game launcher for Linux")
    p.add_argument("--launch", metavar="UID", help="launch a game by id, e.g. gog:1207658924 or steam:620")
    p.add_argument("--minimized", action="store_true", help="start hidden in the system tray")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p.parse_args(argv)


def _send_to_running(message: str) -> bool:
    from PySide6.QtNetwork import QLocalSocket

    sock = QLocalSocket()
    sock.connectToServer(SOCKET_NAME)
    if not sock.waitForConnected(400):
        return False
    sock.write(message.encode())
    sock.flush()
    sock.waitForBytesWritten(400)
    sock.disconnectFromServer()
    return True


def main(argv=None) -> int:
    args = _parse(argv if argv is not None else sys.argv[1:])
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    from PySide6.QtCore import QCoreApplication, Qt, QTimer
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    QCoreApplication.setApplicationName("nebula")
    QCoreApplication.setOrganizationName("nebula")
    QGuiApplication.setDesktopFileName(APP_ID)
    app = QApplication(sys.argv[:1])
    app.setApplicationDisplayName(APP_NAME)
    app.setQuitOnLastWindowClosed(False)

    if _send_to_running(f"launch {args.launch}" if args.launch else "show"):
        return 0

    _setup_logging(args.debug)
    from PySide6.QtNetwork import QLocalServer

    from nebula.core import tasks
    from nebula.services import Services
    from nebula.ui import icons, theme
    from nebula.ui.main_window import MainWindow

    tasks.init_main_thread()
    theme.apply(app)
    app.setWindowIcon(icons.app_icon())
    services = Services()
    window = MainWindow(services)

    server = QLocalServer()
    QLocalServer.removeServer(SOCKET_NAME)
    server.listen(SOCKET_NAME)

    def on_connection():
        conn = server.nextPendingConnection()
        if conn is None:
            return

        def read():
            msg = bytes(conn.readAll()).decode(errors="ignore").strip()
            if msg.startswith("launch "):
                services.launcher.launch(msg.split(" ", 1)[1])
            else:
                window.show_window()

        conn.readyRead.connect(read)

    server.newConnection.connect(on_connection)

    services.start()
    window.navigate("home")
    if args.minimized or services.settings.get("general.start_minimized"):
        if window.tray is None or not window.tray.isVisible():
            window.showMinimized()
    else:
        window.show()
    if args.launch:
        QTimer.singleShot(1500, lambda: services.launcher.launch(args.launch))

    signal.signal(signal.SIGINT, lambda *_: window.quit_app())
    tick = QTimer()
    tick.start(500)
    tick.timeout.connect(lambda: None)  # let Python handle Ctrl+C

    code = app.exec()
    services.shutdown()
    server.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
