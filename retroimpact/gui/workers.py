"""Background jobs. All cross-thread traffic goes through one long-lived Bus owned by the main window."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, Qt, Signal
from PySide6.QtGui import QImage

from ..analyze import classify
from ..emulate import emulate
from ..render import render_page, render_region


class Bus(QObject):
    emu_done = Signal(int, object)  # token, Document
    emu_failed = Signal(int, str)
    page_done = Signal(object, int, int, str, object, QImage)  # genbox, gen, page index, tier, key, image
    tile_done = Signal(object, int, int, object, int, int, int, QImage)  # genbox, gen, page, key, dpi, tx, ty, image
    job_received = Signal(str, bytes, str)  # virtual printer: job name, raw data, path of the kept copy ('' = none)
    thumb_done = Signal(object, int, QImage)  # token, page index, image
    preview_one = Signal(object, object)  # token, preview.PrinterPreview (one printer type's first page)
    preview_all = Signal(object, object)  # token, {key: PrinterPreview} once every printer has been tried
    export_progress = Signal(int, int)
    export_done = Signal(int, str)  # pages written, error text ('' = ok)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancel_export = False


class EmuJob(QRunnable):
    def __init__(self, bus: Bus, token: int, data: bytes, opts):
        super().__init__()
        self.bus, self.token, self.data, self.opts = bus, token, data, opts

    def run(self):
        try:
            doc = emulate(self.data, self.opts)
            doc.page_info = classify(doc)  # which sheets look blank or garbled (hidden by default in the view)
        except Exception:
            self.bus.emu_failed.emit(self.token, traceback.format_exc())
            return
        self.bus.emu_done.emit(self.token, doc)


class PageJob(QRunnable):
    def __init__(self, bus: Bus, genbox, gen: int, doc, index: int, settings, tier: str, key):
        super().__init__()
        self.bus, self.genbox, self.gen, self.doc, self.index = bus, genbox, gen, doc, index
        self.settings, self.tier, self.key = settings, tier, key

    def run(self):
        if self.gen != self.genbox.gen:  # a newer request made this one obsolete
            return
        try:
            arr = render_page(self.doc.pages[self.index], self.doc, self.settings)
        except Exception:
            traceback.print_exc()
            return
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.bus.page_done.emit(self.genbox, self.gen, self.index, self.tier, self.key, img)


class TileJob(QRunnable):
    """One square piece of one page at the current zoom. Costs the same at any zoom level."""

    def __init__(self, bus: Bus, genbox, gen: int, doc, index: int, settings, key, tx: int, ty: int, tile: int):
        super().__init__()
        self.bus, self.genbox, self.gen, self.doc, self.index = bus, genbox, gen, doc, index
        self.settings, self.key, self.tx, self.ty, self.tile = settings, key, tx, ty, tile

    def run(self):
        if self.gen != self.genbox.gen:
            return
        try:
            page = self.doc.pages[self.index]
            from ..render import page_pixel_size
            W, H = page_pixel_size(self.doc, page, self.settings)
            x, y = self.tx * self.tile, self.ty * self.tile
            if x >= W or y >= H:
                return
            arr = render_region(page, self.doc, self.settings, x, y, min(self.tile, W - x), min(self.tile, H - y))
        except Exception:
            traceback.print_exc()
            return
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        self.bus.tile_done.emit(self.genbox, self.gen, self.index, self.key, int(self.settings.dpi), self.tx, self.ty, img)


class ThumbJob(QRunnable):
    """A small picture of one sheet for the thumbnail rail."""

    def __init__(self, bus: Bus, token, current, doc, index: int, settings, width: int):
        super().__init__()
        self.bus, self.token, self.current, self.doc, self.index = bus, token, current, doc, index
        self.settings, self.width = settings, width

    def run(self):
        if self.token != self.current[0]:
            return
        try:
            from ..render import page_pixel_size
            page = self.doc.pages[self.index]
            w_in = page_pixel_size(self.doc, page, self.settings.replace(dpi=100))[0] / 100.0
            dpi = int(min(150, max(36, -(-self.width // max(0.5, w_in)))))  # about one pixel per thumbnail pixel
            arr = render_page(page, self.doc, self.settings.replace(dpi=dpi, show_holes=True))
        except Exception:
            traceback.print_exc()
            return
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        img = img.scaledToWidth(self.width, Qt.TransformationMode.SmoothTransformation)
        self.bus.thumb_done.emit(self.token, self.index, img)


class PreviewJob(QRunnable):
    """First page of a capture as every printer type would draw it (see preview.py), delivered one printer at a time."""

    def __init__(self, bus: Bus, token: int, current: list, data: bytes, emu, user_settings):
        super().__init__()
        self.bus, self.token, self.current = bus, token, current
        self.data, self.emu, self.user_settings = data, emu, user_settings

    def run(self):
        from ..preview import preview_all

        try:
            results = preview_all(self.data, self.emu, self.user_settings,
                                  on_result=lambda r: self.bus.preview_one.emit(self.token, r),
                                  cancelled=lambda: self.current[0] != self.token)
        except Exception:
            traceback.print_exc()
            return
        if self.current[0] == self.token:
            self.bus.preview_all.emit(self.token, results)


class ExportJob(QRunnable):
    def __init__(self, bus: Bus, fn, *args, **kw):
        super().__init__()
        self.bus, self.fn, self.args, self.kw = bus, fn, args, kw

    def run(self):
        from ..export import Cancelled

        def progress(done, total):
            self.bus.export_progress.emit(done, total)
            return not self.bus.cancel_export

        try:
            n = self.fn(*self.args, progress=progress, **self.kw)
            self.bus.export_done.emit(n, "")
        except Cancelled:
            self.bus.export_done.emit(0, "cancelled")
        except Exception:
            self.bus.export_done.emit(0, traceback.format_exc())
