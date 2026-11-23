"""Commodore MPS-801 / MPS-803 / 1525 emulation (PETSCII text, bit-image graphics)."""
from __future__ import annotations

import numpy as np

from . import charsets
from .engine import Engine, U
from .model import EmuOptions, PrinterProfile

DOT_U = 24.0  # 60 dots per inch horizontally
LEAD_TEXT = U / 6.0
LEAD_GFX = U / 9.0


class MpsEngine(Engine):
    family = "mps"

    def __init__(self, profile: PrinterProfile, opts: EmuOptions):
        super().__init__(profile, opts)
        self.lower = opts.cbm_charset == "lower"
        self.ascii = opts.cbm_charset == "ascii"
        self.bitimage = False
        self.line_gfx = False
        self.after_cr = False
        self.lead = LEAD_TEXT
        self.cur_band: list = []  # (x of first column, uint8 array of 7-bit columns) for the graphics line being built
        self.band_pass = 0
        self.gfx_dpi = 60.0  # 120 for MPS-1000 style lines that carry more than 480 columns
        self.pending_gap = None  # (band, y of the line, pass) whose one-dot gap row is still to be filled in

    # ---------------------------------------------------------------- gap fill
    # A bit-image line is 7 dots (7/72 in) tall but the paper feeds 1/9 in = 8 dots, so a real MPS leaves an empty
    # row between lines. "fill" keeps the 1/9 in feed (the picture keeps its true height) and draws that missing row:
    # alternate columns copy the dot above and the dot below, which averages the two neighbours for solid areas,
    # edges and dither patterns alike. Only the gap between two consecutive graphics lines is filled.
    def _band_rows(self, band):
        xs = np.concatenate([x0 + np.arange(len(a)) * (U / self.gfx_dpi) for x0, a in band])
        arr = np.concatenate([a for _x0, a in band])
        return xs, (arr & 1).astype(bool), ((arr >> 6) & 1).astype(bool)

    def _flush_gap(self, nxt=None):
        p, self.pending_gap = self.pending_gap, None
        if p is None:
            return
        if not nxt:  # nothing below this line: there is no picture row to blend with
            return
        band, y_abs, bpass = p
        xs, _top, bottom = self._band_rows(band)
        xn, top_n, _b = self._band_rows(nxt)
        keys = np.rint(xs / (U / self.gfx_dpi)).astype(np.int64)
        below_map = dict(zip(np.rint(xn / (U / self.gfx_dpi)).astype(np.int64).tolist(), top_n.tolist()))
        below = np.array([below_map.get(k, False) for k in keys.tolist()], dtype=bool)
        fill = np.where(keys % 2 == 0, bottom, below)
        if not fill.any():
            return
        n = int(fill.sum())
        self.chunks.append((
            xs[fill].astype(np.float32), np.full(n, y_abs + U / 72.0, dtype=np.float32), np.full(n, 6, dtype=np.int16),
            np.full(n, bpass, dtype=np.int32), np.zeros(n, dtype=np.int8), np.zeros(n, dtype=np.uint8),
        ))
        self.dirty = True

    def print_char(self, ch):
        self._flush_gap()
        super().print_char(ch)

    def new_page(self, from_ff: bool = False):
        self._flush_gap()
        super().new_page(from_ff)

    def finish(self, n_bytes: int = 0):
        self._flush_gap()
        return super().finish(n_bytes)

    def newline(self):
        self._end_pass()
        self.x = self.left_u
        fill = self.opts.mps_gap == "fill"
        if fill and self.line_gfx and self.cur_band:
            y_abs = self.tof_u + self.y
            p = self.pending_gap
            if p is not None and abs(y_abs - (p[1] + LEAD_GFX)) < 1.0:
                self._flush_gap(self.cur_band)  # consecutive lines: blend with the line below
            else:
                self._flush_gap()
            self.pending_gap = (self.cur_band, y_abs, self.band_pass)
        elif self.line_gfx is False:
            self._flush_gap()
        self.cur_band = []
        if not self.skip_blank_lf():
            gfx_lead = 7 * U / 72.0 if self.opts.mps_gap == "close" else LEAD_GFX
            self.advance_y(gfx_lead if self.line_gfx else LEAD_TEXT)
        self.line_gfx = False
        self.style.reverse = False

    def detect_charset(self, data: bytes):
        if self.opts.cbm_charset != "auto":
            return
        lower = sum(1 for b in data if 0x61 <= b <= 0x7A)
        upper = sum(1 for b in data if 0x41 <= b <= 0x5A)
        hi = sum(1 for b in data if b >= 0x80)
        if hi == 0 and lower > 20 and lower > 2 * upper:
            self.ascii = True
        elif hi == 0 and lower >= 2 and upper >= 0.85 * (upper + lower) and upper + lower >= 30:
            self.lower = True  # PETSCII lower/upper text: lower-case letters are $41-$5A, capitals $61-$7A

    def detect_density(self, data: bytes):
        # A bit-image line of more than 480 columns cannot be the 60 dpi MPS-801 format (it would be wider than the
        # paper); the MPS-1000 GEOS driver sends 960 columns (each source dot twice) for the same 8 inch line.
        best = run = 0
        for b in data:
            if b >= 0x80:
                run += 1
                best = max(best, run)
            elif b != 0:
                run = 0
        if best > 490:
            self.gfx_dpi = 120.0

    def run(self, data: bytes):
        self.detect_charset(data)
        self.detect_density(data)
        n = len(data)
        i = 0
        st = self.style
        while i < n:
            b = data[i]
            if self.bitimage:
                if b >= 0x80:
                    j = i
                    while j < n and data[j] >= 0x80:
                        j += 1
                    cols = bytes(x & 0x7F for x in data[i:j])
                    self.cur_band.append((self.x, np.frombuffer(cols, dtype=np.uint8)))
                    self.band_pass = self.cur_pass + 1000003
                    self.emit_columns(cols, 1, self.gfx_dpi, 72.0, -120.0, msb_top=False, nbits=7)
                    self.line_gfx = True
                    self.after_cr = False
                    i = j
                    continue
                if b == 26:
                    cnt = data[i + 1] if i + 1 < n else 1
                    val = data[i + 2] if i + 2 < n else 0x80
                    cnt = cnt or 256
                    cols = bytes([val & 0x7F]) * cnt
                    self.cur_band.append((self.x, np.frombuffer(cols, dtype=np.uint8)))
                    self.band_pass = self.cur_pass + 1000003
                    self.emit_columns(cols, 1, self.gfx_dpi, 72.0, -120.0, msb_top=False, nbits=7)
                    self.line_gfx = True
                    i += 3
                    continue
                if b == 13:  # CR keeps bit-image mode (PrintMaster and others send CHR$(8) once)
                    self.newline()
                    self.line_gfx = True
                    self.after_cr = True
                    i += 1
                    continue
                if b == 0 or (b == 10 and self.after_cr):
                    i += 1
                    continue
                self.bitimage = False
            if b == 13:
                self.newline()
                self.after_cr = True
                i += 1
                continue
            if b == 10:
                if self.after_cr:
                    self.after_cr = False
                else:
                    self.newline()
                i += 1
                continue
            self.after_cr = False
            if b == 8:
                self.bitimage = True
                self.line_gfx = True
            elif b == 14:
                st.wmul = 2
            elif b == 15:
                st.wmul = 1
            elif b == 16:
                if i + 2 < n and 48 <= data[i + 1] <= 57 and 48 <= data[i + 2] <= 57:
                    pos = (data[i + 1] - 48) * 10 + (data[i + 2] - 48)
                    self.x = pos * (U / 10.0)
                    i += 2
            elif b == 17:
                self.lower = True
            elif b == 145:
                self.lower = False
            elif b == 18:
                st.reverse = True
            elif b == 146:
                st.reverse = False
            elif b == 12:
                self.form_feed()
            elif b == 27:
                if i + 1 < n and data[i + 1] == 16 and i + 3 < n:
                    self.x = (((data[i + 2] & 1) << 8) | data[i + 3]) * DOT_U
                    i += 3
                elif i + 2 < n and data[i + 1] == 45:
                    st.underline = bool(data[i + 2] & 1)
                    i += 2
                else:
                    self.unknown += 1
                    i += 1
            else:
                ch = None
                if self.ascii:
                    if 0x20 <= b < 0x7F:
                        ch = chr(b)
                else:
                    ch = charsets.petscii_char(b, self.lower)
                if ch is not None:
                    self.print_char(ch)
            i += 1
