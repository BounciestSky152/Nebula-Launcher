"""SQLite persistence for the library, user data, play sessions and caches."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from nebula import paths
from nebula.core.models import Achievement, Game, UserData

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    uid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS games_source ON games(source);
CREATE TABLE IF NOT EXISTS user_data (
    key TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL,
    key TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    seconds INTEGER NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sessions_key ON sessions(key);
CREATE TABLE IF NOT EXISTS achievements (
    uid TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS game_config (
    uid TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    uid TEXT,
    text TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else paths.db_path()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, args: Iterable[Any] = ()) -> list[tuple]:
        with self._lock:
            cur = self._conn.execute(sql, tuple(args))
            return cur.fetchall()

    # ---------------------------------------------------------------- games
    def load_games(self) -> list[Game]:
        rows = self._exec("SELECT data FROM games")
        out = []
        for (data,) in rows:
            try:
                out.append(Game.from_dict(json.loads(data)))
            except (ValueError, TypeError):
                continue
        return out

    def save_game(self, game: Game) -> None:
        self._exec(
            "INSERT OR REPLACE INTO games(uid, source, data) VALUES (?,?,?)",
            (game.uid, game.source, json.dumps(game.to_dict())),
        )

    def replace_source(self, source: str, games: list[Game]) -> None:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                self._conn.execute("DELETE FROM games WHERE source=?", (source,))
                self._conn.executemany(
                    "INSERT OR REPLACE INTO games(uid, source, data) VALUES (?,?,?)",
                    [(g.uid, g.source, json.dumps(g.to_dict())) for g in games],
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def delete_game(self, uid: str) -> None:
        self._exec("DELETE FROM games WHERE uid=?", (uid,))

    # ------------------------------------------------------------ user data
    def load_user_data(self) -> dict[str, UserData]:
        out = {}
        for key, data in self._exec("SELECT key, data FROM user_data"):
            try:
                d = json.loads(data)
                known = set(UserData.__dataclass_fields__)
                out[key] = UserData(**{k: v for k, v in d.items() if k in known})
            except (ValueError, TypeError):
                continue
        return out

    def save_user_data(self, ud: UserData) -> None:
        from dataclasses import asdict

        self._exec("INSERT OR REPLACE INTO user_data(key, data) VALUES (?,?)", (ud.key, json.dumps(asdict(ud))))

    # ------------------------------------------------------------- sessions
    def add_session(self, uid: str, key: str, start: float, end: float) -> int:
        seconds = max(0, int(end - start))
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions(uid, key, start, end, seconds) VALUES (?,?,?,?,?)",
                (uid, key, start, end, seconds),
            )
            return cur.lastrowid

    def mark_session_synced(self, session_id: int) -> None:
        self._exec("UPDATE sessions SET synced=1 WHERE id=?", (session_id,))

    def unsynced_sessions(self, uid_prefix: str) -> list[tuple]:
        return self._exec(
            "SELECT id, uid, start, end, seconds FROM sessions WHERE synced=0 AND uid LIKE ?",
            (uid_prefix + "%",),
        )

    def playtime_by_key(self) -> dict[str, tuple[int, float, int]]:
        """key -> (total seconds, last end timestamp, session count)."""
        rows = self._exec("SELECT key, SUM(seconds), MAX(end), COUNT(*) FROM sessions GROUP BY key")
        return {k: (int(s or 0), float(e or 0), int(c)) for k, s, e, c in rows}

    def playtime_by_uid(self) -> dict[str, int]:
        rows = self._exec("SELECT uid, SUM(seconds) FROM sessions GROUP BY uid")
        return {u: int(s or 0) for u, s in rows}

    def sessions_for(self, key: str, limit: int = 50) -> list[tuple]:
        return self._exec(
            "SELECT uid, start, end, seconds FROM sessions WHERE key=? ORDER BY start DESC LIMIT ?",
            (key, limit),
        )

    def sessions_between(self, start: float, end: float) -> list[tuple]:
        return self._exec(
            "SELECT uid, key, start, end, seconds FROM sessions WHERE end>=? AND start<=? ORDER BY start",
            (start, end),
        )

    # --------------------------------------------------------- achievements
    def get_achievements(self, uid: str) -> tuple[list[Achievement], float] | None:
        rows = self._exec("SELECT data, fetched FROM achievements WHERE uid=?", (uid,))
        if not rows:
            return None
        data, fetched = rows[0]
        try:
            return [Achievement.from_dict(a) for a in json.loads(data)], fetched
        except (ValueError, TypeError):
            return None

    def save_achievements(self, uid: str, items: list[Achievement]) -> None:
        self._exec(
            "INSERT OR REPLACE INTO achievements(uid, data, fetched) VALUES (?,?,?)",
            (uid, json.dumps([a.to_dict() for a in items]), time.time()),
        )

    def achievement_summary(self) -> dict[str, tuple[int, int]]:
        out = {}
        for uid, data in self._exec("SELECT uid, data FROM achievements"):
            try:
                items = json.loads(data)
            except ValueError:
                continue
            out[uid] = (sum(1 for a in items if a.get("unlocked")), len(items))
        return out

    # ---------------------------------------------------------- game config
    def get_config(self, uid: str) -> dict:
        rows = self._exec("SELECT data FROM game_config WHERE uid=?", (uid,))
        if not rows:
            return {}
        try:
            return json.loads(rows[0][0])
        except ValueError:
            return {}

    def set_config(self, uid: str, data: dict) -> None:
        self._exec("INSERT OR REPLACE INTO game_config(uid, data) VALUES (?,?)", (uid, json.dumps(data)))

    def update_config(self, uid: str, **changes) -> dict:
        with self._lock:
            cfg = self.get_config(uid)
            cfg.update(changes)
            self.set_config(uid, cfg)
            return cfg

    # ------------------------------------------------------------------- kv
    def kv_get(self, k: str, default: Any = None) -> Any:
        rows = self._exec("SELECT v FROM kv WHERE k=?", (k,))
        if not rows:
            return default
        try:
            return json.loads(rows[0][0])
        except ValueError:
            return default

    def kv_set(self, k: str, v: Any) -> None:
        self._exec("INSERT OR REPLACE INTO kv(k, v) VALUES (?,?)", (k, json.dumps(v)))

    # ------------------------------------------------------------- activity
    def log_activity(self, kind: str, text: str, uid: str = "") -> None:
        self._exec("INSERT INTO activity(ts, kind, uid, text) VALUES (?,?,?,?)", (time.time(), kind, uid, text))
        self._exec("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY ts DESC LIMIT 500)")

    def recent_activity(self, limit: int = 30) -> list[tuple]:
        return self._exec("SELECT ts, kind, uid, text FROM activity ORDER BY ts DESC LIMIT ?", (limit,))
