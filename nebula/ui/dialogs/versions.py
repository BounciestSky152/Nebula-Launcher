"""Change-version dialog: update, roll back or switch channel to a specific GOG build."""
from __future__ import annotations

import copy
from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from nebula.core import tasks
from nebula.integrations.base import branch_target
from nebula.integrations.gog.content import branch_label, nice_date
from nebula.ui import theme
from nebula.ui.dialogs.misc import _buttons
from nebula.ui.widgets.common import Spinner, button, label

OK_TEXT = "Install selected version"


def _plain(lb):
    lb.setTextFormat(Qt.TextFormat.PlainText)  # version and channel names come from the store API
    return lb


class ChangeVersionDialog(QDialog):
    """Lists the builds of one channel. After ``exec()``, ``choice`` holds ``{"version", "change", "label"}``
    for ``Actions.update`` (None when nothing was chosen)."""

    def __init__(self, services, game, info=None, parent=None):
        super().__init__(parent)
        self.s = services
        self.game = game
        self.integ = services.integration(game.source)
        self.info = None
        self.choice: dict | None = None
        self._closed = False
        self._token = 0
        self.setWindowTitle(f"Change version — {game.title}")
        self.setMinimumSize(600, 500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(10)
        lay.addWidget(label("Change version", "SectionTitle"))
        self.sub = _plain(label("", "Subtle", wrap=True))
        lay.addWidget(self.sub)
        self.channel_row = QWidget()
        cr = QHBoxLayout(self.channel_row)
        cr.setContentsMargins(0, 0, 0, 0)
        cr.addWidget(label("Channel", "Subtle"))
        self.channel = QComboBox()
        self.channel.setMinimumWidth(220)
        self.channel.currentIndexChanged.connect(lambda _i: self._fill())
        cr.addWidget(self.channel)
        cr.addStretch(1)
        self.channel_row.hide()
        lay.addWidget(self.channel_row)
        load_row = QHBoxLayout()
        self.spinner = Spinner(20)
        self.spinner.stop()
        self.loading = label("Loading versions from GOG…", "Faint")
        self.loading.hide()
        load_row.addWidget(self.spinner)
        load_row.addWidget(self.loading, 1)
        lay.addLayout(load_row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Version", "Released", ""])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.hide()
        lay.addWidget(self.tree, 1)
        msg_row = QHBoxLayout()
        self.message = _plain(label("", "Subtle", wrap=True))
        self.message.hide()
        self.retry = button("Try again", "refresh")
        self.retry.clicked.connect(lambda: self._load(refresh=True))
        self.retry.hide()
        msg_row.addWidget(self.message, 1)
        msg_row.addWidget(self.retry, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(msg_row)
        self.filler = QWidget()  # takes the tree's space while the tree is hidden
        lay.addWidget(self.filler, 1)
        self.summary = _plain(label("", "Subtle", wrap=True))
        self.summary.hide()
        lay.addWidget(self.summary)
        self.warn = _plain(label("", "Warning", wrap=True))
        self.warn.hide()
        lay.addWidget(self.warn)
        buttons = _buttons(self, OK_TEXT)
        self.cancel_button = buttons.itemAt(1).widget()
        lay.addLayout(buttons)
        self.ok_button.setEnabled(False)
        if info is not None and not info.stale and not info.error and (info.builds or info.kind != "builds"):
            self._show(info)
        else:
            self._load()

    def done(self, r: int) -> None:  # late version_info results must not touch a closed dialog
        self._closed = True
        super().done(r)

    # ---- data
    def _load(self, refresh: bool = False) -> None:
        self._token += 1
        token = self._token
        self._reset_choice()
        for w in (self.tree, self.channel_row, self.message, self.retry):
            w.hide()
        self.filler.show()
        self.spinner.start()
        self.loading.show()
        tasks.run_async(self.integ.version_info, copy.deepcopy(self.game), True, refresh,
                        on_done=lambda info, t=token: self._loaded(t, info),
                        on_error=lambda e, t=token: self._failed(t, e))

    def _loaded(self, token: int, info) -> None:
        if self._closed or token != self._token:
            return
        self._show(info)

    def _failed(self, token: int, exc) -> None:
        if self._closed or token != self._token:
            return
        self._stop_loading()
        self._message(f"Couldn't load versions from GOG. {exc}", retry=True)

    def _stop_loading(self) -> None:
        self.spinner.stop()
        self.loading.hide()

    def _reset_choice(self) -> None:
        self.choice = None
        self.ok_button.setText(OK_TEXT)
        self.ok_button.setEnabled(False)
        self.summary.hide()
        self.warn.hide()

    def _message(self, text: str, retry: bool = False) -> None:
        """Show ``text`` in place of the version list (it never goes into the tree, so it can't be "selected")."""
        self.tree.hide()
        self.channel_row.hide()
        self.filler.show()
        self.message.setText(text)
        self.message.setVisible(bool(text))
        self.retry.setVisible(retry)

    def _close_only(self) -> None:
        self.ok_button.hide()
        self.cancel_button.setText("Close")

    def _show(self, info) -> None:
        self._stop_loading()
        self.info = info
        self._reset_choice()
        if info is None or info.kind not in ("builds", "installer"):
            self.sub.setText("")
            self._message("Nebula can't manage versions for this copy.")
            self._close_only()
            return
        inst = info.installed
        released = nice_date(inst.date)
        self.sub.setText(" · ".join(p for p in (
            f"Installed: {inst.label}", branch_label(inst.branch) if info.kind == "builds" else "",
            released and f"released {released}", "staying on this version" if info.pinned else "") if p))
        if info.error == "signed_out":
            self._message("Sign in to GOG to see the available versions.")
            return
        if info.error:
            self._message(f"Couldn't load versions from GOG. {info.error_text}", retry=True)
            return
        if info.kind == "installer":  # GOG only offers its current offline installer
            self._message(info.note)
            latest = info.latest
            if info.status == "different" and latest:
                self.choice = {"version": "", "change": "update", "label": latest.label}
                self.ok_button.setText(f"Download installer {latest.label}".replace("&", "&&"))
                self.ok_button.setEnabled(True)
            else:
                self._close_only()
            return
        self.message.hide()
        self.retry.hide()
        branches = list(info.branches) or [""]
        self.channel.blockSignals(True)
        self.channel.clear()
        for b in branches:
            self.channel.addItem(branch_label(b) if b else "Stable (recommended)", b)
        self.channel.setCurrentIndex(max(0, self.channel.findData("" if info.branch_gone else inst.branch)))
        self.channel.blockSignals(False)
        self.channel_row.setVisible(len(branches) > 1 or bool(inst.branch))
        self.tree.show()
        self.filler.hide()
        self._fill()

    def _fill(self) -> None:
        """Rows = the builds of the selected channel, newest first; the installed one can't be selected."""
        info = self.info
        if info is None or info.kind != "builds":
            return
        branch = self.channel.currentData() or ""
        rows = [(i, b) for i, b in enumerate(info.builds) if (b.branch or "") == branch]
        names = Counter(b.version for _, b in rows)
        self.tree.clear()
        self._reset_choice()
        installed_row = None
        for i, b in rows:
            text = b.label + (f" (build {b.id})" if b.version and names[b.version] > 1 else "")
            tag = " · ".join(t for t in ("Installed" if b.current else "", "Newest" if b.newest else "") if t)
            it = QTreeWidgetItem([text, nice_date(b.date), tag])
            if tag:
                it.setForeground(2, QBrush(QColor(theme.ACCENT if b.current else theme.SUCCESS)))
            it.setData(0, Qt.ItemDataRole.UserRole, i)
            if b.current:
                it.setFlags(Qt.ItemFlag.ItemIsEnabled)
                installed_row = it
            self.tree.addTopLevelItem(it)
        if installed_row is not None:
            self.tree.scrollToItem(installed_row)
        self.message.setText("No other versions on this channel.")
        self.message.setVisible(all(b.current for _, b in rows))

    # ---- selection
    def _selected(self) -> None:
        info = self.info
        items = self.tree.selectedItems()
        sel = info.builds[items[0].data(0, Qt.ItemDataRole.UserRole)] if info is not None and items else None
        self._reset_choice()
        if sel is None or sel.current:
            return
        inst = info.installed
        ch, v, d = branch_label(sel.branch), sel.label, nice_date(sel.date)
        when = f" (released {d})" if d else ""
        warn = ""
        if sel.branch != inst.branch or info.branch_gone:  # another channel
            change, lbl = "switch", f"{ch} {v}"
            summary = f"Switch to the {ch} channel and install {v}{when}. You'll get {ch} updates from now on."
            if sel.newest:  # resolved when the job runs, so a build published meanwhile isn't pinned by mistake
                version, ok = branch_target(sel.branch), f"Switch to {ch}"
            else:
                version, ok = sel.id, f"Switch to {ch} {v}"
                summary += f" Updates will be paused because this isn't the newest {ch} version."
            if sel.branch:
                warn = "Beta versions can be unstable. Back up your saves first."
            elif inst.branch:
                warn = "Saves made in a beta may not load in Stable versions. Back up your saves first."
        elif sel.newest:
            version, change, lbl, ok = "", "update", v, f"Update to {v}"
            summary = f"Update to {v}, the newest {ch} version{when}. Only files that changed are downloaded."
            if info.pinned:
                summary += " This turns off “Stay on this version”."
        elif not info.installed_listed:  # GOG doesn't list the installed build: older or newer is unknown
            version, change, lbl, ok = sel.id, "change", v, f"Install {v}"
            summary = f"Install {v}{when}."
        elif (sel.published or sel.date) > (inst.published or inst.date):
            newest = next((b.label for b in info.builds if b.newest and b.branch == sel.branch), "")
            version, change, lbl, ok = sel.id, "update", v, f"Install {v}"
            summary = (f"Install {v}{when}. A newer version, {newest}, exists, so Nebula will stop offering updates "
                       "until you choose it.")
        else:
            version, change, lbl, ok = sel.id, "rollback", v, f"Roll back to {v}"
            summary = (f"Roll back to {v}{when}. Only files that differ are downloaded. Nebula will stop offering "
                       "updates for this game until you update again.")
            warn = "Saves made by a newer version may not work in an older one. Back them up if you're unsure."
        if change in ("rollback", "switch") and self.game.extra.get("installed_dlcs"):
            summary += f" DLC that isn't part of {v} is removed and comes back when you update again."
        self.choice = {"version": version, "change": change, "label": lbl}
        self.ok_button.setText(ok.replace("&", "&&"))  # no mnemonics from version names
        self.ok_button.setEnabled(True)
        self.summary.setText(summary)
        self.summary.show()
        self.warn.setText(warn)
        self.warn.setVisible(bool(warn))
