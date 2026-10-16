"""Library merging/filters, VDF, process detection, jobs, auth helpers."""
import os
import subprocess
import sys
import threading
import time

import pytest

from nebula.core import process, vdf
from nebula.core.library import LibraryFilter
from nebula.core.models import Game, title_key


def test_vdf_parser():
    data = vdf.loads('''
    // comment
    "Root"
    {
        "key"   "value with \\"quotes\\""
        "Nested" { "a" "1" "B" "two" }
        unquoted token
        "flag" "x" [$WIN32]
    }''')
    assert data["Root"]["key"] == 'value with "quotes"'
    assert vdf.get(data, "root", "nested", "b") == "two"
    assert data["Root"]["unquoted"] == "token"


def test_title_keys_merge_editions():
    assert title_key("The Witcher\u00ae 3: Wild Hunt") == title_key("the witcher 3 wild hunt")
    assert title_key("Baldur's Gate II") == title_key("Baldurs Gate II")
    assert title_key("Heroes & Villains") == title_key("Heroes and Villains")


def test_library_merge_filters_and_playtime(services):
    lib = services.library
    db = services.db
    now = time.time()
    games = [
        Game("gog", "1", "Cyberpunk 2077", installed=True, platform="windows", os_support=["windows"],
             genres=["RPG"], added_at=now - 100),
        Game("steam", "1091500", "Cyberpunk 2077\u2122", platform_playtime=600, platform_last_played=now - 50,
             genres=["RPG"], added_at=now - 10),
        Game("steam", "620", "Portal 2", installed=True, genres=["Puzzle"], platform_playtime=90),
        Game("gog", "2", "Stardew Valley", os_support=["linux", "windows"], genres=["Simulation"]),
        Game("gog", "3", "Some DLC", is_dlc=True),
    ]
    for src in ("gog", "steam"):
        db.replace_source(src, [g for g in games if g.source == src])
    db.add_session("gog:1", title_key("Cyberpunk 2077"), now - 7200, now - 3600)
    lib.load()
    assert len(lib.entries) == 3, "DLC hidden and Cyberpunk merged across GOG + Steam"
    cp = lib.entry_for_uid("steam:1091500")
    assert cp.sources == ["gog", "steam"] and cp.primary.source == "gog", "installed copy is primary"
    assert cp.playtime_minutes == 60 + 600 and cp.sessions == 1
    lib.set_user(cp.key, tags=["rpg"], rating=5, favorite=True)
    assert [e.title for e in lib.query(LibraryFilter(tags=["rpg"]))] == ["Cyberpunk 2077"]
    assert [e.title for e in lib.query(LibraryFilter(installed="not_installed"))] == ["Stardew Valley"]
    assert [e.title for e in lib.query(LibraryFilter(os=["linux"]))] == ["Stardew Valley"]
    assert [e.title for e in lib.query(LibraryFilter(sources=["steam"], sort="playtime"))] == ["Cyberpunk 2077", "Portal 2"]
    assert [e.title for e in lib.query(LibraryFilter(text="portal"))] == ["Portal 2"]
    lib.set_user(cp.key, hidden=True)
    assert "Cyberpunk 2077" not in [e.title for e in lib.query(LibraryFilter())]
    assert [e.title for e in lib.query(LibraryFilter(hidden="only"))] == ["Cyberpunk 2077"]
    lib.set_user(cp.key, preferred_uid="steam:1091500")
    assert lib.entry(cp.key).primary.uid == "steam:1091500"
    stats = lib.stats()
    assert stats["owned"] == 2 and stats["minutes"] == 90


def test_process_matching(tmp_path):
    game_dir = tmp_path / "Games" / "Witcher"
    procs = [
        process.Proc(10, "/usr/bin/wine64-preloader", ["Z:\\" + str(game_dir / "bin" / "witcher3.exe")[1:].replace("/", "\\")]),
        process.Proc(11, "/usr/bin/bash", ["/bin/bash", str(tmp_path / "Games" / "Stardew" / "start.sh")]),
        process.Proc(12, "/home/x/.steam/ubuntu12_32/reaper", ["reaper", "SteamLaunch", "AppId=620", "--", "x"]),
        process.Proc(13, "/usr/bin/nautilus", ["nautilus", str(game_dir)]),
    ]
    hints = {
        "gog:w": {"paths": [str(game_dir)]},
        "gog:s": {"paths": [str(tmp_path / "Games" / "Stardew")]},
        "steam:620": {"steam_appid": "620", "paths": []},
        "steam:570": {"steam_appid": "570", "paths": []},
    }
    found = process.match(procs, hints)
    assert found == {"gog:w": [10], "gog:s": [11], "steam:620": [12]}


def test_real_process_snapshot_detects_child(tmp_path):
    script = tmp_path / "Game" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nsleep 5\n")
    script.chmod(0o755)
    p = subprocess.Popen(["/bin/sh", str(script)])
    try:
        time.sleep(0.3)
        found = process.match(process.snapshot(), {"local:x": {"paths": [str(script.parent)]}})
        assert p.pid in found.get("local:x", [])
    finally:
        p.kill()


def test_jobs_pause_resume_cancel(qapp, services):
    from PySide6.QtCore import QCoreApplication

    progress = []
    gate = threading.Event()

    def work(ctx):
        ctx.set_total(100)
        for _ in range(100):
            ctx.check()
            ctx.add(1, network=False)
            progress.append(1)
            if len(progress) == 10:
                gate.set()
            time.sleep(0.01)
        return "ok"

    results = []
    job = services.jobs.submit("T", "install", work, uid="x:1", on_success=results.append)
    gate.wait(5)
    services.jobs.pause(job.id)
    time.sleep(0.2)
    n = len(progress)
    time.sleep(0.3)
    assert len(progress) <= n + 1, "paused job must not make progress"
    services.jobs.resume(job.id)
    deadline = time.time() + 10
    while job.active and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    QCoreApplication.processEvents()
    assert job.state == "done" and results == ["ok"]

    def forever(ctx):
        while True:
            ctx.check()
            time.sleep(0.01)

    j2 = services.jobs.submit("T2", "install", forever)
    time.sleep(0.2)
    services.jobs.cancel(j2.id)
    deadline = time.time() + 5
    while j2.active and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert j2.state == "canceled"


def test_rate_limiter_throttles():
    from nebula.core.jobs import RateLimiter

    rl = RateLimiter(lambda: 1_000_000)
    start = time.monotonic()
    for _ in range(30):
        rl.consume(100_000)  # 3 MB at 1 MB/s with a 1 s burst -> ~2 s
    elapsed = time.monotonic() - start
    assert 1.6 < elapsed < 3.5


def test_auth_code_extraction():
    from nebula.integrations.gog.auth import GogAuth

    url = "https://embed.gog.com/on_login_success?origin=client&code=AbC-123_x"
    assert GogAuth.extract_code(url) == "AbC-123_x"
    assert GogAuth.extract_code("  rawcode ") == "rawcode"


def test_launcher_tracks_session(qapp, services, tmp_path):
    from PySide6.QtCore import QCoreApplication

    script = tmp_path / "game.sh"
    script.write_text("#!/bin/sh\nsleep 1\n")
    script.chmod(0o755)
    gid = services.integrations["local"].save_entry({"title": "Sleepy", "exe": str(script), "platform": "linux"})
    services.library._fetched(services.integrations["local"],  # noqa: SLF001
                              services.integrations["local"].fetch_library(), False, None)
    uid = f"local:{gid}"
    import nebula.core.launcher as launcher_mod

    launcher_mod.MIN_SESSION = 0
    stopped = []
    services.bus.game_stopped.connect(lambda u, secs: stopped.append((u, secs)))
    services.launcher.launch(uid)
    deadline = time.time() + 6
    while uid not in services.launcher.running and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    st = services.launcher.running[uid]
    st.grace_until = 0
    st.seen = time.time() - 10  # pretend the process has gone quiet
    st.proc.wait(5)
    services.launcher._scanning = False  # noqa: SLF001
    services.launcher.poll()
    deadline = time.time() + 6
    while not stopped and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    assert stopped and stopped[0][0] == uid
    assert services.db.sessions_for(services.library.entry_for_uid(uid).key)
    assert services.launcher.log_file(uid).exists()


@pytest.mark.skipif(sys.platform != "linux", reason="linux only")
def test_desktop_shortcut(services, tmp_path, monkeypatch):
    from nebula import paths
    from nebula.core import shortcuts

    monkeypatch.setattr(paths, "applications_dir", lambda: tmp_path / "apps")
    monkeypatch.setattr(paths, "desktop_dir", lambda: tmp_path / "Desktop")
    (tmp_path / "apps").mkdir()
    created = shortcuts.create("gog:1", "My Game", [], desktop=False)
    text = created[0].read_text()
    assert "Exec=" in text and "--launch gog:1" in text and "Name=My Game" in text
    assert os.access(created[0], os.X_OK)


def test_run_async_delivers_errors_to_callback(qapp):
    from PySide6.QtCore import QCoreApplication

    from nebula.core import tasks

    got = []

    def boom():
        raise ValueError("kaput")

    tasks.run_async(boom, on_error=lambda e: got.append(e))
    deadline = time.time() + 3
    while not got and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert got and isinstance(got[0], ValueError) and str(got[0]) == "kaput"
