"""UI-level actions shared by pages, menus and the tray."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox

from nebula.core import tasks
from nebula.core.utils import xdg_open
from nebula.integrations import base
from nebula.runners.compat import RunnerMissing
from nebula.ui import icons


class UiController:
    def __init__(self, services, window):
        self.s = services
        self.w = window
        services.launcher.conflict_resolver = self._resolve_conflict

    # ------------------------------------------------------------ helpers
    def integ(self, game):
        return self.s.integration(game.source)

    def entry(self, key: str):
        return self.s.library.entry(key)

    def game_state(self, entry) -> str:
        """running | preparing | job | update | installed | not_installed"""
        if self.s.launcher.entry_running(entry):
            return "running"
        if self.s.launcher.entry_preparing(entry):
            return "preparing"
        if any(self.s.jobs.active_for(g.uid) for g in entry.games):
            return "job"
        if entry.installed:
            return "update" if entry.update_available else "installed"
        return "not_installed"

    def installed_game(self, entry):
        primary = entry.primary
        if primary.installed:
            return primary
        return next((g for g in entry.games if g.installed), None)

    # ------------------------------------------------------- main action
    def primary_action(self, key: str) -> None:
        e = self.entry(key)
        if not e:
            return
        state = self.game_state(e)
        if state == "running":
            self.stop(e)
        elif state in ("installed", "update"):
            self.play(e)
        elif state == "not_installed":
            self.install(e.primary)
        elif state == "job":
            self.w.navigate("downloads")

    def play(self, entry, uid: str = "", task_id: str = "") -> None:
        game = self.s.library.game(uid) if uid else self.installed_game(entry)
        if game is None:
            return
        self.s.launcher.launch(game.uid, task_id)

    def stop(self, entry) -> None:
        st = self.s.launcher.entry_running(entry)
        if st and QMessageBox.question(self.w, "Stop game", f"Force {st.title} to quit?") == QMessageBox.StandardButton.Yes:
            self.s.launcher.stop(st.uid)

    def _resolve_conflict(self, game, result, callback) -> None:
        from nebula.ui.dialogs.misc import CloudConflictDialog

        dlg = CloudConflictDialog(game, result, self.w)
        dlg.exec()
        callback(dlg.choice)

    # ------------------------------------------------------ installation
    def install(self, game) -> None:
        integ = self.integ(game)
        if not integ.supports(base.INSTALL):
            QMessageBox.information(self.w, "Install", f"{integ.name} games can't be installed from Nebula.")
            return
        if game.source == "gog" and not integ.connected():
            self.gog_login()
            return
        if game.source in ("steam",):
            self.s.actions.install(game, base.InstallRequest())
            self.w.toast(game.title, "Steam is installing the game")
            return
        from nebula.ui.dialogs.install import InstallDialog

        dlg = InstallDialog(self.s, game, self.s.library.entry_for_uid(game.uid), self.w)
        if dlg.exec():
            self.s.actions.install(game, dlg.request())
            self.w.toast(f"Installing {game.title}", "Added to the download queue")

    def update(self, game) -> None:
        self.s.actions.update(game)

    def repair(self, game) -> None:
        self.s.actions.repair(game)
        self.w.toast(game.title, "Verifying game files")

    def versions(self, game) -> None:
        from nebula.ui.dialogs.misc import VersionsDialog

        dlg = VersionsDialog(self.s, game, self.w)
        if dlg.exec() and dlg.selected():
            self.s.actions.update(game, version=dlg.selected())

    def uninstall(self, game) -> None:
        from nebula.ui.dialogs.misc import UninstallDialog

        has_prefix = self.s.compat.prefix_for(game.uid).exists()
        dlg = UninstallDialog(game, has_prefix, self.w)
        if dlg.exec():
            self.s.actions.uninstall(game, delete_prefix=dlg.prefix.isChecked())

    def manage_dlc(self, game) -> None:
        from nebula.ui.dialogs.misc import DlcDialog

        def show(details):
            dlg = DlcDialog(details, self.w)
            if dlg.exec() and dlg.selected():
                self.s.actions.install_dlcs(game, dlg.selected())

        tasks.run_async(self.integ(game).details, game, on_done=show)

    def settings(self, game) -> None:
        from nebula.ui.dialogs.game_settings import GameSettingsDialog

        GameSettingsDialog(self.s, game, self.w).exec()

    def cloud_sync(self, game, mode: str = "auto") -> None:
        def done(r):
            if r.action == "conflict":
                self._resolve_conflict(game, r, lambda ch: ch != "skip" and self.cloud_sync(game, ch))
            else:
                self.w.toast("Cloud saves", r.message, "error" if r.action == "error" else "info")

        tasks.run_async(self.integ(game).cloud_sync, game, mode, "manual", on_done=done)

    # ------------------------------------------------------------ library
    def edit_tags(self, key: str) -> None:
        from nebula.ui.dialogs.misc import TagsDialog

        e = self.entry(key)
        dlg = TagsDialog(self.s.library.all_tags(), e.user.tags, e.title, self.w)
        if dlg.exec():
            self.s.library.set_user(key, tags=dlg.tags())

    def toggle_favorite(self, key: str) -> None:
        e = self.entry(key)
        self.s.library.set_user(key, favorite=not e.user.favorite)

    def toggle_hidden(self, key: str) -> None:
        e = self.entry(key)
        self.s.library.set_user(key, hidden=not e.user.hidden)
        self.w.toast(e.title, "Unhidden" if e.user.hidden else "Hidden from your library")

    def set_rating(self, key: str, value: int) -> None:
        self.s.library.set_user(key, rating=value)

    def rename(self, key: str) -> None:
        from nebula.ui.dialogs.misc import TextInputDialog

        e = self.entry(key)
        dlg = TextInputDialog("Rename", "Display title", e.title, self.w)
        if dlg.exec():
            self.s.library.set_user(key, custom_title=dlg.value() if dlg.value() != e.primary.title else "")

    def set_cover(self, key: str) -> None:
        f, _ = QFileDialog.getOpenFileName(self.w, "Custom cover", "", "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            self.s.library.set_user(key, custom_cover=f)

    def open_store(self, game) -> None:
        url = self.integ(game).store_url(game)
        if url:
            xdg_open(url)

    def open_log(self, game) -> None:
        f = self.s.launcher.log_file(game.uid)
        if f.exists():
            xdg_open(str(f))
        else:
            self.w.toast("No log yet", "Launch the game from Nebula first")

    def shortcut(self, game) -> None:
        self.s.actions.create_shortcut(game, on_done=lambda paths: self.w.toast(
            "Shortcut created", f"{game.title} added to your desktop and application menu"))

    def edit_custom(self, game) -> None:
        from nebula.ui.dialogs.misc import AddGameDialog

        local = self.s.integrations["local"]
        entry = local.entries().get(game.source_id)
        dlg = AddGameDialog(entry, self.w)
        if dlg.exec():
            local.save_entry(dlg.data())
            self.s.library.refresh(["local"], enrich=False)

    def add_game(self) -> None:
        from nebula.ui.dialogs.misc import AddGameDialog

        dlg = AddGameDialog(None, self.w)
        if dlg.exec() and dlg.data().get("exe"):
            self.s.actions.add_custom_game(dlg.data())
            self.w.toast("Game added", dlg.data()["title"])

    def scan_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self.w, "Scan a folder for installed games")
        if d:
            self.s.actions.import_folder(d, on_done=lambda games: self.w.toast(
                "Scan complete", f"Imported {len(games)} installed game(s)" if games else "No GOG games found there"))

    def gog_login(self) -> None:
        from nebula.ui.dialogs.login import GogLoginDialog

        GogLoginDialog(self.s, self.w).exec()

    def proton_download(self) -> None:
        from nebula.ui.dialogs.misc import ProtonDialog

        dlg = ProtonDialog(self.s, self.w)
        if dlg.exec() and dlg.selected():
            self.s.actions.install_ge_proton(dlg.selected())
            self.w.toast("Downloading " + dlg.selected()["tag"], "See Downloads for progress")

    def steam_api(self) -> None:
        from nebula.ui.dialogs.misc import SteamApiDialog

        dlg = SteamApiDialog(self.s, self.w)
        if dlg.exec():
            self.s.integrations["steam"].connect(api_key=dlg.key.text(), steamid=dlg.sid.text())
            self.s.bus.integration_changed.emit("steam")
            self.s.library.refresh(["steam"])
            self.s.friends.refresh()

    def guard(self, fn, *args) -> None:
        try:
            fn(*args)
        except RunnerMissing as exc:
            self._no_runner(str(exc))
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self.w, "Error", str(exc))

    def _no_runner(self, text: str) -> None:
        box = QMessageBox(self.w)
        box.setWindowTitle("Proton needed")
        box.setText(text)
        dl = box.addButton("Download GE-Proton", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Close)
        box.exec()
        if box.clickedButton() is dl:
            self.proton_download()

    # -------------------------------------------------------------- menus
    def entry_menu(self, key: str, parent=None) -> QMenu:
        e = self.entry(key)
        m = QMenu(parent or self.w)
        m.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        if e is None:
            return m
        state = self.game_state(e)
        game = self.installed_game(e) or e.primary
        integ = self.integ(game)
        if state == "running":
            m.addAction(icons.icon("stop"), "Stop", lambda: self.stop(e))
        elif state in ("installed", "update"):
            m.addAction(icons.icon("play"), "Play", lambda: self.play(e))
            tasks_ = [t for t in integ.launch_tasks(game) if not t.primary]
            if tasks_:
                sub = m.addMenu("Other launch options")
                for t in tasks_:
                    sub.addAction(t.name, lambda t=t: self.play(e, game.uid, t.id))
        elif state == "not_installed":
            m.addAction(icons.icon("download"), "Install", lambda: self.install(e.primary))
        if state == "update" and integ.supports(base.UPDATE):
            m.addAction(icons.icon("download"), "Update now", lambda: self.update(game))
        if len(e.games) > 1:
            sub = m.addMenu("Platform")
            for g in e.games:
                gi = self.integ(g)
                act = sub.addAction(f"{gi.name}" + ("  (installed)" if g.installed else ""),
                                    lambda g=g: self.s.library.set_user(key, preferred_uid=g.uid))
                act.setCheckable(True)
                act.setChecked(g is e.primary)
                if g.installed and g is not game:
                    sub.addAction(f"Play on {gi.name}", lambda g=g: self.play(e, g.uid))
        m.addSeparator()
        if game.installed:
            manage = m.addMenu(icons.icon("wrench"), "Manage installation")
            manage.addAction(icons.icon("settings"), "Configure…", lambda: self.settings(game))
            if game.install_path:
                manage.addAction(icons.icon("folder"), "Show folder", lambda: self.s.actions.open_folder(game))
            if integ.supports(base.REPAIR):
                manage.addAction(icons.icon("shield"), "Verify / Repair", lambda: self.repair(game))
            if integ.supports(base.VERSIONS):
                manage.addAction(icons.icon("history"), "Versions / Roll back…", lambda: self.versions(game))
            if integ.supports(base.DLC):
                manage.addAction(icons.icon("layers"), "Install DLC…", lambda: self.manage_dlc(game))
            if integ.supports(base.CLOUD_SAVES):
                manage.addAction(icons.icon("cloud"), "Sync cloud saves", lambda: self.cloud_sync(game))
            manage.addAction(icons.icon("terminal"), "Open launch log", lambda: self.open_log(game))
            manage.addAction(icons.icon("shortcut"), "Create shortcut", lambda: self.shortcut(game))
            if game.source == "local":
                manage.addAction(icons.icon("wrench"), "Edit game…", lambda: self.edit_custom(game))
            if integ.supports(base.UNINSTALL):
                manage.addSeparator()
                manage.addAction(icons.icon("trash"), "Uninstall…" if game.source != "local" else "Remove…",
                                 lambda: self.uninstall(game))
        m.addAction(icons.icon("heart"), "Remove from favourites" if e.user.favorite else "Add to favourites",
                    lambda: self.toggle_favorite(key))
        tags = m.addMenu(icons.icon("tag"), "Tags")
        for t in self.s.library.all_tags():
            act = tags.addAction(t)
            act.setCheckable(True)
            act.setChecked(t in e.user.tags)
            act.toggled.connect(lambda on, t=t: self.s.library.add_tag(key, t) if on else self.s.library.remove_tag(key, t))
        tags.addSeparator()
        tags.addAction("Edit tags…", lambda: self.edit_tags(key))
        rating = m.addMenu(icons.icon("star"), "Rating")
        for v in range(0, 6):
            act = rating.addAction("★" * v if v else "No rating", lambda v=v: self.set_rating(key, v))
            act.setCheckable(True)
            act.setChecked(e.user.rating == v)
        custom = m.addMenu(icons.icon("image"), "Customise")
        custom.addAction("Rename…", lambda: self.rename(key))
        custom.addAction("Set custom cover…", lambda: self.set_cover(key))
        if e.user.custom_cover:
            custom.addAction("Reset cover", lambda: self.s.library.set_user(key, custom_cover=""))
        m.addAction(icons.icon("eye" if e.user.hidden else "eye-off"), "Unhide" if e.user.hidden else "Hide game",
                    lambda: self.toggle_hidden(key))
        m.addSeparator()
        if integ.store_url(game):
            m.addAction(icons.icon("external"), f"Open {integ.name} page", lambda: self.open_store(game))
        return m
