"""Desktop / application-menu shortcuts for games."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from nebula import paths
from nebula.core.utils import slugify


def launcher_command() -> str:
    exe = shutil.which("supernova") or shutil.which("nebula")  # "nebula": installs from before the rename
    if exe:
        return exe
    return f"{sys.executable} -m nebula"


def _icon_for(uid: str, urls: list[str]) -> str:
    from nebula.core.images import images

    for url in urls:
        f = images().fetch_file(url)
        if f:
            target = paths.sub(paths.data_dir(), "icons") / (slugify(uid) + Path(str(f)).suffix)
            try:
                shutil.copyfile(f, target)
                return str(target)
            except OSError:
                continue
    return "applications-games"


def desktop_entry(uid: str, title: str, icon: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={title}\n"
        f"Comment=Play {title} with Super Nova\n"
        f"Exec={launcher_command()} --launch {uid}\n"
        f"Icon={icon}\n"
        "Terminal=false\n"
        "Categories=Game;\n"
        f"X-Nebula-Game={uid}\n"
    )


def create(uid: str, title: str, icon_urls: list[str], desktop: bool = True, menu: bool = True) -> list[Path]:
    """Blocking: write .desktop files for the game. Returns created paths."""
    icon = _icon_for(uid, icon_urls)
    content = desktop_entry(uid, title, icon)
    name = f"nebula-{slugify(uid)}.desktop"
    created = []
    targets = []
    if menu:
        targets.append(paths.applications_dir() / name)
    if desktop:
        d = paths.desktop_dir()
        if d.is_dir():
            targets.append(d / name)
    for t in targets:
        t.write_text(content)
        os.chmod(t, 0o755)
        if shutil.which("gio"):
            subprocess.run(["gio", "set", str(t), "metadata::trusted", "true"], capture_output=True)
        created.append(t)
    return created


def remove(uid: str) -> None:
    name = f"nebula-{slugify(uid)}.desktop"
    for t in (paths.applications_dir() / name, paths.desktop_dir() / name):
        try:
            t.unlink()
        except OSError:
            pass


def exists(uid: str) -> bool:
    return (paths.applications_dir() / f"nebula-{slugify(uid)}.desktop").exists()
