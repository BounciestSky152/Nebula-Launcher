"""GOG integration: library, Galaxy-style installs/updates, launching through Proton, cloud saves,
achievements, play time, presence, friends and the store."""
from __future__ import annotations

import json
import logging
import shlex
import shutil
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

    # ------------------------------------------------------- install book
    def installs(self) -> dict:
        return self.db.kv_get(INSTALLS_KEY, {}) or {}

    def install_record(self, product_id: str) -> dict | None:
        return self.installs().get(str(product_id))

    def _set_record(self, product_id: str, rec: dict | None) -> None:
        data = self.installs()
        if rec is None:
            data.pop(str(product_id), None)
        else:
            data[str(product_id)] = rec
        self.db.kv_set(INSTALLS_KEY, data)

    def _apply_record(self, game: Game, rec: dict | None) -> None:
        if rec and Path(rec.get("install_path", "")).is_dir():
            game.installed = True
            game.install_path = rec["install_path"]
            game.platform = rec.get("platform", "windows")
            game.version = rec.get("version", "")
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
            cfg = self.db.get_config(g.uid)
            if cfg.get("pin_version"):
                g.update_available = False
                continue
            rec = self.install_record(g.source_id) or {}
            try:
                if rec.get("method") in ("gen2", "imported-gen2"):
                    builds = content.Gen2(self.api, g.source_id).builds(rec.get("platform", "windows"))
                    latest = content.Gen2(self.api, g.source_id).pick(builds, branch=rec.get("branch"))
                    g.update_available = bool(latest and str(latest.get("build_id")) != str(rec.get("build_id")))
                else:
                    product = self.api.product(g.source_id, expand=("downloads",))
                    inst = offline.pick_installer(offline.installers_for(product, rec.get("platform", "linux")),
                                                  rec.get("language", "en"))
                    g.update_available = bool(inst and rec.get("version") and inst.get("version") != rec.get("version"))
            except Exception as exc:  # noqa: BLE001
                log.debug("update check failed for %s: %s", g.title, exc)
                continue
            if g.update_available:
                out.append(g.uid)
        return out

    # ------------------------------------------------------------- import
    def _import_heroic(self) -> None:
        """Pick up GOG games previously installed with Heroic Games Launcher."""
        records = self.installs()
        changed = False
        for cfg_root in (Path.home() / ".config/heroic", Path.home() / ".var/app/com.heroicgameslauncher.hgl/config/heroic"):
            f = cfg_root / "gog_store" / "installed.json"
            try:
                data = json.loads(f.read_text())
            except (OSError, ValueError):
                continue
            for item in data.get("installed", []):
                pid = str(item.get("appName", ""))
                path = item.get("install_path", "")
                if not pid or pid in records or not Path(path).is_dir():
                    continue
                records[pid] = {
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
                changed = True
        if changed:
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
                found[pid] = {
                    "install_path": str(install_dir),
                    "platform": "linux" if is_linux else "windows",
                    "version": data.get("version", ""),
                    "build_id": str(data.get("buildId", "")),
                    "language": (data.get("language") or "English"),
                    "dlcs": [],
                    "method": "imported-gen2" if data.get("buildId") and not is_linux else "imported",
                    "size": dir_size(install_dir),
                    "title": data.get("name", pid),
                }
        if not found:
            return []
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
        win_builds = content.Gen2(self.api, game.source_id).builds("windows")
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
        lang = self.settings.get("general.language") or "en-US"
        opts = InstallOptions(platforms=platforms, platform=platform)
        title_dir = sanitize_dirname(game.title)
        if platform == "windows" and win_builds:
            info = content.Gen2(self.api, game.source_id).build_info(win_builds[0])
            opts.method = "GOG Galaxy content system (Proton)"
            langs = info.languages() or ["en-US"]
            opts.languages = [(c, content.lang_label(c)) for c in langs]
            opts.language = next((c for c in langs if content.lang_matches([c], lang)), langs[0])
            opts.dlcs = [{"id": p["id"], "title": p["name"], "size": info.dlc_size(p["id"], opts.language)}
                         for p in info.dlc_products() if p["id"] in owned]
            opts.download_size, opts.disk_size = info.sizes(opts.language, [d["id"] for d in opts.dlcs])
            opts.versions = [{"id": str(b["build_id"]), "label": b.get("version_name") or str(b["build_id"]),
                              "date": (b.get("date_published") or "")[:10], "branch": b.get("branch")}
                             for b in win_builds]
            opts.version = opts.versions[0]["id"] if opts.versions else ""
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
        if platform == "windows" and content.Gen2(self.api, game.source_id).builds("windows"):
            return self._gen2(game, ctx, "install", base_dir=base_dir, language=request.language,
                              dlcs=request.dlcs, build_id=request.version)
        return self._offline(game, ctx, platform, base_dir=base_dir, language=request.language, dlcs=request.dlcs)

    def update(self, game: Game, ctx: JobContext, version: str = "") -> Game:
        rec = self.install_record(game.source_id) or {}
        if rec.get("method", "").endswith("gen2") or rec.get("method") == "gen2":
            return self._gen2(game, ctx, "update", build_id=version)
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
        dlcs = sorted(set(rec.get("dlcs", [])) | set(dlc_ids))
        if rec.get("method", "").endswith("gen2"):
            return self._gen2(game, ctx, "update", build_id=str(rec.get("build_id", "")), dlcs=dlcs, same_build=True)
        return self._offline(game, ctx, rec.get("platform", "linux"), language=rec.get("language", ""),
                             dlcs=dlc_ids, existing=Path(game.install_path), dlc_only=True, all_dlcs=dlcs)

    def versions(self, game: Game) -> list[dict]:
        rec = self.install_record(game.source_id) or {}
        os_name = rec.get("platform") or "windows"
        if os_name != "windows":
            return []
        builds = content.Gen2(self.api, game.source_id).builds(os_name)
        return [{"id": str(b["build_id"]), "label": b.get("version_name") or str(b["build_id"]),
                 "date": (b.get("date_published") or "")[:10], "branch": b.get("branch") or "",
                 "current": str(b["build_id"]) == str(rec.get("build_id"))} for b in builds]

    def _gen2(self, game: Game, ctx: JobContext, mode: str, base_dir: Path | None = None, language: str = "",
              dlcs: list | None = None, build_id: str = "", same_build: bool = False) -> Game:
        rec = self.install_record(game.source_id) or {}
        gen2 = content.Gen2(self.api, game.source_id)
        ctx.stage("Fetching build information")
        builds = gen2.builds("windows")
        if not builds:
            raise RuntimeError("No Galaxy builds available for this game")
        build = gen2.pick(builds, build_id=build_id, branch=rec.get("branch"))
        if build is None:
            raise RuntimeError(f"Build {build_id} is no longer available")
        info = gen2.build_info(build)
        language = language or rec.get("language") or self.settings.get("general.language") or "en-US"
        owned = self.owned_ids()
        product_ids = {p["id"] for p in info.dlc_products()}
        dlc_list = [d for d in (dlcs if dlcs is not None else rec.get("dlcs", [])) if d in product_ids and d in owned]
        if mode == "install":
            install_dir = Path(base_dir) / sanitize_dirname(info.install_directory or game.title)
        else:
            install_dir = Path(game.install_path)
        ctx.stage("Preparing file list")
        fs = gen2.file_set(info, language, dlc_list, ctx=ctx)
        old_record = content.load_manifest(game.source_id)
        if old_record is None and mode != "install" and rec.get("build_id"):
            # imported install: rebuild the manifest from the installed build
            old_build = gen2.pick(builds, build_id=str(rec.get("build_id")))
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
        new_rec = {
            "install_path": str(install_dir), "platform": "windows", "version": info.version,
            "build_id": info.build_id, "branch": info.branch, "language": language, "dlcs": dlc_list,
            "method": "gen2", "size": size, "redists": redists, "title": game.title,
            "installed_at": rec.get("installed_at") or time.time(),
        }
        self._set_record(game.source_id, new_rec)
        if build_id and mode == "update" and not same_build:
            latest = gen2.pick(builds, branch=info.branch)
            if latest and str(latest.get("build_id")) != info.build_id:
                self.db.update_config(game.uid, pin_version=True)  # rolled back: stop auto-updating
        self._apply_record(game, new_rec)
        game.update_available = False
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
        new_rec = {
            "install_path": str(install_dir), "platform": platform,
            "version": inst.get("version", "") if not dlc_only else rec.get("version", ""),
            "build_id": "", "language": inst.get("language", "en"),
            "dlcs": sorted(set(all_dlcs or []) | set(dlcs or [])), "method": "installer",
            "size": dir_size(install_dir), "title": game.title,
            "installed_at": rec.get("installed_at") or time.time(),
        }
        self._set_record(game.source_id, new_rec)
        self._apply_record(game, new_rec)
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
        self._apply_record(game, None)
        return game

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
