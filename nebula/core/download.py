"""Resumable streaming file download used by installers and runner downloads."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import requests

from nebula.core.jobs import JobContext

CHUNK = 256 * 1024


def download_file(
    session: requests.Session,
    url: str,
    dest: Path,
    ctx: JobContext | None = None,
    expected_size: int = 0,
    expected_md5: str = "",
    headers: dict | None = None,
    count_progress: bool = True,
) -> Path:
    """Download ``url`` to ``dest`` resuming a previous ``.part`` file when possible."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and expected_size and dest.stat().st_size == expected_size:
        if not expected_md5 or _md5(dest) == expected_md5:
            if ctx and count_progress:
                ctx.add(expected_size, network=False)
            return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if expected_size and have > expected_size:
        part.unlink()
        have = 0
    hdrs = dict(headers or {})
    if have:
        hdrs["Range"] = f"bytes={have}-"
    with session.get(url, headers=hdrs, stream=True, timeout=(15, 60)) as r:
        if r.status_code == 416:  # already complete
            r.close()
        else:
            if r.status_code not in (200, 206):
                raise IOError(f"Download failed: HTTP {r.status_code} for {dest.name}")
            if r.status_code == 200 and have:
                have = 0  # server ignored the range; restart
            if ctx and count_progress and have:
                ctx.add(have, network=False)
            mode = "ab" if have else "wb"
            with open(part, mode) as fh:
                for block in r.iter_content(CHUNK):
                    if ctx:
                        ctx.check()
                    if not block:
                        continue
                    fh.write(block)
                    if ctx:
                        if count_progress:
                            ctx.add(len(block))
                        else:
                            ctx.add_network(len(block))
    if expected_md5:
        if ctx:
            ctx.stage(f"Verifying {dest.name}")
        actual = _md5(part)
        if actual.lower() != expected_md5.lower():
            part.unlink(missing_ok=True)
            raise IOError(f"Checksum mismatch for {dest.name} (expected {expected_md5}, got {actual})")
    os.replace(part, dest)
    return dest


def _md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
