"""Scrollable page area: lazy background rendering, continuous / single-page, 1-up / 2-up, hand and zoom tools."""
from __future__ import annotations

import math
import os
from collections import OrderedDict
from typing import Dict, Optional

from PySide6.QtCore import QPoint, QRect, QRectF, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QImage, QPainter
from PySide6.QtWidgets import (QFrame, QGridLayout, QLabel, QPushButton, QRubberBand, QScrollArea, QVBoxLayout,
                               QWidget)

from ..analyze import OK, classify, describe_run
from ..pageset import PageSet
from ..render import page_pixel_size, render_page
from ..settings import RenderSettings
from .icons import cursor_pixmap
from .workers import Bus, PageJob, TileJob

TILE = 512  # edge of a render tile in device pixels
TILE_LIMIT = 170  # tiles kept in memory (~0.8 MB each)
BACKDROP_LIMIT = 40  # low-res whole-page previews kept in memory
BACKDROP_DPI = 72  # resolution of the whole-page preview shown while tiles are still being drawn
MAX_DPI = 2400
MARGIN = 18
GAP = 18


class GenBox:
    """Generation counter shared with worker threads; bumping it cancels queued renders."""

    def __init__(self):
        self.gen = 0


class PageWidget(QWidget):
    """One sheet. All the drawing is delegated to the view, which owns the tile and preview caches."""

    def __init__(self, view: "PageView", index: int, w: int, h: int):
        super().__init__()
        self.view = view
        self.index = index
        self.setFixedSize(w, h)

    def paintEvent(self, ev):
        p = QPainter(self)
        self.view.paint_page(self, p, ev.rect())


class GapCard(QFrame):
    """Stands in for a run of hidden sheets ("Pages 10-60 hidden") so it is clear that something was left out."""

    def __init__(self, view: "PageView", first: int, last: int, w: int, h: int):
        super().__init__()
        self.setFixedSize(max(150, w), max(70, h))
        self.setStyleSheet("GapCard { background: #45484c; border: 2px dashed #8a8d92; border-radius: 6px; }"
                           "QLabel { color: #d8dade; background: transparent; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        hidden = [i for i in range(first, last + 1) if i not in view.deleted]
        why = view.flags[hidden[0]].reason if hidden and hidden[0] in view.flags else ""
        text = describe_run(first, last, view.flags)
        if len(hidden) > 1:
            text += f"\n{len(hidden)} sheets"
        if why:
            text += f"\n{why}"
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        btn = QPushButton("Show" if len(hidden) == 1 else "Show these pages")
        btn.clicked.connect(lambda: view.reveal(first, last))
        lay.addWidget(label, 1)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignHCenter)


class PageView(QScrollArea):
    document_changed = Signal()  # a new conversion replaced the document (same page numbers, possibly different content)
    pages_changed = Signal()  # sheets were deleted, hidden or shown again
    look_changed = Signal()  # the rendering settings or the rotation changed (thumbnails need redrawing)
    page_changed = Signal(int, int)  # current page (0-based), total
    zoom_changed = Signal(str)  # 'Fit width', 'Fit page' or '150%'
    layout_changed = Signal()

    def __init__(self, bus: Bus):
        super().__init__()
        self.bus = bus
        self.genbox = GenBox()
        self.doc = None
        self._base = RenderSettings()  # the settings panel's values (no rotation)
        self.settings = RenderSettings()  # ... plus this view's rotation: what is actually rendered
        self.rotation = 0  # degrees clockwise, per tab (banners print sideways)
        self.strip = False  # banner layout: every page side by side in one row, no gaps
        self.zoom_mode = "fit"  # 'fit' (width), 'page' (whole page) or 'fixed'
        self.zoom = 1.0  # 1.0 == 96 dpi on screen
        self.columns = 1  # 1 = one page across, 2 = two pages side by side
        self.continuous = True  # False: show one page (or one spread) at a time
        self.first = 0  # first page index shown when not continuous
        self.tool = "arrow"  # arrow | hand | zoom
        self.ps = PageSet()  # deleted / hidden / revealed sheets
        self.hide_default = False  # whether flagged sheets start out hidden in a newly loaded document (a window setting)
        self.gapw: list = []
        self.restore_page: Optional[int] = None  # scroll here once the first layout has happened (session restore)
        self._pan_last = None
        self._marquee_origin: Optional[QPoint] = None  # zoom tool: where a drag-a-box started (viewport coordinates)
        self._band: Optional[QRubberBand] = None
        self._stale = False
        self._needs_layout = False
        self.wmap: Dict[int, PageWidget] = {}
        self.tiles: "OrderedDict[tuple, tuple]" = OrderedDict()  # (page, dpi, tx, ty) -> (key, QImage, W, H)
        self.backdrops: "OrderedDict[int, tuple]" = OrderedDict()  # page -> (key, QImage)
        self.pending: set = set()
        self.pool = QThreadPool()
        self.pool.setMaxThreadCount(max(1, min(3, (os.cpu_count() or 2) - 1)))
        self.setWidgetResizable(True)
        self.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setStyleSheet("QScrollArea { background: #3b3d40; }")
        self.container = QWidget()
        self.container.setStyleSheet("background: #3b3d40;")
        self.grid = QGridLayout(self.container)
        self.grid.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
        self.grid.setSpacing(GAP)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.setWidget(self.container)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(140)
        self.debounce.timeout.connect(self._apply_settings)
        self.resize_timer = QTimer(self)
        self.resize_timer.setSingleShot(True)
        self.resize_timer.setInterval(120)
        self.resize_timer.timeout.connect(lambda: self._relayout(keep_position=True))
        self.verticalScrollBar().valueChanged.connect(self._scrolled)
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self._scrolled(_v))
        self.bus.page_done.connect(self._page_done)
        self.bus.tile_done.connect(self._tile_done)

    # ------------------------------------------------------------------ public API
    @property
    def n_pages(self) -> int:
        return len(self.doc.pages) if self.doc else 0

    def set_document(self, doc, settings: RenderSettings):
        self.doc = doc
        self._base = settings
        self.settings = settings.replace(rotation=self.rotation)
        self.tiles.clear()
        self.backdrops.clear()
        self.pending.clear()
        self.first = 0
        infos = getattr(doc, "page_info", None) or classify(doc)  # classify() normally ran on the worker thread already
        self.ps = PageSet(len(doc.pages), {i.index: i for i in infos if i.kind != OK})
        self.ps.hide_flagged = self.hide_default
        self.genbox.gen += 1
        self._stale = False
        if self.isVisible():
            self._relayout()
        else:
            self._needs_layout = True
        self.document_changed.emit()  # before pages_changed, so the thumbnail strip drops its old pictures first
        self.pages_changed.emit()

    def set_settings(self, settings: RenderSettings, immediate: bool = False):
        self._base = settings
        self.settings = settings.replace(rotation=self.rotation)
        if not self.isVisible():
            self._stale = True
        elif immediate:
            self._apply_settings()
        else:
            self.debounce.start()

    def set_zoom(self, mode: str, zoom: float = 1.0, anchor=None):
        self.zoom_mode, self.zoom = mode, zoom
        self._relayout(keep_position=True, anchor=anchor)

    def zoom_by(self, factor: float, anchor=None):
        self.zoom_mode = "fixed"
        self.zoom = min(8.0, max(0.1, self.zoom * factor))
        self._relayout(keep_position=True, anchor=anchor)

    def zoom_to_rect(self, rect: QRect):
        """Zoom tool, drag a box: scale so that the boxed part of the viewport fills the window, and centre on it."""
        vp = self.viewport()
        if rect.width() < 4 or rect.height() < 4 or not self.wmap:
            return
        factor = min(vp.width() / float(rect.width()), vp.height() / float(rect.height())) * 0.97
        cx, cy = rect.center().x(), rect.center().y()
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        ry = (vb.value() + cy) / float(max(1, self.container.height()))
        rx = (hb.value() + cx) / float(max(1, self.container.width()))
        self.zoom_mode = "fixed"
        self.zoom = min(8.0, max(0.1, self.zoom * factor))
        self._relayout()

        def settle():
            vb.setValue(int(ry * self.container.height() - vp.height() / 2.0))
            hb.setValue(int(rx * self.container.width() - vp.width() / 2.0))
            self._request_visible()

        QTimer.singleShot(50, settle)

    def set_rotation(self, degrees: int):
        """Turn the pages (clockwise, multiples of 90). Banners from Print Shop / PrintMaster print sideways."""
        degrees = int(degrees) % 360
        if degrees == self.rotation:
            return
        cur = self.current_page()
        self.rotation = degrees
        self.settings = self._base.replace(rotation=degrees)
        self.tiles.clear()
        self.backdrops.clear()
        self.pending.clear()
        self.genbox.gen += 1
        if self.isVisible():
            self._relayout()
            QTimer.singleShot(60, lambda: self.go_to(cur))
        else:
            self._needs_layout = True
        self.layout_changed.emit()
        self.look_changed.emit()

    def set_strip(self, on: bool):
        """Banner layout: all pages in a single row, touching, like the sheets of a banner left on the tractor feed."""
        if on == self.strip:
            return
        cur = self.current_page()
        self.strip = on
        self.first = cur
        self._relayout()
        QTimer.singleShot(60, lambda: self.go_to(cur))
        self.layout_changed.emit()

    def set_columns(self, n: int):
        self.columns = 2 if n >= 2 else 1
        self._relayout(keep_position=True)
        self.layout_changed.emit()

    def set_continuous(self, on: bool):
        if on == self.continuous:
            return
        cur = self.current_page()
        self.continuous = on
        self.first = cur
        self._relayout()
        if on:
            QTimer.singleShot(60, lambda: self.go_to(cur))
        self.layout_changed.emit()

    def set_tool(self, tool: str):
        self.tool = tool
        self._update_cursor()

    def state(self) -> dict:
        """What is needed to put this view back the way it was: zoom and the page at the top."""
        page = self.restore_page if self.restore_page is not None else self.current_page()
        return {"zoom_mode": self.zoom_mode, "zoom": float(self.zoom), "page": int(page),
                "rotation": int(self.rotation), "strip": bool(self.strip), "pageset": self.pageset_state()}

    def zoom_text(self) -> str:
        if self.zoom_mode == "fit":
            return "Fit width"
        if self.zoom_mode == "page":
            return "Fit page"
        return f"{int(round(self.zoom * 100))}%"

    def current_image(self, dpi: int = 150) -> Optional[QImage]:
        """The current page rendered right now at ``dpi`` (blocks for a moment; used by Copy)."""
        if not self.doc or not self.doc.pages:
            return None
        arr = render_page(self.doc.pages[self.current_page()], self.doc, self.settings.replace(dpi=dpi))
        h, w = arr.shape[:2]
        return QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()

    def current_page(self) -> int:
        if not self.doc or not self.wmap:
            return 0
        if self.strip:
            mid = self.horizontalScrollBar().value() + self.viewport().width() // 3
            for i in sorted(self.wmap):
                if self.wmap[i].geometry().right() >= mid:
                    return i
            return max(self.wmap)
        if not self.continuous:
            return min(self.wmap)
        mid = self.verticalScrollBar().value() + self.viewport().height() // 3
        for i in sorted(self.wmap):
            if self.wmap[i].geometry().bottom() >= mid:
                return i
        return max(self.wmap)

    def go_to(self, i: int):
        vis = self.visible_pages()
        if not self.doc or not vis:
            return
        i = next((p for p in vis if p >= max(0, i)), vis[-1])  # a hidden or deleted sheet: the next one that is shown
        if self.strip:
            w = self.wmap.get(i)
            if w is not None:
                self.horizontalScrollBar().setValue(max(0, w.x() - 10))
        elif self.continuous:
            w = self.wmap.get(i)
            if w is not None:
                self.verticalScrollBar().setValue(max(0, w.y() - 10))
        else:
            pos = vis.index(i)
            self.first = vis[(pos // self.columns) * self.columns]
            self.verticalScrollBar().setValue(0)
            self._relayout()
        self.page_changed.emit(self.current_page(), self.n_pages)

    def _step(self) -> int:
        return 1 if self.strip else self.columns

    def _move(self, direction: int):
        vis = self.visible_pages()
        if not vis:
            return
        cur = self.current_page()
        pos = next((k for k, p in enumerate(vis) if p >= cur), len(vis) - 1)
        self.go_to(vis[max(0, min(len(vis) - 1, pos + direction * self._step()))])

    def next_page(self):
        self._move(1)

    def prev_page(self):
        self._move(-1)

    # ------------------------------------------------------------------ which sheets are shown (rules live in PageSet)
    deleted = property(lambda self: self.ps.deleted, lambda self, v: setattr(self.ps, "deleted", v))
    flags = property(lambda self: self.ps.flags, lambda self, v: setattr(self.ps, "flags", v))
    revealed = property(lambda self: self.ps.revealed, lambda self, v: setattr(self.ps, "revealed", v))
    hide_flagged = property(lambda self: self.ps.hide_flagged, lambda self, v: setattr(self.ps, "hide_flagged", v))

    def is_hidden(self, i: int) -> bool:
        return self.ps.is_hidden(i)

    def live_pages(self) -> list:
        return self.ps.live()

    def visible_pages(self) -> list:
        return self.ps.visible()

    def hidden_pages(self) -> list:
        return self.ps.hidden()

    def items(self) -> list:
        """What is laid out, in order: ("page", i) for a shown sheet, ("gap", first, last) for a run of hidden ones."""
        return self.ps.items()

    def _layout_items(self) -> list:
        items = self.items()
        if self.continuous or self.strip:
            return items
        pages = [it[1] for it in items if it[0] == "page"]
        if not pages:
            return items  # nothing but hidden sheets: show the markers so the window is not just empty
        pos = next((k for k, p in enumerate(pages) if p >= self.first), len(pages) - 1)
        start = (pos // self.columns) * self.columns
        return [("page", p) for p in pages[start:start + self.columns]]

    def delete_pages(self, indices):
        """Organise pages: drop sheets from the view and from every export (they come back with restore_deleted)."""
        self.ps.delete(indices)
        self._pageset_changed()

    def restore_deleted(self):
        self.ps.deleted.clear()
        self._pageset_changed()

    def hide_pages(self, indices):
        self.ps.hide(indices)
        self._pageset_changed()

    def reveal(self, first: int, last: int):
        self.ps.reveal(first, last)
        self._pageset_changed()

    def set_hide_flagged(self, on: bool):
        self.ps.set_hide_flagged(on)
        self._pageset_changed()

    def _pageset_changed(self):
        cur = self.current_page()
        self.first = min(self.visible_pages() or [0], key=lambda p: abs(p - self.first))
        self.tiles.clear()
        self.pending.clear()
        if self.isVisible():
            self._relayout()
            QTimer.singleShot(60, lambda: self.go_to(cur))
        else:
            self._needs_layout = True
        self.pages_changed.emit()
        self.page_changed.emit(self.current_page(), self.n_pages)

    def pageset_state(self) -> dict:
        return self.ps.state()

    def apply_pageset(self, d: dict):
        """Put back the deletions and hidden sheets saved with the session."""
        self.ps.apply(d)
        self._pageset_changed()

    def kept_count(self) -> int:
        return len(self.visible_pages())

    # ------------------------------------------------------------------ layout
    def _eff_cols(self) -> int:
        return max(1, len(self._layout_items())) if self.strip else self.columns

    def _gap(self) -> int:
        return 0 if self.strip else GAP

    def _shown(self):
        return [it[1] for it in self._layout_items() if it[0] == "page"]

    def _dpr(self) -> float:
        return float(self.devicePixelRatioF())

    def _paper_inches(self, page):
        w, h = page_pixel_size(self.doc, page, self.settings.replace(dpi=100))
        return w / 100.0, h / 100.0

    def _ppi(self) -> float:
        if self.zoom_mode in ("fit", "page") and self.doc and self.doc.pages:
            idx = self._shown()[: self._eff_cols()] or [0]
            sizes = [self._paper_inches(self.doc.pages[i]) for i in idx]
            pw = max(s[0] for s in sizes)
            ph = max(s[1] for s in sizes)
            sb = self.verticalScrollBar().sizeHint().width() if not self.verticalScrollBar().isVisible() else 0
            ncols = max(1, len(idx)) if self.strip else self._eff_cols()
            avail_w = max(100, self.viewport().width() - 2 * MARGIN - self._gap() * (ncols - 1) - sb)
            ppi = avail_w / (pw * ncols)
            if self.zoom_mode == "page":
                ppi = min(ppi, max(100, self.viewport().height() - 2 * MARGIN) / ph)
            self.zoom = min(8.0, max(0.1, ppi / 96.0))
        return 96.0 * self.zoom

    def _relayout(self, keep_position: bool = False, anchor=None):
        self._needs_layout = False
        ratio = None
        if keep_position and self.wmap:
            hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
            ax, ay = anchor if anchor else (self.viewport().width() / 2, self.viewport().height() / 2)
            ch, cw = max(1, self.container.height()), max(1, self.container.width())
            ratio = ((vb.value() + ay) / ch, (hb.value() + ax) / cw, ax, ay)
        for w in list(self.wmap.values()) + self.gapw:
            self.grid.removeWidget(w)
            w.deleteLater()
        self.wmap, self.gapw = {}, []
        if not self.doc:
            return
        ppi = self._ppi()
        cols = self._eff_cols()
        self.grid.setSpacing(self._gap())
        items = self._layout_items()
        ref = next((it[1] for it in items if it[0] == "page"), None)
        ref_w, ref_h = self._paper_inches(self.doc.pages[ref if ref is not None else 0]) if self.doc.pages else (8.5, 11.0)
        row = col = 0
        for it in items:
            if it[0] == "gap":
                if self.strip:
                    card = GapCard(self, it[1], it[2], 200, int(round(ref_h * ppi)))
                    self.grid.addWidget(card, 0, col, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                    col += 1
                else:
                    if col:
                        row, col = row + 1, 0
                    card = GapCard(self, it[1], it[2], int(round(ref_w * ppi)), 96)
                    self.grid.addWidget(card, row, 0, 1, max(1, cols), Qt.AlignmentFlag.AlignHCenter)
                    row += 1
                self.gapw.append(card)
                continue
            i = it[1]
            pw, ph = self._paper_inches(self.doc.pages[i])
            w = PageWidget(self, i, int(round(pw * ppi)), int(round(ph * ppi)))
            self.wmap[i] = w
            if self.strip:
                al = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
            elif cols == 1:
                al = Qt.AlignmentFlag.AlignHCenter
            else:  # a spread: the left page hugs the gutter from the left, the right page from the right
                al = Qt.AlignmentFlag.AlignRight if col % 2 == 0 else Qt.AlignmentFlag.AlignLeft
            self.grid.addWidget(w, row, col, al)
            col += 1
            if not self.strip and col >= cols:
                row, col = row + 1, 0
        self.genbox.gen += 1
        self.pending.clear()
        self.zoom_changed.emit(self.zoom_text())
        if ratio is not None:
            def restore(r=ratio):
                vb, hb = self.verticalScrollBar(), self.horizontalScrollBar()
                vb.setValue(int(r[0] * self.container.height() - r[3]))
                hb.setValue(int(r[1] * self.container.width() - r[2]))
                self._request_visible()

            QTimer.singleShot(40, restore)
        QTimer.singleShot(0, self._request_visible)
        if self.restore_page is not None:
            target, self.restore_page = self.restore_page, None
            QTimer.singleShot(90, lambda t=target: self.go_to(t))
        self.page_changed.emit(self.current_page(), self.n_pages)

    def showEvent(self, ev):
        super().showEvent(ev)
        if self._needs_layout:
            self._relayout()
        elif self.zoom_mode in ("fit", "page"):
            self._relayout(keep_position=True)
        if self._stale:
            self._stale = False
            self._apply_settings()
        else:
            QTimer.singleShot(0, self._request_visible)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        if self.zoom_mode in ("fit", "page") and self.isVisible():
            self.resize_timer.start()
        else:
            self._request_visible()

    def _scrolled(self, _v):
        self._request_visible()
        if self.wmap and (self.continuous or self.strip):
            self.page_changed.emit(self.current_page(), self.n_pages)

    # ------------------------------------------------------------------ tools
    def _update_cursor(self):
        vp = self.viewport()
        if self.tool == "hand":
            vp.setCursor(Qt.CursorShape.OpenHandCursor)
        elif self.tool == "zoom":
            vp.setCursor(QCursor(cursor_pixmap("zoom", 30), 12, 12))
        else:
            vp.setCursor(Qt.CursorShape.ArrowCursor)

    def mousePressEvent(self, ev):
        left, mid = ev.button() == Qt.MouseButton.LeftButton, ev.button() == Qt.MouseButton.MiddleButton
        if mid or (left and self.tool == "hand"):
            self._pan_last = ev.globalPosition().toPoint()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            ev.accept()
            return
        if left and self.tool == "zoom":
            self._marquee_origin = ev.position().toPoint()  # a click zooms in, a drag zooms to the box (see release)
            ev.accept()
            return
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev):
        if self._marquee_origin is not None:
            a, b = self._marquee_origin, ev.position().toPoint()
            if (b - a).manhattanLength() > 6:
                if self._band is None:
                    self._band = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
                self._band.setGeometry(QRect(a, b).normalized())
                self._band.show()
            ev.accept()
            return
        if self._pan_last is not None:
            p = ev.globalPosition().toPoint()
            d = p - self._pan_last
            self._pan_last = p
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            ev.accept()
            return
        super().mouseMoveEvent(ev)

    def mouseReleaseEvent(self, ev):
        if self._marquee_origin is not None:
            a, b = self._marquee_origin, ev.position().toPoint()
            self._marquee_origin = None
            dragged = self._band is not None and self._band.isVisible()
            if self._band is not None:
                self._band.hide()
            if dragged:
                self.zoom_to_rect(QRect(a, b).normalized())
            else:
                out = bool(ev.modifiers() & (Qt.KeyboardModifier.ShiftModifier | Qt.KeyboardModifier.AltModifier))
                self.zoom_by(0.8 if out else 1.25, anchor=(a.x(), a.y()))
            ev.accept()
            return
        if self._pan_last is not None:
            self._pan_last = None
            self._update_cursor()
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def wheelEvent(self, ev):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            dy = ev.angleDelta().y()
            if dy:
                p = ev.position()
                self.zoom_by(1.12 ** (dy / 120.0), anchor=(p.x(), p.y()))
            ev.accept()
            return
        super().wheelEvent(ev)

    # ------------------------------------------------------------------ rendering
    # The page is cut into TILE x TILE pixel tiles at the exact zoom (device) resolution. Only tiles that are on
    # screen, plus a ring around them, are rendered, so zooming in costs the same as zooming out. A low-res
    # whole-page preview shows underneath until a tile arrives, and stale tiles stay up (instead of flashing)
    # while a changed setting is re-rendered.
    def _render_dpi(self) -> int:
        return int(min(MAX_DPI, max(48, round(96.0 * self.zoom * self._dpr()))))

    def _skey(self):
        d = self.settings.to_dict()
        d.pop("dpi", None)
        return tuple(sorted(d.items()))

    def _apply_settings(self):
        self.genbox.gen += 1
        self.pending.clear()
        self._request_visible()
        for w in self.wmap.values():
            w.update()
        self.look_changed.emit()

    def _grid(self, index: int, dpi: int):
        """(W, H, tiles across, tiles down) of a page at ``dpi``."""
        W, H = page_pixel_size(self.doc, self.doc.pages[index], self.settings.replace(dpi=dpi))
        return W, H, max(1, math.ceil(W / TILE)), max(1, math.ceil(H / TILE))

    def paint_page(self, w: PageWidget, p: QPainter, exposed: QRect):
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        r = w.rect()
        bd = self.backdrops.get(w.index)
        if bd is not None:
            p.drawImage(r, bd[1])
        else:
            p.fillRect(r, QColor(236, 234, 226))
            p.setPen(QColor(120, 120, 120))
            f = QFont(w.font())
            f.setPointSize(11)
            p.setFont(f)
            p.drawText(QRectF(r), Qt.AlignmentFlag.AlignCenter, f"Page {w.index + 1}")
        if not self.doc or w.index >= self.n_pages:
            return
        dpi = self._render_dpi()
        W, H, nx, ny = self._grid(w.index, dpi)
        sx, sy = w.width() / float(W), w.height() / float(H)
        tx0 = max(0, int(exposed.left() / sx // TILE))
        tx1 = min(nx - 1, int(exposed.right() / sx // TILE))
        ty0 = max(0, int(exposed.top() / sy // TILE))
        ty1 = min(ny - 1, int(exposed.bottom() / sy // TILE))
        for ty in range(ty0, ty1 + 1):
            for tx in range(tx0, tx1 + 1):
                k = (w.index, dpi, tx, ty)
                ent = self.tiles.get(k)
                if ent is None:
                    continue
                self.tiles.move_to_end(k)
                _key, img, tw, th = ent
                ssx, ssy = w.width() / float(tw), w.height() / float(th)
                l, t = round(tx * TILE * ssx), round(ty * TILE * ssy)
                rr, bb = round((tx * TILE + img.width()) * ssx), round((ty * TILE + img.height()) * ssy)
                p.drawImage(QRect(l, t, rr - l, bb - t), img)

    def _tiles_in(self, w: PageWidget, local: QRect, W: int, H: int, nx: int, ny: int):
        sx, sy = w.width() / float(W), w.height() / float(H)
        tx0 = max(0, int(local.left() / sx // TILE))
        tx1 = min(nx - 1, int(local.right() / sx // TILE))
        ty0 = max(0, int(local.top() / sy // TILE))
        ty1 = min(ny - 1, int(local.bottom() / sy // TILE))
        return {(tx, ty) for ty in range(ty0, ty1 + 1) for tx in range(tx0, tx1 + 1)}

    def _request_visible(self):
        if not self.doc or not self.wmap or not self.isVisible():
            return
        skey, gen, dpi = self._skey(), self.genbox.gen, self._render_dpi()
        tile_settings = self.settings.replace(dpi=dpi)
        vp = self.viewport()
        vis = QRect(0, 0, vp.width(), vp.height())
        near = vis.adjusted(-vp.width() // 2, -int(vp.height() * 0.6), vp.width() // 2, int(vp.height() * 0.6))
        centre = vis.center()
        keep_pages, keep_tiles = set(), set()
        for i, w in sorted(self.wmap.items()):
            origin = w.mapTo(vp, QPoint(0, 0))
            wr = QRect(origin, w.size())
            if wr.intersected(near).isEmpty():
                continue
            keep_pages.add(i)
            on_screen = not wr.intersected(vis).isEmpty()
            bd = self.backdrops.get(i)
            if (bd is None or bd[0] != skey) and ("bd", i, skey) not in self.pending:
                self.pending.add(("bd", i, skey))
                self.pool.start(PageJob(self.bus, self.genbox, gen, self.doc, i,
                                        self.settings.replace(dpi=BACKDROP_DPI), "draft", skey), 30 if on_screen else 5)
            W, H, nx, ny = self._grid(i, dpi)
            vis_tiles = set()
            if on_screen:
                vis_tiles = self._tiles_in(w, wr.intersected(vis).translated(-origin), W, H, nx, ny)
            near_tiles = self._tiles_in(w, wr.intersected(near).translated(-origin), W, H, nx, ny) - vis_tiles
            for group, prio in ((vis_tiles, 10), (near_tiles, 2)):
                def dist(t, w=w, origin=origin, W=W, H=H):
                    cx = origin.x() + (t[0] + 0.5) * TILE * w.width() / W
                    cy = origin.y() + (t[1] + 0.5) * TILE * w.height() / H
                    return (cx - centre.x()) ** 2 + (cy - centre.y()) ** 2
                for tx, ty in sorted(group, key=dist):
                    k = (i, dpi, tx, ty)
                    keep_tiles.add(k)
                    ent = self.tiles.get(k)
                    if ent is not None and ent[0] == skey:
                        continue
                    pk = ("t",) + k + (skey,)
                    if pk in self.pending:
                        continue
                    self.pending.add(pk)
                    self.pool.start(TileJob(self.bus, self.genbox, gen, self.doc, i, tile_settings, skey, tx, ty, TILE), prio)
        self._evict(keep_pages, keep_tiles)

    def _page_done(self, box, gen: int, index: int, tier: str, key, img: QImage):
        """A low-res whole-page preview arrived."""
        if box is not self.genbox or gen != self.genbox.gen:
            return
        self.pending.discard(("bd", index, key))
        self.backdrops[index] = (key, img)
        self.backdrops.move_to_end(index)
        w = self.wmap.get(index)
        if w is not None:
            w.update()

    def _tile_done(self, box, gen: int, index: int, key, dpi: int, tx: int, ty: int, img: QImage):
        if box is not self.genbox or gen != self.genbox.gen or not self.doc or index >= self.n_pages:
            return
        self.pending.discard(("t", index, dpi, tx, ty, key))
        W, H = page_pixel_size(self.doc, self.doc.pages[index], self.settings.replace(dpi=dpi))
        self.tiles[(index, dpi, tx, ty)] = (key, img, W, H)
        self.tiles.move_to_end((index, dpi, tx, ty))
        w = self.wmap.get(index)
        if w is not None and dpi == self._render_dpi():
            sx, sy = w.width() / float(W), w.height() / float(H)
            w.update(QRect(int(tx * TILE * sx) - 1, int(ty * TILE * sy) - 1,
                           int(img.width() * sx) + 3, int(img.height() * sy) + 3))

    def _evict(self, keep_pages, keep_tiles):
        # tiles at other zoom levels are useless now; then drop the least recently used ones
        dpi = self._render_dpi()
        for k in [k for k in self.tiles if k[1] != dpi and k not in keep_tiles]:
            if len(self.tiles) <= TILE_LIMIT // 2:
                break
            del self.tiles[k]
        while len(self.tiles) > TILE_LIMIT:
            k = next((k for k in self.tiles if k not in keep_tiles), None)
            if k is None:
                break
            del self.tiles[k]
        while len(self.backdrops) > BACKDROP_LIMIT:
            k = next((k for k in self.backdrops if k not in keep_pages), None)
            if k is None:
                break
            del self.backdrops[k]
