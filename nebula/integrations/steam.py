"""Steam integration: local library, play time, launching via the Steam client,
plus owned games, achievements and friends through the Steam Web API (API key)."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from nebula.core import vdf
from nebula.core.http import new_session
from nebula.core.models import Achievement, Friend, Game
from nebula.core.utils import flatpak_installed, which, xdg_open
from nebula.integrations import base
from nebula.integrations.base import InstallRequest, Integration
from nebula.runners.compat import LaunchSpec, steam_library_folders, steam_roots

log = logging.getLogger(__name__)

STEAMID64_BASE = 76561197960265728
WEB_API = "https://api.steampowered.com"
CDN = "https://shared.steamstatic.com/store_item_assets/steam/apps"
CDN_OLD = "https://cdn.cloudflare.steamstatic.com/steam/apps"

TOOL_APPIDS = {
    "228980", "1070560", "1391110", "1628350", "1493710", "2180100", "961940", "1054830", "1113280",
    "1245040", "1420170", "1580130", "1887720", "2348590", "2805730", "3658110", "1826330", "1161040",
    "250820", "858280", "4185400",
}
TOOL_NAME = re.compile(r"^(proton|steam linux runtime|steamworks common|steamvr|steam controller)", re.I)
PERSONA = {0: "offline", 1: "online", 2: "busy", 3: "away", 4: "away", 5: "online", 6: "online"}


class SteamIntegration(Integration):
    id = "steam"
    name = "Steam"
    color = "#3d8fd6"
    letter = "S"
    description = "Installed Steam games and play time; add a Web API key for your full library, " \
                  "achievements and friends."
    capabilities = frozenset({base.LIBRARY, base.INSTALL, base.UNINSTALL, base.LAUNCH, base.REPAIR,
                              base.ACHIEVEMENTS, base.FRIENDS, base.PLAYTIME, base.STORE})

    def __init__(self, services):
        super().__init__(services)
        self.session = new_session()
        self._schema_cache: dict[str, dict] = {}

    # ------------------------------------------------------------ state
    def root(self) -> Path | None:
        roots = steam_roots()
        return roots[0] if roots else None

    def available(self) -> bool:
        return self.root() is not None

    def connected(self) -> bool:
        return self.available() or bool(self.api_key() and self.steamid())

    def api_key(self) -> str:
        return (self.settings.get("steam.api_key") or "").strip()

    def login_users(self) -> dict:
        root = self.root()
        if not root:
            return {}
        try:
            return vdf.get(vdf.load(root / "config" / "loginusers.vdf"), "users", default={}) or {}
        except OSError:
            return {}

    def steamid(self) -> str:
        sid = (self.settings.get("steam.steamid") or "").strip()
        if sid:
            return sid
        users = self.login_users()
        best = None
        for sid, info in users.items():
            if not isinstance(info, dict):
                continue
            if str(info.get("MostRecent", info.get("mostrecent", "0"))) == "1":
                return sid
            ts = int(info.get("Timestamp", 0) or 0)
            if best is None or ts > best[0]:
                best = (ts, sid)
        return best[1] if best else ""

    def account_name(self) -> str:
        sid = self.steamid()
        info = self.login_users().get(sid, {})
        return info.get("PersonaName") or info.get("AccountName") or (sid if sid else "")

    def status_text(self) -> str:
        if not self.available() and not self.api_key():
            return "Steam not detected"
        parts = [f"Signed in as {self.account_name()}" if self.account_name() else "Detected"]
        parts.append("Web API key set" if self.api_key() else "no Web API key (installed games only)")
        return " • ".join(parts)

    def connect(self, api_key: str = "", steamid: str = "", **_kw) -> None:
        self.settings.set("steam.api_key", api_key.strip())
        if steamid:
            self.settings.set("steam.steamid", steamid.strip())

    def disconnect(self) -> None:
        self.settings.set("steam.api_key", "")

    # ---------------------------------------------------------- library
    def _manifests(self) -> dict[str, tuple[dict, Path]]:
        out: dict[str, tuple[dict, Path]] = {}
        root = self.root()
        if not root:
            return out
        for lib in steam_library_folders(root):
            for acf in (lib / "steamapps").glob("appmanifest_*.acf"):
                try:
                    data = vdf.get(vdf.load(acf), "AppState", default={}) or {}
                except OSError:
                    continue
                appid = str(data.get("appid") or acf.stem.split("_")[1])
                out[appid] = (data, lib)
        return out

    def _localconfig(self) -> dict[str, dict]:
        root, sid = self.root(), self.steamid()
        if not root or not sid:
            return {}
        try:
            account = str(int(sid) - STEAMID64_BASE)
        except ValueError:
            return {}
        f = root / "userdata" / account / "config" / "localconfig.vdf"
        try:
            data = vdf.load(f)
        except OSError:
            return {}
        apps = vdf.get(data, "UserLocalConfigStore", "Software", "Valve", "Steam", "apps", default={}) or {}
        return {k: v for k, v in apps.items() if isinstance(v, dict)}

    def _local_art(self, appid: str, kind: str) -> list[str]:
        root = self.root()
        if not root:
            return []
        cache = root / "appcache" / "librarycache"
        out = []
        for name in (f"{appid}_{kind}.jpg", f"{appid}_{kind}.png"):
            if (cache / name).exists():
                out.append(str(cache / name))
        sub = cache / appid
        if sub.is_dir():
            out += [str(p) for p in sorted(sub.rglob(f"{kind}*.jpg"))] + [str(p) for p in sorted(sub.rglob(f"{kind}*.png"))]
        return out

    def _art(self, g: Game) -> None:
        a = g.source_id
        covers = self._local_art(a, "library_600x900") + [f"{CDN}/{a}/library_600x900_2x.jpg",
                                                          f"{CDN_OLD}/{a}/library_600x900_2x.jpg"]
        heroes = self._local_art(a, "library_hero") + [f"{CDN}/{a}/library_hero.jpg", f"{CDN_OLD}/{a}/library_hero.jpg"]
        g.cover = covers[0]
        g.extra["cover_alts"] = covers[1:]
        g.hero = heroes[0]
        g.extra["hero_alts"] = heroes[1:]
        g.horizontal = (self._local_art(a, "header") or [f"{CDN}/{a}/header.jpg"])[0]
        g.logo = (self._local_art(a, "logo") or [f"{CDN}/{a}/logo.png"])[0]
        g.store_url = f"https://store.steampowered.com/app/{a}"

    def _owned_web(self) -> list[dict]:
        key, sid = self.api_key(), self.steamid()
        if not key or not sid:
            return []
        r = self.session.get(f"{WEB_API}/IPlayerService/GetOwnedGames/v1/", params={
            "key": key, "steamid": sid, "include_appinfo": 1, "include_played_free_games": 1, "format": "json"})
        if not r.ok:
            log.warning("Steam GetOwnedGames failed: HTTP %s", r.status_code)
            return []
        return (r.json().get("response") or {}).get("games", []) or []

    def fetch_library(self) -> list[Game]:
        prev = {g.source_id: g for g in self.services.library.games_for_source(self.id)}
        manifests = self._manifests()
        local = self._localconfig()
        games: dict[str, Game] = {}
        for appid, (m, lib) in manifests.items():
            name = m.get("name") or appid
            if appid in TOOL_APPIDS or TOOL_NAME.match(name):
                continue
            g = prev.get(appid) or Game(self.id, appid, name, added_at=time.time())
            g.title = name
            flags = int(m.get("StateFlags", 0) or 0)
            g.installed = bool(flags & 4) or bool(flags & 2)
            g.update_available = bool(flags & 2)
            g.install_path = str(lib / "steamapps" / "common" / m.get("installdir", ""))
            g.install_size = int(m.get("SizeOnDisk", 0) or 0)
            g.version = str(m.get("buildid", ""))
            g.extra["library"] = str(lib)
            games[appid] = g
        for item in self._owned_web():
            appid = str(item.get("appid"))
            if appid in TOOL_APPIDS:
                continue
            g = games.get(appid) or prev.get(appid) or Game(self.id, appid, item.get("name", appid), added_at=time.time())
            if appid not in games:
                g.installed = False
                g.install_path = ""
            g.title = item.get("name") or g.title
            g.platform_playtime = int(item.get("playtime_forever", 0) or 0)
            g.platform_last_played = float(item.get("rtime_last_played", 0) or 0)
            games[appid] = g
        for appid, g in games.items():
            lc = local.get(appid) or {}
            if lc:
                g.platform_playtime = max(g.platform_playtime, int(lc.get("Playtime", 0) or 0))
                g.platform_last_played = max(g.platform_last_played, float(lc.get("LastPlayed", 0) or 0))
            self._art(g)
            g.platform = "" if not g.installed else g.platform
        return list(games.values())

    def enrich(self, games: list[Game]) -> list[Game]:
        """Fill genres/description from the public store API for games lacking metadata."""
        todo = [g for g in games if not g.extra.get("store_meta")][:60]
        changed = []
        for g in todo:
            try:
                r = self.session.get("https://store.steampowered.com/api/appdetails",
                                     params={"appids": g.source_id, "l": "english"}, timeout=10)
                data = (r.json() or {}).get(g.source_id, {}) if r.ok else {}
            except Exception:  # noqa: BLE001
                data = {}
            info = data.get("data") or {}
            if info:
                g.genres = [x.get("description") for x in info.get("genres", []) if x.get("description")] or g.genres
                g.developer = ", ".join(info.get("developers", []) or []) or g.developer
                g.publisher = ", ".join(info.get("publishers", []) or []) or g.publisher
                g.description = info.get("short_description") or g.description
                rd = (info.get("release_date") or {}).get("date", "")
                g.release_date = _steam_date(rd) or g.release_date
                plats = info.get("platforms") or {}
                g.os_support = [o for o, k in (("windows", "windows"), ("linux", "linux"), ("mac", "mac")) if plats.get(k)]
                if info.get("metacritic"):
                    g.critic_score = float(info["metacritic"].get("score", 0) or 0)
                g.extra["screenshots"] = [s.get("path_full") for s in info.get("screenshots", [])][:12]
                g.extra["movies"] = [(m.get("thumbnail", ""), (m.get("mp4") or {}).get("max", ""))
                                     for m in info.get("movies", [])][:4]
            g.extra["store_meta"] = time.time()
            changed.append(g)
            time.sleep(0.3)  # the store API is rate limited
        return changed

    # ------------------------------------------------------------ actions
    def _client_cmd(self) -> list[str]:
        if which("steam"):
            return ["steam"]
        if flatpak_installed("com.valvesoftware.Steam"):
            return ["flatpak", "run", "com.valvesoftware.Steam"]
        return []

    def _uri(self, uri: str, silent: bool = False) -> LaunchSpec:
        cmd = self._client_cmd()
        if cmd:
            # -silent: a Steam that isn't running yet starts in the tray instead of opening its library window
            return LaunchSpec(argv=cmd + (["-silent"] if silent else []) + [uri], description="Steam client")
        return LaunchSpec(uri=uri, description="Steam client")

    def prepare_launch(self, game: Game, task_id: str = "") -> LaunchSpec:
        return self._uri(f"steam://rungameid/{game.source_id}", silent=True)

    def process_hints(self, game: Game) -> dict:
        return {"steam_appid": game.source_id, "paths": [game.install_path] if game.installed else []}

    def _run_uri(self, uri: str) -> None:
        spec = self._uri(uri)
        if spec.uri:
            xdg_open(spec.uri)
        else:
            import subprocess

            subprocess.Popen(spec.argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def install(self, game: Game, request: InstallRequest, ctx) -> Game:
        ctx.stage("Handing over to Steam")
        self._run_uri(f"steam://install/{game.source_id}")
        return game

    def uninstall(self, game: Game, ctx) -> Game:
        ctx.stage("Handing over to Steam")
        self._run_uri(f"steam://uninstall/{game.source_id}")
        return game

    def repair(self, game: Game, ctx) -> Game:
        self._run_uri(f"steam://validate/{game.source_id}")
        return game

    def open_platform_client(self) -> None:
        self._run_uri("steam://open/games")

    def store_url(self, game: Game) -> str:
        return f"https://store.steampowered.com/app/{game.source_id}"

    # ------------------------------------------------- achievements/friends
    def achievements(self, game: Game) -> list[Achievement] | None:
        key, sid = self.api_key(), self.steamid()
        if not key or not sid:
            return None
        appid = game.source_id
        schema = self._schema_cache.get(appid)
        if schema is None:
            r = self.session.get(f"{WEB_API}/ISteamUserStats/GetSchemaForGame/v2/",
                                 params={"key": key, "appid": appid, "l": "english"})
            schema = {}
            if r.ok:
                for a in ((r.json().get("game") or {}).get("availableGameStats") or {}).get("achievements", []) or []:
                    schema[a["name"]] = a
            self._schema_cache[appid] = schema
        if not schema:
            return []
        r = self.session.get(f"{WEB_API}/ISteamUserStats/GetPlayerAchievements/v1/",
                             params={"key": key, "steamid": sid, "appid": appid})
        player = {}
        if r.ok:
            for a in (r.json().get("playerstats") or {}).get("achievements", []) or []:
                player[a["apiname"]] = a
        rarity = {}
        try:
            g = self.session.get(f"{WEB_API}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/",
                                 params={"gameid": appid}, timeout=10)
            if g.ok:
                for a in (g.json().get("achievementpercentages") or {}).get("achievements", []) or []:
                    rarity[a["name"]] = float(a.get("percent", 0))
        except Exception:  # noqa: BLE001
            pass
        out = []
        for name, a in schema.items():
            p = player.get(name, {})
            out.append(Achievement(
                id=name, name=a.get("displayName", name), description=a.get("description", ""),
                icon=a.get("icon", ""), icon_locked=a.get("icongray", ""),
                unlocked=bool(p.get("achieved")), unlocked_at=float(p.get("unlocktime", 0) or 0),
                rarity=rarity.get(name, -1.0), hidden=bool(a.get("hidden")),
            ))
        return out

    def friends(self) -> list[Friend]:
        key, sid = self.api_key(), self.steamid()
        if not key or not sid:
            return []
        r = self.session.get(f"{WEB_API}/ISteamUser/GetFriendList/v1/",
                             params={"key": key, "steamid": sid, "relationship": "friend"})
        if not r.ok:
            return []
        ids = [f["steamid"] for f in (r.json().get("friendslist") or {}).get("friends", [])]
        out = []
        for i in range(0, len(ids), 100):
            s = self.session.get(f"{WEB_API}/ISteamUser/GetPlayerSummaries/v2/",
                                 params={"key": key, "steamids": ",".join(ids[i:i + 100])})
            if not s.ok:
                continue
            for p in (s.json().get("response") or {}).get("players", []):
                playing = p.get("gameextrainfo", "")
                status = "playing" if playing else PERSONA.get(int(p.get("personastate", 0)), "offline")
                out.append(Friend("steam", p["steamid"], p.get("personaname", ""), avatar=p.get("avatarfull", ""),
                                  status=status, playing=playing, profile_url=p.get("profileurl", ""),
                                  last_online=float(p.get("lastlogoff", 0) or 0)))
        return out


def _steam_date(text: str) -> str:
    for fmt in ("%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%Y"):
        try:
            return time.strftime("%Y-%m-%d", time.strptime(text, fmt))
        except ValueError:
            continue
    return ""
