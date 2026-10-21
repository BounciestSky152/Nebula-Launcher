"""Download and manage Proton builds (GE-Proton) and umu-launcher."""
from __future__ import annotations

import logging
import shutil
import stat
import tarfile
import time
from pathlib import Path

import requests

from nebula import paths
from nebula.core.download import download_file, sha512
from nebula.core.http import new_session
from nebula.core.jobs import JobContext

log = logging.getLogger(__name__)

GE_PROTON_RELEASES = "https://api.github.com/repos/GloriousEggroll/proton-ge-custom/releases?per_page=15"
UMU_LATEST = "https://api.github.com/repos/Open-Wine-Components/umu-launcher/releases/latest"
UMU_DB_API = "https://umu.openwinecomponents.org/umu_api.php"


def _session() -> requests.Session:
    s = new_session()
    s.headers["Accept"] = "application/vnd.github+json"
    return s


def list_ge_proton(session: requests.Session | None = None) -> list[dict]:
    """Return available GE-Proton releases (newest first)."""
    s = session or _session()
    r = s.get(GE_PROTON_RELEASES, timeout=20)
    r.raise_for_status()
    out = []
    for rel in r.json():
        if rel.get("draft"):
            continue
        tarball = next((a for a in rel.get("assets", []) if a["name"].endswith(".tar.gz")), None)
        if not tarball:
            continue
        checksum = next((a for a in rel.get("assets", []) if a["name"].endswith(".sha512sum")), None)
        out.append({
            "tag": rel.get("tag_name", tarball["name"][:-7]),
            "name": rel.get("name") or rel.get("tag_name"),
            "date": rel.get("published_at", ""),
            "url": tarball["browser_download_url"],
            "size": tarball.get("size", 0),
            "sha512_url": checksum["browser_download_url"] if checksum else "",
            "prerelease": rel.get("prerelease", False),
        })
    return out


def installed_ge_tags() -> set[str]:
    out = set()
    for d in paths.proton_dir().iterdir():
        if (d / "proton").exists():
            out.add(d.name)
    return out


def _safe_extract(tar: tarfile.TarFile, dest: Path, ctx: JobContext | None = None) -> None:
    dest = dest.resolve()
    members = tar.getmembers()
    for m in members:
        target = (dest / m.name).resolve()
        if dest != target and dest not in target.parents:
            raise IOError(f"Unsafe path in archive: {m.name}")
    try:
        tar.extractall(dest, filter="tar")  # Python >= 3.12 (keeps symlinks/permissions)
    except TypeError:  # pragma: no cover - older Pythons
        tar.extractall(dest)


def install_ge_proton(ctx: JobContext, release: dict) -> str:
    """Job function: download, verify and unpack one GE-Proton release."""
    s = _session()
    tmpdir = paths.cache_dir() / "runner-downloads"
    tmpdir.mkdir(parents=True, exist_ok=True)
    archive = tmpdir / release["url"].rsplit("/", 1)[-1]
    ctx.stage(f"Downloading {release['tag']}")
    ctx.set_total(release.get("size") or 0)
    download_file(s, release["url"], archive, ctx, expected_size=release.get("size") or 0)
    if release.get("sha512_url"):
        ctx.stage("Verifying checksum")
        expected = s.get(release["sha512_url"], timeout=20).text.split()[0].strip()
        if expected and sha512(archive) != expected:
            archive.unlink(missing_ok=True)
            raise IOError("GE-Proton checksum mismatch - download corrupted")
    ctx.check()
    ctx.stage("Extracting")
    dest = paths.proton_dir()
    with tarfile.open(archive) as tar:
        _safe_extract(tar, dest, ctx)
    archive.unlink(missing_ok=True)
    ctx.stage("Installed")
    return release["tag"]


def install_umu(ctx: JobContext) -> str:
    """Job function: install the umu-launcher zipapp into Super Nova's runner directory."""
    s = _session()
    ctx.stage("Looking up latest umu-launcher")
    rel = s.get(UMU_LATEST, timeout=20)
    rel.raise_for_status()
    data = rel.json()
    asset = next((a for a in data.get("assets", []) if "zipapp" in a["name"] and a["name"].endswith(".tar")), None)
    if not asset:
        raise IOError("No umu-launcher zipapp asset found in the latest release")
    tmp = paths.cache_dir() / "runner-downloads" / asset["name"]
    ctx.set_total(asset.get("size", 0))
    ctx.stage(f"Downloading umu-launcher {data.get('tag_name', '')}")
    download_file(s, asset["browser_download_url"], tmp, ctx, expected_size=asset.get("size", 0))
    ctx.stage("Extracting")
    dest = paths.umu_dir()
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tmp) as tar:
        _safe_extract(tar, dest, ctx)
    tmp.unlink(missing_ok=True)
    for p in dest.rglob("umu-run"):
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return str(p)
    raise IOError("umu-run not found in archive")


def remove_proton(path: str) -> None:
    p = Path(path).resolve()
    if paths.proton_dir().resolve() not in p.parents:
        raise ValueError("Only Proton builds downloaded by Super Nova can be removed")
    shutil.rmtree(p)


def umu_game_id(db, store: str, codename: str, session: requests.Session | None = None) -> str:
    """Look up the umu-database id for protonfixes (cached for a week)."""
    if not store or not codename:
        return "umu-0"
    store = {"epic": "egs"}.get(store, store)
    key = f"umu_id:{store}:{codename}"
    cached = db.kv_get(key)
    if cached and time.time() - cached.get("ts", 0) < 7 * 86400:
        return cached["id"]
    umu_id = "umu-0"
    try:
        s = session or new_session()
        r = s.get(UMU_DB_API, params={"store": store, "codename": codename}, timeout=4)
        if r.ok:
            rows = r.json()
            if isinstance(rows, list) and rows and rows[0].get("umu_id"):
                umu_id = rows[0]["umu_id"]
    except (requests.RequestException, ValueError):
        pass
    db.kv_set(key, {"id": umu_id, "ts": time.time()})
    return umu_id
