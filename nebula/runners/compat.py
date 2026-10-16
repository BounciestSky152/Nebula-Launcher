"""Compatibility layer: runs Windows games through Proton (default) or Wine.

Proton is located from Nebula's own runner directory (GE-Proton downloads),
Steam's ``compatibilitytools.d``, Steam's ``steamapps/common/Proton*`` and
system packages. When ``umu-run`` (umu-launcher) is available it is used so
Proton runs inside the Steam Linux Runtime exactly like it would in Steam,
including protonfixes; otherwise Proton's own ``proton`` script is invoked
directly with the environment Steam would normally provide.
"""
from __future__ import annotations

import getpass
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nebula import paths
from nebula.core import vdf
from nebula.core.utils import natural_key, which

log = logging.getLogger(__name__)

IGNORED_TOOLS = ("easyanticheat", "battleye", "steam linux runtime", "steamlinuxruntime", "legacyruntime")


@dataclass
class Runner:
    kind: str  # proton | wine
    name: str
    path: str  # proton: directory containing the `proton` script; wine: the wine binary
    origin: str  # nebula | steam | system | lutris

    @property
    def id(self) -> str:
        return f"{self.kind}:{self.path}"

    @property
    def is_ge(self) -> bool:
        return "ge-proton" in self.name.lower() or "proton-ge" in self.name.lower()

    @property
    def removable(self) -> bool:
        return self.origin == "nebula"

    def wine_binary(self) -> str:
        """Wine binary inside a Proton build (for winetricks/wineserver)."""
        if self.kind == "wine":
            return self.path
        for sub in ("files/bin/wine", "dist/bin/wine"):
            p = Path(self.path, sub)
            if p.exists():
                return str(p)
        return ""

    def wineserver_binary(self) -> str:
        w = self.wine_binary()
        if not w:
            return ""
        p = Path(w).with_name("wineserver")
        return str(p) if p.exists() else (which("wineserver") or "")


@dataclass
class LaunchSpec:
    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str = ""
    uri: str = ""  # open via xdg-open instead of spawning argv
    prefix: str = ""
    runner: Runner | None = None
    description: str = ""


class RunnerMissing(RuntimeError):
    pass


# --------------------------------------------------------------- discovery
def steam_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".steam" / "root",
        home / ".steam" / "steam",
        home / ".local" / "share" / "Steam",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
        home / "snap" / "steam" / "common" / ".local" / "share" / "Steam",
    ]
    seen, out = set(), []
    for c in candidates:
        try:
            if not (c / "steamapps").is_dir():
                continue
            real = c.resolve()
        except OSError:
            continue
        if real not in seen:
            seen.add(real)
            out.append(real)
    return out


def steam_library_folders(root: Path) -> list[Path]:
    out = [root]
    lf = root / "steamapps" / "libraryfolders.vdf"
    if lf.exists():
        try:
            data = vdf.load(lf)
        except OSError:
            data = {}
        folders = vdf.get(data, "libraryfolders", default={}) or {}
        for k, v in folders.items():
            path = v.get("path") if isinstance(v, dict) else (v if k.isdigit() else None)
            if path:
                p = Path(path)
                if p.exists() and p.resolve() not in [o.resolve() for o in out]:
                    out.append(p)
    return out


def _proton_name(d: Path) -> str:
    tool = d / "compatibilitytool.vdf"
    if tool.exists():
        try:
            data = vdf.load(tool)
            tools = vdf.get(data, "compatibilitytools", "compat_tools", default={}) or {}
            for _key, info in tools.items():
                if isinstance(info, dict) and info.get("display_name"):
                    return info["display_name"]
        except OSError:
            pass
    ver = d / "version"
    if ver.exists():
        try:
            parts = ver.read_text().split()
            if len(parts) >= 2 and parts[1].lower().startswith("ge-proton"):
                return parts[1]
        except OSError:
            pass
    return d.name


def detect_protons() -> list[Runner]:
    found: dict[str, Runner] = {}

    def add(d: Path, origin: str):
        if not (d / "proton").is_file():
            return
        if any(t in d.name.lower() for t in IGNORED_TOOLS):
            return
        real = str(d.resolve())
        if real in found:
            return
        name = _proton_name(d)
        if origin == "steam" and not name.lower().startswith(("ge-", "proton-ge")) and "(steam)" not in name.lower():
            name = f"{name} (Steam)"
        found[real] = Runner("proton", name, str(d), origin)

    def scan(base: Path, origin: str):
        try:
            entries = sorted(base.iterdir())
        except OSError:
            return
        for d in entries:
            if d.is_dir():
                add(d, origin)

    scan(paths.proton_dir(), "nebula")
    for root in steam_roots():
        scan(root / "compatibilitytools.d", "steam")
        for lib in steam_library_folders(root):
            common = lib / "steamapps" / "common"
            try:
                for d in common.iterdir():
                    if d.name.lower().startswith("proton"):
                        add(d, "steam")
            except OSError:
                pass
    for base in ("/usr/share/steam/compatibilitytools.d", "/usr/local/share/steam/compatibilitytools.d",
                 "/opt/proton"):
        scan(Path(base), "system")
    runners = list(found.values())
    runners.sort(key=_runner_rank, reverse=True)
    return runners


def _runner_rank(r: Runner):
    # GE-Proton first, then newest version; "Experimental" counts as newest Valve build
    name = r.name.lower()
    ge = 1 if r.is_ge else 0
    experimental = 1 if "experimental" in name else 0
    return (ge, experimental, natural_key(r.name))


def detect_wines() -> list[Runner]:
    out: list[Runner] = []
    sys_wine = which("wine")
    if sys_wine:
        out.append(Runner("wine", "System Wine", sys_wine, "system"))
    for base, origin in (
        (paths.wine_dir(), "nebula"),
        (Path.home() / ".local/share/lutris/runners/wine", "lutris"),
        (Path.home() / ".var/app/net.lutris.Lutris/data/lutris/runners/wine", "lutris"),
    ):
        try:
            for d in sorted(base.iterdir(), key=lambda p: natural_key(p.name), reverse=True):
                b = d / "bin" / "wine"
                if b.is_file():
                    out.append(Runner("wine", f"{d.name} ({origin.capitalize()})", str(b), origin))
        except OSError:
            continue
    return out


def find_umu() -> str | None:
    p = which("umu-run")
    if p:
        return p
    for cand in (paths.umu_dir() / "umu" / "umu-run", paths.umu_dir() / "umu-run"):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def all_runners() -> list[Runner]:
    return detect_protons() + detect_wines()


def windows_username(runner: Runner | None) -> str:
    if runner is None or runner.kind == "proton":
        return "steamuser"
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return os.environ.get("USER", "user")


# ------------------------------------------------------------ the layer
WRAPPER_TOOLS = {
    "gamemode": "gamemoderun",
    "mangohud": "mangohud",
    "gamescope": "gamescope",
}


class CompatLayer:
    """Builds launch commands and manages Wine/Proton prefixes."""

    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self._protons: list[Runner] | None = None
        self._wines: list[Runner] | None = None

    # ----------------------------------------------------------- runners
    def refresh(self) -> None:
        self._protons = detect_protons()
        self._wines = detect_wines()

    @property
    def protons(self) -> list[Runner]:
        if self._protons is None:
            self.refresh()
        return list(self._protons or [])

    @property
    def wines(self) -> list[Runner]:
        if self._wines is None:
            self.refresh()
        return list(self._wines or [])

    def find_runner(self, ident: str) -> Runner | None:
        if not ident:
            return None
        for r in self.protons + self.wines:
            if ident in (r.id, r.path, r.name):
                return r
        return None

    def default_proton(self) -> Runner | None:
        chosen = self.find_runner(self.settings.get("compat.default_proton"))
        if chosen and chosen.kind == "proton":
            return chosen
        return self.protons[0] if self.protons else None

    def default_wine(self) -> Runner | None:
        chosen = self.find_runner(self.settings.get("compat.default_wine"))
        if chosen and chosen.kind == "wine":
            return chosen
        return self.wines[0] if self.wines else None

    def config(self, uid: str) -> dict[str, Any]:
        return self.db.get_config(uid)

    def opt(self, cfg: dict, key: str) -> Any:
        """Per-game override, falling back to the global compat.* setting."""
        val = cfg.get(key)
        if val is None or val == "":
            return self.settings.get(f"compat.{key}")
        return val

    def runner_for(self, uid: str, cfg: dict | None = None) -> Runner | None:
        cfg = self.config(uid) if cfg is None else cfg
        kind = cfg.get("runner") or self.settings.get("compat.default_runner") or "proton"
        if kind == "native":
            return None
        if kind == "wine":
            r = self.find_runner(cfg.get("wine", "")) or self.default_wine()
            if r:
                return r
            # fall back to Proton if no Wine is installed
        r = self.find_runner(cfg.get("proton", ""))
        if r and r.kind == "proton":
            return r
        return self.default_proton() or (self.default_wine() if kind != "wine" else None)

    # ----------------------------------------------------------- prefixes
    def prefix_for(self, uid: str, cfg: dict | None = None) -> Path:
        cfg = self.config(uid) if cfg is None else cfg
        if cfg.get("prefix"):
            return Path(cfg["prefix"]).expanduser()
        safe = uid.replace(":", "_").replace("/", "_")
        return paths.prefixes_dir() / safe

    def drive_c(self, uid: str, cfg: dict | None = None) -> Path:
        return self.prefix_for(uid, cfg) / "drive_c"

    def user_dir(self, uid: str, cfg: dict | None = None) -> Path:
        cfg = self.config(uid) if cfg is None else cfg
        return self.drive_c(uid, cfg) / "users" / windows_username(self.runner_for(uid, cfg))

    def _prepare_prefix(self, prefix: Path, runner: Runner) -> None:
        prefix.mkdir(parents=True, exist_ok=True)
        if runner.kind == "proton":
            # Same layout umu uses: STEAM_COMPAT_DATA_PATH/pfx -> STEAM_COMPAT_DATA_PATH,
            # so drive_c always lives directly in the prefix folder.
            pfx = prefix / "pfx"
            if not pfx.exists() and not pfx.is_symlink():
                try:
                    pfx.symlink_to(".", target_is_directory=True)
                except OSError:
                    pass

    def _steam_client_path(self) -> str:
        roots = steam_roots()
        if roots:
            return str(roots[0])
        dummy = paths.runners_dir() / "steam-client"
        dummy.mkdir(parents=True, exist_ok=True)
        return str(dummy)

    # ------------------------------------------------------------ commands
    def runner_env(self, runner: Runner, prefix: Path, install_dir: str = "", store: str = "",
                   umu_id: str = "umu-0") -> tuple[list[str], dict[str, str]]:
        """Return (command prefix, env) to execute a Windows binary with ``runner``."""
        env: dict[str, str] = {}
        if runner.kind == "proton":
            env["WINEPREFIX"] = str(prefix)
            env["STEAM_COMPAT_DATA_PATH"] = str(prefix)
            env["STEAM_COMPAT_CLIENT_INSTALL_PATH"] = self._steam_client_path()
            if install_dir:
                env["STEAM_COMPAT_INSTALL_PATH"] = install_dir
            env["PROTONPATH"] = runner.path
            env["GAMEID"] = umu_id or "umu-0"
            if store:
                env["STORE"] = {"gog": "gog", "epic": "egs"}.get(store, store)
            umu = find_umu() if self.settings.get("compat.use_umu") else None
            if umu:
                return [umu], env
            env.setdefault("SteamAppId", "0")
            env.setdefault("SteamGameId", "0")
            return [str(Path(runner.path) / "proton"), "waitforexitandrun"], env
        env["WINEPREFIX"] = str(prefix)
        env["WINEDLLOVERRIDES"] = "winemenubuilder.exe=d"
        env.setdefault("WINEDEBUG", "-all")
        return [runner.path], env

    def tuning_env(self, cfg: dict, runner: Runner | None) -> dict[str, str]:
        env: dict[str, str] = {}
        if runner is not None:
            if not self.opt(cfg, "esync"):
                env["PROTON_NO_ESYNC" if runner.kind == "proton" else "WINEESYNC"] = "1" if runner.kind == "proton" else "0"
            if not self.opt(cfg, "fsync"):
                env["PROTON_NO_FSYNC" if runner.kind == "proton" else "WINEFSYNC"] = "1" if runner.kind == "proton" else "0"
            if self.opt(cfg, "dxvk_hud"):
                env["DXVK_HUD"] = "fps"
            if self.opt(cfg, "wayland") and runner.kind == "proton":
                env["PROTON_ENABLE_WAYLAND"] = "1"
            if self.opt(cfg, "nvapi") and runner.kind == "proton":
                env["PROTON_ENABLE_NVAPI"] = "1"
                env["DXVK_ENABLE_NVAPI"] = "1"
            fps = cfg.get("fps_limit")
            if fps:
                env["DXVK_FRAME_RATE"] = str(int(fps))
        for k, v in (cfg.get("env") or {}).items():
            if k:
                env[str(k)] = os.path.expandvars(str(v))
        return env

    def wrappers(self, cfg: dict) -> list[str]:
        cmd: list[str] = []
        if self.opt(cfg, "gamescope") and which("gamescope"):
            cmd += ["gamescope", *shlex.split(self.opt(cfg, "gamescope_args") or ""), "--"]
        if self.opt(cfg, "gamemode") and which("gamemoderun"):
            cmd.append("gamemoderun")
        if self.opt(cfg, "mangohud") and which("mangohud"):
            cmd.append("mangohud")
        if cfg.get("wrapper"):
            cmd += shlex.split(cfg["wrapper"])
        return cmd

    def build(
        self,
        uid: str,
        exe: str,
        args: list[str] | None = None,
        cwd: str = "",
        platform: str = "windows",
        install_dir: str = "",
        store: str = "",
        game_id: str = "",
    ) -> LaunchSpec:
        """Build the full launch command for a game binary."""
        cfg = self.config(uid)
        args = list(args or [])
        if cfg.get("exe_override"):
            exe = cfg["exe_override"]
            cwd = str(Path(exe).parent)
        if cfg.get("args"):
            args += shlex.split(cfg["args"])
        cwd = cwd or str(Path(exe).parent)
        env = {}
        wrappers = self.wrappers(cfg)
        forced_native = cfg.get("runner") == "native"
        if platform != "windows" or forced_native:
            if exe.endswith(".sh") and not os.access(exe, os.X_OK):
                argv = wrappers + ["/bin/sh", exe, *args]
            else:
                argv = wrappers + [exe, *args]
            env.update(self.tuning_env(cfg, None))
            return LaunchSpec(argv=argv, env=env, cwd=cwd, description="Native")
        runner = self.runner_for(uid, cfg)
        if runner is None:
            raise RunnerMissing(
                "No Proton or Wine runner was found. Open Settings › Compatibility and "
                "download GE-Proton (one click), or install Steam's Proton."
            )
        prefix = self.prefix_for(uid, cfg)
        self._prepare_prefix(prefix, runner)
        umu_id = "umu-0"
        if runner.kind == "proton" and store and game_id:
            from nebula.runners.manager import umu_game_id

            umu_id = f"umu-{game_id}" if store == "steam" else umu_game_id(self.db, store, game_id)
        base, renv = self.runner_env(runner, prefix, install_dir, store, umu_id)
        env.update(renv)
        env.update(self.tuning_env(cfg, runner))
        argv = wrappers + base + [exe, *args]
        desc = runner.name + (" via umu" if base and base[0].endswith("umu-run") else "")
        return LaunchSpec(argv=argv, env=env, cwd=cwd, prefix=str(prefix), runner=runner, description=desc)

    # --------------------------------------------------------- prefix tools
    def run_in_prefix(self, uid: str, argv: list[str], cwd: str | None = None, wait: bool = True,
                      timeout: float | None = None, extra_env: dict | None = None) -> int:
        """Run a Windows program (installer, redistributable, tool) in the game's prefix."""
        cfg = self.config(uid)
        runner = self.runner_for(uid, cfg)
        if runner is None:
            raise RunnerMissing("No Proton/Wine runner available")
        prefix = self.prefix_for(uid, cfg)
        self._prepare_prefix(prefix, runner)
        base, env = self.runner_env(runner, prefix)
        if runner.kind == "proton" and base[-1] == "waitforexitandrun":
            base = base[:-1] + ["run"]
        full_env = os.environ.copy()
        full_env.update(env)
        full_env.update(extra_env or {})
        cmd = base + argv
        log.info("Running in prefix %s: %s", prefix, shlex.join(cmd))
        proc = subprocess.Popen(cmd, env=full_env, cwd=cwd or str(prefix), stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        if not wait:
            return 0
        try:
            return proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            return -1

    def winecfg(self, uid: str) -> None:
        self.run_in_prefix(uid, ["winecfg"], wait=False)

    def winetricks(self, uid: str, verbs: list[str] | None = None) -> None:
        cfg = self.config(uid)
        runner = self.runner_for(uid, cfg)
        if runner is None:
            raise RunnerMissing("No Proton/Wine runner available")
        prefix = self.prefix_for(uid, cfg)
        self._prepare_prefix(prefix, runner)
        umu = find_umu() if self.settings.get("compat.use_umu") else None
        env = os.environ.copy()
        if runner.kind == "proton" and umu:
            _, renv = self.runner_env(runner, prefix)
            env.update(renv)
            cmd = [umu, "winetricks", *(verbs or ["--gui"])]
        else:
            wt = which("winetricks")
            if not wt:
                raise RunnerMissing("winetricks is not installed (install it with your package manager)")
            env["WINEPREFIX"] = str(prefix)
            env["WINE"] = runner.wine_binary() or "wine"
            env["WINESERVER"] = runner.wineserver_binary() or "wineserver"
            cmd = [wt, *(verbs or ["--gui"])]
        subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def kill_prefix(self, uid: str) -> None:
        cfg = self.config(uid)
        runner = self.runner_for(uid, cfg)
        if runner is None:
            return
        ws = runner.wineserver_binary()
        if not ws:
            return
        env = os.environ.copy()
        env["WINEPREFIX"] = str(self.prefix_for(uid, cfg))
        try:
            subprocess.run([ws, "-k"], env=env, timeout=15, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            pass

    def delete_prefix(self, uid: str) -> None:
        from nebula.core.utils import safe_rmtree

        prefix = self.prefix_for(uid)
        if prefix.exists() and paths.prefixes_dir() in prefix.resolve().parents:
            safe_rmtree(prefix)


def native_substitute(exe: str, args: list[str], cwd: str, settings) -> tuple[list[str], str] | None:
    """Replace GOG's bundled Windows DOSBox/ScummVM with native Linux builds."""
    low = os.path.basename(exe).lower()
    if low == "dosbox.exe" and settings.get("compat.native_dosbox"):
        native = which("dosbox-staging", "dosbox-x", "dosbox")
        if native:
            fixed = [a.replace("\\", "/") for a in args]
            return [native, *fixed], cwd
    if low == "scummvm.exe" and settings.get("compat.native_scummvm"):
        native = which("scummvm")
        if native:
            fixed = [a.replace("\\", "/") for a in args]
            # point the bundled ScummVM ini at the Linux install path
            try:
                idx = fixed.index("-c")
                ini = Path(cwd, fixed[idx + 1])
                if ini.exists() and fixed:
                    import configparser

                    cp = configparser.ConfigParser()
                    cp.read(ini)
                    section = fixed[-1]
                    if cp.has_section(section):
                        cp.set(section, "path", cwd)
                        with open(ini, "w") as fh:
                            cp.write(fh)
            except (ValueError, IndexError, OSError):
                pass
            return [native, *fixed], cwd
    return None


def copy_runner_env_to(env: dict[str, str]) -> dict[str, str]:
    full = os.environ.copy()
    full.update(env)
    return full


def is_executable_file(p: str | Path) -> bool:
    p = Path(p)
    return p.is_file() and os.access(p, os.X_OK)


def remove_runner(runner: Runner) -> None:
    if not runner.removable:
        raise ValueError("Only runners downloaded by Nebula can be removed here")
    target = Path(runner.path if runner.kind == "proton" else Path(runner.path).parent.parent)
    if paths.runners_dir().resolve() in target.resolve().parents:
        shutil.rmtree(target)
