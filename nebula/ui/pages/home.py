"""Home / "Recent" page."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from nebula.core.utils import relative_time
from nebula.ui import icons, theme
from nebula.ui.pages.base import Page, scroll_page
from nebula.ui.widgets.common import Avatar, BarChart, button, card, clear_layout, label, status_color
from nebula.ui.widgets.gamegrid import CoverRow


class HomePage(Page):
    name = "Recent"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        lay = scroll_page(self)
        head = QHBoxLayout()
        head.addWidget(label("Recent", "PageTitle"))
        head.addStretch(1)
        lay.addLayout(head)
        self.greeting = label("", "Subtle")
        lay.addWidget(self.greeting)

        self.welcome = card()
        wl = QVBoxLayout(self.welcome)
        wl.setContentsMargins(24, 22, 24, 22)
        wl.setSpacing(10)
        wl.addWidget(label("Welcome to Nebula", "SectionTitle"))
        wl.addWidget(label("All your games in one place. Connect your accounts, or let Nebula find what is already "
                           "installed on this computer. Windows-only games run through Proton automatically.",
                           "Subtle", wrap=True))
        row = QHBoxLayout()
        for text, ic, fn in (("Sign in to GOG", "user", ctl.gog_login),
                             ("Connect Steam library", "link", ctl.steam_api),
                             ("Add a game", "plus", ctl.add_game),
                             ("Scan a folder", "folder", ctl.scan_folder),
                             ("Get GE-Proton", "proton", ctl.proton_download)):
            b = button(text, ic)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        wl.addLayout(row)
        lay.addWidget(self.welcome)

        self.sections: dict[str, tuple[QWidget, CoverRow]] = {}
        for key, title in (("continue", "Continue playing"), ("updates", "Updates available"),
                           ("added", "Recently added")):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(8)
            bl.addWidget(label(title, "SectionTitle"))
            row = CoverRow(services, 150 if key != "continue" else 170)
            row.open_entry.connect(lambda k: self.win.navigate("game", k))
            row.play_entry.connect(ctl.primary_action)
            row.context_entry.connect(lambda k, pos: ctl.entry_menu(k).exec(pos))
            bl.addWidget(row)
            lay.addWidget(box)
            self.sections[key] = (box, row)

        grid = QGridLayout()
        grid.setSpacing(16)
        # week chart
        week = card()
        wlay = QVBoxLayout(week)
        wlay.setContentsMargins(18, 16, 18, 16)
        wh = QHBoxLayout()
        wh.addWidget(label("Your week", "SectionTitle"))
        wh.addStretch(1)
        self.week_total = label("", "Subtle")
        wh.addWidget(self.week_total)
        wlay.addLayout(wh)
        self.week_chart = BarChart("h")
        wlay.addWidget(self.week_chart)
        grid.addWidget(week, 0, 0)
        # friends
        friends = card()
        flay = QVBoxLayout(friends)
        flay.setContentsMargins(18, 16, 18, 16)
        flay.addWidget(label("Friends playing now", "SectionTitle"))
        self.friends_box = QVBoxLayout()
        self.friends_box.setSpacing(8)
        flay.addLayout(self.friends_box)
        flay.addStretch(1)
        grid.addWidget(friends, 0, 1)
        # activity
        act = card()
        alay = QVBoxLayout(act)
        alay.setContentsMargins(18, 16, 18, 16)
        alay.addWidget(label("Activity", "SectionTitle"))
        self.activity_box = QVBoxLayout()
        self.activity_box.setSpacing(6)
        alay.addLayout(self.activity_box)
        alay.addStretch(1)
        grid.addWidget(act, 1, 0, 1, 2)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 2)
        lay.addLayout(grid)
        lay.addStretch(1)

        self._pending = False
        services.bus.library_changed.connect(self._schedule)
        services.bus.game_changed.connect(lambda _k: self._schedule())
        services.bus.game_stopped.connect(lambda *_: self._schedule())
        services.bus.friends_changed.connect(self._friends)
        services.bus.jobs_changed.connect(self._schedule)

    def _schedule(self) -> None:
        if not self._pending and self.isVisible():
            self._pending = True
            QTimer.singleShot(250, self.refresh)

    def on_show(self, arg=None) -> None:
        self.refresh()

    def refresh(self) -> None:
        self._pending = False
        lib = self.s.library
        recent = lib.recently_played(14)
        updates = [e for e in lib.all_entries() if e.update_available and not e.user.hidden]
        added = lib.recently_added(16)
        self.sections["continue"][1].set_entries(recent)
        self.sections["continue"][0].setVisible(bool(recent))
        self.sections["updates"][1].set_entries(updates)
        self.sections["updates"][0].setVisible(bool(updates))
        self.sections["added"][1].set_entries(added)
        self.sections["added"][0].setVisible(bool(added))
        self.welcome.setVisible(len(lib.entries) < 5)
        days = lib.daily_hours(7)
        self.week_chart.set_data(days)
        total = sum(h for _, h in days)
        self.week_total.setText(f"{total:.1f} h played" if total else "No play time this week")
        counts = lib.counts()
        self.greeting.setText(f"{counts.get('owned', 0)} games • {counts.get('installed', 0)} installed"
                              + (f" • {counts['updates']} update{'' if counts['updates'] == 1 else 's'}"
                                 if counts.get("updates") else ""))
        clear_layout(self.activity_box)
        rows = self.s.db.recent_activity(8)
        if not rows:
            self.activity_box.addWidget(label("Your play sessions, installs and updates will show up here.", "Faint"))
        for ts, kind, _uid, text in rows:
            r = QHBoxLayout()
            ic = label()
            ic.setPixmap(icons.pixmap({"session": "clock", "launch": "play", "install": "download",
                                       "update": "refresh", "uninstall": "trash"}.get(kind, "info"), 16))
            r.addWidget(ic)
            r.addWidget(label(text), 1)
            r.addWidget(label(relative_time(ts), "Faint"))
            self.activity_box.addLayout(r)
        self._friends()

    def _friends(self) -> None:
        clear_layout(self.friends_box)
        playing = [f for f in self.s.friends.friends if f.status == "playing"][:6]
        if not playing:
            if self.s.friends.friends:
                online = self.s.friends.online_count()
                text = f"{online} friend{'s' if online != 1 else ''} online • nobody is playing right now."
            else:
                text = "Connect GOG or a Steam Web API key to see your friends."
            self.friends_box.addWidget(label(text, "Faint", wrap=True))
            return
        for f in playing:
            r = QHBoxLayout()
            av = Avatar(30)
            av.set_avatar(f.avatar, f.name, status_color(f.status))
            r.addWidget(av)
            col = QVBoxLayout()
            col.setSpacing(0)
            col.addWidget(label(f.name))
            pl = label(f.playing, "Faint")
            pl.setStyleSheet(f"color:{theme.SUCCESS};")
            col.addWidget(pl)
            r.addLayout(col, 1)
            self.friends_box.addLayout(r)

