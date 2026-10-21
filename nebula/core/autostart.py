"""Start Super Nova when the user logs in (freedesktop autostart entry)."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from nebula import APP_ID, APP_NAME, LEGACY_APP_ID


def entry_path(app_id: str = APP_ID) -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return config / "autostart" / f"{app_id}.desktop"


def migrate() -> None:
    """An autostart entry written before the rename becomes the new one."""
    if entry_path(LEGACY_APP_ID).exists():
        apply(True)


def apply(enabled: bool) -> None:
    entry_path(LEGACY_APP_ID).unlink(missing_ok=True)
    path = entry_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    exe = shutil.which("supernova") or shutil.which("nebula") or f"{sys.executable} -m nebula"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[Desktop Entry]\nType=Application\nName={APP_NAME}\nExec={exe}\nIcon={APP_ID}\n"
                    "X-GNOME-Autostart-enabled=true\nTerminal=false\n")
