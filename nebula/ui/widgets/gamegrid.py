"""Model/view widgets for showing library entries as cover grids, rows and tables."""
from __future__ import annotations

from PySide6.QtCore import (QAbstractListModel, QAbstractTableModel, QEvent, QModelIndex, QPoint, QRect, QRectF,
                            QSize, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QListView, QStyle, QStyledItemDelegate, QTableView)

from nebula.core.images import images
from nebula.core.utils import human_size, playtime_text, relative_time
from nebula.ui import icons, theme
from nebula.ui.widgets.common import placeholder_pixmap

ENTRY_ROLE = Qt.ItemDataRole.UserRole + 1
COVER_RATIO = 1.42


class EntryModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries: list = []
        self._rows: dict[str, int] = {}

    def set_entries(self, entries: list) -> None:
        self.beginResetModel()
        self.entries = list(entries)
        self._rows = {e.key: i for i, e in enumerate(self.entries)}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self.entries):
            return None
        e = self.entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return e.title
        if role == ENTRY_ROLE:
            return e
        if role == Qt.ItemDataRole.ToolTipRole:
            return e.title
        return None

    def update_entry(self, entry) -> None:
        row = self._rows.get(entry.key)
        if row is not None:
            self.entries[row] = entry
            idx = self.index(row)
            self.dataChanged.emit(idx, idx)

    def row_for(self, key: str) -> int | None:
        return self._rows.get(key)


class CoverDelegate(QStyledItemDelegate):
    play_clicked = Signal(str)

    def __init__(self, services, view, cover_width: int = 170, compact: bool = False):
        super().__init__(view)
        self.s = services
        self.view = view
        self.cover_width = cover_width
        self.compact = compact
        self._hover_row = -1

    @property
    def cover_height(self) -> int:
        return int(self.cover_width * COVER_RATIO)

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(self.cover_width + 20, self.cover_height + (46 if not self.compact else 34))

    def _cover_rect(self, cell: QRect) -> QRect:
        x = cell.x() + (cell.width() - self.cover_width) // 2
        return QRect(x, cell.y() + 8, self.cover_width, self.cover_height)

    def _play_rect(self, cover: QRect) -> QRect:
        d = min(56, self.cover_width // 3)
        return QRect(cover.center().x() - d // 2, cover.center().y() - d // 2, d, d)

    def _request(self, entry, size: QSize):
        dpr = self.view.devicePixelRatioF() or 1.0
        target = QSize(int(size.width() * dpr), int(size.height() * dpr))
        view = self.view

        def got(_pm):
            try:
                view.viewport().update()
            except RuntimeError:
                pass

        return images().get(entry.cover_urls(), target, got)

    def paint(self, p: QPainter, option, index):
        entry = index.data(ENTRY_ROLE)
        if entry is None:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cell = option.rect
        cover = self._cover_rect(cell)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected = bool(option.state & QStyle.StateFlag.State_Selected) and self.view.hasFocus()
        path = QPainterPath()
        path.addRoundedRect(QRectF(cover), 6, 6)
        pm = self._request(entry, cover.size())
        p.setClipPath(path)
        if pm is not None and not pm.isNull():
            p.drawPixmap(cover, pm)
        else:
            p.drawPixmap(cover, placeholder_pixmap(cover.size(), entry.title))
        installed = entry.installed
        if not installed:
            p.fillRect(cover, QColor(0, 0, 0, 70))
        launcher = self.s.launcher
        running = launcher.entry_running(entry)
        job = next((self.s.jobs.active_for(g.uid) for g in entry.games if self.s.jobs.active_for(g.uid)), None)
        if hovered:
            p.fillRect(cover, QColor(10, 10, 14, 120))
            pr = self._play_rect(cover)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(pr)
            name = "stop" if running else ("play" if installed else "download")
            ic = icons.pixmap(name, pr.width() // 2, "white")
            p.drawPixmap(pr.center().x() - pr.width() // 4, pr.center().y() - pr.width() // 4, ic)
        if job is not None:
            bar = QRect(cover.x(), cover.bottom() - 26, cover.width(), 26)
            p.fillRect(bar, QColor(0, 0, 0, 180))
            prog = QRect(bar.x(), bar.bottom() - 3, int(bar.width() * job.progress), 4)
            p.fillRect(prog, QColor(theme.ACCENT))
            p.setPen(QColor("white"))
            f = QFont(p.font())
            f.setPointSizeF(8)
            p.setFont(f)
            txt = f"{job.stage}  {int(job.progress * 100)}%" if job.total else job.stage
            p.drawText(bar.adjusted(8, 0, -8, -4), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(txt, Qt.TextElideMode.ElideRight, bar.width() - 16))
        pill = None
        if running:
            pill = ("PLAYING", theme.SUCCESS)
        elif entry.update_available:
            pill = ("UPDATE", theme.WARNING)
        if pill:
            f = QFont(p.font())
            f.setPointSizeF(7)
            f.setBold(True)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(pill[0]) + 12
            r = QRect(cover.x() + 6, cover.y() + 6, w, 16)
            pp = QPainterPath()
            pp.addRoundedRect(QRectF(r), 4, 4)
            p.fillPath(pp, QColor(pill[1]))
            p.setPen(QColor("#111"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, pill[0])
        if entry.user.favorite:
            p.drawPixmap(cover.right() - 22, cover.y() + 6, icons.pixmap("heart", 16, theme.DANGER))
        p.setClipping(False)
        if hovered or selected:
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(QRectF(cover).adjusted(1, 1, -1, -1), 6, 6)
        # caption
        p.setPen(QColor(theme.TEXT if installed else theme.TEXT_DIM))
        f = QFont(p.font())
        f.setPointSizeF(9.5)
        f.setBold(False)
        p.setFont(f)
        y = cover.bottom() + 6
        title = p.fontMetrics().elidedText(entry.title, Qt.TextElideMode.ElideRight, cover.width())
        p.drawText(QRect(cover.x(), y, cover.width(), 18), Qt.AlignmentFlag.AlignLeft, title)
        if not self.compact:
            x = cover.x()
            for src in entry.sources[:4]:
                integ = self.s.integrations.get(src)
                if integ:
                    p.drawPixmap(x, y + 21, icons.badge(integ.letter, integ.color, 12))
                    x += 16
            f.setPointSizeF(8)
            p.setFont(f)
            p.setPen(QColor(theme.TEXT_FAINT))
            sub = playtime_text(entry.playtime_minutes) if entry.playtime_minutes else (
                "Installed" if installed else "")
            p.drawText(QRect(x + 4, y + 19, cover.right() - x - 4, 16),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, sub)
        p.restore()

    def editorEvent(self, event, model, option, index):  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            cover = self._cover_rect(option.rect)
            if self._play_rect(cover).contains(event.position().toPoint()):
                entry = index.data(ENTRY_ROLE)
                if entry is not None:
                    self.play_clicked.emit(entry.key)
                    return True
        return super().editorEvent(event, model, option, index)


class _BaseCoverView(QListView):
    open_entry = Signal(str)
    play_entry = Signal(str)
    context_entry = Signal(str, QPoint)

    def __init__(self, services, cover_width: int = 170, compact: bool = False, parent=None):
        super().__init__(parent)
        self.s = services
        self.model_ = EntryModel(self)
        self.setModel(self.model_)
        self.delegate = CoverDelegate(services, self, cover_width, compact)
        self.setItemDelegate(self.delegate)
        self.setMouseTracking(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.clicked.connect(lambda idx: self.open_entry.emit(idx.data(ENTRY_ROLE).key))
        self.activated.connect(lambda idx: self.open_entry.emit(idx.data(ENTRY_ROLE).key))
        self.delegate.play_clicked.connect(self.play_entry.emit)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_entries(self, entries) -> None:
        self.model_.set_entries(entries)

    def _context(self, pos):
        idx = self.indexAt(pos)
        if idx.isValid():
            self.context_entry.emit(idx.data(ENTRY_ROLE).key, self.viewport().mapToGlobal(pos))

    def refresh_entry(self, entry) -> None:
        self.model_.update_entry(entry)


class CoverGrid(_BaseCoverView):
    def __init__(self, services, cover_width: int = 170, parent=None):
        super().__init__(services, cover_width, parent=parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setSpacing(0)
        self.setContentsMargins(0, 0, 0, 0)

    def set_cover_width(self, w: int) -> None:
        self.delegate.cover_width = w
        self._update_grid()
        self.viewport().update()

    def _update_grid(self) -> None:
        base = self.delegate.sizeHint(None, QModelIndex())
        avail = max(base.width(), self.viewport().width() - 2)
        cols = max(1, avail // base.width())
        self.setGridSize(QSize(avail // cols, base.height()))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._update_grid()


class CoverRow(_BaseCoverView):
    """A single horizontally scrolling row of covers."""

    def __init__(self, services, cover_width: int = 150, parent=None):
        super().__init__(services, cover_width, compact=True, parent=parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setMovement(QListView.Movement.Static)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        h = self.delegate.sizeHint(None, QModelIndex()).height()
        self.setFixedHeight(h + 14)

    def wheelEvent(self, e):  # noqa: N802
        bar = self.horizontalScrollBar()
        if bar.maximum() > 0 and abs(e.angleDelta().y()) > abs(e.angleDelta().x()):
            bar.setValue(bar.value() - e.angleDelta().y())
            e.accept()
        else:
            super().wheelEvent(e)


COLUMNS = [
    ("title", "Title"),
    ("platforms", "Platforms"),
    ("last_played", "Last played"),
    ("playtime", "Time played"),
    ("achievements", "Achievements"),
    ("genres", "Genres"),
    ("release", "Release date"),
    ("rating", "My rating"),
    ("size", "Size"),
    ("tags", "Tags"),
]


class EntryTableModel(QAbstractTableModel):
    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.s = services
        self.entries: list = []
        self.columns = [c for c in COLUMNS]

    def set_entries(self, entries) -> None:
        self.beginResetModel()
        self.entries = list(entries)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.entries)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.columns[section][1]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        e = self.entries[index.row()]
        col = self.columns[index.column()][0]
        if role == ENTRY_ROLE:
            return e
        if role == Qt.ItemDataRole.ForegroundRole:
            if col == "title":
                return QColor(theme.TEXT if e.installed else theme.TEXT_DIM)
            return QColor(theme.TEXT_DIM)
        if role == Qt.ItemDataRole.DecorationRole and col == "title":
            return icons.icon("check" if e.installed else "download",
                              theme.SUCCESS if e.installed else theme.TEXT_FAINT)
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if col == "title":
            return e.title
        if col == "platforms":
            return ", ".join(self.s.integrations[s].name for s in e.sources if s in self.s.integrations)
        if col == "last_played":
            return relative_time(e.last_played) if e.last_played else "—"
        if col == "playtime":
            return playtime_text(e.playtime_minutes) if e.playtime_minutes else "—"
        if col == "achievements":
            u, t = e.achievements
            return f"{u}/{t}" if t else "—"
        if col == "genres":
            return ", ".join(e.genres[:3])
        if col == "release":
            return e.release_date[:10] if e.release_date else ""
        if col == "rating":
            return "★" * e.user.rating if e.user.rating else ""
        if col == "size":
            return human_size(e.install_size) if e.install_size else ""
        if col == "tags":
            return ", ".join(e.user.tags)
        return None


class EntryTable(QTableView):
    open_entry = Signal(str)
    context_entry = Signal(str, QPoint)
    sort_requested = Signal(str)

    SORT_FOR = {"title": "title", "last_played": "last_played", "playtime": "playtime", "release": "release",
                "rating": "rating", "size": "size", "achievements": "achievements"}

    def __init__(self, services, parent=None):
        super().__init__(parent)
        self.s = services
        self.model_ = EntryTableModel(services, self)
        self.setModel(self.model_)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setShowGrid(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(38)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionsClickable(True)
        self.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.setColumnWidth(0, 300)
        for i, w in enumerate((300, 130, 110, 100, 100, 160, 100, 90, 90)):
            self.setColumnWidth(i, w)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context)
        self.doubleClicked.connect(lambda idx: self.open_entry.emit(self.model_.entries[idx.row()].key))
        self.clicked.connect(lambda idx: self.open_entry.emit(self.model_.entries[idx.row()].key)
                             if idx.column() == 0 else None)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setIconSize(QSize(14, 14))

    def set_entries(self, entries) -> None:
        self.model_.set_entries(entries)

    def set_visible_columns(self, keys: list[str]) -> None:
        for i, (k, _) in enumerate(self.model_.columns):
            self.setColumnHidden(i, k not in keys and k != "title")

    def _header_clicked(self, section: int) -> None:
        key = self.model_.columns[section][0]
        if key in self.SORT_FOR:
            self.sort_requested.emit(self.SORT_FOR[key])

    def _context(self, pos):
        idx = self.indexAt(pos)
        if idx.isValid():
            self.context_entry.emit(self.model_.entries[idx.row()].key, self.viewport().mapToGlobal(pos))
