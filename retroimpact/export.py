"""PDF / TIFF export. Pages are rendered one at a time so memory stays flat."""
from __future__ import annotations

import io
import os
from typing import Callable, Iterable, Optional

import numpy as np
from PIL import Image

from .model import Document
from .render import render_page
from .settings import RenderSettings

Progress = Optional[Callable[[int, int], bool]]  # (done, total) -> return False to cancel


class Cancelled(Exception):
    pass


def _page_list(doc: Document, pages: Optional[Iterable[int]]):
    idx = list(range(doc.n_pages)) if pages is None else [i for i in pages if 0 <= i < doc.n_pages]
    return idx


def export_pdf(doc: Document, path: str, s: RenderSettings, pages=None, jpeg_quality: int = 90,
               lossless: bool = False, progress: Progress = None) -> int:
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    idx = _page_list(doc, pages)
    c = None
    for n, i in enumerate(idx):
        if progress and progress(n, len(idx)) is False:
            raise Cancelled()
        arr = render_page(doc.pages[i], doc, s)
        h, w = arr.shape[:2]
        wpt, hpt = w * 72.0 / s.dpi, h * 72.0 / s.dpi
        if c is None:
            c = canvas.Canvas(path, pagesize=(wpt, hpt), pageCompression=1)
            c.setTitle(os.path.basename(path))
            c.setAuthor("RetroImpact")
            c.setCreator("RetroImpact dot-matrix converter")
        c.setPageSize((wpt, hpt))
        im = Image.fromarray(arr)
        if lossless:
            c.drawImage(ImageReader(im), 0, 0, wpt, hpt)
        else:
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=jpeg_quality, subsampling=0, dpi=(s.dpi, s.dpi))
            buf.seek(0)
            c.drawImage(ImageReader(buf), 0, 0, wpt, hpt)
        c.showPage()
    if c is None:  # empty document -> one blank sheet
        from .render import page_pixel_size  # noqa: F401
        c = canvas.Canvas(path, pagesize=(612, 792))
        c.showPage()
    c.save()
    if progress:
        progress(len(idx), len(idx))
    return len(idx)


def _convert(arr: np.ndarray, mode: str) -> Image.Image:
    im = Image.fromarray(arr)
    if mode == "gray":
        return im.convert("L")
    if mode == "mono":
        return im.convert("L").point(lambda v: 255 if v > 150 else 0).convert("1")
    return im


def export_tiff(doc: Document, path: str, s: RenderSettings, pages=None, mode: str = "color",
                per_page: bool = False, progress: Progress = None) -> int:
    """mode: color | gray | mono (1-bit, CCITT G4). Multi-page TIFF unless per_page."""
    idx = _page_list(doc, pages)
    comp = "group4" if mode == "mono" else "tiff_lzw"
    first, rest = None, []
    base, ext = os.path.splitext(path)
    for n, i in enumerate(idx):
        if progress and progress(n, len(idx)) is False:
            raise Cancelled()
        im = _convert(render_page(doc.pages[i], doc, s), mode)
        if per_page:
            im.save(f"{base}_{n + 1:03d}{ext or '.tif'}", compression=comp, dpi=(s.dpi, s.dpi))
        elif first is None:
            first = im
        else:
            rest.append(im)  # held in memory; pages are 1-bit/8-bit/24-bit at export dpi
    if not per_page:
        if first is None:
            first = Image.new("RGB", (int(8.5 * s.dpi), int(11 * s.dpi)), "white")
        first.save(path, save_all=True, append_images=rest, compression=comp, dpi=(s.dpi, s.dpi))
    if progress:
        progress(len(idx), len(idx))
    return len(idx)


def export_png(doc: Document, path: str, s: RenderSettings, pages=None, progress: Progress = None) -> int:
    idx = _page_list(doc, pages)
    base, ext = os.path.splitext(path)
    for n, i in enumerate(idx):
        if progress and progress(n, len(idx)) is False:
            raise Cancelled()
        name = path if len(idx) == 1 else f"{base}_{n + 1:03d}{ext or '.png'}"
        Image.fromarray(render_page(doc.pages[i], doc, s)).save(name, dpi=(s.dpi, s.dpi))
    return len(idx)
