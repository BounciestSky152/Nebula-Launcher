"""Smaller dialogs."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout)

from nebula.core import tasks
from nebula.core.utils import human_size, xdg_open
from nebula.runners import manager as runner_manager
from nebula.ui.widgets.common import Spinner, button, label


def _buttons(dialog: QDialog, ok_text: str = "OK", primary: bool = True, danger: bool = False) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addStretch(1)
    cancel = button("Cancel")
    cancel.clicked.connect(dialog.reject)
    ok = button(ok_text, primary=primary and not danger, obj="Danger" if danger else None)
    ok.clicked.connect(dialog.accept)
    row.addWidget(cancel)
    row.addWidget(ok)
    dialog.ok_button = ok
    return row


class AddGameDialog(QDialog):
    """Galaxy's "Add game manually": any Linux binary, Windows .exe (Proton) or emulator command."""

    def __init__(self, entry: dict | None = None, parent=None):
        super().__init__(parent)
        self.entry = dict(entry or {})
        self.setWindowTitle("Edit game" if entry else "Add a game")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.addWidget(label("Edit game" if entry else "Add a game to your library", "SectionTitle"))
        lay.addWidget(label("Windows programs (.exe) are run with Proton automatically.", "Faint"))
        form = QFormLayout()
        form.setSpacing(10)
        self.title = QLineEdit(self.entry.get("title", ""))
        form.addRow("Title", self.title)
        exe_row = QHBoxLayout()
        self.exe = QLineEdit(self.entry.get("exe", ""))
        self.exe.setPlaceholderText("/path/to/game  or  game.exe  or  retroarch -L core.so rom")
        b = button("Browse…")
        b.clicked.connect(self._browse)
        exe_row.addWidget(self.exe, 1)
        exe_row.addWidget(b)
        form.addRow("Executable", exe_row)
        self.args = QLineEdit(self.entry.get("args", ""))
        form.addRow("Arguments", self.args)
        self.cwd = QLineEdit(self.entry.get("cwd", ""))
        self.cwd.setPlaceholderText("Defaults to the executable's folder")
        form.addRow("Working folder", self.cwd)
        self.platform = QComboBox()
        self.platform.addItem("Detect automatically", "")
        self.platform.addItem("Linux (native)", "linux")
        self.platform.addItem("Windows (Proton)", "windows")
        self.platform.setCurrentIndex(max(0, self.platform.findData(self.entry.get("platform", ""))))
        form.addRow("Platform", self.platform)
        cover_row = QHBoxLayout()
        self.cover = QLineEdit(self.entry.get("cover", ""))
        self.cover.setPlaceholderText("Image file or URL (portrait)")
        cb = button("Browse…")
        cb.clicked.connect(lambda: self._browse_image(self.cover))
        cover_row.addWidget(self.cover, 1)
        cover_row.addWidget(cb)
        form.addRow("Cover", cover_row)
        hero_row = QHBoxLayout()
        self.hero = QLineEdit(self.entry.get("hero", ""))
        self.hero.setPlaceholderText("Image file or URL (landscape)")
        hb = button("Browse…")
        hb.clicked.connect(lambda: self._browse_image(self.hero))
        hero_row.addWidget(self.hero, 1)
        hero_row.addWidget(hb)
        form.addRow("Background", hero_row)
        genres = self.entry.get("genres", "")
        self.genres = QLineEdit(", ".join(genres) if isinstance(genres, list) else genres)
        form.addRow("Genres", self.genres)
        self.developer = QLineEdit(self.entry.get("developer", ""))
        form.addRow("Developer", self.developer)
        self.description = QPlainTextEdit(self.entry.get("description", ""))
        self.description.setMaximumHeight(80)
        form.addRow("Description", self.description)
        lay.addLayout(form)
        lay.addLayout(_buttons(self, "Save" if entry else "Add game"))

    def _browse(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Choose the game executable", str(Path.home()))
        if f:
            self.exe.setText(f)
            if not self.title.text():
                self.title.setText(Path(f).stem.replace("_", " ").replace("-", " ").title())

    def _browse_image(self, field: QLineEdit) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Choose image", str(Path.home()), "Images (*.png *.jpg *.jpeg *.webp)")
        if f:
            field.setText(f)

    def data(self) -> dict:
        d = dict(self.entry)
        d.update({
            "title": self.title.text().strip() or Path(self.exe.text()).stem,
            "exe": self.exe.text().strip(),
            "args": self.args.text().strip(),
            "cwd": self.cwd.text().strip(),
            "platform": self.platform.currentData() or "",
            "cover": self.cover.text().strip(),
            "hero": self.hero.text().strip(),
            "genres": [g.strip() for g in self.genres.text().split(",") if g.strip()],
            "developer": self.developer.text().strip(),
            "description": self.description.toPlainText().strip(),
        })
        return d


class TagsDialog(QDialog):
    def __init__(self, all_tags: list[str], current: list[str], title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Tags — {title}")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.addWidget(label("Tags", "SectionTitle"))
        self.list = QListWidget()
        for t in sorted(set(all_tags) | set(current), key=str.lower):
            self._add(t, t in current)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.new = QLineEdit()
        self.new.setPlaceholderText("New tag")
        add = button("Add", "plus")
        add.clicked.connect(self._new)
        self.new.returnPressed.connect(self._new)
        row.addWidget(self.new, 1)
        row.addWidget(add)
        lay.addLayout(row)
        lay.addLayout(_buttons(self, "Save"))

    def _add(self, tag: str, checked: bool) -> None:
        it = QListWidgetItem(tag)
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.list.addItem(it)

    def _new(self) -> None:
        t = self.new.text().strip()
        if t and not self.list.findItems(t, Qt.MatchFlag.MatchExactly):
            self._add(t, True)
        self.new.clear()

    def tags(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.CheckState.Checked]


class UninstallDialog(QDialog):
    def __init__(self, game, has_prefix: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Uninstall {game.title}")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.addWidget(label(f"Uninstall {game.title}?", "SectionTitle"))
        if game.source == "local":
            lay.addWidget(label("The game is removed from your library. Its files are not deleted.", "Subtle", wrap=True))
        elif game.source in ("steam", "lutris"):
            lay.addWidget(label("The uninstall is handed over to the platform's own client.", "Subtle", wrap=True))
        else:
            lay.addWidget(label(f"All files in {game.install_path} will be deleted.", "Subtle", wrap=True))
        self.prefix = QCheckBox("Also delete the Proton prefix (local saves stored in it are lost)")
        self.prefix.setVisible(has_prefix)
        lay.addWidget(self.prefix)
        lay.addLayout(_buttons(self, "Uninstall" if game.source != "local" else "Remove", danger=True))


class CloudConflictDialog(QDialog):
    def __init__(self, game, result, parent=None):
        super().__init__(parent)
        self.choice = "skip"
        self.setWindowTitle("Cloud save conflict")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.addWidget(label("Cloud save conflict", "SectionTitle"))
        d = result.details
        lt = datetime.fromtimestamp(d.get("local_time", 0)).strftime("%d %b %Y %H:%M") if d.get("local_time") else "?"
        ct = datetime.fromtimestamp(d.get("cloud_time", 0)).strftime("%d %b %Y %H:%M") if d.get("cloud_time") else "?"
        lay.addWidget(label(f"Saves for <b>{game.title}</b> changed both on this computer and in the cloud.<br><br>"
                            f"Local saves: {lt}<br>Cloud saves: {ct}", wrap=True))
        row = QHBoxLayout()
        for text, choice, primary in (("Skip sync", "skip", False), ("Keep local (upload)", "upload", False),
                                      ("Use cloud (download)", "download", True)):
            b = button(text, primary=primary)
            b.clicked.connect(lambda _c=False, ch=choice: self._pick(ch))
            row.addWidget(b)
        lay.addLayout(row)

    def _pick(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class ProtonDialog(QDialog):
    """Browse and install GE-Proton releases."""

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.s = services
        self.setWindowTitle("Download GE-Proton")
        self.setMinimumSize(520, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.addWidget(label("GE-Proton", "SectionTitle"))
        lay.addWidget(label("GloriousEggroll's Proton build includes extra game fixes, media codecs and the latest "
                            "DXVK/VKD3D. Nebula uses the newest installed GE-Proton for Windows games by default.",
                            "Faint", wrap=True))
        self.spinner = Spinner(20)
        self.spinner.start()
        lay.addWidget(self.spinner)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        lay.addLayout(_buttons(self, "Download && install"))
        self.ok_button.setEnabled(False)
        self.releases: list[dict] = []
        tasks.run_async(runner_manager.list_ge_proton, on_done=self._loaded, on_error=self._failed)

    def _failed(self, exc) -> None:
        self.spinner.stop()
        self.list.addItem(f"Could not reach GitHub: {exc}")

    def _loaded(self, releases: list[dict]) -> None:
        self.spinner.stop()
        self.releases = releases
        installed = runner_manager.installed_ge_tags()
        for r in releases:
            done = r["tag"] in installed
            it = QListWidgetItem(f"{r['tag']}    {r['date'][:10]}    {human_size(r['size'])}" +
                                 ("    — installed" if done else ""))
            it.setData(Qt.ItemDataRole.UserRole, r)
            if done:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.list.addItem(it)
        self.list.currentItemChanged.connect(lambda cur, _p: self.ok_button.setEnabled(cur is not None))
        for i in range(self.list.count()):
            if self.list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled:
                self.list.setCurrentRow(i)
                break

    def selected(self) -> dict | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None


class SteamApiDialog(QDialog):
    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.s = services
        self.setWindowTitle("Steam Web API")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.addWidget(label("Connect your full Steam library", "SectionTitle"))
        lay.addWidget(label("Installed Steam games and play time are read locally. To also import games you own but "
                            "haven't installed, plus achievements and friends, enter a personal Steam Web API key. "
                            "Your profile's game details must be public.", "Subtle", wrap=True))
        link = button("Get an API key at steamcommunity.com/dev/apikey", obj="Link")
        link.clicked.connect(lambda: xdg_open("https://steamcommunity.com/dev/apikey"))
        lay.addWidget(link)
        form = QFormLayout()
        self.key = QLineEdit(services.settings.get("steam.api_key"))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API key", self.key)
        steam = services.integrations.get("steam")
        self.sid = QLineEdit(services.settings.get("steam.steamid"))
        self.sid.setPlaceholderText(steam.steamid() if steam else "SteamID64")
        form.addRow("SteamID64", self.sid)
        lay.addLayout(form)
        lay.addLayout(_buttons(self, "Save"))


class DlcDialog(QDialog):
    def __init__(self, details, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage DLC")
        self.setMinimumSize(440, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.addWidget(label("Install additional DLC", "SectionTitle"))
        self.list = QListWidget()
        for d in details.dlcs:
            if not d.get("owned") or d.get("installed"):
                continue
            it = QListWidgetItem(d["title"])
            it.setData(Qt.ItemDataRole.UserRole, d["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(it)
        if not self.list.count():
            self.list.addItem("All owned DLC is already installed.")
        lay.addWidget(self.list, 1)
        lay.addLayout(_buttons(self, "Install"))

    def selected(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                if self.list.item(i).data(Qt.ItemDataRole.UserRole) and
                self.list.item(i).checkState() == Qt.CheckState.Checked]


class TextInputDialog(QDialog):
    def __init__(self, title: str, prompt: str, value: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.addWidget(label(prompt))
        self.edit = QLineEdit(value)
        self.edit.returnPressed.connect(self.accept)
        lay.addWidget(self.edit)
        lay.addLayout(_buttons(self))

    def value(self) -> str:
        return self.edit.text().strip()
