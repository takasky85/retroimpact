"""Repairs for captures that arrive damaged by the way they were taken.

* 86Box's parallel "named pipe" device can deliver every byte twice (1B 1B 40 40 instead of 1B 40), usually after a stray
  leading byte. Real printer data essentially never consists of nothing but identical pairs, so a stream that does is
  collapsed back to single bytes.
* A print can reach us in bursts with long pauses (the GEOS ImageWriter driver on VICE sends a few hundred bytes at a time),
  so "no data for a few seconds" must not end a job in the middle of a bitmap row: see `unfinished_graphics`.
* Programs and drivers probe a printer port before using it (writing 55 / AA / 00 / 04 ...). Over a pipe or a socket these
  show up as tiny "jobs" that are not print data.
"""
from __future__ import annotations

import re

PROBE_BYTES = frozenset({0x00, 0x04, 0x55, 0xAA, 0xFF})
MIN_DOUBLED = 16  # shorter streams are too short to be sure


def undouble(data: bytes):
    """-> (data, note). If the whole stream (from byte 0 or byte 1) is made of identical byte pairs, collapse the pairs."""
    for skip in (0, 1):
        body = data[skip:]
        n = len(body) // 2
        if n < MIN_DOUBLED // 2:
            continue
        even, odd = body[0:2 * n:2], body[1:2 * n:2]
        same = sum(1 for a, b in zip(even, odd) if a == b)
        # a genuine stream would match about 1 pair in 256; allow a few glitches in a long capture (but not the 120 dpi MPS-1000 GEOS stream, where every graphics column is sent twice by design)
        if same == n or (n >= 256 and same >= 0.999 * n):
            out = bytes(even)
            tail = body[2 * n:]
            if tail and len(tail) == 1:
                out += tail
            note = "every byte was received twice - collapsed to single bytes"
            if skip:
                note += f" (ignored a stray leading byte 0x{data[0]:02X})"
            return out, note
    return data, ""


def is_probe(data: bytes) -> bool:
    """A job of only a few port-probe bytes (e.g. 55 AA 00) that is not print data."""
    return len(data) <= 4 and all(b in PROBE_BYTES for b in data)


_IW_HEADER = re.compile(rb"\x1b(?:[GS](\d{4})|g(\d{3}))")
_IW_PARTIAL = re.compile(rb"\x1b[GSg]\d{0,3}\Z")
_ESC_COLUMN_BYTES = {32: 3, 33: 3, 38: 3, 39: 3, 40: 3, 71: 6, 72: 6, 73: 6}  # ESC * m: bytes per column (24-pin modes)


def unfinished_graphics(data: bytes) -> bool:
    """True if `data` stops inside a bitmap row: a graphics command (ImageWriter ESC G/S/g, Epson ESC * / K / L / Y / Z)
    announced more bytes than have arrived. The sender is probably only pausing, so the job is not over yet."""
    pos, n = 0, len(data)
    while True:
        i = data.find(b"\x1b", pos)
        if i < 0 or i + 1 >= n:
            return False
        c = data[i + 1]
        end = None
        if c in b"GSg":
            m = _IW_HEADER.match(data, i)
            if m:
                cnt = int(m.group(1) or m.group(2))
                end = m.end() + (cnt * 8 if c == 0x67 else cnt)
            elif _IW_PARTIAL.match(data, i):  # the header itself is cut off
                return True
        elif c == 0x2A and i + 4 < n:  # ESC * m n1 n2
            end = i + 5 + (data[i + 3] | data[i + 4] << 8) * _ESC_COLUMN_BYTES.get(data[i + 2], 1)
        elif c in b"KLYZ" and i + 3 < n:  # ESC K/L/Y/Z n1 n2 (but ImageWriter's ESC K <digit> is a ribbon colour)
            if not (c == 0x4B and data[i + 2] in b"01234" and data[i + 3] in b"\r\x1b"):
                end = i + 4 + (data[i + 2] | data[i + 3] << 8)
        if end is None:
            pos = i + 2
            continue
        if end > n:
            return True
        pos = end
