"""Glyph generation for the dot-matrix emulators.

Three kinds of glyphs are produced as small 0/1 bitmaps on a printer's dot grid:

* DRAFT   - an original hand-built 5x7 font (descenders use pins 8-9), accents
            composed on the fly, box/block characters drawn procedurally.
* NLQ/LQ  - near-letter-quality: a bundled TTF is rasterised onto the printer's
            fine dot grid (12x14.4 dpi-ish for 9-pin NLQ, 180 dpi for 24-pin LQ)
            and thresholded, so the result really is made of dots.
* fallback- anything the draft font cannot draw is rasterised from a TTF onto the
            5x7 grid.

All coordinates are in "rows" (vertical pins) and "cols" (horizontal dot columns).
"""
from __future__ import annotations

import os
import sys
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _font_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "fonts")
    if os.path.isdir(cand):
        return cand
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "retroimpact", "fonts")
    return cand


FONT_DIR = _font_dir()
FACE_FILES = {
    "roman": "LiberationSerif-Regular.ttf",
    "sans": "LiberationSans-Regular.ttf",
    "courier": "LiberationMono-Regular.ttf",
    "symbols": "DejaVuSansMono.ttf",
}

# --------------------------------------------------------------------------------------
# Grid specifications
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class GridSpec:
    """Geometry of a glyph cell on the head's dot grid (units of 1/1440 inch = 'U')."""

    name: str
    gx: float  # horizontal dot pitch, U  (draft: reference value at 10 cpi)
    gy: float  # vertical dot pitch, U
    rows: int  # rows in a cell (box/block characters span all of them)
    base_row: int  # row index that sits on the baseline (bottom of capitals)
    cap_rows: int  # capital height in rows
    pins: int  # physical pins (for per-pin wear)
    thick: int  # box line thickness (dots)
    sep: int  # separation between the two strokes of a double line


DRAFT = GridSpec("draft", 24.0, 20.0, 9, 6, 7, 9, 1, 2)
NLQ9 = GridSpec("nlq", 12.0, 10.0, 24, 17, 14, 9, 2, 4)
LQ24 = GridSpec("lq", 8.0, 8.0, 30, 22, 18, 24, 3, 6)

# --------------------------------------------------------------------------------------
# Hand built 5x7 draft font. 9 rows: rows 0-6 body, rows 7-8 descender zone.
# --------------------------------------------------------------------------------------

_RAW = {
    " ": "",
    "!": "..#../..#../..#../..#../..#../...../..#..",
    '"': ".#.#./.#.#./.#.#.",
    "#": ".#.#./.#.#./#####/.#.#./#####/.#.#./.#.#.",
    "$": "..#../.####/#.#../.###./..#.#/####./..#..",
    "%": "##.../##..#/...#./..#../.#.../#..##/...##",
    "&": ".##../#..#./#.#../.#.../#.#.#/#..#./.##.#",
    "'": "..#../..#../.#...",
    "(": "...#./..#../.#.../.#.../.#.../..#../...#.",
    ")": ".#.../..#../...#./...#./...#./..#../.#...",
    "*": "...../..#../#.#.#/.###./#.#.#/..#../.....",
    "+": "...../..#../..#../#####/..#../..#../.....",
    ",": "...../...../...../...../..##./...#./..#..",
    "-": "...../...../...../#####/...../...../.....",
    ".": "...../...../...../...../...../.##../.##..",
    "/": "....#/....#/...#./..#../.#.../#..../#....",
    "0": ".###./#...#/#..##/#.#.#/##..#/#...#/.###.",
    "1": "..#../.##../..#../..#../..#../..#../.###.",
    "2": ".###./#...#/....#/...#./..#../.#.../#####",
    "3": "#####/...#./..#../...#./....#/#...#/.###.",
    "4": "...#./..##./.#.#./#..#./#####/...#./...#.",
    "5": "#####/#..../####./....#/....#/#...#/.###.",
    "6": "..##./.#.../#..../####./#...#/#...#/.###.",
    "7": "#####/....#/...#./..#../.#.../.#.../.#...",
    "8": ".###./#...#/#...#/.###./#...#/#...#/.###.",
    "9": ".###./#...#/#...#/.####/....#/...#./.##..",
    ":": "...../.##../.##../...../.##../.##../.....",
    ";": "...../.##../.##../...../.##../..#../.#...",
    "<": "...#./..#../.#.../#..../.#.../..#../...#.",
    "=": "...../...../#####/...../#####/...../.....",
    ">": ".#.../..#../...#./....#/...#./..#../.#...",
    "?": ".###./#...#/....#/...#./..#../...../..#..",
    "@": ".###./#...#/#.###/#.#.#/#.###/#..../.###.",
    "A": ".###./#...#/#...#/#####/#...#/#...#/#...#",
    "B": "####./#...#/#...#/####./#...#/#...#/####.",
    "C": ".###./#...#/#..../#..../#..../#...#/.###.",
    "D": "####./#...#/#...#/#...#/#...#/#...#/####.",
    "E": "#####/#..../#..../####./#..../#..../#####",
    "F": "#####/#..../#..../####./#..../#..../#....",
    "G": ".###./#...#/#..../#.###/#...#/#...#/.####",
    "H": "#...#/#...#/#...#/#####/#...#/#...#/#...#",
    "I": ".###./..#../..#../..#../..#../..#../.###.",
    "J": "..###/...#./...#./...#./...#./#..#./.##..",
    "K": "#...#/#..#./#.#../##.../#.#../#..#./#...#",
    "L": "#..../#..../#..../#..../#..../#..../#####",
    "M": "#...#/##.##/#.#.#/#.#.#/#...#/#...#/#...#",
    "N": "#...#/#...#/##..#/#.#.#/#..##/#...#/#...#",
    "O": ".###./#...#/#...#/#...#/#...#/#...#/.###.",
    "P": "####./#...#/#...#/####./#..../#..../#....",
    "Q": ".###./#...#/#...#/#...#/#.#.#/#..#./.##.#",
    "R": "####./#...#/#...#/####./#.#../#..#./#...#",
    "S": ".####/#..../#..../.###./....#/....#/####.",
    "T": "#####/..#../..#../..#../..#../..#../..#..",
    "U": "#...#/#...#/#...#/#...#/#...#/#...#/.###.",
    "V": "#...#/#...#/#...#/#...#/#...#/.#.#./..#..",
    "W": "#...#/#...#/#...#/#.#.#/#.#.#/##.##/#...#",
    "X": "#...#/#...#/.#.#./..#../.#.#./#...#/#...#",
    "Y": "#...#/#...#/.#.#./..#../..#../..#../..#..",
    "Z": "#####/....#/...#./..#../.#.../#..../#####",
    "[": ".###./.#.../.#.../.#.../.#.../.#.../.###.",
    "\\": "#..../#..../.#.../..#../...#./....#/....#",
    "]": ".###./...#./...#./...#./...#./...#./.###.",
    "^": "..#../.#.#./#...#",
    "_": "...../...../...../...../...../...../...../#####",
    "`": ".#.../..#../...#.",
    "a": "...../...../.###./....#/.####/#...#/.####",
    "b": "#..../#..../#.##./##..#/#...#/#...#/####.",
    "c": "...../...../.###./#...#/#..../#...#/.###.",
    "d": "....#/....#/.##.#/#..##/#...#/#...#/.####",
    "e": "...../...../.###./#...#/#####/#..../.###.",
    "f": "..##./.#..#/.#.../###../.#.../.#.../.#...",
    "g": "...../...../.####/#...#/#...#/.####/....#/#...#/.###.",
    "h": "#..../#..../#.##./##..#/#...#/#...#/#...#",
    "i": "..#../...../.##../..#../..#../..#../.###.",
    "j": "...#./...../..##./...#./...#./...#./...#./#..#./.##..",
    "k": "#..../#..../#..#./#.#../##.../#.#../#..#.",
    "l": ".##../..#../..#../..#../..#../..#../.###.",
    "m": "...../...../##.#./#.#.#/#.#.#/#.#.#/#.#.#",
    "n": "...../...../#.##./##..#/#...#/#...#/#...#",
    "o": "...../...../.###./#...#/#...#/#...#/.###.",
    "p": "...../...../####./#...#/#...#/####./#..../#..../#....",
    "q": "...../...../.####/#...#/#...#/.####/....#/....#/....#",
    "r": "...../...../#.##./##..#/#..../#..../#....",
    "s": "...../...../.####/#..../.###./....#/####.",
    "t": ".#.../.#.../###../.#.../.#.../.#..#/..##.",
    "u": "...../...../#...#/#...#/#...#/#..##/.##.#",
    "v": "...../...../#...#/#...#/#...#/.#.#./..#..",
    "w": "...../...../#...#/#...#/#.#.#/#.#.#/.#.#.",
    "x": "...../...../#...#/.#.#./..#../.#.#./#...#",
    "y": "...../...../#...#/#...#/#...#/.####/....#/#...#/.###.",
    "z": "...../...../#####/...#./..#../.#.../#####",
    "{": "..##./.#.../.#.../##.../.#.../.#.../..##.",
    "|": "..#../..#../..#../..#../..#../..#../..#..",
    "}": "##.../...#./...#./...##/...#./...#./##...",
    "~": "...../...../.#.../#.#.#/...#./...../.....",
    # a few extras the draft ROM would have
    "¡": "..#../...../..#../..#../..#../..#../..#..",
    "¿": "..#../...../..#../.#.../#..../#...#/.###.",
    "£": "..##./.#..#/.#.../###../.#.../.#.../#####",
    "¥": "#...#/.#.#./#####/..#../#####/..#../..#..",
    "¢": "..#../.####/#.#../#.#../#.#../.####/..#..",
    "°": ".##../#..#./.##..",
    "ß": ".##../#..#./#.#../#..#./#...#/#...#/#.##.",
    "µ": "...../...../#...#/#...#/#...#/#..##/.##.#/#..../#....",
    "±": "..#../..#../#####/..#../..#../...../#####",
    "×": "...../#...#/.#.#./..#../.#.#./#...#/.....",
    "÷": "...../..#../...../#####/...../..#../.....",
    "·": "...../...../...../..#../...../...../.....",
    "•": "...../...../.###./.###./.###./...../.....",
    "∙": "...../...../...../..#../...../...../.....",
    "§": ".###./#..../.##../#..#./.##../....#/.###.",
    "¶": ".####/#.#.#/#.#.#/.##.#/..#.#/..#.#/..#.#",
    "ª": ".##../...#./.###./#..#./.####",
    "º": ".##../#..#./#..#./.##../.####",
}

# Accent marks: rows 0-1 above lowercase letters; single row 0 above (compressed) capitals.
_ACC_LOWER = {
    "̀": [".#...", "..#.."],  # grave
    "́": ["...#.", "..#.."],  # acute
    "̂": ["..#..", ".#.#."],  # circumflex
    "̃": [".##.#", "#..#."],  # tilde
    "̈": [".#.#.", "....."],  # diaeresis
    "̊": ["..#..", ".#.#."],  # ring
    "̌": [".#.#.", "..#.."],  # caron
}
_ACC_UPPER = {
    "̀": ".#...",
    "́": "...#.",
    "̂": "..#..",
    "̃": ".#.#.",
    "̈": ".#.#.",
    "̊": "..#..",
    "̌": ".#.#.",
}
_CEDILLA = ["..#..", ".##.."]  # rows 7, 8


def _parse(spec: str) -> np.ndarray:
    rows = spec.split("/") if spec else []
    rows = [r for r in rows]
    for r in rows:
        assert len(r) == 5, (spec, r)
    rows += ["....."] * (9 - len(rows))
    return np.array([[1 if c == "#" else 0 for c in r] for r in rows], dtype=np.uint8)


_HAND: Dict[str, np.ndarray] = {k: _parse(v) for k, v in _RAW.items()}

_DESCENDER_COMPRESS = set("gjpqy")


@dataclass(frozen=True)
class Glyph:
    bits: np.ndarray  # (rows, cols) uint8
    full_cell: bool = False  # box/block glyph that fills the whole advance
    adv_cols: int = 0  # advance in dot columns for NLQ proportional (0 = n/a)

    @property
    def used_cols(self) -> int:
        cols = np.nonzero(self.bits.any(axis=0))[0]
        return int(cols.max()) + 1 if len(cols) else 0


# --------------------------------------------------------------------------------------
# TTF rasterisation (NLQ / fallback)
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=32)
def _calibrated_font(face: str, cap_px: int) -> ImageFont.FreeTypeFont:
    path = os.path.join(FONT_DIR, FACE_FILES[face])
    probe = ImageFont.truetype(path, 200)
    bb = probe.getbbox("H", anchor="ls")
    cap_ratio = (bb[3] - bb[1]) / 200.0
    size = max(4, int(round(cap_px / cap_ratio)))
    return ImageFont.truetype(path, size)


def _face_for(ch: str, face: str) -> str:
    return "symbols" if ord(ch) > 0x2FF else face


@lru_cache(maxsize=None)
def ttf_advance_px(ch: str, face: str, cap_px: int) -> float:
    f = _calibrated_font(_face_for(ch, face), cap_px)
    return float(f.getlength(ch))


@lru_cache(maxsize=None)
def ttf_bits(
    ch: str,
    face: str,
    spec: GridSpec,
    cols: int,
    threshold: float = 0.5,
    gx: Optional[float] = None,
) -> np.ndarray:
    """Rasterise ``ch`` into a (spec.rows, cols) 0/1 dot pattern."""
    gx_u = spec.gx if gx is None else gx
    sx = max(1, int(round(gx_u / 2.0)))
    sy = max(1, int(round(spec.gy / 2.0)))
    W, H = cols * sx, spec.rows * sy
    cap_px = spec.cap_rows * sy
    font = _calibrated_font(_face_for(ch, face), cap_px)
    base_y = (spec.base_row + 1) * sy
    adv = font.getlength(ch)
    pad = int(adv) + 8
    tmp = Image.new("L", (W + 2 * pad, H + 2 * sy), 0)
    d = ImageDraw.Draw(tmp)
    # draw with its advance centred on the cell centre
    ox = pad + W / 2.0 - adv / 2.0
    d.text((ox, base_y + sy), ch, font=font, fill=255, anchor="ls")
    # squeeze horizontally if the ink is wider than the cell
    bbox = tmp.getbbox()
    if bbox is not None:
        ink_w = bbox[2] - bbox[0]
        if ink_w > W * 0.98:
            scale = (W * 0.98) / ink_w
            crop = tmp.crop(bbox)
            nw = max(1, int(round(crop.width * scale)))
            crop = crop.resize((nw, crop.height), Image.LANCZOS)
            tmp = Image.new("L", tmp.size, 0)
            cx = pad + W / 2.0
            tmp.paste(crop, (int(round(cx - nw / 2.0)), bbox[1]))
    img = tmp.crop((pad, sy, pad + W, sy + H))
    a = np.asarray(img, dtype=np.float32) / 255.0
    a = a.reshape(spec.rows, sy, cols, sx).mean(axis=(1, 3))
    return (a >= threshold).astype(np.uint8)


# --------------------------------------------------------------------------------------
# Procedural box drawing / block elements
# --------------------------------------------------------------------------------------

_ADJ = {"LIGHT": 1, "HEAVY": 1, "DOUBLE": 2}


@lru_cache(maxsize=None)
def box_arms(ch: str) -> Optional[Tuple[int, int, int, int]]:
    """Return (left, right, up, down) stroke styles (0 none, 1 single, 2 double)."""
    try:
        name = unicodedata.name(ch)
    except ValueError:
        return None
    if not name.startswith("BOX DRAWINGS "):
        return None
    rest = name[len("BOX DRAWINGS ") :]
    if "DIAGONAL" in rest:
        return None
    rest = rest.replace("ARC ", "")
    for junk in ("TRIPLE ", "QUADRUPLE ", "DASH ", "DASHED "):
        rest = rest.replace(junk, "")
    arms = {"L": 0, "R": 0, "U": 0, "D": 0}
    cur = 1
    for part in rest.split(" AND "):
        words = part.split()
        if not words:
            continue
        if words[0] in _ADJ:
            cur = _ADJ[words[0]]
            words = words[1:]
        style = cur
        if words and words[-1] in ("SINGLE", "DOUBLE"):
            style = 1 if words[-1] == "SINGLE" else 2
            words = words[:-1]
        for w in words:
            if w == "UP":
                arms["U"] = style
            elif w == "DOWN":
                arms["D"] = style
            elif w == "LEFT":
                arms["L"] = style
            elif w == "RIGHT":
                arms["R"] = style
            elif w == "HORIZONTAL":
                arms["L"] = arms["R"] = style
            elif w == "VERTICAL":
                arms["U"] = arms["D"] = style
    if not any(arms.values()):
        return None
    return arms["L"], arms["R"], arms["U"], arms["D"]


def _strokes(style: int, centre: int, thick: int, sep: int):
    """Return list of (start, end) inclusive coordinate ranges for a stroke family."""
    if style == 1:
        s = centre - (thick - 1) // 2
        return [(s, s + thick - 1)]
    s1 = centre - sep // 2 - (thick - 1)
    s2 = centre + (sep + 1) // 2
    return [(s1, s1 + thick - 1), (s2, s2 + thick - 1)]


def box_bits(ch: str, cols: int, spec: GridSpec) -> Optional[np.ndarray]:
    """Draw a box-drawing character filling the whole cell so neighbours join up."""
    arms = box_arms(ch)
    if arms is None:
        return None
    L, R, U, D = arms
    rows = spec.rows
    t, sep = spec.thick, spec.sep
    if spec is DRAFT:
        cx, cy = 2, 3
    else:
        cx = cols // 2 - 1
        cy = spec.base_row - spec.cap_rows // 3  # about hyphen height
    hs = {s: _strokes(s, cy, t, sep) for s in (1, 2)}  # horizontal strokes (y ranges)
    vs = {s: _strokes(s, cx, t, sep) for s in (1, 2)}  # vertical strokes (x ranges)
    out = np.zeros((rows, cols), dtype=np.uint8)

    def fill(y0, y1, x0, x1):
        y0, y1 = max(0, y0), min(rows - 1, y1)
        x0, x1 = max(0, x0), min(cols - 1, x1)
        if y1 >= y0 and x1 >= x0:
            out[y0 : y1 + 1, x0 : x1 + 1] = 1

    # --- horizontal arms ------------------------------------------------------------
    for arm, style in (("L", L), ("R", R)):
        if not style:
            continue
        for k, (y0, y1) in enumerate(hs[style]):
            side = 0 if style == 1 else (-1 if k == 0 else 1)
            if side == 0:
                ref = max(U, D)
                if arm == "R":
                    x0, x1 = vs[ref or 1][0][0], cols - 1
                else:
                    x0, x1 = 0, vs[ref or 1][-1][1]
            else:
                P = U if side < 0 else D
                O = D if side < 0 else U
                if arm == "R":
                    x0 = vs[P][-1][0] if P else (vs[O][0][0] if O else vs[1][0][0])
                    x1 = cols - 1
                else:
                    x0 = 0
                    x1 = vs[P][0][1] if P else (vs[O][-1][1] if O else vs[1][-1][1])
            fill(y0, y1, x0, x1)
    # --- vertical arms --------------------------------------------------------------
    for arm, style in (("U", U), ("D", D)):
        if not style:
            continue
        for k, (x0, x1) in enumerate(vs[style]):
            side = 0 if style == 1 else (-1 if k == 0 else 1)
            if side == 0:
                ref = max(L, R)
                if arm == "D":
                    y0, y1 = hs[ref or 1][0][0], rows - 1
                else:
                    y0, y1 = 0, hs[ref or 1][-1][1]
            else:
                P = L if side < 0 else R
                O = R if side < 0 else L
                if arm == "D":
                    y0 = hs[P][-1][0] if P else (hs[O][0][0] if O else hs[1][0][0])
                    y1 = rows - 1
                else:
                    y0 = 0
                    y1 = hs[P][0][1] if P else (hs[O][-1][1] if O else hs[1][-1][1])
            fill(y0, y1, x0, x1)
    return out


_BLOCKS = {
    "▀": [(0, 0.5, 0, 1)],
    "▄": [(0.5, 1, 0, 1)],
    "█": [(0, 1, 0, 1)],
    "▌": [(0, 1, 0, 0.5)],
    "▐": [(0, 1, 0.5, 1)],
    "▖": [(0.5, 1, 0, 0.5)],
    "▗": [(0.5, 1, 0.5, 1)],
    "▘": [(0, 0.5, 0, 0.5)],
    "▝": [(0, 0.5, 0.5, 1)],
    "▙": [(0, 0.5, 0, 0.5), (0.5, 1, 0, 0.5), (0.5, 1, 0.5, 1)],
    "▚": [(0, 0.5, 0, 0.5), (0.5, 1, 0.5, 1)],
    "▛": [(0, 0.5, 0, 0.5), (0, 0.5, 0.5, 1), (0.5, 1, 0, 0.5)],
    "▜": [(0, 0.5, 0, 0.5), (0, 0.5, 0.5, 1), (0.5, 1, 0.5, 1)],
    "▞": [(0, 0.5, 0.5, 1), (0.5, 1, 0, 0.5)],
    "▟": [(0, 0.5, 0.5, 1), (0.5, 1, 0, 0.5), (0.5, 1, 0.5, 1)],
    "▁": [(0.875, 1, 0, 1)],
    "▂": [(0.75, 1, 0, 1)],
    "▃": [(0.625, 1, 0, 1)],
    "▅": [(0.375, 1, 0, 1)],
    "▆": [(0.25, 1, 0, 1)],
    "▇": [(0.125, 1, 0, 1)],
    "▉": [(0, 1, 0, 0.875)],
    "▊": [(0, 1, 0, 0.75)],
    "▋": [(0, 1, 0, 0.625)],
    "▍": [(0, 1, 0, 0.375)],
    "▎": [(0, 1, 0, 0.25)],
    "▏": [(0, 1, 0, 0.125)],
    "▔": [(0, 0.125, 0, 1)],
    "▕": [(0, 1, 0.875, 1)],
}


def block_bits(ch: str, cols: int, spec: GridSpec) -> Optional[np.ndarray]:
    rows = spec.rows
    if ch in _BLOCKS:
        out = np.zeros((rows, cols), dtype=np.uint8)
        for r0, r1, c0, c1 in _BLOCKS[ch]:
            ra, rb = int(round(r0 * rows)), max(int(round(r1 * rows)), int(round(r0 * rows)) + 1)
            ca, cb = int(round(c0 * cols)), max(int(round(c1 * cols)), int(round(c0 * cols)) + 1)
            out[ra:rb, ca:cb] = 1
        return out
    if ch in ("░", "▒", "▓"):
        yy, xx = np.mgrid[0:rows, 0:cols]
        if ch == "░":
            return (((xx % 4 == 0) & (yy % 4 == 0)) | ((xx % 4 == 2) & (yy % 4 == 2))).astype(np.uint8)
        if ch == "▒":
            return ((xx + yy) % 2 == 0).astype(np.uint8)
        return (1 - (((xx % 4 == 1) & (yy % 4 == 1)) | ((xx % 4 == 3) & (yy % 4 == 3)))).astype(np.uint8)
    if ch in ("■", "▪"):  # black square
        out = np.zeros((rows, cols), dtype=np.uint8)
        r0 = spec.base_row - max(2, spec.cap_rows * 2 // 3) + 1
        out[r0 : spec.base_row, max(0, cols // 2 - max(1, cols // 4)) : cols // 2 + max(1, cols // 4)] = 1
        return out
    return None


# --------------------------------------------------------------------------------------
# Draft glyph lookup
# --------------------------------------------------------------------------------------


def _compose(base: str, marks) -> Optional[np.ndarray]:
    g = _HAND.get(base)
    if g is None:
        return None
    g = g.copy()
    upper = base.isupper()
    cedilla = False
    accent = None
    for m in marks:
        if m == "̧":
            cedilla = True
        elif m in _ACC_LOWER:
            accent = m
    if accent is not None:
        if upper:
            body = np.vstack([g[0:1], g[2:7]])  # drop row 1 (compress capital to 6 rows)
            acc = np.array([[1 if c == "#" else 0 for c in _ACC_UPPER[accent]]], dtype=np.uint8)
            g = np.zeros((9, 5), dtype=np.uint8)
            g[0] = acc[0]
            g[1:7] = body
        else:
            if base == "i":
                g[0] = 0  # dotless
            acc = np.array([[1 if c == "#" else 0 for c in r] for r in _ACC_LOWER[accent]], dtype=np.uint8)
            g[0] = acc[0]
            g[1] = acc[1]
    if cedilla:
        for i, r in enumerate(_CEDILLA):
            g[7 + i] = np.array([1 if c == "#" else 0 for c in r], dtype=np.uint8)
    return g


_OVERLAY_SLASH = np.zeros((9, 5), dtype=np.uint8)
for _i, _c in enumerate([4, 4, 3, 2, 1, 0, 0]):
    _OVERLAY_SLASH[_i, _c] = 1


@lru_cache(maxsize=None)
def draft_glyph(ch: str) -> Glyph:
    """5x7(+descender) draft glyph for ``ch`` as a (9, 5) bitmap (or (9, 6) for box chars)."""
    if ch in _HAND:
        g = _HAND[ch]
        return Glyph(g)
    b = box_bits(ch, 6, DRAFT)
    if b is not None:
        return Glyph(b, full_cell=True)
    b = block_bits(ch, 6, DRAFT)
    if b is not None:
        return Glyph(b, full_cell=True)
    if ch == "ø":  # o slash
        g = _HAND["o"].copy()
        g[2:7] |= _OVERLAY_SLASH[2:7]
        return Glyph(g)
    if ch == "Ø":
        g = _HAND["O"].copy()
        g |= _OVERLAY_SLASH
        return Glyph(g)
    dec = unicodedata.normalize("NFD", ch)
    if len(dec) > 1 and dec[0] in _HAND:
        g = _compose(dec[0], dec[1:])
        if g is not None and all(m in _ACC_LOWER or m == "̧" for m in dec[1:]):
            return Glyph(g)
    if ch in (" ",):
        return Glyph(_HAND[" "])
    # fallback to TTF on the 5x7 grid
    bits = ttf_bits(ch, "courier", DRAFT, 5, 0.42)
    return Glyph(bits)


def squeeze_descender(bits: np.ndarray, limit_rows: int = 8) -> np.ndarray:
    """Fit a descender glyph into ``limit_rows`` pins (8-pin heads) by dropping its middle row."""
    if bits.shape[0] > limit_rows and bits[limit_rows:].any():
        keep = [r for r in range(bits.shape[0]) if r != 4]
        out = bits[keep]
        return np.vstack([out, np.zeros((bits.shape[0] - out.shape[0], bits.shape[1]), dtype=np.uint8)])
    return bits


# --------------------------------------------------------------------------------------
# NLQ glyph lookup
# --------------------------------------------------------------------------------------


@lru_cache(maxsize=None)
def nlq_glyph(ch: str, face: str, spec: GridSpec, cols: int, prop: bool) -> Glyph:
    """Near-letter-quality glyph.  For fixed pitch ``cols`` is the cell width in dot columns.

    For proportional text ``cols`` is ignored and the advance comes from the font.
    """
    if not prop:
        b = box_bits(ch, cols, spec)
        if b is not None:
            return Glyph(b, full_cell=True)
        b = block_bits(ch, cols, spec)
        if b is not None:
            return Glyph(b, full_cell=True)
    if ch == " " or ch == " ":
        c = cols
        if prop:
            c = max(3, int(round(spec.cap_rows * spec.gy * 0.5 / spec.gx * 1.0)))
        return Glyph(np.zeros((spec.rows, max(1, c)), dtype=np.uint8), adv_cols=c)
    face_use = face
    if prop:
        sy = max(1, int(round(spec.gy / 2.0)))
        sx = max(1, int(round(spec.gx / 2.0)))
        adv = ttf_advance_px(ch, _face_for(ch, face_use) if ord(ch) > 0x2FF else face_use, spec.cap_rows * sy)
        c = max(2, int(round(adv / sx)))
        bits = ttf_bits(ch, face_use, spec, c + 1, 0.5)
        return Glyph(bits, adv_cols=c)
    bits = ttf_bits(ch, face_use, spec, cols, 0.5)
    return Glyph(bits, adv_cols=cols)
