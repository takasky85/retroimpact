"""Main window: menus, toolbar, settings panel (left quarter) and a tab per open capture (right)."""
from __future__ import annotations

import dataclasses
import json
import os
import re
import time

from PySide6.QtCore import QByteArray, QEvent, QSettings, QSize, Qt, QThreadPool
from PySide6.QtGui import QAction, QActionGroup, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (QComboBox, QFileDialog, QLabel, QMainWindow, QMessageBox, QProgressDialog, QSpinBox,
                               QSplitter, QStackedWidget, QTabWidget, QToolBar, QVBoxLayout, QWidget)

from .. import __version__
from ..export import export_pdf, export_png, export_tiff
from ..model import PROFILE_ORDER, PROFILES, EmuOptions
from ..samples import SAMPLES
from ..settings import LOOK_PRESETS, RenderSettings
from ..virtual_printer import VirtualPrinter
from .export_dialog import ExportDialog
from .icons import app_icon, icon
from .page_view import PageView
from .printer_picker import PrinterPicker
from .printer_tab import PrinterPreviewPanel
from .settings_panel import SettingsPanel
from .playback_dialog import PlaybackDialog
from .thumbs import ThumbPanel
from .virtual_printer_dialog import VirtualPrinterDialog, default_spool_dir
from .workers import Bus, EmuJob, ExportJob, PreviewJob

RAW_FILTER = "Raw printer data (*.prn *.bin *.dat *.txt *.out *.lst *.spool *.raw);;All files (*)"
CODEPAGES = [("auto", "Printer default"), ("cp437", "IBM PC (CP437)"), ("cp850", "Multilingual (CP850)"),
             ("latin1", "Latin-1"), ("mac_roman", "Mac Roman"), ("ascii", "ASCII")]
CBM = [("auto", "Auto-detect"), ("upper", "Uppercase / graphics"), ("lower", "Lower / uppercase"),
       ("ascii", "ASCII text")]
MPSGAP = [
    ("gap", "Authentic: thin gap between lines"),
    ("fill", "Fill the gap (keeps full height)"),
    ("close", "Close the gap (picture shrinks)"),
]
BLANKS = [("keep", "Keep as sent"), ("collapse", "Collapse runs to one blank line"),
          ("remove", "Remove all blank lines (single-space)")]
NEWLINE = [("auto", "Auto-detect"), ("crlf", "CR = CR+LF"), ("lfcr", "LF = CR+LF"), ("raw", "Exactly as sent")]
ZOOMS = [("Fit width", ("fit", 0)), ("Fit page", ("page", 0))] + [
    (f"{p}%", ("fixed", p / 100.0)) for p in (25, 50, 75, 100, 150, 200, 300, 400)]


class Tab:
    def __init__(self, name: str, data: bytes, path: str, view: PageView):
        self.name, self.data, self.path, self.view = name, data, path, view
        self.doc = None
        self.token = 0
        self.emu_version = -1
        self.data_version = 0  # bumped whenever `data` is replaced (reload / refresh): previews are rebuilt
        self.previews: dict = {}  # preview.PrinterPreview per printer key: the "Printers" tab's thumbnails for this capture
        self.preview_sig = None  # what the finished previews were made from
        self.preview_running = None  # signature of the preview job in flight
        self.preview_token = 0
        self.preview_box = [0]  # shared with the job: it stops early once this no longer equals its token
        self.printer = "auto"  # this tab's printer type: 'auto' or a PROFILES key (never shared with other tabs)
        self.restore: dict | None = None  # zoom / page to put back after the first conversion (session restore)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("RetroImpact")
        self.setWindowIcon(app_icon())
        self.setAcceptDrops(True)
        self.qs = QSettings("RetroImpact", "RetroImpact")
        self.bus = Bus(self)
        self.pool = QThreadPool.globalInstance()
        self.emu = EmuOptions()
        self.emu_version = 0
        self.tokens = 0
        self.jobs = {}
        self.pjobs = {}  # preview job token -> Tab
        self.vprinter = None  # the running virtual printer, if any
        self.vp_label = QLabel("")
        self.def_columns, self.def_continuous = 1, True
        self._restoring = False
        self._themed: list = []  # (QAction, icon name) pairs re-themed when the palette changes (dark/light switch)

        try:
            settings = RenderSettings.from_dict(json.loads(self.qs.value("render", "{}")))
        except Exception:
            settings = RenderSettings.from_dict(LOOK_PRESETS["Office workhorse"])
        if not self.qs.contains("render"):
            settings = RenderSettings.from_dict(LOOK_PRESETS["Office workhorse"])

        self.panel = SettingsPanel(settings)
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        hint = QLabel("Drop a raw printer file here\nor use File ▸ Open")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #aaa; font-size: 16px; background: #3b3d40;")
        self.stack = QStackedWidget()
        self.stack.addWidget(hint)
        self.stack.addWidget(self.tabs)
        self.thumbs = ThumbPanel(self.bus, self.qs.value("thumb_w", 112, type=int))
        self.thumbs.width_changed.connect(lambda w: self.qs.setValue("thumb_w", int(w)))
        self.def_hide = self.qs.value("hide_flagged", False, type=bool)  # fold blank / garbled sheets away? (off by default)
        self.picker = PrinterPicker()
        self.picker.chosen.connect(self._printer_chosen)
        self.picker.setEnabled(False)
        self.pprev = PrinterPreviewPanel()
        self.pprev.chosen.connect(self._printer_chosen)
        self.left_tabs = QTabWidget()
        self.left_tabs.setDocumentMode(True)
        self.left_tabs.addTab(self.panel, "Look")
        self.left_tabs.addTab(self.pprev, "Printers")
        self.left_tabs.currentChanged.connect(lambda _i: self._maybe_previews(show=True))
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(0)
        lv.addWidget(self.left_tabs, 1)
        lv.addWidget(self.picker, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(left)
        split.addWidget(self.thumbs)
        split.addWidget(self.stack)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setStretchFactor(2, 3)
        split.setChildrenCollapsible(False)
        left.setMinimumWidth(285)
        self.setCentralWidget(split)
        self.split = split
        self.resize(1400, 860)
        split.setSizes([300, 150, 950])  # the thumbnail rail starts one small page wide; drag the splitter for more
        self.thumbs.setVisible(self.qs.value("show_thumbs", True, type=bool))

        self.status_file = QLabel("No file")
        self.status_pages = QLabel("")
        self.statusBar().addWidget(self.status_file, 1)
        self.statusBar().addPermanentWidget(self.vp_label)
        self.statusBar().addPermanentWidget(self.status_pages)

        self.panel.changed.connect(self._look_changed)
        self.bus.job_received.connect(self._job_received)
        self.bus.emu_done.connect(self._emu_done)
        self.bus.emu_failed.connect(self._emu_failed)
        self.bus.preview_one.connect(self._preview_one)
        self.bus.preview_all.connect(self._preview_all)
        self._themed.append((self.panel.reroll_btn, "dice"))
        self._build_menus()
        self._build_toolbar()
        self._restore()
        self._restore_session()
        cfg = self._vp_config()
        if cfg.get("autostart"):
            self._vp_start(cfg, quiet=True)

    # ------------------------------------------------------------------ helpers
    def cur(self):
        i = self.tabs.currentIndex()
        return self._tab_at(i) if i >= 0 else None

    def _tab_at(self, i):
        w = self.tabs.widget(i)
        return getattr(w, "tab", None)

    def all_tabs(self):
        return [self._tab_at(i) for i in range(self.tabs.count())]

    def _act(self, menu, text, slot=None, shortcut=None, checkable=False, checked=False, icon_name=None):
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icon(icon_name))
            self._themed.append((a, icon_name))
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
            a.setChecked(checked)
        if slot:
            (a.toggled if checkable else a.triggered).connect(slot)
        if menu is not None:
            menu.addAction(a)
        return a

    def _radio_menu(self, menu, title, items, current, slot):
        sub = menu.addMenu(title)
        grp = QActionGroup(self)
        grp.setExclusive(True)
        for key, label in items:
            a = QAction(label, self)
            a.setCheckable(True)
            a.setChecked(key == current)
            a.triggered.connect(lambda _c=False, k=key: slot(k))
            grp.addAction(a)
            sub.addAction(a)
        return sub

    # ------------------------------------------------------------------ menus
    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._act(m, "&Open raw printer data…", self.open_dialog, "Ctrl+O", icon_name="open")
        self.recent_menu = m.addMenu("Open &Recent")
        self._fill_recent()
        sm = m.addMenu("Load &Sample")
        for name, fn in SAMPLES.items():
            a = QAction(name, self)
            a.triggered.connect(lambda _c=False, n=name, f=fn: self.new_tab(n, f(), ""))
            sm.addAction(a)
        self.act_reopen = self._act(m, "Reopen last files on startup", None, None, True,
                                    self.qs.value("reopen_session", True, type=bool))
        self.act_reopen.toggled.connect(lambda c: self.qs.setValue("reopen_session", c))
        self._act(m, "&Reload", self.reload, "F5", icon_name="reload")
        self._act(m, "&Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        self._act(m, "Save &raw data as…", self.save_raw)
        m.addSeparator()
        self._act(m, "&Virtual printer…", self.virtual_printer_dialog, "Ctrl+Shift+P", icon_name="printer")
        m.addSeparator()
        self.act_pdf = self._act(m, "Export as &PDF…", lambda: self.export("pdf"), "Ctrl+E", icon_name="save")
        self._act(m, "Export as &TIFF…", lambda: self.export("tiff"), "Ctrl+T")
        self._act(m, "Export as P&NG…", lambda: self.export("png"))
        m.addSeparator()
        self._act(m, "&Quit", self.close, "Ctrl+Q")

        m = mb.addMenu("&Edit")
        self._act(m, "&Copy page image", self.copy_page, "Ctrl+C", icon_name="copy")
        self.act_dice = self._act(m, "Re-roll random &dots", self.panel._reroll, "Ctrl+R", icon_name="dice")
        m.addSeparator()
        self._act(m, "&Reset look to default", self.reset_look)
        self._act(m, "Restore deleted and hidden &pages", lambda: self.thumbs._restore())

        m = mb.addMenu("&View")
        self._act(m, "Zoom &in", lambda: self._view_do("zoom_by", 1.25), "Ctrl+=", icon_name="zoom_in")
        self._act(m, "Zoom &out", lambda: self._view_do("zoom_by", 0.8), "Ctrl+-", icon_name="zoom_out")
        self._act(m, "Fit &width", lambda: self._view_do("set_zoom", "fit"), "Ctrl+9", icon_name="fit_width")
        self._act(m, "Fit &page", lambda: self._view_do("set_zoom", "page"), "Ctrl+8", icon_name="fit_page")
        self._act(m, "&Actual size (100%)", lambda: self._view_do("set_zoom", "fixed", 1.0), "Ctrl+0")
        m.addSeparator()
        self._act(m, "Print-head &playback…", self.playback_dialog, "Ctrl+Shift+H")
        m.addSeparator()
        self.act_hand = self._act(m, "&Hand tool (drag to pan)", lambda on: self._tool_toggled("hand", on), "H",
                                  True, False, "hand")
        self.act_zoomtool = self._act(m, "&Zoom tool (drag a box to zoom to it, click zooms in, Shift+click out)",
                                      lambda on: self._tool_toggled("zoom", on), "Z", True, False, "zoom")
        m.addSeparator()
        self.act_one = self._act(m, "&One page across", lambda on: on and self._set_columns(1), None, True, True,
                                 "one_up")
        self.act_two = self._act(m, "&Two pages side by side", lambda on: on and self._set_columns(2), None, True,
                                 False, "two_up")
        self.cols_group = QActionGroup(self)
        self.cols_group.setExclusive(True)
        self.cols_group.addAction(self.act_one)
        self.cols_group.addAction(self.act_two)
        self.act_cont = self._act(m, "&Continuous scrolling", self._set_continuous, None, True, True, "continuous")
        m.addSeparator()
        self._act(m, "Rotate view &clockwise", lambda: self._rotate(90), "Ctrl+]", icon_name="rotate_cw")
        self._act(m, "Rotate view c&ounter-clockwise", lambda: self._rotate(-90), "Ctrl+[", icon_name="rotate_ccw")
        self._act(m, "Reset rotation", lambda: self._rotate(0, absolute=True), "Ctrl+Backspace")
        self.act_strip = self._act(m, "&Banner layout: all pages in one row", self._set_strip, None, True, False,
                                   "banner")
        m.addSeparator()
        self.act_thumbs = self._act(m, "&Thumbnails and page organiser", self._set_thumbs, "F4", True,
                                    self.thumbs.isVisible(), "thumbs")
        self.act_hidejunk = self._act(m, "Hide &blank and garbled pages", self._set_hide_flagged, None, True, self.def_hide)
        self.act_hidejunk.setToolTip("Sheets that are empty or look damaged are folded into a \"pages 10-60 hidden\" marker")
        m.addSeparator()
        self._act(m, "Next page", lambda: self._view_do("next_page"), "PgDown", icon_name="next")
        self._act(m, "Previous page", lambda: self._view_do("prev_page"), "PgUp", icon_name="prev")
        self._act(m, "Next tab", lambda: self._step_tab(1), "Ctrl+Tab")
        self._act(m, "Previous tab", lambda: self._step_tab(-1), "Ctrl+Shift+Tab")

        m = mb.addMenu("&Options")
        items = [("auto", "Auto-detect printer")] + [(k, PROFILES[k].label) for k in PROFILE_ORDER]
        psub = self._radio_menu(m, "&Printer (this tab)", items, "auto", self._printer_chosen)
        self.printer_actions = dict(zip([k for k, _ in items], psub.actions()))
        self._act(m, "Try next printer", lambda: self.cur() and self.picker.cycle(), "F8")
        self._act(m, "Split captures that switch printer type", lambda c: self._set_emu(split_mixed=c), None, True,
                  self.emu.split_mixed)
        self._radio_menu(m, "&Character set", CODEPAGES, self.emu.codepage, lambda k: self._set_emu(codepage=k))
        self._radio_menu(m, "Commodore c&harset", CBM, self.emu.cbm_charset, lambda k: self._set_emu(cbm_charset=k))
        self._radio_menu(m, "&Newlines", NEWLINE, "auto", self._set_newlines)
        self._radio_menu(m, "&Blank lines", BLANKS, self.emu.blank_lines, lambda k: self._set_emu(blank_lines=k))
        m.addSeparator()
        self._act(m, "ESC A line spacing applies immediately", lambda c: self._set_emu(esc_a_immediate=c), None, True,
                  self.emu.esc_a_immediate)
        self._radio_menu(m, "Commodore graphics &gap", MPSGAP, self.emu.mps_gap, lambda k: self._set_emu(mps_gap=k))
        self._act(m, "Skip blank form feeds", lambda c: self._set_emu(skip_blank_ff=c), None, True,
                  self.emu.skip_blank_ff)
        self._act(m, "Honour page-length commands", lambda c: self._set_emu(honor_page_length=c), None, True,
                  self.emu.honor_page_length)
        self._act(m, "Wide carriage (13.6 in)", lambda c: self._set_emu(wide_carriage=True if c else None), None, True,
                  False)
        self._act(m, "Force near-letter-quality", lambda c: self._set_emu(force_nlq=True if c else None), None, True,
                  False)
        m.addSeparator()
        lm = m.addMenu(icon("looks"), "&Look presets")
        self._themed.append((lm.menuAction(), "looks"))
        for name in LOOK_PRESETS:
            a = QAction(name, self)
            a.triggered.connect(lambda _c=False, n=name: self.apply_look(n))
            lm.addAction(a)

        m = mb.addMenu("&Help")
        self._act(m, "&Supported printers", self.show_printers, icon_name="printer")
        self._act(m, "&About RetroImpact", self.about, icon_name="info")

    def _build_toolbar(self):
        tb = QToolBar("Tools")
        tb.setMovable(False)
        tb.setIconSize(QSize(28, 28))
        self.addToolBar(tb)
        self.toolbar = tb

        def add(name, text, slot, checkable=False, tip=None):
            a = QAction(icon(name), text, self)
            self._themed.append((a, name))
            a.setToolTip(tip or text)
            a.setCheckable(checkable)
            (a.toggled if checkable else a.triggered).connect(slot)
            tb.addAction(a)
            return a

        add("open", "Open", lambda: self.open_dialog(), tip="Open raw printer data (Ctrl+O)")
        add("save", "Export PDF", lambda: self.export("pdf"), tip="Export as PDF (Ctrl+E)")
        add("reload", "Reload", lambda: self.reload(), tip="Reload from disk (F5)")
        tb.addSeparator()
        self.tb_hand = add("hand", "Hand tool", lambda on: self._tool_toggled("hand", on), True,
                           "Hand tool (H): drag to pan. Middle-mouse drag always pans.")
        self.tb_zoomtool = add("zoom", "Zoom tool", lambda on: self._tool_toggled("zoom", on), True,
                               "Zoom tool (Z): drag a box around what you want to see, or click to zoom in and "
                               "Shift+click to zoom out")
        tb.addSeparator()
        add("zoom_out", "Zoom out", lambda: self._view_do("zoom_by", 0.8), tip="Zoom out (Ctrl+-)")
        self.zoom_box = QComboBox()
        self.zoom_box.setEditable(True)
        self.zoom_box.setMinimumWidth(110)
        for label, _v in ZOOMS:
            self.zoom_box.addItem(label)
        self.zoom_box.activated.connect(self._zoom_chosen)
        self.zoom_box.lineEdit().returnPressed.connect(lambda: self._zoom_typed(self.zoom_box.currentText()))
        tb.addWidget(self.zoom_box)
        add("zoom_in", "Zoom in", lambda: self._view_do("zoom_by", 1.25), tip="Zoom in (Ctrl+=)")
        add("fit_width", "Fit width", lambda: self._view_do("set_zoom", "fit"), tip="Fit width (Ctrl+9)")
        add("fit_page", "Fit page", lambda: self._view_do("set_zoom", "page"), tip="Fit whole page (Ctrl+8)")
        tb.addSeparator()
        self.tb_one = add("one_up", "One page", lambda on: on and self._set_columns(1), True, "One page across")
        self.tb_two = add("two_up", "Two pages", lambda on: on and self._set_columns(2), True,
                          "Two pages side by side")
        g = QActionGroup(self)
        g.setExclusive(True)
        g.addAction(self.tb_one)
        g.addAction(self.tb_two)
        self.tb_one.setChecked(True)
        self.tb_cont = add("continuous", "Continuous", self._set_continuous, True,
                           "Continuous scrolling (off = one page or spread at a time)")
        self.tb_cont.setChecked(True)
        tb.addSeparator()
        add("rotate_ccw", "Rotate left", lambda: self._rotate(-90), tip="Rotate the view 90° counter-clockwise "
            "(Ctrl+[). Banners from Print Shop and PrintMaster print sideways.")
        add("rotate_cw", "Rotate right", lambda: self._rotate(90), tip="Rotate the view 90° clockwise (Ctrl+])")
        self.tb_strip = add("banner", "Banner layout", self._set_strip, True,
                            "Banner layout: all pages side by side in one row, like a banner coming off the tractor feed")
        tb.addSeparator()
        self.tb_thumbs = add("thumbs", "Thumbnails", self._set_thumbs, True,
                             "Thumbnails and page organiser (F4): delete, hide and restore pages")
        self.tb_thumbs.setChecked(self.thumbs.isVisible())
        add("prev", "Previous page", lambda: self._view_do("prev_page"), tip="Previous page (PgUp)")
        self.page_spin = QSpinBox()
        self.page_spin.setRange(1, 1)
        self.page_spin.setMinimumWidth(64)
        self.page_spin.editingFinished.connect(lambda: self._view_do("go_to", self.page_spin.value() - 1))
        self.page_spin.valueChanged.connect(lambda v: self._view_do("go_to", v - 1) if self.page_spin.hasFocus() else None)
        tb.addWidget(self.page_spin)
        self.page_total = QLabel(" / 0 ")
        tb.addWidget(self.page_total)
        add("next", "Next page", lambda: self._view_do("next_page"), tip="Next page (PgDown)")
        tb.addSeparator()
        self.tb_tractor = add("tractor", "Tractor holes", lambda on: self.panel.holes.setChecked(on), True,
                              "Show tractor-feed holes")
        self.tb_tractor.setChecked(self.panel.holes.isChecked())
        self.panel.holes.toggled.connect(self.tb_tractor.setChecked)
        add("dice", "Re-roll dots", lambda: self.panel._reroll(), tip="New random ink, dropouts and jitter (Ctrl+R)")
        self._sync_toolbar()

    # ------------------------------------------------------------------ toolbar plumbing
    def _view_do(self, name, *args):
        t = self.cur()
        if t:
            getattr(t.view, name)(*args)

    def _tool_toggled(self, tool, on):
        new = tool if on else "arrow"
        for a, nm in ((self.act_hand, "hand"), (self.tb_hand, "hand"), (self.act_zoomtool, "zoom"),
                      (self.tb_zoomtool, "zoom")):
            a.blockSignals(True)
            a.setChecked(new == nm)
            a.blockSignals(False)
        self.tool = new
        for t in self.all_tabs():
            t.view.set_tool(new)

    def _set_columns(self, n):
        self.def_columns = n
        for a in (self.act_one, self.tb_one):
            a.blockSignals(True)
            a.setChecked(n == 1)
            a.blockSignals(False)
        for a in (self.act_two, self.tb_two):
            a.blockSignals(True)
            a.setChecked(n == 2)
            a.blockSignals(False)
        t = self.cur()
        if t:
            t.view.set_columns(n)

    def _set_continuous(self, on):
        self.def_continuous = on
        for a in (self.act_cont, self.tb_cont):
            a.blockSignals(True)
            a.setChecked(on)
            a.blockSignals(False)
        t = self.cur()
        if t:
            t.view.set_continuous(on)

    def _zoom_chosen(self, i):
        mode, z = ZOOMS[i][1]
        self._view_do("set_zoom", mode, z)

    def _zoom_typed(self, text):
        t = text.strip().rstrip("%").strip()
        try:
            self._view_do("set_zoom", "fixed", min(800.0, max(10.0, float(t))) / 100.0)
        except ValueError:
            t = self.cur()
            if t:
                self.zoom_box.setEditText(t.view.zoom_text())

    def _sync_toolbar(self):
        t = self.cur()
        n = t.view.n_pages if t else 0
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, max(1, n))
        self.page_spin.setValue(min(max(1, (t.view.current_page() + 1) if t else 1), max(1, n)))
        self.page_spin.blockSignals(False)
        self.page_total.setText(f" / {n} ")
        if t:
            self.zoom_box.setEditText(t.view.zoom_text())
            self._set_columns_silent(t.view.columns)
            for a in (self.act_strip, self.tb_strip):
                a.blockSignals(True)
                a.setChecked(t.view.strip)
                a.blockSignals(False)
            self.act_hidejunk.blockSignals(True)
            self.act_hidejunk.setChecked(t.view.hide_flagged)
            self.act_hidejunk.blockSignals(False)
            for a in (self.act_cont, self.tb_cont):
                a.blockSignals(True)
                a.setChecked(t.view.continuous)
                a.blockSignals(False)

    def _rotate(self, delta: int, absolute: bool = False):
        t = self.cur()
        if t:
            t.view.set_rotation(delta if absolute else t.view.rotation + delta)
            self._sync_toolbar()
            self._save_session()

    def _set_thumbs(self, on):
        self.qs.setValue("show_thumbs", bool(on))
        self.thumbs.setVisible(bool(on))
        for a in (self.act_thumbs, self.tb_thumbs):
            a.blockSignals(True)
            a.setChecked(bool(on))
            a.blockSignals(False)

    def _set_hide_flagged(self, on):
        self.def_hide = bool(on)
        self.qs.setValue("hide_flagged", self.def_hide)
        for t in self.all_tabs():  # a window-wide choice: every open file follows it, and so does the next one you open
            t.view.hide_default = self.def_hide
            t.view.set_hide_flagged(self.def_hide)

    def _pages_changed(self, view):
        t = self.cur()
        if t is None or t.view is not view:
            return
        self._sync_toolbar()
        if t.doc is not None:
            self._show_status(t)
        self._save_session()

    def _show_status(self, t):
        v, doc = t.view, t.doc
        extra = []
        if v.hidden_pages():
            extra.append(f"{len(v.hidden_pages())} hidden")
        if v.deleted:
            extra.append(f"{len(v.deleted)} deleted")
        nd = sum(p.n_dots for p in doc.pages)
        self.status_pages.setText(f"{doc.n_pages} page(s)" + (f" ({', '.join(extra)})" if extra else "") + f", {nd:,} dots")

    def _set_strip(self, on):
        for a in (self.act_strip, self.tb_strip):
            a.blockSignals(True)
            a.setChecked(on)
            a.blockSignals(False)
        t = self.cur()
        if t:
            t.view.set_strip(on)
            self._save_session()

    def _set_columns_silent(self, n):
        for a, v in ((self.act_one, n == 1), (self.tb_one, n == 1), (self.act_two, n == 2), (self.tb_two, n == 2)):
            a.blockSignals(True)
            a.setChecked(v)
            a.blockSignals(False)

    def _step_tab(self, d):
        n = self.tabs.count()
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % n)

    # ------------------------------------------------------------------ files and tabs
    def _recent(self):
        v = self.qs.value("recent", [])
        if isinstance(v, str):  # QSettings collapses one-element lists to a string
            v = [v]
        return list(v or [])

    def _fill_recent(self):
        self.recent_menu.clear()
        files = [f for f in self._recent() if os.path.exists(f)]
        for f in files[:10]:
            a = QAction(f, self)
            a.triggered.connect(lambda _c=False, p=f: self.open_path(p))
            self.recent_menu.addAction(a)
        self.recent_menu.setEnabled(bool(files))

    def open_dialog(self):
        ps, _ = QFileDialog.getOpenFileNames(self, "Open raw printer data", self.qs.value("last_dir", ""), RAW_FILTER)
        for p in ps:
            self.open_path(p)

    def open_path(self, p: str):
        try:
            with open(p, "rb") as f:
                data = f.read()
        except OSError as e:
            QMessageBox.warning(self, "RetroImpact", f"Could not read {p}:\n{e}")
            return
        self.qs.setValue("last_dir", os.path.dirname(p))
        rec = [p] + [x for x in self._recent() if x != p]
        self.qs.setValue("recent", rec[:10])
        self._fill_recent()
        self.new_tab(os.path.basename(p), data, p)

    def new_tab(self, name: str, data: bytes, path: str, restore: dict | None = None):
        if path:  # same file already open: refresh that tab instead of opening a duplicate
            for i, t in enumerate(self.all_tabs()):
                if t.path == path:
                    t.data = data
                    t.data_version += 1
                    self.tabs.setCurrentIndex(i)
                    self._run(t)
                    return
        view = PageView(self.bus)
        view.columns, view.continuous = self.def_columns, self.def_continuous
        view.set_tool(getattr(self, "tool", "arrow"))
        view.hide_default = self.def_hide
        view.settings = self.panel.settings()
        t = Tab(name, data, path, view)
        t.restore = restore
        if restore and restore.get("printer") in PROFILES:
            t.printer = restore["printer"]
        view.tab = t
        view.page_changed.connect(lambda i, n, v=view: self._page_changed(v, i, n))
        view.zoom_changed.connect(lambda s, v=view: self._zoom_changed(v, s))
        view.layout_changed.connect(lambda v=view: self._layout_changed(v))
        view.pages_changed.connect(lambda v=view: self._pages_changed(v))
        idx = self.tabs.addTab(view, name)
        self.tabs.setTabToolTip(idx, path or name)
        self.stack.setCurrentIndex(1)
        if not self._restoring:
            self.tabs.setCurrentIndex(idx)
        self._run(t)
        self._save_session()

    def close_tab(self, i):
        if i < 0:
            return
        w = self.tabs.widget(i)
        self.tabs.removeTab(i)
        if w is not None:
            w.genbox.gen += 1
            w.deleteLater()
        if self.tabs.count() == 0:
            self.stack.setCurrentIndex(0)
            self.setWindowTitle("RetroImpact")
            self.status_file.setText("No file")
            self.status_pages.setText("")
            self.panel.set_info("")
            self._sync_printer(None)
            self._sync_toolbar()
        self._save_session()

    def reload(self):
        t = self.cur()
        if t and t.path:
            try:
                with open(t.path, "rb") as f:
                    t.data = f.read()
                t.data_version += 1
            except OSError as e:
                QMessageBox.warning(self, "RetroImpact", f"Could not read {t.path}:\n{e}")
                return
            self._run(t)

    def _run(self, t: Tab):
        self.tokens += 1
        t.token = self.tokens
        t.emu_version = self.emu_version
        self.jobs[t.token] = t
        self.status_file.setText(f"{t.name}  ({len(t.data):,} bytes) - converting…")
        self.pool.start(EmuJob(self.bus, t.token, t.data, dataclasses.replace(self.emu, printer=t.printer)))

    def _emu_done(self, token, doc):
        t = self.jobs.pop(token, None)
        if t is None or token != t.token or self.tabs.indexOf(t.view) < 0:
            return
        t.doc = doc
        pageset = None
        if t.restore:  # session restore: back to the zoom and page this file had when we quit
            r, t.restore = t.restore, None
            if r.get("zoom_mode") in ("fit", "page", "fixed"):
                t.view.zoom_mode = r["zoom_mode"]
                t.view.zoom = min(8.0, max(0.1, float(r.get("zoom", 1.0))))
            t.view.restore_page = max(0, int(r.get("page", 0)))
            pageset = r.get("pageset")
            t.view.rotation = int(r.get("rotation", 0)) % 360
            t.view.strip = bool(r.get("strip", False))
        t.view.set_document(doc, self.panel.settings())
        if pageset:
            t.view.apply_pageset(pageset)
        if t is self.cur():
            self._show_info(t)
            self._sync_printer(t)
            self._sync_toolbar()

    def _emu_failed(self, token, tb):
        t = self.jobs.pop(token, None)
        if t is not None:
            QMessageBox.critical(self, "RetroImpact", "Conversion failed:\n\n" + tb)

    def _show_info(self, t: Tab):
        doc = t.doc
        self.setWindowTitle(f"{t.name} - RetroImpact")
        if doc is None:
            return
        if doc.detected == "mixed":
            det = "mixed capture"
        else:
            det = PROFILES[doc.detected].label if doc.detected in PROFILES else doc.profile.label
        nd = sum(p.n_dots for p in doc.pages)
        self.status_file.setText(f"{t.name}  ({doc.n_bytes:,} bytes)  -  {det}")
        self._show_status(t)
        lines = [f"Printer: {det}"] + (["  " + n for n in doc.notes] if doc.detected == "mixed" else [])
        lines += ["Repaired: " + f for f in doc.fixes]
        lines += ["Warning: " + w for w in doc.warnings]
        cols = doc.colors_used()
        if cols:
            lines.append("Colour ribbon: " + ", ".join(cols))
        lines += [f"Pages: {doc.n_pages}", f"Dots: {nd:,}", f"Unknown commands: {doc.n_unknown}"]
        self.panel.set_info("\n".join(lines))

    def _printer_chosen(self, key: str):
        """The printer buttons / menu / F8 act on the tab that is showing, and only on that tab."""
        t = self.cur()
        if t is None or key not in self.printer_actions:
            return
        t.printer = key
        self._sync_printer(t)
        self._run(t)
        self._save_session()

    # ------------------------------------------------------------------ "Printers" tab: previews for every printer type
    def _preview_sig(self, t):
        s = self.panel.settings()
        return (t.data_version, self.emu_version, s.ink_set, s.custom_inks, s.color_ribbon, s.ribbon)

    def _maybe_previews(self, show: bool = False):
        """Make sure the Printers tab shows up-to-date previews for the tab that is showing (only while it is visible)."""
        if self.left_tabs.currentWidget() is not self.pprev:
            return
        t = self.cur()
        if t is None:
            self.pprev.clear()
            return
        sig = self._preview_sig(t)
        if t.preview_sig == sig and t.previews:
            if show:
                self._show_previews(t)
            return
        if t.preview_running == sig:
            if show:
                self._show_previews(t)
            return
        self.pjobs.pop(t.preview_token, None)
        self.tokens += 1
        t.preview_token = self.tokens
        t.preview_box[0] = self.tokens
        t.preview_running = sig
        t.previews = {}
        self.pjobs[self.tokens] = t
        self._show_previews(t)
        self.pool.start(PreviewJob(self.bus, self.tokens, t.preview_box, t.data, dataclasses.replace(self.emu),
                                   self.panel.settings()))

    def _show_previews(self, t):
        self.pprev.set_current(t.printer)
        auto = t.previews.get("auto")
        self.pprev.show_results(t.previews, auto.detected if auto is not None else "")
        if t.preview_running is not None and t.preview_running != t.preview_sig:
            self.pprev.set_header("Trying every printer type on the first page…")
        else:
            self.pprev.set_header("Page 1 of this file as each printer type would print it. Click one to use it for this tab.")

    def _preview_one(self, token, r):
        t = self.pjobs.get(token)
        if t is None or token != t.preview_token:
            return
        t.previews[r.key] = r
        if t is self.cur() and self.left_tabs.currentWidget() is self.pprev:
            self.pprev.show_one(r)

    def _preview_all(self, token, results):
        t = self.pjobs.pop(token, None)
        if t is None or token != t.preview_token:
            return
        t.previews = results
        t.preview_sig, t.preview_running = t.preview_running, None
        if t is self.cur() and self.left_tabs.currentWidget() is self.pprev:
            self._show_previews(t)

    def _sync_printer(self, t):
        if t is None:
            self.picker.setEnabled(False)
            self.picker.set_status("")
            self.pprev.set_current("auto")
            return
        self.pprev.set_current(t.printer)
        self.picker.setEnabled(True)
        self.picker.set_printer(t.printer)
        self.printer_actions[t.printer].setChecked(True)
        doc = t.doc
        if doc is None:
            self.picker.set_status("")
        elif t.printer == "auto":
            if doc.detected == "mixed":
                self.picker.set_status("Auto: mixed capture (several printer types)")
            else:
                name = PROFILES[doc.detected].label if doc.detected in PROFILES else doc.profile.label
                self.picker.set_status(f"Auto picked: {name}")
        else:
            self.picker.set_status(f"Forced: {PROFILES[t.printer].label}")

    def _tab_changed(self, i):
        t = self._tab_at(i) if i >= 0 else None
        self.thumbs.bind(t.view if t is not None else None)
        self._sync_printer(t)
        self._maybe_previews(show=True)
        if t is None:
            return
        if t.emu_version != self.emu_version:
            self._run(t)
        self._show_info(t)
        self._sync_toolbar()
        self._save_session()

    def _page_changed(self, view, i, n):
        t = self.cur()
        if t is None or t.view is not view:
            return
        self.page_spin.blockSignals(True)
        self.page_spin.setRange(1, max(1, n))
        self.page_spin.setValue(i + 1)
        self.page_spin.blockSignals(False)
        self.page_total.setText(f" / {n} ")

    def _zoom_changed(self, view, text):
        t = self.cur()
        if t is not None and t.view is view:
            self.zoom_box.setEditText(text)

    def _layout_changed(self, view):
        t = self.cur()
        if t is not None and t.view is view:
            self._sync_toolbar()

    # ------------------------------------------------------------------ emulation / look options
    def _set_emu(self, **kw):
        for k, v in kw.items():
            setattr(self.emu, k, v)
        self.emu_version += 1
        t = self.cur()
        if t:
            self._run(t)
        self._maybe_previews()

    def _set_newlines(self, key):
        mapping = {"auto": (None, None), "crlf": (True, False), "lfcr": (False, True), "raw": (False, False)}
        self.emu.cr_adds_lf, self.emu.lf_adds_cr = mapping[key]
        self._set_emu()

    def _look_changed(self):
        s = self.panel.settings()
        for t in self.all_tabs():
            t.view.set_settings(s)
        self._maybe_previews()

    def apply_look(self, name):
        self.panel.set_settings(self.panel.settings().replace(**LOOK_PRESETS[name]))
        s = self.panel.settings()
        for t in self.all_tabs():
            t.view.set_settings(s, immediate=True)

    def reset_look(self):
        self.apply_look("Office workhorse")

    def copy_page(self):
        t = self.cur()
        img = t.view.current_image() if t else None
        if img is not None:
            QGuiApplication.clipboard().setImage(img)

    # ------------------------------------------------------------------ export
    def export(self, fmt):
        t = self.cur()
        if t is None or t.doc is None:
            QMessageBox.information(self, "RetroImpact", "Open some printer data first.")
            return
        doc = t.doc
        shown = t.view.visible_pages()
        dlg = ExportDialog(self, fmt, doc.n_pages, t.view.current_page(), rotation=t.view.rotation, kept=len(shown))
        if dlg.exec() != ExportDialog.DialogCode.Accepted:
            return
        base = os.path.splitext(t.path)[0] if t.path else os.path.expanduser("~/printout")
        ext = {"pdf": ".pdf", "tiff": ".tif", "png": ".png"}[fmt]
        filt = {"pdf": "PDF (*.pdf)", "tiff": "TIFF (*.tif *.tiff)", "png": "PNG (*.png)"}[fmt]
        out, _ = QFileDialog.getSaveFileName(self, f"Export {fmt.upper()}", base + ext, filt)
        if not out:
            return
        s = self.panel.settings().replace(dpi=dlg.dpi.value(), rotation=t.view.rotation if dlg.rotate.isChecked() else 0)
        pages = dlg.pages()
        pages = shown if pages is None else [p for p in pages if p in set(shown)]  # deleted / hidden sheets stay out
        if not pages:
            QMessageBox.information(self, "RetroImpact", "There are no pages to export: they are all deleted or hidden.")
            return
        if fmt == "pdf":
            job = ExportJob(self.bus, export_pdf, doc, out, s, pages, jpeg_quality=dlg.quality.value(),
                            lossless=dlg.lossless.isChecked())
        elif fmt == "tiff":
            job = ExportJob(self.bus, export_tiff, doc, out, s, pages, mode=dlg.mode.currentData(),
                            per_page=dlg.per_page.isChecked())
        else:
            job = ExportJob(self.bus, export_png, doc, out, s, pages)
        self.bus.cancel_export = False
        pd = QProgressDialog("Rendering pages…", "Cancel", 0, 0, self)
        pd.setWindowModality(Qt.WindowModality.WindowModal)
        pd.setMinimumDuration(0)
        pd.canceled.connect(lambda: setattr(self.bus, "cancel_export", True))

        def prog(done, total):
            pd.setMaximum(max(1, total))
            pd.setValue(done)

        def finished(n, err):
            self.bus.export_progress.disconnect(prog)
            self.bus.export_done.disconnect(finished)
            pd.close()
            if err and err != "cancelled":
                QMessageBox.critical(self, "Export failed", err)
            elif not err:
                self.statusBar().showMessage(f"Wrote {n} page(s) to {out}", 8000)

        self.bus.export_progress.connect(prog)
        self.bus.export_done.connect(finished)
        self.pool.start(job)
        pd.show()

    # ------------------------------------------------------------------ virtual printer
    def _vp_config(self) -> dict:
        try:
            return json.loads(self.qs.value("virtual_printer", "{}"))
        except Exception:
            return {}

    def _vp_start(self, cfg: dict, quiet: bool = False) -> bool:
        self.qs.setValue("virtual_printer", json.dumps(cfg))
        spool = cfg.get("folder") or default_spool_dir() if cfg.get("keep", True) else ""

        def on_job(job):  # runs on a listener thread: only the signal crosses over to the window
            path = job.path  # a hot-folder file already lives somewhere; keep it there
            if spool and not path:
                try:
                    os.makedirs(spool, exist_ok=True)
                    path = os.path.join(spool, time.strftime("job_%Y%m%d_%H%M%S", time.localtime(job.when))
                                        + f"_{job.number}.prn")
                    with open(path, "wb") as f:
                        f.write(job.data)
                except OSError:
                    path = ""
            self.bus.job_received.emit(job.name, job.data, path)

        vp = VirtualPrinter(on_job, port=cfg.get("port", 9100) if cfg.get("tcp", True) else None,
                            host="0.0.0.0" if cfg.get("everyone") else "127.0.0.1",
                            pipe=(cfg.get("pipe") or None) if cfg.get("pipe_on") else None, idle=cfg.get("idle", 3.0),
                            watch=(cfg.get("watch") or None) if cfg.get("watch_on") else None)
        try:
            where = vp.start()
        except OSError as e:
            vp.stop()
            if not quiet:
                QMessageBox.warning(self, "Virtual printer", f"Could not start listening:\n{e}")
            self.vp_label.setText("")
            return False
        self.vprinter = vp
        self.vp_label.setText("Virtual printer: " + ", ".join(where) + "   ")
        return True

    def _vp_stop(self):
        if self.vprinter is not None:
            self.vprinter.stop()
            self.vprinter = None
        self.vp_label.setText("")

    def playback_dialog(self):
        """Watch the current page being printed: the head sweeps, the paper feeds, the ink appears."""
        t = self.cur()
        if t is None or t.view.doc is None or not t.view.doc.pages:
            return
        v = t.view
        idx = min(max(0, v.current_page()), len(v.doc.pages) - 1)
        dlg = PlaybackDialog(self, v.doc, idx, v.settings)
        self._playback = dlg  # keep a reference; it deletes itself when closed
        dlg.show()

    def save_raw(self):
        """Save the bytes exactly as received (before any repair), e.g. to send in with a bug report."""
        t = self.cur()
        if t is None:
            return
        base = re.sub(r"[^\w.-]+", "_", t.name).strip("_") or "capture"
        out, _ = QFileDialog.getSaveFileName(self, "Save raw data", base + ".prn", "Printer data (*.prn *.bin *.out);;All files (*)")
        if out:
            try:
                with open(out, "wb") as f:
                    f.write(t.data)
            except OSError as e:
                QMessageBox.warning(self, "Save raw data", f"Could not save:\n{e}")

    def virtual_printer_dialog(self):
        running = self.vprinter is not None
        if running:
            vp = self.vprinter
            status = (f"Listening: {self.vp_label.text().replace('Virtual printer: ', '').strip()}\n"
                      f"{vp.jobs} job(s), {vp.bytes_in:,} bytes received so far"
                      + (f"\nPipe: {vp.pipe_state}" if vp.pipe_state else "")
                      + (f"\nHot folder: {vp.watch_taken} file(s) picked up" if vp.watch else ""))
        else:
            status = "Not listening."
        dlg = VirtualPrinterDialog(self, self._vp_config(), running, status)
        accepted = dlg.exec() == VirtualPrinterDialog.DialogCode.Accepted
        cfg = dlg.config()
        self.qs.setValue("virtual_printer", json.dumps(cfg))  # the edits are kept even if nothing was started
        if not accepted:
            return
        if dlg.action in ("stop", "restart"):
            self._vp_stop()
        if dlg.action in ("start", "restart"):
            self._vp_start(cfg)

    def _job_received(self, name: str, data: bytes, path: str):
        self.new_tab(name, data, path)
        self.statusBar().showMessage(f"Received {name}: {len(data):,} bytes", 8000)

    # ------------------------------------------------------------------ misc UI
    def show_printers(self):
        text = "\n\n".join(f"{p.label}\n  {p.description}" for p in PROFILES.values())
        QMessageBox.information(self, "Supported printers", text)

    def about(self):
        QMessageBox.about(self, "About RetroImpact",
                          f"<b>RetroImpact {__version__}</b><br>Raw dot-matrix printer data to PDF / TIFF with a "
                          "genuine ribbon-and-paper look.<br><br>Epson ESC/P, IBM Proprinter, Star, Apple ImageWriter "
                          "and Commodore MPS streams, including captures that switch printer type.")

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        for u in ev.mimeData().urls():
            if u.isLocalFile():
                self.open_path(u.toLocalFile())

    def _restore(self):
        g = self.qs.value("geometry")
        if isinstance(g, (QByteArray, bytes)) and g:
            self.restoreGeometry(g)
        sp = self.qs.value("splitter2")  # (a new key: the first version saved a much wider thumbnail rail)
        if isinstance(sp, (QByteArray, bytes)) and sp:
            self.split.restoreState(sp)

    def changeEvent(self, ev):
        if ev.type() == QEvent.Type.PaletteChange:  # light <-> dark switch: repaint the icons in the new colours
            for obj, name in self._themed:
                obj.setIcon(icon(name))
        super().changeEvent(ev)

    # ------------------------------------------------------------------ session
    def _save_session(self):
        """Remember which files are open (and where we were in them) so the next launch can put them back."""
        if self._restoring:
            return
        tabs, current = [], 0
        for i, t in enumerate(self.all_tabs()):
            if t is None or not t.path:  # built-in samples have no file to reopen
                continue
            if i == self.tabs.currentIndex():
                current = len(tabs)
            tabs.append({"path": t.path, "printer": t.printer, **t.view.state()})
        self.qs.setValue("session", json.dumps({"tabs": tabs, "current": current, "columns": self.def_columns,
                                                "continuous": self.def_continuous}))

    def _restore_session(self):
        if not self.qs.value("reopen_session", True, type=bool):
            return
        try:
            sess = json.loads(self.qs.value("session", "{}") or "{}")
        except (ValueError, TypeError):
            return
        entries = [e for e in sess.get("tabs", []) if isinstance(e, dict) and os.path.isfile(e.get("path", ""))]
        if not entries:
            return
        self._restoring = True
        try:
            self._set_columns(2 if sess.get("columns") == 2 else 1)
            self._set_continuous(bool(sess.get("continuous", True)))
            for e in entries:
                try:
                    with open(e["path"], "rb") as f:
                        data = f.read()
                except OSError:
                    continue
                self.new_tab(os.path.basename(e["path"]), data, e["path"], restore=e)
            cur = min(max(0, int(sess.get("current", 0))), self.tabs.count() - 1)
            if self.tabs.count():
                self.tabs.setCurrentIndex(cur)
        finally:
            self._restoring = False
        self._tab_changed(self.tabs.currentIndex())
        self._save_session()

    def closeEvent(self, ev):
        self._save_session()
        self.qs.setValue("geometry", self.saveGeometry())
        self.qs.setValue("splitter2", self.split.saveState())
        self.qs.setValue("render", json.dumps(self.panel.settings().to_dict()))
        for t in self.all_tabs():
            t.view.genbox.gen += 1
        super().closeEvent(ev)
