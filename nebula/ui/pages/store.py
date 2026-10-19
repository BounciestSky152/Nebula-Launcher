"""GOG store browser, product page and wishlist."""
from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QLineEdit, QListView, QScrollArea,
                               QStyle, QStyledItemDelegate, QVBoxLayout, QWidget)

from nebula.core import tasks
from nebula.core.images import images
from nebula.core.utils import xdg_open
from nebula.integrations.gog.api import https
from nebula.ui import icons, theme
from nebula.ui.pages.base import Page
from nebula.ui.widgets.common import (HeroBanner, RemoteImage, Spinner, button, clear_layout, icon_button, label,
                                      placeholder_pixmap)

ORDERS = [("Trending", "desc:trending"), ("Bestselling", "desc:bestselling"), ("New releases", "desc:releaseDate"),
          ("Price: low to high", "asc:price"), ("Biggest discounts", "desc:discount"),
          ("Best rated", "desc:reviewsRating")]
ITEM_ROLE = Qt.ItemDataRole.UserRole + 5


class StoreModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.items: list[dict] = []

    def set_items(self, items):
        self.beginResetModel()
        self.items = list(items)
        self.endResetModel()

    def append(self, items):
        if not items:
            return
        self.beginInsertRows(QModelIndex(), len(self.items), len(self.items) + len(items) - 1)
        self.items += items
        self.endInsertRows()

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        it = self.items[index.row()]
        if role == ITEM_ROLE:
            return it
        if role == Qt.ItemDataRole.DisplayRole:
            return it.get("title")
        return None


class StoreDelegate(QStyledItemDelegate):
    W, H = 300, 169
    MIN_W, MAX_W = 260, 400

    def __init__(self, page, view):
        super().__init__(view)
        self.page = page
        self.view = view

    def set_width(self, w: int) -> None:
        """Cards scale with the view so columns fill the page instead of floating in wide cells."""
        self.W = max(self.MIN_W, min(self.MAX_W, w))
        self.H = self.W * 169 // 300

    def sizeHint(self, option, index):  # noqa: N802
        return QSize(self.W + 20, self.H + 66)

    def paint(self, p: QPainter, option, index):
        it = index.data(ITEM_ROLE)
        if not it:
            return
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        cell = option.rect
        x = cell.x() + (cell.width() - self.W) // 2
        r = QRect(x, cell.y() + 8, self.W, self.H)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 6, 6)
        p.setClipPath(path)
        dpr = self.view.devicePixelRatioF()
        view = self.view

        def got(_pm):
            try:
                view.viewport().update()
            except RuntimeError:
                pass

        pm = images().get([it.get("coverHorizontal", "")], QSize(int(self.W * dpr), int(self.H * dpr)), got)
        if pm is not None:
            p.drawPixmap(r, pm)
        else:
            p.drawPixmap(r, placeholder_pixmap(r.size(), it.get("title", "")))
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if hovered:
            p.fillRect(r, QColor(255, 255, 255, 18))
        pid = str(it.get("id"))
        tag = None
        if pid in self.page.owned:
            tag = ("IN LIBRARY", theme.SUCCESS)
        elif pid in self.page.wishlist:
            tag = ("WISHLISTED", theme.ACCENT)
        if tag:
            f = QFont(p.font())
            f.setPointSizeF(7)
            f.setBold(True)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(tag[0]) + 12
            tr = QRect(r.x() + 6, r.y() + 6, w, 16)
            tp = QPainterPath()
            tp.addRoundedRect(QRectF(tr), 4, 4)
            p.fillPath(tp, QColor(tag[1]))
            p.setPen(QColor("white"))
            p.drawText(tr, Qt.AlignmentFlag.AlignCenter, tag[0])
        p.setClipping(False)
        if hovered:
            p.setPen(QColor(theme.ACCENT))
            p.drawRoundedRect(QRectF(r).adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        f = QFont(p.font())
        f.setPointSizeF(10)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.TEXT))
        y = r.bottom() + 8
        price = it.get("price") or {}
        title_w = self.W - (150 if price.get("discount") else 90)
        p.drawText(QRect(r.x(), y, title_w, 20), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(it.get("title", ""), Qt.TextElideMode.ElideRight, title_w))
        f.setBold(False)
        f.setPointSizeF(9)
        p.setFont(f)
        final = price.get("final") or ""
        if price.get("discount"):
            dr = QRect(r.right() - 140, y, 46, 18)
            dp = QPainterPath()
            dp.addRoundedRect(QRectF(dr), 3, 3)
            p.fillPath(dp, QColor(theme.SUCCESS))
            p.setPen(QColor("#0d1f12"))
            p.drawText(dr, Qt.AlignmentFlag.AlignCenter, price["discount"])
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRect(r.right() - 90, y, 90, 20), Qt.AlignmentFlag.AlignRight,
                   "Free" if final in ("$0.00", "0") else final or "—")
        xi = r.x()
        for os_name in it.get("operatingSystems", []) or []:
            ic = {"windows": "windows", "linux": "linux", "osx": "apple"}.get(os_name)
            if ic:
                p.drawPixmap(xi, y + 24, icons.pixmap(ic, 14, theme.TEXT_FAINT))
                xi += 20
        devs = ", ".join(it.get("developers", []) or [])
        p.setPen(QColor(theme.TEXT_FAINT))
        p.drawText(QRect(xi + 6, y + 22, self.W - (xi - r.x()) - 6, 18), Qt.AlignmentFlag.AlignLeft,
                   p.fontMetrics().elidedText(devs, Qt.TextElideMode.ElideRight, self.W - (xi - r.x()) - 6))
        p.restore()


class StoreGrid(QListView):
    open_item = Signal(dict)

    def __init__(self, page):
        super().__init__()
        self.model_ = StoreModel()
        self.setModel(self.model_)
        self.setItemDelegate(StoreDelegate(page, self))
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda idx: self.open_item.emit(idx.data(ITEM_ROLE)))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        d = self.itemDelegate()
        avail = max(d.MIN_W + 20, self.viewport().width() - 2)
        cols = max(1, avail // (d.MIN_W + 20))
        cell = avail // cols
        d.set_width(cell - 20)
        self.setGridSize(QSize(cell, d.sizeHint(None, QModelIndex()).height()))


class StoreRow(StoreGrid):
    """One horizontally scrolling row of store products (used on Home)."""

    def __init__(self, page):
        super().__init__(page)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(False)
        self.setResizeMode(QListView.ResizeMode.Fixed)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        h = self.itemDelegate().sizeHint(None, QModelIndex()).height()
        self.setFixedHeight(h + 16)

    def resizeEvent(self, e):  # noqa: N802
        QListView.resizeEvent(self, e)
        self.setGridSize(self.itemDelegate().sizeHint(None, QModelIndex()))

    def wheelEvent(self, e):  # noqa: N802
        bar = self.horizontalScrollBar()
        if bar.maximum() > 0 and abs(e.angleDelta().y()) > abs(e.angleDelta().x()):
            bar.setValue(bar.value() - e.angleDelta().y())
            e.accept()
        else:
            super().wheelEvent(e)


class StorePage(Page):
    name = "Store"

    def __init__(self, services, ctl, window, wishlist_mode: bool = False):
        super().__init__(services, ctl, window)
        self.wishlist_mode = wishlist_mode
        self.gog = services.integrations["gog"]
        self.page_no = 1
        self.pages = 1
        self.loading = False
        self.owned: set[str] = set()
        self.wishlist: set[str] = set()
        self._gen = 0
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 22, 32, 0)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.addWidget(label("Wishlist" if wishlist_mode else "Store", "PageTitle"))
        head.addWidget(label("  GOG.COM", "Subtle"), 0, Qt.AlignmentFlag.AlignBottom)
        head.addStretch(1)
        open_web = button("Open GOG.COM", "external")
        open_web.clicked.connect(lambda: xdg_open("https://www.gog.com/en/account/wishlist" if wishlist_mode
                                                  else "https://www.gog.com/en/games"))
        head.addWidget(open_web)
        lay.addLayout(head)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search the store")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(360)
        self.search.addAction(icons.icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(450)
        self._debounce.timeout.connect(self.reload)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.order = QComboBox()
        for text, val in ORDERS:
            self.order.addItem(text, val)
        self.order.currentIndexChanged.connect(lambda _i: self.reload())
        self.linux = button("Linux native", "linux", obj="Chip")
        self.linux.setCheckable(True)
        self.linux.toggled.connect(lambda _c: self.reload())
        self.sale = button("On sale", obj="Chip")
        self.sale.setCheckable(True)
        self.sale.toggled.connect(lambda _c: self.reload())
        for w in (self.search, self.order, self.linux, self.sale):
            bar.addWidget(w)
        bar.addStretch(1)
        self.spinner = Spinner(20)
        self.spinner.hide()
        bar.addWidget(self.spinner)
        if wishlist_mode:
            self.search.hide()
            self.order.hide()
            self.linux.hide()
            self.sale.hide()
        lay.addLayout(bar)
        self.status = label("", "Subtle", wrap=True)
        lay.addWidget(self.status)
        self.grid = StoreGrid(self)
        self.grid.open_item.connect(lambda it: self.win.navigate("product", it))
        self.grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.grid.customContextMenuRequested.connect(self._item_menu)
        self.grid.verticalScrollBar().valueChanged.connect(self._maybe_more)
        lay.addWidget(self.grid, 1)
        self._loaded_once = False

    def _item_menu(self, pos) -> None:
        idx = self.grid.indexAt(pos)
        it = idx.data(ITEM_ROLE) if idx.isValid() else None
        if not it:
            return
        from PySide6.QtWidgets import QMenu

        pid = str(it.get("id"))
        m = QMenu(self)
        m.addAction(icons.icon("store"), "Open", lambda: self.win.navigate("product", it))
        if self.gog.connected() and pid not in self.owned:
            if pid in self.wishlist:
                m.addAction(icons.icon("heart"), "Remove from wishlist", lambda: self._set_wish(it, False))
            else:
                m.addAction(icons.icon("heart"), "Add to wishlist", lambda: self._set_wish(it, True))
        m.exec(self.grid.viewport().mapToGlobal(pos))

    def _set_wish(self, it: dict, want: bool) -> None:
        pid = str(it.get("id"))

        def done(_r):
            (self.wishlist.add if want else self.wishlist.discard)(pid)
            if self.wishlist_mode and not want:
                self.grid.model_.set_items([x for x in self.grid.model_.items if str(x.get("id")) != pid])
                if not self.grid.model_.items:
                    self.status.setText("Your wishlist is empty.")
            self.grid.viewport().update()
            self.win.toast("Wishlist", f"{'Added' if want else 'Removed'} {it.get('title', '')}")

        tasks.run_async(self.gog.set_wishlisted, pid, want, on_done=done,
                        on_error=lambda e: self.win.toast("Wishlist", str(e), "error"))

    def on_show(self, arg=None) -> None:
        if isinstance(arg, str) and arg:
            self.search.setText(arg)
        if not self._loaded_once or self.wishlist_mode:
            self._loaded_once = True
            self.reload()

    def _context(self):
        def work():
            owned = self.gog.owned_ids() if self.gog.connected() else set()
            wish = self.gog.wishlist(refresh=self.wishlist_mode) if self.gog.connected() else set()
            return owned, wish
        return work

    def reload(self) -> None:
        self._gen += 1
        gen = self._gen
        self.page_no = 1
        self.grid.model_.set_items([])
        self.spinner.start()
        self.status.setText("")

        def work():
            owned, wish = self._context()()
            if self.wishlist_mode:
                if not self.gog.connected():
                    return owned, wish, {"products": [], "pages": 1, "_login": True}
                ids = sorted(wish)
                products = []
                for i in range(0, len(ids), 48):  # the catalog serves at most 48 products per request
                    products += self.gog.catalog(product_ids=ids[i:i + 48], limit=48, order="desc:trending",
                                                 product_types="in:game,pack,dlc,extras").get("products", [])
                return owned, wish, {"products": products, "pages": 1}
            data = self.gog.catalog(query=self.search.text().strip(), page=1, order=self.order.currentData(),
                                    linux_only=self.linux.isChecked(), discounted=self.sale.isChecked())
            return owned, wish, data

        tasks.run_async(work, on_done=lambda r: self._loaded(gen, r, reset=True), on_error=self._failed)

    def _failed(self, exc) -> None:
        self.spinner.stop()
        self.loading = False
        self.status.setText(f"Could not load the store: {exc}")

    def _loaded(self, gen: int, result, reset: bool) -> None:
        if gen != self._gen:
            return
        owned, wish, data = result
        self.owned, self.wishlist = owned, wish
        self.spinner.stop()
        self.loading = False
        self.pages = int(data.get("pages", 1) or 1)
        products = data.get("products", []) or []
        if reset:
            self.grid.model_.set_items(products)
        else:
            self.grid.model_.append(products)
        if data.get("_login"):
            self.status.setText("Sign in to GOG to see your wishlist.")
        elif reset and not products:
            self.status.setText("Your wishlist is empty." if self.wishlist_mode else "No products found.")
        elif reset:
            total = data.get("productCount")
            self.status.setText(f"{total} products" if total and not self.wishlist_mode else "")

    def _maybe_more(self, value: int) -> None:
        bar = self.grid.verticalScrollBar()
        if self.wishlist_mode or self.loading or self.page_no >= self.pages or value < bar.maximum() - 400:
            return
        self.loading = True
        self.page_no += 1
        gen = self._gen
        self.spinner.start()
        page = self.page_no

        def work():
            data = self.gog.catalog(query=self.search.text().strip(), page=page, order=self.order.currentData(),
                                    linux_only=self.linux.isChecked(), discounted=self.sale.isChecked())
            return self.owned, self.wishlist, data

        tasks.run_async(work, on_done=lambda r: self._loaded(gen, r, reset=False), on_error=self._failed)


class ProductPage(Page):
    name = "Product"

    def __init__(self, services, ctl, window):
        super().__init__(services, ctl, window)
        self.gog = services.integrations["gog"]
        self.item: dict = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        outer.addWidget(area)
        content = QWidget()
        content.setObjectName("ContentArea")
        area.setWidget(content)
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 30)
        self.hero = HeroBanner(360)
        hl = QVBoxLayout(self.hero)
        hl.setContentsMargins(40, 30, 40, 24)
        hl.addStretch(1)
        self.title = label("", "PageTitle", wrap=True)
        self.title.setStyleSheet("font-size: 26pt; font-weight: 800;")
        hl.addWidget(self.title)
        self.by = label("", "Subtle")
        hl.addWidget(self.by)
        row = QHBoxLayout()
        self.price = label("", "SectionTitle")
        row.addWidget(self.price)
        row.addSpacing(12)
        self.buy = button("Buy on GOG.COM", "store", primary=True)
        self.buy.clicked.connect(self._buy)
        row.addWidget(self.buy)
        self.go = button("Go to game", "forward", primary=True)
        self.go.clicked.connect(self._go)
        row.addWidget(self.go)
        self.wish = icon_button("heart", "Wishlist", checkable=True, size=22)
        self.wish.clicked.connect(self._toggle_wish)
        row.addWidget(self.wish)
        row.addStretch(1)
        self.os_row = QHBoxLayout()
        row.addLayout(self.os_row)
        hl.addLayout(row)
        col.addWidget(self.hero)
        body = QVBoxLayout()
        body.setContentsMargins(40, 20, 40, 0)
        body.setSpacing(14)
        self.spinner = Spinner(22)
        body.addWidget(self.spinner)
        self.shots = QHBoxLayout()
        self.shots.setSpacing(10)
        shots_w = QWidget()
        shots_w.setLayout(self.shots)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFixedHeight(200)
        sa.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(shots_w)
        body.addWidget(sa)
        self.desc = label("", wrap=True, selectable=True)
        self.desc.setStyleSheet(f"color:{theme.TEXT_DIM};")
        body.addWidget(self.desc)
        col.addLayout(body)
        col.addStretch(1)

    def on_show(self, arg=None) -> None:
        if isinstance(arg, dict):
            self.item = arg
        pid = str(self.item.get("id", ""))
        self.title.setText(self.item.get("title", ""))
        devs = ", ".join(self.item.get("developers", []) or [])
        pubs = ", ".join(self.item.get("publishers", []) or [])
        self.by.setText(" • ".join(x for x in (devs, pubs, (self.item.get("releaseDate") or "").replace(".", "-")) if x))
        price = self.item.get("price") or {}
        text = price.get("final") or ""
        if price.get("discount"):
            text += f"   <span style='color:{theme.SUCCESS}'>{price['discount']}</span>  " \
                    f"<s style='color:{theme.TEXT_FAINT}'>{price.get('base', '')}</s>"
        self.price.setText(text)
        owned = pid in self.gog.owned_ids() if self.gog.connected() else False
        self.go.setVisible(owned)
        self.buy.setVisible(not owned)
        self.wish.setVisible(self.gog.connected() and not owned)
        wished = pid in (self.gog.wishlist() if self.gog.connected() else set())
        self.wish.setChecked(wished)
        self.wish.setIcon(icons.icon("heart", theme.DANGER if wished else theme.TEXT_DIM))
        clear_layout(self.os_row)
        for os_name in self.item.get("operatingSystems", []) or []:
            ic = {"windows": "windows", "linux": "linux", "osx": "apple"}.get(os_name)
            if ic:
                lb = label()
                lb.setPixmap(icons.pixmap(ic, 18, theme.TEXT_DIM))
                lb.setToolTip(os_name)
                self.os_row.addWidget(lb)
        self.hero.set_source([self.item.get("coverHorizontal", "")], self.item.get("title", ""))
        clear_layout(self.shots)
        self.desc.setText("")
        self.spinner.start()
        tasks.run_async(self.gog.store_product, pid, on_done=self._details,
                        on_error=lambda e: (self.spinner.stop(), self.desc.setText(f"Could not load details: {e}")))

    def _details(self, p: dict) -> None:
        self.spinner.stop()
        if str(p.get("id")) != str(self.item.get("id")):
            return
        bg = (p.get("images") or {}).get("background")
        if bg:
            self.hero.set_source([https(bg), self.item.get("coverHorizontal", "")], self.item.get("title", ""))
        self.desc.setText(p.get("_description_text") or "")
        for s in (p.get("screenshots") or [])[:10]:
            tmpl = s.get("formatter_template_url")
            if tmpl:
                img = RemoteImage(QSize(320, 180), radius=6)
                img.set_source([tmpl.replace("{formatter}", "ggvgl_2x")])
                self.shots.addWidget(img)
        self.shots.addStretch(1)

    def _buy(self) -> None:
        link = self.item.get("storeLink") or f"https://www.gog.com/en/game/{self.item.get('slug', '')}"
        xdg_open(link)

    def _go(self) -> None:
        g = self.s.library.game(f"gog:{self.item.get('id')}")
        e = self.s.library.entry_for_uid(g.uid) if g else None
        if e:
            self.win.navigate("game", e.key)

    def _toggle_wish(self) -> None:
        pid = str(self.item.get("id"))
        want = self.wish.isChecked()
        self.wish.setIcon(icons.icon("heart", theme.DANGER if want else theme.TEXT_DIM))
        tasks.run_async(self.gog.set_wishlisted, pid, want,
                        on_done=lambda _r: self.win.toast("Wishlist", "Added to your wishlist" if want else "Removed from your wishlist"),
                        on_error=lambda e: self.win.toast("Wishlist", str(e), "error"))
