"""Render settings, look presets, ribbon colours and paper presets."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Dict, Tuple

# name -> (label, RGB of the ink as it lands on white paper)
RIBBONS: Dict[str, Tuple[str, Tuple[int, int, int]]] = {
    "black": ("Black", (22, 22, 28)),
    "blue_black": ("Blue-black", (18, 24, 62)),
    "blue": ("Blue", (22, 40, 140)),
    "purple": ("Purple", (74, 28, 110)),
    "red": ("Red", (170, 26, 30)),
    "green": ("Green", (20, 96, 52)),
    "brown": ("Brown", (88, 52, 30)),
}

# Colour-ribbon inks (ESC r n on Epson / Star colour printers): how each ink tints white paper at full strike.
# Overprinting two inks multiplies them (yellow + magenta = red, cyan + yellow = green, magenta + cyan = violet).
COLOR_INKS: Dict[int, Tuple[str, Tuple[int, int, int]]] = {
    1: ("magenta", (206, 34, 124)),
    2: ("cyan", (22, 138, 210)),
    4: ("yellow", (250, 222, 34)),
}
# Ink sets for the colour ribbon (magenta, cyan, yellow as they land on white paper). The first is the long-standing default;
# the others only matter when a stream prints in colour. Overprinted inks multiply, so Commodore / Okimate reds, greens,
# blues and purples all come out of these three.
INK_SETS: Dict[str, Tuple[str, Tuple[Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int]]]] = {
    "ribbon": ("Standard colour ribbon", ((206, 34, 124), (22, 138, 210), (250, 222, 34))),
    "vivid": ("Vivid (fresh, saturated)", ((226, 16, 128), (0, 156, 228), (255, 232, 0))),
    "process": ("Process inks (print-shop CMY)", ((236, 0, 140), (0, 174, 239), (255, 242, 0))),
    "faded": ("Faded (old ribbon)", ((186, 72, 122), (74, 134, 180), (226, 206, 96))),
    "thermal": ("Thermal transfer (Okimate waxy)", ((214, 46, 110), (36, 120, 196), (246, 210, 48))),
}
INK_ORDER = ("magenta", "cyan", "yellow")  # the order of the three colours in an ink set / the custom_inks field
_INK_CODE = {1: 0, 2: 1, 4: 2}  # ribbon ink code (as in COLOR_INKS) -> index in an ink set

# ESC r n -> the inks it prints with (3/5/6 are two inks struck on top of each other)
COLOR_CODES = {0: (0,), 1: (1,), 2: (2,), 3: (1, 2), 4: (4,), 5: (1, 4), 6: (2, 4)}

# key -> (label, width_in or None=auto, height_in or None=auto, kind, tractor)
PAPERS: Dict[str, Tuple[str, float | None, float | None, str, bool]] = {
    "auto": ("Auto (continuous 8.5 in)", None, None, "white", False),
    "letter": ("US Letter, white", 8.5, 11.0, "white", False),
    "a4": ("A4, white", 8.27, 11.69, "white", False),
    "tractor": ("Tractor feed 9½ × 11, white", 9.5, 11.0, "white", True),
    "tractor_cream": ("Tractor feed 9½ × 11, cream", 9.5, 11.0, "cream", True),
    "tractor_aged": ("Tractor feed 9½ × 11, aged", 9.5, 11.0, "aged", True),
    "greenbar": ("Greenbar 9½ × 11", 9.5, 11.0, "greenbar", True),
    "greenbar_wide": ("Greenbar 14⅞ × 11", 14.875, 11.0, "greenbar", True),
    "bluebar": ("Blue-bar 9½ × 11", 9.5, 11.0, "bluebar", True),
}


# the footprint of one pin strike: label, description
PIN_SHAPES: Dict[str, Tuple[str, str]] = {
    "round": ("Round (new pin)", "A clean round dot, like a fresh head."),
    "oval": ("Oval (head moving)", "Slightly wider than tall: the head keeps moving while the pin strikes. "
                                   "Typical of 9-pin printers."),
    "rough": ("Rough (worn pin)", "Ragged, uneven edges where the pin tip is worn and the ribbon fabric breaks the ink up."),
    "square": ("Square (blunt pin)", "Rounded-square dots from a flat, blunted pin tip."),
    "ring": ("Ring (heavy strike)", "Darker rim with a pale centre: a hard strike squeezes the ink to the edge."),
}


def _parse_inks(text: str):
    """'#rrggbb,#rrggbb,#rrggbb' -> ((r, g, b), ...) or None if it is not three valid colours."""
    parts = [p.strip().lstrip("#") for p in str(text).split(",")]
    if len(parts) != 3 or any(len(p) != 6 for p in parts):
        return None
    try:
        return tuple(tuple(int(p[i:i + 2], 16) for i in (0, 2, 4)) for p in parts)
    except ValueError:
        return None


@dataclass
class RenderSettings:
    dpi: int = 144
    dot_size: float = 1.0  # pin diameter multiplier (0.5 .. 1.6)
    density: float = 0.88  # 1 = every dot strikes fully, lower = weak/missing strikes
    saturation: float = 0.90  # ribbon ink load (darkness / colour richness)
    ribbon: str = "black"
    paper: str = "tractor"
    ribbon_wear: float = 0.35  # streaks, line-to-line ink variation, weak pins
    pin_shape: str = "round"  # key of PIN_SHAPES: the footprint one pin leaves on the paper
    misalign: float = 0.35  # bidirectional head misalignment
    paper_texture: float = 0.5
    impact: float = 0.5  # emboss / dent shading around strikes
    jitter: float = 0.4  # random dot position error
    show_holes: bool = True
    ink_set: str = "ribbon"  # key of INK_SETS, or "custom" to use custom_inks
    custom_inks: str = "#ce227c,#168ad2,#fade22"  # magenta,cyan,yellow as #rrggbb when ink_set == "custom"
    color_ribbon: bool = True  # honour ESC r colour changes (off = everything prints in the black ribbon)
    rotation: int = 0  # view/export rotation in degrees clockwise: 0, 90, 180, 270 (banners print sideways)
    seed: int = 1984

    def clamp(self) -> "RenderSettings":
        self.dpi = int(min(2400, max(36, self.dpi)))
        self.dot_size = min(2.0, max(0.3, self.dot_size))
        for n in ("density", "ribbon_wear", "misalign", "paper_texture", "impact", "jitter"):
            setattr(self, n, min(1.0, max(0.0, getattr(self, n))))
        self.saturation = min(1.5, max(0.05, self.saturation))
        self.rotation = (int(round(float(self.rotation) / 90.0)) * 90) % 360
        if self.pin_shape not in PIN_SHAPES:
            self.pin_shape = "round"
        if self.ink_set != "custom" and self.ink_set not in INK_SETS:
            self.ink_set = "ribbon"
        if self.ink_set == "custom" and _parse_inks(self.custom_inks) is None:
            self.custom_inks = "#ce227c,#168ad2,#fade22"
        if self.ribbon not in RIBBONS:
            self.ribbon = "black"
        if self.paper not in PAPERS:
            self.paper = "tractor"
        return self

    def ink_rgb(self, ink: int) -> Tuple[int, int, int]:
        """RGB of ribbon ink 1 (magenta), 2 (cyan) or 4 (yellow) as it lands on white paper."""
        idx = _INK_CODE[ink]
        if self.ink_set == "custom":
            tones = _parse_inks(self.custom_inks)
            if tones:
                return tones[idx]
        return INK_SETS.get(self.ink_set, INK_SETS["ribbon"])[1][idx]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RenderSettings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names}).clamp()

    def replace(self, **kw) -> "RenderSettings":
        d = self.to_dict()
        d.update(kw)
        return RenderSettings.from_dict(d)


LOOK_PRESETS: Dict[str, dict] = {
    "Fresh ribbon": dict(dot_size=1.0, density=0.97, saturation=1.1, ribbon_wear=0.1, misalign=0.15,
                         jitter=0.2, paper="tractor", impact=0.5, paper_texture=0.35),
    "Office workhorse": dict(pin_shape="oval", dot_size=1.0, density=0.88, saturation=0.8, ribbon_wear=0.35, misalign=0.35,
                             jitter=0.4, paper="tractor", impact=0.5, paper_texture=0.5),
    "Worn ribbon": dict(pin_shape="rough", dot_size=0.95, density=0.7, saturation=0.5, ribbon_wear=0.65, misalign=0.5,
                        jitter=0.55, paper="tractor_cream", impact=0.6, paper_texture=0.6),
    "Dying ribbon": dict(pin_shape="rough", dot_size=0.9, density=0.5, saturation=0.28, ribbon_wear=0.9, misalign=0.7,
                         jitter=0.7, paper="tractor_aged", impact=0.7, paper_texture=0.75),
    "Greenbar mainframe": dict(pin_shape="oval", dot_size=1.05, density=0.85, saturation=0.9, ribbon_wear=0.45, misalign=0.4,
                               jitter=0.4, paper="greenbar", impact=0.5, paper_texture=0.5),
    "Clean (for PDF)": dict(dot_size=1.0, density=1.0, saturation=1.2, ribbon_wear=0.0, misalign=0.0,
                            jitter=0.0, paper="letter", impact=0.2, paper_texture=0.0),
}
