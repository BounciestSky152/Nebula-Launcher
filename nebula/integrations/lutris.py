"""Lutris integration: imports installed Lutris games and launches them through Lutris."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from nebula.core.models import Game
from nebula.core.utils import flatpak_installed, which
from nebula.integrations import base
from nebula.integrations.base import Integration
from nebula.runners.compat import LaunchSpec

HOME = Path.home()
DATA_DIRS = [HOME / ".local/share/lutris", HOME / ".var/app/net.lutris.Lutris/data/lutris"]
CACHE_DIRS = [HOME / ".cache/lutris", HOME / ".var/app/net.lutris.Lutris/cache/lutris"]


class LutrisIntegration(Integration):
    id = "lutris"
    name = "Lutris"
    color = "#f0a030"
    letter = "L"
    description = "Games installed with Lutris (emulators, Battle.net, Ubisoft Connect, EA app, ...)."
    capabilities = frozenset({base.LIBRARY, base.LAUNCH, base.PLAYTIME})

    def db_path(self) -> Path | None:
        for d in DATA_DIRS:
            if (d / "pga.db").exists():
                return d / "pga.db"
        return None

    def available(self) -> bool:
        return self.db_path() is not None

    def status_text(self) -> str:
        return "Lutris library detected" if self.available() else "Lutris not detected"

    def _flatpak(self) -> bool:
        p = self.db_path()
        return bool(p and ".var/app" in str(p))

    def _art(self, kind: str, slug: str) -> list[str]:
        out = []
        for root in DATA_DIRS + CACHE_DIRS:
            for ext in ("jpg", "png"):
                p = root / kind / f"{slug}.{ext}"
                if p.exists():
                    out.append(str(p))
        return out

    def fetch_library(self) -> list[Game]:
        path = self.db_path()
        if not path:
            return []
        prev = {g.source_id: g for g in self.services.library.games_for_source(self.id)}
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(games)")}
            want = [c for c in ("id", "name", "slug", "runner", "platform", "directory", "installed", "playtime",
                                "lastplayed", "installed_at", "service") if c in cols]
            rows = conn.execute(f"SELECT {', '.join(want)} FROM games WHERE installed=1").fetchall()
        finally:
            conn.close()
        out = []
        for row in rows:
            r = dict(zip(want, row))
            gid = str(r["id"])
            g = prev.get(gid) or Game(self.id, gid, r.get("name") or r.get("slug") or gid,
                                      added_at=float(r.get("installed_at") or time.time()))
            g.title = r.get("name") or g.title
            g.installed = True
            g.install_path = r.get("directory") or ""
            g.platform = "linux" if (r.get("runner") == "linux") else "windows" if r.get("runner") == "wine" else ""
            g.extra["runner"] = r.get("runner", "")
            g.extra["lutris_platform"] = r.get("platform", "")
            g.extra["service"] = r.get("service", "")
            try:
                g.platform_playtime = int(float(r.get("playtime") or 0) * 60)
            except (TypeError, ValueError):
                pass
            g.platform_last_played = float(r.get("lastplayed") or 0)
            slug = r.get("slug") or ""
            covers = self._art("coverart", slug)
            banners = self._art("banners", slug)
            if covers:
                g.cover = covers[0]
            if banners:
                g.hero = banners[0]
                g.horizontal = banners[0]
            g.store_url = f"https://lutris.net/games/{slug}/" if slug else ""
            out.append(g)
        return out

    def _cmd(self) -> list[str]:
        if not self._flatpak() and which("lutris"):
            return ["lutris"]
        if flatpak_installed("net.lutris.Lutris"):
            return ["flatpak", "run", "net.lutris.Lutris"]
        return ["lutris"] if which("lutris") else []

    def prepare_launch(self, game: Game, task_id: str = "") -> LaunchSpec:
        cmd = self._cmd()
        uri = f"lutris:rungameid/{game.source_id}"
        if cmd:
            return LaunchSpec(argv=cmd + [uri], description="Lutris")
        return LaunchSpec(uri=uri, description="Lutris")

    def process_hints(self, game: Game) -> dict:
        return {"paths": [game.install_path] if game.install_path and game.install_path != str(HOME) else []}

    def open_platform_client(self) -> None:
        import subprocess

        cmd = self._cmd()
        if cmd:
            subprocess.Popen(cmd, start_new_session=True)
