"""Core data structures shared by the emulators, renderer, exporters and GUI."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

U_PER_INCH = 1440.0  # internal unit "U" = 1/1440 inch


@dataclass(frozen=True)
class PrinterProfile:
    key: str
    label: str
    family: str  # escp | ibm | star | imagewriter | mps | okimate
    pins: int
    dot_diam_in: float  # nominal printed dot diameter
    unit_v: int  # vertical unit of ESC 3 / ESC J (units per inch)
    nlq_default: bool = False
    lq24: bool = False
    default_codepage: str = "cp437"
    description: str = ""


PROFILES: Dict[str, PrinterProfile] = {
    p.key: p
    for p in [
        PrinterProfile("epson_fx", "Epson FX / MX / RX (9-pin ESC/P)", "escp", 9, 0.0125, 216,
                       description="Epson FX-80/85/100, MX-80, RX-80, LX-series and many clones"),
        PrinterProfile("epson_lq", "Epson LQ (24-pin ESC/P)", "escp", 24, 0.0085, 180, nlq_default=True, lq24=True,
                       description="Epson LQ-500/800/1000/1500 and ESC/P2 printers"),
        PrinterProfile("ibm", "IBM Proprinter / Graphics Printer", "ibm", 9, 0.0125, 216,
                       description="IBM Graphics Printer, Proprinter, PPDS emulation"),
        PrinterProfile("star", "Star NX / NL / Gemini / SG (Epson mode)", "star", 9, 0.0120, 216,
                       description="Star Micronics 9-pin printers in their Epson-compatible mode"),
        PrinterProfile("imagewriter", "Apple ImageWriter I / II", "imagewriter", 9, 0.0135, 144,
                       default_codepage="mac_roman",
                       description="Apple ImageWriter (Apple II, Macintosh, Basilisk II, SheepShaver)"),
        PrinterProfile("okimate", "Okidata Okimate 10 / 20 (colour thermal transfer)", "okimate", 8, 0.0160, 216,
                       description="Okimate 10/20 and Commodore MCS-820: three-pass colour ribbon (GEOS, Print Shop and friends)"),
        PrinterProfile("mps", "Commodore MPS-801 / MPS-803 / 1525 (PETSCII)", "mps", 8, 0.0135, 72,
                       default_codepage="ascii",
                       description="Commodore 8-bit printers (VICE, C64/C128/VIC-20 software)"),
    ]
}
PROFILE_ORDER = list(PROFILES.keys())


@dataclass
class EmuOptions:
    printer: str = "auto"  # 'auto' or a PROFILES key
    codepage: str = "auto"  # auto -> per printer
    page_length_in: float = 11.0
    page_width_in: float = 8.5
    cr_adds_lf: Optional[bool] = None  # None = auto
    lf_adds_cr: Optional[bool] = None  # None = auto
    force_nlq: Optional[bool] = None  # None = follow data
    cbm_charset: str = "auto"  # auto | upper | lower | ascii
    tof_offset_in: float = 0.17  # distance from top edge to first baseline
    honor_page_length: bool = True
    skip_blank_ff: bool = True
    auto_wrap: bool = True
    wide_carriage: Optional[bool] = None  # None = auto
    esc_a_immediate: bool = True  # ESC A n applies at once (True) or only after ESC 2 like a strict Epson (False)
    mps_gap: str = "gap"  # Commodore bit-image lines feed 1/9 in, leaving a one-dot gap: gap = authentic | fill = keep the spacing but fill the gap in | close = feed 7/72 in (seamless, but the picture shrinks)
    split_mixed: bool = True  # auto mode: split a capture that switches printer types part-way through
    fix_capture: bool = True  # repair known capture damage first (e.g. every byte received twice, see capture_fix.py)
    blank_lines: str = "keep"  # keep | collapse (max one blank line in a row) | remove (single-space everything)


@dataclass
class DotPage:
    """All dots printed on one sheet. Coordinates are in U (1/1440 in) from the printable origin."""

    index: int
    width_in: float
    height_in: float
    x: np.ndarray  # float32
    y: np.ndarray  # float32 (from top edge, includes top-of-form offset)
    pin: np.ndarray  # int16 pin number
    pass_no: np.ndarray  # int32 head pass (line) number
    dirn: np.ndarray  # int8 0=left->right, 1=right->left
    profile_key: str = ""  # which printer produced this sheet (mixed streams); '' = Document.profile
    unknown: int = 0  # commands this sheet's data used that the emulator did not understand
    color: Optional[np.ndarray] = None  # uint8 ribbon colour per dot (ESC r n: 0 black 1 magenta 2 cyan 3 violet 4 yellow 5 red 6 green); None = all black

    @property
    def n_dots(self) -> int:
        return int(self.x.shape[0])

    def extent_in(self):
        if self.n_dots == 0:
            return 0.0, 0.0, 0.0, 0.0
        return (
            float(self.x.min()) / U_PER_INCH,
            float(self.y.min()) / U_PER_INCH,
            float(self.x.max()) / U_PER_INCH,
            float(self.y.max()) / U_PER_INCH,
        )


@dataclass
class Document:
    pages: List[DotPage]
    profile: PrinterProfile
    detected: str = ""
    carriage_in: float = 8.0  # printable width of the carriage
    notes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)  # things in the data that look wrong (lost bytes ...)
    fixes: List[str] = field(default_factory=list)  # repairs applied to the raw capture before emulation
    n_bytes: int = 0
    n_unknown: int = 0
    page_info: List = field(default_factory=list)  # analyze.PageInfo per sheet, filled in by the GUI worker

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    def colors_used(self) -> List[str]:
        """Names of the ribbon colours the data actually prints in (empty for plain black printing)."""
        names = {0: "black", 1: "magenta", 2: "cyan", 3: "violet", 4: "yellow", 5: "red", 6: "green"}
        used = set()
        for p in self.pages:
            if p.color is not None:
                used |= {int(c) for c in np.unique(p.color)}
        return [names[c] for c in sorted(used)] if used - {0} else []
