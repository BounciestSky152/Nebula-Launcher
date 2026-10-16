"""Application-wide signal bus."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EventBus(QObject):
    library_changed = Signal()  # games added/removed or bulk refresh
    game_changed = Signal(str)  # library entry key changed (install state, user data...)
    sync_state = Signal(str, bool)  # integration id, syncing?
    integration_changed = Signal(str)  # connected / disconnected
    jobs_changed = Signal()
    job_progress = Signal(str)  # job id
    game_started = Signal(str)  # uid
    game_stopped = Signal(str, int)  # uid, seconds played
    game_preparing = Signal(str, str)  # uid, status text ('' when done)
    friends_changed = Signal()
    achievements_changed = Signal(str)  # uid
    runners_changed = Signal()
    notify = Signal(str, str)  # title, body
    error = Signal(str, str)  # title, body
    navigate = Signal(str, object)  # page name, argument


bus: EventBus | None = None


def get_bus() -> EventBus:
    global bus
    if bus is None:
        bus = EventBus()
    return bus
