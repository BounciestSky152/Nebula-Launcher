"""Smoke-test every page, menu and dialog of the UI (offscreen) and fail on exceptions raised in slots."""
import sys
import time

import pytest

from nebula.core.models import Achievement, Friend, Game


@pytest.fixture()
def errors(monkeypatch):
    caught = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: caught.append((t, v)))
    return caught


def pump(app, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
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
