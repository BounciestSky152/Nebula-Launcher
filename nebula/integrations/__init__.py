"""Integration registry and the third-party plugin loader.

Plugins are plain Python files dropped into ``~/.local/share/nebula/plugins``
(see docs/examples/example_plugin.py). Every subclass of
:class:`nebula.integrations.base.Integration` with an ``id`` found in such a
file is registered, exactly like GOG Galaxy's community integrations.
"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import sys

from nebula import paths
from nebula.integrations.base import Integration

log = logging.getLogger(__name__)


def builtin_classes() -> list[type[Integration]]:
    from nebula.integrations.epic import EpicIntegration
    from nebula.integrations.gog import GogIntegration
    from nebula.integrations.local import LocalIntegration
    from nebula.integrations.lutris import LutrisIntegration
    from nebula.integrations.steam import SteamIntegration

    return [GogIntegration, SteamIntegration, EpicIntegration, LutrisIntegration, LocalIntegration]


def plugin_classes() -> list[type[Integration]]:
    found: list[type[Integration]] = []
    for path in sorted(paths.plugins_dir().glob("*.py")):
        name = f"nebula_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001
            log.error("Failed to load plugin %s: %s", path.name, exc)
            continue
        for _n, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Integration) and obj is not Integration and obj.__module__ == name and obj.id:
                found.append(obj)
    return found


def create_all(services) -> dict[str, Integration]:
    out: dict[str, Integration] = {}
    for cls in builtin_classes() + plugin_classes():
        if cls.id in out:
            log.warning("Duplicate integration id %s ignored", cls.id)
            continue
        try:
            out[cls.id] = cls(services)
        except Exception as exc:  # noqa: BLE001
            log.error("Could not initialise integration %s: %s", cls.id, exc)
    return out
