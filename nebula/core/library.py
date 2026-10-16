"""The unified library: merges games from every integration into one entry per title."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta

from nebula.core import tasks
from nebula.core.models import SOURCES_ORDER, Game, LibraryEntry, UserData, sort_title

log = logging.getLogger(__name__)


@dataclass
class LibraryFilter:
    text: str = ""
    sources: list = field(default_factory=list)  # empty = every platform
    installed: str = "all"  # all | installed | not_installed
    tags: list = field(default_factory=list)
    genres: list = field(default_factory=list)
    os: list = field(default_factory=list)  # linux | windows
    hidden: str = "hide"  # hide | show | only
    favorites: bool = False
    updates: bool = False
    played: str = "all"  # all | played | unplayed
    sort: str = "title"  # title | last_played | playtime | added | release | rating | size | critic | achievements
    desc: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "LibraryFilter":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in (d or {}).items() if k in known})

    def is_default(self) -> bool:
        return self.to_dict() == LibraryFilter(sort=self.sort, desc=self.desc).to_dict()


SORT_LABELS = {
    "title": "Title",
    "last_played": "Last played",
    "playtime": "Time played",
    "added": "Date added",
    "release": "Release date",
    "rating": "My rating",
    "critic": "Critics score",
    "size": "Size on disk",
    "achievements": "Achievements",
}


class LibraryManager:
    def __init__(self, services):
        self.services = services
        self.games: dict[str, Game] = {}
        self.user: dict[str, UserData] = {}
        self.entries: dict[str, LibraryEntry] = {}
        self.syncing: set[str] = set()
        self._resync: set[str] = set()
        self._lock = threading.RLock()
        self._uid_to_key: dict[str, str] = {}

    @property
    def bus(self):
        return self.services.bus

    @property
    def db(self):
        return self.services.db

    # -------------------------------------------------------------- load
    def load(self) -> None:
        with self._lock:
            self.games = {g.uid: g for g in self.db.load_games()}
            self.user = self.db.load_user_data()
        self.rebuild()

    def games_for_source(self, source: str) -> list[Game]:
        with self._lock:
            return [g for g in self.games.values() if g.source == source]

    def rebuild(self) -> None:
        with self._lock:
            play_uid = self.db.playtime_by_uid()
            play_key = self.db.playtime_by_key()
            ach = self.db.achievement_summary()
            groups: dict[str, list[Game]] = {}
            for g in self.games.values():
                if g.is_dlc:
                    continue
                if g.source not in self._enabled_sources():
                    continue
                groups.setdefault(g.key, []).append(g)
            entries: dict[str, LibraryEntry] = {}
            uid_to_key: dict[str, str] = {}
            for key, games in groups.items():
                games.sort(key=lambda g: (not g.installed, _src_rank(g.source)))
                ud = self.user.get(key) or UserData(key)
                minutes = 0
                last = 0.0
                for g in games:
                    uid_to_key[g.uid] = key
                    local_min = play_uid.get(g.uid, 0) // 60
                    minutes += max(int(g.platform_playtime or 0), local_min)
                    last = max(last, float(g.platform_last_played or 0))
                total_s, last_end, count = play_key.get(key, (0, 0.0, 0))
                last = max(last, last_end)
                unlocked = total = 0
                for g in games:
                    u, t = ach.get(g.uid, (0, 0))
                    if t > total:
                        unlocked, total = u, t
                entries[key] = LibraryEntry(key=key, games=games, user=ud, playtime_minutes=minutes,
                                            last_played=last, sessions=count, achievements=(unlocked, total))
            self.entries = entries
            self._uid_to_key = uid_to_key

    def _enabled_sources(self) -> set[str]:
        enabled = self.services.settings.get("integrations.enabled", {}) or {}
        return {s for s in self.services.integrations if enabled.get(s, True)}

    # ---------------------------------------------------------- lookups
    def entry(self, key: str) -> LibraryEntry | None:
        return self.entries.get(key)

    def entry_for_uid(self, uid: str) -> LibraryEntry | None:
        key = self._uid_to_key.get(uid)
        return self.entries.get(key) if key else None

    def game(self, uid: str) -> Game | None:
        return self.games.get(uid)

    def all_entries(self) -> list[LibraryEntry]:
        return list(self.entries.values())

    def all_tags(self) -> list[str]:
        tags = set()
        for ud in self.user.values():
            tags.update(ud.tags)
        return sorted(tags, key=str.lower)

    def all_genres(self) -> list[str]:
        genres = set()
        for e in self.entries.values():
            genres.update(e.genres)
        return sorted(genres, key=str.lower)

    def counts(self) -> dict[str, int]:
        out = {"owned": 0, "installed": 0, "hidden": 0, "updates": 0, "favorites": 0}
        for e in self.entries.values():
            if e.user.hidden:
                out["hidden"] += 1
                continue
            out["owned"] += 1
            out["installed"] += int(e.installed)
            out["updates"] += int(e.update_available)
            out["favorites"] += int(e.user.favorite)
            for s in e.sources:
                out[s] = out.get(s, 0) + 1
        return out

    # ------------------------------------------------------------ query
    def query(self, f: LibraryFilter) -> list[LibraryEntry]:
        text = f.text.strip().lower()
        out = []
        for e in self.entries.values():
            if f.hidden == "hide" and e.user.hidden:
                continue
            if f.hidden == "only" and not e.user.hidden:
                continue
            if f.sources and not set(f.sources) & set(e.sources):
                continue
            if f.installed == "installed" and not e.installed:
                continue
            if f.installed == "not_installed" and e.installed:
                continue
            if f.tags and not set(f.tags) & set(e.user.tags):
                continue
            if f.genres and not set(f.genres) & set(e.genres):
                continue
            if f.os:
                oses = set()
                for g in e.games:
                    oses.update(g.os_support)
                    if g.platform:
                        oses.add(g.platform)
                if not set(f.os) & oses:
                    continue
            if f.favorites and not e.user.favorite:
                continue
            if f.updates and not e.update_available:
                continue
            if f.played == "played" and not e.playtime_minutes and not e.last_played:
                continue
            if f.played == "unplayed" and (e.playtime_minutes or e.last_played):
                continue
            if text and text not in e.title.lower() and not any(text in t.lower() for t in e.user.tags) \
                    and not any(text in g.developer.lower() for g in e.games):
                continue
            out.append(e)
        out.sort(key=lambda e: sort_title(e.title))
        keyfn = {
            "title": None,
            "last_played": lambda e: e.last_played,
            "playtime": lambda e: e.playtime_minutes,
            "added": lambda e: e.added_at,
            "release": lambda e: e.release_date or "",
            "rating": lambda e: e.user.rating,
            "critic": lambda e: max((g.critic_score for g in e.games), default=0),
            "size": lambda e: e.install_size,
            "achievements": lambda e: (e.achievements[0] / e.achievements[1]) if e.achievements[1] else -1,
        }.get(f.sort)
        if keyfn is None:
            if f.desc:
                out.reverse()
        else:
            out.sort(key=keyfn, reverse=not f.desc)
        return out

    # ---------------------------------------------------------- updates
    def update_game(self, game: Game, emit: bool = True) -> None:
        with self._lock:
            old = self.games.get(game.uid)
            if old and not game.added_at:
                game.added_at = old.added_at
            self.games[game.uid] = game
            self.db.save_game(game)
        self.rebuild()
        if emit:
            key = self._uid_to_key.get(game.uid, game.key)
            self.bus.game_changed.emit(key)
            if old is None:
                self.bus.library_changed.emit()

    def remove_game(self, uid: str) -> None:
        with self._lock:
            self.games.pop(uid, None)
            self.db.delete_game(uid)
        self.rebuild()
        self.bus.library_changed.emit()

    def user_data(self, key: str) -> UserData:
        return self.user.get(key) or UserData(key)

    def set_user(self, key: str, **changes) -> None:
        with self._lock:
            ud = self.user.get(key) or UserData(key)
            for k, v in changes.items():
                setattr(ud, k, v)
            self.user[key] = ud
            self.db.save_user_data(ud)
        self.rebuild()
        self.bus.game_changed.emit(key)

    def add_tag(self, key: str, tag: str) -> None:
        ud = self.user_data(key)
        if tag and tag not in ud.tags:
            self.set_user(key, tags=sorted(ud.tags + [tag], key=str.lower))

    def remove_tag(self, key: str, tag: str) -> None:
        ud = self.user_data(key)
        if tag in ud.tags:
            self.set_user(key, tags=[t for t in ud.tags if t != tag])

    def rename_tag(self, old: str, new: str) -> None:
        for key, ud in list(self.user.items()):
            if old in ud.tags:
                tags = [new if t == old else t for t in ud.tags]
                self.set_user(key, tags=sorted(set(tags), key=str.lower))

    # ------------------------------------------------------------ syncing
    def refresh(self, sources: list[str] | None = None, enrich: bool = True, on_done=None) -> None:
        integs = [i for i in self.services.integrations.values()
                  if (sources is None or i.id in sources) and i.enabled() and i.supports("library")]
        for integ in integs:
            if integ.id in self.syncing:
                self._resync.add(integ.id)  # refresh again once the running sync finishes
                continue
            self.syncing.add(integ.id)
            self.bus.sync_state.emit(integ.id, True)
            tasks.run_async(self._fetch, integ, on_done=lambda res, i=integ: self._fetched(i, res, enrich, on_done),
                            on_error=lambda exc, i=integ: self._fetch_failed(i, exc, on_done))

    def _fetch(self, integ) -> list[Game] | None:
        if not integ.available() or not integ.connected():
            return None
        return integ.fetch_library()

    def _after_sync(self, integ) -> None:
        self.syncing.discard(integ.id)
        self.bus.sync_state.emit(integ.id, False)
        if integ.id in self._resync:
            self._resync.discard(integ.id)
            tasks.on_main(lambda: self.refresh([integ.id], enrich=False))

    def _fetched(self, integ, games: list[Game] | None, enrich: bool, on_done) -> None:
        self._after_sync(integ)
        if games is not None:
            now = time.time()
            with self._lock:
                for g in games:
                    old = self.games.get(g.uid)
                    g.added_at = (old.added_at if old and old.added_at else g.added_at) or now
                    if old:
                        # keep metadata that enrichment gathered earlier
                        for attr in ("cover", "hero", "logo", "icon", "description", "developer", "publisher"):
                            if not getattr(g, attr) and getattr(old, attr):
                                setattr(g, attr, getattr(old, attr))
                        for k, v in old.extra.items():
                            g.extra.setdefault(k, v)
                self.db.replace_source(integ.id, games)
                self.games = {u: g for u, g in self.games.items() if g.source != integ.id}
                self.games.update({g.uid: g for g in games})
            self.rebuild()
            self.bus.library_changed.emit()
            if enrich:
                tasks.run_async(integ.enrich, list(games), on_done=self._enriched)
        if on_done:
            on_done(integ.id)

    def _fetch_failed(self, integ, exc, on_done) -> None:
        self._after_sync(integ)
        log.warning("Library sync for %s failed: %s", integ.id, exc)
        self.bus.error.emit(f"{integ.name} sync failed", str(exc))
        if on_done:
            on_done(integ.id)

    def _enriched(self, changed: list[Game]) -> None:
        if not changed:
            return
        with self._lock:
            for g in changed:
                if g.uid in self.games:
                    self.games[g.uid] = g
                    self.db.save_game(g)
        self.rebuild()
        self.bus.library_changed.emit()

    # ------------------------------------------------------------- stats
    def stats(self) -> dict:
        entries = [e for e in self.entries.values() if not e.user.hidden]
        total_minutes = sum(e.playtime_minutes for e in entries)
        played = [e for e in entries if e.playtime_minutes or e.last_played]
        ach_unlocked = sum(e.achievements[0] for e in entries)
        ach_total = sum(e.achievements[1] for e in entries)
        per_source: dict[str, int] = {}
        for e in entries:
            for s in e.sources:
                per_source[s] = per_source.get(s, 0) + 1
        genres: dict[str, int] = {}
        for e in entries:
            for g in e.genres[:2]:
                genres[g] = genres.get(g, 0) + 1
        top = sorted(entries, key=lambda e: e.playtime_minutes, reverse=True)[:10]
        return {
            "owned": len(entries),
            "installed": sum(1 for e in entries if e.installed),
            "played": len(played),
            "minutes": total_minutes,
            "achievements": (ach_unlocked, ach_total),
            "per_source": per_source,
            "genres": sorted(genres.items(), key=lambda kv: kv[1], reverse=True)[:8],
            "top": [(e.title, e.playtime_minutes, e.key) for e in top if e.playtime_minutes],
            "weeks": self.weekly_hours(12),
            "days": self.daily_hours(7),
        }

    def daily_hours(self, days: int) -> list[tuple[str, float]]:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for i in range(days - 1, -1, -1):
            start = today - timedelta(days=i)
            end = start + timedelta(days=1)
            secs = self._seconds_between(start.timestamp(), end.timestamp())
            out.append((start.strftime("%a"), secs / 3600))
        return out

    def weekly_hours(self, weeks: int) -> list[tuple[str, float]]:
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        monday = today - timedelta(days=today.weekday())
        out = []
        for i in range(weeks - 1, -1, -1):
            start = monday - timedelta(weeks=i)
            end = start + timedelta(weeks=1)
            out.append((start.strftime("%d %b"), self._seconds_between(start.timestamp(), end.timestamp()) / 3600))
        return out

    def _seconds_between(self, start: float, end: float) -> float:
        total = 0.0
        for _uid, _key, s, e, _secs in self.db.sessions_between(start, end):
            total += max(0.0, min(e, end) - max(s, start))
        return total

    def recently_played(self, limit: int = 8) -> list[LibraryEntry]:
        es = [e for e in self.entries.values() if e.last_played and not e.user.hidden]
        es.sort(key=lambda e: e.last_played, reverse=True)
        return es[:limit]

    def recently_added(self, limit: int = 12) -> list[LibraryEntry]:
        es = [e for e in self.entries.values() if not e.user.hidden]
        es.sort(key=lambda e: e.added_at, reverse=True)
        return es[:limit]


def _src_rank(source: str) -> int:
    try:
        return SOURCES_ORDER.index(source)
    except ValueError:
        return len(SOURCES_ORDER)
