"""Background service that keeps Super Nova itself up to date from GitHub."""
from __future__ import annotations

import logging
import os
import time

from PySide6.QtCore import QObject, QTimer, Signal

from nebula.core import tasks
from nebula.updater import DEFAULT_BRANCH, DEFAULT_REPO, UpdateInfo, Updater, find_token

log = logging.getLogger(__name__)

IDLE, CHECKING, UP_TO_DATE, AVAILABLE, WAITING, INSTALLING, INSTALLED, ERROR = (
    "idle", "checking", "up_to_date", "available", "waiting", "installing", "installed", "error")
RESTART_DELAY = 20  # seconds shown to the user before an automatic restart


class SelfUpdateService(QObject):
    changed = Signal()

    def __init__(self, services, restart_cb=None):
        super().__init__()
        self.s = services
        self.restart_cb = restart_cb
        self.status = IDLE
        self.message = ""
        self.info: UpdateInfo | None = None
        self.restart_at = 0.0
        self._updater: Updater | None = None
        self._busy = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.check())
        self.wait_timer = QTimer(self)
        self.wait_timer.setInterval(60_000)
        self.wait_timer.timeout.connect(self._retry_waiting)
        self.restart_timer = QTimer(self)
        self.restart_timer.setInterval(1000)
        self.restart_timer.timeout.connect(self._tick_restart)
        services.settings.on_change(self._setting_changed)

    # ----------------------------------------------------------- config
    @property
    def settings(self):
        return self.s.settings

    def updater(self) -> Updater:
        repo = self.settings.get("updates.repo") or DEFAULT_REPO
        branch = self.settings.get("updates.branch") or DEFAULT_BRANCH
        if self._updater is None or self._updater.repo != repo or self._updater.branch != branch:
            self._updater = Updater(repo, branch, token=find_token(self.settings.get("updates.github_token") or ""))
        return self._updater

    def enabled(self) -> bool:
        return not (os.environ.get("SUPERNOVA_NO_UPDATE") or os.environ.get("NEBULA_NO_UPDATE"))

    def _setting_changed(self, key: str, _value) -> None:
        if key == "updates.interval_minutes" and self.timer.isActive():
            self._arm_timer()
        elif key in ("updates.repo", "updates.branch", "updates.github_token"):
            self._updater = None

    def _arm_timer(self) -> None:
        minutes = max(5, int(self.settings.get("updates.interval_minutes") or 30))
        self.timer.start(minutes * 60_000)

    def start(self) -> None:
        if not self.enabled():
            return
        if self.settings.get("updates.auto_check"):
            QTimer.singleShot(20_000, self.check)
            self._arm_timer()

    def _set(self, status: str, message: str = "") -> None:
        self.status = status
        self.message = message
        self.changed.emit()

    def mode(self) -> str:
        try:
            return self.updater().mode
        except Exception:  # noqa: BLE001
            return "manual"

    def installed_sha(self) -> str:
        try:
            return self.updater().installed_sha
        except Exception:  # noqa: BLE001
            return ""

    def state(self) -> dict:
        try:
            return dict(self.updater().state)
        except Exception:  # noqa: BLE001
            return {}

    # ------------------------------------------------------------ check
    def check(self, manual: bool = False) -> None:
        if self._busy or self.status in (INSTALLING, INSTALLED, WAITING):
            return
        if not manual and not self.settings.get("updates.auto_check"):
            return
        self._busy = True
        self._set(CHECKING, "Checking GitHub for updates…")
        upd = self.updater()
        tasks.run_async(upd.check, on_done=lambda info: self._checked(info, manual),
                        on_error=lambda exc: self._failed(exc, manual))

    def _checked(self, info: UpdateInfo | None, manual: bool) -> None:
        self._busy = False
        if info is None:
            self.info = None
            self._set(UP_TO_DATE, "Super Nova is up to date")
            if manual:
                self.s.bus.notify.emit("Super Nova", "You have the latest version")
            return
        self.info = info
        summary = info.summary()
        if self.mode() == "manual":
            self._set(AVAILABLE, f"An update is available ({summary}). This installation is read-only — "
                                 "reinstall Super Nova to update.")
            return
        if self.settings.get("updates.auto_install") or manual:
            self._set(AVAILABLE, f"Update found: {summary}")
            self.install()
        else:
            self._set(AVAILABLE, f"An update is available: {summary}")
            self.s.bus.notify.emit("Super Nova update available", summary)

    def _failed(self, exc: BaseException, manual: bool) -> None:
        self._busy = False
        log.info("Update check failed: %s", exc)
        self._set(ERROR, f"Update check failed: {exc}")
        if manual:
            self.s.bus.error.emit("Super Nova update", str(exc))

    # ---------------------------------------------------------- install
    def busy_with_games(self) -> bool:
        return bool(self.s.launcher.running) or bool(self.s.jobs.active_jobs())

    def install(self, force: bool = False) -> None:
        if self.info is None or self._busy:
            return
        if not force and self.busy_with_games():
            self._set(WAITING, "An update is ready and will be installed when your game and downloads finish")
            self.wait_timer.start()
            return
        self.wait_timer.stop()
        self._busy = True
        info = self.info
        self._set(INSTALLING, f"Installing update ({info.summary()})…")
        upd = self.updater()
        tasks.run_async(upd.apply, info, on_done=self._installed, on_error=self._install_failed)

    def _retry_waiting(self) -> None:
        if self.status == WAITING and not self.busy_with_games():
            self.status = AVAILABLE
            self.install()

    def _installed(self, result: str) -> None:
        self._busy = False
        self._set(INSTALLED, f"{result}. Restart Super Nova to use the new version.")
        self.s.db.log_activity("update", f"Super Nova updated ({result})")
        if self.settings.get("updates.auto_restart"):
            self.schedule_restart()
        else:
            self.s.bus.notify.emit("Super Nova updated", "Restart Super Nova to use the new version")

    def _install_failed(self, exc: BaseException) -> None:
        self._busy = False
        log.warning("Update install failed: %s", exc)
        self._set(ERROR, f"Update failed: {exc}")
        self.s.bus.error.emit("Super Nova update failed", str(exc))

    # ---------------------------------------------------------- restart
    def schedule_restart(self, delay: int = RESTART_DELAY) -> None:
        self.restart_at = time.time() + delay
        self.restart_timer.start()
        self.changed.emit()

    def cancel_restart(self) -> None:
        self.restart_timer.stop()
        self.restart_at = 0.0
        self.changed.emit()

    def seconds_to_restart(self) -> int:
        return max(0, int(round(self.restart_at - time.time()))) if self.restart_at else -1

    def _tick_restart(self) -> None:
        if self.busy_with_games():
            # never restart under a running game; try again once it is closed
            self.restart_at = time.time() + RESTART_DELAY
        elif time.time() >= self.restart_at:
            self.restart_timer.stop()
            self.restart_now()
        self.changed.emit()

    def restart_now(self) -> None:
        self.restart_timer.stop()
        if self.restart_cb:
            self.restart_cb()
