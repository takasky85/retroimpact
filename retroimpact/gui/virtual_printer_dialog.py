"""File > Virtual printer...: listen for print jobs on a TCP port and/or a named pipe and open each one as a tab."""
from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QSpinBox, QVBoxLayout)

from ..virtual_printer import DEFAULT_PORT, default_pipe_name


def _native(path: str) -> str:
    """A folder as typed or picked (the file dialog hands back forward slashes), in this system's own spelling."""
    path = path.strip().strip('"')
    return os.path.normpath(os.path.expanduser(path)) if path else ""


def default_spool_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "RetroImpact Jobs")


def default_hot_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "RetroImpact Hot Folder")


class VirtualPrinterDialog(QDialog):
    def __init__(self, parent, cfg: dict, running: bool, status: str):
        super().__init__(parent)
        self.setWindowTitle("Virtual printer")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        intro = QLabel("Point an emulator or an old computer at this program instead of a file. Each print job that "
                       "arrives opens in its own tab.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        form = QFormLayout()
        self.use_tcp = QCheckBox("Listen on a TCP port (raw / JetDirect)")
        self.use_tcp.setChecked(cfg.get("tcp", True))
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(cfg.get("port", DEFAULT_PORT)))
        self.port.setToolTip("9100 is the standard raw printing port. MAME: -bitb socket.127.0.0.1:9100. "
                             "Windows: add a printer on a Standard TCP/IP Port with this address.")
        row = QHBoxLayout()
        row.addWidget(self.use_tcp)
        row.addWidget(self.port)
        form.addRow(row)
        self.everyone = QCheckBox("Accept jobs from other computers on the network (otherwise only this computer)")
        self.everyone.setChecked(cfg.get("everyone", False))
        form.addRow(self.everyone)
        self.use_pipe = QCheckBox("Listen on a named pipe")
        self.use_pipe.setChecked(cfg.get("pipe_on", False))
        self.pipe = QLineEdit(cfg.get("pipe") or default_pipe_name())
        self.pipe.setToolTip(r"Windows: \\.\pipe\Name    Linux / macOS: a path such as /tmp/retroimpact.pipe")
        row = QHBoxLayout()
        row.addWidget(self.use_pipe)
        row.addWidget(self.pipe, 1)
        form.addRow(row)
        self.use_watch = QCheckBox("Watch a folder")
        self.use_watch.setChecked(cfg.get("watch_on", False))
        self.use_watch.setToolTip("Every file that appears in this folder (or changes) opens as a tab once it has stopped growing. "
                                  "Good with 86Box's File printer device, or a Windows 'FILE:' printer port.")
        self.watch = QLineEdit(cfg.get("watch") or default_hot_dir())
        wbrowse = QPushButton("Browse…")
        wbrowse.clicked.connect(lambda: self._browse_into(self.watch, "Folder to watch"))
        row = QHBoxLayout()
        row.addWidget(self.use_watch)
        row.addWidget(self.watch, 1)
        row.addWidget(wbrowse)
        form.addRow(row)
        self.idle = QDoubleSpinBox()
        self.idle.setRange(0.5, 120.0)
        self.idle.setSingleStep(0.5)
        self.idle.setSuffix(" s")
        self.idle.setValue(float(cfg.get("idle", 3.0)))
        self.idle.setToolTip("A job ends when the sender closes the connection, or after this much silence "
                             "(emulators such as MAME keep the connection open).")
        form.addRow("A job ends after silence of", self.idle)
        self.keep = QCheckBox("Keep a copy of every job in a folder")
        self.keep.setChecked(cfg.get("keep", True))
        self.folder = QLineEdit(cfg.get("folder") or default_spool_dir())
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.keep)
        row.addWidget(self.folder, 1)
        row.addWidget(browse)
        form.addRow(row)
        self.autostart = QCheckBox("Start listening when RetroImpact opens")
        self.autostart.setChecked(cfg.get("autostart", False))
        form.addRow(self.autostart)
        lay.addLayout(form)
        if sys.platform == "win32":
            tip = QLabel("86Box: LPT port device = Named Pipe, mode Auto, pipe path as above, cable type 'Raw SPP with "
                         "handshaking' (the other cable types are for Direct Cable Connection, not printers). "
                         "Either program can be started first.")
            tip.setWordWrap(True)
            lay.addWidget(tip)
        self.status = QLabel(status)
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.action = ""  # what the user chose: "start", "restart" (apply the changes) or "stop"
        self.buttons = QDialogButtonBox()
        if running:
            apply_btn = self.buttons.addButton("Apply changes", QDialogButtonBox.ButtonRole.AcceptRole)
            stop_btn = self.buttons.addButton("Stop listening", QDialogButtonBox.ButtonRole.DestructiveRole)
            apply_btn.clicked.connect(lambda: self._finish("restart"))
            stop_btn.clicked.connect(lambda: self._finish("stop"))
        else:
            start_btn = self.buttons.addButton("Start listening", QDialogButtonBox.ButtonRole.AcceptRole)
            start_btn.clicked.connect(lambda: self._finish("start"))
        self.buttons.addButton(QDialogButtonBox.StandardButton.Close).clicked.connect(self.reject)
        lay.addWidget(self.buttons)
        self.running = running

    def _browse(self):
        self._browse_into(self.folder, "Folder for received jobs")

    def _browse_into(self, edit, title):
        d = QFileDialog.getExistingDirectory(self, title, edit.text())
        if d:
            edit.setText(os.path.normpath(d))

    def _finish(self, action: str):
        self.action = action
        self.accept()

    def config(self) -> dict:
        return {"tcp": self.use_tcp.isChecked(), "port": self.port.value(), "everyone": self.everyone.isChecked(),
                "watch_on": self.use_watch.isChecked(), "watch": _native(self.watch.text()),
                "pipe_on": self.use_pipe.isChecked(), "pipe": self.pipe.text().strip(), "idle": self.idle.value(),
                "keep": self.keep.isChecked(), "folder": _native(self.folder.text()), "autostart": self.autostart.isChecked()}


__all__ = ["VirtualPrinterDialog", "default_spool_dir"]
