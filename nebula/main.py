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
    p.add_argument("--no-update", action="store_true", help="don't check GitHub for Nebula updates this session")
    p.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
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
    # needed by the optional embedded GOG login (QtWebEngine) and harmless otherwise
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
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

    from nebula.updater import preload_modules, restart_command

    tasks.init_main_thread()
    # load every module now: a self-update replaces files on disk and must never be half-loaded
    preload_modules()
    theme.apply(app)
    app.setWindowIcon(icons.app_icon())
    _install_icon()
    if args.no_update:
        os.environ["NEBULA_NO_UPDATE"] = "1"
    services = Services()
    server = QLocalServer()

    def restart():
        """Re-exec the (updated) app with the same arguments."""
        logging.getLogger(__name__).info("Restarting into the updated version")
        services.settings.set("window.geometry", bytes(window.saveGeometry().toBase64()).decode())
        services.shutdown()
        server.close()
        QLocalServer.removeServer(SOCKET_NAME)
        logging.shutdown()
        env = os.environ.copy()
        root = str(paths_root())
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        cmd = restart_command()
        os.execve(cmd[0], cmd, env)

    services.self_update.restart_cb = restart
    window = MainWindow(services)

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
    services.self_update.start()
    window.start_page()
    if args.after_update:
        last = services.self_update.state().get("last_update") or {}
        commits = [m for _sha, m in last.get("commits", [])][:3]
        QTimer.singleShot(1200, lambda: window.toast("Nebula was updated",
                                                     "\n".join(commits) or "You're on the latest version"))
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


def paths_root():
    """Folder that contains the ``nebula`` package (kept on PYTHONPATH across restarts)."""
    from pathlib import Path

    return Path(__file__).resolve().parent.parent


if __name__ == "__main__":
    raise SystemExit(main())


def _install_icon() -> None:
    """Keep the desktop/taskbar icon in step with the app (self-updates don't run the installer)."""
    import os
    from pathlib import Path

    src = Path(__file__).resolve().parent / "assets" / "nebula.svg"
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    dst = data / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
    try:
        if dst.parent.is_dir() and (not dst.exists() or dst.read_bytes() != src.read_bytes()):
            dst.write_bytes(src.read_bytes())
    except OSError:
        pass
