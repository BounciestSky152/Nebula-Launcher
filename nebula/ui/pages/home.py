"""Home: GOG-centred landing page (featured deal, your games, GOG sales, wishlist, activity)."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from nebula.core import tasks
from nebula.core.utils import relative_time
from nebula.ui import icons, theme
from nebula.ui.pages.base import Page, scroll_page
from nebula.ui.pages.store import StoreRow
from nebula.ui.widgets.common import (Avatar, BarChart, HeroBanner, button, card, clear_layout, label,
                                      status_color)
from nebula.ui.widgets.gamegrid import CoverRow


class HomePage(Page):
    name = "Home"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.gog = services.integrations.get("gog")
        self.owned: set[str] = set()
        self.wishlist: set[str] = set()
        self._store_loaded = False
        lay = scroll_page(self)
        head = QHBoxLayout()
        self.title = label("Home", "PageTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        lay.addLayout(head)
        self.greeting = label("", "Subtle")
        lay.addWidget(self.greeting)

        # signed-out: GOG comes first
        self.signin = card()
        sl = QHBoxLayout(self.signin)
        sl.setContentsMargins(24, 20, 24, 20)
        sl.setSpacing(18)
        badge = label()
        badge.setPixmap(icons.badge("G", theme.ACCENT, 46))
        sl.addWidget(badge)
        col = QVBoxLayout()
        col.addWidget(label("Bring your GOG library", "SectionTitle"))
        col.addWidget(label("Sign in with GOG to see your games, cloud saves, achievements and friends. "
                            "DRM-free installs, updates and roll-backs all happen right here.", "Subtle", wrap=True))
        sl.addLayout(col, 1)
        b = button("Sign in with GOG", primary=True)
        b.clicked.connect(ctl.gog_login)
        sl.addWidget(b)
        lay.addWidget(self.signin)

        # featured GOG deal
        self.featured = card()
        self.featured.setFixedHeight(250)
        fl = QVBoxLayout(self.featured)
        fl.setContentsMargins(0, 0, 0, 0)
        self.feat_img = HeroBanner(250)
        fl.addWidget(self.feat_img)
        ol = QVBoxLayout(self.feat_img)
        ol.setContentsMargins(28, 22, 28, 22)
        tag = label("FEATURED ON GOG.COM", "BrandSub")
        ol.addWidget(tag)
        ol.addStretch(1)
        self.feat_title = label("", "PageTitle")
        self.feat_title.setStyleSheet("font-size: 24pt; font-weight: 800; background: transparent;")
        ol.addWidget(self.feat_title)
        self.feat_sub = label("", "Subtle")
        self.feat_sub.setStyleSheet("background: transparent;")
        ol.addWidget(self.feat_sub)
        frow = QHBoxLayout()
        self.feat_price = label("", "SectionTitle")
        self.feat_price.setStyleSheet("background: transparent;")
        frow.addWidget(self.feat_price)
        frow.addSpacing(10)
        self.feat_btn = button("View in store", "store", primary=True)
        self.feat_btn.clicked.connect(lambda: self._feat_item and self.win.navigate("product", self._feat_item))
        frow.addWidget(self.feat_btn)
        frow.addStretch(1)
        ol.addLayout(frow)
        self._feat_item = None
        self.featured.hide()
        lay.addWidget(self.featured)

        self.sections: dict[str, tuple[QWidget, object]] = {}

        def section(key: str, title: str, widget, link: tuple[str, str, object] | None = None):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(8)
            hl = QHBoxLayout()
            hl.addWidget(label(title, "SectionTitle"))
            hl.addStretch(1)
            if link:
                lk = button(link[0], obj="Link")
                lk.clicked.connect(lambda _c=False, l=link: self.win.navigate(l[1], l[2]))
                hl.addWidget(lk)
            bl.addLayout(hl)
            bl.addWidget(widget)
            lay.addWidget(box)
            self.sections[key] = (box, widget)

        def cover_row(width: int) -> CoverRow:
            row = CoverRow(services, width)
            row.open_entry.connect(lambda k: self.win.navigate("game", k))
            row.play_entry.connect(ctl.primary_action)
            row.context_entry.connect(lambda k, pos: ctl.entry_menu(k).exec(pos))
            return row

        section("continue", "Continue playing", cover_row(170), ("All games", "library", "owned"))
        sale = StoreRow(self)
        sale.open_item.connect(lambda it: self.win.navigate("product", it))
        section("sale", "On sale on GOG", sale, ("Browse the store", "store", None))
        wish = StoreRow(self)
        wish.open_item.connect(lambda it: self.win.navigate("product", it))
        section("wish", "From your wishlist", wish, ("Wishlist", "wishlist", None))
        section("updates", "Updates available", cover_row(150), ("Show all", "library", "updates"))
        section("gog", "Your GOG games", cover_row(150), ("My GOG games", "library", "source:gog"))
        section("added", "Recently added", cover_row(150))

        grid = QGridLayout()
        grid.setSpacing(16)
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
        friends = card()
        flay = QVBoxLayout(friends)
        flay.setContentsMargins(18, 16, 18, 16)
        flay.addWidget(label("Friends playing now", "SectionTitle"))
        self.friends_box = QVBoxLayout()
        self.friends_box.setSpacing(8)
        flay.addLayout(self.friends_box)
        flay.addStretch(1)
        grid.addWidget(friends, 0, 1)
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
        bus = services.bus
        bus.library_changed.connect(self._schedule)
        bus.game_changed.connect(lambda _k: self._schedule())
        bus.game_stopped.connect(lambda *_: self._schedule())
        bus.friends_changed.connect(self._friends)
        bus.jobs_changed.connect(self._schedule)
        bus.integration_changed.connect(lambda i: (setattr(self, "_store_loaded", False), self._schedule())
                                        if i == "gog" else None)

    def _schedule(self) -> None:
        if not self._pending and self.isVisible():
            self._pending = True
            QTimer.singleShot(250, self.refresh)

    def on_show(self, arg=None) -> None:
        self.refresh()

    # -------------------------------------------------------------- data
    def refresh(self) -> None:
        self._pending = False
        lib = self.s.library
        signed_in = bool(self.gog and self.gog.connected())
        self.signin.setVisible(not signed_in)
        name = self.gog.account_name() if signed_in else ""
        self.title.setText(f"Welcome back, {name}" if name else "Home")
        counts = lib.counts()
        upd = counts.get("updates", 0)
        self.greeting.setText(f"{counts.get('gog', 0)} GOG games • {counts.get('owned', 0)} games in total "
                              f"• {counts.get('installed', 0)} installed"
                              + (f" • {upd} update{'' if upd == 1 else 's'}" if upd else ""))
        recent = lib.recently_played(14)
        updates = [e for e in lib.all_entries() if e.update_available and not e.user.hidden]
        gog_games = sorted([e for e in lib.all_entries() if "gog" in e.sources and not e.user.hidden],
                           key=lambda e: (not e.installed, -e.last_played, e.title.lower()))[:16]
        for key, items in (("continue", recent), ("updates", updates), ("gog", gog_games),
                           ("added", lib.recently_added(16))):
            box, row = self.sections[key]
            row.set_entries(items)
            box.setVisible(bool(items))
        if not self._store_loaded:
            self._load_store()
        days = lib.daily_hours(7)
        self.week_chart.set_data(days)
        total = sum(h for _, h in days)
        self.week_total.setText(f"{total:.1f} h played" if total else "No play time this week")
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

    def _load_store(self) -> None:
        if not self.gog:
            return
        self._store_loaded = True
        gog = self.gog

        def work():
            owned = gog.owned_ids() if gog.connected() else set()
            wish = gog.wishlist() if gog.connected() else set()
            sale = gog.catalog(discounted=True, order="desc:trending", limit=16).get("products", [])
            wished = []
            if wish:
                try:
                    wished = gog.catalog(product_ids=sorted(wish)[:50], limit=50).get("products", [])
                except Exception:  # noqa: BLE001
                    wished = []
            return owned, wish, sale, wished

        def done(res):
            self.owned, self.wishlist, sale, wished = res
            for key, items in (("sale", sale), ("wish", wished)):
                box, row = self.sections[key]
                row.model_.set_items(items)
                box.setVisible(bool(items))
            feat = next((p for p in sale if str(p.get("id")) not in self.owned and p.get("coverHorizontal")), None)
            self._show_featured(feat)

        def failed(_exc):
            self._store_loaded = False
            for key in ("sale", "wish"):
                self.sections[key][0].hide()
            self.featured.hide()

        for key in ("sale", "wish"):
            self.sections[key][0].hide()
        tasks.run_async(work, on_done=done, on_error=failed)

    def _show_featured(self, item) -> None:
        self._feat_item = item
        self.featured.setVisible(bool(item))
        if not item:
            return
        self.feat_img.set_source([item.get("coverHorizontal", "")], item.get("title", ""))
        self.feat_title.setText(item.get("title", ""))
        self.feat_sub.setText(", ".join(item.get("developers", []) or []))
        price = item.get("price") or {}
        text = price.get("final") or ""
        if price.get("discount"):
            text = f"<span style='color:{theme.SUCCESS}'>{price['discount']}</span>  {text}"
        self.feat_price.setText(text)

    def _friends(self) -> None:
        clear_layout(self.friends_box)
        playing = [f for f in self.s.friends.friends if f.status == "playing"][:6]
        if not playing:
            if self.s.friends.friends:
                online = self.s.friends.online_count()
                text = f"{online} friend{'s' if online != 1 else ''} online • nobody is playing right now."
            else:
                text = "Sign in with GOG to see your friends here."
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

