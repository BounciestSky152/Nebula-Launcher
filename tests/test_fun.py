"""Fun extras: sky effects, the Konami code, secret search words and milestones."""
import time


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.01)


def test_sky_effects_start_and_end(qapp):
    import datetime

    from nebula.ui.widgets.starfield import FPS, Starfield, season_for

    sky = Starfield()
    sky.resize(400, 300)
    sky.show()
    pump(qapp, 0.05)
    for start in (sky.warp, sky.fireworks, lambda: sky.snow(2), sky.party):
        start()
        assert sky._effects and sky.timer.interval() < 1000 // FPS  # faster only while it runs
        for _ in range(400):
            sky._tick()
        sky.grab()
        assert not [fx for fx in sky._effects if fx.name in ("warp", "fireworks", "snow", "party")]
    sky.set_fun(False)
    sky.warp()
    assert not sky._effects  # fun extras off: no effects
    assert season_for(datetime.date(2026, 10, 31)) == "halloween"
    assert season_for(datetime.date(2026, 12, 10)) == "winter"
    assert season_for(datetime.date(2027, 1, 1)) == "newyear"
    assert season_for(datetime.date(2026, 7, 1)) is None


def test_konami_words_and_milestones(qapp, services, monkeypatch):
    from PySide6.QtCore import Qt

    from nebula.ui import theme
    from nebula.ui.handheld import send_key
    from nebula.ui.main_window import MainWindow

    theme.apply(qapp)
    w = MainWindow(services)
    w.show()
    calls = []
    for name in ("warp", "party", "fireworks", "snow"):
        monkeypatch.setattr(w.starfield, name, lambda *a, n=name: calls.append(n))
    w.topbar.search.setFocus()
    pump(qapp, 0.05)
    for k in (Qt.Key.Key_Up, Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Down, Qt.Key.Key_Left, Qt.Key.Key_Right,
              Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_B, Qt.Key.Key_A):
        send_key(k)
        pump(qapp, 0.02)
    pump(qapp, 1.6)
    assert calls[:2] == ["warp", "party"]
    calls.clear()
    w.topbar.search.setText("Let it snow")
    w.fun._search()
    assert calls == ["snow"]
    services.settings.set("appearance.fun", False)
    calls.clear()
    w.topbar.search.setText("warp")
    w.fun._search()
    assert calls == []
    services.settings.set("appearance.fun", True)
    seen = []
    monkeypatch.setattr(w, "celebrate", lambda title, body="": seen.append(title))
    e = next(iter(services.library.all_entries()), None)
    if e is not None:
        uid = e.games[0].uid
        w.fun._played[uid] = 9 * 60
        monkeypatch.setattr(type(e), "playtime_minutes", 11 * 60, raising=False)
        w.fun._stopped(uid, 7200)
        assert seen and seen[0].startswith("10 hours")
    w.close()
