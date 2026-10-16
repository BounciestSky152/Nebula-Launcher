"""Steam, Epic (Legendary), Lutris, custom games, Linux installers, folder import and GE-Proton install."""
import io
import json
import os
import sqlite3
import stat
import tarfile
import zipfile

import pytest

from conftest import make_ctx
from nebula.core.models import Game
from nebula.runners import compat


def test_steam_local_library(services, tmp_path, monkeypatch):
    root = tmp_path / ".steam" / "steam"
    apps = root / "steamapps"
    apps.mkdir(parents=True)
    (apps / "libraryfolders.vdf").write_text('"libraryfolders" { "0" { "path" "%s" } }' % root)
    (apps / "appmanifest_620.acf").write_text(
        '"AppState" { "appid" "620" "name" "Portal 2" "installdir" "Portal 2" "StateFlags" "4" "SizeOnDisk" "12345" '
        '"buildid" "99" }')
    (apps / "appmanifest_1493710.acf").write_text('"AppState" { "appid" "1493710" "name" "Proton Experimental" '
                                                  '"StateFlags" "4" }')
    (apps / "appmanifest_570.acf").write_text('"AppState" { "appid" "570" "name" "Dota 2" "StateFlags" "6" '
                                              '"installdir" "dota 2 beta" }')
    (root / "config").mkdir()
    (root / "config" / "loginusers.vdf").write_text(
        '"users" { "76561197960287930" { "AccountName" "gabe" "PersonaName" "Gabe" "MostRecent" "1" } }')
    cfg = root / "userdata" / "22202" / "config"
    cfg.mkdir(parents=True)
    (cfg / "localconfig.vdf").write_text('"UserLocalConfigStore" { "Software" { "Valve" { "Steam" { "apps" { '
                                         '"620" { "LastPlayed" "1700000000" "Playtime" "754" } } } } } }')
    (root / "appcache" / "librarycache" / "620").mkdir(parents=True)
    cover = root / "appcache" / "librarycache" / "620" / "library_600x900.jpg"
    cover.write_bytes(b"jpg")
    monkeypatch.setattr(compat.Path, "home", staticmethod(lambda: tmp_path))
    steam = services.integrations["steam"]
    assert steam.available() and steam.account_name() == "Gabe" and steam.steamid() == "76561197960287930"
    games = {g.source_id: g for g in steam.fetch_library()}
    assert set(games) == {"620", "570"}, "Proton/runtime tools must be filtered out"
    portal = games["620"]
    assert portal.installed and portal.platform_playtime == 754 and portal.platform_last_played == 1700000000
    assert portal.cover == str(cover) and portal.install_size == 12345
    assert games["570"].update_available
    spec = steam.prepare_launch(portal)
    assert (spec.argv and spec.argv[-1] == "steam://rungameid/620") or spec.uri == "steam://rungameid/620"
    assert steam.process_hints(portal)["steam_appid"] == "620"


def test_epic_via_legendary_launches_with_proton(services, tmp_path, monkeypatch):
    from nebula import paths
    from nebula.integrations import epic

    cfgdir = tmp_path / "legendary"
    (cfgdir / "metadata").mkdir(parents=True)
    (cfgdir / "metadata" / "Sugar.json").write_text(json.dumps({
        "app_name": "Sugar", "app_title": "Celeste",
        "metadata": {"keyImages": [{"type": "DieselGameBoxTall", "url": "https://x/tall.jpg"}],
                     "categories": [{"path": "games"}], "developer": "EXOK"},
        "asset_infos": {"Windows": {"build_version": "1.4.1"}}}))
    (cfgdir / "metadata" / "SugarDLC.json").write_text(json.dumps({
        "app_name": "SugarDLC", "app_title": "Farewell", "metadata": {"mainGameItem": {"id": "x"}}}))
    (cfgdir / "installed.json").write_text(json.dumps({"Sugar": {
        "app_name": "Sugar", "title": "Celeste", "install_path": str(tmp_path / "Celeste"), "version": "1.4.0",
        "platform": "Windows", "executable": "Celeste.exe", "install_size": 1000}}))
    (cfgdir / "user.json").write_text(json.dumps({"displayName": "maddy"}))
    monkeypatch.setattr(epic, "CONFIG_DIRS", [(cfgdir, "legendary")])
    leg = tmp_path / "bin" / "legendary"
    leg.parent.mkdir()
    leg.write_text("#!/bin/sh\n")
    leg.chmod(0o755)
    monkeypatch.setattr(epic, "which", lambda *n: str(leg) if "legendary" in n else None)
    monkeypatch.setattr(compat, "which", lambda *n: None)
    monkeypatch.setattr(compat, "find_umu", lambda: None)
    monkeypatch.setattr("nebula.runners.manager.umu_game_id", lambda db, store, codename, session=None: "umu-123")
    d = paths.proton_dir() / "GE-Proton10-2"
    d.mkdir(parents=True)
    (d / "proton").write_text("")
    services.compat.refresh()
    integ = services.integrations["epic"]
    assert integ.connected() and integ.account_name() == "maddy"
    games = integ.fetch_library()
    assert [g.title for g in games] == ["Celeste"]
    g = games[0]
    assert g.installed and g.update_available and g.cover == "https://x/tall.jpg"
    spec = integ.prepare_launch(g)
    assert spec.argv[:5] == [str(leg), "launch", "Sugar", "--no-wine", "--wrapper"]
    assert "GE-Proton10-2/proton" in spec.argv[5] and spec.argv[5].endswith("waitforexitandrun")
    assert spec.env["LEGENDARY_CONFIG_PATH"] == str(cfgdir) and spec.env["STORE"] == "egs"
    assert spec.env["GAMEID"] == "umu-123"


def test_legendary_progress_parsing(services, tmp_path, monkeypatch):
    script = tmp_path / "legendary"
    script.write_text("#!/bin/sh\n"
                      "echo '[cli] INFO: Download size: 100.00 MiB (Compression savings: 10%)'\n"
                      "echo '[DLManager] INFO: = Progress: 50.00% (5/10), Running for 00:00:01, ETA: 00:00:01'\n"
                      "echo '[DLManager] INFO:  - Downloaded: 50.00 MiB, Written: 55.00 MiB'\n"
                      "exit 0\n")
    script.chmod(0o755)
    integ = services.integrations["epic"]
    monkeypatch.setattr(integ, "legendary", lambda: str(script))
    ctx = make_ctx()
    integ._run_legendary(Game("epic", "Sugar", "Celeste"), ["install", "Sugar"], ctx, str(tmp_path))  # noqa: SLF001
    assert ctx.job.total == 100 * 2**20 and ctx.job.done == 50 * 2**20
    script.write_text("#!/bin/sh\necho 'ERROR: nope'\nexit 3\n")
    with pytest.raises(RuntimeError, match="nope"):
        integ._run_legendary(Game("epic", "Sugar", "Celeste"), ["install", "Sugar"], make_ctx(), str(tmp_path))  # noqa: SLF001


def test_lutris_library(services, tmp_path, monkeypatch):
    from nebula.integrations import lutris

    data = tmp_path / "lutris"
    data.mkdir()
    conn = sqlite3.connect(data / "pga.db")
    conn.execute("CREATE TABLE games (id INTEGER, name TEXT, slug TEXT, runner TEXT, platform TEXT, directory TEXT, "
                 "installed INTEGER, playtime REAL, lastplayed INTEGER, installed_at INTEGER, service TEXT)")
    conn.execute("INSERT INTO games VALUES (7, 'Overwatch 2', 'overwatch-2', 'wine', 'Windows', '/g/ow', 1, 2.5, "
                 "1700000000, 1600000000, 'battlenet')")
    conn.execute("INSERT INTO games VALUES (8, 'Gone', 'gone', 'linux', 'Linux', '/g/gone', 0, 0, 0, 0, '')")
    conn.commit()
    conn.close()
    (data / "coverart").mkdir()
    (data / "coverart" / "overwatch-2.jpg").write_bytes(b"x")
    monkeypatch.setattr(lutris, "DATA_DIRS", [data])
    monkeypatch.setattr(lutris, "CACHE_DIRS", [])
    integ = services.integrations["lutris"]
    games = integ.fetch_library()
    assert [g.title for g in games] == ["Overwatch 2"]
    assert games[0].platform_playtime == 150 and games[0].cover.endswith("overwatch-2.jpg")
    spec = integ.prepare_launch(games[0])
    assert (spec.argv and spec.argv[-1] == "lutris:rungameid/7") or spec.uri == "lutris:rungameid/7"


def test_linux_installer_extraction(tmp_path):
    from nebula.integrations.gog.offline import extract_linux_installer

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("data/noarch/start.sh")
        info.external_attr = (stat.S_IFREG | 0o755) << 16
        z.writestr(info, "#!/bin/bash\necho hi\n")
        z.writestr("data/noarch/game/data.pak", b"x" * 5000)
        link = zipfile.ZipInfo("data/noarch/game/link")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        z.writestr(link, "data.pak")
        z.writestr("scripts/config.lua", "ignored")
    sh = tmp_path / "game_1.0.sh"
    sh.write_bytes(b"#!/bin/sh\n# makeself header\nexit 0\n" + b"\0" * 1000 + buf.getvalue())
    dest = tmp_path / "Game"
    ctx = make_ctx()
    extract_linux_installer(ctx, sh, dest)
    assert os.access(dest / "start.sh", os.X_OK)
    assert (dest / "game" / "data.pak").stat().st_size == 5000
    assert os.readlink(dest / "game" / "link") == "data.pak"
    assert not (dest / "scripts").exists()
    assert ctx.job.done == ctx.job.total


def test_scan_and_import_gog_folder(services, tmp_path):
    lib = tmp_path / "Games"
    win = lib / "Witcher"
    win.mkdir(parents=True)
    (win / "goggame-1207658924.info").write_text(json.dumps({"gameId": "1207658924", "name": "The Witcher",
                                                             "buildId": "555", "playTasks": []}))
    lin = lib / "Stardew"
    (lin / "game").mkdir(parents=True)
    (lin / "start.sh").write_text("")
    (lin / "game" / "goggame-1453375253.info").write_text(json.dumps({"gameId": "1453375253", "name": "Stardew"}))
    games = {g.source_id: g for g in services.integrations["gog"].import_path(str(lib))}
    assert games["1207658924"].installed and games["1207658924"].platform == "windows"
    assert games["1453375253"].platform == "linux"
    rec = services.integrations["gog"].install_record("1207658924")
    assert rec["method"] == "imported-gen2" and rec["build_id"] == "555"


def test_ge_proton_download_and_install(services, server, monkeypatch):
    from nebula import paths
    from nebula.runners import manager

    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as tar:
        for name, data in (("GE-Proton10-9/proton", b"#!/usr/bin/env python3\n"),
                           ("GE-Proton10-9/version", b"1 GE-Proton10-9\n")):
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o755
            tar.addfile(ti, io.BytesIO(data))
    blob = payload.getvalue()
    import hashlib

    server.files["/GE-Proton10-9.tar.gz"] = blob
    server.files["/GE-Proton10-9.sha512sum"] = (hashlib.sha512(blob).hexdigest() + "  GE-Proton10-9.tar.gz").encode()
    server.files["/releases"] = json.dumps([{"tag_name": "GE-Proton10-9", "published_at": "2026-09-01T00:00:00Z",
                                             "assets": [{"name": "GE-Proton10-9.tar.gz", "size": len(blob),
                                                         "browser_download_url": server.base + "/GE-Proton10-9.tar.gz"},
                                                        {"name": "GE-Proton10-9.sha512sum", "size": 10,
                                                         "browser_download_url": server.base + "/GE-Proton10-9.sha512sum"}]}]).encode()
    monkeypatch.setattr(manager, "GE_PROTON_RELEASES", server.base + "/releases")
    releases = manager.list_ge_proton()
    assert releases[0]["tag"] == "GE-Proton10-9" and releases[0]["sha512_url"]
    ctx = make_ctx()
    assert manager.install_ge_proton(ctx, releases[0]) == "GE-Proton10-9"
    assert (paths.proton_dir() / "GE-Proton10-9" / "proton").exists()
    services.compat.refresh()
    assert services.compat.default_proton().name == "GE-Proton10-9"
    # corrupted download is rejected
    server.files["/GE-Proton10-9.sha512sum"] = b"deadbeef  x"
    (paths.cache_dir() / "runner-downloads" / "GE-Proton10-9.tar.gz").unlink(missing_ok=True)
    with pytest.raises(IOError):
        manager.install_ge_proton(make_ctx(), releases[0])


def test_custom_game_and_plugin_loader(services, tmp_path):
    from nebula import paths
    from nebula.integrations import plugin_classes

    gid = services.integrations["local"].save_entry({"title": "Tool", "exe": "retroarch -L core.so rom.sfc"})
    g = next(x for x in services.integrations["local"].fetch_library() if x.source_id == gid)
    spec = services.integrations["local"].prepare_launch(g)
    assert spec.argv == ["retroarch", "-L", "core.so", "rom.sfc"]
    src = os.path.join(os.path.dirname(__file__), "..", "docs", "examples", "example_plugin.py")
    (paths.plugins_dir() / "roms.py").write_text(open(src).read())
    assert [c.id for c in plugin_classes()] == ["roms"]
