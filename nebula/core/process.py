"""Detect running games by scanning /proc (Linux binaries, Wine/Proton processes and Steam)."""
from __future__ import annotations

import os
from dataclasses import dataclass

INTERPRETERS = {
    "sh", "bash", "dash", "zsh", "env", "python", "python3", "mono", "java", "wine", "wine64",
    "wine-preloader", "wine64-preloader", "wineloader", "steam-runtime-launch-client",
}


@dataclass
class Proc:
    pid: int
    exe: str
    argv: list[str]


def snapshot() -> list[Proc]:
    out = []
    me = os.getpid()
    try:
        pids = [int(p) for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return out
    for pid in pids:
        if pid == me:
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                raw = fh.read()
        except OSError:
            continue
        if not raw:
            continue
        argv = [a.decode("utf-8", "replace") for a in raw.rstrip(b"\0").split(b"\0")]
        try:
            exe = os.readlink(f"/proc/{pid}/exe")
        except OSError:
            exe = ""
        out.append(Proc(pid, exe, argv))
    return out


def _norm_unix(p: str) -> str:
    return os.path.normpath(p).rstrip("/") + "/"


def _win_to_unix(p: str) -> str:
    q = p.replace("\\", "/")
    if len(q) > 2 and q[1] == ":" and q[0] in "zZ":
        return q[2:]
    return q


def match(procs: list[Proc], hints: dict[str, dict]) -> dict[str, list[int]]:
    """Return uid -> pids of processes that belong to each game."""
    prepared = []
    for uid, h in hints.items():
        roots = [_norm_unix(p) for p in h.get("paths", []) if p and len(p) > 5 and p != os.path.expanduser("~")]
        prepared.append((uid, roots, [r.lower() for r in roots], h.get("steam_appid"), h.get("exe")))
    found: dict[str, list[int]] = {}
    for proc in procs:
        argv0 = proc.argv[0] if proc.argv else ""
        cands = [proc.exe, argv0]
        if os.path.basename(argv0) in INTERPRETERS and len(proc.argv) > 1:
            cands.append(proc.argv[1])
        win_cands = [_win_to_unix(c).lower() for c in cands if "\\" in c or (len(c) > 1 and c[1] == ":")]
        steam_launch = "SteamLaunch" in proc.argv
        for uid, roots, lroots, appid, exe in prepared:
            hit = False
            if appid and steam_launch and f"AppId={appid}" in proc.argv:
                hit = True
            elif exe and exe in cands:
                hit = True
            else:
                for c in cands:
                    if c and any(c.startswith(r) for r in roots):
                        hit = True
                        break
                if not hit:
                    for c in win_cands:
                        if any(c.startswith(r) for r in lroots):
                            hit = True
                            break
            if hit:
                found.setdefault(uid, []).append(proc.pid)
    return found
