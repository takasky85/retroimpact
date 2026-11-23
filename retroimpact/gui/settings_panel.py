"""Left-hand options panel: dot size, density, ribbon saturation and the rest of the 'look'."""
from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..settings import INK_ORDER, INK_SETS, LOOK_PRESETS, PAPERS, PIN_SHAPES, RIBBONS, RenderSettings
from .icons import icon


class SliderRow(QWidget):
    changed = Signal(float)

    def __init__(self, title: str, lo: float, hi: float, value: float, decimals: int = 2, tip: str = ""):
        super().__init__()
        self.lo, self.hi, self.decimals = lo, hi, decimals
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setVerticalSpacing(0)
        self.title = QLabel(title)
        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        g.addWidget(self.title, 0, 0)
        g.addWidget(self.value_label, 0, 1)
        g.addWidget(self.slider, 1, 0, 1, 2)
        if tip:
            self.setToolTip(tip)
        self.set_value(value)
        self.slider.valueChanged.connect(self._moved)

    def _to_f(self, v: int) -> float:
        return self.lo + (self.hi - self.lo) * v / 1000.0

    def value(self) -> float:
        return self._to_f(self.slider.value())

    def set_value(self, v: float):
        with QSignalBlocker(self.slider):
            self.slider.setValue(int(round((v - self.lo) / (self.hi - self.lo) * 1000)))
        self.value_label.setText(f"{self.value():.{self.decimals}f}")

    def _moved(self, _):
        self.value_label.setText(f"{self.value():.{self.decimals}f}")
        self.changed.emit(self.value())


def _swatch(rgb) -> QIcon:
    pm = QPixmap(16, 16)
    pm.fill(QColor(*rgb))
    return QIcon(pm)


class SettingsPanel(QWidget):
    changed = Signal()

    def __init__(self, settings: RenderSettings):
        super().__init__()
        self._custom_inks = settings.custom_inks
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)

        # --- look preset
        lay.addWidget(QLabel("Look"))
        self.preset = QComboBox()
        self.preset.addItem("Custom")
        self.preset.addItems(list(LOOK_PRESETS))
        lay.addWidget(self.preset)

        # --- print head
        g = QGroupBox("Print head")
        v = QVBoxLayout(g)
        self.dot_size = SliderRow("Dot size", 0.5, 1.6, settings.dot_size, 2,
                                  "Diameter of each pin strike relative to the printer's real pin size.")
        self.density = SliderRow("Density", 0.0, 1.0, settings.density, 2,
                                 "1 = every dot strikes cleanly. Lower = weak and missing strikes.")
        self.pin_shape = QComboBox()
        for key, (label, tip) in PIN_SHAPES.items():
            self.pin_shape.addItem(label, key)
            self.pin_shape.setItemData(self.pin_shape.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.pin_shape.setToolTip("The footprint one pin leaves on the paper.")
        v.addWidget(self.dot_size)
        v.addWidget(self.density)
        v.addWidget(QLabel("Pin shape"))
        v.addWidget(self.pin_shape)
        lay.addWidget(g)

        # --- ribbon
        g = QGroupBox("Ribbon")
        v = QVBoxLayout(g)
        self.saturation = SliderRow("Saturation", 0.05, 1.5, settings.saturation, 2,
                                    "How much ink the ribbon deposits: pale and grey to dense and black.")
        self.wear = SliderRow("Wear / streaks", 0.0, 1.0, settings.ribbon_wear, 2,
                              "Line-to-line ink variation and tired pins.")
        self.ribbon = QComboBox()
        for key, (label, rgb) in RIBBONS.items():
            self.ribbon.addItem(_swatch(rgb), label, key)
        v.addWidget(self.saturation)
        v.addWidget(self.wear)
        v.addWidget(QLabel("Colour"))
        v.addWidget(self.ribbon)
        self.color_ribbon = QCheckBox("Colour ribbon (ESC r colour changes)")
        self.color_ribbon.setToolTip("Epson / Star colour printers print in magenta, cyan and yellow passes. "
                                     "Untick to print everything with the black ribbon.")
        self.color_ribbon.setChecked(settings.color_ribbon)
        v.addWidget(self.color_ribbon)
        # the colour inks: Okimate / Commodore / Epson colour all mix their reds, greens and blues from these three
        v.addWidget(QLabel("Colour inks"))
        self.ink_set = QComboBox()
        for key, (label, tones) in INK_SETS.items():
            self.ink_set.addItem(label, key)
        self.ink_set.addItem("Custom (your own three inks)", "custom")
        self.ink_set.setToolTip("Colour printouts are made from magenta, cyan and yellow ink; overprinting them gives red, "
                                "green, blue and purple. Pick how the inks look, or click a swatch to set your own.")
        v.addWidget(self.ink_set)
        row = QHBoxLayout()
        self.ink_btns = []
        for name in INK_ORDER:
            b = QPushButton(name.capitalize())
            b.setToolTip(f"Click to choose the {name} ink")
            b.clicked.connect(lambda _c=False, n=name: self._pick_ink(n))
            row.addWidget(b)
            self.ink_btns.append(b)
        v.addLayout(row)
        lay.addWidget(g)

        # --- mechanics
        g = QGroupBox("Mechanics")
        v = QVBoxLayout(g)
        self.misalign = SliderRow("Head misalignment", 0.0, 1.0, settings.misalign, 2,
                                  "Left-to-right and right-to-left passes land slightly apart.")
        self.jitter = SliderRow("Dot jitter", 0.0, 1.0, settings.jitter, 2)
        self.impact = SliderRow("Paper impact", 0.0, 1.0, settings.impact, 2, "Embossed dents around strikes.")
        v.addWidget(self.misalign)
        v.addWidget(self.jitter)
        v.addWidget(self.impact)
        lay.addWidget(g)

        # --- paper
        g = QGroupBox("Paper")
        v = QVBoxLayout(g)
        self.paper = QComboBox()
        for key, (label, *_rest) in PAPERS.items():
            self.paper.addItem(label, key)
        self.texture = SliderRow("Texture / ageing", 0.0, 1.0, settings.paper_texture, 2)
        self.holes = QCheckBox("Show tractor holes")
        v.addWidget(self.paper)
        v.addWidget(self.texture)
        v.addWidget(self.holes)
        lay.addWidget(g)

        # --- seed
        row = QHBoxLayout()
        row.addWidget(QLabel("Random seed"))
        self.seed = QSpinBox()
        self.seed.setRange(0, 999999)
        row.addWidget(self.seed)
        reroll = QPushButton(icon("dice"), "Reroll")
        self.reroll_btn = reroll
        reroll.setIconSize(QSize(20, 20))
        row.addWidget(reroll)
        lay.addLayout(row)
        reroll.clicked.connect(self._reroll)

        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lay.addWidget(self.info)
        lay.addStretch(1)

        self.set_settings(settings)
        for w in (self.dot_size, self.density, self.saturation, self.wear, self.misalign, self.jitter,
                  self.impact, self.texture):
            w.changed.connect(self._edited)
        self.ribbon.currentIndexChanged.connect(self._edited)
        self.pin_shape.currentIndexChanged.connect(self._edited)
        self.paper.currentIndexChanged.connect(self._edited)
        self.holes.toggled.connect(self._edited)
        self.color_ribbon.toggled.connect(self._edited)
        self.ink_set.currentIndexChanged.connect(self._ink_set_chosen)
        self.seed.valueChanged.connect(self._edited)
        self.preset.activated.connect(self._preset_chosen)

    # -- helpers
    def _reroll(self):
        import random

        self.seed.setValue(random.randint(0, 999999))

    def _edited(self, *_):
        with QSignalBlocker(self.preset):
            self.preset.setCurrentIndex(0)
        self.changed.emit()

    def _ink_tones(self):
        """The three ink colours the swatches currently show: magenta, cyan, yellow."""
        return [self.settings().ink_rgb(c) for c in (1, 2, 4)]

    def _paint_inks(self, s: RenderSettings):
        for b, c in zip(self.ink_btns, (1, 2, 4)):
            r, g_, b_ = s.ink_rgb(c)
            lum = 0.299 * r + 0.587 * g_ + 0.114 * b_
            b.setStyleSheet(f"background-color: rgb({r},{g_},{b_}); color: {'#000' if lum > 140 else '#fff'};")

    def _ink_set_chosen(self, _i):
        self._paint_inks(self.settings())
        self._edited()

    def _pick_ink(self, name: str):
        tones = self._ink_tones()
        idx = INK_ORDER.index(name)
        r, g_, b_ = tones[idx]
        col = QColorDialog.getColor(QColor(r, g_, b_), self, f"{name.capitalize()} ink")
        if not col.isValid():
            return
        tones[idx] = (col.red(), col.green(), col.blue())
        text = ",".join("#%02x%02x%02x" % t for t in tones)
        self._custom_inks = text
        with QSignalBlocker(self.ink_set):
            self.ink_set.setCurrentIndex(self.ink_set.findData("custom"))
        self._paint_inks(self.settings())
        self._edited()

    def _preset_chosen(self, index: int):
        name = self.preset.itemText(index)
        if name in LOOK_PRESETS:
            self.set_settings(self.settings().replace(**LOOK_PRESETS[name]))
            with QSignalBlocker(self.preset):
                self.preset.setCurrentIndex(index)
            self.changed.emit()

    def settings(self) -> RenderSettings:
        return RenderSettings(
            dot_size=self.dot_size.value(), density=self.density.value(), saturation=self.saturation.value(),
            ribbon_wear=self.wear.value(), ribbon=self.ribbon.currentData(), misalign=self.misalign.value(),
            jitter=self.jitter.value(), impact=self.impact.value(), paper=self.paper.currentData(),
            paper_texture=self.texture.value(), show_holes=self.holes.isChecked(), seed=self.seed.value(),
            color_ribbon=self.color_ribbon.isChecked(), pin_shape=self.pin_shape.currentData(),
            ink_set=self.ink_set.currentData(), custom_inks=self._custom_inks,
        ).clamp()

    def set_settings(self, s: RenderSettings):
        blockers = [QSignalBlocker(w) for w in (self.ribbon, self.paper, self.holes, self.seed, self.color_ribbon,
                                                self.pin_shape, self.ink_set)]
        self._custom_inks = s.custom_inks
        self.ink_set.setCurrentIndex(max(0, self.ink_set.findData(s.ink_set)))
        self.dot_size.set_value(s.dot_size)
        self.density.set_value(s.density)
        self.saturation.set_value(s.saturation)
        self.wear.set_value(s.ribbon_wear)
        self.misalign.set_value(s.misalign)
        self.jitter.set_value(s.jitter)
        self.impact.set_value(s.impact)
        self.texture.set_value(s.paper_texture)
        self.ribbon.setCurrentIndex(max(0, self.ribbon.findData(s.ribbon)))
        self.pin_shape.setCurrentIndex(max(0, self.pin_shape.findData(s.pin_shape)))
        self.paper.setCurrentIndex(max(0, self.paper.findData(s.paper)))
        self.holes.setChecked(s.show_holes)
        self.color_ribbon.setChecked(s.color_ribbon)
        self.seed.setValue(s.seed % 1000000)
        del blockers
        self._paint_inks(s)

    def set_info(self, text: str):
        self.info.setText(text)
