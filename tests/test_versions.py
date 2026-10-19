"""GOG version info: installed vs newest version, channels, caching, errors and imports (no real network)."""
import json
import threading
import time

import pytest
import requests

from conftest import make_ctx
from nebula.core.models import Game
from nebula.integrations import base
from nebula.integrations.gog import api as gog_api
from nebula.integrations.gog import content, offline

DROP = object()  # leave a key out of the install record


def B(id, ver, date, branch=None, public=True, legacy=None):  # noqa: N802
    b = {"build_id": id, "version_name": ver, "date_published": f"{date}T10:00:00+0000", "branch": branch,
         "public": public, "generation": 2, "link": ""}
    if legacy:
        b["legacy_build_id"] = legacy
    return b


def builds():
    """b3 is a newer beta than the newest Stable build b2."""
    return [B("b3", "2.1", "2024-03-01", "public_beta"), B("b2", "2.0", "2024-02-01"), B("b1", "1.0", "2024-01-01")]


class FakeBuilds:
    """Stands in for GogApi.builds and counts requests."""

    def __init__(self, items, gate: threading.Event | None = None):
        self.items = items
        self.gate = gate
        self.calls = 0

    def __call__(self, product_id, os_name, generation=2):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if isinstance(self.items, Exception):
            raise self.items
        return [dict(b) for b in self.items]


def no_network(*_a, **_k):
    pytest.fail("no network access expected")


@pytest.fixture()
def gog(services, monkeypatch):
    g = services.integrations["gog"]
    monkeypatch.setattr(g, "connected", lambda: True)
    return g


def install(gog, tmp_path, **changes):
    """Write an install record for product 123 and return the matching installed Game."""
    path = tmp_path / "Game"
    path.mkdir(exist_ok=True)
    rec = {"install_path": str(path), "platform": "windows", "method": "gen2", "build_id": "b1", "version": "1.0",
           "branch": None, "language": "en-US"}
    rec.update(changes)
    rec = {k: v for k, v in rec.items() if v is not DROP}
    gog._set_record("123", rec)
    return Game("gog", "123", "Test Game", installed=True, install_path=str(path), platform=rec["platform"],
                build_id=str(rec.get("build_id", "")))


# ------------------------------------------------------------------ content helpers
def test_content_helpers():
    assert content.branch_label(None) == "Stable" and content.branch_label("") == "Stable"
    assert content.branch_label("public_beta") == "Public beta" and content.branch_label("GOG beta") == "GOG beta"
    assert content.nice_date("2023-06-03T09:19:10+0000") == "3 Jun 2023"
    assert content.nice_date("") == "" and content.nice_date("soon") == ""
    items = builds()
    assert content.Gen2.latest(items, None)["build_id"] == "b2"
    assert content.Gen2.latest(items, "public_beta")["build_id"] == "b3"
    assert content.Gen2.latest(items, "gone") is None, "latest() must not fall back to another channel"
    gen2 = content.Gen2(None, "123")
    assert gen2.pick(items, branch="gone")["build_id"] == "b2", "a closed channel falls back to Stable, not a beta"
    assert gen2.pick(items)["build_id"] == "b2"
    assert gen2.pick(items, branch="public_beta")["build_id"] == "b3"
    assert gen2.pick(items, build_id="b1")["build_id"] == "b1" and gen2.pick(items, build_id="nope") is None
    legacy = items + [B("b0", "0.9", "2023-12-01", legacy="L0")]
    assert content.Gen2.find(legacy, "L0")["build_id"] == "b0"
    assert content.Gen2.find(legacy, "") is None
    assert content.Gen2.branches(items) == [None, "public_beta"]
    assert content.Gen2.branches(items + [B("b9", "3.0", "2024-06-01", "next")]) == [None, "next", "public_beta"]
    assert content.Gen2.branches([B("x", "1", "2024-01-01", "beta")]) == ["beta"]

    class Api:
        def builds(self, product_id, os_name, generation=2):
            return [B("p", "9.9", "2024-09-01", "secret", public=False)] + builds()

    assert [b["build_id"] for b in content.Gen2(Api(), "123").builds()] == ["b3", "b2", "b1"]


# ------------------------------------------------------------------ builds kind
def test_version_info_stable_ignores_newer_beta(gog, monkeypatch, tmp_path):
    monkeypatch.setattr(gog.api, "builds", FakeBuilds(builds()))
    g = install(gog, tmp_path)
    info = gog.version_info(g)
    assert info.kind == "builds" and info.origin == "nebula" and not info.error and not info.stale
    assert info.status == "behind" and info.latest.id == "b2" and info.latest.version == "2.0"
    assert info.newer_count == 1 and info.update_available and info.can_choose and info.can_update
    assert info.branches == ["", "public_beta"] and info.branch == ""
    assert {b.id for b in info.builds if b.newest} == {"b2", "b3"}
    assert [b.id for b in info.builds if b.current] == ["b1"]
    assert info.installed.date == "2024-01-01" and info.installed_listed
    assert info.method == "GOG Galaxy builds · Windows version (runs with Proton)"
    saved = gog.install_record("123")["latest"]
    assert saved["id"] == "b2" and saved["for"] == "b1" and saved["status"] == "behind" and saved["update_available"]
    # not installed: the newest Stable build Nebula would install (never the newer beta), no installed version
    avail = gog.version_info(Game("gog", "123", "Not installed", os_support=["windows"]))
    assert avail.status == "not_installed" and avail.latest.id == "b2" and avail.installed.id == ""
    assert avail.branches == ["", "public_beta"] and not avail.update_available
    assert gog.version_info(Game("gog", "555", "No record", installed=True)) is None


def test_version_info_up_to_date_and_beta(gog, monkeypatch, tmp_path):
    fake = FakeBuilds(builds())
    monkeypatch.setattr(gog.api, "builds", fake)
    info = gog.version_info(install(gog, tmp_path, build_id="b2", version="2.0"))
    assert info.status == "up_to_date" and not info.update_available and info.newer_count == 0
    g = install(gog, tmp_path, build_id="b3", version="2.1", branch="public_beta")
    info = gog.version_info(g)
    assert info.status == "up_to_date" and info.branch == "public_beta" and info.latest.id == "b3"
    fake.items.insert(0, B("b5", "2.2", "2024-04-01", "public_beta"))
    info = gog.version_info(g, refresh=True)
    assert info.status == "behind" and info.latest.id == "b5" and info.update_available and info.newer_count == 1
    g = install(gog, tmp_path, build_id="b5", version="2.2", branch="public_beta")
    fake.items.insert(0, B("b6", "3.0", "2024-05-01"))
    info = gog.version_info(g, refresh=True)
    assert info.status == "up_to_date" and info.latest.id == "b5", "a new Stable build doesn't concern a beta copy"


def test_pinned_still_reports_newest(gog, services, monkeypatch, tmp_path):
    monkeypatch.setattr(gog.api, "builds", FakeBuilds(builds()))
    g = install(gog, tmp_path)
    services.db.update_config("gog:123", pin_version=True)
    info = gog.version_info(g)
    assert info.pinned and info.update_available, "VersionInfo.update_available ignores the pin"
    g.update_available = True
    assert gog.check_updates([g]) == [] and g.update_available is False
    assert gog.install_record("123")["latest"]["id"] == "b2"


def test_channel_ended(gog, monkeypatch, tmp_path):
    monkeypatch.setattr(gog.api, "builds", FakeBuilds([B("b2", "2.0", "2024-02-01"), B("b1", "1.0", "2024-01-01")]))
    g = install(gog, tmp_path, build_id="b3", version="2.1", branch="public_beta")
    info = gog.version_info(g)
    assert info.status == "channel_ended" and info.branch_gone and info.branch == ""
    assert info.latest.id == "b2" and not info.update_available
    g.update_available = False
    assert gog.check_updates([g]) == [] and not g.update_available


def test_unlisted_and_imports(gog, monkeypatch, tmp_path):
    items = builds()
    items[2]["legacy_build_id"] = "L1"
    monkeypatch.setattr(gog.api, "builds", FakeBuilds(items))
    info = gog.version_info(install(gog, tmp_path, build_id="b0", version="0.9"))
    assert info.status == "unlisted" and info.update_available and not info.installed_listed
    info = gog.version_info(install(gog, tmp_path, build_id="b0", version="", method="imported-gen2", branch=DROP))
    assert info.status == "unlisted" and not info.update_available and info.origin == "folder"
    info = gog.version_info(install(gog, tmp_path, build_id="L1", version="", method="imported-gen2", branch=DROP))
    assert info.installed_listed and info.installed.version == "1.0" and info.status == "behind"
    rec = gog.install_record("123")
    assert rec["version"] == "1.0" and rec["date"] == "2024-01-01" and rec["branch"] is None
    assert rec["build_id"] == "L1", "the recorded build id is never rewritten"
    g = install(gog, tmp_path, build_id="b3", version="", method="imported-gen2", branch=DROP, source="heroic")
    info = gog.version_info(g)
    assert info.installed.branch == "public_beta" and info.branch == "public_beta" and info.status == "up_to_date"
    assert info.method == "GOG Galaxy builds · imported from Heroic"
    rec = gog.install_record("123")
    assert rec["branch"] == "public_beta" and rec["version"] == "2.1"


def test_patch_record_is_compare_and_set(gog, tmp_path):
    install(gog, tmp_path)
    assert not gog._patch_record("123", "b9", date="x"), "a record rewritten by a job must not be patched"
    assert not gog._patch_record("123", "b1", "9.9", date="x")
    assert gog._patch_record("123", "b1", "1.0", date="2024-01-01")
    assert gog.install_record("123")["date"] == "2024-01-01"
    assert not gog._patch_record("404", "", date="x")


# ------------------------------------------------------------------ errors
def test_errors_keep_flag(gog, monkeypatch, tmp_path, server):
    g = install(gog, tmp_path)
    monkeypatch.setattr(gog.api, "builds", FakeBuilds(builds()))
    assert gog.version_info(g).status == "behind"  # saves the summary
    monkeypatch.setattr(gog.api, "builds", FakeBuilds(requests.ConnectionError("down")))
    info = gog.version_info(g, refresh=True)
    assert info.error == "offline" and info.stale and "connection" in info.error_text
    assert info.latest.id == "b2" and info.status == "behind" and info.update_available, "from the saved summary"
    g.update_available = True
    assert gog.check_updates([g]) == [] and g.update_available is True, "a failed check keeps the previous flag"
    monkeypatch.setattr(gog.api, "builds", FakeBuilds(gog_api.ApiError("HTTP 500", status=500)))
    info = gog.version_info(g, refresh=True)
    assert info.error == "failed" and "500" in info.error_text
    monkeypatch.setattr(gog.api, "builds", FakeBuilds([]))
    info = gog.version_info(g, refresh=True)
    assert info.error == "failed" and info.stale and info.latest.id == "b2"
    assert gog.install_record("123")["latest"]["id"] == "b2", "errors never overwrite the saved summary"

    # GogApi.builds: 4xx means "no builds", 5xx raises
    monkeypatch.setattr(gog_api, "CONTENT", server.base)
    for code in (503, 403):
        server.handlers[("GET", f"/products/{code}/")] = lambda req, c=code: (c, {}, b"{}")
    server.files["/products/200/os/windows/builds?generation=2"] = json.dumps({"items": [B("b1", "1", "2024-01-01")]}
                                                                              ).encode()

    class Auth:
        logged_in = False

    client = gog_api.GogApi(Auth(), requests.Session())
    with pytest.raises(gog_api.ApiError) as err:
        client.builds("503", "windows")
    assert err.value.status == 503
    assert client.builds("403", "windows") == [] and client.builds("404", "windows") == []
    assert [b["build_id"] for b in client.builds("200", "windows")] == ["b1"]
    assert gog_api.ApiError("plain").status == 0


def test_signed_out_no_network(gog, monkeypatch, tmp_path):
    monkeypatch.setattr(gog, "connected", lambda: False)
    monkeypatch.setattr(gog.api, "builds", no_network)
    monkeypatch.setattr(gog.api, "product", no_network)
    g = install(gog, tmp_path, latest={"id": "b2", "version": "2.0", "for": "b1", "status": "behind",
                                       "update_available": True, "checked_at": 1000.0})
    for fetch in (True, False):
        info = gog.version_info(g, fetch=fetch)
        assert info.error == "signed_out" and "Sign in" in info.error_text and info.stale
        assert info.installed.id == "b1" and info.installed.version == "1.0"
        assert info.latest.version == "2.0" and info.checked_at == 1000.0 and not info.can_update
    g2 = install(gog, tmp_path, method="installer", platform="linux", build_id="", version="1.1")
    assert gog.version_info(g2).error == "signed_out"


# ------------------------------------------------------------------ installer kind
def test_installer_linux(gog, monkeypatch, tmp_path):
    installers = [{"os": "linux", "language": "en", "version": "1.2", "total_size": 1234},
                  {"os": "windows", "language": "en", "version": "1.3", "total_size": 999}]
    expands = []

    def product(product_id, expand=(), locale="en-US"):
        expands.append(tuple(expand))
        return {"downloads": {"installers": [dict(i) for i in installers]}}

    monkeypatch.setattr(gog.api, "product", product)
    monkeypatch.setattr(gog.api, "builds", no_network)
    g = install(gog, tmp_path, method="installer", platform="linux", build_id="", version="1.1", language="en")
    info = gog.version_info(g)
    assert info.kind == "installer" and info.status == "different" and info.update_available
    assert info.latest.version == "1.2" and info.latest.size == 1234 and not info.can_choose and info.can_update
    assert "newest Linux installer" in info.note and "downloads" in expands[0]
    assert info.method == "GOG offline installer · Linux native"
    assert gog.install_record("123")["latest"]["for"] == "1.1"
    assert gog.versions(g) == []

    installers[0]["version"] = "1.5 (gog-4)"
    g = install(gog, tmp_path, method="installer", platform="linux", build_id="", version="1.5 (gog-3)")
    assert gog.version_info(g, refresh=True).status == "different", "a (gog-N) bump is a new installer"
    g = install(gog, tmp_path, method="installer", platform="linux", build_id="", version="1.5 (gog-4)")
    assert gog.version_info(g).status == "up_to_date"
    (tmp_path / "Game" / "gameinfo").write_text("Test Game\n1.5\n20240101\n")
    g = install(gog, tmp_path, method="installer", platform="linux", build_id="", version="")
    info = gog.version_info(g)
    assert info.installed.version == "1.5" and info.status == "up_to_date", "gameinfo has no (gog-N) suffix"
    installers[0]["version"] = None
    assert gog.version_info(g, refresh=True).status == "unknown"
    installers[:] = installers[1:]
    info = gog.version_info(g, refresh=True)
    assert info.error == "failed" and "Linux installer" in info.error_text


def test_import_versions(gog, monkeypatch, tmp_path):
    lib = tmp_path / "Games"
    win = lib / "Win"
    win.mkdir(parents=True)
    (win / "goggame-123.info").write_text(json.dumps({"gameId": "123", "name": "Win Game", "version": 1,
                                                      "buildId": "b1", "playTasks": []}))
    lin = lib / "Lin"
    (lin / "game").mkdir(parents=True)
    (lin / "start.sh").write_text("")
    (lin / "gameinfo").write_text("Lin Game\n1.5.0\n20240101\n")
    (lin / "game" / "goggame-456.info").write_text(json.dumps({"gameId": "456", "name": "Lin Game", "version": 1}))
    games = {g.source_id: g for g in gog.import_path(str(lib))}
    assert games["123"].version == "" and isinstance(games["123"].version, str)
    rec = gog.install_record("123")
    assert rec["version"] == "" and "branch" not in rec and rec["method"] == "imported-gen2"
    assert games["456"].version == "1.5.0"
    gog._set_record("789", {"install_path": str(win), "platform": "windows", "version": 1, "build_id": "x",
                            "method": "imported-gen2"})
    legacy = Game("gog", "789", "Legacy")
    gog._apply_record(legacy, gog.install_record("789"))
    assert legacy.installed and legacy.version == ""

    # Heroic: a "branch" key is kept (None = Stable); without one the matched build's channel is adopted later
    heroic = tmp_path / "home" / ".config" / "heroic" / "gog_store"
    heroic.mkdir(parents=True)
    (heroic / "installed.json").write_text(json.dumps({"installed": [
        {"appName": "901", "install_path": str(win), "platform": "windows", "buildId": "h1", "branch": "public_beta"},
        {"appName": "902", "install_path": str(win), "platform": "windows", "buildId": "h2", "branch": None},
        {"appName": "903", "install_path": str(win), "platform": "windows", "buildId": "h3"}]}))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    gog._import_heroic()
    assert gog.install_record("901")["branch"] == "public_beta" and gog.install_record("901")["source"] == "heroic"
    assert gog.install_record("902")["branch"] is None and "branch" not in gog.install_record("903")


# ------------------------------------------------------------------ cache
def test_cache_and_coalescing(gog, monkeypatch, tmp_path):
    fake = FakeBuilds(builds())
    monkeypatch.setattr(gog.api, "builds", fake)
    g = install(gog, tmp_path)
    gog.version_info(g)
    gog.version_info(g)
    assert fake.calls == 1
    gog.version_info(g, refresh=True)
    assert fake.calls == 2
    info = gog.version_info(g, fetch=False)
    assert fake.calls == 2 and info.stale and info.status == "behind", "fetch=False uses the memory cache"
    gog.invalidate_versions("123")
    info = gog.version_info(g, fetch=False)
    assert fake.calls == 2 and info.stale and info.latest.id == "b2", "nothing cached: the saved summary"

    gate = threading.Event()
    fake.gate = gate
    results = []
    threads = [threading.Thread(target=lambda: results.append(gog.version_info(g))) for _ in range(2)]
    for t in threads:
        t.start()
    time.sleep(0.3)
    gate.set()
    for t in threads:
        t.join(5)
    assert fake.calls == 3, "concurrent checks share one request"
    assert [i.latest.id for i in results] == ["b2", "b2"]


def test_update_version_rejected_for_installer(gog, monkeypatch, tmp_path):
    monkeypatch.setattr(gog.api, "product", no_network)
    monkeypatch.setattr(gog.api, "builds", no_network)
    monkeypatch.setattr(offline, "download_installer", no_network)
    g = install(gog, tmp_path, method="installer", platform="linux", build_id="", version="1.1")
    with pytest.raises(base.NotSupported):
        gog.update(g, make_ctx(), version="b1")
    with pytest.raises(base.NotSupported):
        gog.update(g, make_ctx(), version=base.branch_target("public_beta"))
