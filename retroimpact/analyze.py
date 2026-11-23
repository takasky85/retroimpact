"""Spot sheets that did not come out right (blank, a few stray dots, garbage from damaged or truncated data).

The test is deliberately conservative and explains itself: every flagged sheet carries a one-line reason, and nothing is ever
thrown away - the GUI only hides flagged sheets behind a "pages 10-60 hidden" marker that can be opened again.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

from .model import U_PER_INCH, DotPage, Document

BLANK, STRAY, JUNK, OK = "blank", "stray", "junk", "ok"
STRAY_DOTS = 12  # a sheet with this few dots is a leftover fragment, not a print
UNKNOWN_LIMIT = 24  # this many commands the emulator did not understand on a single sheet = the data is misframed
OFF_PAGE_LIMIT = 0.30  # more than this share of the dots landing outside the paper
INK_LIMIT = 0.62  # share of the sheet that is covered in dots: nothing legible ever prints that dense


@dataclass(frozen=True)
class PageInfo:
    index: int
    kind: str  # ok | blank | stray | junk
    reason: str = ""

    @property
    def hidden_by_default(self) -> bool:
        return self.kind != OK


def _coverage(page: DotPage) -> float:
    """Share of the sheet (1/90 in cells) that holds at least one dot, among the cells that fall on the paper."""
    W, H = int(page.width_in * 90) + 1, int(page.height_in * 90) + 1
    ix, iy = (page.x / 16.0).astype(np.int64), (page.y / 16.0).astype(np.int64)
    ok = (ix >= 0) & (ix < W) & (iy >= 0) & (iy < H)
    if not ok.any():
        return 0.0
    cells = np.unique(iy[ok] * W + ix[ok])
    return float(cells.size) / float(W * H)


def classify_page(page: DotPage) -> PageInfo:
    n = page.n_dots
    if n == 0:
        return PageInfo(page.index, BLANK, "nothing printed")
    if n <= STRAY_DOTS:
        return PageInfo(page.index, STRAY, f"only {n} stray dots")
    off = float(((page.x < -U_PER_INCH * 0.05) | (page.x > (page.width_in + 0.5) * U_PER_INCH)
                 | (page.y < 0) | (page.y > (page.height_in + 0.3) * U_PER_INCH)).mean())
    if page.unknown >= UNKNOWN_LIMIT:
        return PageInfo(page.index, JUNK, f"{page.unknown} commands the printer would not understand: data missing or damaged")
    if off > OFF_PAGE_LIMIT:
        return PageInfo(page.index, JUNK, f"{off:.0%} of the dots land off the paper: graphics out of step")
    cov = _coverage(page)
    if cov > INK_LIMIT:
        return PageInfo(page.index, JUNK, f"{cov:.0%} of the sheet is solid dots: random characters")
    return PageInfo(page.index, OK)


def classify(doc: Document) -> List[PageInfo]:
    return [classify_page(p) for p in doc.pages]


def runs(indices) -> List[Tuple[int, int]]:
    """Group sorted page numbers into inclusive (first, last) runs: [9, 10, 11, 20] -> [(9, 11), (20, 20)]."""
    out: List[Tuple[int, int]] = []
    for i in sorted(indices):
        if out and i == out[-1][1] + 1:
            out[-1] = (out[-1][0], i)
        else:
            out.append((i, i))
    return out


def describe_run(first: int, last: int, infos: dict) -> str:
    """'Pages 10-60 hidden (51 sheets): garbage' - 1-based numbers, as the page spin box shows them."""
    n = last - first + 1
    kinds = {infos[i].kind for i in range(first, last + 1) if i in infos}
    what = {BLANK: "blank", STRAY: "nearly empty", JUNK: "garbled"}
    label = " / ".join(what[k] for k in (JUNK, BLANK, STRAY) if k in kinds) or "flagged"
    span = f"Page {first + 1}" if n == 1 else f"Pages {first + 1}-{last + 1}"
    return f"{span} hidden ({label})"
