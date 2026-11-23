"""Epson ESC/P (FX, LQ), IBM Proprinter and Star (Epson mode) emulation."""
from __future__ import annotations

import numpy as np

from . import charsets
from .engine import Engine, U
from .model import EmuOptions, PrinterProfile

# ESC * modes: (dpi_x, bytes_per_col, no_adjacent)
GFX_MODES = {
    0: (60, 1, False), 1: (120, 1, False), 2: (120, 1, True), 3: (240, 1, True),
    4: (80, 1, False), 5: (72, 1, False), 6: (90, 1, False), 7: (144, 1, False),
    32: (60, 3, False), 33: (120, 3, False), 38: (90, 3, False), 39: (180, 3, False),
    40: (360, 3, True), 71: (180, 6, False), 72: (360, 6, True), 73: (360, 6, True),
}

# Commands whose only job here is to swallow their fixed-length parameters.
SKIP_PARAMS = {
    "U": 1, "s": 1, "i": 1, "m": 1, "a": 1, "I": 1, "e": 2, "f": 2, "q": 1,
    "u": 1, "%": 1, "=": 0, ">": 0, "<": 0, "#": 0, "8": 0, "9": 0, "O": 0, "y": 1,
    "z": 1, "X": 3, "c": 0, "o": 0, "n": 0, "v": 0, "d": 1, "h": 1, "l2": 0,
}


class EscpEngine(Engine):
    family = "escp"

    def __init__(self, profile: PrinterProfile, opts: EmuOptions):
        self.variant = profile.family  # escp | ibm | star
        super().__init__(profile, opts)
        self.kly = {"K": 0, "L": 1, "Y": 2, "Z": 3}
        if profile.lq24:
            self.codepage = opts.codepage if opts.codepage != "auto" else "cp437"

    # ---------------------------------------------------------------- helpers
    @property
    def unit_v(self) -> float:
        return float(self.profile.unit_v)

    def _cell(self) -> float:
        return U / (self.style.eff_cpi() if not self.style.prop else 10.0)

    def _gfx_geometry(self, bytes_per_col: int):
        """Return (dpi_y, top_off) for a graphics column of this many bytes on this printer."""
        if self.profile.lq24:
            if bytes_per_col >= 3:
                return 180.0, -18 * 8.0
            return 60.0, -18 * 8.0
        return 72.0, -6 * 20.0

    # ---------------------------------------------------------------- main loop
    def run(self, data: bytes):
        n = len(data)
        i = 0
        dec = charsets.decode
        while i < n:
            b = data[i]
            if b >= 0x20 and b != 0x7F:
                ch = dec(b, self.codepage, self.national)
                if ch is not None:
                    self.print_char(ch)
                i += 1
                continue
            if b == 0x1B:
                i = self.escape(data, i + 1)
                continue
            if b < 0x20 and self.ibm_set2 and b in (1, 2, 3, 4, 5, 6, 0x15, 0x16, 0x17, 0x19, 0x1A, 0x1C, 0x1D, 0x1E, 0x1F):
                ch = charsets.CP437_LOW[b]
                self.print_char(ch)
                i += 1
                continue
            self.control(b)
            i += 1

    def control(self, b: int):
        st = self.style
        if b == 0x08:
            self.backspace()
        elif b == 0x09:
            self.x = self.next_hstop()
        elif b == 0x0A:
            self.lf()
        elif b == 0x0B:
            self.vtab()
        elif b == 0x0C:
            self.form_feed()
        elif b == 0x0D:
            self.cr()
        elif b == 0x0E:
            st.wmul_so = True
        elif b == 0x0F:
            st.condensed = True
        elif b == 0x12:
            st.condensed = False
        elif b == 0x14:
            st.wmul_so = False
        # BEL, DC1, DC3, CAN, DEL and the rest are ignored

    # ---------------------------------------------------------------- ESC
    def escape(self, data: bytes, i: int) -> int:
        n = len(data)
        if i >= n:
            return i
        c = data[i]
        i += 1
        ch = chr(c)
        st = self.style
        ibm = self.variant == "ibm"

        def u8(k=0):
            return data[i + k] if i + k < n else 0

        # ---- reset & line spacing
        if ch == "@":
            keep_pass = (self.cur_pass, self.dirty)
            self.reset_state()
            self.cur_pass, self.dirty = keep_pass
            return i
        if ch == "0":
            self.lead = U / 8.0
            return i
        if ch == "1":
            self.lead = 7.0 * U / 72.0
            return i
        if ch == "2":
            self.lead = self.lead_custom if self.lead_custom else U / 6.0
            return i
        if ch == "3":
            self.lead = u8() * U / self.unit_v
            return i + 1
        if ch == "A":
            self.lead_custom = u8() * U / 72.0
            if self.opts.esc_a_immediate:  # Star/Okidata/IBM-style; strict Epson waits for ESC 2
                self.lead = self.lead_custom
            return i + 1
        if ch == "J":
            self.advance_y(u8() * U / self.unit_v)
            return i + 1
        if ch == "j":
            self.y = max(0.0, self.y - u8() * U / self.unit_v)
            return i + 1
        if ch == "+":
            self.advance_y(u8() * U / 360.0)
            return i + 1
        # ---- page
        if ch == "C":
            if u8() == 0:
                if self.opts.honor_page_length and u8(1) > 0:
                    self.page_len = u8(1) * U
                return i + 2
            if self.opts.honor_page_length:
                self.page_len = u8() * self.lead
            return i + 1
        if ch == "N":
            self.bottom_margin = u8() * self.lead
            return i + 1
        if ch == "O":
            self.bottom_margin = 0.0
            return i
        if ch == "l":
            self.left_u = u8() * self._cell()
            self.x = max(self.x, self.left_u)
            return i + 1
        if ch == "Q":
            v = u8() * self._cell()
            if v > self.left_u:
                self.right_u = v
            return i + 1
        # ---- tabs
        if ch == "D":
            stops, k, last = [], i, 0
            while k < n and len(stops) < 32:
                v = data[k]
                k += 1
                if v == 0 or v <= last:
                    break
                stops.append(v * self._cell())
                last = v
            self.hstops = stops
            return k
        if ch == "B":
            stops, k, last = [], i, 0
            while k < n and len(stops) < 16:
                v = data[k]
                k += 1
                if v == 0 or v <= last:
                    break
                stops.append(v * self.lead)
                last = v
            self.vstops = stops
            return k
        if ch == "b":
            k = i + 1
            while k < n and data[k] != 0:
                k += 1
            return k + 1
        if ch == "/":
            return i + 1
        # ---- absolute / relative horizontal position
        if ch == "$":
            v = u8() + 256 * u8(1)
            self.x = v * U / 60.0
            return i + 2
        if ch == "\\":
            v = u8() + 256 * u8(1)
            if v >= 32768:
                v -= 65536
            if ibm:
                # IBM: ESC \ n1 n2 prints the next n bytes as CP437 graphics
                cnt = v
                for k in range(cnt):
                    if i + 2 + k < n:
                        cc = charsets.decode(data[i + 2 + k], "cp437", 0, True)
                        if cc is None and data[i + 2 + k] < 32:
                            cc = charsets.CP437_LOW[data[i + 2 + k]]
                        if cc:
                            self.print_char(cc)
                return i + 2 + max(0, cnt)
            unit = 8.0 if self.profile.lq24 else 12.0
            self.x = max(0.0, self.x + v * unit)
            return i + 2
        if ch == " ":
            unit = 8.0 if self.profile.lq24 else 12.0
            self.extra_space_u = u8() * unit
            return i + 1
        # ---- attributes
        if ch == "E":
            st.emph = True
            return i
        if ch == "F":
            st.emph = False
            return i
        if ch == "G":
            st.dstrike = True
            return i
        if ch == "H":
            st.dstrike = False
            return i
        if ch == "4":
            if not ibm:
                st.italic = True
            return i
        if ch == "5":
            if ibm:
                return i + 1
            st.italic = False
            return i
        if ch == "-":
            st.underline = bool(u8() & 1) or u8() == 49
            return i + 1
        if ch == "_":
            if ibm:
                st.underline = bool(u8() & 1) or u8() == 49
            else:
                st.overscore = bool(u8() & 1) or u8() == 49
            return i + 1
        if ch == "S":
            v = u8()
            st.script = 1 if v in (0, 48) else 2
            return i + 1
        if ch == "T":
            st.script = 0
            return i
        if ch == "W":
            st.wmul = 2 if (u8() & 1 or u8() == 49) else 1
            return i + 1
        if ch == "w":
            st.hmul = 2 if (u8() & 1 or u8() == 49) else 1
            return i + 1
        if ch == "M":
            st.base_cpi = 12.0
            return i
        if ch == "P":
            if ibm:
                st.prop = bool(u8() & 1 or u8() == 49)
                return i + 1
            st.base_cpi = 10.0
            st.prop = False
            return i
        if ch == ":":
            if ibm:
                st.base_cpi = 12.0
                return i
            return i + 3
        if ch == "g" and self.variant == "star" and u8() in GFX_MODES and i + 2 < n:  # Star SG-10/15: ESC g m n1 n2 (as ESC *)
            return self._graphics(data, i + 1, u8(), 2)
        if ch == "g":
            st.base_cpi = 15.0
            return i
        if ch == "p":
            st.prop = bool(u8() & 1 or u8() == 49)
            if st.prop:
                st.nlq = True if not self.profile.lq24 else st.nlq
            return i + 1
        if ch == "x":
            st.nlq = bool(u8() & 1 or u8() == 49)
            return i + 1
        if ch == "k":
            st.face = {0: "roman", 1: "sans", 2: "courier", 3: "courier", 4: "roman"}.get(u8() & 7, "roman")
            return i + 1
        if ch == "!":
            v = u8()
            st.base_cpi = 12.0 if v & 1 else 10.0
            st.prop = bool(v & 2)
            st.condensed = bool(v & 4)
            st.emph = bool(v & 8)
            st.dstrike = bool(v & 16)
            st.wmul = 2 if v & 32 else 1
            st.italic = bool(v & 64)
            st.underline = bool(v & 128)
            return i + 1
        if ch == "R":
            v = u8()
            if 0 <= v <= 12:
                self.national = v
            return i + 1
        if ch == "t":
            return i + 1
        if ch == "r":  # ESC r n - ribbon colour (Epson / Star colour printers): 0 black ... 6 green
            v = u8()
            self.color = (v - 48 if 48 <= v <= 54 else v) if (v <= 6 or 48 <= v <= 54) else self.color
            return i + 1
        if ch == "6":
            self.ibm_set2 = True
            return i
        if ch == "7":
            self.ibm_set2 = False
            return i
        if ch == "I" and ibm:
            v = u8()
            st.nlq = v in (2, 3, 50, 51)
            return i + 1
        if c in (0x0E, 0x0F):
            if c == 0x0E:
                st.wmul_so = True
            else:
                st.condensed = True
            return i
        # ---- downloaded characters (skipped)
        if ch == "&":
            lo, hi = u8(1), u8(2)
            cnt = max(0, hi - lo + 1)
            k = i + 3
            for _ in range(cnt):
                if k >= n:
                    break
                attr = data[k]
                width = (attr & 0x0F) if False else 11
                k += 1 + (width if not self.profile.lq24 else 33)
            return k
        # ---- graphics
        if ch in "KLYZ":
            mode = self.kly[ch]
            return self._graphics(data, i, mode, 2)
        if ch == "z" and self.variant == "star":  # Star Gemini / Delta: ESC z n1 n2 = quadruple density (Epson's ESC Z)
            return self._graphics(data, i, 3, 2)
        if ch == "?":
            s = chr(u8())
            if s in self.kly:
                self.kly[s] = u8(1)
            return i + 2
        if ch == "*":
            return self._graphics(data, i + 1, u8(), 2)
        if ch == "^":
            if ibm:
                if i < n:
                    cc = charsets.decode(data[i], "cp437", 0, True)
                    if cc is None and data[i] < 32:
                        cc = charsets.CP437_LOW[data[i]]
                    if cc:
                        self.print_char(cc)
                return i + 1
            m = u8()
            cnt = u8(1) + 256 * u8(2)
            body = data[i + 3 : i + 3 + cnt * 2]
            dpi = 120 if m else 60
            dpi_y, top = self._gfx_geometry(1)
            self.emit_columns(body, 2, dpi, dpi_y, top, True, False, True)
            return i + 3 + cnt * 2
        if ch == ".":
            return self._raster(data, i)
        if ch == "(":
            return self._ext(data, i)
        if ch == "[":
            # IBM extended command: ESC [ c nL nH data...
            ln = u8(1) + 256 * u8(2)
            return i + 3 + ln
        if c == 0x19:  # ESC EM n
            return i + 1
        if ch in SKIP_PARAMS:
            return i + SKIP_PARAMS[ch]
        self.unknown += 1
        return i

    # ---------------------------------------------------------------- graphics
    def _graphics(self, data: bytes, i: int, mode: int, hdr: int) -> int:
        n = len(data)
        spec = GFX_MODES.get(mode)
        if spec is None:
            self.unknown += 1
            return i + hdr
        n1 = data[i] if i < n else 0
        n2 = data[i + 1] if i + 1 < n else 0
        cols = n1 + 256 * n2
        dpi_x, bpc, noadj = spec
        body = data[i + 2 : i + 2 + cols * bpc]
        dpi_y, top = self._gfx_geometry(bpc)
        if bpc == 6:  # 48-pin modes: print the first 24 pins only
            arr = np.frombuffer(body[: (len(body) // 6) * 6], dtype=np.uint8).reshape(-1, 6)[:, :3]
            body = arr.tobytes()
            bpc_eff = 3
        else:
            bpc_eff = bpc
        self.emit_columns(body, bpc_eff, dpi_x, dpi_y, top, True, noadj)
        return i + 2 + cols * bpc

    def _raster(self, data: bytes, i: int) -> int:
        """ESC . c v h m nL nH data  (ESC/P2 raster graphics)."""
        n = len(data)
        if i + 6 > n:
            return n
        c, v, h, m = data[i], data[i + 1], data[i + 2], data[i + 3]
        width = data[i + 4] + 256 * data[i + 5]
        rb = (width + 7) // 8
        k = i + 6
        total = rb * m
        if c == 1:
            out = bytearray()
            while len(out) < total and k < n:
                ctr = data[k]
                k += 1
                if ctr < 128:
                    out += data[k : k + ctr + 1]
                    k += ctr + 1
                elif ctr > 128:
                    if k < n:
                        out += bytes([data[k]]) * (257 - ctr)
                    k += 1
            buf = bytes(out[:total])
        else:
            buf = data[k : k + total]
            k += total
        if len(buf) < total:
            buf = buf + bytes(total - len(buf))
        dx = U * h / 3600.0 if h else 0.0
        dy = U * v / 3600.0 if v else 0.0
        if m and width and v and h:
            bits = np.unpackbits(np.frombuffer(buf, dtype=np.uint8).reshape(m, rb), axis=1)[:, :width]
            rr, cc = np.nonzero(bits)
            if len(rr):
                xs = (self.x + cc * dx).astype(np.float32)
                ys = (self.tof_u + self.y + rr * dy - m * dy * 0.75).astype(np.float32)
                self.chunks.append((
                    xs, ys, (rr % 24).astype(np.int16),
                    np.full(len(rr), self.cur_pass + 1000003, dtype=np.int32),
                    np.zeros(len(rr), dtype=np.int8),
                    np.full(len(rr), self.color, dtype=np.uint8),
                ))
                self.page_dots += 1
                self.dirty = True
        self.x += width * dx
        return k

    def _ext(self, data: bytes, i: int) -> int:
        """ESC ( c nL nH data  (ESC/P2 extended commands).  Mostly skipped."""
        n = len(data)
        if i >= n:
            return i
        cmd = chr(data[i])
        ln = (data[i + 1] if i + 1 < n else 0) + 256 * (data[i + 2] if i + 2 < n else 0)
        body = data[i + 3 : i + 3 + ln]
        end = i + 3 + ln
        if cmd == "C" and len(body) >= 2 and self.opts.honor_page_length:
            v = body[0] + 256 * body[1]
            if v:
                self.page_len = v * U / 360.0
        elif cmd == "V" and len(body) >= 2:
            v = body[0] + 256 * body[1]
            self.y = v * U / 360.0
        elif cmd == "v" and len(body) >= 2:
            v = body[0] + 256 * body[1]
            if v >= 32768:
                v -= 65536
            self.advance_y(v * U / 360.0) if v > 0 else None
        elif cmd == "^":
            for b in body:
                ch = charsets.decode(b, self.codepage, self.national, True) or (
                    charsets.CP437_LOW[b] if b < 32 else None
                )
                if ch:
                    self.print_char(ch)
        elif cmd == "-" and len(body) >= 3:
            self.style.underline = body[1] in (1, 2, 5, 6) or (body[0] in (1, 2, 5, 6))
        return end
