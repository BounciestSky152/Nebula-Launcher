"""Integration interface, modelled after GOG Galaxy 2.0's integration API.

Each platform (GOG, Steam, Epic, Lutris, custom games, user plugins) implements
this class. Every method may block - the services layer always calls them from
worker threads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from nebula.core.models import Achievement, Friend, Game, GameDetails, LaunchTask

if TYPE_CHECKING:  # pragma: no cover
    from nebula.core.jobs import JobContext
    from nebula.runners.compat import LaunchSpec
    from nebula.services import Services

# capability flags
LIBRARY = "library"
INSTALL = "install"
UNINSTALL = "uninstall"
LAUNCH = "launch"
UPDATE = "update"
REPAIR = "repair"
VERSIONS = "versions"
DLC = "dlc"
ACHIEVEMENTS = "achievements"
FRIENDS = "friends"
PLAYTIME = "playtime"
CLOUD_SAVES = "cloud_saves"
STORE = "store"
IMPORT = "import"
WISHLIST = "wishlist"


class NotSupported(Exception):
    pass


@dataclass
class InstallOptions:
    platforms: list = field(default_factory=list)  # e.g. ["linux", "windows"]
    platform: str = ""
    languages: list = field(default_factory=list)  # list of (code, label)
    language: str = ""
    dlcs: list = field(default_factory=list)  # list of {"id", "title", "size"}
    versions: list = field(default_factory=list)  # list of {"id", "label", "date"}
    version: str = ""
    download_size: int = 0
    disk_size: int = 0
    install_dir: str = ""
    notes: list = field(default_factory=list)
    method: str = ""  # human readable install method


@dataclass
class InstallRequest:
    platform: str = ""
    language: str = ""
    dlcs: list = field(default_factory=list)
    version: str = ""
    base_dir: str = ""
    shortcut: bool = False


@dataclass
class CloudSyncResult:
    action: str = "none"  # none | upload | download | conflict | unsupported | error
    message: str = ""
    files: int = 0
    details: dict = field(default_factory=dict)


class Integration:
    id = ""
    name = ""
    color = "#888888"
    letter = "?"
    description = ""
    capabilities: frozenset = frozenset()

    def __init__(self, services: "Services"):
        self.services = services

    # ------------------------------------------------------------ state
    @property
    def settings(self):
        return self.services.settings

    @property
    def db(self):
        return self.services.db

    def supports(self, cap: str) -> bool:
        return cap in self.capabilities

    def available(self) -> bool:
        """The platform exists on this machine / can be used."""
        return True

    def connected(self) -> bool:
        return self.available()

    def enabled(self) -> bool:
        return bool(self.settings.get("integrations.enabled", {}).get(self.id, True))

    def account_name(self) -> str:
        return ""

    def status_text(self) -> str:
        if not self.available():
            return "Not detected"
        return "Connected" if self.connected() else "Not connected"

    def connect(self, **kwargs: Any) -> None:
        raise NotSupported()

    def disconnect(self) -> None:
        raise NotSupported()

    # ---------------------------------------------------------- library
    def fetch_library(self) -> list[Game]:
        return []

    def enrich(self, games: list[Game]) -> list[Game]:
        """Fetch missing metadata (covers, genres...). Returns changed games."""
        return []

    def check_updates(self, games: list[Game]) -> list[str]:
        return []

    # ------------------------------------------------------------ games
    def launch_tasks(self, game: Game) -> list[LaunchTask]:
        return [LaunchTask(id="", name="Play", primary=True)]

    def prepare_launch(self, game: Game, task_id: str = "") -> "LaunchSpec":
        raise NotSupported()

    def process_hints(self, game: Game) -> dict:
        """Hints for the process watcher: {"paths": [...], "steam_appid": str|None}."""
        return {"paths": [game.install_path] if game.installed and game.install_path else []}

    def install_options(self, game: Game, platform: str = "") -> InstallOptions:
        raise NotSupported()

    def install(self, game: Game, request: InstallRequest, ctx: "JobContext") -> Game:
        raise NotSupported()

    def uninstall(self, game: Game, ctx: "JobContext") -> Game:
        raise NotSupported()

    def update(self, game: Game, ctx: "JobContext", version: str = "") -> Game:
        raise NotSupported()

    def repair(self, game: Game, ctx: "JobContext") -> Game:
        raise NotSupported()

    def versions(self, game: Game) -> list[dict]:
        return []

    def import_path(self, path: str) -> list[Game]:
        return []

    def details(self, game: Game) -> GameDetails:
        return GameDetails(description=game.description, genres=game.genres, developer=game.developer,
                           publisher=game.publisher, release_date=game.release_date)

    def achievements(self, game: Game) -> list[Achievement] | None:
        return None

    def friends(self) -> list[Friend]:
        return []

    def playtime(self, game: Game) -> int | None:
        return None

    def store_url(self, game: Game) -> str:
        return game.store_url

    def open_platform_client(self) -> None:
        raise NotSupported()

    # --------------------------------------------------- session hooks
    def before_launch(self, game: Game, status) -> None:
        """Called in a worker thread before launching; ``status(text)`` updates the UI."""

    def on_session_start(self, game: Game) -> None:
        pass

    def on_session_end(self, game: Game, start: float, end: float) -> None:
        pass

    def cloud_sync(self, game: Game, mode: str = "auto", when: str = "manual") -> CloudSyncResult:
        return CloudSyncResult("unsupported", "Cloud saves are not supported for this platform")
