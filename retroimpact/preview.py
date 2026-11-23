"""One-page previews of a capture as every printer type would draw it, with a plain-language verdict for each.

Used by the GUI's "Printers" tab, but Qt-free so it can be tested headless. The point: when a printout looks wrong the
usual reason is that it was read as the wrong printer, so show the first page for each printer side by side and say what is
wrong with the ones that are.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

from .analyze import BLANK, JUNK, STRAY, _coverage, classify_page
from .emulate import emulate
from .model import PROFILE_ORDER, PROFILES, Document, EmuOptions
from .render import page_pixel_size, render_page
from .settings import LOOK_PRESETS, RenderSettings

PREVIEW_BYTES = 120_000  # only the start of a big capture is converted: the first page is all that is shown
PREVIEW_WIDTH = 260  # pixels across the finished preview (the GUI scales it to the tile)
DENSE_WARN = 0.40  # share of the sheet covered in dots above which "random characters" is worth a mention
OK_LEVEL, WARN_LEVEL, BAD_LEVEL = "ok", "warn", "bad"
KEYS = ["auto"] + list(PROFILE_ORDER)
SHORT_NAMES = {"auto": "Auto-detect", "epson_fx": "Epson FX", "epson_lq": "Epson LQ", "ibm": "IBM", "star": "Star",
               "imagewriter": "ImageWriter", "okimate": "Okimate", "mps": "Commodore"}


@dataclass
class PrinterPreview:
    key: str  # 'auto' or a PROFILES key
    label: str
    image: Optional[np.ndarray] = None  # uint8 (H, W, 3); None if nothing could be drawn
    level: str = OK_LEVEL  # ok | warn | bad
    summary: str = ""  # one short line for under the tile
    detail: str = ""  # the full explanation (tooltip)
    detected: str = ""  # for the auto tile: the printer key auto-detect chose ('mixed' for a split capture)
    n_dots: int = 0
    n_pages: int = 0
    signature: tuple = ()  # equal signatures = the same picture


def preview_settings(user: RenderSettings) -> RenderSettings:
    """A clean, legible look (so garbage is easy to see and rendering is quick) that keeps the user's ink choices."""
    s = RenderSettings.from_dict(LOOK_PRESETS["Clean (for PDF)"])
    return s.replace(paper="auto", show_holes=False, ink_set=user.ink_set, custom_inks=user.custom_inks,
                     color_ribbon=user.color_ribbon, ribbon=user.ribbon, dot_size=1.5, seed=user.seed)


def judge(doc: Document):
    """-> (level, short verdict, full explanation) for the first sheet of `doc`."""
    if doc.n_pages == 0 or doc.pages[0].n_dots == 0:
        return (BAD_LEVEL, "Nothing printed",
                "This printer type ignores all of the data, so no dots land on the page.")
    page = doc.pages[0]
    info = classify_page(page)
    if info.kind in (BLANK, STRAY):
        return BAD_LEVEL, "Almost nothing printed", f"Only a few stray dots: {info.reason}. Most of the data was ignored."
    if info.kind == JUNK:
        if "commands the printer would not understand" in info.reason:
            return (BAD_LEVEL, "Doesn't understand the data",
                    f"{page.unknown} commands in the first page are ones this printer doesn't have, so the data is "
                    "being read as the wrong language.")
        if "off the paper" in info.reason:
            return (BAD_LEVEL, "Graphics out of step",
                    info.reason[0].upper() + info.reason[1:] + ". The graphics row lengths don't match this printer.")
        return (BAD_LEVEL, "Looks like random characters",
                info.reason[0].upper() + info.reason[1:] + ". Text read as graphics, or graphics read as text.")
    problems = []
    if page.unknown:
        problems.append((f"{page.unknown} command{'s' if page.unknown != 1 else ''} ignored",
                         f"{page.unknown} commands on the first page aren't understood by this printer and were skipped."))
    if doc.warnings:
        problems.append(("Data looks cut short", "; ".join(doc.warnings)))
    cov = _coverage(page)
    if cov > DENSE_WARN:
        problems.append(("Very dense, may be garbled", f"{cov:.0%} of the sheet is covered in dots: unusually dense."))
    if problems:
        return WARN_LEVEL, problems[0][0], " ".join(p[1] for p in problems)
    return OK_LEVEL, "Looks fine", "No problems found on the first page."


def _signature(doc: Document) -> tuple:
    if doc.n_pages == 0:
        return (0,)
    p = doc.pages[0]
    return (doc.n_pages, p.n_dots, int(round(float(p.x.sum()) / 16.0)), int(round(float(p.y.sum()) / 16.0)))


def _picture(doc: Document, user: RenderSettings, width: int) -> Optional[np.ndarray]:
    if doc.n_pages == 0 or doc.pages[0].n_dots == 0:
        return None
    s = preview_settings(user)
    page = doc.pages[0]
    w100 = page_pixel_size(doc, page, s.replace(dpi=100))[0] / 100.0
    dpi = int(min(150, max(36, width * 1.6 / max(0.5, w100))))
    arr = render_page(page, doc, s.replace(dpi=dpi), workers=1)
    h, w = arr.shape[:2]
    return cv2.resize(arr, (width, max(1, int(round(h * width / w)))), interpolation=cv2.INTER_AREA)


def preview_one(data: bytes, key: str, emu: EmuOptions, user: RenderSettings, width: int = PREVIEW_WIDTH,
                _doc: Optional[Document] = None) -> PrinterPreview:
    """Preview of the first page of `data` as printer `key` ('auto' or a PROFILES key) would draw it."""
    doc = _doc or emulate(data[:PREVIEW_BYTES], dataclasses.replace(emu, printer=key))
    label = "Auto-detect" if key == "auto" else PROFILES[key].label
    level, summary, detail = judge(doc)
    return PrinterPreview(key=key, label=label, image=_picture(doc, user, width), level=level, summary=summary,
                          detail=detail, detected=doc.detected if key == "auto" else "",
                          n_dots=doc.pages[0].n_dots if doc.n_pages else 0, n_pages=doc.n_pages,
                          signature=_signature(doc))


def annotate(results: Dict[str, PrinterPreview]) -> Dict[str, PrinterPreview]:
    """Add what only the whole set can say: a printer that gets far fewer dots than the others is probably ignoring
    data, and printers that draw exactly the same page are named so the choice between them is clearly harmless."""
    # Dot counts are only comparable loosely: a 24-pin Epson draws two or three times the dots of a 9-pin printer for the same
    # text, and printers differ in dot size. So the 24-pin printer is never used as the yardstick, and a printer has to fall
    # well short of the best printer of another kind (or far short of its own kind) to be called out.
    def group(k):
        return "lq" if k == "epson_lq" else PROFILES[k].family

    fine = [r for r in results.values() if r.level != BAD_LEVEL and r.key != "auto" and r.n_dots]
    auto = results.get("auto")
    pick = results.get(auto.detected) if auto is not None else None
    for r in results.values():
        if r.level != OK_LEVEL or r.key == "auto" or not fine:
            continue
        g = group(r.key)
        yard = [x for x in fine if group(x.key) != "lq" or g == "lq"]
        other_top = max([x.n_dots for x in yard if group(x.key) != g] or [0])
        own_top = max([x.n_dots for x in yard if group(x.key) == g] or [0])
        if other_top and r.n_dots < 0.12 * other_top:
            pct = r.n_dots * 100 // other_top
        elif own_top and r.n_dots < 0.3 * own_top:
            pct = r.n_dots * 100 // own_top
        elif (pick is not None and pick is not r and pick.level != BAD_LEVEL and pick.key != "epson_lq"
              and r.n_dots * 2 <= pick.n_dots):
            pct = r.n_dots * 100 // pick.n_dots
        else:
            continue
        r.level = WARN_LEVEL
        r.summary = "Prints far less than others"
        r.detail = (f"This printer type draws only {pct}% of the dots the best match does: "
                    "some of the data (probably graphics) is being ignored.")
    by_sig: Dict[tuple, List[str]] = {}
    for k in KEYS:
        r = results.get(k)
        if r is not None and k != "auto" and r.signature and r.n_dots:
            by_sig.setdefault(r.signature, []).append(k)
    for keys in by_sig.values():
        if len(keys) < 2:
            continue
        for k in keys:
            others = [o for o in keys if o != k]
            r = results[k]
            r.detail += "\nDraws exactly the same page as " + ", ".join(PROFILES[o].label for o in others) + "."
            if r.level == OK_LEVEL:
                r.summary = "Looks fine, same as " + SHORT_NAMES[others[0]]
    if auto is not None and auto.detected in results:
        picked = results[auto.detected]
        auto.summary = f"Picked {SHORT_NAMES[auto.detected]}"
        auto.detail = f"Auto-detect chose {PROFILES[auto.detected].label}. " + picked.detail
        auto.level = picked.level
    elif auto is not None and auto.detected == "mixed":
        auto.summary = "Mixed capture"
        auto.detail = "This capture switches printer type part-way through, so Auto-detect splits it into parts."
    return results


def preview_all(data: bytes, emu: EmuOptions, user: RenderSettings, width: int = PREVIEW_WIDTH,
                on_result: Optional[Callable[[PrinterPreview], None]] = None,
                cancelled: Optional[Callable[[], bool]] = None) -> Dict[str, PrinterPreview]:
    """Previews for Auto plus every printer type. `on_result` is called as each one is ready (for a progressive display);
    `cancelled` is polled between printers so a superseded job stops early."""
    results: Dict[str, PrinterPreview] = {}
    head = data[:PREVIEW_BYTES]
    auto_doc = emulate(head, dataclasses.replace(emu, printer="auto"))
    order = ["auto"] + list(PROFILE_ORDER)
    for key in order:
        if cancelled is not None and cancelled():
            break
        reuse = auto_doc if key == "auto" or key == auto_doc.detected else None
        r = preview_one(data, key, emu, user, width, _doc=reuse)
        results[key] = r
        if on_result is not None:
            on_result(r)
    return annotate(results)
