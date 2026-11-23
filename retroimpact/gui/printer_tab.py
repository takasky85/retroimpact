"""The "Printers" tab of the left column: page 1 of the current capture as every printer type would draw it.

Each tile is a thumbnail, the printer's name and a one-line verdict ("Looks fine", "Graphics out of step" ...); the tooltip
has the full explanation. Clicking a tile makes that printer the choice for the tab that is showing.
"""
from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..preview import BAD_LEVEL, KEYS, OK_LEVEL, SHORT_NAMES, PrinterPreview

LEVEL_COLOUR = {OK_LEVEL: "#3f9b4a", "warn": "#c58a12", BAD_LEVEL: "#cc4b3c"}
ASPECT = 11.0 / 8.5


class Tile(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self.selected = False
        self.src: Optional[QImage] = None
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFrameShape(QFrame.Shape.NoFrame)
        v = QVBoxLayout(self)
        v.setContentsMargins(5, 5, 5, 5)
        v.setSpacing(2)
        self.name = QLabel(SHORT_NAMES[key])
        self.name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        f = self.name.font()
        f.setBold(True)
        self.name.setFont(f)
        self.pic = QLabel("…")
        self.pic.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self.pic.setStyleSheet("background: #f1efe8; color: #888;")
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        self.verdict.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        vf = self.verdict.font()
        vf.setPointSizeF(max(7.0, vf.pointSizeF() * 0.9))
        self.verdict.setFont(vf)
        v.addWidget(self.name)
        v.addWidget(self.pic)
        v.addWidget(self.verdict)
        self.set_width(110)

    def set_width(self, w: int):
        self.img_w = max(60, w)
        self.pic.setFixedSize(self.img_w, int(self.img_w * ASPECT))
        self._paint_image()

    def _paint_image(self):
        if self.src is None or self.src.isNull():
            self.pic.setPixmap(QPixmap())
            return
        scaled = self.src.scaledToWidth(self.img_w, Qt.TransformationMode.SmoothTransformation)
        self.pic.setPixmap(QPixmap.fromImage(scaled))

    def show_result(self, r: PrinterPreview):
        if r.image is not None:
            h, w = r.image.shape[:2]
            self.src = QImage(r.image.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        else:
            self.src = None
        self._paint_image()
        if self.src is None:
            self.pic.setText("nothing printed")
        else:
            self.pic.setText("")
        self.verdict.setText(r.summary)
        self.verdict.setStyleSheet(f"color: {LEVEL_COLOUR.get(r.level, '#888')};")
        self.setToolTip(f"{r.label}\n\n{r.summary}\n{r.detail}\n\nClick to use this printer for this tab.")

    def clear_result(self, text: str = "…"):
        self.src = None
        self.pic.setPixmap(QPixmap())
        self.pic.setText(text)
        self.verdict.setText("")
        self.setToolTip("")

    def set_selected(self, on: bool):
        self.selected = on
        self.update()

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        pen = QPen(QColor("#3b82f6") if self.selected else QColor(128, 128, 128, 90))
        pen.setWidth(3 if self.selected else 1)
        p.setPen(pen)
        p.drawRoundedRect(QRect(1, 1, self.width() - 3, self.height() - 3), 6, 6)

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(ev)


class PrinterPreviewPanel(QWidget):
    chosen = Signal(str)  # 'auto' or a PROFILES key

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.head = QLabel("Open a file to see it as each printer would print it.")
        self.head.setWordWrap(True)
        self.head.setContentsMargins(6, 6, 6, 2)
        outer.addWidget(self.head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        body = QWidget()
        scroll.setWidget(body)
        self.grid = QGridLayout(body)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(6)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.tiles: Dict[str, Tile] = {}
        for n, key in enumerate(KEYS):
            t = Tile(key)
            t.clicked.connect(self.chosen.emit)
            self.tiles[key] = t
            self.grid.addWidget(t, n // 2, n % 2)
        self.scroll = scroll
        self.current = "auto"
        self.detected = ""

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        bar = self.scroll.verticalScrollBar().sizeHint().width()
        w = (self.width() - 8 - 6 - bar - 4 * 2 - 10 * 2) // 2  # two columns: margins, spacing, scrollbar, tile padding
        for t in self.tiles.values():
            t.set_width(w)

    # -- content
    def set_header(self, text: str):
        self.head.setText(text)

    def show_results(self, results: Dict[str, PrinterPreview], detected: str = ""):
        self.detected = detected
        for key, t in self.tiles.items():
            r = results.get(key)
            if r is None:
                t.clear_result()
            else:
                t.show_result(r)

    def show_one(self, r: PrinterPreview):
        t = self.tiles.get(r.key)
        if t is not None:
            t.show_result(r)

    def clear(self, text: str = ""):
        for t in self.tiles.values():
            t.clear_result()
        self.head.setText(text or "Open a file to see it as each printer would print it.")

    def set_current(self, key: str):
        self.current = key
        for k, t in self.tiles.items():
            t.set_selected(k == key)
