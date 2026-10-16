"""GOG Galaxy cloud saves, resolved inside each game's Proton/Wine prefix."""
from __future__ import annotations

import gzip
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from nebula.core.utils import case_insensitive_path
from nebula.integrations.base import CloudSyncResult
from nebula.integrations.gog.api import CLOUD, ApiError, GogApi

log = logging.getLogger(__name__)

GALAXY_UA = ("GOGGalaxyCommunicationService/2.0.13.27 (Windows_32bit) dont_sync_marker/true "
             "installation_source/gog")
DELETED_MD5 = "aadd86936a80ee8a369579c3926f1b3c"


@dataclass
class LocalFile:
    rel: str
    path: Path
    mtime: float
    md5: str


@dataclass
class CloudFile:
    rel: str
    md5: str
    mtime: float


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: str) -> float:
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def gz_md5(data: bytes) -> str:
    return hashlib.md5(gzip.compress(data, 6, mtime=0)).hexdigest()


class CloudSaves:
    def __init__(self, api: GogApi, compat, db):
        self.api = api
        self.compat = compat
        self.db = db

    # --------------------------------------------------------- locations
    def resolve(self, location: str, uid: str, install_path: str) -> Path:
        user = self.compat.user_dir(uid)
        drive_c = self.compat.drive_c(uid)
        variables = {
            "INSTALL": install_path,
            "SAVED_GAMES": str(user / "Saved Games"),
            "APPLICATION_DATA_LOCAL": str(user / "AppData" / "Local"),
            "APPLICATION_DATA_LOCAL_LOW": str(user / "AppData" / "LocalLow"),
            "APPLICATION_DATA_ROAMING": str(user / "AppData" / "Roaming"),
            "DOCUMENTS": str(user / "Documents"),
        }
        path = re.sub(r"<\?(\w+)\?>", lambda m: variables.get(m.group(1), m.group(0)), location)
        win_env = {
            "localappdata": user / "AppData" / "Local",
            "appdata": user / "AppData" / "Roaming",
            "userprofile": user,
            "public": drive_c / "users" / "Public",
            "programdata": drive_c / "ProgramData",
            "documents": user / "Documents",
        }
        path = re.sub(r"%(\w+)%", lambda m: str(win_env.get(m.group(1).lower(), m.group(0))), path)
        path = path.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", path):
            letter = path[0].lower()
            path = str(drive_c) + path[2:] if letter == "c" else path[2:]
        return case_insensitive_path(Path(os.path.normpath(path)))

    def locations(self, client_id: str, uid: str, install_path: str) -> list[tuple[str, Path]]:
        cfg = self.api.remote_config(client_id)
        content = (cfg.get("content") or {}).get("Windows") or {}
        storage = content.get("cloudStorage") or {}
        if not storage.get("enabled"):
            return []
        locs = storage.get("locations") or [{
            "name": "__default",
            "location": f"%LocalAppData%/GOG.com/Galaxy/Applications/{client_id}/Storage/Shared/Files",
        }]
        return [(loc["name"], self.resolve(loc["location"], uid, install_path)) for loc in locs]

    # --------------------------------------------------------------- sync
    def _session(self, client_id: str, client_secret: str) -> tuple[requests.Session, str]:
        creds = self.api.auth.game_credentials(client_id, client_secret)
        s = requests.Session()
        s.headers.update({
            "User-Agent": GALAXY_UA,
            "X-Object-Meta-User-Agent": GALAXY_UA,
            "Authorization": f"Bearer {creds['access_token']}",
        })
        return s, str(creds.get("user_id") or self.api.auth.user_id)

    def _local(self, root: Path) -> list[LocalFile]:
        out = []
        if not root.is_dir():
            return out
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                p = Path(dirpath) / name
                try:
                    data = p.read_bytes()
                    out.append(LocalFile(p.relative_to(root).as_posix(), p, p.stat().st_mtime, gz_md5(data)))
                except OSError:
                    continue
        return out

    def _cloud(self, s: requests.Session, user_id: str, client_id: str, dirname: str) -> list[CloudFile]:
        r = s.get(f"{CLOUD}/v1/{user_id}/{client_id}", headers={"Accept": "application/json"}, timeout=30)
        if r.status_code == 404:
            return []
        if not r.ok:
            raise ApiError(f"cloud storage HTTP {r.status_code}")
        out = []
        prefix = dirname + "/"
        for item in r.json() or []:
            name = item.get("name", "")
            if not name.startswith(prefix) or item.get("hash") == DELETED_MD5:
                continue
            out.append(CloudFile(name[len(prefix):], item.get("hash", ""), _parse_ts(item.get("last_modified", ""))))
        return out

    def _upload(self, s, user_id, client_id, dirname, f: LocalFile) -> None:
        data = gzip.compress(f.path.read_bytes(), 6, mtime=0)
        r = s.put(f"{CLOUD}/v1/{user_id}/{client_id}/{dirname}/{quote(f.rel)}", data=data, timeout=60, headers={
            "X-Object-Meta-LocalLastModified": _iso(f.mtime),
            "Etag": hashlib.md5(data).hexdigest(),
            "Content-Encoding": "gzip",
        })
        if not r.ok:
            raise ApiError(f"upload of {f.rel} failed (HTTP {r.status_code})")

    def _download(self, s, user_id, client_id, dirname, root: Path, f: CloudFile) -> None:
        r = s.get(f"{CLOUD}/v1/{user_id}/{client_id}/{dirname}/{quote(f.rel)}", timeout=60)
        if not r.ok:
            raise ApiError(f"download of {f.rel} failed (HTTP {r.status_code})")
        target = root / f.rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".nbcloud")
        tmp.write_bytes(r.content)
        os.replace(tmp, target)
        ts = _parse_ts(r.headers.get("X-Object-Meta-LocalLastModified", "")) or f.mtime
        if ts:
            os.utime(target, (ts, ts))

    def sync(self, uid: str, install_path: str, client_id: str, client_secret: str,
             mode: str = "auto", conflict_policy: str = "ask") -> CloudSyncResult:
        """mode: auto | upload | download (forced direction)."""
        locations = self.locations(client_id, uid, install_path)
        if not locations:
            return CloudSyncResult("unsupported", "This game does not use GOG cloud saves")
        s, user_id = self._session(client_id, client_secret)
        cfg = self.db.get_config(uid)
        stamps = dict(cfg.get("cloud_timestamps") or {})
        actions, moved = [], 0
        for name, root in locations:
            local = self._local(root)
            cloud = self._cloud(s, user_id, client_id, name)
            last = float(stamps.get(name, 0))
            local_map = {f.rel: f for f in local}
            cloud_map = {f.rel: f for f in cloud}
            if mode == "upload":
                action = "upload"
            elif mode == "download":
                action = "download"
            elif not local and not cloud:
                action = "none"
            elif local and not cloud:
                action = "upload"
            elif cloud and not local:
                action = "download"
            elif all(r in cloud_map and cloud_map[r].md5 == f.md5 for r, f in local_map.items()) and \
                    set(local_map) == set(cloud_map):
                action = "none"
            else:
                newer_local = [f for f in local if f.mtime > last + 1]
                newer_cloud = [f for f in cloud if f.mtime > last + 1]
                if newer_local and newer_cloud:
                    if conflict_policy == "newest":
                        action = "upload" if max(f.mtime for f in local) >= max(f.mtime for f in cloud) else "download"
                    else:
                        return CloudSyncResult("conflict", f"Local and cloud saves both changed ({name})", details={
                            "location": name,
                            "local_time": max(f.mtime for f in local),
                            "cloud_time": max(f.mtime for f in cloud),
                        })
                elif newer_local:
                    action = "upload"
                elif newer_cloud:
                    action = "download"
                else:
                    # out of sync without timestamps: prefer the newest side
                    action = "upload" if max(f.mtime for f in local) >= max(f.mtime for f in cloud) else "download"
            if action == "upload":
                for f in local:
                    c = cloud_map.get(f.rel)
                    if c is None or c.md5 != f.md5:
                        self._upload(s, user_id, client_id, name, f)
                        moved += 1
            elif action == "download":
                for f in cloud:
                    lf = local_map.get(f.rel)
                    if lf is None or lf.md5 != f.md5:
                        self._download(s, user_id, client_id, name, root, f)
                        moved += 1
            stamps[name] = time.time()
            actions.append(action)
        self.db.update_config(uid, cloud_timestamps=stamps)
        final = "upload" if "upload" in actions else "download" if "download" in actions else "none"
        msg = {"upload": f"Uploaded {moved} save file(s)", "download": f"Downloaded {moved} save file(s)",
               "none": "Saves are up to date"}[final]
        return CloudSyncResult(final, msg, files=moved,
                               details={"locations": [(n, str(p)) for n, p in locations]})
