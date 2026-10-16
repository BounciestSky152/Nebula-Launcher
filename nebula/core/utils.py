"""Small helpers shared across the code base."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path


def human_size(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_speed(bps: float) -> str:
    return human_size(bps) + "/s"


def human_duration(seconds: float, short: bool = False) -> str:
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m" if not short else f"{h}h"
    if m:
        return f"{m}m" if short else f"{m} min"
    return f"{s}s" if seconds else ("0h" if short else "0 min")


def playtime_text(minutes: int) -> str:
    if not minutes:
        return "Not played"
    h, m = divmod(int(minutes), 60)
    if h == 0:
        return f"{m} min"
    if h < 10 and m:
        return f"{h}h {m}m"
    return f"{h} h"


def relative_time(ts: float) -> str:
    if not ts:
        return "Never"
    delta = time.time() - ts
    if delta < 60:
        return "Just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    days = int(delta // 86400)
    if days == 1:
        return "Yesterday"
    if days < 30:
        return f"{days} days ago"
    return datetime.fromtimestamp(ts).strftime("%d %b %Y")


def eta_text(seconds: float) -> str:
    if seconds <= 0 or seconds == float("inf"):
        return "--:--"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def which(*names: str) -> str | None:
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def flatpak_installed(app_id: str) -> bool:
    if not shutil.which("flatpak"):
        return False
    try:
        return subprocess.run(["flatpak", "info", app_id], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def xdg_open(target: str) -> bool:
    opener = which("xdg-open", "gio")
    if not opener:
        return False
    cmd = [opener, target] if opener.endswith("xdg-open") else [opener, "open", target]
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def md5_file(path: str | Path, bufsize: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(bufsize), b""):
            h.update(chunk)
    return h.hexdigest()


def case_insensitive_path(path: str | Path) -> Path:
    """Resolve ``path`` on a case-sensitive filesystem ignoring letter case."""
    p = Path(path)
    if p.exists():
        return p
    parts = p.parts
    cur = Path(parts[0])
    for i, part in enumerate(parts[1:], start=1):
        candidate = cur / part
        if candidate.exists():
            cur = candidate
            continue
        try:
            lower = part.lower()
            match = next((c for c in os.listdir(cur) if c.lower() == lower), None)
        except OSError:
            match = None
        if match is None:
            return cur.joinpath(*parts[i:])
        cur = cur / match
    return cur


def dir_size(path: str | Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def safe_rmtree(path: str | Path, must_contain: str | Path | None = None) -> None:
    """Remove a directory tree, refusing obviously dangerous targets."""
    p = Path(path).resolve()
    home = Path.home().resolve()
    forbidden = {Path("/"), home, home.parent, Path("/usr"), Path("/etc"), Path("/opt"), Path("/var")}
    if p in forbidden or len(p.parts) < 3:
        raise ValueError(f"Refusing to delete {p}")
    if must_contain is not None and not (p / must_contain).exists():
        raise ValueError(f"Refusing to delete {p}: marker {must_contain} missing")
    if p.is_symlink():
        p.unlink()
    elif p.exists():
        shutil.rmtree(p)


def slugify(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    return s or "game"


def sanitize_dirname(text: str) -> str:
    s = re.sub(r'[\\/:*?"<>|]+', "", text).strip().strip(".")
    return s or "Game"
