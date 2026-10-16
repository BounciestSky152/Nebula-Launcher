"""Games added manually (Galaxy's "Add game manually"): native binaries, scripts,
Windows executables (run with Proton) and emulator command lines."""
from __future__ import annotations

import shlex
import time
import uuid
from pathlib import Path

from nebula.core.models import Game
from nebula.integrations import base
from nebula.integrations.base import Integration
from nebula.runners.compat import LaunchSpec

KEY = "local.games"
WINDOWS_EXT = (".exe", ".msi", ".bat", ".lnk")


class LocalIntegration(Integration):
    id = "local"
    name = "Other games"
    color = "#8a8f98"
    letter = "+"
    description = "Games you add yourself: Linux binaries, Windows .exe files (Proton) and emulators."
    capabilities = frozenset({base.LIBRARY, base.LAUNCH, base.UNINSTALL})

    def entries(self) -> dict:
        return self.db.kv_get(KEY, {}) or {}

    def status_text(self) -> str:
        n = len(self.entries())
        return f"{n} game{'s' if n != 1 else ''} added manually"

    def save_entry(self, data: dict) -> str:
        entries = self.entries()
        gid = data.get("id") or uuid.uuid4().hex[:12]
        data["id"] = gid
        data.setdefault("added_at", time.time())
        exe = data.get("exe", "")
        if not data.get("platform"):
            data["platform"] = "windows" if exe.lower().endswith(WINDOWS_EXT) else "linux"
        entries[gid] = data
        self.db.kv_set(KEY, entries)
        return gid

    def remove_entry(self, gid: str) -> None:
        entries = self.entries()
        entries.pop(gid, None)
        self.db.kv_set(KEY, entries)

    def fetch_library(self) -> list[Game]:
        out = []
        for gid, e in self.entries().items():
            exe = e.get("exe", "")
            g = Game(self.id, gid, e.get("title") or Path(exe).stem or "Game", added_at=e.get("added_at", 0))
            g.installed = True
            g.install_path = e.get("cwd") or (str(Path(exe).parent) if exe.startswith("/") else "")
            g.platform = e.get("platform", "linux")
            g.os_support = [g.platform]
            g.cover = e.get("cover", "")
            g.hero = e.get("hero", "")
            g.icon = e.get("icon", "")
            g.genres = [x.strip() for x in e.get("genres", "").split(",") if x.strip()] if isinstance(
                e.get("genres"), str) else list(e.get("genres", []))
            g.developer = e.get("developer", "")
            g.release_date = e.get("release_date", "")
            g.description = e.get("description", "")
            g.extra["entry"] = e
            out.append(g)
        return out

    def prepare_launch(self, game: Game, task_id: str = "") -> LaunchSpec:
        e = self.entries().get(game.source_id)
        if not e:
            raise FileNotFoundError("This game entry no longer exists")
        exe = e.get("exe", "")
        args = shlex.split(e.get("args", "")) if e.get("args") else []
        if not exe.startswith("/"):
            # a command line such as "retroarch -L core.so rom.sfc"
            parts = shlex.split(exe)
            exe, args = parts[0], parts[1:] + args
        return self.services.compat.build(game.uid, exe, args, cwd=e.get("cwd") or "",
                                          platform=e.get("platform", "linux"), install_dir=e.get("cwd") or "")

    def process_hints(self, game: Game) -> dict:
        e = self.entries().get(game.source_id, {})
        exe = e.get("exe", "")
        if exe.startswith("/"):
            return {"paths": [str(Path(exe).parent)], "exe": exe}
        return {"paths": []}

    def uninstall(self, game: Game, ctx) -> Game:
        ctx.stage("Removing from library")
        self.remove_entry(game.source_id)
        game.installed = False
        return game
