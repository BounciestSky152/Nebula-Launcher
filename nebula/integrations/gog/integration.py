"""GOG integration: library, Galaxy-style installs/updates, launching through Proton, cloud saves,
achievements, play time, presence, friends and the store."""
from __future__ import annotations

import json
import logging
import re
import shlex
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from nebula import __version__, paths
from nebula.core.http import new_session
from nebula.core.jobs import JobContext
from nebula.core.models import Achievement, Friend, Game, GameDetails, LaunchTask
from nebula.core.utils import case_insensitive_path, dir_size, safe_rmtree, sanitize_dirname
from nebula.integrations import base
from nebula.integrations.base import CloudSyncResult, InstallOptions, InstallRequest, Integration
from nebula.integrations.gog import content, offline
from nebula.integrations.gog.api import ApiError, GogApi, gamesdb_image, https, localized, strip_html
from nebula.integrations.gog.auth import LOGIN_URL, SUCCESS_PREFIX, AuthError, GogAuth
from nebula.integrations.gog.cloud import CloudSaves
from nebula.runners.compat import LaunchSpec, native_substitute

log = logging.getLogger(__name__)

INSTALLS_KEY = "gog.installs"
VERSIONS_TTL = 600  # seconds a fetched build / installer list is reused
SUMMARY_MAX_AGE = 3600  # rewrite the saved "newest version" summary at least this often

INSTALLER_NOTES = {
    "linux": "GOG only offers the newest Linux installer, so older versions and beta channels can't be installed "
             "for the Linux version. They're only offered for Windows (GOG Galaxy builds), which Nebula can run "
             "with Proton.",
    "windows": "This copy came from GOG's offline installer, which only exists for the newest version. To choose "
               "older versions or beta channels, uninstall it and install it again from Nebula. Nebula then uses "
               "GOG Galaxy builds when GOG has them.",
}


def _split_args(text: str) -> list[str]:
    """Split Windows-style argument strings without eating backslashes."""
    if not text:
        return []
    lex = shlex.shlex(text, posix=True)
    lex.whitespace_split = True
    lex.escape = ""
    try:
        return list(lex)
    except ValueError:
        return text.split()


def _norm_version(v: str) -> str:
    """Installer version without GOG's "(gog-N)" repack suffix, which gameinfo files leave out."""
    return re.sub(r"\s*\(gog-\d+\)\s*$", "", v.strip(), flags=re.I).lower()


def _iso_date(value) -> str:
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d")
    if isinstance(value, dict):
        value = value.get("date", "")
    s = str(value)
    s = s.replace(".", "-")
    return s[:10]


class GogIntegration(Integration):
    id = "gog"
    name = "GOG"
    color = "#a35dd8"
    letter = "G"
    description = "Your GOG.COM library: install, update, roll back, cloud saves, achievements and friends."
    capabilities = frozenset({
        base.LIBRARY, base.INSTALL, base.UNINSTALL, base.LAUNCH, base.UPDATE, base.REPAIR, base.VERSIONS,
        base.DLC, base.ACHIEVEMENTS, base.FRIENDS, base.PLAYTIME, base.CLOUD_SAVES, base.STORE, base.IMPORT,
        base.WISHLIST,
    })
    login_url = LOGIN_URL
    success_prefix = SUCCESS_PREFIX

    def __init__(self, services):
        super().__init__(services)
        self.session = new_session()
        self.dl_session = new_session(pool=32)
        self.auth = GogAuth(self.session)
        self.api = GogApi(self.auth, self.session)
        self.cloud = CloudSaves(self.api, services.compat, services.db)
        self._owned: set[str] | None = None
        self._wishlist: set[str] | None = None
        self._presence_game: str | None = None
        # build / installer lists: key -> (monotonic time, wall time, items)
        self._vcache: dict[tuple, tuple[float, float, list]] = {}
        self._vlocks: dict[tuple, threading.Lock] = {}
        self._vmeta = threading.Lock()
        self._rec_lock = threading.RLock()  # install records are rewritten by jobs and version checks

    # ------------------------------------------------------------ account
    def connected(self) -> bool:
        return self.auth.logged_in

    def account(self) -> dict:
        return self.db.kv_get("gog.user", {}) or {}

    def account_name(self) -> str:
        return self.account().get("username", "")

    def avatar_url(self) -> str:
        return self.account().get("avatar", "")

    def status_text(self) -> str:
        return f"Signed in as {self.account_name()}" if self.connected() else "Not signed in"

    def connect(self, code: str = "", **_kw) -> None:
        self.auth.login_with_code(code)
        self.refresh_account()

    def refresh_account(self) -> dict:
        info = {}
        try:
            data = self.api.user_data()
            info["username"] = data.get("username", "")
            info["user_id"] = str(data.get("galaxyUserId") or data.get("userId") or self.auth.user_id)
            avatar = data.get("avatar") or ""
            if avatar:
                info["avatar"] = https(avatar) + ("_avm2.jpg" if not avatar.endswith(".jpg") else "")
        except (ApiError, AuthError, OSError) as exc:
            log.warning("GOG userData failed: %s", exc)
        try:
            prof = self.api.user_profile(self.auth.user_id)
            av = prof.get("avatar") or {}
            if isinstance(av, dict):
                info["avatar"] = av.get("medium_2x") or av.get("medium") or info.get("avatar", "")
            info.setdefault("username", prof.get("username", ""))
        except Exception as exc:  # noqa: BLE001
            log.debug("GOG profile failed: %s", exc)
        if info:
            self.db.kv_set("gog.user", info)
        return info

    def disconnect(self) -> None:
        try:
            self.api.delete_presence()
        except Exception:  # noqa: BLE001
            pass
        self.auth.logout()
        self.db.kv_set("gog.user", {})
        self._owned = None
        self._wishlist = None
        self.invalidate_versions()

    # ------------------------------------------------------- install book
    def installs(self) -> dict:
        return self.db.kv_get(INSTALLS_KEY, {}) or {}

    def install_record(self, product_id: str) -> dict | None:
        return self.installs().get(str(product_id))

    def _set_record(self, product_id: str, rec: dict | None) -> None:
        with self._rec_lock:
            data = self.installs()
            if rec is None:
                data.pop(str(product_id), None)
            else:
                data[str(product_id)] = rec
            self.db.kv_set(INSTALLS_KEY, data)

    def _patch_record(self, product_id: str, expect_build: str, expect_version=None, **fields) -> bool:
        """Merge ``fields`` into an install record, unless a job rewrote it meanwhile (compare-and-set on the
        build id, and on the version when ``expect_version`` is given: offline installs have no build id)."""
        with self._rec_lock:
            data = self.installs()
            rec = data.get(str(product_id))
            if not rec or str(rec.get("build_id") or "") != str(expect_build or ""):
                return False
            if expect_version is not None and rec.get("version", "") != expect_version:
                return False
            rec.update(fields)
            self.db.kv_set(INSTALLS_KEY, data)
            return True

    @staticmethod
    def _version_source(rec: dict) -> tuple[str, bool]:
        """(installed version, read from the Linux ``gameinfo`` file). goggame info files store their own
        format version (an int) in "version", so only non-empty strings count as a game version."""
        v = rec.get("version")
        if isinstance(v, str) and v.strip():
            return v.strip(), False
        if rec.get("platform") == "linux" and rec.get("install_path"):
            # GOG's Linux installers write <install>/gameinfo: title, version, ... (one per line)
            try:
                lines = (Path(rec["install_path"]) / "gameinfo").read_text(errors="replace").splitlines()
            except OSError:
                lines = []
            if len(lines) > 1 and lines[1].strip():
                return lines[1].strip(), True
        return "", False

    def _installed_version(self, rec: dict) -> str:
        return self._version_source(rec)[0]

    def _apply_record(self, game: Game, rec: dict | None) -> None:
        if rec and Path(rec.get("install_path", "")).is_dir():
            game.installed = True
            game.install_path = rec["install_path"]
            game.platform = rec.get("platform", "windows")
            game.version = self._installed_version(rec)
            game.build_id = str(rec.get("build_id", ""))
            game.install_size = int(rec.get("size", 0))
            game.extra["install_method"] = rec.get("method", "")
            game.extra["language"] = rec.get("language", "")
            game.extra["installed_dlcs"] = rec.get("dlcs", [])
        else:
            game.installed = False
            game.install_path = ""
            game.update_available = False
            game.platform = ""

    def owned_ids(self, refresh: bool = False) -> set[str]:
        if self._owned is None or refresh:
            try:
                self._owned = self.api.owned_ids()
                self.db.kv_set("gog.owned", sorted(self._owned))
            except Exception:  # noqa: BLE001
                self._owned = set(self.db.kv_get("gog.owned", []) or [])
        return self._owned

    # ------------------------------------------------------------ library
    def fetch_library(self) -> list[Game]:
        if not self.connected():
            return []
        self._import_heroic()
        products = self.api.library()
        self.owned_ids(refresh=True)
        prev = {g.source_id: g for g in self.services.library.games_for_source(self.id)}
        records = self.installs()
        games = []
        for p in products:
            pid = str(p.get("id"))
            g = prev.get(pid) or Game(self.id, pid, p.get("title", pid), added_at=time.time())
            g.title = p.get("title") or g.title
            if p.get("image"):
                g.horizontal = https(p["image"]) + ".jpg"
            works = p.get("worksOn") or {}
            g.os_support = [o for o, k in (("windows", "Windows"), ("linux", "Linux"), ("mac", "Mac")) if works.get(k)]
            url = p.get("url") or ""
            g.store_url = ("https://www.gog.com" + url) if url.startswith("/") else url
            if not g.genres and p.get("category"):
                g.genres = [p["category"]]
            if not g.release_date:
                g.release_date = _iso_date(p.get("releaseDate"))
            g.extra["slug"] = p.get("slug", "")
            g.extra["dlc_count"] = p.get("dlcCount", 0)
            g.extra["gog_tags"] = [t.get("name") for t in p.get("tags", []) if isinstance(t, dict)]
            self._apply_record(g, records.get(pid))
            games.append(g)
        # installed games that are not (or no longer) in the account library
        known = {g.source_id for g in games}
        for pid, rec in records.items():
            if pid not in known and Path(rec.get("install_path", "")).is_dir():
                g = prev.get(pid) or Game(self.id, pid, rec.get("title", pid), added_at=time.time())
                self._apply_record(g, rec)
                games.append(g)
        return games

    def enrich(self, games: list[Game]) -> list[Game]:
        todo = [g for g in games if not g.extra.get("gamesdb") or time.time() - g.extra["gamesdb"] > 30 * 86400]
        changed: list[Game] = []

        def one(g: Game):
            try:
                data = self.api.gamesdb(g.source_id, "gog")
            except Exception:  # noqa: BLE001
                g.extra["gamesdb"] = time.time()
                return g
            game = data.get("game") or {}
            g.cover = gamesdb_image(game.get("vertical_cover")) or g.cover
            g.hero = gamesdb_image(game.get("background")) or g.hero
            g.logo = gamesdb_image(game.get("logo")) or g.logo
            g.icon = gamesdb_image(game.get("square_icon")) or g.icon
            genres = [localized(x.get("name")) for x in game.get("genres", []) if isinstance(x, dict)]
            if genres:
                g.genres = genres
            devs = [d.get("name", "") for d in game.get("developers", []) if isinstance(d, dict)]
            pubs = [d.get("name", "") for d in game.get("publishers", []) if isinstance(d, dict)]
            g.developer = ", ".join(x for x in devs if x) or g.developer
            g.publisher = ", ".join(x for x in pubs if x) or g.publisher
            g.release_date = _iso_date(game.get("first_release_date")) or g.release_date
            g.description = localized(game.get("summary")) or g.description
            try:
                g.critic_score = float(game.get("aggregated_rating") or 0)
            except (TypeError, ValueError):
                pass
            g.extra["screenshots"] = [gamesdb_image(s) for s in game.get("screenshots", [])][:12]
            g.extra["gamesdb"] = time.time()
            return g

        with ThreadPoolExecutor(max_workers=8) as pool:
            for g in pool.map(one, todo):
                changed.append(g)
        return changed

    def check_updates(self, games: list[Game]) -> list[str]:
        out = []
        for g in games:
            if not g.installed:
                continue
            try:
                info = self.version_info(g, refresh=True)
            except Exception as exc:  # noqa: BLE001
                log.debug("update check failed for %s: %s", g.title, exc)
                continue
            if info is None or info.error:
                continue  # keep the previous flag: a failed check says nothing about updates
            # the check may have healed the record's version (imports)
            g.version = self._installed_version(self.install_record(g.source_id) or {}) or g.version
            pinned = bool(self.db.get_config(g.uid).get("pin_version"))  # may have changed during the fetch
            g.update_available = info.update_available and not pinned  # pinned games are still checked
            if g.update_available:
                out.append(g.uid)
        return out

    # ------------------------------------------------------------- import
    def _import_heroic(self) -> None:
        """Pick up GOG games previously installed with Heroic Games Launcher."""
        found: dict[str, dict] = {}
        for cfg_root in (Path.home() / ".config/heroic", Path.home() / ".var/app/com.heroicgameslauncher.hgl/config/heroic"):
            f = cfg_root / "gog_store" / "installed.json"
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            for item in data.get("installed", []):
                pid = str(item.get("appName", ""))
                path = item.get("install_path", "")
                if not pid or pid in found or not Path(path).is_dir():
                    continue
                found[pid] = {
                    "install_path": path,
                    "platform": item.get("platform", "windows"),
                    "version": item.get("version", ""),
                    "build_id": item.get("buildId", ""),
                    "language": item.get("language", "en-US"),
                    "dlcs": item.get("installedDLCs", []),
                    "method": "imported-gen2" if item.get("platform") == "windows" else "imported",
                    "size": 0,
                    "source": "heroic",
                }
                if "branch" in item:  # otherwise the channel of the matched build is adopted on the first check
                    found[pid]["branch"] = item["branch"] or None
        if not found:
            return
        with self._rec_lock:
            records = self.installs()
            new = {k: v for k, v in found.items() if k not in records}
            if new:
                records.update(new)
                self.db.kv_set(INSTALLS_KEY, records)

    def import_path(self, path: str) -> list[Game]:
        """Scan a folder for already-installed GOG games (Galaxy's "Scan and import folders")."""
        found: dict[str, dict] = {}
        root = Path(path)
        candidates = [root] + [p for p in root.glob("*") if p.is_dir()] + [p for p in root.glob("*/*") if p.is_dir()]
        for d in candidates:
            for info in list(d.glob("goggame-*.info")) + list(d.glob("game/goggame-*.info")):
                try:
                    data = json.loads(info.read_text(errors="replace"))
                except (OSError, ValueError):
                    continue
                pid = str(data.get("gameId") or info.stem.split("-", 1)[1])
                if data.get("rootGameId") and str(data["rootGameId"]) != pid:
                    continue  # DLC info file
                if pid in found:
                    continue
                # native Linux installs keep the info file in <install>/game/ next to <install>/start.sh
                is_linux = info.parent.name.lower() == "game" and (info.parent.parent / "start.sh").exists()
                install_dir = info.parent.parent if is_linux else info.parent
                # goggame "version" is the info file's format version (1), not the game's: the game version
                # comes from the matched build (Windows) or <install>/gameinfo (Linux). No "branch" key, so
                # the first version check adopts the channel of the matched build.
                found[pid] = {
                    "install_path": str(install_dir),
                    "platform": "linux" if is_linux else "windows",
                    "version": "",
                    "build_id": str(data.get("buildId", "")),
                    "language": (data.get("language") or "English"),
                    "dlcs": [],
                    "method": "imported-gen2" if data.get("buildId") and not is_linux else "imported",
                    "size": dir_size(install_dir),
                    "title": data.get("name", pid),
                }
        if not found:
            return []
        with self._rec_lock:
            records = self.installs()
            records.update({k: v for k, v in found.items() if k not in records})
            self.db.kv_set(INSTALLS_KEY, records)
        out = []
        existing = {g.source_id: g for g in self.services.library.games_for_source(self.id)}
        for pid, rec in found.items():
            g = existing.get(pid) or Game(self.id, pid, rec.get("title", pid), added_at=time.time())
            self._apply_record(g, records[pid])
            out.append(g)
        return out

    # ------------------------------------------------------------ launch
    def _info_file(self, game: Game) -> dict | None:
        base = Path(game.install_path)
        for cand in (base / f"goggame-{game.source_id}.info", base / "game" / f"goggame-{game.source_id}.info"):
            p = case_insensitive_path(cand)
            if p.is_file():
                try:
                    return json.loads(p.read_text(errors="replace"))
                except ValueError:
                    return None
        return None

    def launch_tasks(self, game: Game) -> list[LaunchTask]:
        if not game.installed:
            return []
        if game.platform == "linux":
            return [LaunchTask("", "Play", "game", True)]
        info = self._info_file(game) or {}
        tasks = []
        for i, t in enumerate(info.get("playTasks", [])):
            kind = "url" if t.get("type") == "URLTask" else (t.get("category") or "game")
            if kind == "launcher":
                kind = "tool"
            tasks.append(LaunchTask(str(i), t.get("name") or ("Play" if t.get("isPrimary") else f"Task {i + 1}"),
                                    kind, bool(t.get("isPrimary"))))
        if not tasks:
            tasks.append(LaunchTask("", "Play", "game", True))
        return tasks

    def prepare_launch(self, game: Game, task_id: str = "") -> LaunchSpec:
        compat = self.services.compat
        base_dir = Path(game.install_path)
        if game.platform == "linux":
            start = base_dir / "start.sh"
            if not start.exists():
                raise FileNotFoundError(f"{start} is missing - try repairing the game")
            return compat.build(game.uid, str(start), [], cwd=str(base_dir), platform="linux")
        info = self._info_file(game)
        if not info:
            raise FileNotFoundError("goggame info file missing - try Verify / Repair")
        tasks = info.get("playTasks", [])
        task = None
        if task_id != "" and task_id.isdigit() and int(task_id) < len(tasks):
            task = tasks[int(task_id)]
        if task is None:
            task = next((t for t in tasks if t.get("isPrimary")), tasks[0] if tasks else None)
        if task is None:
            raise FileNotFoundError("This game does not define a launch task")
        if task.get("type") == "URLTask":
            return LaunchSpec(uri=task.get("link", ""))
        exe = case_insensitive_path(base_dir / task["path"].replace("\\", "/"))
        work = case_insensitive_path(base_dir / (task.get("workingDir") or "").replace("\\", "/")) \
            if task.get("workingDir") else exe.parent
        args = _split_args(task.get("arguments", ""))
        if exe.suffix.lower() in (".pdf", ".txt", ".html", ".htm"):
            return LaunchSpec(uri=str(exe))
        cfg = self.db.get_config(game.uid)
        if cfg.get("runner") not in ("wine",) and not cfg.get("exe_override"):
            native = native_substitute(str(exe), args, str(work), self.settings)
            if native:
                argv, cwd = native
                spec = compat.build(game.uid, argv[0], argv[1:], cwd=cwd, platform="linux")
                spec.description = f"Native {Path(argv[0]).name}"
                return spec
        return compat.build(game.uid, str(exe), args, cwd=str(work), platform="windows",
                            install_dir=str(base_dir), store="gog", game_id=game.source_id)

    def process_hints(self, game: Game) -> dict:
        return {"paths": [game.install_path] if game.installed else []}

    def before_launch(self, game: Game, status) -> None:
        if game.platform != "windows":
            return
        cfg = self.db.get_config(game.uid)
        if cfg.get("runner") == "native":
            return
        rec = self.install_record(game.source_id) or {}
        done = set(cfg.get("redists_done", []))
        pending = [r for r in rec.get("redists", []) if r["id"] not in done]
        for r in pending:
            if not Path(r["exe"]).exists():
                done.add(r["id"])
                continue
            status(f"Installing {r['name']}…")
            try:
                self.services.compat.run_in_prefix(game.uid, [r["exe"], *_split_args(r.get("args", ""))],
                                                   cwd=str(Path(r["exe"]).parent), timeout=1200)
            except Exception as exc:  # noqa: BLE001
                log.warning("Redistributable %s failed: %s", r["id"], exc)
            done.add(r["id"])
            self.db.update_config(game.uid, redists_done=sorted(done))

    def on_session_start(self, game: Game) -> None:
        self._presence_game = game.source_id
        self.heartbeat()

    def on_session_end(self, game: Game, start: float, end: float) -> None:
        self._presence_game = None
        self.heartbeat()
        minutes = int((end - start) // 60)
        if minutes >= 1 and self.connected() and self.settings.get("gog.sync_playtime"):
            try:
                self.api.post_session(game.source_id, int(start), minutes)
            except Exception as exc:  # noqa: BLE001
                log.info("Could not post GOG play session: %s", exc)

    def heartbeat(self) -> None:
        if not self.connected() or not self.settings.get("gog.presence"):
            return
        try:
            self.api.set_presence(self._presence_game, __version__)
        except Exception as exc:  # noqa: BLE001
            log.debug("presence failed: %s", exc)

    def go_offline(self) -> None:
        if self.connected():
            self.api.delete_presence()

    # ------------------------------------------------------- installation
    def default_platform(self, game: Game) -> str:
        if "linux" in game.os_support and self.settings.get("downloads.prefer_native"):
            return "linux"
        return "windows" if "windows" in game.os_support or not game.os_support else game.os_support[0]

    def install_options(self, game: Game, platform: str = "") -> InstallOptions:
        product = self.api.product(game.source_id, expand=("downloads", "expanded_dlcs"))
        owned = self.owned_ids()
        try:
            _, win_builds, _ = self._builds(game.source_id, "windows")
            builds_error = None
        except (ApiError, OSError) as exc:  # only matters if the Windows version is installed
            win_builds, builds_error = [], exc
        platforms = []
        if offline.installers_for(product, "linux"):
            platforms.append("linux")
        if win_builds or offline.installers_for(product, "windows"):
            platforms.append("windows")
        if not platforms:
            raise RuntimeError("This product has no Linux or Windows downloads")
        if self.settings.get("downloads.prefer_native"):
            platforms.sort(key=lambda p: 0 if p == "linux" else 1)
        else:
            platforms.sort(key=lambda p: 0 if p == "windows" else 1)
        platform = platform if platform in platforms else platforms[0]
        if platform == "windows" and builds_error is not None:
            raise RuntimeError(f"Couldn't get the build list from GOG: {builds_error}") from builds_error
        lang = self.settings.get("general.language") or "en-US"
        opts = InstallOptions(platforms=platforms, platform=platform)
        title_dir = sanitize_dirname(game.title)
        if platform == "windows" and win_builds:
            newest = content.Gen2.latest(win_builds, None) or win_builds[0]  # a newer beta is never the default
            info = content.Gen2(self.api, game.source_id).build_info(newest)
            opts.method = "GOG Galaxy content system (Proton)"
            langs = info.languages() or ["en-US"]
            opts.languages = [(c, content.lang_label(c)) for c in langs]
            opts.language = next((c for c in langs if content.lang_matches([c], lang)), langs[0])
            opts.dlcs = [{"id": p["id"], "title": p["name"], "size": info.dlc_size(p["id"], opts.language)}
                         for p in info.dlc_products() if p["id"] in owned]
            opts.download_size, opts.disk_size = info.sizes(opts.language, [d["id"] for d in opts.dlcs])
            # Stable builds first (newest first), then the beta channels' builds
            ordered = [b for b in win_builds if not b.get("branch")] + [b for b in win_builds if b.get("branch")]
            opts.versions = [{"id": str(b["build_id"]),
                              "label": (b.get("version_name") or f"Build {b['build_id']}")
                              + (" (newest)" if b is newest else ""),
                              "date": (b.get("date_published") or "")[:10],
                              "branch": content.branch_label(b["branch"]) if b.get("branch") else ""}
                             for b in ordered]
            opts.latest = opts.version = str(newest["build_id"])
            title_dir = sanitize_dirname(info.install_directory or game.title)
            if info.dependencies and self.settings.get("downloads.install_dependencies"):
                opts.notes.append("Required redistributables (" + ", ".join(info.dependencies[:4]) +
                                  ") will be installed into the Proton prefix on first launch.")
        else:
            installers = offline.installers_for(product, platform)
            opts.method = "Native Linux installer" if platform == "linux" else "Offline installer (innoextract / Proton)"
            opts.languages = offline.installer_languages(installers)
            inst = offline.pick_installer(installers, lang)
            opts.language = inst.get("language", "en") if inst else "en"
            opts.download_size = int(inst.get("total_size", 0)) if inst else 0
            opts.disk_size = int(opts.download_size * 1.6)
            opts.versions = [{"id": "", "label": inst.get("version") or "Latest", "date": ""}] if inst else []
            for dlc in product.get("expanded_dlcs", []) or []:
                did = str(dlc.get("id"))
                if did in owned and offline.installers_for(dlc, platform):
                    di = offline.pick_installer(offline.installers_for(dlc, platform), opts.language)
                    opts.dlcs.append({"id": did, "title": dlc.get("title", did), "size": int(di.get("total_size", 0))})
            if platform == "windows":
                opts.notes.append("This game is only available as a Windows offline installer; it will be "
                                  "unpacked with innoextract when available, otherwise installed silently with Proton.")
        if platform == "windows":
            opts.notes.append("Windows build: runs through Proton automatically.")
        opts.install_dir = str(Path(self.settings.get("library.install_dir")) / title_dir)
        return opts

    def install(self, game: Game, request: InstallRequest, ctx: JobContext) -> Game:
        platform = request.platform or self.default_platform(game)
        base_dir = Path(request.base_dir or self.settings.get("library.install_dir"))
        if platform == "windows":
            try:
                _, builds, _ = self._builds(game.source_id, "windows")
            except (ApiError, OSError) as exc:
                # don't quietly fall back to the offline installer just because GOG could not be reached
                raise RuntimeError(f"Couldn't get the build list from GOG: {exc}") from exc
            if builds:
                return self._gen2(game, ctx, "install", base_dir=base_dir, language=request.language,
                                  dlcs=request.dlcs, build_id=request.version)
        return self._offline(game, ctx, platform, base_dir=base_dir, language=request.language, dlcs=request.dlcs)

    def update(self, game: Game, ctx: JobContext, version: str = "") -> Game:
        rec = self.install_record(game.source_id) or {}
        if rec.get("method", "") in ("gen2", "imported-gen2"):
            if version.startswith(base.BRANCH_TARGET):
                return self._gen2(game, ctx, "update", to_branch=version[len(base.BRANCH_TARGET):] or None,
                                  switch_branch=True)
            return self._gen2(game, ctx, "update", build_id=version)
        if version:
            raise base.NotSupported("GOG only offers its newest installer for this copy, so it can only be updated "
                                    "to the newest version.")
        return self._offline(game, ctx, rec.get("platform", "linux"), language=rec.get("language", ""),
                             dlcs=rec.get("dlcs", []), existing=Path(game.install_path))

    def repair(self, game: Game, ctx: JobContext) -> Game:
        rec = self.install_record(game.source_id) or {}
        if rec.get("method", "").endswith("gen2"):
            return self._gen2(game, ctx, "repair", build_id=str(rec.get("build_id", "")))
        return self._offline(game, ctx, rec.get("platform", "linux"), language=rec.get("language", ""),
                             dlcs=rec.get("dlcs", []), existing=Path(game.install_path))

    def install_dlcs(self, game: Game, dlc_ids: list[str], ctx: JobContext) -> Game:
        rec = self.install_record(game.source_id) or {}
        dlcs = sorted(set(rec.get("dlcs_wanted", [])) | set(rec.get("dlcs", [])) | set(dlc_ids))
        if rec.get("method", "").endswith("gen2"):
            return self._gen2(game, ctx, "update", build_id=str(rec.get("build_id", "")), dlcs=dlcs, same_build=True)
        return self._offline(game, ctx, rec.get("platform", "linux"), language=rec.get("language", ""),
                             dlcs=dlc_ids, existing=Path(game.install_path), dlc_only=True, all_dlcs=dlcs)

    def _gen2(self, game: Game, ctx: JobContext, mode: str, base_dir: Path | None = None, language: str = "",
              dlcs: list | None = None, build_id: str = "", same_build: bool = False, to_branch: str | None = None,
              switch_branch: bool = False) -> Game:
        """Install / update / repair from Galaxy builds. Updates stay on the recorded channel unless an explicit
        build (``build_id``) or channel (``switch_branch`` + ``to_branch``) is asked for."""
        rec = self.install_record(game.source_id) or {}
        gen2 = content.Gen2(self.api, game.source_id)
        ctx.stage("Fetching build information")
        # always a fresh list (this also refreshes what the game page shows)
        _, builds, _ = self._builds(game.source_id, "windows", fetch=True, refresh=True)
        if not builds:
            raise RuntimeError("GOG has no Galaxy builds for this game")
        if "branch" in rec:
            rec_branch = rec["branch"] or None
        else:  # imports: the channel of the installed build
            rec_branch = (gen2.find(builds, str(rec.get("build_id") or "")) or {}).get("branch") or None
        if build_id:
            build = gen2.find(builds, build_id)
            if build is None:
                raise RuntimeError(f"Build {build_id} is no longer available from GOG")
        elif switch_branch:
            build = gen2.latest(builds, to_branch)
            if build is None:
                raise RuntimeError(f"GOG has no builds on the {content.branch_label(to_branch)} channel")
        elif mode == "install":
            build = gen2.pick(builds) or builds[0]  # a game that only has beta builds still installs
        else:
            build = gen2.pick(builds, branch=rec_branch)
            if build is None:
                raise RuntimeError(f"GOG has no builds on the {content.branch_label(rec_branch)} channel any more")
        info = gen2.build_info(build)
        language = language or rec.get("language") or self.settings.get("general.language") or "en-US"
        owned = self.owned_ids()
        product_ids = {p["id"] for p in info.dlc_products()}
        # DLCs the user asked for are remembered, so a rollback to a build without them doesn't lose the choice
        wanted = list(dlcs) if dlcs is not None else list(rec.get("dlcs_wanted", rec.get("dlcs", [])))
        dlc_list = [d for d in wanted if d in product_ids and d in owned]
        if mode == "install":
            install_dir = Path(base_dir) / sanitize_dirname(info.install_directory or game.title)
        else:
            install_dir = Path(game.install_path)
        ctx.stage("Preparing file list")
        fs = gen2.file_set(info, language, dlc_list, ctx=ctx)
        old_record = content.load_manifest(game.source_id)
        if old_record is None and mode != "install" and rec.get("build_id"):
            # imported install: rebuild the manifest from the installed build
            old_build = gen2.find(builds, str(rec.get("build_id")))
            if old_build:
                old_info = gen2.build_info(old_build)
                old_fs = gen2.file_set(old_info, language, dlc_list, ctx=ctx)
                old_record = content.build_manifest_record(old_info, old_fs, "windows", language, dlc_list,
                                                           str(install_dir), [])
        old_files = {f.path.lower(): f for f in content.manifest_files(old_record)} if old_record else {}
        resume_path = install_dir / content.RESUME_FILE
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / content.MARKER_FILE).write_text(json.dumps({"source": "gog", "id": game.source_id}))
        fetcher = content.ChunkFetcher(ctx, self.dl_session, lambda pid: self.api.secure_link(pid))
        executor = content.Executor(ctx, install_dir, install_dir / ".support", fetcher, resume_path)
        deletions = []
        if mode == "install":
            done = content.read_resume(resume_path)
            to_get = []
            for f in fs.files:
                t = executor.target(f)
                if f.path.lower() in done and t.is_file() and t.stat().st_size == f.size:
                    continue
                to_get.append(f)
        elif mode == "repair":
            ctx.stage("Verifying files")
            to_get = content.verify_files(ctx, fs.files, executor)
        else:
            new_keys = {f.path.lower() for f in fs.files}
            to_get = [f for f in fs.files
                      if f.path.lower() not in old_files or not f.same_content(old_files[f.path.lower()])
                      or not executor.resolved_target(f).exists()]
            if not same_build:
                deletions = [f for k, f in old_files.items() if k not in new_keys]
        ctx.stage({"install": "Downloading", "update": "Updating", "repair": "Repairing"}[mode]
                  + (f" ({len(to_get)} files)" if to_get else ""))
        executor.apply(to_get, old=old_files if mode == "update" else None, dirs=fs.dirs, links=fs.links,
                       deletions=deletions)
        redists = rec.get("redists", []) if mode != "install" else []
        if self.settings.get("downloads.install_dependencies") and info.dependencies:
            try:
                redists = content.install_dependencies(self.api, ctx, self.dl_session, info.dependencies, install_dir)
            except Exception as exc:  # noqa: BLE001
                log.warning("Dependencies could not be downloaded: %s", exc)
        content.save_manifest(game.source_id, content.build_manifest_record(
            info, fs, "windows", language, dlc_list, str(install_dir), redists))
        resume_path.unlink(missing_ok=True)
        size = sum(f.size for f in fs.files)
        changed = mode in ("install", "update") and not same_build  # a different build is now installed
        newest = gen2.latest(builds, info.branch) or build
        newer = sum(1 for b in builds if (b.get("branch") or None) == (info.branch or None)
                    and (b.get("date_published") or "") > info.date)
        status = "up_to_date" if str(newest.get("build_id")) == info.build_id else "behind"
        new_rec = {
            "install_path": str(install_dir), "platform": "windows", "version": info.version,
            "build_id": info.build_id, "branch": info.branch, "language": language, "dlcs": dlc_list,
            "dlcs_wanted": sorted(set(wanted)), "method": "gen2", "size": size, "redists": redists,
            "title": game.title, "date": (info.date or "")[:10],
            "installed_at": rec.get("installed_at") or time.time(),
            "updated_at": time.time() if changed
            else (rec.get("updated_at") or rec.get("installed_at") or time.time()),
            "latest": self._summary(self._build_ref(newest, newest=True), info.build_id, status, status == "behind",
                                    newer),
        }
        self._set_record(game.source_id, new_rec)
        if changed:
            # pin rule: an explicitly chosen build that isn't the newest on its channel stops update offers;
            # anything else (plain update, newest build, channel switch) lifts the pin
            pin = bool(build_id) and str(newest.get("build_id")) != info.build_id
            if bool(self.db.get_config(game.uid).get("pin_version")) != pin:
                self.db.update_config(game.uid, pin_version=pin)
        self._apply_record(game, new_rec)
        # a repair or DLC install keeps the build, so an outstanding update stays outstanding
        game.update_available = status == "behind" and not self.db.get_config(game.uid).get("pin_version")
        return game

    def _offline(self, game: Game, ctx: JobContext, platform: str, base_dir: Path | None = None,
                 language: str = "", dlcs: list | None = None, existing: Path | None = None,
                 dlc_only: bool = False, all_dlcs: list | None = None) -> Game:
        ctx.stage("Fetching installer information")
        product = self.api.product(game.source_id, expand=("downloads", "expanded_dlcs"))
        language = language or self.settings.get("general.language") or "en"
        installers = offline.installers_for(product, platform)
        inst = offline.pick_installer(installers, language)
        if inst is None:
            raise RuntimeError(f"No {platform} installer available")
        install_dir = existing or Path(base_dir or self.settings.get("library.install_dir")) / sanitize_dirname(game.title)
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / content.MARKER_FILE).write_text(json.dumps({"source": "gog", "id": game.source_id}))
        cache = offline.cache_dir_for(game.source_id)
        packages: list[tuple[str, dict]] = [] if dlc_only else [(game.title, inst)]
        owned = self.owned_ids()
        for dlc in product.get("expanded_dlcs", []) or []:
            did = str(dlc.get("id"))
            if did in (dlcs or []) and did in owned:
                di = offline.pick_installer(offline.installers_for(dlc, platform), inst.get("language", "en"))
                if di:
                    packages.append((dlc.get("title", did), di))
        for title, package in packages:
            ctx.check()
            files = offline.download_installer(self.api, self.dl_session, ctx, package, cache)
            ctx.stage(f"Installing {title}")
            if platform == "linux":
                offline.extract_linux_installer(ctx, files[0], install_dir)
            else:
                setup = next((f for f in files if f.suffix.lower() == ".exe"), files[0])
                offline.extract_windows_installer(ctx, setup, install_dir, self.services.compat, game.uid)
            if not self.settings.get("downloads.keep_installers"):
                for f in files:
                    f.unlink(missing_ok=True)
        ctx.stage("Finishing")
        rec = self.install_record(game.source_id) or {}
        version = str(inst.get("version") or "")
        new_rec = {
            "install_path": str(install_dir), "platform": platform,
            "version": version if not dlc_only else rec.get("version", ""),
            "build_id": "", "language": inst.get("language", "en"),
            "dlcs": sorted(set(all_dlcs or []) | set(dlcs or [])), "method": "installer",
            "size": dir_size(install_dir), "title": game.title,
            "installed_at": rec.get("installed_at") or time.time(),
            "updated_at": (rec.get("updated_at") or rec.get("installed_at") or time.time()) if dlc_only
            else time.time(),
            # a DLC-only run doesn't change the base game, so its summary stays valid
            "latest": rec.get("latest") if dlc_only else self._summary(
                base.BuildRef(version=version, size=int(inst.get("total_size") or 0), newest=True), version,
                "up_to_date", False),
        }
        self._set_record(game.source_id, new_rec)
        if not dlc_only and self.db.get_config(game.uid).get("pin_version"):
            self.db.update_config(game.uid, pin_version=False)  # the newest installer is never a pinned choice
        self.invalidate_versions(game.source_id)  # the cached installer list may predate this install
        self._apply_record(game, new_rec)
        if not dlc_only:
            game.update_available = False
        return game

    def uninstall(self, game: Game, ctx: JobContext) -> Game:
        ctx.stage("Removing files")
        path = Path(game.install_path)
        if path.is_dir():
            marker = content.MARKER_FILE if (path / content.MARKER_FILE).exists() else None
            if marker is None:
                infos = list(path.glob("goggame-*.info")) + list(path.glob("game/goggame-*.info"))
                if not infos and not (path / "start.sh").exists():
                    raise RuntimeError(f"{path} does not look like a GOG game folder; refusing to delete it")
            safe_rmtree(path)
        content.delete_manifest(game.source_id)
        self._set_record(game.source_id, None)
        self.invalidate_versions(game.source_id)
        if self.db.get_config(game.uid).get("pin_version"):
            self.db.update_config(game.uid, pin_version=False)
        self._apply_record(game, None)
        return game

    # ------------------------------------------------------------ versions
    def versions(self, game: Game) -> list[dict]:
        """The Galaxy builds this copy can change to (a thin wrapper around ``version_info``)."""
        info = self.version_info(game)
        if not info or info.kind != "builds":
            return []
        if info.error:
            raise RuntimeError(info.error_text)
        return [{"id": b.id, "label": b.label, "date": b.date, "branch": b.branch, "current": b.current,
                 "newest": b.newest} for b in info.builds]

    def _cached(self, key: tuple, loader, fetch: bool, refresh: bool):
        """-> (wall time, items, expired) or None. ``fetch=False`` never calls ``loader`` (any memory hit is
        returned, however old). Concurrent fetches of one key share a single request; errors are not cached."""
        hit = self._vcache.get(key)
        now = time.monotonic()
        if hit and not refresh and now - hit[0] < VERSIONS_TTL:
            return hit[1], list(hit[2]), False
        if not fetch:
            return (hit[1], list(hit[2]), True) if hit else None
        with self._vmeta:
            lock = self._vlocks.setdefault(key, threading.Lock())
        with lock:
            hit = self._vcache.get(key)
            if hit and hit[0] >= now:  # another thread fetched it while this one waited
                return hit[1], list(hit[2]), False
            items = loader()
            entry = (time.monotonic(), time.time(), list(items))
            self._vcache[key] = entry
            return entry[1], list(items), False

    def _builds(self, product_id: str, os_name: str = "windows", fetch: bool = True, refresh: bool = False):
        pid = str(product_id)
        return self._cached(("builds", pid, os_name), lambda: content.Gen2(self.api, pid).builds(os_name),
                            fetch, refresh)

    def _installers(self, product_id: str, fetch: bool = True, refresh: bool = False):
        pid = str(product_id)

        def load():
            return (self.api.product(pid, expand=("downloads",)).get("downloads") or {}).get("installers") or []
        return self._cached(("installers", pid), load, fetch, refresh)

    def invalidate_versions(self, product_id: str | None = None) -> None:
        """Forget cached build / installer lists (of one product, or all of them)."""
        for key in list(self._vcache):
            if product_id is None or key[1] == str(product_id):
                self._vcache.pop(key, None)

    @staticmethod
    def _version_kind(rec: dict) -> str:
        platform = rec.get("platform", "windows")
        if rec.get("method") in ("gen2", "imported-gen2") and platform == "windows":
            return "builds"
        return "installer" if platform in ("linux", "windows") else "none"

    @staticmethod
    def _origin(rec: dict) -> str:
        if rec.get("source") == "heroic":
            return "heroic"
        return "folder" if str(rec.get("method", "")).startswith("imported") else "nebula"

    @staticmethod
    def _method_text(kind: str, origin: str, platform: str) -> str:
        runs = {"linux": "Linux native", "windows": "Windows version (runs with Proton)"}.get(
            platform, f"{(platform or 'unknown').capitalize()} version")
        if kind == "builds":
            return {"heroic": "GOG Galaxy builds · imported from Heroic",
                    "folder": "GOG Galaxy builds · imported from a folder"}.get(origin, f"GOG Galaxy builds · {runs}")
        if origin == "heroic":
            return f"Imported from Heroic · {runs}"
        if origin == "folder":
            return f"Imported folder · {runs}"
        return f"GOG offline installer · {runs}"

    @staticmethod
    def _build_ref(b: dict, **kw) -> base.BuildRef:
        published = str(b.get("date_published") or "")
        return base.BuildRef(id=str(b.get("build_id") or ""), version=str(b.get("version_name") or ""),
                             branch=b.get("branch") or "", date=published[:10], published=published, **kw)

    @staticmethod
    def _summary(latest: base.BuildRef, identity: str, status: str, update_available: bool, newer_count: int = 0,
                 branch_gone: bool = False, checked_at: float = 0.0) -> dict:
        """The "newest version" summary kept in the install record, shown until the next live check.
        ``identity`` is the installed build id (or installer version) the status was computed for."""
        return {"id": latest.id, "version": latest.version, "branch": latest.branch, "date": latest.date,
                "size": latest.size, "checked_at": checked_at or time.time(), "for": identity, "status": status,
                "update_available": update_available, "newer_count": newer_count, "branch_gone": branch_gone}

    def version_info(self, game: Game, fetch: bool = True, refresh: bool = False) -> base.VersionInfo | None:
        if not game.installed:
            return self._available_info(game, fetch, refresh)
        pid = game.source_id
        rec = self.install_record(pid)
        if not rec:
            return None
        cfg = self.db.get_config(game.uid)
        kind, origin, platform = self._version_kind(rec), self._origin(rec), rec.get("platform", "windows")
        version, from_gameinfo = self._version_source(rec)
        info = base.VersionInfo(
            kind=kind, method=self._method_text(kind, origin, platform), origin=origin, platform=platform,
            pinned=bool(cfg.get("pin_version")), auto_update=cfg.get("auto_update", True) is not False,
            updated_at=float(rec.get("updated_at") or rec.get("installed_at") or 0),
            installed=base.BuildRef(id=str(rec.get("build_id") or ""), version=version,
                                    branch=rec.get("branch") or "", date=str(rec.get("date") or "")[:10],
                                    current=True))
        if kind == "none":
            return info
        if kind == "installer":
            info.note = INSTALLER_NOTES[platform]  # what can't be done with an offline-installer copy
        match, live = None, False
        if not self.connected():
            info.error = "signed_out"
            info.error_text = "Sign in to GOG to check for updates and change versions."
        else:
            try:
                hit = (self._builds(pid, "windows", fetch, refresh) if kind == "builds"
                       else self._installers(pid, fetch, refresh))
                if hit is not None:  # None: nothing fetched yet and fetch=False -> the saved summary
                    ts, items, expired = hit
                    info.stale, info.checked_at = expired or not fetch, ts
                    if kind == "builds":
                        match = self._compare_builds(info, rec, items)
                    else:
                        self._compare_installer(info, rec, items, from_gameinfo)
                    live = not info.error
            except OSError as exc:  # requests' exceptions are OSErrors too
                log.debug("version check for %s failed: %s", game.title, exc)
                info.error, info.error_text = "offline", "Couldn't reach GOG. Check your connection and try again."
            except ApiError as exc:
                log.debug("version check for %s failed: %s", game.title, exc)
                info.error, info.error_text = "failed", f"GOG returned an error ({exc})."
            except Exception as exc:  # noqa: BLE001
                log.info("version check for %s failed: %s", game.title, exc)
                info.error, info.error_text = "failed", str(exc)
        if not live:
            self._apply_saved(info, rec.get("latest"))
        elif not info.stale:
            try:
                self._save_summary(pid, rec, info, match)
            except Exception as exc:  # noqa: BLE001
                log.debug("could not save the version summary for %s: %s", game.title, exc)
        return info

    def _available_info(self, game: Game, fetch: bool, refresh: bool) -> base.VersionInfo:
        """A game that isn't installed: the newest version GOG offers for the platform Nebula would install."""
        platform = self.default_platform(game)
        info = base.VersionInfo(kind="builds" if platform == "windows" else "installer", platform=platform,
                                status="not_installed")
        if not self.connected():
            info.error, info.error_text = "signed_out", "Sign in to GOG to see the newest version."
            return info
        pid = game.source_id
        try:
            if platform == "windows":
                hit = self._builds(pid, "windows", fetch, refresh)
                if hit is None:  # nothing fetched yet and fetch=False
                    info.stale = True
                    return info
                ts, builds, expired = hit
                info.stale, info.checked_at = expired or not fetch, ts
                if builds:
                    latest = content.Gen2.latest(builds, None) or builds[0]  # a newer beta is never the default
                    info.latest = self._build_ref(latest, newest=True)
                    info.branches = ["" if br is None else br for br in content.Gen2.branches(builds)]
                    return info
                info.kind = "installer"  # no Galaxy builds: Nebula installs GOG's offline installer
            hit = self._installers(pid, fetch, refresh)
            if hit is None:
                info.stale = True
                return info
            ts, items, expired = hit
            info.stale, info.checked_at = expired or not fetch, ts
            inst = offline.pick_installer([i for i in items if i.get("os") == platform],
                                          self.settings.get("general.language") or "en")
            if inst is None:
                info.error = "failed"
                info.error_text = f"GOG doesn't offer a {'Linux' if platform == 'linux' else 'Windows'} installer."
            else:
                info.latest = base.BuildRef(version=str(inst.get("version") or ""),
                                            size=int(inst.get("total_size") or 0), newest=True)
        except OSError as exc:
            log.debug("version check for %s failed: %s", game.title, exc)
            info.error, info.error_text = "offline", "Couldn't reach GOG. Check your connection and try again."
        except ApiError as exc:
            info.error, info.error_text = "failed", f"GOG returned an error ({exc})."
        except Exception as exc:  # noqa: BLE001
            log.info("version check for %s failed: %s", game.title, exc)
            info.error, info.error_text = "failed", str(exc)
        return info

    def _compare_builds(self, info: base.VersionInfo, rec: dict, builds: list[dict]) -> dict | None:
        """Fill ``info`` from GOG's build list; returns the installed build (None when GOG doesn't list it)."""
        if not builds:
            info.error, info.error_text = "failed", "GOG didn't return any builds for this game. Try again later."
            return None
        inst = info.installed
        match = content.Gen2.find(builds, inst.id)
        if match is not None:
            inst.version = str(match.get("version_name") or "") or inst.version
            inst.published = str(match.get("date_published") or "")
            inst.date = inst.published[:10]
            info.installed_listed = True
            if "branch" not in rec:  # imports: adopt the channel of the matched build
                inst.branch = match.get("branch") or ""
        tracked = inst.branch
        latest = content.Gen2.latest(builds, tracked)
        if latest is None:  # the channel was closed: offer Stable (if any), never move automatically
            info.branch_gone = True
            latest = content.Gen2.latest(builds, None) if tracked else None
        info.branch = "" if info.branch_gone else tracked
        if info.branch_gone:
            info.status = "channel_ended"
        elif match is not None:
            info.status = "up_to_date" if str(match.get("build_id")) == str(latest["build_id"]) else "behind"
        else:
            info.status = "unlisted"
        if match is not None:
            info.newer_count = sum(1 for b in builds if (b.get("branch") or "") == tracked
                                   and str(b.get("date_published") or "") > inst.published)
        # an unlisted build only means "update" for Nebula's own installs (imports may carry legacy ids)
        info.update_available = info.status == "behind" or (info.status == "unlisted" and rec.get("method") == "gen2")
        seen: set[str] = set()
        for b in builds:
            br = b.get("branch") or ""
            info.builds.append(self._build_ref(b, newest=br not in seen, current=b is match))
            seen.add(br)
        info.branches = ["" if br is None else br for br in content.Gen2.branches(builds)]
        info.latest = self._build_ref(latest, newest=True, current=latest is match) if latest else None
        info.can_choose = info.can_update = True
        return match

    def _compare_installer(self, info: base.VersionInfo, rec: dict, items: list[dict], from_gameinfo: bool) -> None:
        """Fill ``info`` from GOG's current offline installer for the installed platform."""
        lang = rec.get("language") or self.settings.get("general.language") or "en"
        inst = offline.pick_installer([i for i in items if i.get("os") == info.platform], lang)
        if inst is None:
            os_name = "Linux" if info.platform == "linux" else "Windows"
            info.error = "failed"
            info.error_text = f"GOG doesn't offer a {os_name} installer for this game any more."
            return
        info.latest = base.BuildRef(version=str(inst.get("version") or ""), size=int(inst.get("total_size") or 0),
                                    newest=True)
        have, newest = info.installed.version, info.latest.version
        if not have or not newest:
            info.status = "unknown"
        elif from_gameinfo:  # gameinfo has no "(gog-N)" suffix, so compare without it
            info.status = "up_to_date" if _norm_version(have) == _norm_version(newest) else "different"
        else:  # exact: a "(gog-N)" bump is a new installer
            info.status = "up_to_date" if have.strip() == newest.strip() else "different"
        info.update_available = info.status == "different"
        info.can_update, info.can_choose = True, False

    @staticmethod
    def _apply_saved(info: base.VersionInfo, saved) -> None:
        """No live data (signed out, offline, nothing fetched yet): use the summary of the last check."""
        info.stale = True
        info.builds, info.branches, info.installed_listed = [], [], False
        info.status, info.update_available, info.newer_count, info.branch_gone = "unknown", False, 0, False
        info.can_update = not info.error
        info.can_choose = not info.error and info.kind == "builds"
        info.latest, info.checked_at = None, 0.0
        if isinstance(saved, dict) and (saved.get("id") or saved.get("version")):
            info.latest = base.BuildRef(id=str(saved.get("id") or ""), version=str(saved.get("version") or ""),
                                        branch=saved.get("branch") or "", date=str(saved.get("date") or "")[:10],
                                        size=int(saved.get("size") or 0), newest=True)
            info.checked_at = float(saved.get("checked_at") or 0)
            identity = info.installed.id if info.kind == "builds" else info.installed.version
            if str(saved.get("for") or "").strip() == identity:
                info.status = saved.get("status") or "unknown"
                info.update_available = bool(saved.get("update_available"))
                info.newer_count = int(saved.get("newer_count") or 0)
                info.branch_gone = bool(saved.get("branch_gone"))
            elif info.latest.id and info.latest.id == info.installed.id:
                info.status = "up_to_date"
        info.branch = "" if info.branch_gone else info.installed.branch

    def _save_summary(self, pid: str, rec: dict, info: base.VersionInfo, match: dict | None) -> None:
        """Persist a live check (the newest-version summary) and heal what imports recorded wrongly."""
        identity = info.installed.id if info.kind == "builds" else info.installed.version
        summary = self._summary(info.latest or base.BuildRef(), identity, info.status, info.update_available, info.newer_count,
                                info.branch_gone, info.checked_at)
        saved = rec.get("latest") if isinstance(rec.get("latest"), dict) else {}
        patch = {}
        if any(saved.get(k) != summary[k] for k in ("id", "version", "status", "for")) \
                or time.time() - float(saved.get("checked_at") or 0) > SUMMARY_MAX_AGE:
            patch["latest"] = summary
        if match is not None:
            name = str(match.get("version_name") or "")
            if name and rec.get("version") != name:
                patch["version"] = name
            if not rec.get("date") and info.installed.date:
                patch["date"] = info.installed.date
            if "branch" not in rec:
                patch["branch"] = match.get("branch") or None
        if patch:  # the build id stays as recorded (it may be a legacy id that still matches)
            self._patch_record(pid, info.installed.id, rec.get("version", ""), **patch)

    # ------------------------------------------------------------ details
    def details(self, game: Game) -> GameDetails:
        d = GameDetails(description=game.description, genres=game.genres, developer=game.developer,
                        publisher=game.publisher, release_date=game.release_date,
                        screenshots=list(game.extra.get("screenshots", [])))
        try:
            p = self.api.product(game.source_id, expand=("description", "screenshots", "videos",
                                                         "expanded_dlcs", "changelog"))
        except Exception as exc:  # noqa: BLE001
            log.debug("product details failed: %s", exc)
            return d
        desc = p.get("description") or {}
        full = desc.get("full") or desc.get("lead") or ""
        if full:
            d.description = strip_html(full)
        shots = []
        for s in p.get("screenshots", []) or []:
            tmpl = s.get("formatter_template_url")
            if tmpl:
                shots.append(tmpl.replace("{formatter}", "ggvgl_2x"))
        if shots:
            d.screenshots = shots
        d.videos = [(v.get("thumbnail_url", ""), v.get("video_url", "").replace("/embed/", "/watch?v=").split("?wmode")[0])
                    for v in p.get("videos", []) or []]
        links = p.get("links") or {}
        d.links = {k.replace("_", " ").title(): v for k, v in links.items() if v}
        if game.store_url:
            d.links["Store page"] = game.store_url
        d.changelog = strip_html(p.get("changelog") or "")
        d.languages = list((p.get("languages") or {}).values())
        images = p.get("images") or {}
        if images.get("background") and not game.hero:
            game.hero = https(images["background"])
        owned = self.owned_ids()
        installed_dlcs = set(game.extra.get("installed_dlcs", []))
        for dlc in p.get("expanded_dlcs", []) or []:
            did = str(dlc.get("id"))
            img = (dlc.get("images") or {}).get("logo2x") or (dlc.get("images") or {}).get("logo") or ""
            d.dlcs.append({"id": did, "title": dlc.get("title", did), "owned": did in owned,
                           "installed": did in installed_dlcs, "cover": https(img),
                           "url": https((dlc.get("links") or {}).get("product_card", ""))})
        return d

    def achievements(self, game: Game) -> list[Achievement] | None:
        if not self.connected():
            return None
        items = None
        try:
            items = self.api.achievements(game.source_id)
        except Exception:  # noqa: BLE001
            items = None
        if not items:
            manifest = content.load_manifest(game.source_id) or {}
            cid, secret = manifest.get("client_id"), manifest.get("client_secret")
            if cid and secret:
                try:
                    token = self.auth.game_credentials(cid, secret)["access_token"]
                    items = self.api.achievements(cid, token=token)
                except Exception:  # noqa: BLE001
                    items = None
        if items is None:
            return None
        out = []
        for a in items:
            unlocked = a.get("date_unlocked")
            ts = 0.0
            if unlocked:
                try:
                    ts = datetime.fromisoformat(str(unlocked).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ts = time.time()
            rarity = a.get("rarity")
            out.append(Achievement(
                id=str(a.get("achievement_key") or a.get("achievement_id")),
                name=a.get("name", ""), description=a.get("description", ""),
                icon=a.get("image_url_unlocked", ""), icon_locked=a.get("image_url_locked", ""),
                unlocked=bool(unlocked), unlocked_at=ts,
                rarity=float(rarity) if rarity is not None else -1.0,
                hidden=not a.get("visible", True),
            ))
        return out

    def playtime(self, game: Game) -> int | None:
        if not self.connected():
            return None
        return self.api.playtime(game.source_id)

    def friends(self) -> list[Friend]:
        if not self.connected():
            return []
        out = []
        for f in self.api.friends():
            user = f.get("user") if isinstance(f.get("user"), dict) else f
            uid = str(user.get("user_id") or user.get("id") or "")
            name = user.get("username") or user.get("name") or uid
            images = user.get("images") or user.get("avatar") or {}
            avatar = ""
            if isinstance(images, dict):
                avatar = images.get("medium_2x") or images.get("medium") or images.get("small") or ""
            out.append(Friend("gog", uid, name, avatar=https(avatar), status="unknown",
                              profile_url=f"https://www.gog.com/u/{name}"))
        return out

    def store_url(self, game: Game) -> str:
        return game.store_url or f"https://www.gog.com/en/game/{game.extra.get('slug', '')}"

    # --------------------------------------------------------- cloud saves
    def cloud_sync(self, game: Game, mode: str = "auto", when: str = "manual") -> CloudSyncResult:
        if not self.connected():
            return CloudSyncResult("unsupported", "Sign in to GOG to use cloud saves")
        if not game.installed or game.platform != "windows":
            return CloudSyncResult("unsupported", "GOG cloud saves are available for Windows builds (run via Proton)")
        manifest = content.load_manifest(game.source_id) or {}
        cid, secret = manifest.get("client_id"), manifest.get("client_secret")
        if not (cid and secret):
            info = self._info_file(game) or {}
            cid = cid or info.get("clientId")
            if cid:
                try:
                    builds = content.Gen2(self.api, game.source_id).builds("windows")
                    if builds:
                        bi = content.Gen2(self.api, game.source_id).build_info(builds[0])
                        secret = bi.client_secret
                except Exception:  # noqa: BLE001
                    secret = None
        if not (cid and secret):
            return CloudSyncResult("unsupported", "This game does not support GOG cloud saves")
        policy = "newest" if when != "manual" and self.settings.get("cloud.conflict") == "newest" else "ask"
        try:
            return self.cloud.sync(game.uid, game.install_path, cid, secret, mode=mode, conflict_policy=policy)
        except Exception as exc:  # noqa: BLE001
            return CloudSyncResult("error", f"Cloud sync failed: {exc}")

    def cloud_locations(self, game: Game) -> list[tuple[str, str]]:
        manifest = content.load_manifest(game.source_id) or {}
        cid = manifest.get("client_id") or (self._info_file(game) or {}).get("clientId")
        if not cid:
            return []
        try:
            return [(n, str(p)) for n, p in self.cloud.locations(cid, game.uid, game.install_path)]
        except Exception:  # noqa: BLE001
            return []

    # --------------------------------------------------------------- store
    def wishlist(self, refresh: bool = False) -> set[str]:
        if not self.connected():
            return set()
        if self._wishlist is None or refresh:
            try:
                self._wishlist = self.api.wishlist()
            except Exception:  # noqa: BLE001
                self._wishlist = set()
        return self._wishlist

    def set_wishlisted(self, product_id: str, wished: bool) -> None:
        if wished:
            self.api.wishlist_add(product_id)
            self.wishlist().add(str(product_id))
        else:
            self.api.wishlist_remove(product_id)
            self.wishlist().discard(str(product_id))

    def catalog(self, **kwargs) -> dict:
        kwargs.setdefault("country", self.settings.get("gog.country") or "US")
        kwargs.setdefault("currency", self.settings.get("gog.currency") or "USD")
        return self.api.catalog(**kwargs)

    def find_in_store(self, title: str) -> dict | None:
        """Look a title up in the GOG catalog (used to offer a DRM-free copy of games owned elsewhere)."""
        from nebula.core.models import title_key

        key = title_key(title)
        cache = self.__dict__.setdefault("_store_lookup", {})
        if key in cache:
            return cache[key]
        found = None
        try:
            data = self.catalog(query=title, limit=10)
            for prod in data.get("products", []) or []:
                if title_key(prod.get("title", "")) == key:
                    found = prod
                    break
        except Exception as exc:  # noqa: BLE001
            log.debug("store lookup for %s failed: %s", title, exc)
            return None
        cache[key] = found
        return found

    def store_product(self, product_id: str) -> dict:
        p = self.api.product(product_id, expand=("description", "screenshots", "videos"))
        p["_description_text"] = strip_html((p.get("description") or {}).get("full") or
                                            (p.get("description") or {}).get("lead") or "")
        return p

    def open_platform_client(self) -> None:
        from nebula.core.utils import xdg_open

        xdg_open("https://www.gog.com/")


def remove_installer_cache(product_id: str) -> None:
    d = paths.installers_cache() / str(product_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
