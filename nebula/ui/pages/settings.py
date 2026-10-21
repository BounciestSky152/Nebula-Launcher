"""Settings page (General, Library, Downloads, Compatibility, Cloud saves, Integrations, About)."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QScrollArea, QSpinBox, QTabWidget,
                               QVBoxLayout, QWidget)

from nebula import __version__, paths
from nebula.core import tasks
from nebula.core.images import images
from nebula.core.utils import human_size, which, xdg_open
from nebula.runners import manager as runner_manager
from nebula.runners.compat import find_umu
from nebula.ui import icons, theme
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import button, card, clear_layout, icon_button, label

LANGUAGES = [("en-US", "English"), ("de-DE", "Deutsch"), ("fr-FR", "Français"), ("es-ES", "Español"),
             ("it-IT", "Italiano"), ("pl-PL", "Polski"), ("pt-BR", "Português (Brasil)"),
             ("ru-RU", "Русский"), ("zh-Hans", "中文"),
             ("ja-JP", "日本語"), ("ko-KR", "한국어"), ("tr-TR", "Türkçe"),
             ("cs-CZ", "Čeština"), ("hu-HU", "Magyar"), ("nl-NL", "Nederlands")]


class SettingsPage(Page):
    name = "Settings"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.st = services.settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 22, 32, 0)
        lay.addWidget(label("Settings", "PageTitle"))
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(self._scroll(self._general()), "General")
        self.tabs.addTab(self._scroll(self._library()), "Library")
        self.tabs.addTab(self._scroll(self._downloads()), "Downloads")
        self.tabs.addTab(self._scroll(self._compat()), "Compatibility (Proton)")
        self.tabs.addTab(self._scroll(self._cloud()), "Cloud saves")
        self.integrations_w = QWidget()
        self.tabs.addTab(self._scroll(self.integrations_w), "Integrations")
        self._build_integrations()
        self.tabs.addTab(self._scroll(self._updates()), "Updates")
        self.tabs.addTab(self._scroll(self._about()), "About")
        services.bus.integration_changed.connect(lambda _i: (self._build_integrations(), self._refresh_gog_account()))
        services.bus.runners_changed.connect(self._refresh_runners)
        services.bus.sync_state.connect(lambda *_: self._build_integrations() if self.isVisible() else None)
        # handheld mode drives this page as a form (see nebula.ui.handheld.Navigation); the footer shows how
        self.setProperty("controllerForm", True)
        from nebula.ui.pages.handheld import Prompts

        self.prompts = Prompts()
        lay.addWidget(self.prompts)
        self.set_controller("xbox")
        self.set_handheld(theme.HANDHELD)

    def set_controller(self, kind: str) -> None:
        from nebula.ui.pages.handheld import PROMPTS

        items = PROMPTS.get(kind, PROMPTS["xbox"])
        sh = ("L1", "R1") if kind == "playstation" else ("LB", "RB")
        self.prompts.set_items(((sh[0], "#8c84b8", ""), (sh[1], "#8c84b8", "Section"), ("◀▶", "#8c84b8", "Change"),
                                items[0], items[2]))

    def set_handheld(self, on: bool) -> None:
        self.handheld_combo.blockSignals(True)  # F11 switches the mode without this page
        self.handheld_combo.setCurrentIndex(max(0, self.handheld_combo.findData("on" if on else "off")))
        self.handheld_combo.blockSignals(False)
        self.prompts.setVisible(on)
        self.layout().setContentsMargins(32, 22, 32, 14 if on else 0)

    def on_show(self, arg=None) -> None:
        if isinstance(arg, str):
            for i in range(self.tabs.count()):
                if self.tabs.tabText(i).lower().startswith(arg.lower()):
                    self.tabs.setCurrentIndex(i)
        self._build_integrations()
        self._refresh_runners()
        if theme.HANDHELD:
            QTimer.singleShot(0, self.tabs.tabBar().setFocus)  # a pad starts on the section tabs

    # ------------------------------------------------------------ helpers
    def _scroll(self, w: QWidget) -> QScrollArea:
        a = QScrollArea()
        a.setWidgetResizable(True)
        a.setWidget(w)
        w.setObjectName("ContentArea")
        return a

    def _page(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(4, 18, 18, 18)
        lay.setSpacing(14)
        return w, lay

    def check(self, key: str, text: str, tip: str = "") -> QCheckBox:
        c = QCheckBox(text)
        c.setChecked(bool(self.st.get(key)))
        c.toggled.connect(lambda v: self.st.set(key, v))
        if tip:
            c.setToolTip(tip)
        return c

    def font_combo(self, key: str) -> QComboBox:
        """Any installed font (the bundled Audiowide and Orbitron included), or the system font."""
        from PySide6.QtGui import QFontDatabase

        c = QComboBox()
        c.setMaximumWidth(260)
        c.addItem("System default", "")
        for fam in QFontDatabase.families():
            c.addItem(fam, fam)
        c.setCurrentIndex(max(0, c.findData(self.st.get(key) or "")))
        c.currentIndexChanged.connect(lambda _i: self.st.set(key, c.currentData()))
        return c

    def spin(self, key: str, lo: int, hi: int, suffix: str = "", special: str = "") -> QSpinBox:
        s = QSpinBox()
        s.setMaximumWidth(180)
        s.setRange(lo, hi)
        s.setValue(int(self.st.get(key) or 0))
        if suffix:
            s.setSuffix(suffix)
        if special:
            s.setSpecialValueText(special)
        s.valueChanged.connect(lambda v: self.st.set(key, v))
        return s

    def combo(self, key: str, options: list[tuple]) -> QComboBox:
        c = QComboBox()
        for val, text in options:
            c.addItem(text, val)
        c.setCurrentIndex(max(0, c.findData(self.st.get(key))))
        c.currentIndexChanged.connect(lambda _i: self.st.set(key, c.currentData()))
        return c

    def group(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        g = QGroupBox(title)
        f = QFormLayout(g)
        f.setSpacing(10)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return g, f

    # ------------------------------------------------------------ general
    def _general(self) -> QWidget:
        w, lay = self._page()
        g3, f3 = self.group("GOG account")
        self.gog_status = label("", "Subtle", wrap=True)
        self.gog_btn = button("")
        self.gog_btn.clicked.connect(self._gog_account_action)
        row = QHBoxLayout()
        row.addWidget(self.gog_status, 1)
        row.addWidget(self.gog_btn)
        f3.addRow(row)
        f3.addRow(self.check("gog.presence", "Show my online status and current game to GOG friends"))
        f3.addRow(self.check("gog.sync_playtime", "Report play time to GOG (shown on your GOG profile)"))
        f3.addRow("Store country", self.combo("gog.country", [(c, c) for c in (
            "US", "GB", "DE", "FR", "PL", "ES", "IT", "NL", "SE", "CA", "AU", "BR", "JP", "RU", "CN")]))
        f3.addRow("Store currency", self.combo("gog.currency", [(c, c) for c in (
            "USD", "EUR", "GBP", "PLN", "CAD", "AUD", "BRL", "JPY", "RUB", "CNY", "SEK")]))
        lay.addWidget(g3)
        self._refresh_gog_account()
        ga, fa = self.group("Appearance")
        fa.addRow(self.check("appearance.starfield", "Starry sky background"))
        fa.addRow(self.check("appearance.animate_stars", "Animate the stars (twinkling and shooting stars)"))
        fa.addRow(label("The animation pauses automatically while a game is running.", "Faint"))
        fa.addRow(self.check("appearance.fun", "Fun extras: launch warp, seasonal skies and a few secrets"))
        th = QComboBox()
        th.setMaximumWidth(260)
        th.addItem("Nebula", "nebula")
        th.addItem("Super Nova (light)", "supernova")
        th.addItem("Classic (the original look)", "classic")
        th.setCurrentIndex(max(0, th.findData(self.st.get("appearance.theme") or "nebula")))
        th.currentIndexChanged.connect(lambda _i: self.st.set("appearance.theme", th.currentData()))
        fa.addRow("Theme", th)
        from nebula.core.settings import DEFAULTS

        for key, name in (("appearance.font_ui", "Interface font"), ("appearance.font_display", "Titles & logo font"),
                          ("appearance.font_sidebar", "Sidebar font")):
            combo = self.font_combo(key)
            reset = icon_button("refresh", "Reset this font to its default")
            # each font resets on its own (the interface font's default is the system font)
            reset.clicked.connect(lambda _c=False, c=combo, k=key: c.setCurrentIndex(
                max(0, c.findData(DEFAULTS.get(k) or ""))))
            row = QHBoxLayout()
            row.addWidget(combo)
            row.addWidget(reset)
            row.addStretch(1)
            fa.addRow(name, row)
        lay.addWidget(ga)
        g, f = self.group("Application")
        f.addRow(self.check("general.show_tray", "Show icon in the system tray"))
        f.addRow(self.check("general.close_to_tray", "Closing the window keeps Super Nova running in the tray"))
        f.addRow(self.check("general.autostart", "Start Super Nova when I log in"))
        f.addRow(self.check("general.start_minimized", "Start minimised"))
        self.handheld_combo = self.combo("handheld.mode", [
            ("off", "Off"), ("on", "On (controller-first, fullscreen)")])
        f.addRow("Handheld mode", self.handheld_combo)
        f.addRow(self.check("general.notifications", "Desktop notifications"))
        f.addRow("When a game starts", self.combo("general.launch_behaviour", [
            ("keep", "Keep Super Nova open"), ("minimize", "Minimise Super Nova"), ("hide", "Hide to tray")]))
        f.addRow("Game language", self.combo("general.language", LANGUAGES))
        lay.addWidget(g)
        g2, f2 = self.group("Friends")
        f2.addRow("Refresh friends every", self.spin("friends.poll_minutes", 1, 60, " min"))
        f2.addRow(self.check("friends.notify_playing", "Notify me when a friend starts playing"))
        lay.addWidget(g2)
        lay.addStretch(1)
        return w

    def _refresh_gog_account(self) -> None:
        gog = self.s.integrations.get("gog")
        if gog and gog.connected():
            self.gog_status.setText(f"Signed in as <b>{gog.account_name() or 'GOG user'}</b>")
            self.gog_btn.setText("Sign out")
        else:
            self.gog_status.setText("Not signed in. Sign in to sync your GOG library, cloud saves, achievements "
                                    "and friends.")
            self.gog_btn.setText("Sign in with GOG")

    def _gog_account_action(self) -> None:
        gog = self.s.integrations.get("gog")
        if gog and gog.connected():
            self.ctl.gog_logout()
        else:
            self.ctl.gog_login()

    # ------------------------------------------------------------ updates
    def _updates(self) -> QWidget:
        from nebula import __version__

        w, lay = self._page()
        g, f = self.group("Status")
        self.upd_version = label("", selectable=True)
        f.addRow("Version", self.upd_version)
        self.upd_mode = label("", "Subtle", wrap=True)
        f.addRow("Install type", self.upd_mode)
        self.upd_status = label("", wrap=True)
        f.addRow("Status", self.upd_status)
        check = button("Check for updates now", "refresh", primary=True)
        check.clicked.connect(lambda: self.s.self_update.check(manual=True))
        r = QHBoxLayout()
        r.addWidget(check)
        r.addStretch(1)
        f.addRow("", r)
        lay.addWidget(g)
        g2, f2 = self.group("Automatic updates")
        f2.addRow(self.check("updates.auto_check", "Check for new versions"))
        f2.addRow("Check every", self.spin("updates.interval_minutes", 5, 1440, " min"))
        f2.addRow(self.check("updates.auto_install", "Install updates automatically"))
        f2.addRow(self.check("updates.auto_restart", "Restart after updating (only when no game is running)"))
        repo = QLineEdit(self.st.get("updates.repo"))
        repo.editingFinished.connect(lambda: self.st.set("updates.repo", repo.text().strip()))
        f2.addRow("GitHub repository", repo)
        branch = QLineEdit(self.st.get("updates.branch"))
        branch.editingFinished.connect(lambda: self.st.set("updates.branch", branch.text().strip() or "main"))
        f2.addRow("Branch", branch)
        token = QLineEdit(self.st.get("updates.github_token"))
        token.setEchoMode(QLineEdit.EchoMode.Password)
        token.setPlaceholderText("Only for a private repository (uses `gh auth token` if empty)")
        token.editingFinished.connect(lambda: self.st.set("updates.github_token", token.text().strip()))
        f2.addRow("GitHub token", token)
        lay.addWidget(g2)
        g3 = QGroupBox("Last update")
        v3 = QVBoxLayout(g3)
        self.upd_last = QListWidget()
        self.upd_last.setMaximumHeight(160)
        v3.addWidget(self.upd_last)
        lay.addWidget(g3)
        lay.addStretch(1)
        self._version = __version__
        self.s.self_update.changed.connect(self._refresh_updates)
        self._refresh_updates()
        return w

    def _refresh_updates(self) -> None:
        upd = self.s.self_update
        sha = upd.installed_sha()
        self.upd_version.setText(f"Super Nova {self._version}" + (f"  \u2022  commit {sha[:7]}" if sha else ""))
        self.upd_mode.setText({
            "git": "Git checkout \u2014 updates are fast-forward pulls",
            "pip": "Installed with scripts/install.sh \u2014 updates reinstall the new source into Super Nova's venv",
            "files": "Source folder \u2014 changed files are replaced in place",
            "manual": "Read-only installation \u2014 you'll be told when an update exists",
        }.get(upd.mode(), upd.mode()))
        state = upd.state()
        checked = state.get("last_check")
        from nebula.core.utils import relative_time

        text = upd.message or "Not checked yet this session"
        if checked and upd.status in ("idle", "up_to_date"):
            text += f"  (last check: {relative_time(checked)})"
        self.upd_status.setText(text)
        self.upd_last.clear()
        last = state.get("last_update") or {}
        for _sha, msg in last.get("commits", []):
            self.upd_last.addItem(f"\u2022 {msg}")
        for f in last.get("files", [])[:30]:
            self.upd_last.addItem(f"    {f}")
        if not last:
            self.upd_last.addItem("No updates installed yet.")

    # ------------------------------------------------------------ library
    def _library(self) -> QWidget:
        w, lay = self._page()
        g, f = self.group("Installation")
        row = QHBoxLayout()
        self.install_dir = QLineEdit(self.st.get("library.install_dir"))
        self.install_dir.editingFinished.connect(lambda: self.st.set("library.install_dir", self.install_dir.text()))
        b = button("Browse…")
        b.clicked.connect(self._browse_install_dir)
        row.addWidget(self.install_dir, 1)
        row.addWidget(b)
        f.addRow("Install games to", row)
        scan = button("Scan a folder for installed games…", "folder")
        scan.clicked.connect(self.ctl.scan_folder)
        f.addRow("", scan)
        lay.addWidget(g)
        g2, f2 = self.group("Play time")
        f2.addRow(self.check("library.detect_external_sessions",
                             "Track play time for games started outside Super Nova (process detection)"))
        lay.addWidget(g2)
        g3, f3 = self.group("List view columns")
        cols = self.st.get("library.list_columns")
        from nebula.ui.widgets.gamegrid import COLUMNS

        for key, text in COLUMNS[1:]:
            c = QCheckBox(text)
            c.setChecked(key in cols)
            c.toggled.connect(lambda on, k=key: self._toggle_column(k, on))
            f3.addRow(c)
        lay.addWidget(g3)
        g4, f4 = self.group("Maintenance")
        resync = button("Re-sync all platforms", "refresh")
        resync.clicked.connect(lambda: self.s.library.refresh())
        f4.addRow("", resync)
        self.cache_lbl = label("", "Faint")
        clear = button("Clear image cache", "trash")
        clear.clicked.connect(self._clear_cache)
        r2 = QHBoxLayout()
        r2.addWidget(clear)
        r2.addWidget(self.cache_lbl)
        r2.addStretch(1)
        f4.addRow("", r2)
        tasks.run_async(images().disk_usage, on_done=lambda n: self.cache_lbl.setText(human_size(n)))
        lay.addWidget(g4)
        lay.addStretch(1)
        return w

    def _browse_install_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Install games to", self.install_dir.text())
        if d:
            self.install_dir.setText(d)
            self.st.set("library.install_dir", d)

    def _toggle_column(self, key: str, on: bool) -> None:
        cols = self.st.get("library.list_columns")
        if on and key not in cols:
            cols.append(key)
        elif not on and key in cols:
            cols.remove(key)
        self.st.set("library.list_columns", cols)
        lib = self.win.pages.get("library")
        if lib:
            lib.table.set_visible_columns(cols)

    def _clear_cache(self) -> None:
        tasks.run_async(images().clear, on_done=lambda _r: self.cache_lbl.setText("0 B"))

    # ---------------------------------------------------------- downloads
    def _downloads(self) -> QWidget:
        w, lay = self._page()
        g, f = self.group("Downloads")
        f.addRow("Speed limit", self.spin("downloads.speed_limit_kbps", 0, 1_000_000, " KB/s", "Unlimited"))
        f.addRow("Parallel connections", self.spin("downloads.workers", 1, 32))
        f.addRow("Simultaneous installs", self.spin("downloads.concurrent_jobs", 1, 4))
        f.addRow(self.check("downloads.prefer_native", "Prefer native Linux builds when a game offers one"))
        f.addRow(self.check("downloads.install_dependencies",
                            "Install required redistributables (MSVC, DirectX, .NET) into the Proton prefix"))
        f.addRow(self.check("downloads.keep_installers", "Keep downloaded offline installers"))
        lay.addWidget(g)
        g2, f2 = self.group("Updates")
        f2.addRow(self.check("downloads.check_updates_on_start", "Check for game updates on start-up"))
        f2.addRow(self.check("downloads.auto_update", "Install game updates automatically"))
        now = button("Check for updates now", "refresh")
        now.clicked.connect(lambda: self.s.actions.check_updates(on_done=lambda found: self.win.toast(
            "Updates", f"{len(found)} update(s) found" if found else "All games are up to date")))
        f2.addRow("", now)
        lay.addWidget(g2)
        lay.addStretch(1)
        return w

    # ------------------------------------------------------- compatibility
    def _compat(self) -> QWidget:
        w, lay = self._page()
        g, f = self.group("Default runner")
        f.addRow("Windows games use", self.combo("compat.default_runner", [
            ("proton", "Proton (recommended)"), ("wine", "Wine")]))
        self.def_proton = QComboBox()
        self.def_proton.currentIndexChanged.connect(
            lambda _i: self.st.set("compat.default_proton", self.def_proton.currentData() or ""))
        f.addRow("Proton version", self.def_proton)
        self.def_wine = QComboBox()
        self.def_wine.currentIndexChanged.connect(
            lambda _i: self.st.set("compat.default_wine", self.def_wine.currentData() or ""))
        f.addRow("Wine version", self.def_wine)
        lay.addWidget(g)
        g2 = QGroupBox("Installed Proton && Wine builds")
        v2 = QVBoxLayout(g2)
        self.runner_list = QListWidget()
        self.runner_list.setMinimumHeight(150)
        v2.addWidget(self.runner_list)
        r = QHBoxLayout()
        dl = button("Download GE-Proton…", "download", primary=True)
        dl.clicked.connect(self.ctl.proton_download)
        rm = button("Remove selected", "trash")
        rm.clicked.connect(self._remove_runner)
        ref = button("Rescan", "refresh")
        ref.clicked.connect(lambda: (self.s.compat.refresh(), self._refresh_runners()))
        op = button("Open folder", "folder")
        op.clicked.connect(lambda: xdg_open(str(paths.proton_dir())))
        for b in (dl, rm, ref, op):
            r.addWidget(b)
        r.addStretch(1)
        v2.addLayout(r)
        lay.addWidget(g2)
        g3, f3 = self.group("umu-launcher (Steam Linux Runtime)")
        self.umu_status = label("", wrap=True)
        f3.addRow("Status", self.umu_status)
        self.umu_btn = button("Install umu-launcher", "download")
        self.umu_btn.clicked.connect(lambda: (self.s.actions.install_umu(), self.win.toast(
            "umu-launcher", "Downloading — see Downloads")))
        f3.addRow("", self.umu_btn)
        f3.addRow(self.check("compat.use_umu", "Use umu-launcher for Proton when it is available"))
        lay.addWidget(g3)
        g4, f4 = self.group("Defaults for every game (override per game in Game settings)")
        f4.addRow(self.check("compat.esync", "Esync"))
        f4.addRow(self.check("compat.fsync", "Fsync (needs a futex2-capable kernel)"))
        f4.addRow(self.check("compat.dxvk_hud", "Show the DXVK FPS counter"))
        f4.addRow(self.check("compat.wayland", "Native Wayland driver (GE-Proton 9+)"))
        f4.addRow(self.check("compat.nvapi", "Enable NVAPI / DLSS on NVIDIA GPUs"))
        f4.addRow(self.check("compat.gamemode", "Run games with Feral GameMode when installed"))
        f4.addRow(self.check("compat.mangohud", "Show MangoHud performance overlay"))
        f4.addRow(self.check("compat.gamescope", "Run games inside gamescope"))
        gs = QLineEdit(self.st.get("compat.gamescope_args"))
        gs.editingFinished.connect(lambda: self.st.set("compat.gamescope_args", gs.text()))
        f4.addRow("gamescope arguments", gs)
        f4.addRow(self.check("compat.native_dosbox", "Use native DOSBox for GOG's DOS games"))
        f4.addRow(self.check("compat.native_scummvm", "Use native ScummVM for GOG's ScummVM games"))
        lay.addWidget(g4)
        g5 = QGroupBox("Detected tools")
        self.tools_lay = QVBoxLayout(g5)
        lay.addWidget(g5)
        lay.addStretch(1)
        return w

    def _refresh_runners(self) -> None:
        compat = self.s.compat
        self.runner_list.clear()
        for r in compat.protons + compat.wines:
            it = QListWidgetItem(icons.icon("proton" if r.kind == "proton" else "wine", theme.TEXT_DIM),
                                 f"{r.name}    —  {r.origin}    {r.path}")
            it.setData(Qt.ItemDataRole.UserRole, r)
            self.runner_list.addItem(it)
        if not self.runner_list.count():
            self.runner_list.addItem("No Proton found — click “Download GE-Proton” to run Windows games.")
        for combo, items, key in ((self.def_proton, compat.protons, "compat.default_proton"),
                                  (self.def_wine, compat.wines, "compat.default_wine")):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Automatic (newest GE-Proton)" if combo is self.def_proton else "Automatic", "")
            for r in items:
                combo.addItem(r.name, r.path)
            combo.setCurrentIndex(max(0, combo.findData(self.st.get(key))))
            combo.blockSignals(False)
        umu = find_umu()
        self.umu_status.setText(f"<span style='color:{theme.SUCCESS}'>Installed</span> — {umu}" if umu else
                                "Not installed. Proton will be started directly (works for most games).")
        self.umu_btn.setText("Reinstall / update umu-launcher" if umu else "Install umu-launcher")
        clear_layout(self.tools_lay)
        for name, binary, purpose in (
                ("umu-run", "umu-run", "Proton in the Steam Runtime"), ("GameMode", "gamemoderun", "CPU/GPU tuning"),
                ("MangoHud", "mangohud", "performance overlay"), ("gamescope", "gamescope", "micro-compositor"),
                ("winetricks", "winetricks", "prefix tweaks"), ("innoextract", "innoextract", "GOG offline installers"),
                ("Legendary", "legendary", "Epic Games"), ("DOSBox", "dosbox", "native DOS games"),
                ("ScummVM", "scummvm", "native adventure games")):
            path = find_umu() if binary == "umu-run" else which(binary)
            ok = bool(path)
            row = label(f"<span style='color:{theme.SUCCESS if ok else theme.TEXT_FAINT}'>"
                        f"{'✔' if ok else '✖'}</span>  <b>{name}</b> — {purpose}"
                        + (f"  <span style='color:{theme.TEXT_FAINT}'>({path})</span>" if ok else ""))
            self.tools_lay.addWidget(row)

    def _remove_runner(self) -> None:
        it = self.runner_list.currentItem()
        r = it.data(Qt.ItemDataRole.UserRole) if it else None
        if not r:
            return
        if not r.removable:
            QMessageBox.information(self, "Remove", "Only builds downloaded by Super Nova can be removed here.")
            return
        if QMessageBox.question(self, "Remove", f"Remove {r.name}?") == QMessageBox.StandardButton.Yes:
            runner_manager.remove_proton(r.path)
            self.s.compat.refresh()
            self._refresh_runners()

    # -------------------------------------------------------------- cloud
    def _cloud(self) -> QWidget:
        w, lay = self._page()
        g, f = self.group("Cloud saves")
        f.addRow(self.check("cloud.enabled", "Synchronise cloud saves (GOG)"))
        f.addRow(self.check("cloud.sync_on_launch", "Download newer saves before a game starts"))
        f.addRow(self.check("cloud.sync_on_exit", "Upload saves after a game closes"))
        f.addRow("On conflict", self.combo("cloud.conflict", [("ask", "Ask me"), ("newest", "Keep the newest saves")]))
        lay.addWidget(g)
        lay.addWidget(label("Save folders are resolved inside each game's Proton prefix (e.g. "
                            "drive_c/users/steamuser/Saved Games). Steam and Epic handle their own cloud saves.",
                            "Faint", wrap=True))
        lay.addStretch(1)
        return w

    # ------------------------------------------------------- integrations
    def _build_integrations(self) -> None:
        lay = self.integrations_w.layout()
        if lay is None:
            lay = QVBoxLayout(self.integrations_w)
            lay.setContentsMargins(4, 18, 18, 18)
            lay.setSpacing(12)
        clear_layout(lay)
        lay.addWidget(label("Connect your gaming platforms. Games from every platform appear in one library and "
                            "the same title owned on several platforms is merged into a single entry.",
                            "Subtle", wrap=True))
        enabled = self.st.get("integrations.enabled") or {}
        for integ in self.s.integrations.values():
            c = card()
            cl = QHBoxLayout(c)
            cl.setContentsMargins(16, 14, 16, 14)
            cl.setSpacing(14)
            b = label()
            b.setPixmap(icons.badge(integ.letter, integ.color, 36))
            cl.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(3)
            t = label(integ.name)
            t.setStyleSheet("font-weight:700; font-size: 12pt;")
            col.addWidget(t)
            col.addWidget(label(integ.description, "Faint", wrap=True))
            status = integ.status_text()
            syncing = integ.id in self.s.library.syncing
            st = label(("Syncing…  " if syncing else "") + status, "Subtle", wrap=True)
            if integ.connected():
                st.setStyleSheet(f"color:{theme.SUCCESS};")
            col.addWidget(st)
            cl.addLayout(col, 1)
            btns = QVBoxLayout()
            btns.setSpacing(6)
            en = QCheckBox("Enabled")
            en.setChecked(enabled.get(integ.id, True))
            en.toggled.connect(lambda on, i=integ.id: self._enable(i, on))
            btns.addWidget(en)
            for text, fn in self._integration_buttons(integ):
                bb = button(text)
                bb.clicked.connect(lambda _c=False, f=fn: f())
                btns.addWidget(bb)
            cl.addLayout(btns)
            lay.addWidget(c)
        plug = QHBoxLayout()
        pb = button("Open plugins folder", "puzzle")
        pb.clicked.connect(lambda: xdg_open(str(paths.plugins_dir())))
        plug.addWidget(pb)
        plug.addWidget(label("Drop integration plugins (.py) here and restart Super Nova — see "
                             "docs/examples/example_plugin.py.", "Faint", wrap=True), 1)
        lay.addLayout(plug)
        lay.addStretch(1)

    def _integration_buttons(self, integ) -> list:
        out = []
        if integ.id == "gog":
            if integ.connected():
                out.append(("Sign out", self.ctl.gog_logout))
            else:
                out.append(("Sign in", self.ctl.gog_login))
        elif integ.id == "steam":
            out.append(("Web API key…", self.ctl.steam_api))
        elif integ.id == "epic":
            if not integ.connected() and integ.available():
                out.append(("Sign in", lambda: self.ctl.guard(integ.connect)))
        elif integ.id == "local":
            out.append(("Add game…", self.ctl.add_game))
        if integ.id != "local" and integ.connected():
            out.append(("Sync now", lambda i=integ.id: self.s.library.refresh([i])))
        return out

    def _enable(self, iid: str, on: bool) -> None:
        enabled = self.st.get("integrations.enabled") or {}
        enabled[iid] = on
        self.st.set("integrations.enabled", enabled)
        self.s.library.rebuild()
        self.s.bus.library_changed.emit()
        self.s.bus.integration_changed.emit(iid)
        if on:
            self.s.library.refresh([iid])

    # -------------------------------------------------------------- about
    def _about(self) -> QWidget:
        w, lay = self._page()
        lay.addWidget(label(f"Super Nova {__version__}", "SectionTitle"))
        lay.addWidget(label("A unified game launcher for Linux inspired by GOG Galaxy 2.0. Not affiliated with "
                            "GOG sp. z o.o., Valve, Epic Games or Lutris.", "Subtle", wrap=True))
        for text, path in (("Configuration", paths.config_dir()), ("Data (prefixes, Proton, manifests)", paths.data_dir()),
                           ("Cache", paths.cache_dir()), ("Logs", paths.logs_dir())):
            row = QHBoxLayout()
            row.addWidget(label(text), 1)
            b = button(str(path), "folder")
            b.clicked.connect(lambda _c=False, p=path: xdg_open(str(p)))
            row.addWidget(b)
            lay.addLayout(row)
        lay.addStretch(1)
        return w
