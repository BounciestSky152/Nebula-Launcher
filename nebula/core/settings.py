"""Global application settings persisted as JSON."""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from nebula import paths

DEFAULTS: dict[str, Any] = {
    # General
    "general.start_minimized": False,
    "general.autostart": False,  # start Super Nova when the user logs in
    "handheld.mode": "off",  # on | off
    "general.close_to_tray": True,
    "general.show_tray": True,
    "general.notifications": True,
    "general.launch_behaviour": "keep",  # keep | minimize | hide
    "general.language": "en-US",
    # Library
    "library.install_dir": str(paths.default_library_dir()),
    "library.scan_dirs": [],
    "library.view": "grid",  # grid | list
    "library.cover_size": 170,
    "library.sort": "title",
    "library.sort_desc": False,
    "library.show_hidden": False,
    "library.custom_views": [],  # list of {"name":..., "filter": {...}}
    "library.detect_external_sessions": True,
    "library.list_columns": ["title", "platforms", "last_played", "playtime", "genres", "release", "rating", "size"],
    # Downloads
    "downloads.speed_limit_kbps": 0,
    "downloads.workers": 8,
    "downloads.concurrent_jobs": 1,
    "downloads.auto_update": True,
    "downloads.check_updates_on_start": True,
    "downloads.keep_installers": False,
    "downloads.prefer_native": True,
    "downloads.install_dependencies": True,
    # Compatibility (Proton is the default for every Windows-only game)
    "compat.default_runner": "proton",  # proton | wine
    "compat.default_proton": "",  # empty = newest detected (GE-Proton preferred)
    "compat.default_wine": "",
    "compat.use_umu": True,
    "compat.esync": True,
    "compat.fsync": True,
    "compat.dxvk_hud": False,
    "compat.wayland": False,
    "compat.nvapi": False,
    "compat.mangohud": False,
    "compat.gamemode": True,
    "compat.gamescope": False,
    "compat.gamescope_args": "-f",
    "compat.native_dosbox": True,
    "compat.native_scummvm": True,
    # Cloud saves
    "cloud.enabled": True,
    "cloud.sync_on_launch": True,
    "cloud.sync_on_exit": True,
    "cloud.conflict": "ask",  # ask | newest
    # GOG
    "gog.presence": True,
    "gog.sync_playtime": True,
    "gog.country": "US",
    "gog.currency": "USD",
    # Steam
    "steam.api_key": "",
    "steam.steamid": "",
    # Integrations enabled
    "integrations.enabled": {"gog": True, "steam": True, "epic": True, "lutris": True, "local": True},
    # Friends
    "friends.poll_minutes": 2,
    "friends.notify_playing": True,
    # Super Nova self-updates (from GitHub)
    "updates.auto_check": True,
    "updates.auto_install": True,
    "updates.auto_restart": True,
    "updates.interval_minutes": 30,
    "updates.repo": "BounciestSky152/Super-Nova-Launcher",
    "updates.branch": "main",
    "updates.github_token": "",
    "appearance.theme": "nebula",  # nebula | classic
    "appearance.font_ui": "",  # "" = the system font
    "appearance.font_display": "Audiowide",  # logo, section headers, page titles
    "appearance.font_sidebar": "Orbitron",  # only needed for a private repository
    # Appearance
    "appearance.starfield": True,
    "appearance.animate_stars": True,
    "appearance.fun": True,
    # Window state
    "window.geometry": "",
    "window.friends_open": False,
}


class Settings:
    """Thread-safe settings store with dotted keys."""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else paths.settings_path()
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._listeners: list = []
        self.load()

    def load(self) -> None:
        with self._lock:
            try:
                self._data = json.loads(self._path.read_text())
            except (OSError, ValueError):
                self._data = {}
            if str(self._data.get("updates.repo", "")).lower() == "bounciestsky152/nebula-launcher":
                self._data["updates.repo"] = DEFAULTS["updates.repo"]  # the repository was renamed

    def save(self) -> None:
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
            os.replace(tmp, self._path)

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            if key in self._data:
                return copy.deepcopy(self._data[key])
            if key in DEFAULTS:
                return copy.deepcopy(DEFAULTS[key])
            return default

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if self._data.get(key) == value:
                return
            self._data[key] = value
            self.save()
        for cb in list(self._listeners):
            try:
                cb(key, value)
            except Exception:
                pass

    def reset(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)
            self.save()

    def on_change(self, callback) -> None:
        self._listeners.append(callback)

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.set(key, value)
