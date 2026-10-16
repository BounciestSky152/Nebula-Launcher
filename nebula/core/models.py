"""Plain data models shared between integrations, services and the UI."""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

SOURCES_ORDER = ["gog", "steam", "epic", "lutris", "local"]

_STRIP = re.compile(r"[™®©]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def title_key(title: str) -> str:
    """Normalised key used to merge the same game across platforms."""
    t = _STRIP.sub("", title or "")
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    t = t.replace("&", " and ")
    t = _NON_ALNUM.sub(" ", t).strip()
    return t.replace(" ", "") or (title or "").lower()


def sort_title(title: str) -> str:
    t = (title or "").lower().strip()
    for article in ("the ", "a ", "an "):
        if t.startswith(article):
            return t[len(article):]
    return t


@dataclass
class Game:
    source: str
    source_id: str
    title: str
    cover: str = ""  # vertical box art (URL or local path)
    hero: str = ""  # wide background
    horizontal: str = ""  # landscape capsule
    icon: str = ""
    logo: str = ""
    installed: bool = False
    install_path: str = ""
    install_size: int = 0
    download_size: int = 0
    version: str = ""
    build_id: str = ""
    platform: str = ""  # OS of the installed build: windows | linux | mac
    os_support: list = field(default_factory=list)
    genres: list = field(default_factory=list)
    developer: str = ""
    publisher: str = ""
    release_date: str = ""  # ISO yyyy-mm-dd
    description: str = ""
    critic_score: float = 0.0
    is_dlc: bool = False
    dlc_of: str = ""
    owned: bool = True
    update_available: bool = False
    platform_playtime: int = 0  # minutes reported by the platform itself
    platform_last_played: float = 0.0
    added_at: float = 0.0
    store_url: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def key(self) -> str:
        return self.extra.get("merge_key") or title_key(self.title)

    @property
    def needs_compat(self) -> bool:
        return self.installed and self.platform == "windows"

    def covers(self) -> list[str]:
        out = [self.cover] if self.cover else []
        out += [c for c in self.extra.get("cover_alts", []) if c]
        if self.horizontal:
            out.append(self.horizontal)
        return out

    def heroes(self) -> list[str]:
        out = [self.hero] if self.hero else []
        out += [c for c in self.extra.get("hero_alts", []) if c]
        if self.horizontal:
            out.append(self.horizontal)
        return out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Game":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class UserData:
    key: str
    tags: list = field(default_factory=list)
    rating: int = 0  # 0..5 stars
    hidden: bool = False
    favorite: bool = False
    notes: str = ""
    preferred_uid: str = ""
    custom_cover: str = ""
    custom_title: str = ""


@dataclass
class LibraryEntry:
    """A game as shown in the library: one title merged across platforms."""

    key: str
    games: list  # list[Game] ordered by preference
    user: UserData
    playtime_minutes: int = 0
    last_played: float = 0.0
    sessions: int = 0
    achievements: tuple = (0, 0)  # unlocked, total

    @property
    def primary(self) -> Game:
        if self.user.preferred_uid:
            for g in self.games:
                if g.uid == self.user.preferred_uid:
                    return g
        for g in self.games:
            if g.installed:
                return g
        return self.games[0]

    @property
    def title(self) -> str:
        return self.user.custom_title or self.primary.title

    @property
    def installed(self) -> bool:
        return any(g.installed for g in self.games)

    @property
    def sources(self) -> list[str]:
        seen = []
        for g in self.games:
            if g.source not in seen:
                seen.append(g.source)
        return seen

    @property
    def update_available(self) -> bool:
        return any(g.update_available and g.installed for g in self.games)

    @property
    def added_at(self) -> float:
        return min((g.added_at for g in self.games if g.added_at), default=0.0)

    @property
    def genres(self) -> list[str]:
        out: list[str] = []
        for g in self.games:
            for genre in g.genres:
                if genre not in out:
                    out.append(genre)
        return out

    @property
    def release_date(self) -> str:
        return next((g.release_date for g in self.games if g.release_date), "")

    @property
    def install_size(self) -> int:
        return sum(g.install_size for g in self.games if g.installed)

    def cover_urls(self) -> list[str]:
        out = [self.user.custom_cover] if self.user.custom_cover else []
        for g in [self.primary] + [g for g in self.games if g is not self.primary]:
            for c in g.covers():
                if c not in out:
                    out.append(c)
        return out

    def hero_urls(self) -> list[str]:
        out: list[str] = []
        for g in [self.primary] + [g for g in self.games if g is not self.primary]:
            for c in g.heroes():
                if c not in out:
                    out.append(c)
        return out


@dataclass
class Achievement:
    id: str
    name: str
    description: str = ""
    icon: str = ""
    icon_locked: str = ""
    unlocked: bool = False
    unlocked_at: float = 0.0
    rarity: float = -1.0  # percent of players, -1 unknown
    hidden: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Achievement":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Friend:
    source: str
    id: str
    name: str
    avatar: str = ""
    status: str = "offline"  # online | offline | away | busy | playing | unknown
    playing: str = ""
    profile_url: str = ""
    last_online: float = 0.0


@dataclass
class LaunchTask:
    id: str
    name: str
    kind: str = "game"  # game | tool | document | url
    primary: bool = False


@dataclass
class GameDetails:
    description: str = ""
    screenshots: list = field(default_factory=list)
    videos: list = field(default_factory=list)  # list of (thumbnail, url)
    genres: list = field(default_factory=list)
    developer: str = ""
    publisher: str = ""
    release_date: str = ""
    links: dict = field(default_factory=dict)  # name -> url
    dlcs: list = field(default_factory=list)  # list of dicts {id,title,owned,installed,cover}
    changelog: str = ""
    features: list = field(default_factory=list)
    languages: list = field(default_factory=list)
    rating: float = 0.0
    requirements: str = ""


@dataclass
class Session:
    uid: str
    key: str
    start: float
    end: float = 0.0

    @property
    def seconds(self) -> int:
        return int((self.end or time.time()) - self.start)
