"""Epic Games Store via Legendary (standalone or the copy bundled with Heroic).

Owned and installed games are read from Legendary's metadata. Games are
launched with ``legendary launch --no-wine --wrapper <Proton>`` so they run
through Super Nova's Proton runner (with Epic authentication handled by
Legendary); games managed by Heroic can alternatively be handed to Heroic.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from nebula.core.jobs import Canceled, JobContext
from nebula.core.models import Game
from nebula.core.utils import flatpak_installed, sanitize_dirname, which, xdg_open
from nebula.integrations import base
from nebula.integrations.base import InstallOptions, InstallRequest, Integration, NotSupported
from nebula.runners.compat import LaunchSpec, RunnerMissing

log = logging.getLogger(__name__)

HOME = Path.home()
CONFIG_DIRS = [
    (HOME / ".config/legendary", "legendary"),
    (HOME / ".config/heroic/legendaryConfig/legendary", "heroic"),
    (HOME / ".var/app/com.heroicgameslauncher.hgl/config/heroic/legendaryConfig/legendary", "heroic-flatpak"),
]
HEROIC_LEGENDARY = [
    "/opt/Heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    "/usr/lib/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    "/usr/lib/heroic-games-launcher-bin/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
]
PROGRESS = re.compile(r"Progress:\s*([\d.]+)%")
DOWNLOADED = re.compile(r"Downloaded:\s*([\d.]+)\s*MiB")
DL_SIZE = re.compile(r"Download size:\s*([\d.]+)\s*MiB")
INSTALL_SIZE = re.compile(r"Install size:\s*([\d.]+)\s*MiB")
MIB = 1024 * 1024


class EpicIntegration(Integration):
    id = "epic"
    name = "Epic Games"
    color = "#cfcfcf"
    letter = "E"
    description = "Epic Games Store library through Legendary or Heroic; Windows games run with Proton."
    capabilities = frozenset({base.LIBRARY, base.INSTALL, base.UNINSTALL, base.LAUNCH, base.UPDATE,
                              base.REPAIR, base.STORE})

    # ------------------------------------------------------------- setup
    def config_dirs(self) -> list[tuple[Path, str]]:
        return [(d, kind) for d, kind in CONFIG_DIRS if (d / "metadata").is_dir() or (d / "installed.json").exists()]

    def legendary(self) -> str | None:
        p = which("legendary")
        if p:
            return p
        for cand in HEROIC_LEGENDARY:
            if os.access(cand, os.X_OK):
                return cand
        return None

    def heroic_available(self) -> bool:
        return bool(which("heroic", "heroic-games-launcher")) or flatpak_installed("com.heroicgameslauncher.hgl")

    def available(self) -> bool:
        return bool(self.config_dirs()) or bool(self.legendary())

    def connected(self) -> bool:
        return any((d / "user.json").exists() for d, _ in self.config_dirs())

    def account_name(self) -> str:
        for d, _ in self.config_dirs():
            try:
                return json.loads((d / "user.json").read_text()).get("displayName", "")
            except (OSError, ValueError):
                continue
        return ""

    def status_text(self) -> str:
        if not self.available():
            return "Install Legendary or Heroic Games Launcher to use your Epic library"
        if not self.connected():
            return "Run `legendary auth` (or log in to Epic in Heroic)"
        tools = "Legendary" if self.legendary() else "Heroic"
        return f"Signed in as {self.account_name()} • via {tools}"

    def connect(self, **_kw) -> None:
        exe = self.legendary()
        if exe:
            subprocess.Popen([exe, "auth"], start_new_session=True)
        elif self.heroic_available():
            xdg_open("heroic://")
        else:
            raise NotSupported("Install Legendary (pip install legendary-gl) or Heroic Games Launcher first")

    def _env_for(self, config_dir: Path) -> dict:
        env = os.environ.copy()
        env["LEGENDARY_CONFIG_PATH"] = str(config_dir)
        return env

    # ---------------------------------------------------------- library
    def fetch_library(self) -> list[Game]:
        prev = {g.source_id: g for g in self.services.library.games_for_source(self.id)}
        games: dict[str, Game] = {}
        for cfg_dir, kind in self.config_dirs():
            try:
                installed = json.loads((cfg_dir / "installed.json").read_text())
            except (OSError, ValueError):
                installed = {}
            for meta_file in (cfg_dir / "metadata").glob("*.json"):
                try:
                    data = json.loads(meta_file.read_text())
                except (OSError, ValueError):
                    continue
                app = data.get("app_name") or meta_file.stem
                meta = data.get("metadata") or {}
                if meta.get("mainGameItem"):
                    continue  # DLC
                cats = {c.get("path") for c in meta.get("categories", []) if isinstance(c, dict)}
                if cats and "games" not in cats:
                    continue
                if app in games and not installed.get(app):
                    continue
                g = prev.get(app) or Game(self.id, app, data.get("app_title") or meta.get("title") or app,
                                          added_at=time.time())
                g.title = data.get("app_title") or meta.get("title") or g.title
                images = {i.get("type"): i.get("url") for i in meta.get("keyImages", []) if isinstance(i, dict)}
                g.cover = images.get("DieselGameBoxTall") or images.get("Thumbnail") or g.cover
                g.hero = images.get("DieselGameBox") or images.get("OfferImageWide") or g.hero
                g.horizontal = images.get("DieselGameBox") or g.horizontal
                g.logo = images.get("DieselGameBoxLogo") or g.logo
                g.developer = meta.get("developer") or g.developer
                g.description = meta.get("description") or g.description
                attrs = meta.get("customAttributes") or {}
                slug = (attrs.get("CloudSaveFolder") or {}).get("value", "")
                g.extra["config_dir"] = str(cfg_dir)
                g.extra["manager"] = kind
                g.extra["namespace"] = meta.get("namespace", "")
                g.extra["latest_version"] = ((data.get("asset_infos") or {}).get("Windows") or {}).get("build_version", "")
                g.extra["cloud_folder"] = slug
                plats = set((data.get("asset_infos") or {}).keys())
                g.os_support = [o for o, k in (("windows", "Windows"), ("mac", "Mac")) if k in plats] or ["windows"]
                g.store_url = f"https://store.epicgames.com/p/{(meta.get('productSlug') or attrs.get('com.epicgames.app.productSlug', {}).get('value') or '').split('/')[0]}"
                inst = installed.get(app)
                if inst:
                    g.installed = True
                    g.install_path = inst.get("install_path", "")
                    g.install_size = int(inst.get("install_size", 0) or 0)
                    g.version = inst.get("version", "")
                    g.platform = "windows" if inst.get("platform", "Windows") == "Windows" else "mac"
                    g.extra["executable"] = inst.get("executable", "")
                    g.extra["launch_parameters"] = inst.get("launch_parameters", "")
                    latest = g.extra.get("latest_version")
                    g.update_available = bool(latest and g.version and latest != g.version)
                else:
                    g.installed = False
                    g.install_path = ""
                games[app] = g
        return list(games.values())

    def check_updates(self, games: list[Game]) -> list[str]:
        return [g.uid for g in games if g.installed and g.update_available]

    # ------------------------------------------------------------ launch
    def prepare_launch(self, game: Game, task_id: str = "") -> LaunchSpec:
        cfg = self.db.get_config(game.uid)
        use_heroic = cfg.get("epic_via_heroic") and game.extra.get("manager", "").startswith("heroic")
        exe = self.legendary()
        if use_heroic or (not exe and self.heroic_available()):
            return LaunchSpec(uri=f"heroic://launch?appName={game.source_id}&runner=legendary",
                              description="Heroic Games Launcher")
        compat = self.services.compat
        if not exe:
            # last resort: run the executable directly (works for DRM-free titles)
            target = Path(game.install_path) / game.extra.get("executable", "")
            return compat.build(game.uid, str(target), shlex.split(game.extra.get("launch_parameters", "")),
                                platform=game.platform or "windows", install_dir=game.install_path,
                                store="epic", game_id=game.source_id)
        runner = compat.runner_for(game.uid, cfg)
        if runner is None:
            raise RunnerMissing("No Proton runner found - download GE-Proton in Settings › Compatibility")
        prefix = compat.prefix_for(game.uid, cfg)
        compat._prepare_prefix(prefix, runner)  # noqa: SLF001
        from nebula.runners.manager import umu_game_id

        umu_id = umu_game_id(self.db, "epic", game.source_id) if runner.kind == "proton" else "umu-0"
        base_cmd, env = compat.runner_env(runner, prefix, game.install_path, "epic", umu_id)
        env.update(compat.tuning_env(cfg, runner))
        wrapper = shlex.join(compat.wrappers(cfg) + base_cmd)
        argv = [exe, "launch", game.source_id, "--no-wine", "--wrapper", wrapper]
        if cfg.get("args"):
            argv += shlex.split(cfg["args"])
        env["LEGENDARY_CONFIG_PATH"] = game.extra.get("config_dir", "")
        return LaunchSpec(argv=argv, env={k: v for k, v in env.items() if v}, cwd=game.install_path,
                          prefix=str(prefix), runner=runner, description=f"{runner.name} via Legendary")

    # ------------------------------------------------------ installation
    def _run_legendary(self, game: Game, args: list[str], ctx: JobContext, config_dir: str = "") -> None:
        exe = self.legendary()
        if not exe:
            raise NotSupported("Legendary is not installed - install it (pip install legendary-gl) or use Heroic")
        cfg_dir = Path(config_dir or game.extra.get("config_dir") or CONFIG_DIRS[0][0])
        proc = subprocess.Popen([exe, *args], env=self._env_for(cfg_dir), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
        total = 0
        tail: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                tail = (tail + [line.strip()])[-15:]
                if ctx.cancelled:
                    proc.terminate()
                    raise Canceled()
                m = DL_SIZE.search(line)
                if m:
                    total = int(float(m.group(1)) * MIB)
                    ctx.set_total(total)
                m = DOWNLOADED.search(line)
                if m and total:
                    ctx.report_external(int(float(m.group(1)) * MIB))
                elif (m := PROGRESS.search(line)) and total:
                    ctx.report_external(int(float(m.group(1)) / 100 * total))
                if "Verification" in line or "verifying" in line.lower():
                    ctx.stage("Verifying")
                elif "Progress" in line:
                    ctx.stage("Downloading")
        finally:
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if proc.returncode not in (0, None):
            raise RuntimeError("Legendary failed: " + " | ".join(t for t in tail[-3:] if t))

    def install_options(self, game: Game, platform: str = "") -> InstallOptions:
        base_dir = Path(self.settings.get("library.install_dir")) / "Epic"
        return InstallOptions(platforms=["windows"], platform="windows", install_dir=str(base_dir / sanitize_dirname(game.title)),
                              method="Legendary (runs with Proton)",
                              notes=["Downloaded by Legendary; launched through Proton by Super Nova."])

    def install(self, game: Game, request: InstallRequest, ctx: JobContext) -> Game:
        base_dir = Path(request.base_dir or Path(self.settings.get("library.install_dir")) / "Epic")
        ctx.stage("Starting Legendary")
        self._run_legendary(game, ["install", game.source_id, "-y", "--base-path", str(base_dir),
                                   "--platform", "Windows", "--skip-sdl"], ctx)
        return game

    def update(self, game: Game, ctx: JobContext, version: str = "") -> Game:
        self._run_legendary(game, ["update", game.source_id, "-y"], ctx)
        game.update_available = False
        return game

    def repair(self, game: Game, ctx: JobContext) -> Game:
        self._run_legendary(game, ["repair", game.source_id, "-y"], ctx)
        return game

    def uninstall(self, game: Game, ctx: JobContext) -> Game:
        ctx.stage("Uninstalling with Legendary")
        self._run_legendary(game, ["uninstall", game.source_id, "-y"], ctx)
        game.installed = False
        game.install_path = ""
        return game

    def store_url(self, game: Game) -> str:
        return game.store_url or "https://store.epicgames.com/"

    def open_platform_client(self) -> None:
        if self.heroic_available():
            xdg_open("heroic://")
        else:
            xdg_open("https://store.epicgames.com/")
