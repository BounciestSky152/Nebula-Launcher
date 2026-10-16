"""GOG offline installers: native Linux ``.sh`` packages and Windows setup fallbacks."""
from __future__ import annotations

import logging
import os
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

from nebula import paths
from nebula.core.download import download_file
from nebula.core.jobs import JobContext
from nebula.core.utils import which
from nebula.integrations.gog.api import GogApi

log = logging.getLogger(__name__)

LANG_PREF = ["en"]


def installers_for(product: dict, os_name: str) -> list[dict]:
    return [i for i in (product.get("downloads", {}) or {}).get("installers", []) if i.get("os") == os_name]


def pick_installer(installers: list[dict], language: str) -> dict | None:
    if not installers:
        return None
    short = (language or "en").split("-")[0].lower()
    for wanted in (short, "en"):
        for inst in installers:
            if str(inst.get("language", "")).lower() == wanted:
                return inst
    return installers[0]


def installer_languages(installers: list[dict]) -> list[tuple[str, str]]:
    out = []
    for inst in installers:
        code = inst.get("language", "")
        label = inst.get("language_full") or code
        if (code, label) not in out:
            out.append((code, label))
    return out


def download_installer(api: GogApi, session: requests.Session, ctx: JobContext, installer: dict,
                       dest_dir: Path) -> list[Path]:
    files = installer.get("files", [])
    total = int(installer.get("total_size") or sum(int(f.get("size", 0)) for f in files))
    ctx.set_total(total)
    ctx.set_done(0)
    out = []
    for idx, f in enumerate(files, 1):
        ctx.check()
        ctx.stage(f"Downloading installer ({idx}/{len(files)})")
        info = api.downlink(f["downlink"])
        real = info.get("downlink")
        if not real:
            raise IOError("GOG did not return a download link")
        chk = api.checksum_info(info.get("checksum", ""))
        name = unquote(urlparse(real).path.rsplit("/", 1)[-1]) or f"part{idx}"
        size = int(chk.get("total_size") or f.get("size") or 0)
        out.append(download_file(session, real, dest_dir / name, ctx, expected_size=size,
                                 expected_md5=chk.get("md5", "")))
    return out


def extract_linux_installer(ctx: JobContext, installer_path: Path, install_dir: Path) -> None:
    """Unpack the zip payload of a GOG MojoSetup ``.sh`` installer (``data/noarch`` → install dir)."""
    install_dir.mkdir(parents=True, exist_ok=True)
    root = install_dir.resolve()
    with zipfile.ZipFile(installer_path) as z:
        members = [m for m in z.infolist() if m.filename.startswith("data/noarch/")]
        if not members:
            raise IOError("Installer does not contain game data (data/noarch)")
        ctx.set_total(sum(m.file_size for m in members))
        ctx.set_done(0)
        ctx.stage("Extracting")
        for m in members:
            ctx.check()
            rel = m.filename[len("data/noarch/"):]
            if not rel:
                continue
            dest = (install_dir / rel)
            if root not in dest.resolve().parents and dest.resolve() != root:
                raise IOError(f"Unsafe path in installer: {m.filename}")
            mode = (m.external_attr >> 16) & 0xFFFF
            if m.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            if stat.S_ISLNK(mode):
                target = z.read(m).decode("utf-8", "replace")
                if dest.is_symlink() or dest.exists():
                    dest.unlink()
                os.symlink(target, dest)
                ctx.add(m.file_size, network=False)
                continue
            with z.open(m) as src, open(dest, "wb") as dst:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    dst.write(block)
                    ctx.add(len(block), network=False)
            if mode & 0o777:
                os.chmod(dest, mode & 0o777)


def extract_windows_installer(ctx: JobContext, setup_exe: Path, install_dir: Path, compat=None,
                              uid: str = "") -> str:
    """Install a Windows offline installer, preferring innoextract (no Wine needed)."""
    install_dir.mkdir(parents=True, exist_ok=True)
    tool = which("innoextract")
    if tool:
        ctx.stage("Extracting with innoextract")
        tmp = install_dir.parent / f".{install_dir.name}.nbextract"
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True)
        proc = subprocess.run([tool, "--gog", "--exclude-temp", "--silent", "-d", str(tmp), str(setup_exe)],
                              capture_output=True, text=True)
        if proc.returncode == 0:
            src = tmp / "app" if (tmp / "app").is_dir() else tmp
            for item in src.iterdir():
                dest = install_dir / item.name
                if dest.exists():
                    if dest.is_dir() and not dest.is_symlink():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
            shutil.rmtree(tmp, ignore_errors=True)
            return "innoextract"
        log.warning("innoextract failed (%s), falling back to running the installer", proc.stderr.strip()[:300])
        shutil.rmtree(tmp, ignore_errors=True)
    if compat is None:
        raise IOError("innoextract is not installed and no Proton/Wine runner is available")
    ctx.stage("Running installer with Proton (silent)")
    win_dir = "Z:" + str(install_dir).replace("/", "\\")
    code = compat.run_in_prefix(uid, [str(setup_exe), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-",
                                      "/NOICONS", f"/DIR={win_dir}"], cwd=str(setup_exe.parent), timeout=4 * 3600)
    if code != 0 and not any(install_dir.iterdir()):
        raise IOError(f"The installer exited with code {code}")
    return "installer"


def cache_dir_for(product_id: str) -> Path:
    p = paths.installers_cache() / str(product_id)
    p.mkdir(parents=True, exist_ok=True)
    return p
