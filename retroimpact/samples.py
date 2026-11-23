"""Synthetic raw printer streams, so the app can be tried without any capture files."""
from __future__ import annotations

import math

ESC = b"\x1b"


def epson_text() -> bytes:
    o = bytearray(ESC + b"@")
    o += ESC + b"E" + b"  RETRO IMPACT  -  EPSON FX-80 SAMPLE" + ESC + b"F\r\n\r\n"
    o += b"The quick brown fox jumps over the lazy dog. 0123456789\r\n"
    o += ESC + b"4Italic text, " + ESC + b"5" + ESC + b"-1underlined, " + ESC + b"-0" + ESC + b"G"
    o += b"double-strike" + ESC + b"H, " + ESC + b"M" + b"elite pitch" + ESC + b"P\r\n"
    o += b"\x0e" + b"Double wide" + b"\x14" + b"  normal  " + b"\x0f" + b"condensed text is quite a bit smaller than normal text" + b"\x12\r\n\r\n"
    o += ESC + b"x1" + ESC + b"k\x00" + b"Near letter quality (Roman): Pack my box with five dozen liquor jugs.\r\n"
    o += ESC + b"k\x01" + b"Near letter quality (Sans): Sphinx of black quartz, judge my vow.\r\n" + ESC + b"x0\r\n"
    o += ESC + b"S\x00" + b"E=mc" + ESC + b"S\x00" + b"2" + ESC + b"T" + b"  H" + ESC + b"S\x01" + b"2" + ESC + b"T" + b"O\r\n\r\n"
    for i in range(1, 25):
        o += b"%3d  %-20s %8.2f\r\n" % (i, b"Line item number %d" % i, i * 13.37)
    # graphics: sine wave with ESC K, and a ramp with ESC L
    cols = bytearray()
    for x in range(240):
        y = int(3.5 + 3.4 * math.sin(x / 9.0))
        cols.append(1 << y)
    o += b"\r\n" + ESC + b"K" + bytes([len(cols) & 255, len(cols) >> 8]) + bytes(cols) + b"\r\n"
    cols = bytearray()
    for x in range(480):
        cols.append(((1 << (1 + (x // 60))) - 1) & 255)
    o += ESC + b"L" + bytes([len(cols) & 255, len(cols) >> 8]) + bytes(cols) + b"\r\n"
    return bytes(o) + b"\x0c" + ESC + b"E" + b"Page two" + ESC + b"F\r\n"


def imagewriter() -> bytes:
    o = bytearray(ESC + b"A" + ESC + b"N")  # ESC A = 6 lpi, ESC N = pica
    o += b"Apple ImageWriter II sample\r\n"
    o += b"The quick brown fox jumps over the lazy dog.\r\n"
    o += ESC + b"!" + b"Bold " + ESC + b'"' + ESC + b"X" + b"underlined" + ESC + b"Y" + b" " + ESC + b"E" + b"elite pitch " + ESC + b"q" + b"semi-condensed" + ESC + b"N\r\n"
    o += ESC + b"a2" + b"Near letter quality text, as set by the Macintosh driver.\r\n" + ESC + b"a0"
    # graphics bands, ESC G dddd (8-pin, 72 dpi) plus ESC T16
    o += b"\r\n" + ESC + b"T16"
    for band in range(3):
        cols = bytearray()
        for x in range(320):
            y = int(3.5 + 3.4 * math.sin((x + band * 40) / 13.0))
            cols.append(1 << y)
        o += ESC + b"G%04d" % len(cols) + bytes(cols) + b"\r\n"
    return bytes(o)


def commodore_mps() -> bytes:
    o = bytearray()
    o += b"COMMODORE MPS-803 SAMPLE\r\n"
    o += b"THE QUICK BROWN FOX JUMPS OVER THE LAZY DOG. 0123456789\r\n"
    o += b"\x0e" + b"DOUBLE WIDTH" + b"\x0f" + b"\r\n"
    cols = bytearray(0x80 | (1 << (int(3 + 2.9 * math.sin(x / 7.0)))) for x in range(240))
    o += b"\x08" + bytes(cols) + b"\r\n"
    return bytes(o)


SAMPLES = {
    "Epson ESC/P text + graphics": epson_text,
    "Apple ImageWriter II": imagewriter,
    "Commodore MPS (ASCII)": commodore_mps,
}
