"""Install dialog: platform/build, language, version, DLC selection and location."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit,
                               QListWidget, QListWidgetItem, QVBoxLayout, QWidget)

from nebula.core import tasks
from nebula.core.utils import human_size
from nebula.integrations.base import InstallRequest
from nebula.ui.widgets.common import RemoteImage, Spinner, button, label

PLATFORM_LABELS = {"linux": "Linux (native)", "windows": "Windows (runs with Proton)", "mac": "macOS"}


class InstallDialog(QDialog):
    def __init__(self, services, game, entry=None, parent=None):
        super().__init__(parent)
        self.s = services
        self.game = game
        self.integ = services.integration(game.source)
        self.opts = None
        self.setWindowTitle(f"Install {game.title}")
        self.setMinimumWidth(620)
        root = QHBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(20)
        cover = RemoteImage(None, radius=8)
        cover.setFixedSize(150, 212)
        cover.set_source(entry.cover_urls() if entry else game.covers(), game.title)
        root.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(10)
        root.addLayout(col, 1)
        col.addWidget(label(game.title, "SectionTitle", wrap=True))
        self.method = label("", "Faint")
        col.addWidget(self.method)
        self.loading = QWidget()
        ll = QHBoxLayout(self.loading)
        ll.setContentsMargins(0, 10, 0, 10)
        sp = Spinner(20)
        sp.start()
        ll.addWidget(sp)
        ll.addWidget(label("Fetching install options…", "Subtle"))
        ll.addStretch(1)
        col.addWidget(self.loading)

        self.form_w = QWidget()
        form = QFormLayout(self.form_w)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.platform = QComboBox()
        self.platform.currentIndexChanged.connect(self._platform_changed)
        form.addRow("Build", self.platform)
        self.language = QComboBox()
        form.addRow("Language", self.language)
        self.version = QComboBox()
        form.addRow("Version", self.version)
        path_row = QHBoxLayout()
        self.path = QLineEdit()
        browse = button("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        form.addRow("Install to", path_row)
        self.dlcs = QListWidget()
        self.dlcs.setMaximumHeight(120)
        self.dlcs.itemChanged.connect(lambda _i: self._update_sizes())
        self.dlc_label = label("DLC")
        form.addRow(self.dlc_label, self.dlcs)
        col.addWidget(self.form_w)
        self.form_w.hide()

        self.sizes = label("", "Subtle")
        col.addWidget(self.sizes)
        self.notes = label("", "Faint", wrap=True)
        col.addWidget(self.notes)
        self.shortcut = QCheckBox("Create desktop && menu shortcut")
        col.addWidget(self.shortcut)
        self.error = label("", "Error", wrap=True)
        col.addWidget(self.error)
        col.addStretch(1)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = button("Cancel")
        cancel.clicked.connect(self.reject)
        self.ok = button("Install", "download", primary=True)
        self.ok.setEnabled(False)
        self.ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(self.ok)
        col.addLayout(btns)
        self._load("")

    def _load(self, platform: str) -> None:
        self.loading.show()
        self.ok.setEnabled(False)
        tasks.run_async(self.integ.install_options, self.game, platform, on_done=self._loaded, on_error=self._failed)

    def _failed(self, exc) -> None:
        self.loading.hide()
        self.error.setText(f"Could not load install options: {exc}")

    def _loaded(self, opts) -> None:
        self.opts = opts
        self.loading.hide()
        self.form_w.show()
        self.method.setText(opts.method)
        self.platform.blockSignals(True)
        self.platform.clear()
        for p in opts.platforms:
            self.platform.addItem(PLATFORM_LABELS.get(p, p), p)
        self.platform.setCurrentIndex(max(0, opts.platforms.index(opts.platform)) if opts.platform in opts.platforms else 0)
        self.platform.setEnabled(len(opts.platforms) > 1)
        self.platform.blockSignals(False)
        self.language.clear()
        for code, lab in opts.languages:
            self.language.addItem(lab, code)
        idx = self.language.findData(opts.language)
        self.language.setCurrentIndex(max(0, idx))
        self.language.setEnabled(len(opts.languages) > 1)
        self.version.clear()
        for v in opts.versions:
            text = v["label"] + (f"  •  {v['date']}" if v.get("date") else "") + (
                f"  [{v['branch']}]" if v.get("branch") else "")
            self.version.addItem(text, v["id"])
        self.version.setEnabled(len(opts.versions) > 1)
        if not self.path.text() or not self.path.isModified():
            self.path.setText(str(Path(opts.install_dir).parent))
        self.dlcs.blockSignals(True)
        self.dlcs.clear()
        for d in opts.dlcs:
            it = QListWidgetItem(f"{d['title']}" + (f"  ({human_size(d['size'])})" if d.get("size") else ""))
            it.setData(Qt.ItemDataRole.UserRole, d["id"])
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked)
            self.dlcs.addItem(it)
        self.dlcs.blockSignals(False)
        self.dlcs.setVisible(bool(opts.dlcs))
        self.dlc_label.setVisible(bool(opts.dlcs))
        self.notes.setText("\n".join(opts.notes))
        self._update_sizes()
        self.ok.setEnabled(True)

    def _platform_changed(self, _i: int) -> None:
        p = self.platform.currentData()
        if self.opts and p and p != self.opts.platform:
            self._load(p)

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Install location", self.path.text())
        if d:
            self.path.setText(d)
            self._update_sizes()

    def _update_sizes(self) -> None:
        if not self.opts:
            return
        dl = self.opts.download_size
        disk = self.opts.disk_size
        target = Path(self.path.text() or ".")
        while not target.exists() and target != target.parent:
            target = target.parent
        try:
            free = shutil.disk_usage(target).free
        except OSError:
            free = 0
        text = []
        if dl:
            text.append(f"Download: {human_size(dl)}")
        if disk:
            text.append(f"Disk space: {human_size(disk)}")
        if free:
            text.append(f"Free: {human_size(free)}")
        self.sizes.setText("   •   ".join(text))
        if disk and free and disk > free:
            self.error.setText("Not enough free disk space at this location.")
        else:
            self.error.setText("")

    def request(self) -> InstallRequest:
        dlcs = [self.dlcs.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.dlcs.count())
                if self.dlcs.item(i).checkState() == Qt.CheckState.Checked]
        version = self.version.currentData() or ""
        if self.opts and self.opts.versions and version == self.opts.versions[0]["id"]:
            version = ""  # latest
        return InstallRequest(platform=self.platform.currentData() or "", language=self.language.currentData() or "",
                              dlcs=dlcs, version=version, base_dir=self.path.text().strip(),
                              shortcut=self.shortcut.isChecked())
