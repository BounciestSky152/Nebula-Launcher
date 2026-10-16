"""Statistics page."""
from __future__ import annotations

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout

from nebula.core.utils import playtime_text
from nebula.ui import theme
from nebula.ui.pages.base import Page, scroll_page
from nebula.ui.widgets.common import BarChart, HBarChart, card, label


def stat_card(title: str):
    c = card()
    lay = QVBoxLayout(c)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(2)
    lay.addWidget(label(title.upper(), "StatLabel"))
    v = label("—", "StatValue")
    v.setStyleSheet("font-size: 22pt; font-weight: 800;")
    lay.addWidget(v)
    sub = label("", "Faint")
    lay.addWidget(sub)
    return c, v, sub


class StatsPage(Page):
    name = "Statistics"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        lay = scroll_page(self)
        lay.addWidget(label("Statistics", "PageTitle"))
        row = QHBoxLayout()
        row.setSpacing(14)
        self.c_time = stat_card("Time played")
        self.c_owned = stat_card("Games")
        self.c_played = stat_card("Played")
        self.c_ach = stat_card("Achievements")
        for c, _, _ in (self.c_time, self.c_owned, self.c_played, self.c_ach):
            row.addWidget(c)
        lay.addLayout(row)
        grid = QGridLayout()
        grid.setSpacing(14)
        w = card()
        wl = QVBoxLayout(w)
        wl.setContentsMargins(18, 14, 18, 14)
        wl.addWidget(label("Hours played per week", "SectionTitle"))
        self.weeks = BarChart("h")
        wl.addWidget(self.weeks)
        grid.addWidget(w, 0, 0, 1, 2)
        d = card()
        dl = QVBoxLayout(d)
        dl.setContentsMargins(18, 14, 18, 14)
        dl.addWidget(label("Last 7 days", "SectionTitle"))
        self.days = BarChart("h")
        dl.addWidget(self.days)
        grid.addWidget(d, 0, 2)
        t = card()
        tl = QVBoxLayout(t)
        tl.setContentsMargins(18, 14, 18, 14)
        tl.addWidget(label("Most played", "SectionTitle"))
        self.top = HBarChart()
        tl.addWidget(self.top)
        self.top_empty = label("Play some games to see them here.", "Faint")
        tl.addWidget(self.top_empty)
        tl.addStretch(1)
        grid.addWidget(t, 1, 0, 1, 2)
        p = card()
        pl = QVBoxLayout(p)
        pl.setContentsMargins(18, 14, 18, 14)
        pl.addWidget(label("Games per platform", "SectionTitle"))
        self.platforms = HBarChart(theme.INFO)
        pl.addWidget(self.platforms)
        pl.addSpacing(10)
        pl.addWidget(label("Top genres", "SectionTitle"))
        self.genres = HBarChart(theme.WARNING)
        pl.addWidget(self.genres)
        pl.addStretch(1)
        grid.addWidget(p, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        lay.addStretch(1)

    def on_show(self, arg=None) -> None:
        st = self.s.library.stats()
        self.c_time[1].setText(f"{st['minutes'] // 60:,} h")
        self.c_time[2].setText(f"{st['minutes'] % 60} min • across every platform")
        self.c_owned[1].setText(f"{st['owned']:,}")
        self.c_owned[2].setText(f"{st['installed']} installed")
        self.c_played[1].setText(f"{st['played']:,}")
        pct = int(st["played"] * 100 / st["owned"]) if st["owned"] else 0
        self.c_played[2].setText(f"{pct}% of your library")
        u, t = st["achievements"]
        self.c_ach[1].setText(f"{u:,}")
        self.c_ach[2].setText(f"of {t:,} in games you've opened" if t else "Open a game's Achievements tab")
        self.weeks.set_data(st["weeks"])
        self.days.set_data(st["days"])
        top = [(title, m / 60, playtime_text(m)) for title, m, _k in st["top"]]
        self.top.set_data(top)
        self.top.setVisible(bool(top))
        self.top_empty.setVisible(not top)
        names = {k: i.name for k, i in self.s.integrations.items()}
        self.platforms.set_data([(names.get(k, k), v, str(v)) for k, v in
                                 sorted(st["per_source"].items(), key=lambda kv: kv[1], reverse=True)])
        self.genres.set_data([(g, v, str(v)) for g, v in st["genres"]])
