"""GOG cloud saves against a fake cloudstorage server."""
import gzip
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import pytest

from nebula.integrations.gog import cloud as cloud_mod


class FakeCloud:
    def __init__(self, server):
        self.server = server
        self.store: dict[str, dict] = {}  # name -> {"data": gz bytes, "hash", "mtime"}
        server.handlers[("GET", "/v1/")] = self.get
        server.handlers[("PUT", "/v1/")] = self.put

    def _name(self, path):
        parts = path.split("/", 4)  # '', 'v1', user, client, name
        return parts[4] if len(parts) > 4 else ""

    def get(self, req):
        name = self._name(req.path)
        if not name:
            items = [{"name": n, "hash": v["hash"], "last_modified": v["iso"]} for n, v in self.store.items()]
            return (200, {"Content-Type": "application/json"}, json.dumps(items).encode()) if items else (404, {}, b"")
        from urllib.parse import unquote

        v = self.store.get(unquote(name))
        if not v:
            return 404, {}, b""
        return 200, {"Content-Encoding": "gzip", "X-Object-Meta-LocalLastModified": v["iso"]}, v["data"]

    def put(self, req):
        from urllib.parse import unquote

        data = req.rfile.read(int(req.headers["Content-Length"]))
        assert req.headers["Content-Encoding"] == "gzip"
        assert req.headers["Etag"] == hashlib.md5(data).hexdigest()
        self.store[unquote(self._name(req.path))] = {"data": data, "hash": hashlib.md5(data).hexdigest(),
                                                     "iso": req.headers["X-Object-Meta-LocalLastModified"]}
        return 201, {}, b""

    def put_direct(self, name, raw: bytes, ts: float):
        gz = gzip.compress(raw, 6, mtime=0)
        iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
        self.store[name] = {"data": gz, "hash": hashlib.md5(gz).hexdigest(), "iso": iso}


@pytest.fixture()
def cloud(services, server, monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_mod, "CLOUD", server.base)
    fake = FakeCloud(server)
    api = services.integrations["gog"].api
    api.remote_config = lambda cid: {"content": {"Windows": {"cloudStorage": {
        "enabled": True, "locations": [{"name": "saves", "location": "<?INSTALL?>/saves"},
                                        {"name": "settings", "location": "<?DOCUMENTS?>/My Game"}]}}}}
    monkeypatch.setattr(api.auth, "game_credentials", lambda cid, sec: {"access_token": "t", "user_id": "u1"})
    cs = cloud_mod.CloudSaves(api, services.compat, services.db)
    install = tmp_path / "game"
    (install / "saves").mkdir(parents=True)
    return cs, fake, install


def test_locations_resolve_inside_proton_prefix(cloud, services):
    cs, _fake, install = cloud
    locs = dict(cs.locations("cid", "gog:1", str(install)))
    assert locs["saves"] == install / "saves"
    assert str(locs["settings"]).endswith("drive_c/users/steamuser/Documents/My Game")
    default = cs.resolve("%LocalAppData%/GOG.com/Galaxy/Applications/cid/Storage/Shared/Files", "gog:1", str(install))
    assert "drive_c/users/steamuser/AppData/Local/GOG.com" in str(default)
    assert str(cs.resolve("C:\\ProgramData\\Game", "gog:1", str(install))).endswith("drive_c/ProgramData/Game")


def test_upload_download_and_conflict(cloud, services):
    cs, fake, install = cloud
    (install / "saves" / "slot1.sav").write_bytes(b"level 1")
    (install / "saves" / "sub").mkdir()
    (install / "saves" / "sub" / "slot2.sav").write_bytes(b"level 2")
    r = cs.sync("gog:1", str(install), "cid", "secret")
    assert r.action == "upload" and r.files == 2
    assert set(fake.store) == {"saves/slot1.sav", "saves/sub/slot2.sav"}
    # nothing changed -> no-op
    assert cs.sync("gog:1", str(install), "cid", "secret").action == "none"
    # newer save from another computer -> download
    time.sleep(1.1)
    fake.put_direct("saves/slot1.sav", b"level 9 (other pc)", time.time() + 5)
    r = cs.sync("gog:1", str(install), "cid", "secret")
    assert r.action == "download"
    assert (install / "saves" / "slot1.sav").read_bytes() == b"level 9 (other pc)"
    # both sides change -> conflict, then forced upload resolves it
    time.sleep(1.1)
    (install / "saves" / "slot1.sav").write_bytes(b"local progress")
    os.utime(install / "saves" / "slot1.sav", (time.time() + 10, time.time() + 10))
    fake.put_direct("saves/sub/slot2.sav", b"cloud progress", time.time() + 10)
    r = cs.sync("gog:1", str(install), "cid", "secret")
    assert r.action == "conflict"
    r = cs.sync("gog:1", str(install), "cid", "secret", mode="upload")
    assert r.action == "upload"
    assert gzip.decompress(fake.store["saves/slot1.sav"]["data"]) == b"local progress"
    # "newest" policy resolves automatically
    time.sleep(1.1)
    (install / "saves" / "slot1.sav").write_bytes(b"x")
    fake.put_direct("saves/sub/slot2.sav", b"newest cloud", time.time() + 100)
    r = cs.sync("gog:1", str(install), "cid", "secret", conflict_policy="newest")
    assert r.action == "download"
