"""Races between a running version check and the user / a finishing job (found in review)."""
import copy
import sys
import threading

import pytest
from test_ui import B1, B2, _builds_info, _fake_version_info, _install_gog, _version_window, pump


@pytest.fixture()
def errors(monkeypatch):
    caught = []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: caught.append((t, v)))
    return caught


def _gated_check(services, monkeypatch):
    """Make gog.version_info read the pin at its start (like the real one) and block on a gate (the HTTP call)."""
    gog = services.integrations["gog"]
    monkeypatch.setattr(gog, "connected", lambda: True)
    monkeypatch.setattr(gog, "enabled", lambda: True)
    gate, started = threading.Event(), threading.Event()
    real_cfg = services.db.get_config

    def fake(game, fetch=True, refresh=False):
        info = _builds_info(B1, B2, "behind")
        info.pinned = bool(real_cfg(game.uid).get("pin_version"))
        started.set()
        gate.wait(5)
        return info
    monkeypatch.setattr(gog, "version_info", fake)
    submitted = []
    monkeypatch.setattr(services.actions, "_job",
                        lambda game, kind, title, fn, *a, **k: submitted.append((game.uid, game.build_id)))
    return gog, gate, started, submitted


def test_pin_on_card_during_fetch_wins(qapp, services, errors, monkeypatch, tmp_path):
    g = _install_gog(services, tmp_path)
    w, gog = _version_window(qapp, services, monkeypatch)
    gate = threading.Event()
    _fake_version_info(monkeypatch, gog, live={"123": _builds_info(B1, B2, "behind")},
                       saved={"123": _builds_info(B1, B2, "behind", stale=True)}, gates={"123": gate})
    gp, card = w.pages["game"], w.pages["game"].version_card
    try:
        w.navigate("game", services.library.entry_for_uid(g.uid).key)
        pump(qapp, 0.2)
        card.pin_cb.click()  # "stay on this version" while the check is still running
        gate.set()
        pump(qapp, until=lambda: not card.new_spin._timer.isActive())
        pump(qapp, 0.2)
    finally:
        gate.set()
    assert services.db.get_config(g.uid).get("pin_version") is True
    assert services.library.game(g.uid).update_available is False
    assert gp.play.text() != "UPDATE"
    assert not errors, errors


def test_pin_during_update_check_blocks_auto_update(qapp, services, errors, monkeypatch, tmp_path):
    g = _install_gog(services, tmp_path)
    gog, gate, started, submitted = _gated_check(services, monkeypatch)
    services.actions.check_updates(auto_update=True)
    assert started.wait(5)
    services.actions.set_version_pin(services.library.game(g.uid), True)
    gate.set()
    pump(qapp, 1.0)
    assert services.library.game(g.uid).update_available is False
    assert submitted == [], "a game pinned during the check is never auto-updated"


def test_rollback_finishing_during_check_is_kept(qapp, services, errors, monkeypatch, tmp_path):
    g = _install_gog(services, tmp_path)
    gog, gate, started, submitted = _gated_check(services, monkeypatch)
    services.actions.check_updates(auto_update=True)
    assert started.wait(5)
    # a rollback to b0 finishes now (what _gen2 + Actions._job do)
    rec = dict(gog.install_record("123"))
    rec.update(build_id="b0", version="0.9")
    gog._set_record("123", rec)  # noqa: SLF001
    services.db.update_config(g.uid, pin_version=True)
    work = copy.deepcopy(services.library.game(g.uid))
    work.build_id, work.version, work.update_available = "b0", "0.9", False
    services.library.update_game(work)
    gate.set()
    pump(qapp, 1.0)
    lg = services.library.game(g.uid)
    assert (lg.build_id, lg.version, lg.update_available) == ("b0", "0.9", False)
    assert [x for x in services.db.load_games() if x.uid == g.uid][0].build_id == "b0"
    assert submitted == []
