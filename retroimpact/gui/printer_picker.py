"""Always-visible printer chooser (bottom of the left column): one button per printer type plus a quick "next" button.

The choice belongs to the tab that is showing: the window calls set_printer() whenever another tab comes to the front.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QGroupBox, QLabel, QPushButton, QVBoxLayout

from ..model import PROFILE_ORDER, PROFILES
from ..preview import SHORT_NAMES

SHORT = SHORT_NAMES  # short button captions (the full names are in the tooltips)
ORDER = ["auto"] + list(PROFILE_ORDER)


class PrinterPicker(QGroupBox):
    chosen = Signal(str)  # 'auto' or a PROFILES key

    def __init__(self):
        super().__init__("Printer for this tab")
        self.setToolTip("If a printout looks wrong, click the printer it was really made for.")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(4)
        grid = QGridLayout()
        grid.setSpacing(4)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = {}
        for n, key in enumerate(ORDER):
            b = QPushButton(SHORT[key])
            b.setCheckable(True)
            b.setMinimumHeight(28)
            tip = "Work out the printer from the data" if key == "auto" else f"{PROFILES[key].label}\n{PROFILES[key].description}"
            b.setToolTip(tip)
            b.clicked.connect(lambda _c=False, k=key: self.chosen.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            grid.addWidget(b, n // 2, n % 2)
        outer.addLayout(grid)
        self.nxt = QPushButton("Try next printer  ⟳  (F8)")
        self.nxt.setMinimumHeight(30)
        self.nxt.setToolTip("Switch to the next printer type: click again until the page looks right")
        self.nxt.clicked.connect(self.cycle)
        outer.addWidget(self.nxt)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft)
        outer.addWidget(self.status)
        self.current = "auto"
        self.set_printer("auto")

    def set_printer(self, key: str):
        self.current = key if key in self.buttons else "auto"
        self.buttons[self.current].setChecked(True)

    def set_status(self, text: str):
        self.status.setText(text)

    def cycle(self):
        i = ORDER.index(self.current) if self.current in ORDER else 0
        key = ORDER[(i + 1) % len(ORDER)]
        self.set_printer(key)
        self.chosen.emit(key)
