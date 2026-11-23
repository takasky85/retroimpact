from __future__ import annotations

import sys


def run_gui(argv) -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow

    app = QApplication(sys.argv[:1] + list(argv))
    app.setApplicationName("RetroImpact")
    app.setOrganizationName("RetroImpact")
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    files = [a for a in argv if not a.startswith("-")]
    for f in files:
        win.open_path(f)
    return app.exec()
