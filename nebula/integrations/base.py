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


# update(game, ctx, version=...) understands three kinds of ``version``:
#   ""                 newest build on the channel the copy is installed from
#   "<build id>"       exactly that build (the copy adopts that build's channel)
#   branch_target(b)   newest build of channel ``b`` ("" / None = the default channel), resolved when the job runs
BRANCH_TARGET = "branch:"


def branch_target(branch: str | None) -> str:
    """``update(version=...)`` value meaning "newest build of this channel, resolved when the job runs"."""
    return BRANCH_TARGET + (branch or "")


@dataclass
class BuildRef:
    """One installable version of a game (a GOG Galaxy build, or GOG's current offline installer)."""

    id: str = ""  # platform build id; "" for offline installers
    version: str = ""  # version name / installer version; "" = unknown
    branch: str = ""  # "" = default channel (Stable)
    date: str = ""  # YYYY-MM-DD release date of this build
    published: str = ""  # full publish timestamp, only used for ordering
    size: int = 0  # installer download size in bytes (0 = unknown)
    newest: bool = False  # newest build on its own channel
    current: bool = False  # the installed build

    @property
    def label(self) -> str:
        return self.version or (f"Build {self.id}" if self.id else "Unknown version")


@dataclass
class VersionInfo:
    """Everything the UI shows about the installed version of a game and the versions it can change to."""

    kind: str = "none"  # "builds" (GOG Galaxy builds) | "installer" (offline installer) | "none"
    method: str = ""  # human readable "Installed with"
    origin: str = "nebula"  # nebula | heroic | folder
    platform: str = ""
    installed: BuildRef = field(default_factory=BuildRef)
    installed_listed: bool = False  # builds kind: the installed build is still in the platform's list
    latest: BuildRef | None = None  # newest build on the tracked channel / the current installer
    branch: str = ""  # tracked channel ("" = Stable)
    branch_gone: bool = False  # the tracked channel no longer has any builds
    branches: list = field(default_factory=list)  # list[str]: "" first (if it has builds), then others
    builds: list = field(default_factory=list)  # list[BuildRef], every channel, newest first
    status: str = "unknown"  # up_to_date | behind | channel_ended | unlisted | different | unknown
    newer_count: int = 0  # builds on the tracked channel newer than the installed one
    update_available: bool = False  # ignores the pin; the stored Game flag is this and not pinned
    can_choose: bool = False  # other versions / channels can be picked
    can_update: bool = False
    pinned: bool = False  # per-game "stay on this version"
    auto_update: bool = True  # per-game automatic updates
    note: str = ""  # limitation text, shown verbatim
    error: str = ""  # "" | "signed_out" | "offline" | "failed"
    error_text: str = ""
    stale: bool = False  # data came from the saved summary / an expired cache
    checked_at: float = 0.0  # wall time of the data behind ``latest``
    updated_at: float = 0.0  # last install / update of this copy
    has_password: bool = False  # a beta channel password is stored (reserved)


@dataclass
class InstallOptions:
    platforms: list = field(default_factory=list)  # e.g. ["linux", "windows"]
    platform: str = ""
    languages: list = field(default_factory=list)  # list of (code, label)
    language: str = ""
    dlcs: list = field(default_factory=list)  # list of {"id", "title", "size"}
    versions: list = field(default_factory=list)  # list of {"id", "label", "date", "branch"}
    version: str = ""
    latest: str = ""  # id of the newest default-channel version ("" = unknown / not applicable)
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
        """Update or change the installed version. ``version`` is "" (newest on the installed channel), a build
        id, or ``branch_target(channel)``. Integrations that cannot honour a version raise NotSupported."""
        raise NotSupported()

    def repair(self, game: Game, ctx: "JobContext") -> Game:
        raise NotSupported()

    def versions(self, game: Game) -> list[dict]:
        return []

    def version_info(self, game: Game, fetch: bool = True, refresh: bool = False) -> VersionInfo | None:
        """Installed / newest version and the versions this copy can change to.

        None means this integration (or this copy) has no version management. With ``fetch=True`` this may
        block on the network, so call it from a worker thread; ``fetch=False`` must never touch the network
        (it is called on the GUI thread). Network and API errors are reported in ``.error``, never raised."""
        return None

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
