"""Entry point: raw printer bytes -> Document (list of DotPage)."""
from __future__ import annotations

import re
from typing import Dict

from .commodore import MpsEngine
from .engine import Engine
from .escp import EscpEngine
from .imagewriter import ImageWriterEngine
from .okimate import OkimateEngine, looks_okimate
from .capture_fix import undouble
from .model import PROFILES, Document, EmuOptions


def looks_like_petscii_text(data: bytes) -> bool:
    """Plain Commodore text in the lower/upper character set, as GEOS and many C64 programs send it.

    There the letters you type in lower case are bytes $41-$5A and capitals are $61-$7A (or $C1-$DA), so the stream looks like
    ASCII with the case swapped: almost every letter is upper case, with a few stray lower-case ones where the capitals were.
    Real ASCII text has the opposite ratio and never starts out in all capitals with a sprinkling of lower case."""
    if b"\x1b" in data or len(data) < 40:
        return False
    if any(b >= 0x80 for b in data) or any(b < 0x20 and b not in (0x0D, 0x0A, 0x0C, 0x09) for b in data):
        return False
    up = sum(1 for b in data if 0x41 <= b <= 0x5A)
    lo = sum(1 for b in data if 0x61 <= b <= 0x7A)
    letters = up + lo
    return letters >= 30 and lo >= 2 and up >= 0.85 * letters


_BYTES_PER_COLUMN = {32: 3, 33: 3, 38: 3, 39: 3, 40: 3, 71: 6, 72: 6, 73: 6}  # ESC * m (24-pin modes); others use 1


_IW_GRAPHICS = re.compile(rb"\x1b([GS](?=\d{4})|g(?=\d{3}))(\d{4}|\d{3})")
_IW_COLOUR = re.compile(rb"\x1bK[0-4](?=[\r\x1b])")


def mask_graphics(data: bytes) -> bytes:
    """Same length as `data`, with the bitmap payload of ESC/P graphics commands (ESC * m n1 n2 ..., ESC K/L/Y/Z n1 n2 ...)
    zeroed. Bitmap bytes are arbitrary, so they can imitate the control codes of other printers (0x08, 0x91, 0xC1 ...);
    printer-type scoring must only look at the commands and text around them."""
    if b"\x1b" not in data:
        return data
    out = bytearray(data)
    pos, n = 0, len(data)
    while True:
        i = data.find(b"\x1b", pos)
        if i < 0 or i + 1 >= n:
            break
        c = data[i + 1]
        m = _IW_GRAPHICS.match(data, i)
        if m:  # Apple ImageWriter: ESC G nnnn / ESC S nnnn (nnnn bytes), ESC g nnn (nnn*8 bytes), ASCII-digit counts
            cnt = int(m.group(2))
            start = m.end()
            end = start + (cnt * 8 if m.group(1) == b"g" else cnt)
        elif c == 0x4B and _IW_COLOUR.match(data, i):  # ImageWriter ribbon colour, not Epson ESC K n1 n2
            pos = i + 3
            continue
        elif c == 0x67 and i + 4 < n and data[i + 2] <= 6 and not data[i + 2:i + 5].isdigit():  # Star SG-10: ESC g m n1 n2
            start = i + 5
            end = start + (data[i + 3] | data[i + 4] << 8) * _BYTES_PER_COLUMN.get(data[i + 2], 1)
        elif c == 0x2A and i + 4 < n:  # ESC * m n1 n2
            start = i + 5
            end = start + (data[i + 3] | data[i + 4] << 8) * _BYTES_PER_COLUMN.get(data[i + 2], 1)
        elif c in (0x4B, 0x4C, 0x59, 0x5A, 0x7A) and i + 3 < n:  # ESC K/L/Y/Z n1 n2 (and Star Gemini's ESC z)
            start = i + 4
            end = start + (data[i + 2] | data[i + 3] << 8)
        else:
            pos = i + 2
            continue
        end = min(end, n)
        out[start:end] = bytes(end - start)
        pos = max(end, i + 2)
    return bytes(out)


def score_printers(data: bytes) -> Dict[str, float]:
    s = {k: 0.0 for k in PROFILES}
    cnt = lambda pat: len(re.findall(pat, data, re.S))  # noqa: E731

    # --- Apple ImageWriter: ASCII-digit parameters are the giveaway
    iw = 3 * cnt(rb"\x1bT\d\d") + 4 * cnt(rb"\x1bG\d{4}") + 3 * cnt(rb"\x1bg\d{3}") + 3 * cnt(rb"\x1bH\d{4}")
    iw += 3 * cnt(rb"\x1bV\d{4}") + 2 * cnt(rb"\x1bL\d{3}") + 2 * cnt(rb"\x1ba[012]") + cnt(rb"\x1b[ABnNeqQ]")
    iw += cnt(rb"\x1b[<>]") + cnt(rb"\x1b[!\"]") + 3 * cnt(rb"\x1bS\d{4}")
    # ribbon colour: ESC K <ASCII digit 0-4> then CR / next command (Epson's ESC K takes raw n1 n2 bytes)
    iw += 3 * cnt(rb"\x1bK[0-4](?=[\r\x1b])")
    s["imagewriter"] = iw

    # --- Commodore MPS
    flat = mask_graphics(data)  # bitmap bytes of Epson-style graphics must not count as Commodore control codes
    mcnt = lambda pat: len(re.findall(pat, flat, re.S))  # noqa: E731
    mps = 5 * mcnt(rb"\x08[\x80-\xff]{3}") + 4 * mcnt(rb"\x1b\x10[\x00\x01]") + 3 * mcnt(rb"\x10\d\d")
    mps += mcnt(rb"[\x91\x92]") + mcnt(rb"\x0e.{0,80}\x0f") + 0.2 * mcnt(rb"[\xc1-\xda]")
    if looks_like_petscii_text(data):
        mps += 12
    s["mps"] = mps

    # --- Epson / IBM / Star (on the masked stream: bitmap bytes must not count as commands)
    ecnt = mcnt
    ep = 4 * ecnt(rb"\x1b@") + 3 * ecnt(rb"\x1b(?:K(?![0-4][\r\x1b])|[LYZ])[\x00-\xff]{2}") + 3 * ecnt(rb"\x1b\*[\x00-\x07]")
    ep += 2 * ecnt(rb"\x1b3[\x00-\xff]") + 2 * ecnt(rb"\x1bA[\x00-\x20]") + ecnt(rb"\x1b(?:[GS](?!\d{4})|g(?!\d{3})|T(?!\d\d)|[EFHMP45])")
    ep += 2 * ecnt(rb"\x1bx[\x00\x01]") + ecnt(rb"\x1bt[\x00\x01]") + ecnt(rb"\x1bR[\x00-\x0c]") + ecnt(rb"\x1bC")
    ep += ecnt(rb"\x1b-[\x00\x01]") + 0.2 * ecnt(rb"\x0f") + 0.2 * ecnt(rb"\x12")
    s["epson_fx"] = ep
    lq = 4 * ecnt(rb"\x1b\*[\x20\x21\x26\x27\x28\x47-\x49]") + 3 * ecnt(rb"\x1b\([A-Za-z\^-]")
    lq += 2 * ecnt(rb"\x1bX[\x00-\xff]{2}")
    s["epson_lq"] = ep * 0.9 + lq if lq else 0.0
    ibm = 3 * ecnt(rb"\x1b\[[\x00-\xff]") + 2 * ecnt(rb"\x1b6") + 2 * ecnt(rb"\x1b:") + ecnt(rb"\x1b[5]")
    s["ibm"] = ibm + (ep * 0.5 if ibm else 0.0)
    # Star Gemini / Delta / SG: ESC z n1 n2 = quadruple-density graphics (what Epson spells ESC Z)
    s["star"] = 4.0 * len(re.findall(rb"\x1bz[\x00-\xff]{2}", flat)) if re.search(rb"\x1bz[\x40-\xff][\x00-\x0f]", flat) else 0.0
    sg = len(re.findall(rb"\x1bg[\x00-\x06][\x00-\xff]{2}", flat))  # Star SG-10/15: ESC g m n1 n2
    if sg >= 2 and not cnt(rb"\x1bg\d{3}"):
        s["star"] += 4.0 * sg
    return s


def detect_printer(data: bytes) -> str:
    if looks_okimate(data):  # colour-ribbon bit images chained only by carriage returns
        return "okimate"
    s = score_printers(data)
    best = max(s, key=lambda k: s[k])
    if s[best] <= 0:
        return "epson_fx"
    # an ImageWriter stream scores on Epson patterns too; prefer it when its giveaways are present
    if s["imagewriter"] >= 6 and s["imagewriter"] >= s["epson_fx"] * 0.6 and s["imagewriter"] >= s["mps"]:
        return "imagewriter"
    # (random graphics bytes can imitate a few Commodore patterns; only believe MPS if it beats every ESC/P family score)
    if s["mps"] >= 8 and s["mps"] > max(s["epson_fx"], s["epson_lq"], s["ibm"]):
        return "mps"
    if s["star"] >= 4:
        return "star"
    if s["epson_lq"] and s["epson_lq"] >= s["epson_fx"] and s["epson_lq"] >= s["ibm"]:
        return "epson_lq"
    if s["ibm"] > s["epson_fx"]:
        return "ibm"
    return best if best != "star" else "epson_fx"


_GROUP_OF = {"epson_fx": "escp", "epson_lq": "escp", "ibm": "escp", "star": "escp", "imagewriter": "imagewriter", "mps": "mps", "okimate": "okimate"}
_START_SIG = {
    "mps": re.compile(rb"\x08(?:\x00\r|[\x80-\xff]{2})|\x1b\x10[\x00\x01]", re.S),
    "imagewriter": re.compile(rb"\x1b[TGHVLgS]\d{2,4}", re.S),
    "escp": re.compile(rb"\x1b@|\x1bA[\x00-\x20]|\x1b[KLYZz][\x00-\xff]{2}|\x1bg[\x00-\x06][\x00-\xff]{2}|\x1b\*[\x00-\x07]|\x1b3[\x00-\xff]", re.S),
}


def _window_family(window: bytes):
    s = score_printers(window)
    fam = {"escp": max(s["epson_fx"], s["epson_lq"], s["ibm"]), "imagewriter": s["imagewriter"], "mps": s["mps"]}
    best = max(fam, key=lambda k: fam[k])
    others = sorted(fam.values())[-2]
    return best if fam[best] >= 5 and fam[best] >= 1.5 * others else None


def _printer_for(group: str, chunk: bytes) -> str:
    if group != "escp":
        return group
    key = detect_printer(chunk)
    return key if _GROUP_OF.get(key) == "escp" else "epson_fx"


def split_segments(data: bytes):
    """Split a capture in which the printer type changed part-way (VICE appends to one file).

    Returns [(start, end, printer_key), ...] covering the whole stream.
    """
    n = len(data)
    if looks_okimate(data):
        return [(0, n, "okimate")]
    win = max(256, n // 200)
    if n < 2 * win:
        return [(0, n, detect_printer(data))]
    starts = list(range(0, n, win))
    masked = mask_graphics(data)  # so that bitmap bytes do not look like another printer's codes
    labels = [_window_family(masked[s : s + win]) for s in starts]
    # fill gaps: unknown windows inherit the previous known family (or the next one at the start)
    known = [l for l in labels if l]
    if not known:
        return [(0, n, detect_printer(data))]
    cur = known[0]
    filled = []
    for l in labels:
        cur = l or cur
        filled.append(cur)
    # hysteresis: a change needs two consecutive windows of the new family
    stable = [filled[0]]
    for i in range(1, len(filled)):
        nxt = filled[i + 1] if i + 1 < len(filled) else filled[i]
        stable.append(filled[i] if (filled[i] == stable[-1] or filled[i] == nxt) else stable[-1])
    cuts = [(0, stable[0])]
    for i in range(1, len(stable)):
        if stable[i] != stable[i - 1]:
            p = starts[i]
            j = i - 1
            while j > 0 and labels[j] != stable[i - 1]:  # last window that clearly looked like the old printer
                j -= 1
            lo, hi = starts[max(j, 0)], min(n, p + win)
            m = _START_SIG[stable[i]].search(data, lo, hi)
            cuts.append((m.start() if m else p, stable[i]))
    segs = []
    for k, (a, grp) in enumerate(cuts):
        b = cuts[k + 1][0] if k + 1 < len(cuts) else n
        if segs and (b - a < 64 or segs[-1][2] == grp):
            segs[-1] = (segs[-1][0], b, segs[-1][2])
        else:
            segs.append((a, b, grp))
    return [(a, b, _printer_for(g, data[a:b])) for a, b, g in segs]


def make_engine(key: str, opts: EmuOptions) -> Engine:
    prof = PROFILES[key]
    fam = prof.family
    if fam == "imagewriter":
        return ImageWriterEngine(prof, opts)
    if fam == "mps":
        return MpsEngine(prof, opts)
    if fam == "okimate":
        return OkimateEngine(prof, opts)
    return EscpEngine(prof, opts)


def _emulate_one(data: bytes, key: str, opts: EmuOptions) -> Document:
    eng = make_engine(key, opts)
    n_cr, n_lf = data.count(b"\r"), data.count(b"\n")
    eng.cr_adds_lf = (n_cr > 0 and n_lf == 0) if opts.cr_adds_lf is None else opts.cr_adds_lf
    eng.lf_adds_cr = (n_lf > 0 and n_cr == 0) if opts.lf_adds_cr is None else opts.lf_adds_cr
    eng.run(data)
    doc = eng.finish(len(data))
    if getattr(eng, "short_rows", 0):
        doc.warnings.append(f"{eng.short_rows} graphics row(s) had fewer bytes than their header announced - the capture "
                            "probably lost data (those rows end early)")
    if opts.wide_carriage:
        doc.carriage_in = 13.6
    return doc


def emulate(data: bytes, opts: EmuOptions | None = None) -> Document:
    opts = opts or EmuOptions()
    fixes: list = []
    original = len(data)
    if opts.fix_capture:
        data, note = undouble(data)
        if note:
            fixes.append(note)
    doc = _emulate_data(data, opts)
    doc.fixes = fixes
    doc.n_bytes = original
    return doc


def _emulate_data(data: bytes, opts: EmuOptions) -> Document:
    key = opts.printer
    auto = key == "auto" or key not in PROFILES
    if auto and opts.split_mixed:
        segs = split_segments(data)
        if len(segs) > 1:
            docs = []
            for a, b, k in segs:
                d = _emulate_one(data[a:b], k, opts)
                d.detected = k
                docs.append((a, b, k, d))
            pages = []
            notes = []
            for a, b, k, d in docs:
                for p in d.pages:
                    p.index = len(pages)
                    p.profile_key = p.profile_key or k
                    pages.append(p)
                notes.append(f"{PROFILES[k].label}: bytes {a:,}-{b:,}, {d.n_pages} page(s)")
            return Document(
                pages=pages, profile=docs[0][3].profile, detected="mixed",
                carriage_in=max(d.carriage_in for *_x, d in docs), notes=notes, n_bytes=len(data),
                n_unknown=sum(d.n_unknown for *_x, d in docs),
                warnings=[w for *_x, d in docs for w in d.warnings],
            )
    if auto:
        key = detect_printer(data)
    doc = _emulate_one(data, key, opts)
    doc.detected = key if auto else ""
    doc.detected = doc.detected or key
    return doc
