"""Friends side panel (Galaxy's right-hand friends list)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from nebula.core.utils import relative_time, xdg_open
from nebula.ui import icons, theme
from nebula.ui.widgets.common import Avatar, Spinner, clear_layout, icon_button, label, status_color

STATUS_TEXT = {"online": "Online", "away": "Away", "busy": "Busy", "offline": "Offline", "unknown": ""}


class FriendRow(QFrame):
    def __init__(self, f, integ):
        super().__init__()
        self.f = f
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Open {f.name}'s profile")
        self.setStyleSheet(f"FriendRow:hover {{ background: {theme.SURFACE}; border-radius: 6px; }}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 5, 8, 5)
        av = Avatar(32)
        av.set_avatar(f.avatar, f.name, status_color(f.status) if f.status != "unknown" else None)
        lay.addWidget(av)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(label(f.name))
        if f.status == "playing":
            sub = label(f.playing, "Faint")
            sub.setStyleSheet(f"color:{theme.SUCCESS}; font-size: 8pt;")
        else:
            text = STATUS_TEXT.get(f.status, f.status)
            if f.status == "offline" and f.last_online:
                text = f"Last online {relative_time(f.last_online)}"
            sub = label(text or (integ.name + " friend" if integ else ""), "Faint")
        col.addWidget(sub)
        lay.addLayout(col, 1)
        if integ:
            b = label()
            b.setPixmap(icons.badge(integ.letter, integ.color, 12))
            b.setToolTip(integ.name)
            lay.addWidget(b)

    def mousePressEvent(self, _e):  # noqa: N802
        if self.f.profile_url:
            xdg_open(self.f.profile_url)


class FriendsPanel(QWidget):
    def __init__(self, services):
        super().__init__()
        self.s = services
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(270)
        self.setStyleSheet(f"#Sidebar {{ border-left: 1px solid {theme.BORDER}; border-right: none; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 12, 12)
        lay.setSpacing(8)
        head = QHBoxLayout()
        self.title = label("Friends", "SectionTitle")
        head.addWidget(self.title)
        head.addStretch(1)
        self.spinner = Spinner(16)
        self.spinner.hide()
        head.addWidget(self.spinner)
        ref = icon_button("refresh", "Refresh")
        ref.clicked.connect(self.refresh)
        head.addWidget(ref)
        lay.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search friends")
        self.search.textChanged.connect(lambda _t: self.render())
        lay.addWidget(self.search)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inner = QWidget()
        inner.setObjectName("Sidebar")
        self.list = QVBoxLayout(inner)
        self.list.setContentsMargins(0, 0, 0, 0)
        self.list.setSpacing(2)
        area.setWidget(inner)
        lay.addWidget(area, 1)
        services.bus.friends_changed.connect(self.render)
        self.render()

    def refresh(self) -> None:
        self.spinner.start()
        self.s.friends.refresh()

    def render(self) -> None:
        self.spinner.stop()
        clear_layout(self.list)
        friends = self.s.friends.friends
        q = self.search.text().strip().lower()
        if q:
            friends = [f for f in friends if q in f.name.lower()]
        online = self.s.friends.online_count()
        self.title.setText(f"Friends  •  {online} online" if self.s.friends.friends else "Friends")
        if not self.s.friends.friends:
            self.list.addWidget(label("Sign in to GOG or add a Steam Web API key (Settings › Integrations) to "
                                      "see your friends here.", "Faint", wrap=True))
        groups = [("Playing", [f for f in friends if f.status == "playing"]),
                  ("Online", [f for f in friends if f.status in ("online", "away", "busy")]),
                  ("Friends", [f for f in friends if f.status == "unknown"]),
                  ("Offline", [f for f in friends if f.status == "offline"])]
        for title, items in groups:
            if not items:
                continue
            h = label(f"{title.upper()}  ({len(items)})", "SidebarSection")
            h.setContentsMargins(4, 10, 0, 2)
            h.setStyleSheet("padding: 10px 4px 2px 4px;")
            self.list.addWidget(h)
            for f in items[:200]:
                self.list.addWidget(FriendRow(f, self.s.integrations.get(f.source)))
        self.list.addStretch(1)
