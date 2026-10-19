"""Game details page."""
from __future__ import annotations

import copy
import dataclasses
import time
from datetime import datetime

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QMenu, QProgressBar,
                               QPushButton, QScrollArea, QStackedWidget, QTabBar, QVBoxLayout, QWidget)

from nebula.core import tasks
from nebula.core.utils import eta_text, human_duration, human_size, human_speed, playtime_text, relative_time, xdg_open
from nebula.integrations import base
from nebula.integrations.gog.content import branch_label, nice_date
from nebula.ui import icons, theme
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import (FlowLayout, HeroBanner, PlatformBadges, RemoteImage, Spinner, StarRating,
                                      button, card, clear_layout, icon_button, label)
from nebula.ui.widgets.version_card import VersionCard, describe_version


def _rel(ts: float) -> str:
    """relative_time() inside a sentence: "yesterday", "3 days ago", "13 Jun 2023"."""
    s = relative_time(ts)
    return s if s[:1].isdigit() else s[:1].lower() + s[1:]


class ImageViewer(QDialog):
    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Screenshot")
        self.resize(1280, 760)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        img = RemoteImage(None, radius=0, mode="fit")
        lay.addWidget(img)
        img.set_source([url])

    def mousePressEvent(self, _e):  # noqa: N802
        self.accept()


class Stat(QWidget):
    def __init__(self, title: str):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 24, 0)
        lay.setSpacing(2)
        self.t = label(title, "StatLabel")
        self.v = label("—", "StatValue")
        self.sub = label("", "Faint")
        lay.addWidget(self.t)
        lay.addWidget(self.v)
        lay.addWidget(self.sub)
        self.bar = QProgressBar()
        self.bar.setObjectName("Achievements")
        self.bar.setFixedHeight(5)
        self.bar.setFixedWidth(140)
        self.bar.hide()
        lay.addWidget(self.bar)

    def set(self, value: str, sub: str = "") -> None:
        self.v.setText(value)
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))


class GamePage(Page):
    name = "Game"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.key = ""
        self.entry = None
        self._details_cache: dict[str, object] = {}
        self._ach_uid = ""
        # version card: the last live VersionInfo and the install signature (uid, build_id, version) it is for
        self._vinfo = None
        self._vsig = None
        self._vreq = None  # signature being loaded
        self._vloading = False
        self._vtoken = 0
        self._vat = 0.0  # monotonic time of the last live VersionInfo
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.area.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(self.area)
        content = QWidget()
        content.setObjectName("ContentArea")
        self.area.setWidget(content)
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 30)
        col.setSpacing(0)

        # ------------------------------------------------------------ hero
        self.hero = HeroBanner(400)
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(40, 30, 40, 26)
        hl.addStretch(1)
        self.logo = RemoteImage(QSize(420, 120), radius=0, mode="fit")
        self.logo.hide()
        hl.addWidget(self.logo)
        self.title = label("", "PageTitle", wrap=True)
        self.title.setStyleSheet("font-size: 28pt; font-weight: 800;")
        hl.addWidget(self.title)
        prow = QHBoxLayout()
        self.badges = PlatformBadges(18)
        prow.addWidget(self.badges)
        self.owned_on = label("", "Subtle")
        prow.addWidget(self.owned_on)
        prow.addSpacing(10)
        self.chips = QHBoxLayout()
        self.chips.setSpacing(6)
        prow.addLayout(self.chips)
        self.branch_chip = label("", "FeatureChip")  # "Beta: Public beta"; kept out of the rebuilt chip row
        self.branch_chip.setTextFormat(Qt.TextFormat.PlainText)
        self.branch_chip.hide()
        prow.addSpacing(6)
        prow.addWidget(self.branch_chip)
        prow.addStretch(1)
        hl.addLayout(prow)
        hl.addSpacing(14)

        actions = QHBoxLayout()
        actions.setSpacing(0)
        self.play = QPushButton("PLAY")
        self.play.setObjectName("PlayButton")
        self.play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play.clicked.connect(lambda: self.ctl.primary_action(self.key))
        self.play_menu_btn = QPushButton()
        self.play_menu_btn.setObjectName("PlayMenu")
        self.play_menu_btn.setIcon(icons.icon("chevron-down", "white"))
        self.play_menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_menu = QMenu(self)
        self.play_menu.aboutToShow.connect(self._build_play_menu)
        self.play_menu_btn.setMenu(self.play_menu)
        actions.addWidget(self.play)
        actions.addWidget(self.play_menu_btn)
        # progress box shown during installs/updates
        self.job_box = QFrame()
        jl = QHBoxLayout(self.job_box)
        jl.setContentsMargins(0, 0, 0, 0)
        jl.setSpacing(10)
        jcol = QVBoxLayout()
        jcol.setSpacing(4)
        self.job_text = label("", None)
        self.job_text.setStyleSheet("font-weight:600;")
        self.job_bar = QProgressBar()
        self.job_bar.setFixedWidth(320)
        self.job_bar.setRange(0, 1000)
        self.job_sub = label("", "Faint")
        jcol.addWidget(self.job_text)
        jcol.addWidget(self.job_bar)
        jcol.addWidget(self.job_sub)
        jl.addLayout(jcol)
        self.job_pause = icon_button("pause", "Pause")
        self.job_pause.clicked.connect(self._toggle_pause)
        self.job_cancel = icon_button("x", "Cancel")
        self.job_cancel.clicked.connect(self._cancel_job)
        jl.addWidget(self.job_pause)
        jl.addWidget(self.job_cancel)
        self.job_box.hide()
        actions.addWidget(self.job_box)
        self.prep = QWidget()
        pl = QHBoxLayout(self.prep)
        pl.setContentsMargins(14, 0, 0, 0)
        self.prep_spin = Spinner(18)
        self.prep_text = label("", "Subtle")
        pl.addWidget(self.prep_spin)
        pl.addWidget(self.prep_text)
        self.prep.hide()
        actions.addWidget(self.prep)
        actions.addSpacing(16)
        self.fav = icon_button("heart", "Favourite", checkable=True, size=20)
        self.fav.clicked.connect(lambda: self.ctl.toggle_favorite(self.key))
        actions.addWidget(self.fav)
        self.cfg_btn = icon_button("settings", "Game settings (Proton, launch options…)", size=20)
        self.cfg_btn.clicked.connect(self._open_settings)
        actions.addWidget(self.cfg_btn)
        self.more = icon_button("more", "More", size=20)
        self.more.clicked.connect(lambda: self.ctl.entry_menu(self.key, self).exec(
            self.more.mapToGlobal(self.more.rect().bottomLeft())))
        actions.addWidget(self.more)
        actions.addStretch(1)
        hl.addLayout(actions)
        col.addWidget(self.hero)

        # ----------------------------------------------------------- stats
        stats = QHBoxLayout()
        stats.setContentsMargins(40, 18, 40, 6)
        self.st_last = Stat("LAST PLAYED")
        self.st_time = Stat("TIME PLAYED")
        self.st_ach = Stat("ACHIEVEMENTS")
        self.st_run = Stat("RUNS WITH")
        self.st_size = Stat("SIZE")
        for s in (self.st_last, self.st_time, self.st_ach, self.st_run, self.st_size):
            stats.addWidget(s, 0, Qt.AlignmentFlag.AlignTop)
        stats.addStretch(1)
        rate = QVBoxLayout()
        rate.setSpacing(6)
        rate.addWidget(label("YOUR RATING", "StatLabel"))
        self.rating = StarRating(20)
        self.rating.changed.connect(lambda v: self.ctl.set_rating(self.key, v))
        rate.addWidget(self.rating)
        rate.addStretch(1)
        stats.addLayout(rate)
        col.addLayout(stats)

        # ------------------------------------------------------------ tabs
        self.tabs = QTabBar()
        self.tabs.setDrawBase(False)
        for t in ("Overview", "Achievements", "DLC && extras", "Activity", "Info"):
            self.tabs.addTab(t)
        self.tabs.currentChanged.connect(self._tab_changed)
        tl = QHBoxLayout()
        tl.setContentsMargins(32, 12, 40, 0)
        tl.addWidget(self.tabs)
        tl.addStretch(1)
        col.addLayout(tl)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER}; margin: 0 40px;")
        col.addWidget(sep)
        self.tab_stack = QStackedWidget()
        col.addWidget(self.tab_stack)
        self.overview = self._make_overview()
        self.ach_tab = self._make_achievements()
        self.dlc_tab = self._make_dlc()
        self.activity_tab = self._make_activity()
        self.info_tab = self._make_info()
        for w in (self.overview, self.ach_tab, self.dlc_tab, self.activity_tab, self.info_tab):
            self.tab_stack.addWidget(w)
        col.addStretch(1)

        bus = services.bus
        bus.game_changed.connect(lambda k: self._refresh_header() if k == self.key else None)
        bus.library_changed.connect(lambda: self._refresh_header() if self.key and self.isVisible() else None)
        bus.jobs_changed.connect(self._refresh_actions)
        bus.job_progress.connect(self._job_progress)
        bus.game_started.connect(lambda _u: self._refresh_actions())
        bus.game_stopped.connect(lambda *_: self._refresh_header())
        bus.game_preparing.connect(lambda *_: self._refresh_actions())
        bus.achievements_changed.connect(lambda _u: self._load_achievements(force=False) if self.isVisible() else None)
        bus.integration_changed.connect(lambda i: self._on_integration(i))

    # ------------------------------------------------------------ builders
    def _section(self) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(40, 20, 40, 10)
        lay.setSpacing(14)
        return w, lay

    def _make_overview(self) -> QWidget:
        w, lay = self._section()
        top = QHBoxLayout()
        top.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(12)
        self.desc = label("", wrap=True)
        self.desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.desc.setStyleSheet(f"color:{theme.TEXT_DIM}; line-height: 140%;")
        self.desc.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        left.addWidget(self.desc)
        self.more_desc = button("Show more", obj="Link")
        self.more_desc.clicked.connect(self._toggle_desc)
        left.addWidget(self.more_desc, 0, Qt.AlignmentFlag.AlignLeft)
        self.shots_title = label("Screenshots", "SectionTitle")
        left.addWidget(self.shots_title)
        self.shots_area = QScrollArea()
        self.shots_area.setFixedHeight(200)
        self.shots_area.setWidgetResizable(True)
        self.shots_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        shots = QWidget()
        self.shots = QHBoxLayout(shots)
        self.shots.setContentsMargins(0, 0, 0, 0)
        self.shots.setSpacing(10)
        self.shots_area.setWidget(shots)
        left.addWidget(self.shots_area)
        self.videos = QHBoxLayout()
        left.addLayout(self.videos)
        left.addStretch(1)
        top.addLayout(left, 3)
        right = QVBoxLayout()
        right.setSpacing(12)
        # games owned elsewhere: offer the DRM-free GOG version
        self.gog_offer = card()
        self.gog_offer.setObjectName("GogOffer")
        gl = QVBoxLayout(self.gog_offer)
        gl.setContentsMargins(16, 14, 16, 14)
        gl.setSpacing(8)
        gh = QHBoxLayout()
        gb = label()
        gb.setPixmap(icons.badge("G", theme.ACCENT, 22))
        gh.addWidget(gb)
        gh.addWidget(label("Also on GOG.COM", "SectionTitle"), 1)
        gh.addWidget(label("DRM-FREE", "DrmFree"))
        gl.addLayout(gh)
        self.gog_offer_text = label("", "Subtle", wrap=True)
        gl.addWidget(self.gog_offer_text)
        orow = QHBoxLayout()
        self.gog_offer_price = label("", "SectionTitle")
        orow.addWidget(self.gog_offer_price)
        orow.addStretch(1)
        self.gog_offer_btn = button("View on GOG", "store", primary=True)
        self.gog_offer_btn.clicked.connect(lambda: self._gog_item and self.win.navigate("product", self._gog_item))
        orow.addWidget(self.gog_offer_btn)
        gl.addLayout(orow)
        self._gog_item = None
        self.gog_offer.hide()
        right.addWidget(self.gog_offer)
        self.version_card = VersionCard()
        self.version_card.hide()
        vc = self.version_card
        vc.update_requested.connect(lambda: self._version_update())
        vc.change_requested.connect(lambda: self._version_change())
        vc.refresh_requested.connect(lambda: self._load_version(force=True))
        vc.signin_requested.connect(lambda: self.ctl.gog_login())
        vc.pin_toggled.connect(lambda on: self._version_pin(on))
        vc.auto_toggled.connect(lambda on: self._version_auto(on))
        right.addWidget(vc)
        tags_card = card()
        tcl = QVBoxLayout(tags_card)
        tcl.setContentsMargins(16, 14, 16, 14)
        th = QHBoxLayout()
        th.addWidget(label("Tags", "SectionTitle"))
        th.addStretch(1)
        et = icon_button("plus", "Edit tags")
        et.clicked.connect(lambda: self.ctl.edit_tags(self.key))
        th.addWidget(et)
        tcl.addLayout(th)
        self.tags_host = QWidget()
        self.tags_flow = FlowLayout(self.tags_host, 6)
        tcl.addWidget(self.tags_host)
        right.addWidget(tags_card)
        plat_card = card()
        self.plat_lay = QVBoxLayout(plat_card)
        self.plat_lay.setContentsMargins(16, 14, 16, 14)
        self.plat_lay.setSpacing(10)
        right.addWidget(plat_card)
        right.addStretch(1)
        top.addLayout(right, 2)
        lay.addLayout(top)
        return w

    def _make_achievements(self) -> QWidget:
        w, lay = self._section()
        head = QHBoxLayout()
        self.ach_summary = label("", "SectionTitle")
        head.addWidget(self.ach_summary)
        head.addStretch(1)
        self.ach_filter = QComboBox()
        for t in ("All", "Unlocked", "Locked"):
            self.ach_filter.addItem(t)
        self.ach_filter.currentIndexChanged.connect(lambda _i: self._render_achievements())
        self.ach_sort = QComboBox()
        for t in ("Default order", "Rarest first", "Recently unlocked"):
            self.ach_sort.addItem(t)
        self.ach_sort.currentIndexChanged.connect(lambda _i: self._render_achievements())
        head.addWidget(self.ach_filter)
        head.addWidget(self.ach_sort)
        refresh = icon_button("refresh", "Refresh achievements")
        refresh.clicked.connect(lambda: self._load_achievements(force=True))
        head.addWidget(refresh)
        lay.addLayout(head)
        self.ach_bar = QProgressBar()
        self.ach_bar.setObjectName("Achievements")
        self.ach_bar.setFixedHeight(6)
        lay.addWidget(self.ach_bar)
        self.ach_spin = Spinner(22)
        lay.addWidget(self.ach_spin)
        self.ach_list = QVBoxLayout()
        self.ach_list.setSpacing(6)
        lay.addLayout(self.ach_list)
        self._achievements = []
        return w

    def _make_dlc(self) -> QWidget:
        w, lay = self._section()
        head = QHBoxLayout()
        head.addWidget(label("Downloadable content", "SectionTitle"))
        head.addStretch(1)
        self.dlc_install = button("Install DLC…", "download")
        self.dlc_install.clicked.connect(lambda: self.ctl.manage_dlc(self._game_for(base.DLC)))
        head.addWidget(self.dlc_install)
        lay.addLayout(head)
        self.dlc_list = QVBoxLayout()
        self.dlc_list.setSpacing(6)
        lay.addLayout(self.dlc_list)
        return w

    def _make_activity(self) -> QWidget:
        w, lay = self._section()
        self.act_summary = label("", "SectionTitle")
        lay.addWidget(self.act_summary)
        self.act_list = QVBoxLayout()
        self.act_list.setSpacing(4)
        lay.addLayout(self.act_list)
        return w

    def _make_info(self) -> QWidget:
        w, lay = self._section()
        self.info_grid = QGridLayout()
        self.info_grid.setHorizontalSpacing(30)
        self.info_grid.setVerticalSpacing(10)
        lay.addLayout(self.info_grid)
        self.links_row = QHBoxLayout()
        lay.addLayout(self.links_row)
        self.changelog_title = label("Changelog", "SectionTitle")
        lay.addWidget(self.changelog_title)
        self.changelog = label("", wrap=True)
        self.changelog.setStyleSheet(f"color:{theme.TEXT_DIM};")
        lay.addWidget(self.changelog)
        lay.addStretch(1)  # the tab stack is as tall as the longest tab: keep the rows compact
        return w

    # -------------------------------------------------------------- data
    def on_show(self, arg=None) -> None:
        if arg:
            if arg != self.key:
                self.tabs.setCurrentIndex(0)
                self._achievements = []
                self._ach_uid = ""
                self._vinfo = self._vsig = self._vreq = None
                self._vloading = False
                self._vtoken += 1  # results still loading for the previous game are dropped
            self.key = arg
        self.area.verticalScrollBar().setValue(0)
        self._refresh_header()
        self._load_details()
        self._load_gog_offer()
        self._load_version()
        if self.tabs.currentIndex() == 1:
            self._load_achievements()

    def title(self) -> str:
        return self.entry.title if self.entry else "Game"

    def _game_for(self, cap: str):
        if not self.entry:
            return None
        for g in [self.entry.primary] + self.entry.games:
            if self.s.integration(g.source).supports(cap) and (g.installed or cap in (base.ACHIEVEMENTS, base.DLC)):
                return g
        return None

    def _refresh_header(self) -> None:
        e = self.s.library.entry(self.key)
        if e is None:
            return
        self.entry = e
        g = e.primary
        self.hero.set_source(e.hero_urls(), e.title)
        if g.logo:
            self.logo.set_source([g.logo], e.title)
            self.logo.show()
            self.title.setVisible(False)
        else:
            self.logo.hide()
            self.title.setVisible(True)
        self.title.setText(e.title)
        self.badges.set_sources(e.sources, self.s.integrations)
        names = [self.s.integrations[s].name for s in e.sources if s in self.s.integrations]
        self.owned_on.setText("  " + ", ".join(names))
        self._render_chips(e)
        self.fav.setChecked(e.user.favorite)
        self.fav.setIcon(icons.icon("heart", theme.DANGER if e.user.favorite else theme.TEXT_DIM))
        self.rating.set_value(e.user.rating)
        self.st_last.set(relative_time(e.last_played) if e.last_played else "Never")
        self.st_time.set(playtime_text(e.playtime_minutes) if e.playtime_minutes else "—",
                         f"{e.sessions} session{'s' if e.sessions != 1 else ''}" if e.sessions else "")
        u, t = e.achievements
        if t:
            self.st_ach.set(f"{u} / {t}")
            self.st_ach.bar.setRange(0, t)
            self.st_ach.bar.setValue(u)
            self.st_ach.bar.show()
        else:
            self.st_ach.set("—")
            self.st_ach.bar.hide()
        inst = self.ctl.installed_game(e)
        self.st_run.sub.setStyleSheet("")
        if inst:
            if inst.platform == "windows" and inst.source not in ("steam", "lutris"):
                cfg = self.s.db.get_config(inst.uid)
                if cfg.get("runner") == "native":
                    self.st_run.set("Native", "Compatibility layer disabled")
                else:
                    runner = self.s.compat.runner_for(inst.uid, cfg)
                    if runner is None:
                        self.st_run.set("Proton", "Not installed \u2014 click Play to get GE-Proton")
                        self.st_run.sub.setStyleSheet(f"color:{theme.WARNING}; font-size: 9pt;")
                    else:
                        self.st_run.set("Proton" if runner.kind == "proton" else "Wine", runner.name)
                        self.st_run.sub.setStyleSheet("")
            elif inst.platform == "linux":
                self.st_run.set("Linux", "Native build")
            else:
                self.st_run.set(self.s.integration(inst.source).name, "Platform client")
            ver = str(inst.version or "")
            self.st_size.set(human_size(inst.install_size) if inst.install_size else "—",
                             f"v{ver}" if ver and len(ver) < 24 else "")
        else:
            self.st_run.set("—")
            self.st_size.set("Not installed")
        self.cfg_btn.setVisible(bool(inst))
        self._refresh_actions()
        self._render_overview_local()
        self._render_activity()
        self._render_info()
        self._render_version()

    def _render_chips(self, e) -> None:
        clear_layout(self.chips)
        chips = []
        if "gog" in e.sources:
            chips.append(("DRM-FREE", "DrmFree"))
        if any("linux" in (g.os_support or []) or g.platform == "linux" for g in e.games):
            chips.append(("Linux native", "FeatureChip"))
        elif any("windows" in (g.os_support or []) or g.platform == "windows" for g in e.games):
            chips.append(("Windows • runs with Proton", "FeatureChip"))
        if e.achievements[1]:
            chips.append(("Achievements", "FeatureChip"))
        if "gog" in e.sources and self.s.integration("gog").supports(base.CLOUD_SAVES) and \
                any(g.source == "gog" and g.installed for g in e.games):
            chips.append(("Cloud saves", "FeatureChip"))
        dlc = max((g.extra.get("dlc_count", 0) or 0 for g in e.games), default=0)
        if dlc:
            chips.append((f"{dlc} DLC", "FeatureChip"))
        for text, obj in chips:
            self.chips.addWidget(label(text, obj))

    def _load_gog_offer(self) -> None:
        """For games owned elsewhere, look the title up on GOG and offer the DRM-free version."""
        e = self.entry
        gog = self.s.integrations.get("gog")
        self._gog_item = None
        self.gog_offer.hide()
        if not e or gog is None or "gog" in e.sources:
            return
        key, title = self.key, e.title

        def done(item) -> None:
            if not item or self.key != key:
                return
            self._gog_item = item
            price = item.get("price") or {}
            text = price.get("final") or ""
            if price.get("discount"):
                text = f"<span style='color:{theme.SUCCESS}'>{price['discount']}</span>  {text}"
            self.gog_offer_price.setText(text)
            self.gog_offer_text.setText(f"Get {item.get('title', title)} on GOG: no DRM, offline installers, "
                                        "cloud saves and roll-backs, installed right here.")
            self.gog_offer.show()

        tasks.run_async(gog.find_in_store, title, on_done=done, on_error=lambda _e: None)

    def _refresh_actions(self) -> None:
        e = self.entry
        if not e or not self.isVisible():
            return
        e = self.s.library.entry(self.key) or e
        state = self.ctl.game_state(e)
        job = next((self.s.jobs.active_for(g.uid) for g in e.games if self.s.jobs.active_for(g.uid)), None)
        self.job_box.setVisible(job is not None)
        self.play.setVisible(job is None)
        self.play_menu_btn.setVisible(job is None and state in ("installed", "update", "not_installed"))
        prep = self.s.launcher.entry_preparing(e)
        self.prep.setVisible(bool(prep))
        if prep:
            self.prep_text.setText(prep)
            self.prep_spin.start()
        else:
            self.prep_spin.stop()
        text, prop = {
            "running": ("PLAYING", "running"),
            "preparing": ("STARTING", "busy"),
            "update": ("UPDATE", "update"),
            "installed": ("PLAY", ""),
            "not_installed": ("INSTALL", ""),
            "job": ("", ""),
        }[state]
        if state == "not_installed" and not self.s.integration(e.primary.source).supports(base.INSTALL):
            text = "UNAVAILABLE"
        self.play.setText(text)
        self.play.setIcon(icons.icon({"running": "stop", "installed": "play", "update": "download",
                                      "not_installed": "download"}.get(state, "play"), "white"))
        self.play.setProperty("state", prop)
        self.play.style().unpolish(self.play)
        self.play.style().polish(self.play)
        if job:
            self._job_progress(job.id)
        self._render_version()  # busy state (running / downloading) follows jobs and launches

    def _job_progress(self, job_id: str) -> None:
        if not self.entry or not self.isVisible():
            return
        job = self.s.jobs.get(job_id)
        if not job or job.uid not in [g.uid for g in self.entry.games]:
            return
        verb = job.verb or {"install": "Installing", "update": "Updating", "repair": "Verifying",
                            "uninstall": "Uninstalling", "dlc": "Installing DLC"}.get(job.kind, job.kind.title())
        self.job_text.setText(f"{verb} — {job.stage}")
        self.job_bar.setValue(int(job.progress * 1000))
        parts = []
        if job.total:
            parts.append(f"{human_size(job.done)} of {human_size(job.total)}")
        if job.speed > 1024:
            parts.append(human_speed(job.speed))
            parts.append(f"ETA {eta_text(job.eta)}")
        if job.state == "paused":
            parts = ["Paused"] + parts[:1]
        self.job_sub.setText("   •   ".join(parts))
        self.job_pause.setVisible(job.can_pause)
        self.job_pause.setIcon(icons.icon("play" if job.state == "paused" else "pause"))

    def _toggle_pause(self) -> None:
        job = next((self.s.jobs.active_for(g.uid) for g in self.entry.games if self.s.jobs.active_for(g.uid)), None)
        if job:
            (self.s.jobs.resume if job.state == "paused" else self.s.jobs.pause)(job.id)

    def _cancel_job(self) -> None:
        job = next((self.s.jobs.active_for(g.uid) for g in self.entry.games if self.s.jobs.active_for(g.uid)), None)
        if job:
            self.s.jobs.cancel(job.id)

    def _open_settings(self) -> None:
        g = self.ctl.installed_game(self.entry) if self.entry else None
        if g:
            self.ctl.settings(g)
            self._refresh_header()

    def _build_play_menu(self) -> None:
        m = self.play_menu
        m.clear()
        e = self.entry
        if not e:
            return
        state = self.ctl.game_state(e)
        if state in ("installed", "update"):
            g = self.ctl.installed_game(e)
            integ = self.s.integration(g.source)
            if state == "update" and self.ctl.update_target(e) is not None:  # UPDATE updates rather than plays
                m.addAction(icons.icon("play"), "Play without updating", lambda: self.ctl.play(e))
            for t in integ.launch_tasks(g):
                if t.primary and state != "update":
                    continue
                if t.primary:
                    continue
                ic = {"document": "info", "url": "external", "tool": "wrench"}.get(t.kind, "play")
                m.addAction(icons.icon(ic), t.name, lambda t=t: self.ctl.play(e, g.uid, t.id))
            for other in e.games:
                if other.installed and other is not g:
                    m.addAction(icons.icon("play"), f"Play on {self.s.integration(other.source).name}",
                                lambda o=other: self.ctl.play(e, o.uid))
            vg = self._vgame()
            vi = self.s.integration(vg.source).version_info(vg, fetch=False) if vg else None
            if vi and vi.kind == "builds":
                m.addAction(icons.icon("history"), "Change version…", lambda: self._version_change())
            m.addSeparator()
            m.addAction(icons.icon("settings"), "Game settings…", lambda: self.ctl.settings(g))
        elif state == "not_installed":
            for g in e.games:
                integ = self.s.integration(g.source)
                if integ.supports(base.INSTALL):
                    m.addAction(icons.icon("download"), f"Install from {integ.name}", lambda g=g: self.ctl.install(g))
        if m.isEmpty():
            m.addAction("No other options").setEnabled(False)

    # ------------------------------------------------------------ version
    def _vgame(self):
        """The installed copy the version card is about (not ctl.installed_game, which may pick Steam's)."""
        e = (self.s.library.entry(self.key) or self.entry) if self.key else None
        if not e:
            return None
        for g in [e.primary] + e.games:
            if g.installed and self.s.integration(g.source).supports(base.VERSIONS):
                return g
        return None

    def _busy(self, g) -> str:
        if self.s.jobs.active_for(g.uid):
            return "job"
        if self.s.launcher.is_running(g.uid) or g.uid in self.s.launcher.preparing:
            return "running"
        return ""

    @staticmethod
    def _sig(g) -> tuple:
        return g.uid, g.build_id, g.version  # offline installs have no build id, so the version counts too

    def _cached_vinfo(self, g):
        """The last live check if it is about ``g``'s current install, with the pin / auto-update settings
        read again (the card's toggles change them without a new check)."""
        if self._vinfo is None or self._vsig != self._sig(g):
            return None
        cfg = self.s.db.get_config(g.uid)
        return dataclasses.replace(self._vinfo, pinned=bool(cfg.get("pin_version")),
                                   auto_update=cfg.get("auto_update", True) is not False)

    def _version_info(self, g):
        """What the page shows about ``g``'s version; never touches the network."""
        return self._cached_vinfo(g) or self.s.integration(g.source).version_info(g, fetch=False)

    def _fresh_vinfo(self):
        """The last live check if it is recent enough to open the change-version dialog with, else None."""
        g = self._vgame()
        info = self._cached_vinfo(g) if g else None
        if info is None or info.stale or info.error or time.monotonic() - self._vat >= 60:
            return None
        return info

    def _render_version(self) -> None:
        g = self._vgame()
        info = self._version_info(g) if g else None
        if info is None:
            self.version_card.hide()
            self.branch_chip.hide()
            return
        integ = self.s.integration(g.source)
        e = self.s.library.entry(self.key) or self.entry
        v = describe_version(info, loading=self._vloading, busy=self._busy(g),
                             global_auto=bool(self.s.settings.get("downloads.auto_update")), store=integ.name,
                             multi_source=len(e.sources) > 1)
        self.version_card.set_view(v)
        self.version_card.setVisible(v.visible)
        self.branch_chip.setText(v.chip_text)
        self.branch_chip.setVisible(bool(v.chip_text))
        sig = self._sig(g)
        if self.isVisible() and integ.connected() and self._vsig != sig and self._vreq != sig:
            self._load_version()  # e.g. an update or rollback finished: check the new install

    def _load_version(self, force: bool = False) -> None:
        """Check for the newest version in a worker; skipped while the last check is recent."""
        g = self._vgame()
        integ = self.s.integration(g.source) if g else None
        if g is None or not integ.connected() or self._version_info(g) is None:
            self._render_version()  # nothing to check, or signed out (the card offers to sign in)
            return
        sig = self._sig(g)
        recent = self._vsig == sig and self._vinfo is not None and not self._vinfo.error \
            and time.monotonic() - self._vat < 300
        if not force and (recent or self._vreq == sig):  # checked lately, or a check is running
            return
        self._vtoken += 1
        token, key = self._vtoken, self.key
        self._vreq, self._vloading = sig, True
        self._render_version()
        tasks.run_async(integ.version_info, copy.deepcopy(g), True, force,
                        on_done=lambda i, t=token, s=sig, k=key: self._version_loaded(t, s, k, i),
                        on_error=lambda e, t=token, s=sig, k=key: self._version_failed(t, s, k, e))

    def _version_loaded(self, token: int, sig: tuple, key: str, info) -> None:
        if token != self._vtoken or key != self.key:
            return
        self._vloading, self._vreq = False, None
        g = self._vgame()
        if g is None or self._sig(g) != sig:
            self._render_version()  # the install changed meanwhile: this checks the new one
            return
        self._vinfo, self._vsig, self._vat = info, sig, time.monotonic()
        if not self.s.actions.apply_version_info(g.uid, sig[1], info):
            self._render_version()
            self._render_info()
        # else the game changed (update flag / version name) and game_changed re-renders the page

    def _version_failed(self, token: int, sig: tuple, key: str, exc) -> None:
        if token != self._vtoken or key != self.key:
            return
        self._vloading, self._vreq = False, None
        g = self._vgame()
        if g is None or self._sig(g) != sig:
            self._render_version()
            return
        info = self.s.integration(g.source).version_info(g, fetch=False)
        if info is not None:
            info.error, info.error_text = "failed", f"Couldn't check for updates: {exc}"
        self._vinfo, self._vsig, self._vat = info, sig, time.monotonic()
        self._render_version()

    def _on_integration(self, integ_id: str) -> None:
        """Signed in or out: the card switches between the sign-in prompt and a live check."""
        g = self._vgame()
        if not self.isVisible() or g is None or g.source != integ_id:
            return
        self._vinfo = self._vsig = self._vreq = None
        self._vloading = False
        self._vtoken += 1
        self._load_version(force=True)
        self._refresh_header()

    def _version_update(self) -> None:
        g, v = self._vgame(), self.version_card.view
        if g is None or v is None or not v.update_visible:
            return
        job = self.s.actions.update(g, version=v.update_version, change=v.update_change, label=v.update_label)
        if job:
            self.win.toast(g.title, f"{job.verb} — added to the download queue")

    def _version_change(self) -> None:
        g = self._vgame()
        if g is not None:
            self.ctl.versions(g, self._fresh_vinfo())

    def _version_pin(self, on: bool) -> None:
        g = self._vgame()
        if g is None:
            return
        self.s.actions.set_version_pin(g, on)  # game_changed re-renders the card and the PLAY button
        if not on:
            self._load_version(force=True)  # whether an update is due again

    def _version_auto(self, on: bool) -> None:
        g = self._vgame()
        if g is None:
            return
        self.s.db.update_config(g.uid, auto_update=on)
        self._render_version()
        self._render_info()

    def _version_rows(self, g, info, other: bool) -> list[tuple[str, str]]:
        """Info tab rows about the installed version (``other``: ``g`` isn't the copy the page shows first)."""
        name = self.s.integration(g.source).name
        inst, latest, builds = info.installed, info.latest, info.kind == "builds"
        mine = inst.label
        newest = ""
        if latest is not None:
            if info.kind == "installer":
                newest = f"{latest.label} ({name}'s current installer)"
            elif info.status == "up_to_date":
                newest = f"{latest.label} (installed)"
            elif info.branch_gone:
                newest = f"{latest.label} (Stable)"
            else:
                newest = latest.label
            if info.stale and info.checked_at:
                newest += f" — last checked {_rel(info.checked_at)}"
        if info.kind not in ("builds", "installer"):
            updates = ""
        elif info.pinned:
            updates = f"Off — staying on {mine}"
        elif not info.auto_update:
            updates = "Manual (you'll be notified)"
        elif not self.s.settings.get("downloads.auto_update"):
            updates = "Manual (automatic updates are off in Settings › Downloads)"
        else:
            updates = "Automatic"
        return [
            (f"{name} version" if other else "Version",
             f"{mine} (build {inst.id})" if builds and inst.id and inst.version else mine),
            ("Channel", branch_label(inst.branch) if builds else ""),
            ("Version released", nice_date(inst.date)),
            ("Last updated", datetime.fromtimestamp(info.updated_at).strftime("%d %b %Y") if info.updated_at else ""),
            ("Newest version", newest),
            ("Installed with", info.method),
            ("Updates", updates),
        ]

    # ---------------------------------------------------------- overview
    def _toggle_desc(self) -> None:
        expanded = self.desc.maximumHeight() > 10000
        self.desc.setMaximumHeight(140 if expanded else 16777215)
        self.more_desc.setText("Show more" if expanded else "Show less")

    def _render_overview_local(self) -> None:
        e = self.entry
        g = e.primary
        desc = next((x.description for x in e.games if x.description), "")
        details = self._details_cache.get(g.uid)
        if details is not None and getattr(details, "description", ""):
            desc = details.description
        self.desc.setText(desc or "No description available.")
        self.desc.setMaximumHeight(140)
        self.more_desc.setVisible(len(desc) > 420)
        clear_layout(self.tags_flow)
        for t in e.user.tags:
            b = button("#" + t, obj="Chip")
            b.clicked.connect(lambda _c=False, t=t: self.win.navigate("library", {"title": f"#{t}",
                                                                                   "filter": {"tags": [t]}}))
            self.tags_flow.addWidget(b)
        if not e.user.tags:
            self.tags_flow.addWidget(label("No tags — use + to organise this game.", "Faint"))
        clear_layout(self.plat_lay)
        self.plat_lay.addWidget(label("Platforms", "SectionTitle"))
        for game in e.games:
            integ = self.s.integration(game.source)
            row = QHBoxLayout()
            b = label()
            from nebula.ui.icons import badge

            b.setPixmap(badge(integ.letter, integ.color, 20))
            row.addWidget(b)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(integ.name))
            if game.installed:
                sub = {"windows": "Windows build via Proton", "linux": "Native Linux build"}.get(game.platform, "Installed")
                if game.source in ("steam", "lutris"):
                    sub = f"Installed in {integ.name}"
            else:
                sub = "Not installed"
            col.addWidget(label(sub, "Faint"))
            row.addLayout(col, 1)
            if game.installed:
                pb = button("Play")
                pb.clicked.connect(lambda _c=False, gg=game: self.ctl.play(e, gg.uid))
            elif integ.supports(base.INSTALL):
                pb = button("Install")
                pb.clicked.connect(lambda _c=False, gg=game: self.ctl.install(gg))
            else:
                pb = None
            if pb:
                row.addWidget(pb)
            self.plat_lay.addLayout(row)
        shots = (details.screenshots if details is not None else []) or g.extra.get("screenshots", [])
        self._render_shots(shots, getattr(details, "videos", []) or g.extra.get("movies", []))

    def _render_shots(self, shots: list, videos: list) -> None:
        clear_layout(self.shots)
        for url in shots[:12]:
            img = RemoteImage(QSize(320, 180), radius=6)
            img.set_source([url])
            img.setCursor(Qt.CursorShape.PointingHandCursor)
            img.mousePressEvent = lambda _e, u=url: ImageViewer(u, self).exec()
            self.shots.addWidget(img)
        self.shots.addStretch(1)
        self.shots_area.setVisible(bool(shots))
        self.shots_title.setVisible(bool(shots))
        clear_layout(self.videos)
        for thumb, url in videos[:4]:
            if url:
                b = button("Watch trailer", "play")
                b.clicked.connect(lambda _c=False, u=url: xdg_open(u))
                self.videos.addWidget(b)
        self.videos.addStretch(1)

    def _load_details(self) -> None:
        e = self.entry
        if not e:
            return
        g = e.primary
        if g.uid in self._details_cache:
            self._apply_details(g.uid, self._details_cache[g.uid])
            return
        integ = self.s.integration(g.source)
        uid = g.uid
        tasks.run_async(integ.details, g, on_done=lambda d: self._apply_details(uid, d))

    def _apply_details(self, uid: str, details) -> None:
        self._details_cache[uid] = details
        if not self.entry or self.entry.primary.uid != uid:
            return
        self._render_overview_local()
        self._render_dlc(details)
        self._render_info()
        # details may have discovered a background image (e.g. GOG product artwork)
        self.hero.set_source(self.entry.hero_urls(), self.entry.title)

    # ------------------------------------------------------- achievements
    def _tab_changed(self, i: int) -> None:
        self.tab_stack.setCurrentIndex(i)
        if i == 1:
            self._load_achievements()

    def _load_achievements(self, force: bool = False) -> None:
        g = self._game_for(base.ACHIEVEMENTS)
        if g is None:
            self.ach_summary.setText("Achievements are not available for this game")
            self.ach_bar.hide()
            clear_layout(self.ach_list)
            return
        cached = self.s.achievements.cached(g.uid)
        if cached is not None and not force:
            self._achievements = cached
            self._ach_uid = g.uid
            self._render_achievements()
        if force or cached is None or self._ach_uid != g.uid:
            self.ach_spin.start()
            uid = g.uid
            tasks.run_async(self.s.achievements.fetch, uid, force,
                            on_done=lambda items: self._got_achievements(uid, items),
                            on_error=lambda _e: self.ach_spin.stop())

    def _got_achievements(self, uid: str, items) -> None:
        self.ach_spin.stop()
        self._ach_uid = uid
        self._achievements = items or []
        self.s.library.rebuild()
        self._render_achievements()

    def _render_achievements(self) -> None:
        clear_layout(self.ach_list)
        items = list(self._achievements)
        if not items:
            g = self._game_for(base.ACHIEVEMENTS)
            msg = "This game has no achievements." if g else "Achievements are not available for this game"
            if g and g.source == "steam" and not self.s.settings.get("steam.api_key"):
                msg = "Add a Steam Web API key (Settings › Integrations) to see Steam achievements."
            if g and g.source == "gog" and not self.s.integration("gog").connected():
                msg = "Sign in to GOG to see achievements."
            self.ach_summary.setText(msg)
            self.ach_bar.hide()
            return
        unlocked = sum(1 for a in items if a.unlocked)
        self.ach_summary.setText(f"{unlocked} of {len(items)} unlocked  ({int(unlocked * 100 / len(items))}%)")
        self.ach_bar.show()
        self.ach_bar.setRange(0, len(items))
        self.ach_bar.setValue(unlocked)
        f = self.ach_filter.currentIndex()
        if f == 1:
            items = [a for a in items if a.unlocked]
        elif f == 2:
            items = [a for a in items if not a.unlocked]
        s = self.ach_sort.currentIndex()
        if s == 1:
            items.sort(key=lambda a: a.rarity if a.rarity >= 0 else 101)
        elif s == 2:
            items.sort(key=lambda a: a.unlocked_at, reverse=True)
        for a in items[:300]:
            row = card()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 8, 14, 8)
            img = RemoteImage(QSize(48, 48), radius=6)
            img.set_source([a.icon if a.unlocked else (a.icon_locked or a.icon)], a.name)
            rl.addWidget(img)
            col = QVBoxLayout()
            col.setSpacing(2)
            name = label(a.name if not (a.hidden and not a.unlocked) else "Hidden achievement")
            name.setStyleSheet("font-weight:600;" + ("" if a.unlocked else f"color:{theme.TEXT_DIM};"))
            col.addWidget(name)
            desc = a.description if not (a.hidden and not a.unlocked) else "Keep playing to discover it."
            col.addWidget(label(desc, "Faint", wrap=True))
            rl.addLayout(col, 1)
            right = QVBoxLayout()
            right.setSpacing(2)
            if a.unlocked:
                right.addWidget(label(datetime.fromtimestamp(a.unlocked_at).strftime("%d %b %Y") if a.unlocked_at
                                      else "Unlocked", "Success"), 0, Qt.AlignmentFlag.AlignRight)
            if a.rarity >= 0:
                rare = label(f"{a.rarity:.1f}% of players", "Faint")
                right.addWidget(rare, 0, Qt.AlignmentFlag.AlignRight)
            rl.addLayout(right)
            self.ach_list.addWidget(row)

    # ---------------------------------------------------------------- dlc
    def _render_dlc(self, details) -> None:
        clear_layout(self.dlc_list)
        dlcs = getattr(details, "dlcs", []) or []
        g = self._game_for(base.DLC)
        self.dlc_install.setVisible(bool(g and g.installed and any(d.get("owned") and not d.get("installed")
                                                                     for d in dlcs)))
        if not dlcs:
            self.dlc_list.addWidget(label("No DLC for this game.", "Faint"))
            return
        for d in dlcs:
            row = card()
            rl = QHBoxLayout(row)
            rl.setContentsMargins(10, 8, 14, 8)
            img = RemoteImage(QSize(96, 54), radius=4)
            img.set_source([d.get("cover", "")], d["title"])
            rl.addWidget(img)
            rl.addWidget(label(d["title"]), 1)
            state = "Installed" if d.get("installed") else ("Owned" if d.get("owned") else "Not owned")
            st = label(state, "Success" if d.get("installed") else ("Subtle" if d.get("owned") else "Faint"))
            rl.addWidget(st)
            if not d.get("owned") and d.get("url"):
                b = button("Store", "external")
                b.clicked.connect(lambda _c=False, u=d["url"]: xdg_open(u))
                rl.addWidget(b)
            self.dlc_list.addWidget(row)

    # ----------------------------------------------------------- activity
    def _render_activity(self) -> None:
        clear_layout(self.act_list)
        rows = self.s.db.sessions_for(self.key, 60)
        e = self.entry
        self.act_summary.setText(f"{playtime_text(e.playtime_minutes)} in total" if e.playtime_minutes
                                 else "You haven't played this game yet")
        for uid, start, end, secs in rows:
            r = QHBoxLayout()
            g = self.s.library.game(uid)
            integ = self.s.integrations.get(g.source) if g else None
            if integ:
                b = label()
                b.setPixmap(icons.badge(integ.letter, integ.color, 14))
                r.addWidget(b)
            r.addWidget(label(datetime.fromtimestamp(start).strftime("%a %d %b %Y, %H:%M")), 1)
            r.addWidget(label(human_duration(secs), "Subtle"))
            self.act_list.addLayout(r)
        if not rows and e.playtime_minutes:
            self.act_list.addWidget(label("Play time was imported from your platforms; sessions played through Nebula "
                                          "are listed here.", "Faint", wrap=True))

    # --------------------------------------------------------------- info
    def _render_info(self) -> None:
        clear_layout(self.info_grid)
        e = self.entry
        g = e.primary
        details = self._details_cache.get(g.uid)
        inst = self.ctl.installed_game(e)
        rows = [
            ("Developer", next((x.developer for x in e.games if x.developer), "") or getattr(details, "developer", "")),
            ("Publisher", next((x.publisher for x in e.games if x.publisher), "") or getattr(details, "publisher", "")),
            ("Release date", e.release_date),
            ("Genres", ", ".join(e.genres)),
            ("Systems", ", ".join(sorted({o.capitalize() for x in e.games for o in x.os_support}))),
            ("Critics score", f"{max(x.critic_score for x in e.games):.0f}/100" if max(
                (x.critic_score for x in e.games), default=0) else ""),
            ("Added", datetime.fromtimestamp(e.added_at).strftime("%d %b %Y") if e.added_at else ""),
        ]
        if inst:
            rows.append(("Install location", inst.install_path))
            vg = self._vgame()
            info = self._version_info(vg) if vg else None
            if info is not None:
                rows += self._version_rows(vg, info, other=vg is not inst)
            else:
                rows.append(("Version", str(inst.version or inst.build_id or "")))
            if inst.platform == "windows" and inst.source not in ("steam", "lutris"):
                rows.append(("Proton prefix", str(self.s.compat.prefix_for(inst.uid))))
        if details is not None and getattr(details, "languages", None):
            rows.append(("Languages", ", ".join(details.languages[:12])))
        r = 0
        for k, v in rows:
            if not v:
                continue
            self.info_grid.addWidget(label(k.upper(), "StatLabel"), r, 0, Qt.AlignmentFlag.AlignTop)
            val = label(str(v), wrap=True, selectable=True)
            val.setTextFormat(Qt.TextFormat.PlainText)  # names and versions come from store APIs
            self.info_grid.addWidget(val, r, 1, Qt.AlignmentFlag.AlignTop)
            r += 1
        self.info_grid.setColumnStretch(1, 1)
        clear_layout(self.links_row)
        links = dict(getattr(details, "links", {}) or {})
        for gg in e.games:
            url = self.s.integration(gg.source).store_url(gg)
            if url:
                links.setdefault(f"{self.s.integration(gg.source).name} page", url)
        for name, url in list(links.items())[:6]:
            b = button(name, "external")
            b.clicked.connect(lambda _c=False, u=url: xdg_open(u))
            self.links_row.addWidget(b)
        self.links_row.addStretch(1)
        cl = getattr(details, "changelog", "") if details is not None else ""
        self.changelog_title.setVisible(bool(cl))
        self.changelog.setVisible(bool(cl))
        self.changelog.setText(cl[:6000])
