from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QSpinBox)


class ExportDialog(QDialog):
    """Options for PDF / TIFF / PNG export."""

    def __init__(self, parent, fmt: str, n_pages: int, current_page: int, default_dpi: int = 200, rotation: int = 0,
                 kept: int | None = None):
        super().__init__(parent)
        self.fmt = fmt
        self.n_pages = n_pages
        self.setWindowTitle(f"Export {fmt.upper()}")
        form = QFormLayout(self)
        self.dpi = QSpinBox()
        self.dpi.setRange(72, 1200)
        self.dpi.setSingleStep(50)
        self.dpi.setValue(default_dpi)
        self.dpi.setSuffix(" dpi")
        form.addRow("Resolution", self.dpi)
        self.which = QComboBox()
        all_label = "All pages" if kept is None or kept == n_pages else f"All pages shown ({kept} of {n_pages}; deleted and hidden ones are left out)"
        self.which.addItems([all_label, f"Current page ({current_page + 1})", "Page range"])
        form.addRow("Pages", self.which)
        self.range = QLineEdit()
        self.range.setPlaceholderText(f"e.g. 1-3,5   (of {n_pages})")
        self.range.setEnabled(False)
        form.addRow("Range", self.range)
        self.which.currentIndexChanged.connect(lambda i: self.range.setEnabled(i == 2))
        self.current_page = current_page
        self.rotate = QCheckBox(f"Rotate pages {rotation}° clockwise, like the view")
        self.rotate.setChecked(rotation != 0)
        self.rotate.setVisible(rotation != 0)
        form.addRow("", self.rotate)
        self.quality = self.lossless = self.mode = self.per_page = None
        if fmt == "pdf":
            self.quality = QSpinBox()
            self.quality.setRange(40, 100)
            self.quality.setValue(90)
            form.addRow("JPEG quality", self.quality)
            self.lossless = QCheckBox("Lossless (bigger files)")
            form.addRow("", self.lossless)
        elif fmt == "tiff":
            self.mode = QComboBox()
            self.mode.addItem("Colour", "color")
            self.mode.addItem("Greyscale", "gray")
            self.mode.addItem("1-bit black & white (CCITT G4)", "mono")
            form.addRow("Colour mode", self.mode)
            self.per_page = QCheckBox("One file per page")
            form.addRow("", self.per_page)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def pages(self):
        i = self.which.currentIndex()
        if i == 0:
            return None
        if i == 1:
            return [self.current_page]
        from ..cli import parse_pages

        try:
            return parse_pages(self.range.text().strip() or None, self.n_pages)
        except ValueError:
            return None
