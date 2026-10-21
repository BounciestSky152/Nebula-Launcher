"""Version card of the game page: installed / newest version, update, change version and update settings.

``describe_version`` turns a ``base.VersionInfo`` into a ``VersionView`` (plain data, no Qt, unit-tested
over every state); ``VersionCard.set_view`` only copies a view onto the widgets.
"""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QCheckBox, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from nebula.core.utils import human_size, relative_time
from nebula.integrations.base import branch_target
from nebula.integrations.gog.content import branch_label, nice_date
from nebula.ui import icons, theme
from nebula.ui.widgets.common import Spinner, button, icon_button, label

PIN_TIP = "Updating turns off “Stay on this version”."
INSTALLER_TIP = ("Downloads GOG's full installer again and unpacks it over the game folder. Files the old version "
                 "no longer uses are not removed.")
BUSY_TIPS = {"running": "Close the game before changing its version.",
             "job": "Wait for the current download to finish."}
ORIGINS = {"heroic": "imported from Heroic", "folder": "imported from a folder"}


@dataclass
class VersionView:
    """Everything the version card shows (texts, object names for colour, visibility and button actions)."""

    visible: bool = True
    title: str = "Version"
    pill_text: str = ""
    pill_obj: str = "Faint"  # Success | Warning | Faint
    installed_text: str = ""
    installed_sub: str = ""
    channel_text: str = ""  # "" hides the CHANNEL row
    newest_text: str = ""
    newest_obj: str = ""  # "" | Faint | Warning | Error
    newest_sub: str = ""
    newest_loading: bool = False
    status_text: str = ""
    status_obj: str = "Faint"
    note_text: str = ""
    update_visible: bool = False
    update_text: str = ""
    update_primary: bool = True
    update_enabled: bool = True
    update_tip: str = ""
    update_version: str = ""  # Actions.update(version=..., change=..., label=...)
    update_change: str = "update"
    update_label: str = ""
    change_visible: bool = False
    change_enabled: bool = True
    change_tip: str = ""
    refresh_visible: bool = True
    refresh_enabled: bool = True
    signin_visible: bool = False
    toggles_visible: bool = True
    pin_checked: bool = False
    pin_enabled: bool = True
    auto_checked: bool = True
    auto_enabled: bool = True
    auto_tip: str = ""
    chip_text: str = ""  # hero chip ("Beta: Public beta")


def _rel(ts: float) -> str:
    """relative_time() for use inside a sentence: "yesterday", "3 days ago", "13 Jun 2023"."""
    s = relative_time(ts)
    return s if s[:1].isdigit() else s[:1].lower() + s[1:]


def _join(*parts) -> str:
    return " · ".join(p for p in parts if p)


def _offer(v: VersionView, info, text: str, version: str = "", change: str = "update", lbl: str = "",
           tip: str = "") -> None:
    """Show the card's action button; a pinned game gets it as a secondary button (using it unpins)."""
    v.update_visible = True
    v.update_text = text
    v.update_version, v.update_change, v.update_label = version, change, lbl
    v.update_primary = not info.pinned
    v.update_tip = " ".join(t for t in (tip, PIN_TIP if info.pinned else "") if t)


def _staying(v: VersionView, mine: str) -> None:
    v.pill_text, v.pill_obj = f"Staying on {mine}", "Faint"
    v.newest_obj = "Faint"
    v.status_text = f"You're staying on {mine}, so Super Nova won't offer updates for this game."


def _builds_state(v: VersionView, info, store: str) -> None:
    inst, latest = info.installed, info.latest
    newest, mine = (latest.label if latest else ""), inst.label
    date = nice_date(latest.date) if latest else ""
    released = f"Released {date}" if date else ""
    st = info.status
    if st == "channel_ended" and not latest:  # neither the channel nor Stable has builds left
        v.pill_text, v.pill_obj = "Channel ended", "Warning"
        v.newest_text, v.newest_obj = "—", "Faint"
        v.status_text = (f"The {branch_label(inst.branch)} channel is no longer available from {store}. Use "
                         "Change version… to pick another channel.")
        v.status_obj = "Warning"
    elif not latest:  # nothing checked yet (or only an unusable saved summary)
        v.newest_text, v.newest_obj = "—", "Faint"
    elif st == "up_to_date":
        v.pill_text, v.pill_obj = "Up to date", "Success"
        v.newest_text = newest
        v.newest_sub = _join("You have the newest version", date and f"released {date}")
        if info.pinned:
            v.status_text = "You're on the newest version. Updates are paused while “Stay on this version” is on."
    elif st == "channel_ended":
        v.pill_text, v.pill_obj = "Channel ended", "Warning"
        v.newest_text = f"{newest} (Stable)"
        v.newest_sub = released
        v.status_text = (f"The {branch_label(inst.branch)} channel is no longer available from {store}. Switch to "
                         "Stable to keep getting updates. Stable may be older than your beta, so back up your saves "
                         "first.")
        v.status_obj = "Warning"
        _offer(v, info, f"Switch to Stable ({newest})", branch_target(None), "switch", f"Stable {newest}")
    elif st == "behind" or (st == "unlisted" and info.update_available):
        v.newest_text = newest
        v.newest_sub = released
        if info.pinned:
            _staying(v, mine)
        else:
            v.pill_text, v.pill_obj = "Update available", "Warning"
            v.newest_obj = "Warning"
            if info.newer_count > 1:
                v.newest_sub = _join(released, f"{info.newer_count} newer versions since yours")
        if st == "unlisted":
            v.status_text = f"{store} no longer lists the build you have (build {inst.id})."
        _offer(v, info, f"Update to {newest}", "", "update", newest)
    elif st == "unlisted":  # an imported copy: no way to tell whether it is older or newer
        v.newest_text = newest
        v.newest_sub = released
        v.status_text = ("Super Nova couldn't match this imported copy to a GOG build. Use Change version… to install "
                         "a specific version.")
    else:  # unknown: only a saved summary for another build
        v.newest_text, v.newest_obj = newest, "Faint"
        if info.checked_at:
            v.newest_sub = f"Last checked {_rel(info.checked_at)}"


def _installer_state(v: VersionView, info, store: str) -> None:
    inst, latest = info.installed, info.latest
    current = f"{store}'s current {'Linux' if info.platform == 'linux' else 'Windows'} installer"
    v.note_text = info.note
    v.change_visible = False
    if not latest:
        v.newest_text, v.newest_obj = "—", "Faint"
        return
    newest = latest.label
    download = f"Download installer {newest}" + (f" ({human_size(latest.size)})" if latest.size > 0 else "")
    v.newest_text = newest
    v.newest_sub = current
    if info.status == "up_to_date":
        v.pill_text, v.pill_obj = "Up to date", "Success"
        if info.pinned:
            v.status_text = "You're on the newest version. Updates are paused while “Stay on this version” is on."
    elif info.status == "different":
        v.newest_sub = f"{current} · differs from yours"
        if info.pinned:
            _staying(v, inst.label)
        else:
            v.pill_text, v.pill_obj = "New installer", "Warning"
            v.newest_obj = "Warning"
        _offer(v, info, download, "", "update", newest, INSTALLER_TIP)
    else:  # unknown: one of the two versions is missing
        v.status_text = ("Super Nova doesn't know which installer version this copy came from." if not inst.version
                         else f"{store} doesn't say which version its current installer is.")
        _offer(v, info, download, "", "update", newest, INSTALLER_TIP)
        v.update_primary = False


def describe_version(info, *, loading: bool = False, busy: str = "", global_auto: bool = True, store: str = "GOG",
                     multi_source: bool = False) -> VersionView:
    """What the version card shows for ``info`` (a ``base.VersionInfo``; None hides the card).

    ``loading``: a live check is running and ``info`` is the stale data to show meanwhile. ``busy``: "" |
    "running" (the game is running or starting) | "job" (it has an active download). ``global_auto``: the
    Settings › Downloads automatic-updates switch. ``store``: integration name used in the texts."""
    v = VersionView()
    if info is None:
        v.visible = False
        return v
    inst, latest = info.installed, info.latest
    builds = info.kind == "builds"
    v.title = f"{store} version" if multi_source else "Version"
    v.installed_text = inst.label
    if info.kind == "installer":
        first = f"{store} Linux installer" if info.platform == "linux" else f"{store} offline installer (Windows)"
    else:
        first = f"Build {inst.id}" if builds and inst.id else ""
    released = nice_date(inst.date)
    v.installed_sub = _join(first, released and f"released {released}",
                            info.updated_at and f"updated {_rel(info.updated_at)}", ORIGINS.get(info.origin, ""))
    others = [b for b in info.branches if b]
    if builds and inst.branch:
        v.channel_text = branch_label(inst.branch)
    elif builds and others:
        v.channel_text = f"Stable · {len(others)} beta channel{'s' if len(others) != 1 else ''} available"
    v.chip_text = f"Beta: {branch_label(inst.branch)}" if inst.branch else ""
    v.pin_checked = info.pinned
    v.pin_enabled = busy != "job"  # the job writes the pin when it ends
    v.auto_checked = info.auto_update
    v.auto_enabled = global_auto and not info.pinned
    if not global_auto:
        v.auto_tip = "Automatic updates are turned off for all games in Settings › Downloads."
    elif info.pinned:
        v.auto_tip = "Turn off “Stay on this version” to allow automatic updates."

    if info.kind not in ("builds", "installer"):
        v.status_text = "Super Nova can't manage versions for this copy."
        v.refresh_visible = v.toggles_visible = False
        return v
    if info.status == "not_installed":
        _not_installed_state(v, info, store, loading)
        tip = BUSY_TIPS.get(busy)
        if tip:
            v.update_enabled, v.update_tip = False, tip
        return v
    v.change_visible = builds
    if info.error == "signed_out":
        v.newest_text, v.newest_obj = (latest.label if latest else "—"), "Faint"
        if latest and info.checked_at:
            v.newest_sub = f"Last checked {_rel(info.checked_at)}"
        v.status_text = f"Sign in to {store} to check for updates and change versions."
        v.signin_visible = True
        v.refresh_visible = False
        v.change_enabled, v.change_tip = False, f"Sign in to {store} first"
    elif info.error and not loading:  # offline / failed; the refresh button retries
        why = f"Couldn't reach {store}" if info.error == "offline" else f"{store} returned an error"
        if latest:
            v.newest_text, v.newest_obj = latest.label, "Faint"
            v.newest_sub = f"{why} · last checked {_rel(info.checked_at)}" if info.checked_at else why
        else:
            v.newest_text, v.newest_obj = why, "Error"
        v.status_text, v.status_obj = info.error_text, "Error"
    else:
        if builds:
            _builds_state(v, info, store)
        else:
            _installer_state(v, info, store)
        if loading:  # stale data plus a spinner until the live check answers
            v.newest_loading = True
            v.refresh_enabled = False
            if not latest:
                v.newest_text, v.newest_obj, v.newest_sub = f"Checking {store} for the newest version…", "Faint", ""

    tip = BUSY_TIPS.get(busy)
    if tip:
        v.update_enabled = v.change_enabled = False
        v.update_tip = v.change_tip = tip
    return v


def _not_installed_state(v: VersionView, info, store: str, loading: bool) -> None:
    """A game that isn't installed: what Super Nova would install, and an Install button."""
    latest = info.latest
    v.installed_text, v.installed_sub, v.chip_text = "Not installed", "", ""
    v.toggles_visible = v.change_visible = False
    if info.kind == "installer":
        os_name = "Linux" if info.platform == "linux" else "Windows"
        sub = _join(f"{store}'s current {os_name} installer", latest and latest.size and human_size(latest.size))
    else:
        date = nice_date(latest.date) if latest else ""
        sub = _join(date and f"Released {date}", "Windows version, runs with Proton")
    if info.error == "signed_out":
        v.newest_text, v.newest_obj = "—", "Faint"
        v.status_text = f"Sign in to {store} to see the newest version."
        v.signin_visible, v.refresh_visible = True, False
        return
    if latest:
        v.newest_text, v.newest_sub = latest.label, sub
    elif loading:
        v.newest_text, v.newest_obj = f"Checking {store} for the newest version…", "Faint"
    elif info.error:
        v.newest_text, v.newest_obj = ("Couldn't reach " + store if info.error == "offline"
                                       else f"{store} returned an error"), "Error"
        v.status_text, v.status_obj = info.error_text, "Error"
    else:
        v.newest_text, v.newest_obj = "—", "Faint"
    v.newest_loading = loading
    v.refresh_enabled = not loading
    others = [b for b in info.branches if b]
    if others:
        v.channel_text = f"Stable · {len(others)} beta channel{'s' if len(others) != 1 else ''} available"
    v.update_visible = True
    v.update_text = f"Install {latest.label}…" if latest and latest.version else "Install…"
    v.update_change, v.update_primary = "install", True
    v.update_tip = "Choose the platform, language, DLC, version and folder"


def _plain(lb):
    lb.setTextFormat(Qt.TextFormat.PlainText)  # version and channel names come from the store API
    return lb


class VersionCard(QFrame):
    """Card at the top of the game page's Overview column; fill it with ``set_view(describe_version(...))``."""

    update_requested = Signal()
    change_requested = Signal()
    refresh_requested = Signal()
    signin_requested = Signal()
    pin_toggled = Signal(bool)
    auto_toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")  # keep it: the glass card background is styled on #Card
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        head = QHBoxLayout()
        ic = label()
        ic.setPixmap(icons.pixmap("history", 18, theme.TEXT_DIM))
        head.addWidget(ic)
        self.title_lbl = label("Version", "SectionTitle")
        head.addWidget(self.title_lbl, 1)
        self.pill = _plain(label("", "Faint"))
        head.addWidget(self.pill)
        self.refresh_btn = icon_button("refresh", "Check for updates")
        self.refresh_btn.clicked.connect(lambda: self.refresh_requested.emit())
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)
        self.cur_value = _plain(label(wrap=True))
        self.cur_value.setStyleSheet("font-weight:600;")
        self.cur_sub = _plain(label("", "Faint", wrap=True))
        cur = QVBoxLayout()
        cur.setSpacing(1)
        cur.addWidget(self.cur_value)
        cur.addWidget(self.cur_sub)
        self.channel_key = label("CHANNEL", "StatLabel")
        self.channel_value = _plain(label(wrap=True))
        self.new_spin = Spinner(14)
        self.new_spin.stop()
        self.new_value = _plain(label(wrap=True))
        self.new_sub = _plain(label("", "Faint", wrap=True))
        new_text = QVBoxLayout()
        new_text.setSpacing(1)
        new_text.addWidget(self.new_value)
        new_text.addWidget(self.new_sub)
        new = QHBoxLayout()
        new.setSpacing(6)
        new.addWidget(self.new_spin, 0, Qt.AlignmentFlag.AlignTop)
        new.addLayout(new_text, 1)
        self.newest_key = label("NEWEST", "StatLabel")
        self.newest_box = QWidget()
        self.newest_box.setLayout(new)
        new.setContentsMargins(0, 0, 0, 0)
        top = Qt.AlignmentFlag.AlignTop
        grid.addWidget(label("INSTALLED", "StatLabel"), 0, 0, top)
        grid.addLayout(cur, 0, 1)
        grid.addWidget(self.channel_key, 1, 0, top)
        grid.addWidget(self.channel_value, 1, 1)
        grid.addWidget(self.newest_key, 2, 0, top)
        grid.addWidget(self.newest_box, 2, 1)
        lay.addLayout(grid)

        self.status = _plain(label("", "Faint", wrap=True))
        lay.addWidget(self.status)
        self.note = _plain(label("", "Faint", wrap=True))
        lay.addWidget(self.note)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.update_btn = button("Update", "download", primary=True)
        self.update_btn.clicked.connect(lambda: self.update_requested.emit())
        self.change_btn = button("Change version…", "history")
        self.change_btn.clicked.connect(lambda: self.change_requested.emit())
        self.signin_btn = button("Sign in to GOG", obj="Link")
        self.signin_btn.clicked.connect(lambda: self.signin_requested.emit())
        row.addWidget(self.update_btn)
        row.addWidget(self.change_btn)
        row.addWidget(self.signin_btn)
        row.addStretch(1)
        lay.addLayout(row)
        self.auto_cb = QCheckBox("Update automatically")
        self.auto_cb.toggled.connect(lambda on: self.auto_toggled.emit(on))
        self.pin_cb = QCheckBox("Stay on this version (don't offer updates)")
        self.pin_cb.toggled.connect(lambda on: self.pin_toggled.emit(on))
        lay.addWidget(self.auto_cb)
        lay.addWidget(self.pin_cb)
        self.view: VersionView | None = None

    @staticmethod
    def _restyle(w, obj: str) -> None:
        if w.objectName() != obj:
            w.setObjectName(obj)
            w.style().unpolish(w)
            w.style().polish(w)

    @staticmethod
    def _text(lb, text: str) -> None:
        lb.setText(text)
        lb.setVisible(bool(text))

    def set_view(self, v: VersionView) -> None:
        self.view = v
        if not v.visible:
            self.hide()
            return
        self.title_lbl.setText(v.title)
        self._text(self.pill, v.pill_text)
        self._restyle(self.pill, v.pill_obj)
        self.cur_value.setText(v.installed_text)
        self._text(self.cur_sub, v.installed_sub)
        self.channel_value.setText(v.channel_text)
        self.channel_key.setVisible(bool(v.channel_text))
        self.channel_value.setVisible(bool(v.channel_text))
        self.new_value.setText(v.newest_text)
        self._restyle(self.new_value, v.newest_obj)
        self._text(self.new_sub, v.newest_sub)
        has_newest = bool(v.newest_text) or v.newest_loading
        self.newest_key.setVisible(has_newest)
        self.newest_box.setVisible(has_newest)
        if v.newest_loading:
            self.new_spin.start()
        else:
            self.new_spin.stop()
        self._text(self.status, v.status_text)
        self._restyle(self.status, v.status_obj)
        self._text(self.note, v.note_text)

        self.update_btn.setVisible(v.update_visible)
        self.update_btn.setText(v.update_text.replace("&", "&&"))  # no mnemonics from version names
        self.update_btn.setEnabled(v.update_enabled)
        self.update_btn.setToolTip(v.update_tip)
        if (self.update_btn.objectName() == "Primary") != v.update_primary:
            self._restyle(self.update_btn, "Primary" if v.update_primary else "")
            self.update_btn.setIcon(icons.icon("download", "white" if v.update_primary else theme.TEXT_DIM,
                                               "white" if v.update_primary else theme.TEXT))
        self.change_btn.setVisible(v.change_visible)
        self.change_btn.setEnabled(v.change_enabled)
        self.change_btn.setToolTip(v.change_tip)
        self.refresh_btn.setVisible(v.refresh_visible)
        self.refresh_btn.setEnabled(v.refresh_enabled)
        self.signin_btn.setVisible(v.signin_visible)

        for cb, checked, enabled, tip in ((self.auto_cb, v.auto_checked, v.auto_enabled, v.auto_tip),
                                          (self.pin_cb, v.pin_checked, v.pin_enabled, "")):
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.setEnabled(enabled)
            cb.blockSignals(False)
            cb.setToolTip(tip)
            cb.setVisible(v.toggles_visible)
