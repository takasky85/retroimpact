"""Okidata Okimate 10 / 20 (and Commodore MCS-820) emulation.

The Okimate prints colour graphics with a three-colour thermal-transfer ribbon (yellow, magenta, cyan panels).
From the Okimate 20 handbook: every printed line is three passes, one per ribbon colour, and a carriage return
steps the ribbon on to the next colour; the carriage return after the cyan pass also advances the paper.
Software such as GEOS therefore sends  ESC K n1 n2 <yellow>  CR  ESC K n1 n2 <magenta>  CR  ESC K n1 n2 <cyan>  CR.
A pass with nothing to print is just a bare CR.

Verified against the handbook: ESC K/L/Y/Z bit images (8 dots per column, 60/120/120/240 dpi across, 72 dpi down),
ESC A n (n/72 in line spacing), ESC 3 n, ESC J n, ESC EM (align ribbon, back to yellow), ESC LF n (n/144 in feed).
The GEOS Okimate 20 colour driver (BSW "ClrPrntDrvr" by David Durran, read from its 6502 code) sends each band of 8 pixel
rows as  ESC EM, then three passes (yellow, magenta, cyan) and then ESC LF 16 (16/144 in). A pass is 03, one 3-byte
column for each of the 8 pixel columns of every 8x8 screen card from the left edge up to the last non-empty card, 03 02,
and a shifted carriage return (0x8D). Of the 3 bytes the first holds pixel rows 0-3 and the second rows 4-7, every dot
doubled (2 pins = 1/72 in) with bit 0 at the top; the third is 0. A data byte of 03 is sent twice. A black-only page has
no ESC EM and a single pass per band. Not documented: the horizontal density (80 columns/in, the GEOS screen
resolution, is assumed).
"""
from __future__ import annotations

import re

import numpy as np

from . import charsets
from .engine import Engine, U
from .model import EmuOptions, PrinterProfile

PASS_INK = (4, 1, 2)  # ribbon panel order -> ink: yellow, magenta, cyan (ink codes as in DotPage.color)
GRAPHICS_DPI = {0x4B: 60.0, 0x4C: 120.0, 0x59: 120.0, 0x5A: 240.0}  # ESC K / L / Y / Z


def _walk_blocks(data: bytes):
    """Yield (start, end) of every ESC K / L / Y / Z bit-image block (header + payload)."""
    n, pos = len(data), 0
    while True:
        i = data.find(b"\x1b", pos)
        if i < 0 or i + 4 > n:
            return
        if data[i + 1] in GRAPHICS_DPI:
            end = i + 4 + (data[i + 2] | data[i + 3] << 8)
            if end > n:
                return
            yield i, end
            pos = end
        else:
            pos = i + 2


_BAND = re.compile(rb"\x1b\x19\x03")
BAND_DPI_X = 80.0  # estimated, see the module docstring
BAND_DPI_Y = 72.0  # one pixel row = 2 pins = 1/72 in; a band is 8 pixel rows = 16/144 in (the ESC LF 16 that follows)


def looks_okimate(data: bytes) -> bool:
    """Colour-ribbon graphics: bit-image blocks chained only by bare carriage returns (no line feeds between passes),
    or GEOS colour bands (ESC EM 3 ... 03 02 8D)."""
    if data.count(b"\x03\x02\x8d") >= 6 and (len(_BAND.findall(data)) >= 2 or data[:3] == b"\x1c\x1bP"):
        return True
    blocks = chained = 0
    ranges = []
    for _a, end in _walk_blocks(data):
        blocks += 1
        ranges.append((_a, end))
        if data[end:end + 1] == b"\r":
            j = end
            while data[j:j + 1] == b"\r":
                j += 1
            if data[j:j + 2] in (b"\x1bK", b"\x1bL", b"\x1bY", b"\x1bZ") or j - end >= 2:
                chained += 1
    if not (blocks >= 6 and chained >= 0.8 * blocks):
        return False
    # Commodore-mode Star / Riteman / Epson streams chain blocks with bare CRs too (CR = new line there). What sets the
    # Okimate apart is that the blocks of one line are three passes over the same picture, so neighbours look alike.
    sims = []
    for (a1, e1), (a2, e2) in zip(ranges[:80], ranges[1:81]):
        x = np.frombuffer(data[a1 + 4:e1], np.uint8)
        y = np.frombuffer(data[a2 + 4:e2], np.uint8)
        n = min(len(x), len(y))
        either = int(np.unpackbits(x[:n] | y[:n]).sum()) if n else 0
        if either:
            sims.append(int(np.unpackbits(x[:n] & y[:n]).sum()) / either)
    return bool(sims) and sum(sims) / len(sims) >= 0.55


class OkimateEngine(Engine):
    family = "okimate"

    def __init__(self, profile: PrinterProfile, opts: EmuOptions):
        super().__init__(profile, opts)
        self.colour_ribbon = False
        self.pass_mode = False
        self.skipped_bytes = 0
        self.bands = 0

    def reset_state(self):
        super().reset_state()
        self.phase = 0
        self.lead = U / 6.0
        self.pending_lead = 0.0

    def run(self, data: bytes):
        self.colour_ribbon = looks_okimate(data)
        self.pass_mode = data.count(b"\x03\x02\x8d") >= 6
        if self.pass_mode:
            self.lead = U * 16 / 144.0  # one band (12 picture rows) per line feed
        self.color = PASS_INK[0] if self.colour_ribbon else 0
        n = len(data)
        i = 0
        dec = charsets.decode
        while i < n:
            b = data[i]
            if b == 0x1B:
                i = self.escape(data, i + 1)
            elif b == 0x03 and self.pass_mode:  # start of a GEOS pass (black ribbon: no ESC EM in front of it)
                i = self._pass(data, i, 0)
            elif b >= 0x20 and b < 0x80 and b != 0x7F:
                ch = dec(b, self.codepage, 0)
                if ch is not None:
                    self.print_char(ch)
                i += 1
            else:
                if b == 0x0D:
                    self._carriage_return()
                elif b == 0x0A:
                    self.lf()
                elif b == 0x0C:
                    self.form_feed()
                elif b == 0x08:
                    self.backspace()
                elif b == 0x10:  # tab to character column: DLE n
                    i += 1
                i += 1

    def _carriage_return(self):
        if not self.colour_ribbon:
            self.cr()
            return
        self._end_pass()
        self.x = self.left_u
        self.phase += 1
        if self.phase == 3:  # after the cyan pass the paper moves on
            self.phase = 0
            self.advance_y(self.lead)
        self.color = PASS_INK[self.phase]

    def escape(self, data: bytes, i: int) -> int:
        n = len(data)
        if i >= n:
            return i
        c = data[i]
        if c in GRAPHICS_DPI:
            cnt = (data[i + 1] | data[i + 2] << 8) if i + 2 < n else 0
            body = data[i + 3:i + 3 + cnt]
            self.emit_columns(body, 1, GRAPHICS_DPI[c], 72.0, -6 * 20.0, msb_top=True, nbits=8)
            return i + 3 + cnt
        if c == 0x19:  # ESC EM: align the ribbon, back to yellow
            self.phase = 0
            if self.colour_ribbon:
                self.color = PASS_INK[0]
            if i + 1 < n and data[i + 1] == 0x03:  # GEOS colour band: three passes of doubled 24-dot columns
                return self._band(data, i + 1)
            return i + 1
        if c == 0x41:  # ESC A n: n/72 in line spacing (takes effect at once, or after ESC 2 with "strict ESC A")
            if i + 1 < n:
                self.pending_lead = data[i + 1] * U / 72.0
                if self.opts.esc_a_immediate:
                    self.lead = self.pending_lead
            return i + 2
        if c == 0x33:  # ESC 3 n: n/216 in
            if i + 1 < n:
                self.lead = data[i + 1] * U / 216.0
            return i + 2
        if c == 0x4A:  # ESC J n: feed n/216 in once
            if i + 1 < n:
                self.advance_y(data[i + 1] * U / 216.0)
            return i + 2
        if c == 0x0A:  # ESC LF n: fine line feed, n/144 in (0 = 256)
            if i + 1 < n:
                self.advance_y((data[i + 1] or 256) * U / 144.0)
            return i + 2
        if c == 0x10:  # ESC DLE n1 n2: absolute dot column
            if i + 2 < n:
                self.x = max(self.left_u, (data[i + 1] * 256 + data[i + 2]) * U / 60.0)
            return i + 3
        if c == 0x25:  # ESC % O n1 n2: 24-dot graphics (density not verified): skipped
            cnt = (data[i + 2] | data[i + 3] << 8) * 3 if i + 3 < n else 0
            self.skipped_bytes += cnt
            self.unknown += 1
            return i + 4 + cnt
        if c == 0x32:  # ESC 2: activate the ESC A spacing
            if self.pending_lead:
                self.lead = self.pending_lead
            return i + 1
        if c in (0x42, 0x50, 0x40):  # ESC B, ESC P, ESC @: harmless here
            if c == 0x40:
                self.reset_state()
                if self.colour_ribbon:
                    self.color = PASS_INK[0]
            return i + 1
        self.unknown += 1
        return i + 1

    def _pass(self, data: bytes, i: int, ink: int) -> int:
        """One pass: 3-byte columns (bits doubled), 03 02, 0x8D. Returns the index after the 0x8D."""
        n = len(data)
        end = data.find(b"\x8d", i)
        end = n if end < 0 else end
        body = data[i + 1:end]  # the 03 that opens a pass is a marker, not data
        if body.endswith(b"\x03\x02"):
            body = body[:-2]
        body = body.replace(b"\x03\x03", b"\x03")  # a data byte of 03 is sent twice
        cols = len(body) // 3
        if cols:
            bits = np.unpackbits(np.frombuffer(body[:cols * 3], np.uint8).reshape(cols, 3), axis=1, bitorder="little")[:, 0::2]  # cols x 12 rows, top first (bit 0 = top)
            packed = np.packbits(np.pad(bits, ((0, 0), (0, 4))), axis=1)  # cols x 2 bytes, 12 bits used
            self.color = ink
            self.emit_columns(packed.tobytes(), 2, BAND_DPI_X, BAND_DPI_Y, 0.0, msb_top=True, nbits=12)
        self._end_pass()
        self.x = self.left_u
        return min(end + 1, n)

    def _band(self, data: bytes, i: int) -> int:
        """Colour band: three passes (yellow, magenta, cyan)."""
        for ink in PASS_INK:
            i = self._pass(data, i, ink)
        self.bands += 1
        self.phase = 0
        self.color = PASS_INK[0]
        return i

    def finish(self, n_bytes: int = 0):
        doc = super().finish(n_bytes)
        while doc.pages and doc.pages[-1].n_dots == 0:  # a blank sheet the form feeds left behind
            doc.pages.pop()
        if self.skipped_bytes:
            doc.warnings.append(f"{self.skipped_bytes:,} byte(s) of Okimate data in a mode this emulator does not decode "
                                "(Commodore-style colour data behind ESC EM 3) were skipped")
        return doc
