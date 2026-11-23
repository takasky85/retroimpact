"""Shared printer-emulation engine: paging, text-to-dots, graphics columns.

Everything is expressed in U (1/1440 inch).  The emulators (escp / imagewriter / commodore)
parse their byte streams and call into this engine, which produces ``DotPage`` objects
holding the raw list of struck dots.  Nothing here knows about pixels or ribbons.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from . import glyphs as G
from .model import DotPage, Document, EmuOptions, PrinterProfile

U = 1440.0


@dataclass
class TextStyle:
    nlq: bool = False
    face: str = "roman"
    base_cpi: float = 10.0
    condensed: bool = False
    prop: bool = False
    wmul: int = 1
    wmul_so: bool = False  # one-line double width (SO)
    hmul: int = 1
    vscale: float = 1.0
    emph: bool = False
    dstrike: bool = False
    italic: bool = False
    script: int = 0  # 0 none, 1 super, 2 sub
    underline: bool = False
    overscore: bool = False
    reverse: bool = False

    def eff_cpi(self) -> float:
        c = self.base_cpi
        if self.condensed and c < 14:
            c = c * 12.0 / 7.0
        return c


class Engine:
    family = "escp"

    def __init__(self, profile: PrinterProfile, opts: EmuOptions):
        self.profile = profile
        self.opts = opts
        self.pages: List[DotPage] = []
        self.brush_ids: Dict[tuple, int] = {}
        self.brush_data: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        self.unknown = 0
        self._adv_cache: Dict[int, float] = {}
        self.notes: List[str] = []
        self.codepage = opts.codepage if opts.codepage != "auto" else profile.default_codepage
        self.tof_u = opts.tof_offset_in * U
        self.page_idx = 0
        self.cr_adds_lf = False
        self.lf_adds_cr = False
        self.extra_space_u = 0.0
        self.reset_state()
        self._start_page()

    # ------------------------------------------------------------------ state
    def reset_state(self):
        self.style = TextStyle(nlq=self.profile.nlq_default)
        self.x = 0.0
        self.y = 0.0
        self.lead = U / 6.0
        self.lead_custom: Optional[float] = None
        self.left_u = 0.0
        self.right_u = (13.6 if self.opts.wide_carriage else 8.0) * U
        self.page_len = self.opts.page_length_in * U
        self.bottom_margin = 0.0
        self.extra_space_u = 0.0
        self.national = 0
        self.ibm_set2 = False
        self.hstops: List[float] = []
        self.vstops: List[float] = []
        self.cur_pass = 0
        self.color = 0  # ESC r n ribbon colour
        self.dirty = False
        self.line_ink = False
        self.blank_run = 0
        self.last_adv = U / 10.0

    # ------------------------------------------------------------------ pages
    def _start_page(self):
        self.st_b: List[int] = []
        self.st_x: List[float] = []
        self.st_y: List[float] = []
        self.st_p: List[int] = []
        self.st_c: List[int] = []
        self.chunks: List[tuple] = []
        self.page_len_at_start = self.page_len
        self.page_dots = 0
        self.unk_at_start = self.unknown
        carry, self.carry = getattr(self, "carry", None), None
        if carry is not None:  # dots that went over the perforation of the previous sheet land on top of this one
            self.chunks.append(carry)
            self.page_dots = 1

    def _expand_stamps(self):
        if not self.st_b:
            return
        B = np.asarray(self.st_b, dtype=np.int32)
        X = np.asarray(self.st_x, dtype=np.float64)
        Y = np.asarray(self.st_y, dtype=np.float64)
        P = np.asarray(self.st_p, dtype=np.int32)
        C = np.asarray(self.st_c, dtype=np.uint8)
        for bid in np.unique(B):
            sel = np.nonzero(B == bid)[0]
            ox, oy, pin = self.brush_data[int(bid)]
            k = len(ox)
            if k == 0:
                continue
            xs = (X[sel][:, None] + ox[None, :]).ravel().astype(np.float32)
            ys = (Y[sel][:, None] + oy[None, :]).ravel().astype(np.float32)
            pins = np.broadcast_to(pin[None, :], (len(sel), k)).ravel().astype(np.int16)
            ps = np.broadcast_to(P[sel][:, None], (len(sel), k)).ravel().astype(np.int32)
            ds = (ps & 1).astype(np.int8)
            cs = np.broadcast_to(C[sel][:, None], (len(sel), k)).ravel().astype(np.uint8)
            self.chunks.append((xs, ys, pins, ps, ds, cs))
        self.st_b, self.st_x, self.st_y, self.st_p, self.st_c = [], [], [], [], []

    def _finish_page(self, keep_empty: bool = False):
        self._expand_stamps()
        if not self.chunks:
            if not keep_empty:
                return False
            x = y = np.zeros(0, np.float32)
            pin = np.zeros(0, np.int16)
            ps = np.zeros(0, np.int32)
            ds = np.zeros(0, np.int8)
            cs = np.zeros(0, np.uint8)
        else:
            x = np.concatenate([c[0] for c in self.chunks])
            y = np.concatenate([c[1] for c in self.chunks])
            pin = np.concatenate([c[2] for c in self.chunks])
            ps = np.concatenate([c[3] for c in self.chunks])
            ds = np.concatenate([c[4] for c in self.chunks])
            cs = np.concatenate([c[5] for c in self.chunks])
        # continuous paper: ink that falls below the bottom edge of this sheet is printed across the perforation,
        # i.e. at the top of the next sheet (banners from Print Shop / PrintMaster cross it all the time)
        self.carry = None
        over = y > np.float32(self.page_len_at_start)
        if over.any():
            self.carry = (x[over], y[over] - np.float32(self.page_len_at_start), pin[over], ps[over], ds[over], cs[over])
            keep = ~over
            x, y, pin, ps, ds, cs = x[keep], y[keep], pin[keep], ps[keep], ds[keep], cs[keep]
        self.pages.append(
            DotPage(
                index=len(self.pages),
                width_in=self.opts.page_width_in,
                height_in=self.page_len_at_start / U,
                x=x, y=y, pin=pin, pass_no=ps, dirn=ds, profile_key=self.profile.key,
                color=cs if cs.any() else None,
                unknown=self.unknown - getattr(self, "unk_at_start", 0),
            )
        )
        return True

    def new_page(self, from_ff: bool = False):
        if from_ff and self.opts.skip_blank_ff and self.page_dots == 0 and self.y < 1.0:
            return
        self._end_pass()
        self._finish_page(keep_empty=True)
        self._start_page()
        self.line_ink = False
        self.blank_run = 0
        self.y = 0.0
        self.page_idx += 1

    # ------------------------------------------------------------------ motion
    def _end_pass(self):
        if self.dirty:
            self.cur_pass += 1
            self.dirty = False

    def cr(self):
        self._end_pass()
        self.x = self.left_u
        if self.cr_adds_lf:
            self.lf(keep_x=True)

    def skip_blank_lf(self) -> bool:
        """True if this line feed should be swallowed under the 'blank lines' option."""
        if self.line_ink:
            self.line_ink = False
            self.blank_run = 0
            return False
        self.blank_run += 1
        mode = self.opts.blank_lines
        return mode == "remove" or (mode == "collapse" and self.blank_run > 1)

    def lf(self, keep_x: bool = False):
        self._end_pass()
        self.style.wmul_so = False
        if not self.skip_blank_lf():
            self.advance_y(self.lead)
        if self.lf_adds_cr and not keep_x:
            self.x = self.left_u

    def advance_y(self, dy: float):
        self._end_pass()
        self.y += dy
        limit = self.page_len - self.bottom_margin
        if self.y >= limit - 1e-3 and dy > 0:
            over = self.y - limit
            self.new_page()
            self.y = over if self.bottom_margin == 0 else 0.0

    def form_feed(self):
        self.new_page(from_ff=True)
        if self.lf_adds_cr:
            self.x = self.left_u

    def next_hstop(self) -> float:
        x = self.x
        for s in self.hstops:
            if s > x + 0.5:
                return s
        if self.hstops:
            return x
        cell = U / (self.style.eff_cpi() if not self.style.prop else 10.0)
        step = 8 * cell
        return (int(x / step + 1e-6) + 1) * step

    def vtab(self):
        if self.vstops:
            for s in self.vstops:
                if s > self.y + 0.5:
                    self.advance_y(s - self.y)
                    return
            self.advance_y(self.lead)
            return
        self.advance_y(self.lead)

    # ------------------------------------------------------------------ text
    def _mode_spec(self) -> G.GridSpec:
        nlq = self.opts.force_nlq if self.opts.force_nlq is not None else self.style.nlq
        if not nlq:
            return G.DRAFT
        return G.LQ24 if self.profile.lq24 else G.NLQ9

    def cell_u(self) -> float:
        st = self.style
        return U / (st.eff_cpi() if not st.prop else 10.0) * self._wm()

    def _wm(self) -> int:
        return 2 * self.style.wmul if self.style.wmul_so else self.style.wmul

    def get_brush(self, ch: str) -> Tuple[int, float]:
        st = self.style
        spec = self._mode_spec()
        wm = self._wm()
        key = (
            ch, spec.name, st.face, round(st.eff_cpi(), 3), st.prop, wm, st.hmul, st.vscale,
            st.emph, st.dstrike, st.italic, st.script, st.underline, st.overscore, st.reverse,
            self.family,
        )
        hit = self.brush_ids.get(key)
        if hit is not None:
            return hit, self._adv_cache[hit]
        bid, adv = self._build_brush(ch, spec, wm)
        self.brush_ids[key] = bid
        self._adv_cache[bid] = adv
        return bid, adv

    def _build_brush(self, ch: str, spec: G.GridSpec, wm: int) -> Tuple[int, float]:
        st = self.style
        hm = st.hmul
        cpi = st.eff_cpi() if not st.prop else 10.0
        if spec is G.DRAFT:
            glyph = G.draft_glyph(ch)
            bits = glyph.bits
            if self.family == "mps":
                bits = G.squeeze_descender(bits, 8)
            if st.reverse:
                rev = np.ones((9, 6), dtype=np.uint8)
                rev[7:, :] = 0
                rev[: bits.shape[0], : bits.shape[1]] ^= bits
                rev[7:, :] = 0
                bits = rev
            gx = 240.0 / cpi
            gy = spec.gy
            if st.prop:
                if ch == " " or ch == " ":
                    adv_cols = 3
                elif glyph.full_cell:
                    adv_cols = bits.shape[1]
                else:
                    adv_cols = max(2, glyph.used_cols + 1 + (1 if glyph.used_cols >= 5 else 0))
                adv = adv_cols * gx * wm
            else:
                adv = U / cpi * wm
            pin_div = 1
            emph_dx, ds_dy = 12.0, 20.0 / 3.0
            under_pin = 8
            under_rows = 2
        else:
            cols_cell = max(2, int(round((U / cpi) / spec.gx)))
            glyph = G.nlq_glyph(ch, st.face, spec, cols_cell, st.prop)
            bits = glyph.bits
            gx, gy = spec.gx, spec.gy
            adv = (glyph.adv_cols if st.prop else cols_cell) * gx * wm
            pin_div = 2 if spec is G.NLQ9 else 1
            emph_dx, ds_dy = gx * 0.5, gy * 0.5
            under_pin = 8 if spec is G.NLQ9 else 23
            under_rows = 4 if spec is G.NLQ9 else 5
        adv += self.extra_space_u

        rr, cc = np.nonzero(bits)
        if len(rr):
            rr = rr.astype(np.int32)
            cc = cc.astype(np.int32)
            cc2 = (cc[:, None] * wm + np.arange(wm)[None, :]).ravel()
            rr2 = np.repeat(rr, wm)
            rel = ((rr2[:, None] - spec.base_row) * hm - (hm - 1) + np.arange(hm)[None, :]).ravel()
            cc3 = np.repeat(cc2, hm)
            pins = np.repeat(rr2, hm) // pin_div
            ox = cc3.astype(np.float64) * gx
            oy = rel.astype(np.float64) * gy * st.vscale
        else:
            ox = oy = np.zeros(0)
            pins = np.zeros(0, dtype=np.int32)
        if st.italic and len(ox):
            ox = ox - 0.25 * oy
        if st.script and len(ox):
            shift = -50.0 if st.script == 1 else 40.0
            oy = oy * 0.7 + shift
        copies = [(0.0, 0.0)]
        if st.emph:
            copies.append((emph_dx, 0.0))
        if st.dstrike:
            copies += [(a, b + ds_dy) for a, b in copies]
        if len(copies) > 1 and len(ox):
            ox = np.concatenate([ox + a for a, _ in copies])
            oy = np.concatenate([oy + b for _, b in copies])
            pins = np.concatenate([pins] * len(copies))
        extras_x, extras_y, extras_p = [], [], []
        step = gx if spec is not G.DRAFT else 12.0
        if st.underline:
            xs = np.arange(0.0, adv + step * 0.5, step)
            extras_x.append(xs)
            extras_y.append(np.full_like(xs, under_rows * gy))
            extras_p.append(np.full(len(xs), under_pin))
        if st.overscore:
            xs = np.arange(0.0, adv + step * 0.5, step)
            extras_x.append(xs)
            extras_y.append(np.full_like(xs, -(spec.cap_rows + 1.5) * gy))
            extras_p.append(np.zeros(len(xs)))
        if extras_x:
            ox = np.concatenate([ox] + extras_x)
            oy = np.concatenate([oy] + extras_y)
            pins = np.concatenate([pins] + [p.astype(np.int32) for p in extras_p])
        bid = len(self.brush_data)
        self.brush_data.append((ox.astype(np.float64), oy.astype(np.float64), pins.astype(np.int16)))
        return bid, adv

    def print_char(self, ch: str):
        bid, adv = self.get_brush(ch)
        if (
            self.opts.auto_wrap
            and self.x + adv > self.right_u + 0.5
            and self.x > self.left_u + 0.5
        ):
            self.cr()
            if not self.cr_adds_lf:
                self.lf(keep_x=True)
        if len(self.brush_data[bid][0]):
            self.st_b.append(bid)
            self.st_x.append(self.x)
            self.st_y.append(self.tof_u + self.y)
            self.st_p.append(self.cur_pass)
            self.st_c.append(self.color)
            self.dirty = True
            self.line_ink = True
            self.page_dots += 1
        self.x += adv
        self.last_adv = adv

    def backspace(self):
        self.x = max(self.left_u, self.x - self.last_adv)

    # ------------------------------------------------------------------ graphics
    def emit_columns(
        self,
        data: bytes,
        bytes_per_col: int,
        dpi_x: float,
        dpi_y: float,
        top_off: float,
        msb_top: bool = True,
        no_adjacent: bool = False,
        nine_pin: bool = False,
        nbits: Optional[int] = None,
        advance: bool = True,
    ):
        n = len(data) // bytes_per_col
        if n <= 0:
            return
        arr = np.frombuffer(data[: n * bytes_per_col], dtype=np.uint8).reshape(n, bytes_per_col)
        bits = np.unpackbits(arr, axis=1, bitorder="big" if msb_top else "little")
        if nine_pin and bytes_per_col == 2:
            bits = np.concatenate([bits[:, :8], bits[:, 8:9]], axis=1)
        if nbits is not None:
            bits = bits[:, :nbits]
        if no_adjacent:
            idx = np.arange(n)[:, None]
            prev = np.vstack([np.zeros((1, bits.shape[1]), dtype=np.uint8), bits[:-1]])
            starts = (bits == 1) & (prev == 0)
            run0 = np.maximum.accumulate(np.where(starts, idx, -1), axis=0)
            bits = ((bits == 1) & (((idx - run0) % 2) == 0)).astype(np.uint8)
        cc, kk = np.nonzero(bits)
        dx = U / dpi_x
        dy = U / dpi_y
        if len(cc):
            xs = (self.x + cc * dx).astype(np.float32)
            ys = (self.tof_u + self.y + top_off + kk * dy).astype(np.float32)
            pins = kk.astype(np.int16)
            ps = np.full(len(cc), self.cur_pass + 1000003, dtype=np.int32)  # graphics passes are distinct
            ds = np.zeros(len(cc), dtype=np.int8)
            cs = np.full(len(cc), self.color, dtype=np.uint8)
            self.chunks.append((xs, ys, pins, ps, ds, cs))
            self.page_dots += 1
            self.dirty = True
            self.line_ink = True
        if advance:
            self.x += n * dx

    # ------------------------------------------------------------------ result
    def finish(self, n_bytes: int = 0) -> Document:
        self._end_pass()
        self._expand_stamps()
        if self.chunks:
            self._finish_page()
        max_x = 0.0
        for p in self.pages:
            if p.n_dots:
                max_x = max(max_x, float(p.x.max()) / U)
        carriage = 8.0 if max_x <= 8.2 else 13.6
        return Document(
            pages=self.pages,
            profile=self.profile,
            carriage_in=carriage,
            notes=self.notes,
            n_bytes=n_bytes,
            n_unknown=self.unknown,
        )
