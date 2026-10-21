"""Version card view model / widget and the change-version dialog (offscreen)."""
import copy
import dataclasses
import sys
import threading
import time

import pytest

from nebula.core.models import Game
from nebula.integrations.base import BuildRef, VersionInfo
from nebula.ui.widgets.version_card import describe_version

LINUX_NOTE = ("GOG only offers the newest Linux installer, so older versions and beta channels can't be installed for "
              "the Linux version. They're only offered for Windows (GOG Galaxy builds), which Super Nova can run with "
              "Proton.")


@pytest.fixture()
def errors(monkeypatch):
    caught = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: caught.append((t, v)))
    return caught


def pump(app, seconds=0.3, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if until is not None and until():
            return
        time.sleep(0.01)


def B(bid, ver, date, branch="", newest=False):  # noqa: N802
    return BuildRef(id=bid, version=ver, branch=branch, date=date, published=f"{date}T10:00:00+0000", newest=newest)


BUILDS = [B("b3", "2.1", "2024-03-01", "public_beta", newest=True), B("b2", "2.0", "2024-02-01", newest=True),
          B("b1", "1.0", "2024-01-01")]


def builds_info(installed: str, status: str, builds=None, **kw) -> VersionInfo:
    """A live builds-kind VersionInfo for a copy of build ``installed`` (as GogIntegration.version_info makes)."""
    builds = copy.deepcopy(builds if builds is not None else BUILDS)
    for b in builds:
        b.current = b.id == installed
    cur = next(b for b in builds if b.current)
    branches = [""] + sorted({b.branch for b in builds if b.branch})
    d = dict(kind="builds", method="GOG Galaxy builds · Windows version (runs with Proton)", platform="windows",
             installed=dataclasses.replace(cur, newest=False), installed_listed=True,
             latest=next(b for b in builds if b.newest and b.branch == cur.branch), branch=cur.branch,
             branches=branches, builds=builds, status=status, update_available=status == "behind",
             can_choose=True, can_update=True, checked_at=time.time() - 120, updated_at=time.time() - 3 * 86400)
    d.update(kw)
    return VersionInfo(**d)


def installer_info(installed: str = "1.1", status: str = "different", **kw) -> VersionInfo:
    d = dict(kind="installer", method="GOG offline installer · Linux native", platform="linux",
             installed=BuildRef(version=installed, current=True),
             latest=BuildRef(version="1.2", size=500 * 1024 * 1024, newest=True), status=status,
             update_available=status == "different", can_update=True, note=LINUX_NOTE, checked_at=time.time())
    d.update(kw)
    return VersionInfo(**d)


def _ended() -> VersionInfo:
    stable = [b for b in BUILDS if not b.branch]
    info = builds_info("b2", "channel_ended", builds=stable, branch="", branch_gone=True, branches=[""])
    # the copy is on a beta build GOG no longer lists
    info.installed = BuildRef(id="b3", version="2.1", branch="public_beta", date="2024-03-01", current=True)
    info.installed_listed = False
    for b in info.builds:
        b.current = False
    return info


def _unlisted(origin: str, update_available: bool) -> VersionInfo:
    info = builds_info("b1", "unlisted", update_available=update_available, origin=origin, installed_listed=False)
    info.installed = BuildRef(id="L9", current=True)
    for b in info.builds:
        b.current = False
    return info


STALE_EMPTY = VersionInfo(kind="builds", installed=BuildRef(id="b1", version="1.0", date="2024-01-01", current=True),
                          stale=True)

# (name, info, describe_version kwargs, expectations: attribute -> value, or callable(value) -> bool)
STATES = [
    ("hidden", None, {}, {"visible": False}),
    ("kind none", VersionInfo(kind="none", installed=BuildRef(version="1.0")), {},
     {"status_text": "Super Nova can't manage versions for this copy.", "update_visible": False,
      "change_visible": False, "refresh_visible": False, "toggles_visible": False, "signin_visible": False}),
    ("loading, nothing known", STALE_EMPTY, {"loading": True},
     {"newest_text": lambda s: s.startswith("Checking GOG"), "newest_loading": True, "refresh_enabled": False,
      "installed_text": "1.0", "update_visible": False, "change_visible": True}),
    ("loading over saved data", builds_info("b1", "behind", stale=True), {"loading": True},
     {"newest_text": "2.0", "newest_loading": True, "refresh_enabled": False, "update_text": "Update to 2.0"}),
    ("loading hides an old error", builds_info("b1", "behind", error="offline", error_text="x", stale=True),
     {"loading": True}, {"status_text": "", "newest_text": "2.0", "newest_loading": True}),
    ("up to date", builds_info("b2", "up_to_date"), {},
     {"pill_text": "Up to date", "pill_obj": "Success", "update_visible": False, "change_visible": True,
      "newest_text": "2.0", "newest_sub": "You have the newest version · released 1 Feb 2024", "status_text": "",
      "channel_text": "Stable · 1 beta channel available", "chip_text": "", "toggles_visible": True}),
    ("up to date, pinned", builds_info("b2", "up_to_date", pinned=True), {},
     {"status_text": lambda s: "Updates are paused" in s, "pin_checked": True, "auto_enabled": False}),
    ("behind", builds_info("b1", "behind", newer_count=1), {},
     {"pill_text": "Update available", "pill_obj": "Warning", "newest_obj": "Warning", "newest_text": "2.0",
      "newest_sub": "Released 1 Feb 2024", "update_visible": True, "update_text": "Update to 2.0",
      "update_primary": True, "update_version": "", "update_change": "update", "update_label": "2.0",
      "installed_text": "1.0", "installed_sub": "Build b1 · released 1 Jan 2024 · updated 3 days ago",
      "change_visible": True, "update_enabled": True, "change_enabled": True}),
    ("behind by several", builds_info("b1", "behind", newer_count=3), {},
     {"newest_sub": "Released 1 Feb 2024 · 3 newer versions since yours"}),
    ("pinned behind", builds_info("b1", "behind", pinned=True), {},
     {"pill_text": "Staying on 1.0", "pill_obj": "Faint", "newest_obj": "Faint",
      "status_text": lambda s: "staying on 1.0" in s, "auto_enabled": False,
      "auto_tip": lambda s: "Stay on this version" in s, "update_visible": True, "update_primary": False,
      "update_text": "Update to 2.0", "update_tip": "Updating turns off “Stay on this version”."}),
    ("beta", builds_info("b3", "up_to_date"), {},
     {"channel_text": "Public beta", "chip_text": "Beta: Public beta", "newest_text": "2.1",
      "pill_obj": "Success"}),
    ("channel ended", _ended(), {},
     {"pill_text": "Channel ended", "pill_obj": "Warning", "newest_text": "2.0 (Stable)",
      "update_text": "Switch to Stable (2.0)", "update_version": "branch:", "update_change": "switch",
      "update_label": "Stable 2.0", "status_obj": "Warning",
      "status_text": lambda s: s.startswith("The Public beta channel is no longer available from GOG"),
      "chip_text": "Beta: Public beta"}),
    ("unlisted import", _unlisted("folder", False), {},
     {"update_visible": False, "status_text": lambda s: "couldn't match this imported copy" in s,
      "installed_text": "Build L9", "installed_sub": lambda s: s.endswith("imported from a folder"),
      "change_visible": True}),
    ("unlisted own install", _unlisted("nebula", True), {},
     {"update_visible": True, "update_text": "Update to 2.0", "pill_text": "Update available",
      "status_text": "GOG no longer lists the build you have (build L9)."}),
    ("installer different", installer_info(), {},
     {"update_text": "Download installer 1.2 (500.0 MB)", "update_visible": True, "update_primary": True,
      "change_visible": False, "note_text": lambda s: "Proton" in s, "pill_text": "New installer",
      "newest_obj": "Warning", "newest_sub": "GOG's current Linux installer · differs from yours",
      "installed_sub": lambda s: s.startswith("GOG Linux installer"),
      "update_tip": lambda s: s.startswith("Downloads GOG's full installer"), "update_version": "",
      "update_label": "1.2", "channel_text": "", "toggles_visible": True}),
    ("installer different, pinned", installer_info(pinned=True), {},
     {"pill_text": "Staying on 1.1", "update_primary": False,
      "update_tip": lambda s: s.endswith("Updating turns off “Stay on this version”.")}),
    ("installer up to date", installer_info("1.2", "up_to_date", platform="windows"), {},
     {"pill_obj": "Success", "update_visible": False, "change_visible": False,
      "newest_sub": "GOG's current Windows installer",
      "installed_sub": lambda s: s.startswith("GOG offline installer (Windows)")}),
    ("installer, installed version unknown", installer_info("", "unknown"), {},
     {"installed_text": "Unknown version", "newest_text": "1.2", "update_visible": True, "update_primary": False,
      "status_text": "Super Nova doesn't know which installer version this copy came from."}),
    ("signed out", builds_info("b1", "behind", error="signed_out", stale=True), {},
     {"signin_visible": True, "refresh_visible": False, "update_visible": False, "change_visible": True,
      "change_enabled": False, "change_tip": "Sign in to GOG first", "newest_obj": "Faint",
      "newest_sub": lambda s: s.startswith("Last checked "),
      "status_text": "Sign in to GOG to check for updates and change versions."}),
    ("signed out, nothing saved", dataclasses.replace(STALE_EMPTY, error="signed_out"), {},
     {"signin_visible": True, "newest_text": "—", "newest_sub": ""}),
    ("signed out installer", installer_info(error="signed_out", stale=True), {},
     {"signin_visible": True, "change_visible": False}),
    ("offline with saved", builds_info("b1", "behind", error="offline", stale=True,
                                       error_text="Couldn't reach GOG. Check your connection and try again."), {},
     {"newest_sub": lambda s: s.startswith("Couldn't reach GOG · last checked "), "newest_obj": "Faint",
      "status_obj": "Error", "status_text": lambda s: s.startswith("Couldn't reach GOG."),
      "update_visible": False, "change_visible": True, "change_enabled": True, "refresh_visible": True,
      "refresh_enabled": True}),
    ("offline, nothing saved", dataclasses.replace(STALE_EMPTY, error="offline", error_text="x"), {},
     {"newest_text": "Couldn't reach GOG", "newest_obj": "Error", "newest_sub": ""}),
    ("failed with saved", builds_info("b1", "behind", error="failed", error_text="GOG returned an error (500).",
                                      stale=True), {},
     {"newest_sub": lambda s: s.startswith("GOG returned an error · last checked")}),
    ("busy job", builds_info("b1", "behind"), {"busy": "job"},
     {"update_enabled": False, "change_enabled": False, "pin_enabled": False,
      "update_tip": "Wait for the current download to finish.",
      "change_tip": "Wait for the current download to finish."}),
    ("busy running", builds_info("b1", "behind"), {"busy": "running"},
     {"update_enabled": False, "change_enabled": False, "pin_enabled": True,
      "update_tip": "Close the game before changing its version."}),
    ("global auto off", builds_info("b2", "up_to_date"), {"global_auto": False},
     {"auto_enabled": False, "auto_checked": True, "auto_tip": lambda s: "Settings › Downloads" in s}),
    ("per-game auto off", builds_info("b2", "up_to_date", auto_update=False), {},
     {"auto_checked": False, "auto_enabled": True, "auto_tip": ""}),
    ("multi source", builds_info("b2", "up_to_date"), {"multi_source": True}, {"title": "GOG version"}),
]


@pytest.mark.parametrize("name,info,kwargs,expect", STATES, ids=[s[0] for s in STATES])
def test_describe_version_states(name, info, kwargs, expect):
    before = copy.deepcopy(info)
    v = describe_version(info, **kwargs)
    for attr, want in expect.items():
        got = getattr(v, attr)
        if callable(want):
            assert want(got), f"{name}: {attr} = {got!r}"
        else:
            assert got == want, f"{name}: {attr} = {got!r}, expected {want!r}"
    assert info == before  # pure: the info is not modified


def test_version_card_widget(qapp):
    from PySide6.QtCore import Qt

    from nebula.ui.widgets.version_card import VersionCard

    card = VersionCard()
    emitted = []
    for sig in ("update_requested", "change_requested", "refresh_requested", "signin_requested"):
        getattr(card, sig).connect(lambda s=sig: emitted.append(s))
    card.pin_toggled.connect(lambda on: emitted.append(("pin", on)))
    card.auto_toggled.connect(lambda on: emitted.append(("auto", on)))
    assert card.objectName() == "Card"

    card.set_view(describe_version(builds_info("b1", "behind")))
    assert card.update_btn.text() == "Update to 2.0" and not card.update_btn.isHidden()
    assert card.update_btn.objectName() == "Primary"
    assert card.new_value.text() == "2.0" and card.new_value.objectName() == "Warning"
    assert card.pill.text() == "Update available" and card.pill.objectName() == "Warning"
    assert not card.channel_value.isHidden() and card.signin_btn.isHidden()
    assert card.new_spin.isHidden()

    card.set_view(describe_version(builds_info("b1", "behind", pinned=True)))
    assert card.update_btn.objectName() == "" and card.pin_cb.isChecked() and not card.auto_cb.isEnabled()
    assert emitted == []  # programmatic state changes don't emit

    card.set_view(describe_version(STALE_EMPTY, loading=True))
    assert not card.new_spin.isHidden() and card.new_spin._timer.isActive()  # noqa: SLF001
    assert card.channel_value.isHidden() and card.pill.isHidden() and not card.refresh_btn.isEnabled()

    card.set_view(describe_version(builds_info("b2", "up_to_date", updated_at=0.0)))
    assert card.new_spin.isHidden() and card.update_btn.isHidden()
    assert card.cur_sub.text() == "Build b2 · released 1 Feb 2024"

    card.set_view(describe_version(builds_info("b1", "behind", error="signed_out", stale=True)))
    assert not card.signin_btn.isHidden() and card.refresh_btn.isHidden() and not card.change_btn.isEnabled()

    card.set_view(describe_version(VersionInfo(kind="builds", installed=BuildRef(version="A&B", current=True),
                                               latest=BuildRef(id="x", version="C&D"), status="behind",
                                               update_available=True)))
    assert card.update_btn.text() == "Update to C&&D"  # shown literally, not as a mnemonic
    for lb in (card.pill, card.cur_value, card.cur_sub, card.channel_value, card.new_value, card.new_sub, card.status):
        assert lb.textFormat() == Qt.TextFormat.PlainText

    card.set_view(describe_version(None))
    assert card.isHidden()

    card.set_view(describe_version(builds_info("b1", "behind")))
    card.update_btn.click()
    card.change_btn.click()
    card.refresh_btn.click()
    card.pin_cb.click()
    card.auto_cb.click()
    assert emitted == ["update_requested", "change_requested", "refresh_requested", ("pin", True), ("auto", False)]


def _game(**kw) -> Game:
    d = dict(installed=True, install_path="/tmp/alpha", platform="windows", build_id="b1", version="1.0")
    d.update(kw)
    return Game("gog", "123", "Alpha", **d)


def _rows(dlg):
    return [dlg.tree.topLevelItem(i) for i in range(dlg.tree.topLevelItemCount())]


def _select(dlg, version: str):
    item = next(it for it in _rows(dlg) if it.text(0).startswith(version))
    dlg.tree.setCurrentItem(item)
    return item


def test_change_version_dialog(qapp, services, errors, monkeypatch):
    from PySide6.QtCore import Qt

    from nebula.ui.dialogs.versions import ChangeVersionDialog

    gog = services.integration("gog")
    monkeypatch.setattr(gog, "version_info", lambda *a, **kw: pytest.fail("a live info must not be fetched again"))

    # b1 installed on Stable; b2 newest Stable; b3 newest beta
    dlg = ChangeVersionDialog(services, _game(), builds_info("b1", "behind"))
    assert dlg.windowTitle() == "Change version — Alpha"
    assert dlg.sub.text() == "Installed: 1.0 · Stable · released 1 Jan 2024"
    assert not dlg.channel_row.isHidden() and dlg.channel.currentText() == "Stable (recommended)"
    assert [dlg.channel.itemText(i) for i in range(dlg.channel.count())] == ["Stable (recommended)", "Public beta"]
    rows = _rows(dlg)
    assert [(r.text(0), r.text(1), r.text(2)) for r in rows] == [("2.0", "1 Feb 2024", "Newest"),
                                                                 ("1.0", "1 Jan 2024", "Installed")]
    assert not rows[1].flags() & Qt.ItemFlag.ItemIsSelectable
    assert not dlg.tree.selectedItems() and not dlg.ok_button.isEnabled() and dlg.choice is None
    assert dlg.message.isHidden() and dlg.retry.isHidden()

    _select(dlg, "2.0")
    assert dlg.ok_button.text() == "Update to 2.0" and dlg.ok_button.isEnabled()
    assert dlg.choice == {"version": "", "change": "update", "label": "2.0"}
    assert dlg.summary.text().startswith("Update to 2.0, the newest Stable version (released 1 Feb 2024).")
    assert dlg.warn.isHidden()

    dlg.channel.setCurrentIndex(dlg.channel.findData("public_beta"))
    assert dlg.choice is None and not dlg.ok_button.isEnabled()  # a new channel clears the selection
    assert [r.text(0) for r in _rows(dlg)] == ["2.1"]
    _select(dlg, "2.1")
    assert dlg.ok_button.text() == "Switch to Public beta"
    assert dlg.choice == {"version": "branch:public_beta", "change": "switch", "label": "Public beta 2.1"}
    assert "You'll get Public beta updates from now on." in dlg.summary.text()
    assert not dlg.warn.isHidden() and "Beta versions can be unstable" in dlg.warn.text()
    dlg.reject()

    # b2 installed: rolling back to b1 (with DLC installed)
    dlg = ChangeVersionDialog(services, _game(build_id="b2", extra={"installed_dlcs": ["999"]}),
                              builds_info("b2", "up_to_date", pinned=True))
    assert dlg.sub.text().endswith("· staying on this version")
    assert [r.text(2) for r in _rows(dlg)] == ["Installed · Newest", ""]
    _select(dlg, "1.0")
    assert dlg.ok_button.text() == "Roll back to 1.0"
    assert dlg.choice == {"version": "b1", "change": "rollback", "label": "1.0"}
    assert not dlg.warn.isHidden() and "Saves made by a newer version" in dlg.warn.text()
    assert "DLC that isn't part of 1.0 is removed" in dlg.summary.text()
    dlg.reject()

    # a beta copy going back to an older Stable build; duplicate version names show their build id
    builds = BUILDS + [B("b0", "1.0", "2023-12-01")]
    dlg = ChangeVersionDialog(services, _game(build_id="b3"), builds_info("b3", "up_to_date", builds=builds))
    assert dlg.channel.currentData() == "public_beta"
    dlg.channel.setCurrentIndex(dlg.channel.findData(""))
    assert [r.text(0) for r in _rows(dlg)] == ["2.0", "1.0 (build b1)", "1.0 (build b0)"]
    _select(dlg, "1.0 (build b1)")
    assert dlg.ok_button.text() == "Switch to Stable 1.0"
    assert dlg.choice == {"version": "b1", "change": "switch", "label": "Stable 1.0"}
    assert "Updates will be paused because this isn't the newest Stable version." in dlg.summary.text()
    assert "Saves made in a beta may not load in Stable versions" in dlg.warn.text()
    dlg.reject()

    # same channel, newer than installed but not the newest
    builds = [B("b4", "3.0", "2024-04-01", newest=True)] + copy.deepcopy([b for b in BUILDS if not b.branch])
    builds[1].newest = False
    dlg = ChangeVersionDialog(services, _game(), builds_info("b1", "behind", builds=builds))
    assert dlg.channel_row.isHidden()  # Stable only, and the copy is on Stable
    _select(dlg, "2.0")
    assert dlg.ok_button.text() == "Install 2.0"
    assert dlg.choice == {"version": "b2", "change": "update", "label": "2.0"}
    assert "A newer version, 3.0, exists" in dlg.summary.text() and dlg.warn.isHidden()
    dlg.reject()

    # the installed build isn't listed: the direction is unknown
    info = _unlisted("folder", False)
    dlg = ChangeVersionDialog(services, _game(build_id="L9"), info)
    _select(dlg, "1.0")
    assert dlg.ok_button.text() == "Install 1.0"
    assert dlg.choice == {"version": "b1", "change": "change", "label": "1.0"}
    dlg.reject()

    # installer copies can only download GOG's current installer
    dlg = ChangeVersionDialog(services, _game(platform="linux", build_id=""), installer_info())
    assert dlg.tree.isHidden() and dlg.channel_row.isHidden()
    assert "newest Linux installer" in dlg.message.text() and not dlg.message.isHidden()
    assert dlg.ok_button.text() == "Download installer 1.2" and dlg.ok_button.isEnabled()
    assert dlg.choice == {"version": "", "change": "update", "label": "1.2"}
    dlg.reject()
    dlg = ChangeVersionDialog(services, _game(platform="linux", build_id=""), installer_info("1.2", "up_to_date"))
    assert dlg.ok_button.isHidden() and dlg.cancel_button.text() == "Close" and dlg.choice is None
    dlg.reject()

    # signed out: nothing to choose, nothing to retry
    calls = []

    def fake_info(game, fetch=True, refresh=False):
        calls.append((game.uid, fetch, refresh))
        return answers.pop(0)

    answers = [builds_info("b1", "behind", error="signed_out", stale=True)]
    monkeypatch.setattr(gog, "version_info", fake_info)
    dlg = ChangeVersionDialog(services, _game(), answers[0])  # unusable info: fetched again
    assert not dlg.loading.isHidden()
    pump(qapp, 2, until=lambda: dlg.info is not None)
    assert calls == [("gog:123", True, False)]
    assert dlg.message.text() == "Sign in to GOG to see the available versions."
    assert dlg.retry.isHidden() and dlg.tree.isHidden() and not dlg.ok_button.isEnabled()
    dlg.reject()

    # an error shows Try again, which refreshes
    calls.clear()
    answers[:] = [builds_info("b1", "behind", error="offline", stale=True, error_text="Couldn't reach GOG."),
                  builds_info("b1", "behind")]
    dlg = ChangeVersionDialog(services, _game())
    pump(qapp, 2, until=lambda: dlg.info is not None)
    assert dlg.tree.isHidden() and not dlg.retry.isHidden() and not dlg.ok_button.isEnabled()
    assert dlg.message.text() == "Couldn't load versions from GOG. Couldn't reach GOG."
    assert dlg.loading.isHidden() and dlg.spinner.isHidden()
    dlg.retry.click()
    assert dlg.retry.isHidden() and not dlg.loading.isHidden()
    pump(qapp, 2, until=lambda: not dlg.tree.isHidden())
    assert calls == [("gog:123", True, False), ("gog:123", True, True)]
    assert not dlg.tree.isHidden() and dlg.message.isHidden() and len(_rows(dlg)) == 2
    dlg.reject()

    # an exception from the worker
    monkeypatch.setattr(gog, "version_info", lambda *a: (_ for _ in ()).throw(OSError("boom")))
    dlg = ChangeVersionDialog(services, _game())
    pump(qapp, 2, until=lambda: not dlg.retry.isHidden())
    assert dlg.message.text() == "Couldn't load versions from GOG. boom"
    dlg.reject()

    # a result arriving after the dialog closed is ignored
    gate = threading.Event()

    def slow_info(game, fetch=True, refresh=False):
        gate.wait(5)
        return builds_info("b1", "behind")

    monkeypatch.setattr(gog, "version_info", slow_info)
    dlg = ChangeVersionDialog(services, _game())
    dlg.reject()
    gate.set()
    pump(qapp, 0.3)
    assert dlg.info is None and dlg.tree.isHidden()
    assert not errors, errors
