"""Per-game configuration: compatibility (Proton/Wine), launch options, performance tools, saves."""
from __future__ import annotations

import shlex

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
                               QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from nebula.core import tasks
from nebula.core.utils import which, xdg_open
from nebula.runners.compat import RunnerMissing, find_umu
from nebula.ui.widgets.common import button, label

TRI = [("Default", None), ("On", True), ("Off", False)]


def tri_combo(value, default=None) -> QComboBox:
    c = QComboBox()
    for text, v in TRI:
        if v is None and default is not None:
            text = f"Default ({'on' if default else 'off'})"
        c.addItem(text, v)
    c.setCurrentIndex({None: 0, True: 1, False: 2}.get(value, 0))
    return c


class GameSettingsDialog(QDialog):
    def __init__(self, services, game, parent=None):
        super().__init__(parent)
        self.s = services
        self.game = game
        self.compat = services.compat
        self.cfg = services.db.get_config(game.uid)
        self.setWindowTitle(f"{game.title} — Settings")
        self.resize(720, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.addWidget(label(game.title, "SectionTitle"))
        integ = services.integration(game.source)
        sub = f"{integ.name}"
        if game.installed:
            sub += f"  •  {game.platform.capitalize() if game.platform else 'installed'}"
            if game.install_path:
                sub += f"  •  {game.install_path}"
        root.addWidget(label(sub, "Faint", wrap=True))
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self.delegated = game.source in ("steam", "lutris")
        if not self.delegated:
            self.tabs.addTab(self._compat_tab(), "Compatibility")
            self.tabs.addTab(self._launch_tab(), "Launch")
            self.tabs.addTab(self._perf_tab(), "Performance")
        self.tabs.addTab(self._saves_tab(), "Saves && updates")
        if not self.delegated:
            self.tabs.addTab(self._preview_tab(), "Command preview")
            self.tabs.currentChanged.connect(lambda i: self._refresh_preview() if self.tabs.tabText(i).startswith("Command") else None)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        save = button("Save", primary=True)
        save.clicked.connect(lambda: self._save())
        btns.addWidget(cancel)
        btns.addWidget(save)
        root.addLayout(btns)

    # -------------------------------------------------------------- tabs
    def _compat_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(10)
        is_windows = self.game.platform == "windows" or (not self.game.platform and "windows" in self.game.os_support)
        info = label("This is a Windows game: it runs through Proton by default." if is_windows else
                     "This is a native Linux game. Compatibility settings only apply if you force a Windows runner.",
                     "Subtle", wrap=True)
        form.addRow(info)
        self.runner = QComboBox()
        self.runner.addItem("Default (" + ("Proton" if self.s.settings.get("compat.default_runner") != "wine" else "Wine") + ")", "")
        self.runner.addItem("Proton", "proton")
        self.runner.addItem("Wine", "wine")
        self.runner.addItem("None — run natively", "native")
        self.runner.setCurrentIndex(max(0, self.runner.findData(self.cfg.get("runner", ""))))
        form.addRow("Runner", self.runner)
        self.proton = QComboBox()
        default = self.compat.default_proton()
        self.proton.addItem(f"Default ({default.name if default else 'none installed'})", "")
        for r in self.compat.protons:
            self.proton.addItem(f"{r.name}  —  {r.origin}", r.path)
        self.proton.setCurrentIndex(max(0, self.proton.findData(self.cfg.get("proton", ""))))
        form.addRow("Proton version", self.proton)
        self.wine = QComboBox()
        self.wine.addItem("Default", "")
        for r in self.compat.wines:
            self.wine.addItem(r.name, r.path)
        self.wine.setCurrentIndex(max(0, self.wine.findData(self.cfg.get("wine", ""))))
        form.addRow("Wine version", self.wine)
        umu = find_umu()
        form.addRow("umu-launcher", label(f"Found: {umu}" if umu else
                                          "Not installed — Proton runs directly (install umu in Settings › "
                                          "Compatibility for Steam Runtime + protonfixes)", "Faint", wrap=True))
        prefix_row = QHBoxLayout()
        self.prefix = QLineEdit(self.cfg.get("prefix", ""))
        self.prefix.setPlaceholderText(str(self.compat.prefix_for(self.game.uid, {})))
        pb = button("Browse…")
        pb.clicked.connect(self._browse_prefix)
        prefix_row.addWidget(self.prefix, 1)
        prefix_row.addWidget(pb)
        form.addRow("Prefix", prefix_row)
        tools = QHBoxLayout()
        for text, fn in (("winecfg", self._winecfg), ("Winetricks", self._winetricks),
                         ("Run .exe in prefix…", self._run_exe), ("Open prefix", self._open_prefix),
                         ("Kill wineserver", self._kill)):
            b = button(text)
            b.clicked.connect(fn)
            tools.addWidget(b)
        tools.addStretch(1)
        form.addRow("Tools", tools)
        reset = button("Delete prefix…", obj="Danger")
        reset.clicked.connect(self._delete_prefix)
        rrow = QHBoxLayout()
        rrow.addWidget(reset)
        rrow.addStretch(1)
        form.addRow("", rrow)
        return w

    def _launch_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(10)
        self.args = QLineEdit(self.cfg.get("args", ""))
        self.args.setPlaceholderText("-windowed -skipintro")
        form.addRow("Extra arguments", self.args)
        exe_row = QHBoxLayout()
        self.exe = QLineEdit(self.cfg.get("exe_override", ""))
        self.exe.setPlaceholderText("Use the game's default executable")
        eb = button("Browse…")
        eb.clicked.connect(self._browse_exe)
        exe_row.addWidget(self.exe, 1)
        exe_row.addWidget(eb)
        form.addRow("Executable override", exe_row)
        self.wrapper = QLineEdit(self.cfg.get("wrapper", ""))
        self.wrapper.setPlaceholderText("e.g. strace -f  or  obs-gamecapture")
        form.addRow("Wrapper command", self.wrapper)
        form.addRow(label("Environment variables", "Subtle"))
        self.env = QTableWidget(0, 2)
        self.env.setHorizontalHeaderLabels(["Variable", "Value"])
        self.env.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.env.verticalHeader().hide()
        for k, v in (self.cfg.get("env") or {}).items():
            self._add_env(k, v)
        form.addRow(self.env)
        row = QHBoxLayout()
        add = button("Add variable", "plus")
        add.clicked.connect(lambda: self._add_env("", ""))
        rem = button("Remove selected", "minus")
        rem.clicked.connect(lambda: self.env.removeRow(self.env.currentRow()) if self.env.currentRow() >= 0 else None)
        row.addWidget(add)
        row.addWidget(rem)
        row.addStretch(1)
        form.addRow(row)
        if self.game.source == "epic":
            self.via_heroic = QCheckBox("Launch through Heroic instead of Legendary + Proton")
            self.via_heroic.setChecked(bool(self.cfg.get("epic_via_heroic")))
            form.addRow(self.via_heroic)
        return w

    def _perf_tab(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(10, 16, 10, 10)
        form.setSpacing(10)
        form.addRow(label("“Default” follows Settings › Compatibility.", "Faint"))

        def tool(name, binary):
            return name + ("" if which(binary) else "  (not installed)")

        self.t_gamemode = tri_combo(self.cfg.get("gamemode"), self.s.settings.get("compat.gamemode"))
        form.addRow(tool("Feral GameMode", "gamemoderun"), self.t_gamemode)
        self.t_mangohud = tri_combo(self.cfg.get("mangohud"), self.s.settings.get("compat.mangohud"))
        form.addRow(tool("MangoHud overlay", "mangohud"), self.t_mangohud)
        self.t_gamescope = tri_combo(self.cfg.get("gamescope"), self.s.settings.get("compat.gamescope"))
        form.addRow(tool("gamescope", "gamescope"), self.t_gamescope)
        self.gs_args = QLineEdit(self.cfg.get("gamescope_args", "") or "")
        self.gs_args.setPlaceholderText(self.s.settings.get("compat.gamescope_args"))
        form.addRow("gamescope arguments", self.gs_args)
        self.t_dxvk = tri_combo(self.cfg.get("dxvk_hud"), self.s.settings.get("compat.dxvk_hud"))
        form.addRow("DXVK HUD (FPS)", self.t_dxvk)
        self.t_esync = tri_combo(self.cfg.get("esync"), self.s.settings.get("compat.esync"))
        form.addRow("Esync", self.t_esync)
        self.t_fsync = tri_combo(self.cfg.get("fsync"), self.s.settings.get("compat.fsync"))
        form.addRow("Fsync", self.t_fsync)
        self.t_wayland = tri_combo(self.cfg.get("wayland"), self.s.settings.get("compat.wayland"))
        form.addRow("Native Wayland (Proton)", self.t_wayland)
        self.t_nvapi = tri_combo(self.cfg.get("nvapi"), self.s.settings.get("compat.nvapi"))
        form.addRow("NVIDIA DLSS / NVAPI", self.t_nvapi)
        self.fps = QSpinBox()
        self.fps.setRange(0, 1000)
        self.fps.setSpecialValueText("Unlimited")
        self.fps.setValue(int(self.cfg.get("fps_limit") or 0))
        form.addRow("FPS limit (DXVK)", self.fps)
        return w

    def _saves_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 16, 10, 10)
        lay.setSpacing(10)
        integ = self.s.integration(self.game.source)
        self.cloud = QCheckBox("Synchronise cloud saves for this game")
        self.cloud.setChecked(self.cfg.get("cloud_saves", True) is not False)
        supports_cloud = integ.supports("cloud_saves")
        self.cloud.setEnabled(supports_cloud)
        lay.addWidget(self.cloud)
        if supports_cloud:
            self.locations = QListWidget()
            self.locations.setMaximumHeight(90)
            lay.addWidget(label("Save locations (resolved inside the Proton prefix):", "Faint"))
            lay.addWidget(self.locations)
            if hasattr(integ, "cloud_locations"):
                tasks.run_async(integ.cloud_locations, self.game, on_done=self._show_locations)
            row = QHBoxLayout()
            for text, mode in (("Sync now", "auto"), ("Upload local saves", "upload"),
                               ("Download cloud saves", "download")):
                b = button(text, "cloud")
                b.clicked.connect(lambda _c=False, m=mode: self._sync(m))
                row.addWidget(b)
            row.addStretch(1)
            lay.addLayout(row)
            self.sync_status = label("", "Subtle", wrap=True)
            lay.addWidget(self.sync_status)
        else:
            lay.addWidget(label(f"{integ.name} manages saves for this game itself.", "Faint"))
        self.auto_update = QCheckBox("Update automatically")
        self.auto_update.setChecked(self.cfg.get("auto_update", True) is not False)
        self.pin = QCheckBox("Keep the current version (don't offer updates)")
        self.pin.setChecked(bool(self.cfg.get("pin_version")))
        if integ.supports("update"):
            lay.addSpacing(10)
            lay.addWidget(self.auto_update)
            lay.addWidget(self.pin)
        lay.addStretch(1)
        return w

    def _preview_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(10, 16, 10, 10)
        lay.addWidget(label("The exact command Nebula will run (saved settings):", "Subtle"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        lay.addWidget(self.preview, 1)
        return w

    # ----------------------------------------------------------- helpers
    def _add_env(self, k: str, v: str) -> None:
        r = self.env.rowCount()
        self.env.insertRow(r)
        self.env.setItem(r, 0, QTableWidgetItem(k))
        self.env.setItem(r, 1, QTableWidgetItem(v))

    def _show_locations(self, locs) -> None:
        self.locations.clear()
        for name, path in locs or []:
            self.locations.addItem(f"{name}:  {path}")
        if not locs:
            self.locations.addItem("No cloud save locations (game not installed for Windows, or no cloud support)")

    def _sync(self, mode: str) -> None:
        integ = self.s.integration(self.game.source)
        self.sync_status.setText("Synchronising…")
        tasks.run_async(integ.cloud_sync, self.game, mode, "manual",
                        on_done=lambda r: self.sync_status.setText(r.message),
                        on_error=lambda e: self.sync_status.setText(f"Failed: {e}"))

    def _browse_prefix(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Prefix folder", self.prefix.text())
        if d:
            self.prefix.setText(d)

    def _browse_exe(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Executable", self.game.install_path)
        if f:
            self.exe.setText(f)

    def _guard(self, fn) -> None:
        try:
            fn()
        except RunnerMissing as exc:
            QMessageBox.warning(self, "No runner", str(exc))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Error", str(exc))

    def _winecfg(self) -> None:
        self._save(close=False)
        self._guard(lambda: self.compat.winecfg(self.game.uid))

    def _winetricks(self) -> None:
        self._save(close=False)
        self._guard(lambda: self.compat.winetricks(self.game.uid))

    def _run_exe(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Run in prefix", self.game.install_path, "Windows programs (*.exe *.msi *.bat);;All files (*)")
        if f:
            self._save(close=False)
            self._guard(lambda: self.compat.run_in_prefix(self.game.uid, [f], wait=False))

    def _open_prefix(self) -> None:
        p = self.compat.prefix_for(self.game.uid)
        p.mkdir(parents=True, exist_ok=True)
        xdg_open(str(p))

    def _kill(self) -> None:
        tasks.run_async(self.compat.kill_prefix, self.game.uid)

    def _delete_prefix(self) -> None:
        if QMessageBox.question(self, "Delete prefix",
                                "Delete this game's Proton/Wine prefix? Save games stored inside it will be lost "
                                "(cloud saves are kept on GOG).") == QMessageBox.StandardButton.Yes:
            self._guard(lambda: self.compat.delete_prefix(self.game.uid))

    def _refresh_preview(self) -> None:
        self._save(close=False)
        integ = self.s.integration(self.game.source)

        def build():
            spec = integ.prepare_launch(self.game)
            if spec.uri:
                return f"xdg-open {spec.uri}"
            env = "\n".join(f"{k}={shlex.quote(v)}" for k, v in sorted(spec.env.items()))
            return f"# {spec.description}\n# working directory: {spec.cwd}\n\n{env}\n\n{shlex.join(spec.argv)}"

        self.preview.setPlainText("Building…")
        tasks.run_async(build, on_done=self.preview.setPlainText,
                        on_error=lambda e: self.preview.setPlainText(f"Cannot build the command:\n{e}"))

    def _collect(self) -> dict:
        cfg = dict(self.cfg)
        if not self.delegated:
            cfg["runner"] = self.runner.currentData() or ""
            cfg["proton"] = self.proton.currentData() or ""
            cfg["wine"] = self.wine.currentData() or ""
            cfg["prefix"] = self.prefix.text().strip()
            cfg["args"] = self.args.text().strip()
            cfg["exe_override"] = self.exe.text().strip()
            cfg["wrapper"] = self.wrapper.text().strip()
            env = {}
            for r in range(self.env.rowCount()):
                k = (self.env.item(r, 0).text() if self.env.item(r, 0) else "").strip()
                v = self.env.item(r, 1).text() if self.env.item(r, 1) else ""
                if k:
                    env[k] = v
            cfg["env"] = env
            for key, combo in (("gamemode", self.t_gamemode), ("mangohud", self.t_mangohud),
                               ("gamescope", self.t_gamescope), ("dxvk_hud", self.t_dxvk), ("esync", self.t_esync),
                               ("fsync", self.t_fsync), ("wayland", self.t_wayland), ("nvapi", self.t_nvapi)):
                val = combo.currentData()
                if val is None:
                    cfg.pop(key, None)
                else:
                    cfg[key] = val
            cfg["gamescope_args"] = self.gs_args.text().strip()
            cfg["fps_limit"] = self.fps.value() or None
            if hasattr(self, "via_heroic"):
                cfg["epic_via_heroic"] = self.via_heroic.isChecked()
        cfg["cloud_saves"] = self.cloud.isChecked()
        cfg["auto_update"] = self.auto_update.isChecked()
        cfg["pin_version"] = self.pin.isChecked()
        return cfg

    def _save(self, close: bool = True) -> None:
        cfg = self._collect()
        self.s.db.set_config(self.game.uid, cfg)
        self.cfg = cfg
        if close:
            self.accept()

