"""Vector toolbar icons, drawn from primitives on a 24 x 24 grid. No image files, no Qt needed here.

Every icon is a list of *strokes* (polylines drawn with a round pen) and *fills* (solid polygons). Qt paints them
with QPainter at whatever size and device-pixel-ratio the toolbar wants, in the current theme's text colour, so they
stay razor sharp on HiDPI screens and never vanish on a dark toolbar. ``tests/icon_preview.py`` rasterises the same
geometry with Pillow for a contact sheet.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Sequence, Tuple

GRID = 24.0
STROKE = 1.75  # pen width on the 24-unit grid

Pt = Tuple[float, float]


class Icon:
    def __init__(self):
        self.strokes: List[Tuple[List[Pt], bool]] = []  # (points, closed)
        self.fills: List[List[Pt]] = []

    # ---- primitives
    def line(self, x0, y0, x1, y1):
        self.strokes.append(([(x0, y0), (x1, y1)], False))
        return self

    def poly(self, pts: Sequence[Pt], closed=False, round_r: float = 0.0):
        pts = [(float(x), float(y)) for x, y in pts]
        pts = [q for i, q in enumerate(pts) if i == 0 or q != pts[i - 1]]  # drop repeated points
        if closed and len(pts) > 1 and pts[0] == pts[-1]:
            pts.pop()
        if round_r > 0:
            pts = _round_corners(pts, closed, round_r)
        self.strokes.append((pts, closed))
        return self

    def rect(self, x, y, w, h, r=0.0):
        pts = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        self.strokes.append((_round_corners(pts, True, r) if r > 0 else pts, True))
        return self

    def circle(self, cx, cy, r):
        self.strokes.append((_arc(cx, cy, r, 0, 360, 48)[:-1], True))
        return self

    def arc(self, cx, cy, r, a0, a1):
        """Degrees, clockwise on screen (y points down)."""
        self.strokes.append((_arc(cx, cy, r, a0, a1, max(8, int(abs(a1 - a0) / 6))), False))
        return self

    def dot(self, cx, cy, r=1.2):
        self.fills.append(_arc(cx, cy, r, 0, 360, 20)[:-1])
        return self

    def fpoly(self, pts: Sequence[Pt]):
        self.fills.append([(float(x), float(y)) for x, y in pts])
        return self


def _arc(cx, cy, r, a0, a1, n) -> List[Pt]:
    return [(cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def _round_corners(pts: List[Pt], closed: bool, r: float, steps: int = 5) -> List[Pt]:
    """Replace each interior corner by a quadratic curve that starts/ends ``r`` away from the corner."""
    n = len(pts)
    out: List[Pt] = []
    idx = range(n) if closed else range(1, n - 1)
    if not closed:
        out.append(pts[0])
    for i in idx:
        p0, p1, p2 = pts[(i - 1) % n], pts[i], pts[(i + 1) % n]
        d0 = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
        d2 = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        rr = min(r, d0 / 2, d2 / 2)
        a = (p1[0] + (p0[0] - p1[0]) * rr / d0, p1[1] + (p0[1] - p1[1]) * rr / d0)
        b = (p1[0] + (p2[0] - p1[0]) * rr / d2, p1[1] + (p2[1] - p1[1]) * rr / d2)
        for k in range(steps + 1):
            t = k / steps
            out.append(((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * p1[0] + t * t * b[0],
                        (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * p1[1] + t * t * b[1]))
    if not closed:
        out.append(pts[-1])
    return out


# --------------------------------------------------------------------------------------------- the icons
def _arrowhead(ic: Icon, x, y, dx, dy, size=4.2, spread=38):
    """A V-shaped head whose tip is at (x, y) pointing along (dx, dy)."""
    L = math.hypot(dx, dy)
    dx, dy = dx / L, dy / L
    pts = []
    for s in (+1, -1):
        a = math.radians(180 + s * spread)
        c, sn = math.cos(a), math.sin(a)
        pts.append((x + size * (dx * c - dy * sn), y + size * (dx * sn + dy * c)))
    ic.poly([pts[0], (x, y), pts[1]], False)


def _open() -> Icon:
    ic = Icon()
    ic.poly([(3, 19), (3, 5.5), (9, 5.5), (11, 8), (19.5, 8), (19.5, 10.5)], False, 1.0)
    ic.poly([(3, 19), (5.4, 10.5), (21.5, 10.5), (19.2, 19), (3, 19)], True, 1.0)
    return ic


def _save() -> Icon:  # export: arrow into a tray
    ic = Icon()
    ic.line(12, 3.5, 12, 15)
    ic.poly([(7.5, 10.5), (12, 15), (16.5, 10.5)], False)
    ic.poly([(4, 14.5), (4, 20), (20, 20), (20, 14.5)], False, 1.0)
    return ic


def _reload() -> Icon:
    ic = Icon()
    ic.arc(12, 12, 7.6, 35, 315)
    ex, ey = 12 + 7.6 * math.cos(math.radians(315)), 12 + 7.6 * math.sin(math.radians(315))
    _arrowhead(ic, ex + 0.6, ey + 0.2, -math.sin(math.radians(315)), math.cos(math.radians(315)), 4.4, 40)
    return ic


def _hand() -> Icon:
    ic = Icon()
    outline = [(8.6, 21), (5.4, 15.2), (4.4, 12.6), (5.2, 11.4), (6.8, 12.2), (8.4, 14.2), (8.4, 5.6), (9.3, 4.2),
               (10.4, 5.6), (10.6, 10), (10.9, 3.2), (12, 2.4), (13.1, 3.2), (13.4, 10), (13.8, 4.4), (15, 3.8),
               (15.9, 4.6), (16, 10.4), (16.6, 7), (17.8, 6.6), (18.7, 7.4), (18.6, 15), (16.2, 21)]
    ic.poly(outline, True, 0.9)
    return ic


def _magnifier(ic: Icon) -> Icon:
    ic.circle(10.2, 10.2, 6.6)
    ic.line(15.1, 15.1, 21, 21)
    return ic


def _zoom() -> Icon:
    return _magnifier(Icon())


def _zoom_in() -> Icon:
    ic = _magnifier(Icon())
    ic.line(7.4, 10.2, 13, 10.2)
    ic.line(10.2, 7.4, 10.2, 13)
    return ic


def _zoom_out() -> Icon:
    ic = _magnifier(Icon())
    ic.line(7.4, 10.2, 13, 10.2)
    return ic


def _fit_width() -> Icon:
    ic = Icon()
    ic.line(3.5, 5, 3.5, 19)
    ic.line(20.5, 5, 20.5, 19)
    ic.line(7, 12, 17, 12)
    ic.poly([(10, 9), (7, 12), (10, 15)], False)
    ic.poly([(14, 9), (17, 12), (14, 15)], False)
    return ic


def _fit_page() -> Icon:
    ic = Icon()
    for pts in ([(3.5, 8.5), (3.5, 3.5), (8.5, 3.5)], [(15.5, 3.5), (20.5, 3.5), (20.5, 8.5)],
                [(20.5, 15.5), (20.5, 20.5), (15.5, 20.5)], [(8.5, 20.5), (3.5, 20.5), (3.5, 15.5)]):
        ic.poly(pts, False)
    ic.rect(8.2, 7, 7.6, 10, 1)
    return ic


def _lines(ic: Icon, x0, x1, ys):
    for y in ys:
        ic.line(x0, y, x1, y)


def _one_up() -> Icon:
    ic = Icon()
    ic.rect(6, 3, 12, 18, 1.6)
    _lines(ic, 9, 15, (8, 12, 16))
    return ic


def _two_up() -> Icon:
    ic = Icon()
    ic.rect(2.5, 4.5, 8.6, 15, 1.4)
    ic.rect(12.9, 4.5, 8.6, 15, 1.4)
    _lines(ic, 5, 8.6, (9, 12.5, 16))
    _lines(ic, 15.4, 19, (9, 12.5, 16))
    return ic


def _continuous() -> Icon:
    ic = Icon()
    ic.rect(6, 2.5, 12, 8, 1.4)
    ic.rect(6, 13.5, 12, 8, 1.4)
    ic.line(9, 6.5, 15, 6.5)
    ic.line(9, 17.5, 15, 17.5)
    return ic


def _prev() -> Icon:
    return Icon().poly([(14.6, 5.2), (7.8, 12), (14.6, 18.8)], False)


def _next() -> Icon:
    return Icon().poly([(9.4, 5.2), (16.2, 12), (9.4, 18.8)], False)


def _copy() -> Icon:
    ic = Icon()
    ic.rect(8.5, 8.5, 12, 12.5, 1.6)
    ic.poly([(8.5, 16), (6, 16), (4, 14), (4, 6), (6, 4), (14, 4), (16, 6), (16, 8.5)], False, 0.8)
    return ic


def _dice() -> Icon:
    ic = Icon()
    ic.rect(3.5, 3.5, 17, 17, 3.2)
    for x, y in ((8.3, 8.3), (15.7, 8.3), (12, 12), (8.3, 15.7), (15.7, 15.7)):
        ic.dot(x, y, 1.35)
    return ic


def _printer() -> Icon:
    ic = Icon()
    ic.poly([(7, 8.5), (7, 3.5), (17, 3.5), (17, 8.5)], False, 0.6)
    ic.rect(2.8, 8.5, 18.4, 8.5, 2.2)
    ic.poly([(7, 14.5), (7, 20.5), (17, 20.5), (17, 14.5)], False, 0.6)
    ic.dot(17.3, 11.4, 1.1)
    ic.line(9.5, 17.5, 14.5, 17.5)
    return ic


def _tractor() -> Icon:  # a sheet of tractor-feed paper
    ic = Icon()
    ic.rect(3.5, 2.8, 17, 18.4, 1.4)
    ic.line(8.2, 3.5, 8.2, 20.5)
    ic.line(15.8, 3.5, 15.8, 20.5)
    for x in (5.85, 18.15):
        for y in (6.5, 12, 17.5):
            ic.dot(x, y, 0.95)
    _lines(ic, 10.6, 13.4, (8.5, 12, 15.5))
    return ic


def _looks() -> Icon:  # sliders
    ic = Icon()
    for y, kx in ((6.5, 9), (12, 15.5), (17.5, 8)):
        ic.line(3.5, y, kx - 2.7, y)
        ic.line(kx + 2.7, y, 20.5, y)
        ic.circle(kx, y, 2.0)
    return ic


def _info() -> Icon:
    ic = Icon()
    ic.circle(12, 12, 9)
    ic.line(12, 11, 12, 16.6)
    ic.dot(12, 7.7, 1.25)
    return ic


def _rotate_cw() -> Icon:  # a page under a clockwise arc
    ic = Icon()
    ic.arc(12, 12.5, 8.6, 200, 338)
    ex, ey = 12 + 8.6 * math.cos(math.radians(338)), 12.5 + 8.6 * math.sin(math.radians(338))
    _arrowhead(ic, ex + 0.5, ey + 0.4, -math.sin(math.radians(338)), math.cos(math.radians(338)), 4.2, 40)
    ic.rect(8.2, 11.6, 7.6, 9.4, 1.2)
    return ic


def _rotate_ccw() -> Icon:  # the mirror image of the clockwise one
    src = _rotate_cw()
    ic = Icon()
    ic.strokes = [([(GRID - x, y) for x, y in pts], closed) for pts, closed in src.strokes]
    ic.fills = [[(GRID - x, y) for x, y in pts] for pts in src.fills]
    return ic


def _banner() -> Icon:  # three sheets side by side with one long headline running across them
    ic = Icon()
    ic.rect(2.5, 6, 19, 12, 1.4)
    ic.line(8.83, 6, 8.83, 18)
    ic.line(15.17, 6, 15.17, 18)
    ic.line(5.5, 12, 18.5, 12)
    return ic


def _thumbs() -> Icon:  # a column of small pages
    ic = Icon()
    ic.rect(3.5, 3.5, 7, 7.5, 1.0)
    ic.rect(3.5, 13, 7, 7.5, 1.0)
    ic.line(14, 5.5, 20.5, 5.5)
    ic.line(14, 9, 20.5, 9)
    ic.line(14, 15, 20.5, 15)
    ic.line(14, 18.5, 20.5, 18.5)
    return ic


ICONS: Dict[str, Callable[[], Icon]] = {
    "open": _open, "save": _save, "reload": _reload, "hand": _hand, "zoom": _zoom, "zoom_in": _zoom_in,
    "zoom_out": _zoom_out, "fit_width": _fit_width, "fit_page": _fit_page, "one_up": _one_up, "two_up": _two_up,
    "continuous": _continuous, "prev": _prev, "next": _next, "copy": _copy, "dice": _dice, "printer": _printer,
    "tractor": _tractor, "looks": _looks, "info": _info, "rotate_cw": _rotate_cw, "rotate_ccw": _rotate_ccw,
    "banner": _banner, "thumbs": _thumbs,
}
_cache: Dict[str, Icon] = {}


def art(name: str) -> Icon:
    if name not in _cache:
        _cache[name] = ICONS[name]()
    return _cache[name]
