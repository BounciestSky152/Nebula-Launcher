"""Example Nebula integration plugin.

Copy this file to ~/.local/share/nebula/plugins/ and restart Nebula. It adds a
"ROMs" platform that lists every *.sfc file in ~/ROMs/snes and launches it
with RetroArch. Implement any subset of the methods in
nebula/integrations/base.py (fetch_library, prepare_launch, achievements,
friends, install, ...).
"""
from __future__ import annotations

from pathlib import Path

from nebula.core.models import Game
from nebula.integrations import base
from nebula.integrations.base import Integration
from nebula.runners.compat import LaunchSpec

ROM_DIR = Path.home() / "ROMs" / "snes"
CORE = "/usr/lib/libretro/snes9x_libretro.so"


class RomsIntegration(Integration):
    id = "roms"
    name = "SNES ROMs"
    color = "#d04848"
    letter = "R"
    capabilities = frozenset({base.LIBRARY, base.LAUNCH})

    def available(self) -> bool:
        return ROM_DIR.is_dir()

    def fetch_library(self) -> list[Game]:
        games = []
        for rom in sorted(ROM_DIR.glob("*.sfc")):
            g = Game(self.id, rom.stem, rom.stem.replace("_", " "))
            g.installed = True
            g.install_path = str(rom.parent)
            g.platform = "linux"
            g.extra["rom"] = str(rom)
            games.append(g)
        return games

    def prepare_launch(self, game: Game, task_id: str = "") -> LaunchSpec:
        return LaunchSpec(argv=["retroarch", "-L", CORE, game.extra["rom"]], cwd=str(ROM_DIR))
