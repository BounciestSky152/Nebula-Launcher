"""Proton / Wine command building."""
import os
import stat

import pytest

from nebula import paths
from nebula.runners import compat


def fake_proton(name: str, root=None):
    d = (root or paths.proton_dir()) / name
    (d / "files" / "bin").mkdir(parents=True)
    (d / "proton").write_text("#!/bin/sh\n")
    (d / "files" / "bin" / "wine").write_text("")
    (d / "files" / "bin" / "wineserver").write_text("")
    (d / "version").write_text(f"1700000000 {name}\n")
    return d


def fake_bin(tmp_path, name):
    b = tmp_path / "bin" / name
    b.parent.mkdir(exist_ok=True)
    b.write_text("#!/bin/sh\n")
    b.chmod(b.stat().st_mode | stat.S_IEXEC)
    return b


@pytest.fixture()
def layer(services):
    return services.compat


def test_ranking_prefers_newest_ge(layer):
    fake_proton("GE-Proton9-27")
    fake_proton("GE-Proton10-4")
    fake_proton("Proton 9.0")
    layer.refresh()
    names = [r.name for r in layer.protons]
    assert names[0] == "GE-Proton10-4" and names.index("GE-Proton9-27") < names.index("Proton 9.0")
    assert layer.default_proton().name == "GE-Proton10-4"


def test_windows_game_uses_proton_by_default(layer, services, monkeypatch):
    fake_proton("GE-Proton10-4")
    layer.refresh()
    monkeypatch.setattr(compat, "find_umu", lambda: None)
    monkeypatch.setattr(compat, "which", lambda *n: None)
    spec = layer.build("local:x", "/games/x/game.exe", ["-a"], cwd="/games/x", platform="windows",
                       install_dir="/games/x")
    assert spec.argv[0].endswith("GE-Proton10-4/proton") and spec.argv[1:] == ["waitforexitandrun", "/games/x/game.exe", "-a"]
    prefix = paths.prefixes_dir() / "local_x"
    assert spec.env["STEAM_COMPAT_DATA_PATH"] == str(prefix) and spec.env["STEAM_COMPAT_INSTALL_PATH"] == "/games/x"
    assert os.path.isdir(spec.env["STEAM_COMPAT_CLIENT_INSTALL_PATH"])
    assert os.readlink(prefix / "pfx") == "."
    assert spec.runner.name == "GE-Proton10-4"


def test_umu_and_tuning(layer, services, monkeypatch, tmp_path):
    fake_proton("GE-Proton10-4")
    layer.refresh()
    umu = fake_bin(tmp_path, "umu-run")
    monkeypatch.setattr(compat, "find_umu", lambda: str(umu))
    for tool in ("gamemoderun", "mangohud", "gamescope"):
        fake_bin(tmp_path, tool)
    monkeypatch.setenv("PATH", str(tmp_path / "bin") + os.pathsep + os.environ["PATH"])
    services.settings.set("compat.mangohud", True)
    services.settings.set("compat.gamescope", True)
    services.settings.set("compat.gamescope_args", "-W 1920 -H 1080")
    services.db.set_config("gog:5", {"fsync": False, "dxvk_hud": True, "env": {"FOO": "bar"}, "fps_limit": 60,
                                     "args": "--fast"})
    spec = layer.build("gog:5", "/g/game.exe", [], platform="windows")
    assert spec.argv[:6] == ["gamescope", "-W", "1920", "-H", "1080", "--"]
    assert spec.argv[6:9] == ["gamemoderun", "mangohud", str(umu)]
    assert spec.argv[9:] == ["/g/game.exe", "--fast"]
    assert spec.env["PROTONPATH"].endswith("GE-Proton10-4")
    assert spec.env["PROTON_NO_FSYNC"] == "1" and "PROTON_NO_ESYNC" not in spec.env
    assert spec.env["DXVK_HUD"] == "fps" and spec.env["DXVK_FRAME_RATE"] == "60" and spec.env["FOO"] == "bar"


def test_runner_overrides_and_missing(layer, services, monkeypatch):
    monkeypatch.setattr(compat, "which", lambda *n: None)
    layer.refresh()
    if not layer.protons and not layer.wines:
        with pytest.raises(compat.RunnerMissing):
            layer.build("gog:9", "/g/game.exe", [], platform="windows")
    services.db.set_config("gog:9", {"runner": "native"})
    spec = layer.build("gog:9", "/g/start.sh", [], platform="windows")
    assert spec.description == "Native" and spec.argv[-1] == "/g/start.sh"
    spec = layer.build("gog:10", "/g/game", ["x"], platform="linux")
    assert spec.argv == ["/g/game", "x"]


def test_native_dosbox_substitution(services, monkeypatch, tmp_path):
    monkeypatch.setattr(compat, "which", lambda *names: "/usr/bin/dosbox" if "dosbox" in names else None)
    out = compat.native_substitute("C:/game/DOSBOX/DOSBox.exe", ["-conf", "..\\dosbox_game.conf", "-noconsole"],
                                   str(tmp_path), services.settings)
    assert out[0] == ["/usr/bin/dosbox", "-conf", "../dosbox_game.conf", "-noconsole"]
    assert compat.native_substitute("C:/game/game.exe", [], str(tmp_path), services.settings) is None


def test_steam_library_detection(tmp_path, monkeypatch):
    root = tmp_path / ".steam" / "steam"
    (root / "steamapps" / "common" / "Proton 9.0").mkdir(parents=True)
    (root / "steamapps" / "common" / "Proton 9.0" / "proton").write_text("")
    lib2 = tmp_path / "games2"
    (lib2 / "steamapps" / "common" / "Proton - Experimental").mkdir(parents=True)
    (lib2 / "steamapps" / "common" / "Proton - Experimental" / "proton").write_text("")
    (root / "steamapps" / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0"\n {\n  "path" "%s"\n }\n "1"\n {\n  "path" "%s"\n }\n}\n' % (root, lib2))
    monkeypatch.setattr(compat.Path, "home", staticmethod(lambda: tmp_path))
    assert compat.steam_roots() == [root.resolve()]
    names = sorted(r.name for r in compat.detect_protons() if r.origin == "steam")
    assert names == ["Proton - Experimental (Steam)", "Proton 9.0 (Steam)"]
