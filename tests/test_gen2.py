"""End-to-end tests of the GOG Galaxy generation-2 installer against a fake CDN."""
import hashlib
import json
import os
import zlib

import pytest

from conftest import make_ctx
from nebula.integrations.gog import api as gog_api
from nebula.integrations.gog import content

CHUNK = 1024 * 1024


def md5(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


class FakeGog:
    """Builds generation-2 builds and serves them like GOG's content system."""

    def __init__(self, server, product_id="123"):
        self.server = server
        self.pid = product_id
        self.builds_by_os: dict[str, list] = {"windows": []}
        server.handlers[("GET", f"/store/{product_id}/token=abc/")] = self._chunk

    def _chunk(self, req):
        path = req.path.replace("/token=abc", "")
        if path in self.server.files:
            return 200, {}, self.server.files[path]
        return 404, {}, b""

    def add_build(self, build_id: str, version: str, files: dict[str, bytes], dirs=("saves",), date="2024-01-01",
                  dlc_files: dict | None = None):
        items = [self._item(p, data) for p, data in files.items()] + [{"type": "DepotDirectory", "path": d} for d in dirs]
        depot = zlib.compress(json.dumps({"depot": {"items": items}}).encode())
        depot_hash = md5(depot)
        self.server.files[f"/content-system/v2/meta/{gog_api.galaxy_path(depot_hash)}"] = depot
        depots = [{"productId": self.pid, "languages": ["*"], "manifest": depot_hash,
                   "size": sum(len(d) for d in files.values()), "compressedSize": 1}]
        products = [{"productId": self.pid, "name": "Test Game"}]
        if dlc_files:
            ditems = [self._item(p, d) for p, d in dlc_files.items()]
            ddepot = zlib.compress(json.dumps({"depot": {"items": ditems}}).encode())
            dh = md5(ddepot)
            self.server.files[f"/content-system/v2/meta/{gog_api.galaxy_path(dh)}"] = ddepot
            depots.append({"productId": "999", "languages": ["en-US"], "manifest": dh, "size": 10, "compressedSize": 1})
            products.append({"productId": "999", "name": "Test DLC"})
        meta = {"baseProductId": self.pid, "buildId": build_id, "clientId": "cid", "clientSecret": "secret",
                "dependencies": [], "depots": depots, "installDirectory": "Test Game", "products": products,
                "version": 2}
        raw = zlib.compress(json.dumps(meta).encode())
        link = f"/meta/{build_id}"
        self.server.files[link] = raw
        self.builds_by_os["windows"].insert(0, {"build_id": build_id, "product_id": self.pid, "os": "windows",
                                                "branch": None, "version_name": version, "public": True,
                                                "date_published": date, "generation": 2,
                                                "link": self.server.base + link})

    def _item(self, path: str, data: bytes) -> dict:
        chunks = []
        for i in range(0, max(1, len(data)), CHUNK):
            piece = data[i:i + CHUNK]
            if not piece and data:
                break
            comp = zlib.compress(piece)
            self.server.files[f"/store/{self.pid}/{gog_api.galaxy_path(md5(comp))}"] = comp
            chunks.append({"md5": md5(piece), "size": len(piece), "compressedMd5": md5(comp),
                           "compressedSize": len(comp)})
        item = {"type": "DepotFile", "path": path.replace("/", "\\"), "chunks": chunks if data else []}
        if len(chunks) > 1:
            item["md5"] = md5(data)
        if path.endswith(".exe"):
            item["flags"] = ["executable"]
        return item

    # --- API surface used by content.Gen2 / integration
    def builds(self, product_id, os_name, generation=2):
        return list(self.builds_by_os.get(os_name, []))

    def secure_link(self, product_id, path="/", root=None):
        return [{"url_format": "{base_url}/token=abc{path}",
                 "parameters": {"base_url": f"{self.server.base}/store/{product_id}", "path": "/"}}]


@pytest.fixture()
def fake(server, monkeypatch):
    monkeypatch.setattr(content, "CDN", server.base)
    return FakeGog(server)


def make_api(fake):
    from nebula.core.http import new_session

    class Auth:
        logged_in = False

    api = gog_api.GogApi(Auth(), new_session())
    api.builds = fake.builds
    api.secure_link = fake.secure_link
    return api


def read_tree(root):
    out = {}
    for dirpath, _d, files in os.walk(root):
        for f in files:
            p = os.path.join(dirpath, f)
            rel = os.path.relpath(p, root)
            if rel.startswith(".") or "/." in rel:
                continue
            out[rel] = open(p, "rb").read()
    return out


def test_install_update_repair(fake, server, tmp_path):
    exe = os.urandom(CHUNK * 2 + 12345)
    files = {"bin/game.exe": exe, "data/a.txt": b"alpha", "readme.txt": b"hello", "empty.dat": b"",
             "goggame-123.info": json.dumps({"playTasks": [{"isPrimary": True, "path": "bin\\game.exe"}]}).encode()}
    fake.add_build("b1", "1.0", files)
    api = make_api(fake)
    gen2 = content.Gen2(api, "123")
    builds = gen2.builds("windows")
    info = gen2.build_info(gen2.pick(builds))
    assert info.install_directory == "Test Game" and info.client_id == "cid"
    ctx = make_ctx()
    fs = gen2.file_set(info, "en-US", [], ctx=ctx)
    dest = tmp_path / "Test Game"
    fetcher = content.ChunkFetcher(ctx, api.session, lambda pid: api.secure_link(pid))
    ex = content.Executor(ctx, dest, dest / ".support", fetcher, dest / content.RESUME_FILE)
    dest.mkdir()
    ex.apply(fs.files, dirs=fs.dirs, links=fs.links)
    tree = read_tree(dest)
    assert tree == files
    assert os.access(dest / "bin" / "game.exe", os.X_OK)
    assert (dest / "saves").is_dir(), "empty manifest directories must be created"
    assert content.read_resume(dest / content.RESUME_FILE).keys() >= {"bin/game.exe", "readme.txt"}

    # --- update: change the middle chunk of game.exe, drop readme, add a file
    exe2 = exe[:CHUNK] + os.urandom(CHUNK) + exe[2 * CHUNK:]
    files2 = dict(files)
    files2["bin/game.exe"] = exe2
    del files2["readme.txt"]
    files2["data/new.txt"] = b"new file"
    fake.add_build("b2", "2.0", files2, date="2024-02-01")
    info2 = gen2.build_info(gen2.pick(gen2.builds("windows")))
    assert info2.build_id == "b2"
    fs2 = gen2.file_set(info2, "en-US", [])
    old = {f.path.lower(): f for f in fs.files}
    new_keys = {f.path.lower() for f in fs2.files}
    to_get = [f for f in fs2.files if f.path.lower() not in old or not f.same_content(old[f.path.lower()])]
    deletions = [f for k, f in old.items() if k not in new_keys]
    assert {f.path for f in to_get} == {"bin/game.exe", "data/new.txt"}
    server.hits.clear()
    ex.apply(to_get, old=old, dirs=fs2.dirs, deletions=deletions)
    assert read_tree(dest) == files2
    chunk_hits = [h for h in server.hits if "/store/" in h]
    assert len(chunk_hits) == 2, f"unchanged chunks must be reused from disk, got {chunk_hits}"

    # --- repair: corrupt a file and truncate another
    (dest / "data" / "a.txt").write_bytes(b"ALPHA")
    with open(dest / "bin" / "game.exe", "r+b") as fh:
        fh.truncate(100)
    broken = content.verify_files(make_ctx(), fs2.files, ex)
    assert {f.path for f in broken} == {"data/a.txt", "bin/game.exe"}
    ex.apply(broken)
    assert read_tree(dest) == files2
    assert content.verify_files(make_ctx(), fs2.files, ex) == []


def test_dlc_and_language_selection(fake, tmp_path):
    fake.add_build("b1", "1.0", {"game.bin": b"base"}, dlc_files={"dlc/extra.pak": b"dlc data"})
    api = make_api(fake)
    gen2 = content.Gen2(api, "123")
    info = gen2.build_info(gen2.pick(gen2.builds("windows")))
    assert [p["id"] for p in info.dlc_products()] == ["999"]
    assert {f.path for f in gen2.file_set(info, "en-US", []).files} == {"game.bin"}
    assert {f.path for f in gen2.file_set(info, "en-US", ["999"]).files} == {"game.bin", "dlc/extra.pak"}
    # a German install still gets the DLC because "en-US" depots fall back only when no language matches
    assert content.lang_matches(["en-US"], "en") and not content.lang_matches(["de-DE"], "en-US")
    assert content.normalize_lang("German") == "de-DE"


def test_chunk_checksum_mismatch_is_retried_then_fails(fake, server, monkeypatch):
    fake.add_build("b1", "1.0", {"x.bin": b"payload"})
    api = make_api(fake)
    gen2 = content.Gen2(api, "123")
    info = gen2.build_info(gen2.pick(gen2.builds("windows")))
    f = gen2.file_set(info, "en-US", []).files[0]
    path = f"/store/123/{gog_api.galaxy_path(f.chunks[0]['compressedMd5'])}"
    server.files[path] = zlib.compress(b"tampered")
    monkeypatch.setattr(content.time, "sleep", lambda s: None)
    ctx = make_ctx()
    fetcher = content.ChunkFetcher(ctx, api.session, lambda pid: api.secure_link(pid))
    with pytest.raises(IOError):
        fetcher.fetch("123", f.chunks[0])


def test_full_gog_integration_install_and_proton_launch(services, fake, server, monkeypatch, tmp_path):
    """Install through GogIntegration and check the game launches through Proton."""
    from nebula import paths
    from nebula.core.models import Game
    from nebula.integrations.base import InstallRequest

    info = json.dumps({"gameId": "123", "playTasks": [
        {"isPrimary": True, "type": "FileTask", "path": "bin\\game.exe", "workingDir": "bin", "arguments": "-skipintro",
         "name": "Test Game"},
        {"type": "FileTask", "path": "bin\\editor.exe", "name": "Editor", "category": "tool"}]}).encode()
    fake.add_build("b1", "1.0", {"bin/game.exe": b"MZ" + os.urandom(5000), "bin/editor.exe": b"MZ",
                                 "goggame-123.info": info})
    gog = services.integrations["gog"]
    api = gog.api
    api.builds = fake.builds
    api.secure_link = fake.secure_link
    monkeypatch.setattr(gog, "owned_ids", lambda refresh=False: {"123"})
    services.settings.set("downloads.install_dependencies", False)
    # a fake GE-Proton build
    proton = paths.proton_dir() / "GE-Proton10-1"
    (proton / "files" / "bin").mkdir(parents=True)
    (proton / "proton").write_text("#!/bin/sh\n")
    (proton / "version").write_text("1 GE-Proton10-1\n")
    services.compat.refresh()
    monkeypatch.setattr("nebula.runners.manager.umu_game_id", lambda db, store, codename, session=None: "umu-777")
    services.settings.set("compat.use_umu", False)
    game = Game("gog", "123", "Test Game", os_support=["windows"])
    ctx = make_ctx()
    result = gog.install(game, InstallRequest(platform="windows", base_dir=str(tmp_path / "lib")), ctx)
    assert result.installed and result.platform == "windows" and result.version == "1.0"
    assert (tmp_path / "lib" / "Test Game" / "bin" / "game.exe").exists()
    assert content.load_manifest("123")["build_id"] == "b1"
    tasks_ = gog.launch_tasks(result)
    assert [t.name for t in tasks_] == ["Test Game", "Editor"] and tasks_[0].primary
    spec = gog.prepare_launch(result)
    assert spec.argv[0].endswith("GE-Proton10-1/proton") and spec.argv[1] == "waitforexitandrun"
    assert spec.argv[2].endswith("bin/game.exe") and spec.argv[3] == "-skipintro"
    assert spec.env["STEAM_COMPAT_DATA_PATH"] == spec.env["WINEPREFIX"]
    assert spec.env["GAMEID"] == "umu-777" and spec.env["STORE"] == "gog"
    assert spec.cwd.endswith("bin")
    assert (paths.prefixes_dir() / "gog_123" / "pfx").is_symlink()

    # update to a new build, then roll back to the old one (pins the version)
    fake.add_build("b2", "2.0", {"bin/game.exe": b"MZ2", "bin/editor.exe": b"MZ", "goggame-123.info": info},
                   date="2024-03-01")
    monkeypatch.setattr(gog, "connected", lambda: True)  # version checks need a signed-in account
    assert gog.check_updates([result]) == ["gog:123"]
    updated = gog.update(result, make_ctx())
    assert updated.version == "2.0" and (tmp_path / "lib/Test Game/bin/game.exe").read_bytes() == b"MZ2"
    versions = gog.versions(updated)
    assert [v["id"] for v in versions] == ["b2", "b1"] and versions[0]["current"]
    rolled = gog.update(updated, make_ctx(), version="b1")
    assert rolled.version == "1.0"
    assert services.db.get_config("gog:123").get("pin_version") is True
    assert gog.check_updates([rolled]) == []
    # uninstall removes files, manifest and record
    gog.uninstall(rolled, make_ctx())
    assert not (tmp_path / "lib" / "Test Game").exists()
    assert content.load_manifest("123") is None and gog.install_record("123") is None


# ------------------------------------------------------------------ versions through GogIntegration
def add_branch_build(fake, bid, ver, files, branch, date, public=True, **kw):
    fake.add_build(bid, ver, files, date=date, **kw)
    fake.builds_by_os["windows"][0]["branch"] = branch
    fake.builds_by_os["windows"][0]["public"] = public


def gog_for(services, fake, monkeypatch, owned=("123",)):
    gog = services.integrations["gog"]
    monkeypatch.setattr(gog.api, "builds", fake.builds)
    monkeypatch.setattr(gog.api, "secure_link", fake.secure_link)
    monkeypatch.setattr(gog, "owned_ids", lambda refresh=False: set(owned))
    monkeypatch.setattr(gog, "connected", lambda: True)
    services.settings.set("downloads.install_dependencies", False)
    return gog


def pinned(services) -> bool:
    return bool(services.db.get_config("gog:123").get("pin_version"))


def test_version_switch_flow(services, fake, monkeypatch, tmp_path):
    from nebula.core.models import Game
    from nebula.integrations import base
    from nebula.integrations.base import InstallRequest

    fake.add_build("b1", "1.0", {"game.bin": b"one"}, date="2024-01-01T10:00:00+0000")
    fake.add_build("b2", "2.0", {"game.bin": b"two"}, date="2024-02-01T10:00:00+0000")
    add_branch_build(fake, "b3", "2.1", {"game.bin": b"three"}, "public_beta", "2024-03-01T10:00:00+0000")
    gog = gog_for(services, fake, monkeypatch)
    game = gog.install(Game("gog", "123", "Test Game", os_support=["windows"]),
                       InstallRequest(platform="windows", base_dir=str(tmp_path / "lib")), make_ctx())
    exe = tmp_path / "lib" / "Test Game" / "game.bin"
    assert game.build_id == "b2" and exe.read_bytes() == b"two", "a newer beta is never installed by default"
    rec = gog.install_record("123")
    installed_at = rec["installed_at"]
    assert rec["branch"] is None and rec["latest"]["status"] == "up_to_date" and not pinned(services)

    game = gog.update(game, make_ctx(), version="b1")  # roll back
    rec = gog.install_record("123")
    assert game.build_id == "b1" and exe.read_bytes() == b"one" and pinned(services)
    assert rec["date"] == "2024-01-01" and rec["updated_at"] >= installed_at and rec["installed_at"] == installed_at
    assert rec["latest"]["id"] == "b2" and rec["latest"]["status"] == "behind" and rec["latest"]["for"] == "b1"

    game = gog.update(game, make_ctx())  # a plain update returns to the newest build and lifts the pin
    assert game.build_id == "b2" and exe.read_bytes() == b"two" and not pinned(services)
    game = gog.update(game, make_ctx(), version="b1")
    game = gog.update(game, make_ctx(), version="b2")  # explicitly choosing the newest build lifts it too
    assert game.build_id == "b2" and not pinned(services)

    game = gog.update(game, make_ctx(), version=base.branch_target("public_beta"))
    assert game.build_id == "b3" and exe.read_bytes() == b"three" and not pinned(services)
    assert gog.install_record("123")["branch"] == "public_beta"
    add_branch_build(fake, "b5", "2.2", {"game.bin": b"five"}, "public_beta", "2024-04-01T10:00:00+0000")
    assert gog.check_updates([game]) == ["gog:123"]
    game = gog.update(game, make_ctx())  # stays on the beta channel
    assert game.build_id == "b5" and exe.read_bytes() == b"five"

    game = gog.update(game, make_ctx(), version=base.branch_target(None))
    assert game.build_id == "b2" and exe.read_bytes() == b"two" and gog.install_record("123")["branch"] is None
    assert gog.check_updates([game]) == [] and not pinned(services)
    with pytest.raises(RuntimeError, match="no longer available"):
        gog.update(game, make_ctx(), version="b404")
    with pytest.raises(RuntimeError, match="Next channel"):
        gog.update(game, make_ctx(), version=base.branch_target("next"))


def test_install_options_order_and_install_pin(services, fake, monkeypatch, tmp_path):
    from nebula.core.models import Game
    from nebula.integrations.base import InstallRequest

    fake.add_build("b1", "1.0", {"game.bin": b"one"}, date="2024-01-01T10:00:00+0000")
    fake.add_build("b2", "2.0", {"game.bin": b"two"}, date="2024-02-01T10:00:00+0000")
    add_branch_build(fake, "b3", "2.1", {"game.bin": b"three"}, "public_beta", "2024-03-01T10:00:00+0000")
    gog = gog_for(services, fake, monkeypatch)
    monkeypatch.setattr(gog.api, "product", lambda *a, **k: {"downloads": {"installers": []}, "expanded_dlcs": []})
    game = Game("gog", "123", "Test Game", os_support=["windows"])
    opts = gog.install_options(game, "windows")
    assert [v["id"] for v in opts.versions] == ["b2", "b1", "b3"]
    assert opts.latest == opts.version == "b2" and opts.versions[0]["label"] == "2.0 (newest)"
    assert opts.versions[1]["branch"] == "" and opts.versions[2]["branch"] == "Public beta"
    game = gog.install(game, InstallRequest(platform="windows", base_dir=str(tmp_path / "lib"), version="b1"),
                       make_ctx())
    assert game.build_id == "b1" and pinned(services), "installing an older build pins it"
    gog.uninstall(game, make_ctx())
    assert not pinned(services) and gog.install_record("123") is None


def test_rollback_keeps_wanted_dlcs(services, fake, server, monkeypatch, tmp_path):
    from nebula.core.models import Game
    from nebula.integrations.base import InstallRequest

    def dlc_chunk(req):  # DLC chunks are fetched through the DLC's own secure link
        path = req.path.replace("/store/999/token=abc", "/store/123")
        return (200, {}, server.files[path]) if path in server.files else (404, {}, b"")

    server.handlers[("GET", "/store/999/token=abc/")] = dlc_chunk
    monkeypatch.setattr(content.time, "sleep", lambda s: None)
    fake.add_build("b1", "1.0", {"game.bin": b"one"}, date="2024-01-01")
    fake.add_build("b2", "2.0", {"game.bin": b"two"}, date="2024-02-01", dlc_files={"dlc/extra.pak": b"dlc data"})
    gog = gog_for(services, fake, monkeypatch, owned=("123", "999"))
    game = gog.install(Game("gog", "123", "Test Game", os_support=["windows"]),
                       InstallRequest(platform="windows", base_dir=str(tmp_path / "lib"), dlcs=["999"]), make_ctx())
    pak = tmp_path / "lib" / "Test Game" / "dlc" / "extra.pak"
    assert pak.read_bytes() == b"dlc data" and gog.install_record("123")["dlcs"] == ["999"]
    game = gog.update(game, make_ctx(), version="b1")  # b1 has no DLC
    rec = gog.install_record("123")
    assert rec["dlcs"] == [] and rec["dlcs_wanted"] == ["999"] and not pak.exists()
    game = gog.update(game, make_ctx())
    rec = gog.install_record("123")
    assert pak.read_bytes() == b"dlc data" and rec["dlcs"] == ["999"] and game.extra["installed_dlcs"] == ["999"]


def test_install_no_offline_fallback_on_network_error(services, fake, monkeypatch, tmp_path):
    import requests

    from nebula.core.models import Game
    from nebula.integrations.base import InstallRequest
    from nebula.integrations.gog import offline

    gog = gog_for(services, fake, monkeypatch)

    def down(*_a, **_k):
        raise requests.ConnectionError("network is unreachable")

    monkeypatch.setattr(gog.api, "builds", down)
    monkeypatch.setattr(offline, "download_installer",
                        lambda *a, **k: pytest.fail("must not fall back to the offline installer"))
    game = Game("gog", "123", "Test Game", os_support=["windows"])
    request = InstallRequest(platform="windows", base_dir=str(tmp_path / "lib"))
    with pytest.raises(RuntimeError, match="Couldn't get the build list"):
        gog.install(game, request, make_ctx())
    # "no builds" (an HTTP 4xx, or an empty list) still uses the offline installer
    monkeypatch.setattr(gog.api, "builds", lambda *a, **k: [])
    monkeypatch.setattr(gog, "_offline", lambda *a, **k: "offline")
    assert gog.install(game, request, make_ctx()) == "offline"
