"""Apple ImageWriter I/II emulation (Apple II, Macintosh drivers, Basilisk II / SheepShaver dumps)."""
from __future__ import annotations

import re

from . import charsets
from .engine import Engine, U
from .model import EmuOptions, PrinterProfile

# ESC letter -> (characters per inch, graphics dots per inch, proportional)
# Where a graphics row that is shorter than announced really ends: the next ribbon-colour / header / line-end commands.
# (Bitmap bytes essentially never contain these multi-byte sequences.)
_RESYNC = re.compile(rb"\r\x1bK[0-4]|\r\n\r\x1b|\x1b>\x1bP\x1bG\d")

# ESC K n: ImageWriter II ribbon panel -> ink (0 black, 1 yellow, 2 red = magenta ink, 3 blue = cyan ink)
RIBBON_COLOR = {0: 0, 1: 4, 2: 1, 3: 2}

PITCH = {
    "n": (9.0, 72.0, False),
    "N": (10.0, 80.0, False),
    "E": (12.0, 96.0, False),
    "e": (13.4, 107.2, False),
    "q": (15.0, 120.0, False),
    "Q": (17.16, 136.0, False),
    "p": (10.0, 144.0, True),
    "P": (10.0, 160.0, True),
}


class ImageWriterEngine(Engine):
    family = "imagewriter"

    def __init__(self, profile: PrinterProfile, opts: EmuOptions):
        super().__init__(profile, opts)
        self.gdpi = 80.0
        self.quality = 0  # 0 standard, 1 draft, 2 NLQ
        self.bold = False
        self._pending_cr = False
        self.short_rows = 0  # graphics rows that ended before the announced byte count
        self.style.face = "sans"
        self._apply_style()

    def reset_state(self):
        super().reset_state()
        self.gdpi = 80.0
        self.quality = 0
        self.bold = False

    def _apply_style(self):
        st = self.style
        st.nlq = self.quality == 2
        if self.quality == 2:
            st.emph = self.bold
            st.dstrike = False
        elif self.quality == 0:
            st.emph = True
            st.dstrike = self.bold
        else:
            st.emph = self.bold
            st.dstrike = False

    # ------------------------------------------------------------------
    def _digits(self, data: bytes, i: int, count: int):
        v = 0
        for k in range(count):
            c = data[i + k] if i + k < len(data) else 48
            v = v * 10 + (c - 48 if 48 <= c <= 57 else 0)
        return v, i + count

    def run(self, data: bytes):
        n = len(data)
        i = 0
        dec = charsets.decode
        while i < n:
            b = data[i]
            if b >= 0x20 and b != 0x7F:
                ch = dec(b, self.codepage, 0)
                if ch is not None:
                    self.print_char(ch)
                i += 1
            elif b == 0x1B:
                i = self.escape(data, i + 1)
            elif b == 0x09:  # Apple II parallel card firmware command: Ctrl-I [number] letter (e.g. Ctrl-I Z); not for the printer
                j = i + 1
                while j < n and 48 <= data[j] <= 57:
                    j += 1
                i = j + 1 if j < n and 65 <= data[j] <= 90 else i + 1
            else:
                if b == 0x08:
                    self.backspace()
                elif b == 0x0A:
                    self.lf()
                elif b == 0x0C:
                    self.form_feed()
                elif b == 0x0D:
                    self.cr()
                i += 1

    def escape(self, data: bytes, i: int) -> int:
        n = len(data)
        if i >= n:
            return i
        ch = chr(data[i])
        i += 1
        st = self.style
        if self._pending_cr:  # see _take: a lost carriage return, put back before the next command
            self._pending_cr = False
            self.cr()
        if ch in PITCH:
            cpi, gdpi, prop = PITCH[ch]
            st.base_cpi, st.prop, self.gdpi = cpi, prop, gdpi
            st.condensed = False
            return i
        if ch == "A":
            self.lead = U / 6.0
            return i
        if ch == "B":
            self.lead = U / 8.0
            return i
        if ch == "T":
            v, i = self._digits(data, i, 2)
            self.lead = v * U / 144.0
            return i
        if ch == "L":
            v, i = self._digits(data, i, 3)
            self.left_u = v * (U / (st.eff_cpi() if not st.prop else 10.0))
            self.x = max(self.x, self.left_u)
            return i
        if ch == "H":
            v, i = self._digits(data, i, 4)
            if self.opts.honor_page_length and v > 0:
                self.page_len = v * U / 144.0 if v >= 200 else v * self.lead
            return i
        if ch == "f":
            self.advance_y(self.lead)
            return i
        if ch == "r":
            self.y = max(0.0, self.y - self.lead)
            return i
        if ch == "v":
            return i
        if ch == "c":
            keep = (self.cur_pass, self.dirty)
            self.reset_state()
            self.cur_pass, self.dirty = keep
            self._apply_style()
            return i
        if ch in "<>":
            return i
        if ch == "!":
            self.bold = True
            self._apply_style()
            return i
        if ch == '"':
            self.bold = False
            self._apply_style()
            return i
        if ch == "X":
            st.underline = True
            return i
        if ch == "Y":
            st.underline = False
            return i
        if ch == "x":
            st.script = 1
            return i
        if ch == "y":
            st.script = 2
            return i
        if ch == "z":
            st.script = 0
            return i
        if ch == "w":
            st.vscale = 0.55
            return i
        if ch == "W":
            st.vscale = 1.0
            return i
        if ch == "a":
            c = data[i] if i < n else 48
            self.quality = {48: 0, 49: 1, 50: 2}.get(c, 0)
            self._apply_style()
            return i + 1
        if ch == "K":  # colour ribbon select, as an ASCII digit (a raw 0-4 is accepted too)
            c = data[i] if i < n else 48
            c = c - 48 if 48 <= c <= 57 else c
            self.color = RIBBON_COLOR.get(c, 0)
            return i + 1
        if ch == "s":
            return i + 1
        if ch in ("G", "S"):
            cnt, j = self._digits(data, i, 4)
            body, end = self._take(data, j, cnt)
            self._gfx(body)
            return end
        if ch == "g":
            cnt, j = self._digits(data, i, 3)
            body, end = self._take(data, j, cnt * 8)
            self._gfx(body)
            return end
        if ch == "V":
            cnt, j = self._digits(data, i, 4)
            val = data[j] if j < n else 0
            self._gfx(bytes([val]) * cnt)
            return j + 1
        if ch == "F":
            v, j = self._digits(data, i, 4)
            self.x = v * U / self.gdpi
            return j
        if ch == "R":
            cnt, j = self._digits(data, i, 3)
            c = data[j] if j < n else 32
            chx = charsets.decode(c, self.codepage, 0)
            if chx:
                for _ in range(cnt):
                    self.print_char(chx)
            return j + 1
        self.unknown += 1
        return i

    def _take(self, data: bytes, j: int, cnt: int):
        """The graphics bytes announced by a header: (bytes, index after them). If the next commands show up before `cnt`
        bytes have gone by, the capture lost data: the row ends there and parsing carries on at those commands."""
        end = min(j + cnt, len(data))
        m = _RESYNC.search(data, j, end + 6)
        if m and m.start() < j + cnt:
            self.short_rows += 1
            end = m.start()
            # the row was cut short, and the carriage return that ends it was usually lost with the missing bytes:
            # without it the next colour pass would start where this one stopped, out to the right of the page
            self._pending_cr = data[end:end + 1] != b"\r"
        return data[j:end], end

    def _gfx(self, body: bytes):
        if body:
            self.emit_columns(body, 1, self.gdpi, 72.0, -6 * 20.0, msb_top=False, nbits=8)
