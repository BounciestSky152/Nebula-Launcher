"""Smoke-test every page, menu and dialog of the UI (offscreen) and fail on exceptions raised in slots."""
import copy
import dataclasses
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from nebula.core.models import Achievement, Friend, Game
from nebula.integrations.base import BuildRef, VersionInfo


@pytest.fixture()
def errors(monkeypatch):
    caught = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: caught.append((t, v)))
    return caught


def pump(app, seconds=0.3, until=None):
    """Process events for ``seconds`` (or, with ``until``, until it holds; then up to 5 s)."""
    end = time.time() + (5 if until else seconds)
    while time.time() < end:
        app.processEvents()
        if until is not None and until():
            app.processEvents()
            return
        time.sleep(0.01)


def test_ui_smoke(qapp, services, errors, monkeypatch):
    from nebula.integrations.base import InstallOptions
    from nebula.ui import theme
    from nebula.ui.main_window import MainWindow

    theme.apply(qapp)
    now = time.time()
    games = [Game("gog", "1", "Alpha", installed=True, install_path="/tmp", platform="windows",
                  os_support=["windows"], genres=["RPG"], added_at=now),
             Game("steam", "2", "Alpha", installed=False, platform_playtime=30),
             Game("gog", "3", "Beta", os_support=["linux"]),
             Game("local", "x", "Gamma", installed=True, platform="linux")]
    for src in ("gog", "steam", "local"):
        services.db.replace_source(src, [g for g in games if g.source == src])
    services.db.save_achievements("gog:1", [Achievement("a", "First", "desc", unlocked=True, unlocked_at=now, rarity=5),
                                             Achievement("b", "Second", hidden=True)])
    services.library.load()
    services.friends.friends = [Friend("steam", "1", "Ann", status="playing", playing="Alpha")]
    gog = services.integrations["gog"]
    monkeypatch.setattr(gog, "catalog", lambda **kw: {"pages": 1, "productCount": 1, "products": [
        {"id": "9", "title": "Shop Game", "coverHorizontal": "", "price": {"final": "$9.99", "base": "$19.99",
                                                                          "discount": "-50%"},
         "operatingSystems": ["linux", "windows"], "developers": ["Dev"]}]})
    monkeypatch.setattr(gog, "store_product", lambda pid: {"id": pid, "_description_text": "Nice", "screenshots": []})
    monkeypatch.setattr(gog, "install_options", lambda game, platform="": InstallOptions(
        platforms=["windows", "linux"], platform="windows", languages=[("en-US", "English")], language="en-US",
        versions=[{"id": "b1", "label": "1.0", "date": ""}], install_dir="/tmp/x/Alpha"))
    monkeypatch.setattr(gog, "details", lambda game: __import__("nebula.core.models", fromlist=["GameDetails"]).GameDetails(
        description="Long text", dlcs=[{"id": "d", "title": "DLC", "owned": True, "installed": False}]))
    network = []  # gog:1 has no install record: the version card must not look anything up
    monkeypatch.setattr(gog.api, "builds", lambda *a, **k: network.append(("builds", a)) or [])
    monkeypatch.setattr(gog.api, "product", lambda *a, **k: network.append(("product", a)) or {})
    w = MainWindow(services)
    w.show()
    key = services.library.entry_for_uid("gog:1").key
    for page, arg in [("home", None), ("library", "owned"), ("library", "installed"), ("library", "source:steam"),
                      ("library", {"title": "S", "filter": {"text": "a"}}), ("game", key), ("downloads", None),
                      ("store", None), ("product", {"id": "9", "title": "Shop Game"}), ("wishlist", None),
                      ("stats", None), ("settings", "Compatibility"), ("settings", "Integrations")]:
        w.navigate(page, arg)
        pump(qapp, 0.25)
    gp = w.pages["game"]
    w.navigate("game", key)
    for i in range(5):
        gp.tabs.setCurrentIndex(i)
        pump(qapp, 0.15)
    assert gp.version_card.isHidden() and gp.branch_chip.isHidden()
    gp._build_play_menu()  # noqa: SLF001
    assert not gp.play_menu.isEmpty()
    menu = w.ctl.entry_menu(key)
    assert any(a.text() == "Play" for a in menu.actions())
    lib = w.pages["library"]
    w.navigate("library", "owned")
    lib._build_filter_menu()  # noqa: SLF001
    lib._set_view("list")  # noqa: SLF001
    lib._set_view("grid")  # noqa: SLF001
    lib.flt.sources = ["steam"]
    lib.refresh()
    assert lib.chips.count() == 1
    w.go_back()
    w.go_forward()
    w.toggle_friends()
    w.toast("Hello", "world")
    pump(qapp, 0.2)
    from nebula.ui.dialogs.game_settings import GameSettingsDialog
    from nebula.ui.dialogs.install import InstallDialog
    from nebula.ui.dialogs.misc import AddGameDialog, TagsDialog, UninstallDialog

    g = services.library.game("gog:1")
    for dlg in (InstallDialog(services, g, services.library.entry(key), w), GameSettingsDialog(services, g, w),
                AddGameDialog(None, w), TagsDialog(["a"], ["a"], "Alpha", w), UninstallDialog(g, True, w)):
        dlg.show()
        pump(qapp, 0.4)
        if isinstance(dlg, GameSettingsDialog):
            dlg.args.setText("-dx11")
            dlg._save()  # noqa: SLF001
            assert services.db.get_config("gog:1")["args"] == "-dx11"
        if isinstance(dlg, InstallDialog):
            req = dlg.request()
            assert req.platform == "windows" and req.base_dir == "/tmp/x"
        dlg.close()
    services.library.set_user(key, favorite=True, tags=["rpg"])
    pump(qapp, 0.5)
    assert not network, network
    assert not errors, errors


def test_gog_first_shell_and_self_update(qapp, services, errors, monkeypatch):
    from nebula.core import self_update as su
    from nebula.ui import theme
    from nebula.ui.main_window import MainWindow
    from nebula.updater import UpdateInfo

    theme.apply(qapp)
    services.db.replace_source("steam", [Game("steam", "7", "Hades", installed=True, platform="windows",
                                              os_support=["windows"])])
    services.db.replace_source("gog", [Game("gog", "8", "Owned On Gog", os_support=["linux", "windows"])])
    services.library.load()
    gog = services.integrations["gog"]
    product = {"id": "77", "title": "Hades", "price": {"final": "$24.99", "discount": ""}}
    monkeypatch.setattr(gog, "find_in_store", lambda title: product if title == "Hades" else None)
    monkeypatch.setattr(gog, "catalog", lambda **kw: {"pages": 1, "productCount": 0, "products": []})
    w = MainWindow(services)
    w.show()

    # first run without a GOG account: the GOG sign-in screen comes first, without the shell around it
    w.start_page()
    assert w.stack.currentWidget() is w.pages["welcome"]
    assert not w.sidebar.isVisible() and not w.topbar.isVisible()
    w.pages["welcome"]._skip()  # noqa: SLF001
    assert w.stack.currentWidget() is w.pages["home"] and w.sidebar.isVisible()
    w.start_page()
    assert w.stack.currentWidget() is w.pages["home"], "welcome is only shown until dismissed"
    assert w.topbar.sign_in.isVisible() and not w.topbar.avatar_btn.isVisible()

    # a game owned elsewhere offers the DRM-free GOG version; GOG games carry the DRM-free badge
    gp = w.pages["game"]
    w.navigate("game", services.library.entry_for_uid("steam:7").key)
    pump(qapp, 0.4)
    assert gp.gog_offer.isVisible() and gp._gog_item is product  # noqa: SLF001
    w.navigate("game", services.library.entry_for_uid("gog:8").key)
    pump(qapp, 0.3)
    assert not gp.gog_offer.isVisible()
    chips = [gp.chips.itemAt(i).widget().text() for i in range(gp.chips.count())]
    assert "DRM-FREE" in chips and "Linux native" in chips

    # library: saving/deleting views lives in the Filters menu
    lib = w.pages["library"]
    w.navigate("library", "owned")
    lib._build_filter_menu()  # noqa: SLF001
    assert any("Save as view" in a.text() for a in lib.filter_menu.actions())

    # update banner follows the self-update service
    upd = services.self_update
    banner = w.update_banner
    upd._set(su.AVAILABLE, "2 files changed")  # noqa: SLF001
    assert banner.isVisible() and "2 files changed" in banner.text.text()
    upd._set(su.INSTALLING, "Installing")  # noqa: SLF001
    assert banner.isVisible() and not banner.primary.isVisible()
    upd._set(su.UP_TO_DATE, "")  # noqa: SLF001
    assert not banner.isVisible()
    w.navigate("settings", "Updates")
    pump(qapp, 0.2)

    # updates wait for running games/downloads and never restart underneath them
    restarts = []
    upd.restart_cb = lambda: restarts.append(1)
    upd.info = UpdateInfo("f" * 40, "files", changed=["nebula/x.py"])
    monkeypatch.setattr(upd, "busy_with_games", lambda: True)
    upd.install()
    assert upd.status == su.WAITING
    upd.status = su.INSTALLED
    upd.restart_at = time.time() - 1
    upd._tick_restart()  # noqa: SLF001
    assert not restarts and upd.seconds_to_restart() > 0
    monkeypatch.setattr(upd, "busy_with_games", lambda: False)
    upd.restart_at = time.time() - 1
    upd._tick_restart()  # noqa: SLF001
    assert restarts == [1]
    upd.wait_timer.stop()
    upd.restart_timer.stop()
    pump(qapp, 0.2)
    assert not errors, errors


# ------------------------------------------------------------------ version management on the game page
def _install_gog(services, tmp_path, pid="123", title="Witcher", build="b1", version="1.0") -> Game:
    """An installed GOG Galaxy-builds copy (with its install record) in a freshly loaded library."""
    path = tmp_path / pid
    path.mkdir(exist_ok=True)
    g = Game("gog", pid, title, installed=True, install_path=str(path), platform="windows", os_support=["windows"],
             build_id=build, version=version)
    gog = services.integrations["gog"]
    others = [x for x in services.db.load_games() if x.source == "gog" and x.source_id != pid]
    services.db.replace_source("gog", others + [g])
    rec = {"install_path": str(path), "platform": "windows", "version": version, "build_id": build, "branch": None,
           "language": "en-US", "dlcs": [], "method": "gen2", "size": 1}
    gog._set_record(pid, rec)  # noqa: SLF001
    services.library.load()
    return services.library.game(f"gog:{pid}")


def _ref(bid, ver, date, branch="", **kw) -> BuildRef:
    return BuildRef(id=bid, version=ver, branch=branch, date=date, published=f"{date}T10:00:00+0000", **kw)


def _builds_info(installed: BuildRef, latest: BuildRef, status: str, stale: bool = False, **kw) -> VersionInfo:
    """A builds-kind VersionInfo as GogIntegration.version_info makes it (live, or ``stale`` from the summary)."""
    builds = [] if stale else sorted({latest.id: latest, installed.id: installed}.values(),
                                     key=lambda b: b.published, reverse=True)
    builds = [dataclasses.replace(b, current=b.id == installed.id, newest=b.id == latest.id) for b in builds]
    d = dict(kind="builds", method="GOG Galaxy builds · Windows version (runs with Proton)", platform="windows",
             installed=dataclasses.replace(installed, current=True), installed_listed=not stale,
             latest=dataclasses.replace(latest, newest=True), branches=[] if stale else [""], builds=builds,
             status=status, newer_count=int(status == "behind"), update_available=status == "behind",
             can_choose=True, can_update=True, stale=stale, checked_at=time.time() - (7200 if stale else 5),
             updated_at=time.time() - 3 * 86400)
    d.update(kw)
    return VersionInfo(**d)


B1, B2 = _ref("b1", "1.0", "2024-01-01"), _ref("b2", "2.0", "2024-02-01")


def _fake_version_info(monkeypatch, gog, live: dict, saved: dict, gates: dict | None = None) -> list:
    """Patch gog.version_info: fetch=False -> ``saved[pid]`` (no network), fetch=True -> ``live[pid]`` (after
    ``gates[pid]`` is set, if any). Returns the list of (pid, fetch, refresh) calls."""
    calls = []

    def fake(game, fetch=True, refresh=False):
        calls.append((game.source_id, fetch, refresh))
        if fetch and gates and game.source_id in gates:
            gates[game.source_id].wait(5)
        return copy.deepcopy((live if fetch else saved).get(game.source_id))

    monkeypatch.setattr(gog, "version_info", fake)
    return calls


def _version_window(qapp, services, monkeypatch):
    from nebula.core.models import GameDetails
    from nebula.ui import theme
    from nebula.ui.main_window import MainWindow

    theme.apply(qapp)
    services.settings.set("downloads.auto_update", True)
    gog = services.integrations["gog"]
    monkeypatch.setattr(gog, "details", lambda game: GameDetails(description="A game"))
    w = MainWindow(services)
    w.show()
    monkeypatch.setattr(gog, "connected", lambda: True)  # after the window: no sign-in side effects
    return w, gog


def _info_rows(gp) -> dict:
    texts = [gp.info_grid.itemAt(i).widget().text() for i in range(gp.info_grid.count())]
    return dict(zip(texts[::2], texts[1::2]))


def test_game_page_version_card(qapp, services, errors, caplog, monkeypatch, tmp_path):
    g = _install_gog(services, tmp_path)
    w, gog = _version_window(qapp, services, monkeypatch)
    saved = _builds_info(B1, B1, "up_to_date", stale=True)  # the last check (2 h ago) found nothing newer
    calls = _fake_version_info(monkeypatch, gog, live={"123": _builds_info(B1, B2, "behind")}, saved={"123": saved})
    key = services.library.entry_for_uid(g.uid).key
    gp, card = w.pages["game"], w.pages["game"].version_card

    w.navigate("game", key)
    # at once: the saved summary, with a spinner while the live check runs
    assert card.isVisible() and card.new_spin._timer.isActive() and not card.new_spin.isHidden()  # noqa: SLF001
    assert card.new_value.text() == "1.0" and not card.refresh_btn.isEnabled()
    pump(qapp, until=lambda: not card.new_spin._timer.isActive())  # noqa: SLF001
    assert not card.new_spin._timer.isActive()  # noqa: SLF001
    assert card.new_value.text() == "2.0" and card.pill.text() == "Update available"
    assert card.update_btn.isVisible() and card.update_btn.text() == "Update to 2.0"
    assert card.change_btn.isVisible() and card.cur_value.text() == "1.0"
    assert services.library.game(g.uid).update_available
    assert gp.play.text() == "UPDATE"
    assert [c for c in calls if c[1]] == [("123", True, False)], "one live check, served to the card"

    # Info tab
    gp.tabs.setCurrentIndex(4)
    pump(qapp, 0.1)
    rows = _info_rows(gp)
    assert rows["VERSION"] == "1.0 (build b1)" and rows["NEWEST VERSION"] == "2.0"
    assert rows["INSTALLED WITH"].startswith("GOG Galaxy builds") and rows["UPDATES"] == "Automatic"
    assert rows["VERSION RELEASED"] == "1 Jan 2024"
    gp.tabs.setCurrentIndex(0)

    # the play menu keeps "Play without updating" and offers the change-version dialog
    gp._build_play_menu()  # noqa: SLF001
    texts = [a.text() for a in gp.play_menu.actions()]
    assert "Play without updating" in texts and "Change version…" in texts

    # UPDATE updates (it no longer launches); the card's button too; cover play buttons still launch
    updates, launches = [], []
    monkeypatch.setattr(services.actions, "update", lambda game, **kw: updates.append((game.uid, kw)))
    monkeypatch.setattr(services.launcher, "launch", lambda uid, task_id="": launches.append(uid))
    gp.play.click()
    card.update_btn.click()
    w.ctl.primary_action(key, update=False)
    assert updates == [(g.uid, {}), (g.uid, {"version": "", "change": "update", "label": "2.0"})]
    assert launches == [g.uid]

    # the refresh button forces a new check
    card.refresh_btn.click()
    pump(qapp, until=lambda: not card.new_spin._timer.isActive())  # noqa: SLF001
    assert calls[-1] == ("123", True, True) and card.new_value.text() == "2.0"
    pump(qapp, 0.1)
    assert "UI callback failed" not in caplog.text
    assert not errors, errors


def test_version_card_stale_result_ignored(qapp, services, errors, caplog, monkeypatch, tmp_path):
    a = _install_gog(services, tmp_path)
    b = _install_gog(services, tmp_path, "456", "Gwent", "g1", "3.0")
    assert services.library.game(a.uid).installed and b.installed
    w, gog = _version_window(qapp, services, monkeypatch)
    g1 = _ref("g1", "3.0", "2024-05-01", "public_beta")  # B is on a beta channel
    gate = threading.Event()
    beta = dict(branch="public_beta", branches=["", "public_beta"])
    calls = _fake_version_info(
        monkeypatch, gog,
        live={"123": _builds_info(B1, B2, "behind"), "456": _builds_info(g1, g1, "up_to_date", **beta)},
        saved={"123": _builds_info(B1, B1, "up_to_date", stale=True),
               "456": _builds_info(g1, g1, "up_to_date", stale=True, branch="public_beta")}, gates={"123": gate})
    gp, card = w.pages["game"], w.pages["game"].version_card
    try:
        w.navigate("game", services.library.entry_for_uid(a.uid).key)
        pump(qapp, 0.1)
        assert card.new_spin._timer.isActive() and gp.branch_chip.isHidden()  # noqa: SLF001
        w.navigate("game", services.library.entry_for_uid(b.uid).key)
        pump(qapp, until=lambda: not card.new_spin._timer.isActive())  # noqa: SLF001
        assert card.new_value.text() == "3.0" and card.pill.text() == "Up to date"
        assert ("456", True, False) in calls
        gate.set()  # A's check answers late: it must not land on B's card (or flag A from B's page)
        pump(qapp, 0.3)
    finally:
        gate.set()
    assert card.cur_value.text() == "3.0" and card.new_value.text() == "3.0" and card.pill.text() == "Up to date"
    assert card.channel_value.text() == "Public beta"
    assert gp.branch_chip.isVisible() and gp.branch_chip.text() == "Beta: Public beta"
    assert not card.update_btn.isVisible() and not card.new_spin._timer.isActive()  # noqa: SLF001
    assert not services.library.game(a.uid).update_available
    assert "UI callback failed" not in caplog.text
    assert not errors, errors


def test_version_card_pin_toggle(qapp, services, errors, caplog, monkeypatch, tmp_path):
    g = _install_gog(services, tmp_path)
    w, gog = _version_window(qapp, services, monkeypatch)
    calls = _fake_version_info(monkeypatch, gog, live={"123": _builds_info(B1, B2, "behind")},
                               saved={"123": _builds_info(B1, B2, "behind", stale=True)})
    gp, card = w.pages["game"], w.pages["game"].version_card
    w.navigate("game", services.library.entry_for_uid(g.uid).key)
    pump(qapp, until=lambda: not card.new_spin._timer.isActive())  # noqa: SLF001
    assert gp.play.text() == "UPDATE" and card.auto_cb.isEnabled()

    card.pin_cb.click()  # stay on 1.0
    assert services.db.get_config(g.uid).get("pin_version") is True
    assert not services.library.game(g.uid).update_available and gp.play.text() == "PLAY"
    assert card.pin_cb.isChecked() and card.pill.text() == "Staying on 1.0" and not card.auto_cb.isEnabled()
    assert card.update_btn.objectName() != "Primary"
    gp.tabs.setCurrentIndex(4)
    pump(qapp, 0.1)
    assert _info_rows(gp)["UPDATES"] == "Off — staying on 1.0"
    gp.tabs.setCurrentIndex(0)

    n = len(calls)
    card.pin_cb.click()  # updates allowed again: the flag comes back at once (no network), then a fresh check
    assert services.db.get_config(g.uid).get("pin_version") is False
    assert services.library.game(g.uid).update_available and gp.play.text() == "UPDATE"
    assert not card.pin_cb.isChecked() and card.pill.text() == "Update available"
    pump(qapp, until=lambda: ("123", True, True) in calls[n:] and not card.new_spin._timer.isActive())  # noqa: SLF001
    assert ("123", True, True) in calls[n:] and gp.play.text() == "UPDATE"

    card.auto_cb.click()  # per-game automatic updates
    assert services.db.get_config(g.uid).get("auto_update") is False and not card.auto_cb.isChecked()
    pump(qapp, 0.1)
    assert "UI callback failed" not in caplog.text
    assert not errors, errors


def test_controller_versions_dispatch_and_busy(qapp, services, errors, monkeypatch, tmp_path):
    import nebula.ui.dialogs.versions as versions_mod

    g = _install_gog(services, tmp_path)
    services.db.replace_source("steam", [Game("steam", "7", "Hades", installed=True, platform="windows",
                                              update_available=True)])
    services.library.load()
    w, gog = _version_window(qapp, services, monkeypatch)
    built = []

    class StubDialog:
        def __init__(self, s, game, info=None, parent=None):
            built.append((game.uid, info))
            self.choice = {"version": "b1", "change": "rollback", "label": "1.0"}

        def exec(self):
            return 1

    monkeypatch.setattr(versions_mod, "ChangeVersionDialog", StubDialog)
    updates, launches = [], []
    monkeypatch.setattr(services.actions, "update", lambda game, **kw: updates.append((game.uid, kw)) or
                        SimpleNamespace(verb="Rolling back to 1.0"))
    monkeypatch.setattr(services.launcher, "launch", lambda uid, task_id="": launches.append(uid))

    w.ctl.versions(g)
    assert built == [(g.uid, None)]
    assert updates == [(g.uid, {"version": "b1", "change": "rollback", "label": "1.0"})]

    busy = {g.uid}
    real_active = services.jobs.active_for
    monkeypatch.setattr(services.jobs, "active_for", lambda uid: object() if uid in busy else real_active(uid))
    w.ctl.versions(g)
    assert len(built) == 1 and len(updates) == 1, "busy: the dialog is never built"
    busy.clear()

    # "Change version…" in Manage installation only for Galaxy-builds copies
    def manage_texts():
        menu = w.ctl.entry_menu(services.library.entry_for_uid(g.uid).key)
        manage = next(a.menu() for a in menu.actions() if a.text() == "Manage installation")
        return [a.text() for a in manage.actions()]

    infos = {"123": _builds_info(B1, B2, "behind", stale=True)}
    _fake_version_info(monkeypatch, gog, live={}, saved=infos)
    assert "Change version…" in manage_texts()
    infos["123"] = VersionInfo(kind="installer", installed=BuildRef(version="1.0", current=True))
    assert "Change version…" not in manage_texts()

    # PLAY/UPDATE: an outdated GOG copy updates, Steam updates its games itself (launch)
    services.library.game(g.uid).update_available = True
    services.library.rebuild()
    w.ctl.primary_action(services.library.entry_for_uid(g.uid).key)
    assert updates[-1] == (g.uid, {})
    w.ctl.primary_action(services.library.entry_for_uid("steam:7").key)
    assert launches == ["steam:7"] and len(updates) == 2
    assert not errors, errors


def test_game_settings_three_way_merge(qapp, services, errors, monkeypatch, tmp_path):
    from nebula.ui import theme
    from nebula.ui.dialogs.game_settings import GameSettingsDialog

    theme.apply(qapp)
    g = _install_gog(services, tmp_path)
    services.db.set_config(g.uid, {"args": "-x"})
    dlg = GameSettingsDialog(services, g)
    assert dlg.pin.text() == "Stay on this version (don't offer updates)"
    # written while the dialog is open (a rollback job pins, the launcher records installed redistributables)
    services.db.update_config(g.uid, pin_version=True, redists_done=["vcredist"])
    dlg.args.setText("-dx11")
    dlg._save()  # noqa: SLF001
    cfg = services.db.get_config(g.uid)
    assert cfg["args"] == "-dx11" and cfg["pin_version"] is True and cfg["redists_done"] == ["vcredist"]

    # an untouched dialog writes nothing back
    before = services.db.get_config(g.uid)
    GameSettingsDialog(services, g)._save(close=False)  # noqa: SLF001
    assert services.db.get_config(g.uid) == before

    # ticking the box goes through Actions.set_version_pin (update flag, game_changed)
    pins = []
    monkeypatch.setattr(services.actions, "set_version_pin", lambda game, on: pins.append((game.uid, on)))
    services.db.update_config(g.uid, pin_version=False)
    dlg = GameSettingsDialog(services, g)
    dlg.pin.setChecked(True)
    dlg._save()  # noqa: SLF001
    assert pins == [(g.uid, True)] and services.db.get_config(g.uid)["pin_version"] is True
    assert not errors, errors


def test_version_card_for_game_not_installed(qapp, services, errors, monkeypatch, tmp_path):
    """Every GOG game page shows the newest version, through the real integration (only HTTP is faked)."""
    services.db.replace_source("gog", [Game("gog", "321", "Not Yet", os_support=["windows"])])
    services.library.load()
    w, gog = _version_window(qapp, services, monkeypatch)
    builds = [{"build_id": "b3", "version_name": "2.1 beta", "branch": "beta", "generation": 2,
               "date_published": "2024-03-01T00:00:00+0000"},
              {"build_id": "b2", "version_name": "2.0", "branch": None, "generation": 2,
               "date_published": "2024-02-01T00:00:00+0000"}]
    monkeypatch.setattr(gog.api, "builds", lambda pid, os_name, generation=2: list(builds))
    installs = []
    monkeypatch.setattr(w.ctl, "install", lambda g: installs.append(g.uid))
    gp, card = w.pages["game"], w.pages["game"].version_card
    w.navigate("game", services.library.entry_for_uid("gog:321").key)
    pump(qapp, until=lambda: card.new_value.text() == "2.0")
    assert card.isVisible() and card.cur_value.text() == "Not installed"
    assert card.new_value.text() == "2.0", "the newest Stable build, not the newer beta"
    assert card.update_btn.text() == "Install 2.0…" and not card.change_btn.isVisible()
    assert not card.pin_cb.isVisible()
    card.update_btn.click()
    assert installs == ["gog:321"]
    gp.tabs.setCurrentIndex(4)
    pump(qapp, 0.1)
    texts = [gp.info_grid.itemAt(i).widget().text() for i in range(gp.info_grid.count())]
    assert "NEWEST VERSION" in texts and any(t.startswith("2.0") for t in texts)
    assert not errors, errors
