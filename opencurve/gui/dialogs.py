"""Dialogs: settings, diagram properties, data explorer, import, calculator, formula editor."""

from __future__ import annotations

import copy
from dataclasses import fields
from pathlib import Path

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from ..core.config import CYL_NAMES, CYLINDERS, Config, load_config, save_config
from ..core.datamodel import Formula, FormulaError, formula_names
from ..core.io import CANONICAL, CsvFormat, CsvFormatError, sniff_csv
from .diagram import CURVE_MODES, STYLES, default_spec
from .page import OBJECT_TYPES
from .session import Session

SETTINGS_TABS = ("Engine Parameters", "Channel Scaling", "Evaluation", "PI Detection", "Knock")


def _float(text: str, label: str) -> float:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        raise ValueError(f"'{label}': '{text}' is not a number") from None


# ----------------------------------------------------------- generic fields
class _Field:
    """Editor widget for one dataclass field, chosen by the type of its value."""

    def __init__(self, obj, f):
        self.obj, self.f = obj, f
        md = f.metadata
        self.label = md.get("label", f.name)
        self.unit = md.get("unit", "")
        v = getattr(obj, f.name)
        ch = md.get("choices")
        self.kind = None
        if ch:
            self.kind = "choice"
            self.choices = ch
            self.w = QComboBox()
            self.w.addItems([str(c) for c in ch])
            self.w.setCurrentIndex(ch.index(v) if v in ch else 0)
        elif isinstance(v, bool):
            self.kind = "bool"
            self.w = QCheckBox()
            self.w.setChecked(v)
        elif isinstance(v, int):
            self.kind = "int"
            self.w = QSpinBox()
            self.w.setRange(-1000000, 1000000)
            self.w.setValue(v)
        elif isinstance(v, list):
            self.kind = "list"
            self.w = QWidget()
            lay = QHBoxLayout(self.w)
            lay.setContentsMargins(0, 0, 0, 0)
            self.edits = [QLineEdit(f"{x:g}") for x in v]
            for e in self.edits:
                lay.addWidget(e)
        elif isinstance(v, float):
            self.kind = "float"
            self.w = QLineEdit(f"{v:g}")
        else:                                   # str or "auto"/number
            self.kind = "text"
            self.w = QLineEdit(str(v))
        tip = md.get("help")
        if tip:
            self.w.setToolTip(tip)

    def value(self):
        if self.kind == "choice":
            return self.choices[self.w.currentIndex()]
        if self.kind == "bool":
            return self.w.isChecked()
        if self.kind == "int":
            return self.w.value()
        if self.kind == "list":
            return [_float(e.text(), self.label) for e in self.edits]
        if self.kind == "float":
            return _float(self.w.text(), self.label)
        text = self.w.text().strip()
        cur = getattr(self.obj, self.f.name)
        if self.f.name == "baseline":          # "auto" or a number
            return "auto" if text.lower() in ("auto", "") else _float(text, self.label)
        return text if isinstance(cur, str) or text else cur

    def store(self):
        setattr(self.obj, self.f.name, self.value())


def _form(obj, title: str | None, registry: list, only=None, skip=()) -> QWidget:
    box = QGroupBox(title) if title else QWidget()
    form = QFormLayout(box)
    form.setLabelAlignment(Qt.AlignLeft)
    for f in fields(obj):
        if (only and f.name not in only) or f.name in skip:
            continue
        fld = _Field(obj, f)
        registry.append(fld)
        label = fld.label + (f" [{fld.unit}]" if fld.unit else "")
        form.addRow(label, fld.w)
    return box


class SettingsDialog(QDialog):
    """All evaluation settings; 'Apply' triggers a recalculation."""

    applied = Signal(object)

    def __init__(self, session: Session, tab: str = SETTINGS_TABS[0], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.session = session
        self.resize(560, 620)
        self.lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.lay.addWidget(self.tabs)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self.load_btn = bb.addButton("Load...", QDialogButtonBox.ActionRole)
        self.save_btn = bb.addButton("Save...", QDialogButtonBox.ActionRole)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Apply).clicked.connect(self.apply)
        self.load_btn.clicked.connect(self._load)
        self.save_btn.clicked.connect(self._save)
        self.lay.addWidget(bb)
        self._build(session.cfg.copy(), tab)

    def _build(self, cfg: Config, tab: str) -> None:
        self.cfg = cfg
        self.fields: list[_Field] = []
        self.tabs.clear()

        def page(*widgets):
            w = QWidget()
            lay = QVBoxLayout(w)
            for x in widgets:
                lay.addWidget(x)
            lay.addStretch(1)
            return w

        note = QLabel("Engine data of the Jenbacher Type 6 are a default. Bore, stroke and "
                      "speed are published; conrod length and compression ratio are "
                      "assumptions. Clear 'Engine data are assumptions' once the values "
                      "are confirmed.")
        note.setWordWrap(True)
        note.setStyleSheet("color:#505050;")
        tdc = QGroupBox("TDC offset")
        fl = QFormLayout(tdc)
        for c in CYLINDERS:
            f = next(x for x in fields(cfg.cyl[c]) if x.name == "ca_offset_deg")
            fld = _Field(cfg.cyl[c], f)
            self.fields.append(fld)
            fl.addRow(f"{CYL_NAMES[c]} [deg]", fld.w)
        self.tabs.addTab(page(_form(cfg.engine, "Engine", self.fields), tdc, note),
                         SETTINGS_TABS[0])

        boxes = []
        for c in CYLINDERS:
            boxes.append(_form(cfg.cyl[c], f"Cylinder {CYL_NAMES[c]}", self.fields,
                               skip=("ca_offset_deg",)))
        self.preview = QLabel()
        self.preview.setStyleSheet("color:#505050;")
        self.preview.setWordWrap(True)
        self.tabs.addTab(page(*boxes, self.preview), SETTINGS_TABS[1])
        for fld in self.fields:
            if fld.f.name in ("dmr_sens_mv_per_bar", "dmr_offset_bar") and fld.kind == "float":
                fld.w.textChanged.connect(self._update_preview)
        self._update_preview()

        self.tabs.addTab(page(_form(cfg.ca, "Crank angle", self.fields),
                              _form(cfg.pegging, "Zero level correction", self.fields),
                              _form(cfg.filter, "Filter", self.fields),
                              _form(cfg.heat_release, "Heat release", self.fields)),
                         SETTINGS_TABS[2])
        self.tabs.addTab(page(_form(cfg.pi, None, self.fields)), SETTINGS_TABS[3])
        self.tabs.addTab(page(_form(cfg.knock, None, self.fields)), SETTINGS_TABS[4])
        if tab in SETTINGS_TABS:
            self.tabs.setCurrentIndex(SETTINGS_TABS.index(tab))

    def _update_preview(self) -> None:
        e = self.session.entry
        if e is None:
            self.preview.setText("Load a measurement to see the converted pressure range.")
            return
        lines = []
        for c in CYLINDERS:
            v = e.meas.raw.get(f"DMR_{c}")
            if v is None:
                continue
            try:
                sens = next(_float(f.w.text(), "") for f in self.fields
                            if f.obj is self.cfg.cyl[c] and f.f.name == "dmr_sens_mv_per_bar")
                offs = next(_float(f.w.text(), "") for f in self.fields
                            if f.obj is self.cfg.cyl[c] and f.f.name == "dmr_offset_bar")
                if sens <= 0:
                    raise ValueError
            except (ValueError, StopIteration):
                lines.append(f"{CYL_NAMES[c]}: invalid sensitivity")
                continue
            lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
            lines.append(f"{CYL_NAMES[c]}: {lo:.4f} ... {hi:.4f} V  ->  "
                         f"{lo * 1000 / sens + offs:.1f} ... {hi * 1000 / sens + offs:.1f} bar "
                         "(before zero level correction)")
        self.preview.setText("\n".join(lines))

    def collect(self) -> Config | None:
        try:
            for f in self.fields:
                f.store()
            cfg = self.cfg
            for c in CYLINDERS:
                if cfg.cyl[c].dmr_sens_mv_per_bar <= 0:
                    raise ValueError("pressure sensitivity must be greater than 0")
            if cfg.knock.band_hz[0] >= cfg.knock.band_hz[1]:
                raise ValueError("knock band: the lower limit must be below the upper limit")
            if cfg.heat_release.start_deg >= cfg.heat_release.end_deg:
                raise ValueError("heat release: start must be before end")
        except ValueError as exc:
            QMessageBox.warning(self, "Settings", str(exc))
            return None
        return cfg.copy()

    def apply(self) -> bool:
        cfg = self.collect()
        if cfg is None:
            return False
        self.applied.emit(cfg)
        return True

    def _ok(self) -> None:
        if self.apply():
            self.accept()

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load Settings", "", "Settings (*.toml)")
        if not path:
            return
        try:
            cfg = load_config(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Load Settings", f"Cannot read the settings:\n{exc}")
            return
        self._build(cfg, self.tabs.tabText(self.tabs.currentIndex()))

    def _save(self) -> None:
        cfg = self.collect()
        if cfg is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save Settings", "engine.toml",
                                              "Settings (*.toml)")
        if path:
            save_config(cfg, path)


# ------------------------------------------------------ diagram properties
class _ColorButton(QPushButton):
    def __init__(self, color: str):
        super().__init__()
        self.setFixedWidth(44)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str) -> None:
        self.color = color
        self.setStyleSheet(f"background:{color};border:1px solid #606060;")

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self, "Curve Colour")
        if c.isValid():
            self.set_color(c.name())


class DiagramDialog(QDialog):
    def __init__(self, diagram, session: Session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Diagram Properties")
        self.resize(820, 460)
        self.diagram = diagram
        self.session = session
        self.spec = diagram.to_spec()
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        # curves
        self.table = QTableWidget()
        heads = ["Channel", "File", "Strip", "Axis", "Colour", "Width", "Style", "Display",
                 "Visible", "X channel"]
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        cw = QWidget()
        cl = QVBoxLayout(cw)
        cl.addWidget(self.table)
        row = QHBoxLayout()
        rm = QPushButton("Remove Curve")
        rm.clicked.connect(self._remove)
        row.addWidget(rm)
        row.addStretch(1)
        row.addWidget(QLabel("Add curves by dragging channels from the Channel Browser."))
        cl.addLayout(row)
        tabs.addTab(cw, "Curves")
        self.rows: list = []
        for c in self.spec["curves"]:
            self._add_row(c)

        # axes
        aw = QWidget()
        al = QVBoxLayout(aw)
        xg = QGroupBox("X axis")
        xl = QHBoxLayout(xg)
        x = self.spec.get("x", {})
        self.x_auto = QCheckBox("Auto")
        self.x_auto.setChecked(bool(x.get("auto", True)))
        self.x_min = QLineEdit(f"{x.get('min', 0.0):g}")
        self.x_max = QLineEdit(f"{x.get('max', 1.0):g}")
        self.x_log = QCheckBox("Logarithmic")
        self.x_log.setChecked(bool(self.spec.get("logx")))
        for w in (self.x_auto, QLabel("Min"), self.x_min, QLabel("Max"), self.x_max, self.x_log):
            xl.addWidget(w)
        al.addWidget(xg)
        self.axes_table = QTableWidget()
        self.axes_table.setColumnCount(5)
        self.axes_table.setHorizontalHeaderLabels(["Axis", "Auto", "Min", "Max", "Logarithmic"])
        self.axes_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        al.addWidget(self.axes_table)
        self.axis_rows: list = []
        keys = sorted({(c["strip"], int(c.get("axis", 0))) for c in self.spec["curves"]})
        self.axes_table.setRowCount(len(keys))
        for r, (si, ai) in enumerate(keys):
            a = self.spec.get("axes", {}).get(f"{si}:{ai}", {})
            names = ", ".join(c["name"] for c in self.spec["curves"]
                              if c["strip"] == si and int(c.get("axis", 0)) == ai)
            it = QTableWidgetItem(f"Strip {si + 1} / Axis {ai + 1}: {names}")
            it.setFlags(Qt.ItemIsEnabled)
            self.axes_table.setItem(r, 0, it)
            auto = QCheckBox()
            auto.setChecked(bool(a.get("auto", True)))
            mn, mx = QLineEdit(f"{a.get('min', 0.0):g}"), QLineEdit(f"{a.get('max', 1.0):g}")
            lg = QCheckBox()
            lg.setChecked(bool(a.get("log")))
            lg.setEnabled(ai == 0)
            for cidx, w in enumerate((auto, mn, mx, lg), 1):
                self.axes_table.setCellWidget(r, cidx, w)
            self.axis_rows.append(((si, ai), auto, mn, mx, lg))
        tabs.addTab(aw, "Axes")

        # general
        gw = QWidget()
        gl = QFormLayout(gw)
        self.title = QLineEdit(self.spec.get("title", ""))
        self.legend = QCheckBox()
        self.legend.setChecked(bool(self.spec.get("legend", True)))
        self.grid = QCheckBox()
        self.grid.setChecked(bool(self.spec.get("grid", True)))
        self.cursor2 = QCheckBox()
        self.cursor2.setChecked(bool(self.spec.get("cursor2")))
        gl.addRow("Title", self.title)
        gl.addRow("Legend", self.legend)
        gl.addRow("Grid", self.grid)
        gl.addRow("Second cursor", self.cursor2)
        tabs.addTab(gw, "General")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add_row(self, c: dict) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        it = QTableWidgetItem(f"{c['ds']}: {c['name']}")
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        self.table.setItem(r, 0, it)
        file = QComboBox()
        file.addItem("active", "active")
        for i, e in enumerate(self.session.files):
            file.addItem(f"FILE{i + 1} ({e.name})", i + 1)
        idx = file.findData(c.get("file", "active"))
        file.setCurrentIndex(max(0, idx))
        strip = QSpinBox()
        strip.setRange(1, 8)
        strip.setValue(c["strip"] + 1)
        axis = QSpinBox()
        axis.setRange(1, 4)
        axis.setValue(int(c.get("axis", 0)) + 1)
        color = _ColorButton(c["color"] or "#2a78d6")
        width = QDoubleSpinBox()
        width.setRange(0.5, 6.0)
        width.setSingleStep(0.5)
        width.setValue(float(c.get("width", 1.5)))
        style = QComboBox()
        style.addItems(list(STYLES))
        style.setCurrentText(c.get("style", "solid"))
        mode = QComboBox()
        mode.addItems(list(CURVE_MODES))
        mode.setCurrentText(c.get("mode", "global"))
        mode.setEnabled(c["ds"] == "CA")
        vis = QCheckBox()
        vis.setChecked(bool(c.get("visible", True)))
        xname = QComboBox()
        xname.addItem("(axis)", None)
        e = self.session.entry
        if e is not None:
            for ch in e.channels(c["ds"]):
                xname.addItem(ch.name, ch.name)
        if c.get("xname") and xname.findData(c["xname"]) < 0:
            xname.addItem(c["xname"], c["xname"])
        xname.setCurrentIndex(max(0, xname.findData(c.get("xname"))))
        widgets = (file, strip, axis, color, width, style, mode, vis, xname)
        for col, w in enumerate(widgets, 1):
            self.table.setCellWidget(r, col, w)
        self.rows.append((c, widgets))

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            del self.rows[r]

    def _ok(self) -> None:
        try:
            curves = []
            for c, (file, strip, axis, color, width, style, mode, vis, xname) in self.rows:
                c = dict(c)
                c.update(file=file.currentData(), strip=strip.value() - 1, axis=axis.value() - 1,
                         color=color.color, width=width.value(), style=style.currentText(),
                         mode=mode.currentText(), visible=vis.isChecked(),
                         xname=xname.currentData())
                curves.append(c)
            used = sorted({c["strip"] for c in curves})
            remap = {s: i for i, s in enumerate(used)}
            for c in curves:
                c["strip"] = remap[c["strip"]]
            spec = self.spec
            spec["curves"] = curves
            spec["strips"] = max(1, len(used))
            spec["title"] = self.title.text().strip()
            spec["legend"] = self.legend.isChecked()
            spec["grid"] = self.grid.isChecked()
            spec["cursor2"] = self.cursor2.isChecked()
            spec["logx"] = self.x_log.isChecked()
            spec["x"] = {"auto": self.x_auto.isChecked(), "min": _float(self.x_min.text(), "X min"),
                         "max": _float(self.x_max.text(), "X max")}
            if not spec["x"]["auto"] and spec["x"]["min"] >= spec["x"]["max"]:
                raise ValueError("X axis: min must be smaller than max")
            axes = {}
            for (si, ai), auto, mn, mx, lg in self.axis_rows:
                if si not in remap:
                    continue
                a = {"auto": auto.isChecked(), "min": _float(mn.text(), "Y min"),
                     "max": _float(mx.text(), "Y max"), "log": lg.isChecked()}
                if not a["auto"] and a["min"] >= a["max"]:
                    raise ValueError(f"Strip {si + 1} / Axis {ai + 1}: min must be smaller than max")
                if not a["auto"] or a["log"]:
                    axes[f"{remap[si]}:{ai}"] = a
            spec["axes"] = axes
        except ValueError as exc:
            QMessageBox.warning(self, "Diagram Properties", str(exc))
            return
        self.diagram.set_spec(spec)
        self.accept()


# -------------------------------------------------- simple object dialogs
class ChecklistDialog(QDialog):
    def __init__(self, title: str, names: list, selected: list, hint: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(320, 480)
        lay = QVBoxLayout(self)
        if hint:
            lab = QLabel(hint)
            lab.setWordWrap(True)
            lay.addWidget(lab)
        self.list = QListWidget()
        for n in names:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if n in selected else Qt.Unchecked)
            self.list.addItem(it)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, state in (("All", Qt.Checked), ("None", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: [
                self.list.item(i).setCheckState(s) for i in range(self.list.count())])
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def selected(self) -> list:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class TextDialog(QDialog):
    def __init__(self, spec: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Text")
        self.resize(420, 320)
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit(spec.get("text", ""))
        lay.addWidget(self.edit)
        hint = QLabel("Placeholders: {FILENAME} {DATE} {CYCLE} {NCYCLES} {PAR:BORE} "
                      "{STAT:PMAX_H} {STAT:IMEP_L:cov}")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#505050;")
        lay.addWidget(hint)
        form = QFormLayout()
        self.size = QSpinBox()
        self.size.setRange(6, 72)
        self.size.setValue(int(spec.get("size", 11)))
        self.bold = QCheckBox()
        self.bold.setChecked(bool(spec.get("bold")))
        self.align = QComboBox()
        self.align.addItems(["left", "center", "right"])
        self.align.setCurrentText(spec.get("align", "left"))
        form.addRow("Size [pt]", self.size)
        form.addRow("Bold", self.bold)
        form.addRow("Alignment", self.align)
        lay.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def result_spec(self) -> dict:
        return {"text": self.edit.toPlainText(), "size": self.size.value(),
                "bold": self.bold.isChecked(), "align": self.align.currentText()}


class ObjectWizard(QDialog):
    """Choose the kind of object to insert."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Object Wizard")
        self.resize(460, 300)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Select the object to insert on the current page:"))
        self.list = QListWidget()
        for key, (label, desc, _cls) in OBJECT_TYPES.items():
            it = QListWidgetItem(f"{label}\n    {desc}")
            it.setData(Qt.UserRole, key)
            self.list.addItem(it)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _it: self.accept())
        lay.addWidget(self.list)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def object_type(self) -> str:
        return self.list.currentItem().data(Qt.UserRole)


# ----------------------------------------------------------- data explorer
class DataExplorer(QDialog):
    """Loaded measurements: open, remove, activate, inspect."""

    openRequested = Signal()

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Data Explorer")
        self.resize(760, 420)
        self.session = session
        lay = QVBoxLayout(self)
        self.table = QTableWidget()
        heads = ["File", "Name", "Samples", "Rate [Hz]", "Duration [s]", "Cycles High",
                 "Cycles Low", "Signals"]
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._details)
        lay.addWidget(self.table)
        self.info = QPlainTextEdit()
        self.info.setReadOnly(True)
        self.info.setMaximumHeight(150)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        for text, cb in (("Open...", self.openRequested.emit), ("Set Active", self._activate),
                         ("Remove", self._remove)):
            b = QPushButton(text)
            b.clicked.connect(cb)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        session.filesChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        s = self.session
        self.table.setRowCount(len(s.files))
        for r, e in enumerate(s.files):
            m = e.meas
            cyc = {c: (e.result.cyl[c].n_cycles if c in e.result.cyl else "-") for c in CYLINDERS}
            sig = ", ".join(k for k in m.raw if not k.startswith("CA_"))
            vals = [f"FILE{r + 1}" + (" *" if r == s.active else ""), m.name, str(m.n),
                    f"{m.fs:g}", f"{m.duration:.4f}", str(cyc["H"]), str(cyc["L"]), sig]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(v))
        if s.files:
            self.table.selectRow(max(0, s.active))
        self._details()

    def _details(self) -> None:
        r = self.table.currentRow()
        if not (0 <= r < len(self.session.files)):
            self.info.setPlainText("No measurement loaded.")
            return
        e = self.session.files[r]
        lines = [str(e.meas.path), f"Format: {e.meas.fmt.describe()}",
                 f"Columns: {', '.join(e.meas.fmt.mapping.values())}", "Messages:"]
        lines += [f"  - {w}" for w in e.result.warnings] or ["  none"]
        self.info.setPlainText("\n".join(lines))

    def _activate(self) -> None:
        self.session.set_active(self.table.currentRow())

    def _remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.session.remove_file(r)


# ------------------------------------------------------------------ import
class ImportDialog(QDialog):
    """Shows the detected file format and lets the user correct the column mapping."""

    def __init__(self, path: str, fmt: CsvFormat | None, error: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Import - {Path(path).name}")
        self.resize(720, 520)
        self.path = path
        lay = QVBoxLayout(self)
        if error:
            lab = QLabel(error)
            lab.setWordWrap(True)
            lab.setStyleSheet("color:#a02020;")
            lay.addWidget(lab)
        if fmt is None:
            fmt = CsvFormat()
            try:
                head = Path(path).read_text(encoding="latin-1", errors="replace").splitlines()[:8]
            except OSError:
                head = []
            fmt.preview = head
            if head:
                fmt.columns = [c.strip() for c in head[0].replace(";", ",").replace("\t", ",")
                               .split(",")]
        self.fmt = fmt
        prev = QPlainTextEdit("\n".join(fmt.preview))
        prev.setReadOnly(True)
        prev.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        prev.setFont(mono)
        prev.setMaximumHeight(150)
        lay.addWidget(QLabel("First lines of the file:"))
        lay.addWidget(prev)

        box = QGroupBox("Format")
        fl = QFormLayout(box)
        self.delim = QComboBox()
        for label, d in (("comma  ,", ","), ("semicolon  ;", ";"), ("tab", "\t")):
            self.delim.addItem(label, d)
        self.delim.setCurrentIndex(max(0, self.delim.findData(fmt.delimiter)))
        self.delim.currentIndexChanged.connect(self._resplit)
        self.dec = QComboBox()
        self.dec.addItems([".", ","])
        self.dec.setCurrentText(fmt.decimal)
        self.units = QCheckBox("Second line contains units")
        self.units.setChecked(fmt.units_row)
        fl.addRow("Column delimiter", self.delim)
        fl.addRow("Decimal sign", self.dec)
        fl.addRow("", self.units)
        lay.addWidget(box)

        self.map_box = QGroupBox("Column assignment")
        self.grid = QGridLayout(self.map_box)
        lay.addWidget(self.map_box)
        self.combos: dict = {}
        self._fill_mapping()
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _resplit(self) -> None:
        if self.fmt.preview:
            d = self.delim.currentData()
            self.fmt.columns = [c.strip().strip('"') for c in self.fmt.preview[0].split(d)]
            self._fill_mapping()

    def _fill_mapping(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        self.combos = {}
        labels = {"TIME": "Time", "CA_H": "CA High", "CA_L": "CA Low", "DMR_H": "DMR_H",
                  "DMR_L": "DMR_L", "PI_RM_H": "PI_RM_H", "PI_RM_L": "PI_RM_L",
                  "KLS_H": "KLS_H", "KLS_L": "KLS_L"}
        for r, canon in enumerate(CANONICAL):
            cb = QComboBox()
            cb.addItem("(not present)", None)
            for col in self.fmt.columns:
                cb.addItem(col, col)
            cb.setCurrentIndex(max(0, cb.findData(self.fmt.mapping.get(canon))))
            self.grid.addWidget(QLabel(labels[canon]), r, 0)
            self.grid.addWidget(cb, r, 1)
            self.combos[canon] = cb

    def _ok(self) -> None:
        mapping = {k: cb.currentData() for k, cb in self.combos.items() if cb.currentData()}
        if "TIME" not in mapping:
            QMessageBox.warning(self, "Import", "The time column must be assigned.")
            return
        if len(set(mapping.values())) != len(mapping):
            QMessageBox.warning(self, "Import", "A file column is assigned more than once.")
            return
        if self.delim.currentData() == "," and self.dec.currentText() == ",":
            QMessageBox.warning(self, "Import", "Comma cannot be both the column delimiter "
                                                "and the decimal sign.")
            return
        f = self.fmt
        f.delimiter, f.decimal = self.delim.currentData(), self.dec.currentText()
        f.units_row = self.units.isChecked()
        f.mapping = mapping
        f.ignored = [c for c in f.columns if c not in mapping.values()]
        self.accept()


def sniff_or_ask(path: str, parent=None, always_ask: bool = False) -> CsvFormat | None:
    """Format of a file; the import dialog appears only if detection fails or on request."""
    try:
        fmt = sniff_csv(path)
        need = ("TIME" not in fmt.mapping
                or not any(k in fmt.mapping for k in ("DMR_H", "DMR_L", "PI_RM_H", "PI_RM_L",
                                                      "KLS_H", "KLS_L")))
        err = "The columns could not be assigned automatically." if need else ""
    except CsvFormatError as exc:
        fmt, need, err = None, True, f"Automatic format detection failed: {exc}"
    if not (need or always_ask):
        return fmt
    dlg = ImportDialog(path, fmt, err, parent)
    try:
        return dlg.fmt if dlg.exec() == QDialog.Accepted else None
    finally:
        dlg.deleteLater()


# --------------------------------------------------------------- formulas
class CalculatorDialog(QDialog):
    """Build one formula channel by clicking channels, operators and functions."""

    def __init__(self, session: Session, formula: Formula | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Calculator")
        self.resize(640, 460)
        self.session = session
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(formula.name if formula else "")
        self.name.setPlaceholderText("e.g. DIMEP")
        self.unit = QLineEdit(formula.unit if formula else "")
        self.expr = QLineEdit(formula.expr if formula else "")
        self.expr.setPlaceholderText("e.g. IMEP_H - IMEP_L")
        form.addRow("Name", self.name)
        form.addRow("Unit", self.unit)
        form.addRow("Formula", self.expr)
        lay.addLayout(form)
        mid = QHBoxLayout()
        left = QVBoxLayout()
        self.ds = QComboBox()
        self.ds.addItems(["CY", "CA", "TIME"])
        self.ds.currentTextChanged.connect(self._fill)
        self.chans = QListWidget()
        self.chans.itemDoubleClicked.connect(lambda it: self._insert(it.text()))
        left.addWidget(QLabel("Channels (double-click to insert):"))
        left.addWidget(self.ds)
        left.addWidget(self.chans)
        mid.addLayout(left, 1)
        pad = QGridLayout()
        keys = ["7", "8", "9", "/", "sqrt(", "4", "5", "6", "*", "abs(", "1", "2", "3", "-",
                "mean(", "0", ".", "(", "+", "std(", ")", "**", "log(", "exp(", "max("]
        for i, k in enumerate(keys):
            b = QPushButton(k)
            b.setFixedHeight(30)
            b.clicked.connect(lambda _=False, k=k: self._insert(k))
            pad.addWidget(b, i // 5, i % 5)
        mid.addLayout(pad, 1)
        lay.addLayout(mid)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()
        self.formula = None

    def _fill(self) -> None:
        self.chans.clear()
        e = self.session.entry
        if e is not None:
            self.chans.addItems([c.name for c in e.channels(self.ds.currentText())
                                 if c.name not in {f.name for f in self.session.formulas}])

    def _insert(self, text: str) -> None:
        self.expr.insert(text if text[-1] in "(." or text.isdigit() else f" {text} "
                         if text in "+-*/**" else text)
        self.expr.setFocus()

    def _ok(self) -> None:
        f = Formula(self.name.text().strip(), self.expr.text().strip(), self.unit.text().strip())
        e = self.session.entry
        try:
            if e is None:
                raise FormulaError("load a measurement first")
            ds = e.check_formula(f)
        except FormulaError as exc:
            self.status.setStyleSheet("color:#a02020;")
            self.status.setText(str(exc))
            return
        self.status.setText(f"ok - data set {ds}")
        self.formula = f
        self.accept()


def parse_formula_text(text: str) -> list:
    """Lines 'NAME [unit] = expression  # comment' -> list of Formula."""
    out = []
    for no, line in enumerate(text.splitlines(), 1):
        desc = ""
        if "#" in line:
            line, desc = line.split("#", 1)
        line = line.strip()
        if not line:
            continue
        if "=" not in line:
            raise FormulaError(f"line {no}: expected 'NAME [unit] = expression'")
        left, expr = line.split("=", 1)
        left = left.strip()
        unit = ""
        if "[" in left and left.endswith("]"):
            left, unit = left[:-1].split("[", 1)
        out.append(Formula(left.strip(), expr.strip(), unit.strip(), desc.strip()))
    names = [f.name for f in out]
    for n in names:
        if names.count(n) > 1:
            raise FormulaError(f"the name '{n}' is defined more than once")
    return out


def formula_text(formulas: list) -> str:
    lines = []
    for f in formulas:
        unit = f" [{f.unit}]" if f.unit else ""
        desc = f"  # {f.desc}" if f.desc else ""
        lines.append(f"{f.name}{unit} = {f.expr}{desc}")
    return "\n".join(lines)


class FormulaEditor(QDialog):
    """Text editor for all formula channels."""

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Formula Editor")
        self.resize(680, 440)
        self.session = session
        lay = QVBoxLayout(self)
        hint = QLabel("One formula per line:  NAME [unit] = expression  # comment\n"
                      "Operators + - * / ** %, functions abs sqrt exp log log10 sin cos "
                      "minimum maximum, statistics mean std min max median.\n"
                      "All channels of a formula must belong to the same data set "
                      "(CY, CA or TIME).")
        hint.setStyleSheet("color:#505050;")
        lay.addWidget(hint)
        self.edit = QPlainTextEdit(formula_text(session.formulas))
        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        self.edit.setFont(mono)
        lay.addWidget(self.edit)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        check = bb.addButton("Check", QDialogButtonBox.ActionRole)
        check.clicked.connect(self.check)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.formulas: list | None = None

    def check(self) -> list | None:
        e = self.session.entry
        try:
            formulas = parse_formula_text(self.edit.toPlainText())
            sets = []
            for f in formulas:
                if e is not None:
                    try:
                        sets.append(f"{f.name}: {e.check_formula(f)}")
                    except FormulaError as exc:
                        raise FormulaError(f"{f.name}: {exc}") from None
                else:
                    formula_names(f.expr)
        except FormulaError as exc:
            self.status.setStyleSheet("color:#a02020;")
            self.status.setText(str(exc))
            return None
        self.status.setStyleSheet("color:#206020;")
        self.status.setText("ok - " + (", ".join(sets) if sets else f"{len(formulas)} formula(s)"))
        return formulas

    def _ok(self) -> None:
        formulas = self.check()
        if formulas is not None:
            self.formulas = formulas
            self.accept()
