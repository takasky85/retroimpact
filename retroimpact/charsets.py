"""Character sets: byte -> Unicode character used to pick a glyph."""
from __future__ import annotations

from typing import Optional

CODEPAGES = {
    "cp437": "IBM PC (CP437)",
    "cp850": "IBM PC Multilingual (CP850)",
    "latin1": "ISO Latin-1",
    "mac_roman": "Mac Roman (ImageWriter)",
    "ascii": "ASCII only",
}

# CP437 glyphs for the low control range (printed in IBM "character set 2" mode) and DEL.
CP437_LOW = (
    "\u0000☺☻♥♦♣♠•◘○◙♂♀♪♫☼"
    "►◄↕‼¶§▬↨↑↓→←∟↔▲▼"
)

# Epson national character sets, ESC R n.  Positions: # $ @ [ \ ] ^ ` { | } ~
_NAT_POS = "#$@[\\]^`{|}~"
_NAT_POS = [0x23, 0x24, 0x40, 0x5B, 0x5C, 0x5D, 0x5E, 0x60, 0x7B, 0x7C, 0x7D, 0x7E]
NATIONAL = {
    0: "#$@[\\]^`{|}~",
    1: "#$à°ç§^`éùè¨",
    2: "#$§ÄÖÜ^`äöüß",
    3: "£$@[\\]^`{|}~",
    4: "#$@ÆØÅ^`æøå~",
    5: "#¤ÉÄÖÅÜéäöåü",
    6: "#$@°\\é^ùàòèì",
    7: "₧$@¡Ñ¿^`¨ñ}~",
    8: "#$@[¥]^`{|}~",
    9: "#¤ÉÆØÅÜéæøåü",
    10: "#$ÉÆØÅÜéæøåü",
    11: "#$á¡Ñ¿é`íñóú",
    12: "#$á¡Ñ¿éüíñóú",
}
for _k, _v in NATIONAL.items():
    assert len(_v) == 12, (_k, len(_v))

NATIONAL_NAMES = [
    "USA", "France", "Germany", "UK", "Denmark I", "Sweden", "Italy",
    "Spain I", "Japan", "Norway", "Denmark II", "Spain II", "Latin America",
]


def apply_national(ch: str, national: int) -> str:
    if national <= 0 or national not in NATIONAL:
        return ch
    o = ord(ch)
    if o in _NAT_POS:
        return NATIONAL[national][_NAT_POS.index(o)]
    return ch


def decode(b: int, codepage: str, national: int = 0, ibm_graphics: bool = False) -> Optional[str]:
    """Return the character for printable byte ``b`` (None if not printable)."""
    if b < 0x20:
        if ibm_graphics and b not in (0x07, 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x11, 0x12, 0x13, 0x14, 0x18, 0x1B):
            return CP437_LOW[b]
        return None
    if b < 0x7F:
        return apply_national(chr(b), national)
    if b == 0x7F:
        return None
    if codepage == "ascii":
        return "?"
    try:
        if codepage == "cp437":
            return bytes([b]).decode("cp437")
        if codepage == "cp850":
            return bytes([b]).decode("cp850")
        if codepage == "mac_roman":
            return bytes([b]).decode("mac_roman")
        return bytes([b]).decode("latin-1")
    except Exception:
        return "?"


# --------------------------------------------------------------------------------------
# PETSCII (Commodore).  Graphics symbols are mapped to the nearest Unicode glyph; shapes
# that have no Unicode twin are approximated.
# --------------------------------------------------------------------------------------

_PET_A0 = [
    " ", "▌", "▄", "▔", "▁", "▏", "▒", "▕",
    "◤", "◥", "▕", "├", "▗", "└", "┐", "▂",
    "┌", "┴", "┬", "┤", "▎", "▍", "▌", "▔",
    "▀", "▃", "✓", "▖", "▝", "┘", "▘", "▚",
]
_PET_C0 = [
    "─", "♠", "│", "─", "─", "─", "─", "│",
    "│", "╮", "╰", "╯", "▗", "╲", "╱", "▘",
    "▝", "●", "▔", "♥", "▎", "╭", "╳", "○",
    "♣", "▏", "♦", "┼", "▚", "│", "π", "◥",
]


def petscii_char(b: int, lower: bool) -> Optional[str]:
    """Map a PETSCII byte to a displayable Unicode character (None = no glyph/control)."""
    if b < 0x20 or 0x80 <= b < 0xA0:
        return None
    if 0x20 <= b <= 0x3F:
        return chr(b)
    if b == 0x40:
        return "@"
    if 0x41 <= b <= 0x5A:
        return chr(b + 32) if lower else chr(b)
    if b == 0x5B:
        return "["
    if b == 0x5C:
        return "£"
    if b == 0x5D:
        return "]"
    if b == 0x5E:
        return "↑"
    if b == 0x5F:
        return "←"
    if 0x60 <= b <= 0x7F:
        b = b - 0x60 + 0xC0
    if 0xE0 <= b <= 0xFE:
        b = b - 0xE0 + 0xA0
    if b == 0xFF:
        b = 0xDE
    if 0xA0 <= b <= 0xBF:
        return _PET_A0[b - 0xA0]
    if 0xC0 <= b <= 0xDF:
        if lower and 0xC1 <= b <= 0xDA:
            return chr(b - 0xC1 + 0x41)
        return _PET_C0[b - 0xC0]
    return None
