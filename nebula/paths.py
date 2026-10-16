"""XDG-compliant filesystem locations used by Nebula.

Setting ``NEBULA_HOME`` relocates every directory under a single root, which
is handy for portable installs and for the test-suite.
"""
from __future__ import annotations

import os
from pathlib import Path


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var)
    if value and os.path.isabs(value):
        return Path(value)
    return Path.home() / fallback


def _root(kind: str) -> Path:
    home = os.environ.get("NEBULA_HOME")
    if home:
        return Path(home) / kind
    return {
        "config": _xdg("XDG_CONFIG_HOME", ".config"),
        "data": _xdg("XDG_DATA_HOME", ".local/share"),
        "cache": _xdg("XDG_CACHE_HOME", ".cache"),
    }[kind] / "nebula"


def config_dir() -> Path:
    return _ensure(_root("config"))


def data_dir() -> Path:
    return _ensure(_root("data"))


def cache_dir() -> Path:
    return _ensure(_root("cache"))


def _ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def sub(base: Path, *parts: str) -> Path:
    return _ensure(base.joinpath(*parts))


def db_path() -> Path:
    return data_dir() / "nebula.db"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def prefixes_dir() -> Path:
    return sub(data_dir(), "prefixes")


def runners_dir() -> Path:
    return sub(data_dir(), "runners")


def proton_dir() -> Path:
    return sub(runners_dir(), "proton")


def wine_dir() -> Path:
    return sub(runners_dir(), "wine")


def umu_dir() -> Path:
    return sub(runners_dir(), "umu")


def redist_dir() -> Path:
    return sub(data_dir(), "redist")


def manifests_dir(source: str) -> Path:
    return sub(data_dir(), "manifests", source)


def plugins_dir() -> Path:
    return sub(data_dir(), "plugins")


def logs_dir() -> Path:
    return sub(data_dir(), "logs")


def images_cache() -> Path:
    return sub(cache_dir(), "images")


def installers_cache() -> Path:
    return sub(cache_dir(), "installers")


def default_library_dir() -> Path:
    return Path.home() / "Games" / "Nebula"


def applications_dir() -> Path:
    return _ensure(_xdg("XDG_DATA_HOME", ".local/share") / "applications")


def desktop_dir() -> Path:
    try:
        import subprocess

        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=2)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except Exception:
        pass
    return Path.home() / "Desktop"
