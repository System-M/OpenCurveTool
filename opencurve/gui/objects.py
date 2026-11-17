"""Page objects besides the diagram: result table, statistics, parameters, text, image."""

from __future__ import annotations

import copy
import datetime as _dt
import re

import numpy as np
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QGuiApplication, QKeySequence, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QLabel, QTableView,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core.analysis import scalar_info
from ..core.config import CYL_NAMES
from ..core.datamodel import split_cyl
from ..core.export import ordered_scalars
from .session import Session

DEFAULT_TABLE = ["SPEED", "PMAX", "APMAX", "IMEP", "AI05", "AI50", "AI90", "KP_PK", "KNOCK",
                 "PI_SOE_1", "PI_DUR_1", "PI_IPK_1"]
DEFAULT_STATS = ["SPEED", "PMAX", "APMAX", "RMAX", "IMEP", "AI05", "AI50", "AI90", "BD",
                 "KP_PK", "KNOCK_FREQ", "PI_N", "PI_SOE_1", "PI_DUR_1", "PI_IPK_1", "PI_IHOLD_1",
                 "IGD"]
DEFAULT_PARAMS = ["FILENAME", "ENGINE", "BORE", "STROKE", "CONROD", "EPSILON", "POLY_COEFF",
                  "FSAMPLE"]


def fmt_num(v) -> str:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    a = abs(v)
    if a != 0 and (a >= 1e6 or a < 1e-3):
        return f"{v:.4g}"
    return f"{v:.4f}" if a < 100 else f"{v:.2f}"


class _Base(QWidget):
    activated = Signal()
    changed = Signal()
    propertiesRequested = Signal()
    type_name = ""

    def __init__(self, session: Session, spec: dict | None, defaults: dict):
        super().__init__()
        self.session = session
        self.spec = dict(defaults)
        if spec:
            self.spec.update(copy.deepcopy(spec))
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(2, 2, 2, 2)
        self.lay.setSpacing(2)
        session.dataChanged.connect(self.refresh)

    def to_spec(self) -> dict:
        return copy.deepcopy(self.spec)

    def set_spec(self, spec: dict) -> None:
        self.spec.update(copy.deepcopy(spec))
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:  # pragma: no cover - overridden
        pass

    def mousePressEvent(self, ev):
        self.activated.emit()
        super().mousePressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self.propertiesRequested.emit()


# ------------------------------------------------------------ result table
class _CycleModel(QAbstractTableModel):
    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self.names: list = []
        self.cols: list = []
        self.used = np.zeros(0, dtype=bool)
        self.n = 0

    def load(self, names: list) -> None:
        self.beginResetModel()
        e = self.session.entry
        self.names, self.cols = [], []
        self.n = e.n_cycles if e else 0
        self.used = np.ones(self.n, dtype=bool)
        if e is not None:
            for name in names:
                ch = e.get("CY", name)
                if ch is None:
                    continue
                col = np.full(self.n, np.nan)
                col[:ch.y.size] = ch.y
                self.names.append((name, ch.unit))
                self.cols.append(col)
            for cr in e.result.cyl.values():
                u = np.zeros(self.n, dtype=bool)
                u[:cr.n_cycles] = cr.used()
                # a row is greyed if the cycle is unused in any cylinder that has it
                self.used &= u | (np.arange(self.n) >= cr.n_cycles)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else self.n

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        v = float(self.cols[index.column()][index.row()])
        if role == Qt.DisplayRole:
            return fmt_num(v)
        if role == Qt.UserRole:
            return -np.inf if np.isnan(v) else v
        if role == Qt.TextAlignmentRole:
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ForegroundRole and not self.used[index.row()]:
            return QBrush(QColor("#a0a0a0"))
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            name, unit = self.names[section]
            return f"{name}\n[{unit}]"
        return str(section + 1)


class TableObject(_Base):
    """Cycle results: one row per cycle; a click selects the cycle."""

    type_name = "table"

    def __init__(self, session, spec=None):
        super().__init__(session, spec, {"type": "table", "columns": [], "cyl": "both"})
        self.model = _CycleModel(session)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortRole(Qt.UserRole)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setSortingEnabled(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setAlternatingRowColors(True)
        self.view.verticalHeader().setDefaultSectionSize(20)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.view.clicked.connect(self._clicked)
        self.lay.addWidget(self.view)
        session.cycleChanged.connect(self._follow)
        self.proxy.sort(-1)
        self.refresh()

    def column_names(self) -> list:
        cols = self.spec.get("columns") or []
        if cols:
            return cols
        cyls = [c for c in ("H", "L") if self.spec.get("cyl", "both") in ("both", c)]
        return [f"{n}_{c}" for c in cyls for n in DEFAULT_TABLE]

    def refresh(self) -> None:
        self.model.load(self.column_names())
        self._follow(self.session.cycle)

    def _clicked(self, index) -> None:
        self.activated.emit()
        self.session.set_cycle(self.proxy.mapToSource(index).row())

    def _follow(self, cycle: int) -> None:
        if self.model.n == 0 or self.model.columnCount() == 0:
            return
        idx = self.proxy.mapFromSource(self.model.index(min(cycle, self.model.n - 1), 0))
        self.view.blockSignals(True)
        self.view.selectRow(idx.row())
        self.view.blockSignals(False)

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.Copy):
            self.copy()
        else:
            super().keyPressEvent(ev)

    def copy(self) -> None:
        m = self.model
        lines = ["Cycle\t" + "\t".join(n for n, _ in m.names)]
        for r in range(m.n):
            lines.append(str(r + 1) + "\t" + "\t".join(
                "" if np.isnan(c[r]) else repr(float(c[r])) for c in m.cols))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def export_table(self):
        m = self.model
        return (["Cycle"] + [n for n, _ in m.names],
                [np.arange(1, m.n + 1, dtype=float)] + list(m.cols))


# -------------------------------------------------------------- statistics
class StatsObject(_Base):
    type_name = "stats"
    COLS = ("mean", "std", "min", "max", "cov", "n")
    HEAD = ("Mean", "Std", "Min", "Max", "COV %", "n")

    def __init__(self, session, spec=None):
        super().__init__(session, spec, {"type": "stats", "names": []})
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.lay.addWidget(self.table)
        self.refresh()

    def refresh(self) -> None:
        e = self.session.entry
        t = self.table
        t.clear()
        if e is None:
            t.setRowCount(0)
            t.setColumnCount(0)
            return
        cyls = list(e.result.cyl)
        names = self.spec.get("names") or DEFAULT_STATS
        have = [n for n in names if any(n in e.result.cyl[c].stats for c in cyls)]
        if not self.spec.get("names"):
            pass
        t.setColumnCount(1 + len(cyls) * len(self.COLS))
        t.setHorizontalHeaderLabels(
            ["Unit"] + [f"{CYL_NAMES[c]}\n{h}" for c in cyls for h in self.HEAD])
        t.setRowCount(len(have))
        t.setVerticalHeaderLabels(have)
        for r, n in enumerate(have):
            unit = "%" if n == "KNOCK_FREQ" else scalar_info(n)[0]
            t.setItem(r, 0, QTableWidgetItem(unit))
            for ci, c in enumerate(cyls):
                st = e.result.cyl[c].stats.get(n)
                for k, key in enumerate(self.COLS):
                    txt = ""
                    if st is not None:
                        v = st[key]
                        txt = str(v) if key == "n" else (
                            "" if np.isnan(v) else f"{v:.2f}" if key == "cov" else fmt_num(v))
                        if key == "cov" and txt and 0 < st["n"] < 10:
                            txt += " *"
                    it = QTableWidgetItem(txt)
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    if key == "cov" and txt.endswith("*"):
                        it.setToolTip("fewer than 10 valid cycles")
                    t.setItem(r, 1 + ci * len(self.COLS) + k, it)

    def copy(self) -> None:
        t = self.table
        lines = ["Name\t" + "\t".join(
            t.horizontalHeaderItem(c).text().replace("\n", " ") for c in range(t.columnCount()))]
        for r in range(t.rowCount()):
            lines.append(t.verticalHeaderItem(r).text() + "\t" + "\t".join(
                t.item(r, c).text() if t.item(r, c) else "" for c in range(t.columnCount())))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def keyPressEvent(self, ev):
        if ev.matches(QKeySequence.Copy):
            self.copy()
        else:
            super().keyPressEvent(ev)


# -------------------------------------------------------------- parameters
class ParamObject(_Base):
    """Name / unit / value block like 'Test Parameter' in the reference layout."""

    type_name = "params"

    def __init__(self, session, spec=None):
        super().__init__(session, spec, {"type": "params", "title": "Test Parameter:",
                                         "names": []})
        self.setAutoFillBackground(True)
        self.setStyleSheet("ParamObject{background:white;}")
        self.title = QLabel()
        f = self.title.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        self.title.setFont(f)
        self.title.setContentsMargins(0, 2, 0, 6)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(20)
        self.table.setShowGrid(True)
        self.table.setGridStyle(Qt.SolidLine)
        self.table.setStyleSheet("QTableWidget{gridline-color:#808080;border:1px solid #808080;}")
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setColumnCount(3)
        hh = self.table.horizontalHeader()
        hh.setMinimumSectionSize(30)
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)      # takes the rest of the frame
        f = self.table.font()
        f.setPointSize(8)
        self.table.setFont(f)
        self.lay.addWidget(self.title)
        self.lay.addWidget(self.table)
        self.lay.addStretch(1)
        self.refresh()

    def refresh(self) -> None:
        self.title.setText(self.spec.get("title", ""))
        self.title.setVisible(bool(self.spec.get("title")))
        e = self.session.entry
        rows = e.result.parameters() if e else []
        names = self.spec.get("names") or DEFAULT_PARAMS
        by_name = {r[0]: r for r in rows}
        sel = [by_name[n] for n in names if n in by_name]
        self.table.setRowCount(len(sel))
        for r, (n, u, v, _d) in enumerate(sel):
            val = v if isinstance(v, str) else (f"{v:.2f}" if isinstance(v, float) else str(v))
            for c, txt in enumerate((n, u, val)):
                it = QTableWidgetItem(txt)
                if c == 2:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, it)
        # exactly as high as its rows, so that the table needs no scroll bar
        self.table.setFixedHeight(len(sel) * self.table.verticalHeader().defaultSectionSize() + 2)
        frame = self.parentWidget()
        if frame is not None and hasattr(frame, "page"):
            frame.page.place(frame)          # the needed size may have changed

    def preferred_size(self) -> tuple[int, int]:
        """Pixel size at which all rows and columns are visible."""
        t = self.table
        w = sum(max(30, t.sizeHintForColumn(c)) for c in range(t.columnCount())) + 14
        h = t.height() + 8 + (self.title.sizeHint().height() if self.spec.get("title") else 0)
        return max(w, self.title.sizeHint().width() + 8), h


# -------------------------------------------------------------------- text
_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_:]+)\}")


def resolve_placeholders(text: str, session: Session) -> str:
    e = session.entry

    def sub(m):
        key = m.group(1)
        parts = key.split(":")
        if key == "FILENAME":
            return e.name if e else "-"
        if key == "DATE":
            return _dt.date.today().isoformat()
        if key == "CYCLE":
            return str(session.cycle + 1) if e else "-"
        if key == "NCYCLES":
            return str(session.n_cycles)
        if e is None:
            return "-"
        if parts[0] == "PAR" and len(parts) == 2:
            for n, _u, v, _d in e.result.parameters():
                if n == parts[1]:
                    return v if isinstance(v, str) else f"{v:g}"
            return "-"
        if parts[0] == "STAT" and len(parts) in (2, 3):
            base, cyl = split_cyl(parts[1])
            cr = e.result.cyl.get(cyl) if cyl else None
            st = cr.stats.get(base) if cr else None
            if st is None:
                return "-"
            v = st.get(parts[2] if len(parts) == 3 else "mean")
            return "-" if v is None or (isinstance(v, float) and np.isnan(v)) else fmt_num(float(v))
        return m.group(0)

    return _PLACEHOLDER.sub(sub, text)


class TextObject(_Base):
    """Text with placeholders: {FILENAME} {DATE} {CYCLE} {NCYCLES} {PAR:BORE} {STAT:PMAX_H}."""

    type_name = "text"

    def __init__(self, session, spec=None):
        super().__init__(session, spec, {"type": "text", "text": "Text", "size": 11,
                                         "bold": False, "align": "left"})
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.label.setTextFormat(Qt.PlainText)
        self.lay.addWidget(self.label)
        session.cycleChanged.connect(lambda _i: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        f = self.label.font()
        f.setPointSize(int(self.spec.get("size", 11)))
        f.setBold(bool(self.spec.get("bold")))
        self.label.setFont(f)
        al = {"left": Qt.AlignLeft, "center": Qt.AlignHCenter, "right": Qt.AlignRight}
        self.label.setAlignment(al.get(self.spec.get("align", "left"), Qt.AlignLeft) | Qt.AlignTop)
        self.label.setText(resolve_placeholders(self.spec.get("text", ""), self.session))


# ------------------------------------------------------------------- image
class ImageObject(_Base):
    type_name = "image"

    def __init__(self, session, spec=None):
        super().__init__(session, spec, {"type": "image", "path": ""})
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.lay.addWidget(self.label)
        self._pix = None
        self.refresh()

    def refresh(self) -> None:
        path = self.spec.get("path", "")
        self._pix = QPixmap(path) if path else None
        if self._pix is None or self._pix.isNull():
            self._pix = None
            self.label.setText("Image\n(double-click to choose a file)" if not path
                               else f"Image not found:\n{path}")
            self.label.setStyleSheet("color:#808080;border:1px dashed #b0b0b0;")
        else:
            self.label.setStyleSheet("")
            self._scale()

    def _scale(self):
        if self._pix is not None:
            self.label.setPixmap(self._pix.scaled(self.label.size(), Qt.KeepAspectRatio,
                                                  Qt.SmoothTransformation))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._scale()


def result_names(session: Session) -> list:
    """All cycle based names (without cylinder suffix) of the active file."""
    e = session.entry
    if e is None:
        return []
    names: list = []
    for cr in e.result.cyl.values():
        for n in cr.scalars:
            if n not in names:
                names.append(n)
    return ordered_scalars(names)
