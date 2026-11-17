"""Main window in the classic arrangement: menu bar, two tool bars, WorkFlow bar on the
left, layout pages in the centre, Channel Browser on the right, cycle selector in the
status bar."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
from PySide6.QtCore import QSettings, QSize, Qt, QTimer
from PySide6.QtGui import (QAction, QActionGroup, QGuiApplication, QKeySequence, QPageLayout,
                           QPageSize, QPainter, QPdfWriter)
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDockWidget, QFileDialog,
                               QInputDialog, QLabel, QMainWindow, QMessageBox, QPlainTextEdit,
                               QProgressBar, QSlider, QSpinBox, QStyle, QTabWidget, QToolBar,
                               QToolButton)

from .. import APP_NAME, __version__
from ..core.config import load_config, save_config
from ..core.datamodel import Formula
from ..core.export import _write_table, write_outputs
from .dialogs import (SETTINGS_TABS, CalculatorDialog, ChecklistDialog, DataExplorer,
                      DiagramDialog, FormulaEditor, ObjectWizard, SettingsDialog, TextDialog,
                      sniff_or_ask)
from .diagram import DiagramObject, default_curve
from .layouts import (LAYOUT_VERSION, SUFFIX, available_layouts, ensure_builtin, read_layout,
                      user_dir, write_layout)
from .objects import DEFAULT_PARAMS, DEFAULT_STATS, result_names
from .page import OBJECT_TYPES, Page, PageView
from .panels import ChannelBrowser, LayoutExplorer, ObjectLibrary, WorkFlowBar
from .session import MODE_LABEL, MODES, Session

WORK_SUFFIX = ".ocwork"


def library_entries() -> list:
    hl = ("H", "L")
    c = default_curve
    return [
        ("Pressure vs. Crank Angle", "Cylinder pressure High and Low",
         {"type": "diagram", "curves": [c("CA", f"PCYL_{x}") for x in hl],
          "x": {"auto": False, "min": -90.0, "max": 120.0}}),
        ("Pressure and PI Current", "Pressure with PI current in a second strip",
         {"type": "diagram", "curves": [c("CA", f"PCYL_{x}") for x in hl]
          + [c("CA", f"PI_{x}", strip=1) for x in hl],
          "x": {"auto": False, "min": -90.0, "max": 120.0}}),
        ("Heat Release", "Rate of heat release and integral",
         {"type": "diagram", "curves": [c("CA", f"Q_{x}") for x in hl]
          + [c("CA", f"I_{x}", strip=1) for x in hl],
          "x": {"auto": False, "min": -40.0, "max": 100.0}}),
        ("p-V Diagram", "Mean cycle, double logarithmic",
         {"type": "diagram", "curves": [c("CA", f"PCYL_{x}", mode="mean", xname=f"V_{x}")
                                        for x in hl], "logx": True,
          "axes": {"0:0": {"auto": True, "min": 1.0, "max": 200.0, "log": True}}}),
        ("Knock", "Pressure and knock band",
         {"type": "diagram", "curves": [c("CA", f"PCYL_{x}") for x in hl]
          + [c("CA", f"PKNK_{x}", strip=1) for x in hl],
          "x": {"auto": False, "min": -20.0, "max": 80.0}}),
        ("Cycle Trend", "IMEP and PMAX over the cycle number",
         {"type": "diagram", "curves": [c("CY", f"IMEP_{x}") for x in hl]
          + [c("CY", f"PMAX_{x}", strip=1) for x in hl]}),
        ("Raw Signals", "Pressure and PI current over time",
         {"type": "diagram", "curves": [c("TIME", f"P_{x}") for x in hl]
          + [c("TIME", f"PI_RM_{x}", strip=1) for x in hl]}),
        ("Result Table", "Cycle results", {"type": "table"}),
        ("Statistics Table", "Statistics High / Low", {"type": "stats"}),
        ("Parameter Table", "Test parameters", {"type": "params"}),
    ]


class MainWindow(QMainWindow):
    def __init__(self, session: Session | None = None):
        super().__init__()
        self.session = session or Session(self)
        self.pages: list[Page] = []
        self.layout_path: Path | None = None
        self.layout_name = "Untitled"
        self.workspace_name = "default"
        self.modified = False
        self.confirm = True                 # ask before discarding changes
        self.design = False
        self._loading = False
        self.resize(1400, 880)
        self.setAcceptDrops(True)
        self.setDockNestingEnabled(True)
        self.setTabPosition(Qt.RightDockWidgetArea, QTabWidget.East)

        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(False)
        self.tabs.setDocumentMode(True)
        self.tabs.currentChanged.connect(self._page_changed)
        self.tabs.tabCloseRequested.connect(self.delete_page)
        self.tabs.tabBarDoubleClicked.connect(self.rename_page)
        self.setCentralWidget(self.tabs)

        self._make_actions()
        self._make_menus()
        self._make_toolbars()
        self._make_docks()
        self._make_statusbar()

        s = self.session
        s.message.connect(self._log)
        s.filesChanged.connect(self._files_changed)
        s.dataChanged.connect(self._update_title)
        s.cycleChanged.connect(self._cycle_changed)
        s.modeChanged.connect(self._mode_changed)
        s.busyChanged.connect(self._busy)
        s.progress.connect(self._progress)
        s.failed.connect(lambda text: QMessageBox.warning(self, APP_NAME, text)
                         if self.confirm else None)
        self.play_timer = QTimer(self)
        self.play_timer.setInterval(250)
        self.play_timer.timeout.connect(self._play_step)

        self.refresh_layout_list()
        self._files_changed()
        self._update_title()

    # ============================================================ actions
    def _act(self, text, slot=None, shortcut=None, checkable=False, icon=None, tip=None):
        a = QAction(text, self)
        if slot is not None:
            a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.setCheckable(checkable)
        if icon is not None:
            a.setIcon(self.style().standardIcon(icon))
        if tip:
            a.setToolTip(tip)
            a.setStatusTip(tip)
        return a

    def _make_actions(self) -> None:
        A, SP = self._act, QStyle
        self.a_new = A("&New Layout", self.new_layout, "Ctrl+N", icon=SP.SP_FileIcon)
        self.a_open = A("&Open Measurement...", lambda: self.open_measurement(), "Ctrl+O",
                        icon=SP.SP_DialogOpenButton)
        self.a_open_opt = A("Open with &Import Options...",
                            lambda: self.open_measurement(ask=True))
        self.a_open_layout = A("Open &Layout...", lambda: self.open_layout())
        self.a_save = A("&Save Layout", lambda: self.save_layout(), "Ctrl+S",
                        icon=SP.SP_DialogSaveButton)
        self.a_save_as = A("Save Layout &As...", lambda: self.save_layout(ask=True))
        self.a_open_work = A("Open &Workspace...", lambda: self.open_workspace())
        self.a_save_work = A("Save Wor&kspace...", lambda: self.save_workspace())
        self.a_print = A("&Print / PDF...", lambda: self.export_pdf(all_pages=True), "Ctrl+P",
                         icon=SP.SP_FileDialogDetailedView)
        self.a_exit = A("E&xit", self.close, "Ctrl+Q")
        self.a_exp_png = A("Page as &PNG...", lambda: self.export_png())
        self.a_exp_pdf = A("Page as P&DF...", lambda: self.export_pdf())
        self.a_exp_pdf_all = A("&Layout as PDF...", lambda: self.export_pdf(all_pages=True))
        self.a_exp_data = A("&Curve Data as CSV...", lambda: self.export_curve_data())
        self.a_exp_results = A("&Results and Report...", lambda: self.export_results())

        self.a_design = A("&Design", self.set_design, "F4", checkable=True,
                          tip="Design mode: insert, move and resize objects")
        self.a_copy = A("&Copy Diagram", self.copy_object, "Ctrl+Shift+C")
        self.a_dup = A("D&uplicate Object", self.duplicate_object, "Ctrl+D")
        self.a_delete = A("&Delete Object", self.delete_object, "Del")
        self.a_props = A("&Properties...", self.object_properties, "Alt+Return")
        self.a_exclude = A("E&xclude Current Cycle from Statistics",
                           lambda: self.session.toggle_exclude(), "Ctrl+E")

        self.a_zoom_back = A("Zoom &Back", lambda: self._diagram_call("zoom_back"), "Backspace",
                             icon=SP.SP_ArrowBack)
        self.a_show_all = A("Show &All", lambda: self._diagram_call("show_all"), "Home",
                            icon=SP.SP_BrowserReload)
        self.a_cursor2 = A("Cursor &2", lambda: self._diagram_call("toggle_cursor2"), "F6")
        self.a_link = A("&Link Cursors", self.set_cursor_link, checkable=True)
        self.a_link.setChecked(True)
        self.mode_group = QActionGroup(self)
        self.a_modes = {}
        for m in MODES:
            a = A(MODE_LABEL[m], lambda _=False, m=m: self.session.set_mode(m), checkable=True)
            self.mode_group.addAction(a)
            self.a_modes[m] = a
        self.a_modes["single"].setChecked(True)
        self.a_range = A("Cycle &Range...", self.ask_range)

        self.a_new_page = A("New &Page", lambda: self.add_page(), "Ctrl+T")
        self.a_rename_page = A("&Rename Page...", lambda: self.rename_page())
        self.a_delete_page = A("De&lete Page", lambda: self.delete_page())
        self.a_wizard = A("Object &Wizard...", self.object_wizard, "Ins")
        self.a_insert = {key: A(label, lambda _=False, k=key: self.insert_object({"type": k}))
                         for key, (label, _d, _c) in OBJECT_TYPES.items()}
        self.a_first = A("First Cycle", lambda: self.session.set_cycle(0), "Ctrl+Home")
        self.a_prev_cycle = A("Previous Cycle",
                              lambda: self.session.set_cycle(self.session.cycle - 1), "PgUp")
        self.a_next_cycle = A("Next Cycle",
                              lambda: self.session.set_cycle(self.session.cycle + 1), "PgDown")
        self.a_last = A("Last Cycle",
                        lambda: self.session.set_cycle(self.session.n_cycles - 1), "Ctrl+End")

        self.a_settings = {t: A(f"{t}...", lambda _=False, t=t: self.settings_dialog(t))
                           for t in SETTINGS_TABS}
        self.a_load_cfg = A("&Load Settings...", lambda: self.load_settings())
        self.a_save_cfg = A("&Save Settings...", lambda: self.save_settings())

        self.a_recalc = A("&Recalculate", self.session.recalculate, "F9")
        self.a_play = A("&Play Cycles", self.toggle_play, "F5", checkable=True)
        self.a_calc = A("&Calculator...", self.calculator)
        self.a_formula = A("&Formula Editor...", self.formula_editor)
        self.a_explorer = A("&Data Explorer...", self.data_explorer, "Ctrl+Shift+O")

        self.a_next = A("&Next Page", lambda: self._step_page(1), "Ctrl+PgDown")
        self.a_prev = A("&Previous Page", lambda: self._step_page(-1), "Ctrl+PgUp")
        self.a_about = A("&About...", self.about)

    def _make_menus(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("&File")
        m.addActions([self.a_new, self.a_open, self.a_open_opt, self.a_open_layout])
        m.addSeparator()
        m.addActions([self.a_save, self.a_save_as])
        m.addSeparator()
        m.addActions([self.a_open_work, self.a_save_work])
        m.addSeparator()
        ex = m.addMenu("&Export")
        ex.addActions([self.a_exp_png, self.a_exp_pdf, self.a_exp_pdf_all, self.a_exp_data,
                       self.a_exp_results])
        m.addAction(self.a_print)
        m.addSeparator()
        self.recent_menu = m.addMenu("&Recent Files")
        m.addSeparator()
        m.addAction(self.a_exit)

        m = mb.addMenu("&Edit")
        m.addActions([self.a_design, self.a_props])
        m.addSeparator()
        m.addActions([self.a_copy, self.a_dup, self.a_delete])
        m.addSeparator()
        m.addAction(self.a_exclude)

        self.view_menu = mb.addMenu("&View")
        m = mb.addMenu("&Components")
        m.addAction(self.a_wizard)
        m.addSeparator()
        m.addActions(list(self.a_insert.values()))
        m.addSeparator()
        m.addActions([self.a_new_page, self.a_rename_page, self.a_delete_page])

        m = mb.addMenu("E&xtras")
        m.addActions(list(self.a_settings.values()))
        m.addSeparator()
        m.addActions([self.a_load_cfg, self.a_save_cfg])

        m = mb.addMenu("E&xecute")
        m.addActions([self.a_recalc, self.a_play])
        m.addSeparator()
        m.addActions([self.a_first, self.a_prev_cycle, self.a_next_cycle, self.a_last])
        m.addSeparator()
        m.addActions([self.a_calc, self.a_formula])

        m = mb.addMenu("&Window")
        m.addActions([self.a_next, self.a_prev])
        m = mb.addMenu("&Help")
        m.addAction(self.a_about)
        self._fill_recent()

    def _make_toolbars(self) -> None:
        tb = QToolBar("Standard")
        tb.setObjectName("tb_standard")
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        for a, text in ((self.a_new, "New"), (self.a_open, "Open"), (self.a_save, "Save"),
                        (self.a_print, "Print")):
            a.setIconText(text)
            tb.addAction(a)
        tb.addSeparator()
        self.page_combo = QComboBox()
        self.page_combo.setMinimumWidth(150)
        self.page_combo.setToolTip("Page")
        self.page_combo.activated.connect(self.tabs.setCurrentIndex)
        tb.addWidget(self.page_combo)
        tb.addSeparator()
        tb.addAction(self.a_design)
        tb.addSeparator()
        self.a_zoom_back.setIconText("Zoom Back")
        self.a_show_all.setIconText("Show All")
        tb.addActions([self.a_zoom_back, self.a_show_all, self.a_cursor2, self.a_link])

        tb2 = QToolBar("Tools")
        tb2.setObjectName("tb_tools")
        tb2.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBarBreak()
        self.addToolBar(tb2)
        self.a_tb_explorer = self._act("Data Explorer", self.data_explorer)
        self.a_tb_wizard = self._act("Object Wizard", self.object_wizard)
        self.a_tb_calc = self._act("Calculator", self.calculator)
        self.a_tb_formula = self._act("Formula Editor", self.formula_editor)
        self.a_tb_eval = self._act("Evaluation", lambda: self.settings_dialog("Evaluation"))
        tb2.addActions([self.a_tb_explorer, self.a_tb_wizard, self.a_tb_calc,
                        self.a_tb_formula, self.a_tb_eval])
        name = QLabel(f"  {APP_NAME.upper()}  ")
        name.setStyleSheet("color:#274b86;font-weight:bold;font-size:13pt;")
        spacer = QLabel()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding,
                             spacer.sizePolicy().verticalPolicy())
        tb2.addWidget(spacer)
        tb2.addWidget(name)

    def _make_docks(self) -> None:
        groups = [
            ("Data Access", "data", [
                ("Open Measurement", "Load a CSV measurement file", lambda: self.open_measurement()),
                ("Data Explorer", "Manage the loaded measurements", self.data_explorer),
                ("Import Options", "Open a file and check format and columns",
                 lambda: self.open_measurement(ask=True))]),
            ("Data Display", "display", [
                ("Object Wizard", "Insert a diagram, table or text", self.object_wizard),
                ("New Diagram", "Insert an empty diagram on the page",
                 lambda: self.insert_object({"type": "diagram"})),
                ("New Page", "Add a page to the layout", lambda: self.add_page()),
                ("Design Mode", "Move and resize the objects of the page",
                 lambda: self.set_design(not self.design))]),
            ("Calculations", "calc", [
                ("Calculator", "Create a formula using the calculator", self.calculator),
                ("Formula Editor", "Create formulas with the formula editor",
                 self.formula_editor),
                ("Evaluation", "Zero level correction, filter, heat release",
                 lambda: self.settings_dialog("Evaluation")),
                ("Recalculate", "Evaluate all loaded measurements again",
                 self.session.recalculate)]),
            ("Export", "export", [
                ("Page as PNG", "Save the current page as a picture", lambda: self.export_png()),
                ("Layout as PDF", "Save all pages as a PDF file",
                 lambda: self.export_pdf(all_pages=True)),
                ("Curve Data", "Save the curves of the active diagram as CSV",
                 lambda: self.export_curve_data()),
                ("Results and Report", "Write result tables and the HTML report",
                 lambda: self.export_results())]),
            ("Utilities", "util", [
                ("Engine Parameters", "Geometry and TDC offset",
                 lambda: self.settings_dialog("Engine Parameters")),
                ("Channel Scaling", "Pressure sensitivity and offsets",
                 lambda: self.settings_dialog("Channel Scaling")),
                ("PI Detection", "Thresholds of the PI current evaluation",
                 lambda: self.settings_dialog("PI Detection")),
                ("Knock", "Band, windows and threshold of the knock evaluation",
                 lambda: self.settings_dialog("Knock"))]),
        ]
        self.workflow = WorkFlowBar(groups)
        self.workflow.layoutRequested.connect(lambda p: self.open_layout(p))
        self.dock_flow = self._dock("WorkFlow", self.workflow, Qt.LeftDockWidgetArea)
        self.dock_flow.setMinimumWidth(96)
        self.dock_flow.setMaximumWidth(150)

        self.browser = ChannelBrowser(self.session)
        self.browser.channelActivated.connect(self.add_channel)
        self.browser.newDiagramRequested.connect(self.new_diagram_with)
        self.dock_browser = self._dock("Channel Browser", self.browser, Qt.RightDockWidgetArea)
        self.dock_browser.setMinimumWidth(280)

        self.library = ObjectLibrary(library_entries())
        self.library.objectRequested.connect(self.insert_object)
        self.dock_library = self._dock("Object Library", self.library, Qt.RightDockWidgetArea)
        self.explorer = LayoutExplorer()
        self.explorer.objectSelected.connect(self._explorer_select)
        self.dock_explorer = self._dock("Layout Explorer", self.explorer,
                                        Qt.RightDockWidgetArea)
        self.tabifyDockWidget(self.dock_browser, self.dock_library)
        self.tabifyDockWidget(self.dock_library, self.dock_explorer)
        self.dock_browser.raise_()

        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setMaximumBlockCount(2000)
        self.dock_msg = self._dock("Messages", self.messages, Qt.BottomDockWidgetArea)
        self.resizeDocks([self.dock_msg], [110], Qt.Vertical)
        self.resizeDocks([self.dock_browser], [330], Qt.Horizontal)

        vm = self.view_menu
        for d in (self.dock_flow, self.dock_browser, self.dock_library, self.dock_explorer,
                  self.dock_msg):
            vm.addAction(d.toggleViewAction())
        vm.addSeparator()
        vm.addActions([self.a_zoom_back, self.a_show_all, self.a_cursor2, self.a_link])
        vm.addSeparator()
        dm = vm.addMenu("Display &Mode")
        dm.addActions(list(self.a_modes.values()))
        dm.addSeparator()
        dm.addAction(self.a_range)

    def _dock(self, title, widget, area) -> QDockWidget:
        d = QDockWidget(title, self)
        d.setObjectName("dock_" + title.replace(" ", "_").lower())
        d.setWidget(widget)
        self.addDockWidget(area, d)
        return d

    def _make_statusbar(self) -> None:
        sb = self.statusBar()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setRange(0, 100)
        self.progress.hide()
        sb.addPermanentWidget(self.progress)

        sb.addPermanentWidget(QLabel("Zoom"))
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(50, 300)
        self.zoom.setValue(100)
        self.zoom.setFixedWidth(110)
        self.zoom.setToolTip("Page zoom")
        self.zoom.valueChanged.connect(self._zoom_changed)
        sb.addPermanentWidget(self.zoom)

        self.mode_combo = QComboBox()
        for m in MODES:
            self.mode_combo.addItem(MODE_LABEL[m], m)
        self.mode_combo.setToolTip("Display of cycle resolved curves")
        self.mode_combo.activated.connect(
            lambda i: self.session.set_mode(self.mode_combo.itemData(i)))
        sb.addPermanentWidget(self.mode_combo)

        self.play_btn = QToolButton()
        self.play_btn.setDefaultAction(self.a_play)
        self.play_btn.setText("\u25b6")
        self.a_play.setIconText("\u25b6")
        sb.addPermanentWidget(self.play_btn)
        self.cycle_slider = QSlider(Qt.Horizontal)
        self.cycle_slider.setFixedWidth(140)
        self.cycle_slider.setRange(1, 1)
        self.cycle_slider.valueChanged.connect(lambda v: self.session.set_cycle(v - 1))
        sb.addPermanentWidget(self.cycle_slider)
        self.cycle_spin = QSpinBox()
        self.cycle_spin.setRange(1, 1)
        self.cycle_spin.setMinimumWidth(64)
        self.cycle_spin.valueChanged.connect(lambda v: self.session.set_cycle(v - 1))
        sb.addPermanentWidget(self.cycle_spin)
        self.cycle_label = QLabel("Cycle")
        sb.addPermanentWidget(self.cycle_label)
        sb.showMessage("Ready")

    # ============================================================== state
    def current_page(self) -> Page | None:
        i = self.tabs.currentIndex()
        return self.pages[i] if 0 <= i < len(self.pages) else None

    def active_diagram(self) -> DiagramObject | None:
        p = self.current_page()
        return p.active_diagram() if p else None

    def _diagram_call(self, method: str) -> None:
        d = self.active_diagram()
        if d is not None:
            getattr(d, method)()

    def set_modified(self, on: bool = True) -> None:
        if self._loading:
            return
        self.modified = on
        self._update_title()
        self._refresh_explorer()

    def _update_title(self) -> None:
        p = self.current_page()
        s = self.session
        cyc = f" - Cycle: {s.cycle + 1}/{s.n_cycles}" if s.n_cycles else ""
        page = f" - [{p.name}{cyc}]" if p else ""
        name = self.layout_name + (SUFFIX if not self.layout_name.endswith(SUFFIX) else "")
        self.setWindowTitle(f"{APP_NAME} - <{self.workspace_name}> - {name}"
                            f"{' *' if self.modified else ''}{page}")

    def _log(self, text: str) -> None:
        self.messages.appendPlainText(text)
        self.statusBar().showMessage(text.strip(), 8000)
        if text.startswith("Error"):
            self.dock_msg.show()
            self.dock_msg.raise_()

    def _busy(self, on: bool) -> None:
        self.progress.setVisible(on)
        if on:
            self.progress.setValue(0)
        for a in (self.a_open, self.a_recalc, self.a_open_opt):
            a.setEnabled(not on)
        if not on:
            self.statusBar().showMessage("Ready", 3000)

    def _progress(self, frac: float, text: str) -> None:
        self.progress.setValue(int(frac * 100))
        self.statusBar().showMessage(text)

    def _files_changed(self) -> None:
        n = max(1, self.session.n_cycles)
        for w in (self.cycle_spin, self.cycle_slider):
            w.blockSignals(True)
            w.setRange(1, n)
            w.setValue(min(n, self.session.cycle + 1))
            w.blockSignals(False)
        self.cycle_label.setText(f"Cycle / {self.session.n_cycles}")
        self._update_title()

    def _cycle_changed(self, i: int) -> None:
        for w in (self.cycle_spin, self.cycle_slider):
            w.blockSignals(True)
            w.setValue(i + 1)
            w.blockSignals(False)
        self._update_title()

    def _mode_changed(self) -> None:
        m = self.session.mode
        self.a_modes[m].setChecked(True)
        self.mode_combo.setCurrentIndex(MODES.index(m))

    def ask_range(self) -> None:
        a, b = self.session.range
        n = max(1, self.session.n_cycles)
        lo, ok = QInputDialog.getInt(self, "Cycle Range", "First cycle:", a, 1, n)
        if not ok:
            return
        hi, ok = QInputDialog.getInt(self, "Cycle Range", "Last cycle:", max(lo, b), lo, n)
        if ok:
            self.session.set_mode("range", (lo, hi))

    def toggle_play(self, on=None) -> None:
        on = self.a_play.isChecked() if on is None else bool(on)
        self.a_play.setChecked(on)
        if on and self.session.n_cycles > 1:
            self.play_timer.start()
        else:
            self.play_timer.stop()
            self.a_play.setChecked(False)

    def _play_step(self) -> None:
        n = self.session.n_cycles
        if n < 2:
            self.toggle_play(False)
            return
        self.session.set_cycle((self.session.cycle + 1) % n)

    def _zoom_changed(self, v: int) -> None:
        w = self.tabs.currentWidget()
        if isinstance(w, PageView):
            w.set_zoom(v / 100.0)

    def set_cursor_link(self, on: bool) -> None:
        for p in self.pages:
            p.link_cursors = bool(on)

    # ============================================================== pages
    def add_page(self, name: str | None = None, data: dict | None = None) -> Page:
        name = name or (data or {}).get("name") or f"Page {len(self.pages) + 1}"
        page = Page(self.session, name)
        page.link_cursors = self.a_link.isChecked()
        page.design = self.design
        page.modified.connect(self.set_modified)
        page.selectionChanged.connect(self._refresh_explorer)
        page.propertiesRequested.connect(lambda fr: self.object_properties(fr))
        page.contextRequested.connect(self._frame_menu)
        self.pages.append(page)
        view = PageView(page)
        self.tabs.addTab(view, name)
        if data:
            view.resize(self.tabs.size())
            page.load(data)
        self.tabs.setCurrentIndex(len(self.pages) - 1)
        self._sync_page_combo()
        self.set_modified()
        return page

    def delete_page(self, index: int | None = None) -> None:
        index = self.tabs.currentIndex() if index in (None, False) else index
        if not (0 <= index < len(self.pages)) or len(self.pages) <= 1:
            return
        page = self.pages[index]
        if self.confirm and page.frames and QMessageBox.question(
                self, "Delete Page", f"Delete page '{page.name}' with all its objects?"
        ) != QMessageBox.Yes:
            return
        del self.pages[index]
        w = self.tabs.widget(index)
        self.tabs.removeTab(index)
        w.deleteLater()
        self._sync_page_combo()
        self.set_modified()

    def rename_page(self, index: int | None = None, name: str | None = None) -> None:
        index = self.tabs.currentIndex() if index in (None, False) or index < 0 else index
        if not (0 <= index < len(self.pages)):
            return
        if name is None:
            name, ok = QInputDialog.getText(self, "Rename Page", "Page name:",
                                            text=self.pages[index].name)
            if not ok or not name.strip():
                return
        self.pages[index].name = name.strip()
        self.tabs.setTabText(index, name.strip())
        self._sync_page_combo()
        self.set_modified()

    def _sync_page_combo(self) -> None:
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        self.page_combo.addItems([p.name for p in self.pages])
        self.page_combo.setCurrentIndex(self.tabs.currentIndex())
        self.page_combo.blockSignals(False)
        self._refresh_explorer()

    def _page_changed(self, index: int) -> None:
        self.page_combo.blockSignals(True)
        self.page_combo.setCurrentIndex(index)
        self.page_combo.blockSignals(False)
        w = self.tabs.currentWidget()
        if isinstance(w, PageView):
            w.set_zoom(self.zoom.value() / 100.0)
        self._update_title()
        self._refresh_explorer()

    def _step_page(self, d: int) -> None:
        n = len(self.pages)
        if n:
            self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % n)

    def _refresh_explorer(self) -> None:
        if hasattr(self, "explorer") and not self._loading:
            self.explorer.rebuild(self.pages, self.tabs.currentIndex())

    def _explorer_select(self, pi: int, oi: int) -> None:
        if 0 <= pi < len(self.pages):
            self.tabs.setCurrentIndex(pi)
            if 0 <= oi < len(self.pages[pi].frames):
                self.pages[pi].select(self.pages[pi].frames[oi])

    def set_design(self, on: bool) -> None:
        self.design = bool(on)
        self.a_design.setChecked(self.design)
        for p in self.pages:
            p.set_design(self.design)
        self.statusBar().showMessage(
            "Design mode: drag objects to move, drag the border to resize"
            if self.design else "Analysis mode", 5000)

    # ============================================================ objects
    def insert_object(self, spec: dict) -> None:
        page = self.current_page() or self.add_page()
        spec = copy.deepcopy(spec)
        spec.pop("rect", None)
        frame = page.add_object(spec, select=True)
        self.set_modified()
        return frame

    def _run_dialog(self, dlg) -> bool:
        """Modal dialog that is deleted afterwards (no hidden dialogs pile up)."""
        try:
            return dlg.exec() == QDialog.Accepted
        finally:
            dlg.deleteLater()

    def object_wizard(self) -> None:
        dlg = ObjectWizard(self)
        if self._run_dialog(dlg):
            self.insert_object({"type": dlg.object_type()})

    def _selected_frame(self):
        p = self.current_page()
        return p.selected if p else None

    def delete_object(self) -> None:
        p, fr = self.current_page(), self._selected_frame()
        if p is not None and fr is not None:
            p.remove(fr)

    def duplicate_object(self) -> None:
        p, fr = self.current_page(), self._selected_frame()
        if p is None or fr is None:
            return
        spec = fr.to_spec()
        x, y, w, h = spec["rect"]
        spec["rect"] = [min(x + 0.03, 1 - w), min(y + 0.03, 1 - h), w, h]
        p.add_object(spec, select=True)
        self.set_modified()

    def copy_object(self) -> None:
        fr = self._selected_frame()
        obj = fr.obj if fr else self.active_diagram()
        if obj is None:
            return
        if hasattr(obj, "copy_image"):
            obj.copy_image()
        elif hasattr(obj, "copy"):
            obj.copy()
        else:
            QGuiApplication.clipboard().setPixmap(obj.grab())

    def _frame_menu(self, frame, gpos) -> None:
        from PySide6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction(self.a_props)
        m.addActions([self.a_dup, self.a_delete])
        m.exec(gpos)

    def object_properties(self, frame=None) -> None:
        fr = frame if frame not in (None, False) else self._selected_frame()
        if fr is None:
            d = self.active_diagram()
            if d is None:
                return
            obj = d
        else:
            obj = fr.obj
        kind = obj.type_name
        s = self.session
        if kind == "diagram":
            self._run_dialog(DiagramDialog(obj, s, self))
        elif kind == "table":
            e = s.entry
            names = [c.name for c in e.channels("CY")] if e else obj.column_names()
            dlg = ChecklistDialog("Result Table - Columns", names, obj.column_names(),
                                  "Select the cycle results to show.", self)
            if self._run_dialog(dlg):
                obj.set_spec({"columns": dlg.selected()})
        elif kind == "stats":
            names = result_names(s) + ["KNOCK_FREQ"]
            dlg = ChecklistDialog("Statistics Table - Rows", names,
                                  obj.spec.get("names") or DEFAULT_STATS, parent=self)
            if self._run_dialog(dlg):
                obj.set_spec({"names": dlg.selected()})
        elif kind == "params":
            e = s.entry
            names = [r[0] for r in e.result.parameters()] if e else DEFAULT_PARAMS
            dlg = ChecklistDialog("Parameter Table - Rows", names,
                                  obj.spec.get("names") or DEFAULT_PARAMS, parent=self)
            if self._run_dialog(dlg):
                obj.set_spec({"names": dlg.selected()})
        elif kind == "text":
            dlg = TextDialog(obj.spec, self)
            if self._run_dialog(dlg):
                obj.set_spec(dlg.result_spec())
        elif kind == "image":
            path, _ = QFileDialog.getOpenFileName(self, "Image", "",
                                                  "Images (*.png *.jpg *.jpeg *.bmp *.svg)")
            if path:
                obj.set_spec({"path": path})

    def add_channel(self, ds: str, name: str) -> None:
        """Double click in the Channel Browser: add to the active diagram or a new one."""
        d = self.active_diagram()
        if d is not None and d.x_kind() in (None, ds) and d.add_curve(ds, name):
            return
        self.new_diagram_with([{"ds": ds, "name": name}])

    def new_diagram_with(self, refs: list) -> None:
        ds = refs[0]["ds"]
        curves = [default_curve(r["ds"], r["name"]) for r in refs if r["ds"] == ds]
        self.insert_object({"type": "diagram", "curves": curves})

    # ============================================================ layouts
    def layout_dict(self) -> dict:
        return {"version": LAYOUT_VERSION, "name": self.layout_name,
                "pages": [p.to_dict() for p in self.pages],
                "formulas": [f.to_dict() for f in self.session.formulas]}

    def _discard_ok(self) -> bool:
        if not (self.modified and self.confirm):
            return True
        r = QMessageBox.question(
            self, APP_NAME, f"The layout '{self.layout_name}' has unsaved changes.\nSave them?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save_layout()
        return r == QMessageBox.Discard

    def _clear_pages(self) -> None:
        self.tabs.blockSignals(True)
        while self.tabs.count():
            w = self.tabs.widget(0)
            self.tabs.removeTab(0)
            w.deleteLater()
        self.pages = []
        self.tabs.blockSignals(False)

    def set_layout(self, data: dict, path: Path | None = None) -> None:
        self._loading = True
        try:
            self._clear_pages()
            self.session.set_formulas([Formula(f["name"], f["expr"], f.get("unit", ""),
                                               f.get("desc", ""))
                                       for f in data.get("formulas", [])])
            for pd_ in data.get("pages", []) or [{"name": "Page 1", "objects": []}]:
                self.add_page(data=pd_)
            self.tabs.setCurrentIndex(0)
        finally:
            self._loading = False
        self.layout_path = Path(path) if path else None
        self.layout_name = Path(path).stem if path else data.get("name", "Untitled")
        self.set_design(self.design)
        self.modified = False
        self._sync_page_combo()
        self._update_title()

    def new_layout(self) -> None:
        if not self._discard_ok():
            return
        self.set_layout({"name": "Untitled", "pages": [
            {"name": "Page 1", "objects": [{"type": "diagram", "rect": [0.0, 0.0, 1.0, 1.0]}]}]})

    def open_layout(self, path: str | None = None) -> bool:
        if not self._discard_ok():
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Layout", str(self._last_dir()),
                                                  f"Layouts (*{SUFFIX})")
            if not path:
                return False
        try:
            data = read_layout(path)
        except (OSError, ValueError) as exc:
            self._log(f"Error: cannot read the layout {path}: {exc}")
            if self.confirm:
                QMessageBox.warning(self, "Open Layout", f"Cannot read the layout:\n{exc}")
            return False
        self.set_layout(data, Path(path))
        QSettings().setValue("last_layout", str(path))
        self._log(f"Layout {Path(path).name} opened")
        return True

    def save_layout(self, path: str | None = None, ask: bool = False) -> bool:
        if path is None and (ask or self.layout_path is None
                             or self._is_builtin(self.layout_path)):
            folder = user_dir() / "layouts"
            folder.mkdir(parents=True, exist_ok=True)
            path, _ = QFileDialog.getSaveFileName(
                self, "Save Layout", str(folder / f"{self.layout_name}{SUFFIX}"),
                f"Layouts (*{SUFFIX})")
            if not path:
                return False
        path = Path(path or self.layout_path)
        if path.suffix != SUFFIX:
            path = path.with_suffix(SUFFIX)
        self.layout_name = path.stem
        try:
            write_layout(self.layout_dict(), path)
        except OSError as exc:
            self._log(f"Error: cannot write the layout: {exc}")
            return False
        self.layout_path = path
        self.modified = False
        QSettings().setValue("last_layout", str(path))
        self._update_title()
        self.refresh_layout_list()
        self._log(f"Layout saved: {path}")
        return True

    @staticmethod
    def _is_builtin(path: Path) -> bool:
        from .layouts import BUILTIN_DIR
        try:
            return Path(path).resolve().parent == BUILTIN_DIR.resolve()
        except OSError:
            return False

    def refresh_layout_list(self) -> None:
        self.workflow.set_layouts(available_layouts())

    def load_default_layout(self) -> None:
        last = QSettings().value("last_layout", "")
        self.confirm, keep = False, self.confirm
        try:
            if last and Path(last).is_file() and self.open_layout(last):
                return
            std = ensure_builtin()
            if not (std.is_file() and self.open_layout(str(std))):
                from .layouts import standard_layout
                self.set_layout(standard_layout())
        finally:
            self.confirm = keep

    # ======================================================= measurements
    def _last_dir(self) -> Path:
        return Path(QSettings().value("last_dir", str(Path.home())))

    def open_measurement(self, paths=None, ask: bool = False) -> None:
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(
                self, "Open Measurement", str(self._last_dir()),
                "Measurements (*.csv *.txt *.dat);;All files (*)")
            if not paths:
                return
        paths = [str(p) for p in paths]
        QSettings().setValue("last_dir", str(Path(paths[0]).parent))
        for p in paths:
            fmt = sniff_or_ask(p, self, always_ask=ask) if self.confirm or ask else None
            if fmt is None and (self.confirm or ask):
                continue
            try:
                self.session.load_files([p], fmt)
            except ValueError:
                continue
            self._add_recent(p)

    def _add_recent(self, path: str) -> None:
        st = QSettings()
        rec = [r for r in (st.value("recent", []) or []) if r != path]
        rec.insert(0, path)
        st.setValue("recent", rec[:10])
        self._fill_recent()

    def _fill_recent(self) -> None:
        self.recent_menu.clear()
        rec = QSettings().value("recent", []) or []
        if isinstance(rec, str):
            rec = [rec]
        for r in rec:
            self.recent_menu.addAction(r, lambda r=r: self.open_measurement([r]))
        self.recent_menu.setEnabled(bool(rec))

    def data_explorer(self) -> None:
        dlg = DataExplorer(self.session, self)
        dlg.openRequested.connect(lambda: self.open_measurement())
        self._run_dialog(dlg)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev):
        paths = [u.toLocalFile() for u in ev.mimeData().urls() if u.isLocalFile()]
        layouts = [p for p in paths if p.endswith(SUFFIX)]
        data = [p for p in paths if not p.endswith(SUFFIX)]
        if layouts:
            self.open_layout(layouts[0])
        if data:
            self.open_measurement(data)

    # =========================================================== settings
    def settings_dialog(self, tab: str = SETTINGS_TABS[0]) -> None:
        dlg = SettingsDialog(self.session, tab, self)
        dlg.applied.connect(self.session.set_config)
        self._run_dialog(dlg)

    def load_settings(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Load Settings", str(self._last_dir()),
                                                  "Settings (*.toml)")
            if not path:
                return
        try:
            self.session.set_config(load_config(path))
            self._log(f"Settings loaded: {path}")
        except (OSError, ValueError) as exc:
            self._log(f"Error: cannot read the settings {path}: {exc}")

    def save_settings(self, path: str | None = None) -> None:
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save Settings", "engine.toml",
                                                  "Settings (*.toml)")
            if not path:
                return
        save_config(self.session.cfg, path)
        self._log(f"Settings saved: {path}")

    # =========================================================== formulas
    def calculator(self) -> None:
        dlg = CalculatorDialog(self.session, parent=self)
        if self._run_dialog(dlg) and dlg.formula is not None:
            fs = [f for f in self.session.formulas if f.name != dlg.formula.name]
            self.set_formulas(fs + [dlg.formula])

    def formula_editor(self) -> None:
        dlg = FormulaEditor(self.session, self)
        if self._run_dialog(dlg) and dlg.formulas is not None:
            self.set_formulas(dlg.formulas)

    def set_formulas(self, formulas: list) -> None:
        for err in self.session.set_formulas(formulas):
            self._log(f"Formula {err}")
        self._log(f"{len(formulas)} formula channel(s) defined")
        self.set_modified()

    # ============================================================= export
    def _ask_save(self, title: str, default: str, filt: str) -> str:
        path, _ = QFileDialog.getSaveFileName(self, title, str(self._last_dir() / default), filt)
        return path

    def _stem(self) -> str:
        e = self.session.entry
        return e.meas.path.stem if e else "layout"

    def export_png(self, path: str | None = None) -> bool:
        p = self.current_page()
        if p is None:
            return False
        path = path or self._ask_save("Page as PNG", f"{self._stem()}_{p.name}.png",
                                      "PNG (*.png)")
        if not path:
            return False
        ok = p.grab().save(path)
        self._log(f"Page saved: {path}" if ok else f"Error: cannot write {path}")
        return ok

    def export_pdf(self, path: str | None = None, all_pages: bool = False) -> bool:
        pages = self.pages if all_pages else [self.current_page()]
        pages = [p for p in pages if p is not None]
        if not pages:
            return False
        path = path or self._ask_save("Export PDF", f"{self._stem()}.pdf", "PDF (*.pdf)")
        if not path:
            return False
        writer = QPdfWriter(path)
        writer.setPageLayout(QPageLayout(QPageSize(QPageSize.A4), QPageLayout.Landscape,
                                         writer.pageLayout().margins()))
        writer.setResolution(150)
        writer.setTitle(self.layout_name)
        painter = QPainter(writer)
        size = self.current_page().size()
        design = self.design
        if design:
            self.set_design(False)
        for i, p in enumerate(pages):
            if i:
                writer.newPage()
            if p.size() != size:
                p.resize(size)
                QApplication.processEvents()
            pm = p.grab()
            target = painter.viewport()
            scaled = pm.size().scaled(target.size(), Qt.KeepAspectRatio)
            painter.drawPixmap(0, 0, scaled.width(), scaled.height(), pm)
        painter.end()
        if design:
            self.set_design(True)
        self._log(f"PDF saved: {path} ({len(pages)} page(s))")
        return True

    def export_curve_data(self, path: str | None = None) -> bool:
        fr = self._selected_frame()
        obj = fr.obj if fr is not None and hasattr(fr.obj, "export_table") else self.active_diagram()
        if obj is None:
            self._log("No diagram or table to export")
            return False
        header, cols = obj.export_table()
        if not cols:
            self._log("The object contains no data")
            return False
        path = path or self._ask_save("Curve Data as CSV", f"{self._stem()}_data.csv",
                                      "CSV (*.csv)")
        if not path:
            return False
        n = max(len(c) for c in cols)
        rows = [[(float(c[i]) if i < len(c) else np.nan) for c in cols] for i in range(n)]
        out = self.session.cfg.output
        _write_table(Path(path), header, rows, out.csv_delimiter, out.csv_decimal)
        self._log(f"Data saved: {path}")
        return True

    def export_results(self, folder: str | None = None) -> bool:
        e = self.session.entry
        if e is None:
            self._log("No measurement loaded")
            return False
        if not folder:
            folder = QFileDialog.getExistingDirectory(self, "Results and Report",
                                                      str(self._last_dir()))
            if not folder:
                return False
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            written = write_outputs(e.result, folder)
        finally:
            QApplication.restoreOverrideCursor()
        self._log(f"{len(written)} files written to {folder}")
        return True

    # ========================================================== workspace
    def workspace_dict(self) -> dict:
        s = self.session
        return {"version": 1, "files": [str(e.meas.path) for e in s.files], "active": s.active,
                "cycle": s.cycle, "mode": s.mode, "range": list(s.range),
                "config": s.cfg.to_dict(), "layout": self.layout_dict(),
                "layout_path": str(self.layout_path) if self.layout_path else None}

    def save_workspace(self, path: str | None = None) -> bool:
        path = path or self._ask_save("Save Workspace", f"{self.workspace_name}{WORK_SUFFIX}",
                                      f"Workspaces (*{WORK_SUFFIX})")
        if not path:
            return False
        Path(path).write_text(json.dumps(self.workspace_dict(), indent=1), encoding="utf-8")
        self.workspace_name = Path(path).stem
        self._update_title()
        self._log(f"Workspace saved: {path}")
        return True

    def open_workspace(self, path: str | None = None) -> bool:
        if not self._discard_ok():
            return False
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open Workspace", str(self._last_dir()),
                                                  f"Workspaces (*{WORK_SUFFIX})")
            if not path:
                return False
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            from ..core.config import Config
            cfg = Config.from_dict(data.get("config", {}))
        except (OSError, ValueError) as exc:
            self._log(f"Error: cannot read the workspace {path}: {exc}")
            return False
        s = self.session
        for i in reversed(range(len(s.files))):
            s.remove_file(i)
        s.cfg = cfg
        self.set_layout(data.get("layout", {}), data.get("layout_path"))
        s.mode = data.get("mode", "single")
        s.range = tuple(data.get("range", (1, 10)))
        self._mode_changed()
        missing = [f for f in data.get("files", []) if not Path(f).is_file()]
        for f in missing:
            self._log(f"Workspace: measurement not found: {f}")
        files = [f for f in data.get("files", []) if f not in missing]
        if files:
            keep, self.confirm = self.confirm, False
            try:
                self.open_measurement(files)
            finally:
                self.confirm = keep
            self._pending = (data.get("active", 0), data.get("cycle", 0))
            if not s.busy:
                self._restore_pending()
            else:
                s.busyChanged.connect(self._restore_pending_once)
        self.workspace_name = Path(path).stem
        self._update_title()
        self._log(f"Workspace opened: {path}")
        return True

    def _restore_pending_once(self, busy: bool) -> None:
        if not busy:
            self.session.busyChanged.disconnect(self._restore_pending_once)
            self._restore_pending()

    def _restore_pending(self) -> None:
        active, cycle = getattr(self, "_pending", (0, 0))
        self.session.set_active(active)
        self.session.set_cycle(cycle)

    # =============================================================== misc
    def about(self) -> None:
        QMessageBox.about(
            self, f"About {APP_NAME}",
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "Combustion indicating analysis for two cylinders (High / Low):<br>"
            "cylinder pressure, heat release, knock and PI current.<br><br>"
            "Results follow common definitions; they are not guaranteed to be numerically "
            "identical to other tools.<br><br>"
            "Copyright (C) 2026 System-M. Free software under the GNU General Public License, "
            "version 3, without any warranty.")

    def closeEvent(self, ev):
        if not self._discard_ok():
            ev.ignore()
            return
        self.play_timer.stop()
        st = QSettings()
        st.setValue("geometry", self.saveGeometry())
        st.setValue("state", self.saveState())
        ev.accept()

    def restore_window(self) -> None:
        st = QSettings()
        g, s = st.value("geometry"), st.value("state")
        if g is not None:
            self.restoreGeometry(g)
        if s is not None:
            self.restoreState(s)
