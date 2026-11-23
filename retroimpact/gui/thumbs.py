"""Thumbnail rail: a small picture of every sheet, with page organising (delete, hide, restore).

Works on whichever tab is current: ``bind(view)`` points it at that tab's PageView. Sheets that were hidden because they look
blank or garbled show up as one dashed "Pages 10-60 hidden" row, exactly like in the page view.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QListView, QListWidget, QListWidgetItem, QMenu,
                               QPushButton, QSlider, QVBoxLayout, QWidget)

from ..analyze import describe_run
from .page_view import PageView
from .workers import Bus, ThumbJob

THUMB_W = 112  # default thumbnail width in pixels
THUMB_MIN, THUMB_MAX = 56, 360
KIND_ROLE = Qt.ItemDataRole.UserRole
DATA_ROLE = Qt.ItemDataRole.UserRole + 1


def _blank_thumb(w: int, h: int, text: str = "") -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor(236, 234, 226))
    if text:
        p = QPainter(pm)
        p.setPen(QColor(130, 130, 130))
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, text)
        p.end()
    return pm


def _gap_thumb(w: int, h: int) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor(69, 72, 76))
    p = QPainter(pm)
    pen = QPen(QColor(138, 141, 146), 2, Qt.PenStyle.DashLine)
    p.setPen(pen)
    p.drawRoundedRect(2, 2, w - 4, h - 4, 6, 6)
    p.setPen(QColor(216, 218, 222))
    p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "hidden")
    p.end()
    return pm


class ZoomList(QListWidget):
    """Ctrl + wheel changes the thumbnail size, like everywhere else in the program."""

    zoomed = Signal(int)  # wheel steps (+ larger)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = ev.angleDelta().y()
            if dy:
                self.zoomed.emit(1 if dy > 0 else -1)
            ev.accept()
            return
        super().wheelEvent(ev)


class ThumbPanel(QWidget):
    width_changed = Signal(int)  # the thumbnail size was changed (the window remembers it)

    def __init__(self, bus: Bus, width: int = THUMB_W, parent=None):
        super().__init__(parent)
        self.bus = bus
        self.thumb_w = int(min(THUMB_MAX, max(THUMB_MIN, width)))
        self.view: Optional[PageView] = None
        self.token = 0
        self.current = [0]  # shared with the worker jobs: the token that is still wanted
        self.cache: Dict[int, tuple] = {}  # page -> (width drawn at, pixmap); an older width is shown scaled until redrawn
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(1)  # one sheet at a time keeps the window responsive
        self._ratio = 11.0 / 8.5

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.title = QLabel("Pages")
        self.title.setStyleSheet("font-weight: bold;")
        lay.addWidget(self.title)
        row = QHBoxLayout()
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setToolTip("Delete the selected pages (Del). They are left out of the view and of exports; "
                                   "Restore brings them back.")
        self.btn_hide = QPushButton("Hide")
        self.btn_hide.setToolTip("Hide the selected pages behind a marker, as for pages that look like junk")
        self.btn_restore = QPushButton("Restore")
        self.btn_restore.setToolTip("Bring back deleted pages and show every hidden page")
        for b in (self.btn_delete, self.btn_hide, self.btn_restore):
            row.addWidget(b)
        lay.addLayout(row)
        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(THUMB_MIN, THUMB_MAX)
        self.size_slider.setValue(self.thumb_w)
        self.size_slider.setToolTip("Thumbnail size (Ctrl + mouse wheel works too). Make the panel wider to get more "
                                    "pages in a row.")
        lay.addWidget(self.size_slider)
        self.list = ZoomList()
        self.list.setViewMode(QListView.ViewMode.IconMode)  # icons wrap into as many columns as the panel is wide
        self.list.setFlow(QListView.Flow.LeftToRight)
        self.list.setWrapping(True)
        self.list.setResizeMode(QListView.ResizeMode.Adjust)
        self.list.setMovement(QListView.Movement.Static)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setUniformItemSizes(True)
        self.list.setWordWrap(True)
        self.list.setSpacing(2)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.rows: Dict[int, int] = {}  # page index -> row in the list (so nothing has to search a long list)
        self.requested: set = set()  # (page, width) already asked for since the last rebuild
        self._prio = 0
        self.vis_timer = QTimer(self)  # draw only what can be seen: a capture can have thousands of sheets
        self.vis_timer.setSingleShot(True)
        self.vis_timer.setInterval(60)
        self.vis_timer.timeout.connect(self._queue_visible)
        self.list.verticalScrollBar().valueChanged.connect(lambda _v: self.vis_timer.start())
        self.regrid_timer = QTimer(self)
        self.regrid_timer.setSingleShot(True)
        self.regrid_timer.setInterval(260)
        self.regrid_timer.timeout.connect(self.rebuild)
        self._apply_size()
        lay.addWidget(self.list, 1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: #999; font-size: 11px;")
        lay.addWidget(self.note)

        self.list.itemClicked.connect(self._clicked)
        self.list.itemDoubleClicked.connect(self._double_clicked)
        self.list.customContextMenuRequested.connect(self._menu)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.list, self._delete_selected,
                  context=Qt.ShortcutContext.WidgetShortcut)
        self.size_slider.valueChanged.connect(self._slider)
        self.list.zoomed.connect(lambda k: self.size_slider.setValue(self.size_slider.value() + k * 24))
        self.btn_delete.clicked.connect(self._delete_selected)
        self.btn_hide.clicked.connect(self._hide_selected)
        self.btn_restore.clicked.connect(self._restore)
        self.bus.thumb_done.connect(self._thumb_done)
        self.setMinimumWidth(140)
        self._refresh_buttons()

    def _icon_h(self) -> int:
        return max(20, int(self.thumb_w * self._ratio))

    def _apply_size(self):
        w, h = self.thumb_w, self._icon_h()
        self.list.setIconSize(QSize(w, h))
        self.list.setGridSize(QSize(w + 14, h + 36))  # room under the picture for the page number

    def _slider(self, v: int):
        self.thumb_w = int(v)
        self._apply_size()  # the pictures just scale for now; the sharp ones are drawn after a short pause
        self.width_changed.emit(self.thumb_w)
        self.regrid_timer.start()

    # ------------------------------------------------------------------ binding
    def bind(self, view: Optional[PageView]):
        if self.view is view:
            return
        if self.view is not None:
            for sig, slot in ((self.view.document_changed, self._drop_cache), (self.view.pages_changed, self.rebuild), (self.view.look_changed, self.invalidate),
                              (self.view.page_changed, self._page_changed)):
                try:
                    sig.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
        self.view = view
        self.cache.clear()
        if view is not None:
            view.document_changed.connect(self._drop_cache)
            view.pages_changed.connect(self.rebuild)
            view.look_changed.connect(self.invalidate)
            view.page_changed.connect(self._page_changed)
        self.rebuild()

    def _drop_cache(self):
        """A new conversion replaced the document: the cached pictures belong to the old one."""
        self.cache.clear()

    def invalidate(self):
        """The look or the rotation changed: every thumbnail has to be drawn again."""
        self.cache.clear()
        self.rebuild()

    # ------------------------------------------------------------------ building the list
    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.vis_timer.start()

    def rebuild(self):
        self.token += 1
        self.current[0] = self.token
        self.list.clear()
        self.rows = {}
        self.requested = set()
        v = self.view
        if v is None or v.doc is None or not v.doc.pages:
            self.title.setText("Pages")
            self.note.setText("")
            self._refresh_buttons()
            return
        w = self.thumb_w
        pw, ph = v._paper_inches(v.doc.pages[0])
        self._ratio = ph / pw if pw else 11.0 / 8.5
        self._apply_size()
        h = self._icon_h()
        for it in v.items():
            if it[0] == "gap":
                item = QListWidgetItem(describe_run(it[1], it[2], v.flags))
                item.setIcon(_gap_thumb(w, h))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                item.setData(KIND_ROLE, "gap")
                item.setData(DATA_ROLE, (it[1], it[2]))
                item.setToolTip("Double-click to show these pages")
            else:
                i = it[1]
                item = QListWidgetItem(f"{i + 1}")
                self.rows[i] = self.list.count()
                cached = self.cache.get(i)
                item.setIcon(cached[1] if cached is not None else _blank_thumb(w, h))
                item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
                item.setData(KIND_ROLE, "page")
                item.setData(DATA_ROLE, i)
                if i in v.flags:
                    item.setToolTip(v.flags[i].reason or "")
                    item.setText(f"{i + 1}  (flagged)")
            self.list.addItem(item)
        self.vis_timer.start()
        n_del, n_hid = len(v.deleted), len(v.hidden_pages())
        self.title.setText(f"Pages ({len(v.visible_pages())})")
        bits = []
        if n_hid:
            bits.append(f"{n_hid} hidden")
        if n_del:
            bits.append(f"{n_del} deleted")
        self.note.setText(", ".join(bits))
        self._refresh_buttons()
        self._page_changed(v.current_page(), v.n_pages)

    def _queue_visible(self):
        """Ask for thumbnails of the rows on screen (plus a screenful either side); newest requests are drawn first."""
        v = self.view
        n = self.list.count()
        if v is None or v.doc is None or not n:
            return
        model, w = self.list.model(), self.thumb_w
        height = self.list.viewport().height()
        margin = height
        lo, hi = 0, n  # first row whose bottom edge is on or below the top of the wanted band (rows run top to bottom)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.list.visualRect(model.index(mid, 0)).bottom() < -margin:
                lo = mid + 1
            else:
                hi = mid
        asked = 0
        for r in range(lo, n):
            if self.list.visualRect(model.index(r, 0)).top() > height + margin or asked >= 600:
                break
            item = self.list.item(r)
            if item.data(KIND_ROLE) != "page":
                continue
            i = item.data(DATA_ROLE)
            cached = self.cache.get(i)
            if (cached is not None and cached[0] == w) or (i, w) in self.requested:
                continue
            self.requested.add((i, w))
            self._prio += 1
            self.pool.start(ThumbJob(self.bus, self.token, self.current, v.doc, i, v.settings, w), self._prio)
            asked += 1

    def _thumb_done(self, token, index, img):
        if token != self.token:
            return
        pm = QPixmap.fromImage(img)
        self.cache[index] = (img.width(), pm)
        r = self.rows.get(index)
        if r is not None and r < self.list.count():
            it = self.list.item(r)
            if it.data(KIND_ROLE) == "page" and it.data(DATA_ROLE) == index:
                it.setIcon(pm)

    def _page_changed(self, index, _n):
        r = self.rows.get(index)
        if r is None or r >= self.list.count():
            return
        it = self.list.item(r)
        if not self.list.selectedItems() or len(self.list.selectedItems()) == 1:
            self.list.blockSignals(True)
            self.list.setCurrentItem(it)
            self.list.blockSignals(False)
        self.list.scrollToItem(it)

    def _refresh_buttons(self):
        v = self.view
        on = v is not None and v.doc is not None
        self.btn_delete.setEnabled(on)
        self.btn_hide.setEnabled(on)
        self.btn_restore.setEnabled(on and bool(v.deleted or v.hidden_pages() or v.revealed))

    # ------------------------------------------------------------------ actions
    def selected_pages(self) -> list:
        return sorted(it.data(DATA_ROLE) for it in self.list.selectedItems() if it.data(KIND_ROLE) == "page")

    def _clicked(self, item):
        if self.view is not None and item.data(KIND_ROLE) == "page":
            self.view.go_to(item.data(DATA_ROLE))

    def _double_clicked(self, item):
        if self.view is not None and item.data(KIND_ROLE) == "gap":
            self.view.reveal(*item.data(DATA_ROLE))

    def _delete_selected(self):
        sel = self.selected_pages()
        if self.view is not None and sel:
            self.view.delete_pages(sel)

    def _hide_selected(self):
        sel = self.selected_pages()
        if self.view is not None and sel:
            self.view.hide_pages(sel)

    def _restore(self):
        v = self.view
        if v is None:
            return
        v.deleted.clear()
        v.ps.reveal_all()
        v.set_hide_flagged(False)  # every sheet is shown again; View > "Hide blank and garbled pages" folds them away again

    def _menu(self, pos):
        v = self.view
        if v is None:
            return
        m = QMenu(self)
        sel = self.selected_pages()
        m.addAction(f"Delete {len(sel)} page(s)", self._delete_selected).setEnabled(bool(sel))
        m.addAction(f"Hide {len(sel)} page(s) as junk", self._hide_selected).setEnabled(bool(sel))
        m.addSeparator()
        m.addAction("Select all", self.list.selectAll)
        m.addAction("Restore deleted and hidden pages", self._restore).setEnabled(self.btn_restore.isEnabled())
        m.exec(self.list.mapToGlobal(pos))
