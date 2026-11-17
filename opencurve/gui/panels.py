"""Side panels: Channel Browser, WorkFlow bar, Layout Explorer, Object Library."""

from __future__ import annotations

import fnmatch
import json

from PySide6.QtCore import QByteArray, QMimeData, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QGuiApplication, QIcon, QLinearGradient,
                           QPainter, QPainterPath, QPen, QPixmap)
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QSizePolicy, QTabWidget,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
                               QWidgetAction)

from ..core.datamodel import DATASET_LABEL, DATASETS
from .diagram import MIME_CHANNEL
from .page import OBJECT_TYPES
from .session import Session

DRAGGABLE = ("CA", "CY", "TIME")


# -------------------------------------------------------------------- icons
def make_icon(kind: str, size: int = 40) -> QIcon:
    """Simple drawn icons (no third party artwork)."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0, QColor("#3f6fb5"))
    g.setColorAt(1, QColor("#1f3f78"))
    p.setBrush(QBrush(g))
    p.setPen(QPen(QColor("#16305c"), 1))
    p.drawRoundedRect(QRectF(1, 1, size - 2, size - 2), 6, 6)
    s = size / 40.0
    white = QPen(QColor("white"), 2.4 * s, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(white)
    p.setBrush(Qt.NoBrush)
    if kind == "data":                      # magnifier over a sheet
        p.drawRect(QRectF(9 * s, 8 * s, 15 * s, 20 * s))
        p.drawEllipse(QPointF(24 * s, 24 * s), 6 * s, 6 * s)
        p.drawLine(QPointF(28.5 * s, 28.5 * s), QPointF(33 * s, 33 * s))
    elif kind == "display":                 # curve in a frame
        p.drawRect(QRectF(7 * s, 8 * s, 26 * s, 24 * s))
        path = QPainterPath(QPointF(10 * s, 27 * s))
        path.cubicTo(QPointF(16 * s, 27 * s), QPointF(17 * s, 12 * s), QPointF(21 * s, 12 * s))
        path.cubicTo(QPointF(25 * s, 12 * s), QPointF(25 * s, 26 * s), QPointF(31 * s, 26 * s))
        p.drawPath(path)
    elif kind == "calc":                    # sigma
        f = QFont("DejaVu Sans", int(22 * s))
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "Σ")
    elif kind == "export":                  # arrow out of a box
        p.drawPolyline([QPointF(13 * s, 17 * s), QPointF(8 * s, 17 * s), QPointF(8 * s, 32 * s),
                        QPointF(28 * s, 32 * s), QPointF(28 * s, 26 * s)])
        p.drawLine(QPointF(16 * s, 24 * s), QPointF(31 * s, 9 * s))
        p.drawPolyline([QPointF(22 * s, 9 * s), QPointF(31 * s, 9 * s), QPointF(31 * s, 18 * s)])
    elif kind == "util":                    # sliders
        for i, x in enumerate((12, 20, 28)):
            p.drawLine(QPointF(x * s, 9 * s), QPointF(x * s, 31 * s))
            y = (14, 25, 18)[i]
            p.setBrush(QColor("white"))
            p.drawEllipse(QPointF(x * s, y * s), 3 * s, 3 * s)
            p.setBrush(Qt.NoBrush)
    else:                                   # generic dot
        p.drawEllipse(QPointF(20 * s, 20 * s), 8 * s, 8 * s)
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------- channel browser
class _ChannelTree(QTreeWidget):
    def __init__(self, browser: "ChannelBrowser"):
        super().__init__()
        self.browser = browser
        self.setColumnCount(3)
        self.setHeaderLabels(["Name", "Unit", "Description"])
        self.setRootIsDecorated(False)
        self.setUniformRowHeights(True)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragOnly)

    def mimeTypes(self):
        return [MIME_CHANNEL]

    def mimeData(self, items):
        ds = self.browser.dataset
        md = QMimeData()
        if ds in DRAGGABLE:
            refs = [{"ds": ds, "name": it.text(0)} for it in items]
            md.setData(MIME_CHANNEL, QByteArray(json.dumps(refs).encode("utf-8")))
        return md


class ChannelBrowser(QWidget):
    channelActivated = Signal(str, str)          # dataset, name (double click)
    newDiagramRequested = Signal(list)           # list of channel references

    def __init__(self, session: Session, parent=None):
        super().__init__(parent)
        self.session = session
        self.dataset = "CA"
        lay = QVBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(3)
        self.files = QListWidget()
        self.files.setMaximumHeight(64)
        self.files.setToolTip("Loaded measurements - click to make one active")
        self.files.itemClicked.connect(lambda it: session.set_active(self.files.row(it)))
        self.sets = QListWidget()
        self.sets.setMaximumHeight(96)
        self.sets.currentRowChanged.connect(self._set_changed)
        row = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("<Edit Channel Name Filter>")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._fill_channels)
        clear = QToolButton()
        clear.setText("...")
        clear.setToolTip("Clear the filter. Wildcards: * and ?, e.g. P*")
        clear.clicked.connect(self.filter.clear)
        row.addWidget(self.filter)
        row.addWidget(clear)
        self.tree = _ChannelTree(self)
        self.tree.itemDoubleClicked.connect(
            lambda it, _c: self.dataset in DRAGGABLE
            and self.channelActivated.emit(self.dataset, it.text(0)))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.files)
        lay.addWidget(self.sets)
        lay.addLayout(row)
        lay.addWidget(self.tree, 1)
        session.filesChanged.connect(self.reload)
        session.dataChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        s = self.session
        self.files.blockSignals(True)
        self.files.clear()
        for i, e in enumerate(s.files):
            it = QListWidgetItem(f"FILE{i + 1} ({e.name})")
            if i == s.active:
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            self.files.addItem(it)
        if s.active >= 0:
            self.files.setCurrentRow(s.active)
        self.files.blockSignals(False)

        self.sets.blockSignals(True)
        self.sets.clear()
        e = s.entry
        for ds in DATASETS:
            label = DATASET_LABEL[ds]
            if e is not None and ds in DRAGGABLE:
                label += f" ({e.dataset_count(ds)})"
            self.sets.addItem(label)
        self.sets.setCurrentRow(DATASETS.index(self.dataset))
        self.sets.blockSignals(False)
        self._fill_channels()

    def _set_changed(self, row: int) -> None:
        if 0 <= row < len(DATASETS):
            self.dataset = DATASETS[row]
            self._fill_channels()

    def select_dataset(self, ds: str) -> None:
        self.sets.setCurrentRow(DATASETS.index(ds))

    def _fill_channels(self) -> None:
        self.tree.clear()
        e = self.session.entry
        if e is None:
            return
        pat = self.filter.text().strip().lower()
        if pat and not any(ch in pat for ch in "*?"):
            pat = f"*{pat}*"
        value_ds = self.dataset in ("STAT", "PAR")
        self.tree.setColumnCount(4 if value_ds else 3)
        self.tree.setHeaderLabels(["Name", "Unit", "Value", "Description"] if value_ds
                                  else ["Name", "Unit", "Description"])
        bold = QFont()
        bold.setBold(True)
        for ch in e.channels(self.dataset):
            if pat and not fnmatch.fnmatch(ch.name.lower(), pat):
                continue
            cols = [ch.name, ch.unit] + ([ch.value, ch.desc] if value_ds else [ch.desc])
            it = QTreeWidgetItem(cols)
            it.setFont(0, bold)
            if not value_ds:
                it.setToolTip(0, f"{ch.name} [{ch.unit}] - drag into a diagram")
            self.tree.addTopLevelItem(it)
        for c in range(self.tree.columnCount() - 1):
            self.tree.resizeColumnToContents(c)

    def channel_names(self) -> list:
        return [self.tree.topLevelItem(i).text(0) for i in range(self.tree.topLevelItemCount())]

    def _menu(self, pos) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        m = QMenu(self)
        if self.dataset in DRAGGABLE:
            refs = [{"ds": self.dataset, "name": it.text(0)} for it in items]
            m.addAction("Show in New Diagram", lambda: self.newDiagramRequested.emit(refs))
            m.addAction("Add to Active Diagram",
                        lambda: [self.channelActivated.emit(r["ds"], r["name"]) for r in refs])
            m.addSeparator()
        m.addAction("Copy Name", lambda: QGuiApplication.clipboard().setText(
            "\n".join(it.text(0) for it in items)))
        m.exec(self.tree.viewport().mapToGlobal(pos))


# ------------------------------------------------------------ workflow bar
class _FlyoutRow(QPushButton):
    """Menu entry with icon, title and a one line description."""

    def __init__(self, icon: QIcon, title: str, text: str):
        super().__init__()
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QPushButton{text-align:left;padding:6px 10px;border:none;}"
                           "QPushButton:hover{background:#f6d28a;}")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 12, 4)
        ic = QLabel()
        ic.setPixmap(icon.pixmap(28, 28))
        lab = QLabel(f"<b>{title}</b><br><span style='color:#404040'>{text}</span>")
        lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        ic.setAttribute(Qt.WA_TransparentForMouseEvents)
        lay.addWidget(ic)
        lay.addWidget(lab, 1)
        self.setMinimumSize(300, 46)


class WorkFlowBar(QWidget):
    """Large buttons 'Data Access', 'Data Display', ... each with a fly-out menu."""

    layoutRequested = Signal(str)        # path of a layout file

    def __init__(self, groups: list, parent=None):
        """groups: [(title, icon kind, [(title, description, callback), ...]), ...]"""
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setTabPosition(QTabWidget.South)
        outer.addWidget(self.tabs)

        flow = QWidget()
        flow.setStyleSheet("QWidget#flow{background:#274b86;}")
        flow.setObjectName("flow")
        lay = QVBoxLayout(flow)
        lay.setContentsMargins(4, 8, 4, 8)
        lay.setSpacing(8)
        self.buttons = {}
        for title, kind, entries in groups:
            b = QToolButton()
            b.setText(title)
            b.setIcon(make_icon(kind, 44))
            b.setIconSize(QSize(44, 44))
            b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            b.setPopupMode(QToolButton.InstantPopup)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setStyleSheet(
                "QToolButton{color:white;border:1px solid transparent;border-radius:4px;"
                "padding:4px;font-size:8.5pt;}"
                "QToolButton:hover{border-color:#f6d28a;background:#35609f;}"
                "QToolButton::menu-indicator{image:none;}")
            menu = QMenu(b)
            head = QLabel(f"  {title}")
            head.setStyleSheet("background:#274b86;color:white;font-weight:bold;padding:4px;")
            wa = QWidgetAction(menu)
            wa.setDefaultWidget(head)
            menu.addAction(wa)
            for etitle, text, cb in entries:
                row = _FlyoutRow(make_icon(kind, 28), etitle, text)
                wa = QWidgetAction(menu)
                wa.setDefaultWidget(row)
                row.clicked.connect(menu.close)
                row.clicked.connect(cb)
                menu.addAction(wa)
            b.setMenu(menu)
            lay.addWidget(b)
            self.buttons[title] = b
        lay.addStretch(1)
        self.tabs.addTab(flow, "WorkFlow")

        self.layouts = QListWidget()
        self.layouts.itemDoubleClicked.connect(
            lambda it: self.layoutRequested.emit(it.data(Qt.UserRole)))
        self.layouts.setToolTip("Double-click to open a layout")
        self.tabs.addTab(self.layouts, "Layouts")

    def set_layouts(self, entries: list) -> None:
        """entries: [(label, path), ...]"""
        self.layouts.clear()
        for label, path in entries:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, str(path))
            it.setToolTip(str(path))
            self.layouts.addItem(it)


# ---------------------------------------------------------- layout explorer
class LayoutExplorer(QTreeWidget):
    """Pages and objects of the layout as a tree."""

    objectSelected = Signal(int, int)        # page index, object index (-1: page only)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.itemClicked.connect(self._clicked)

    def rebuild(self, pages: list, current: int) -> None:
        self.clear()
        for pi, page in enumerate(pages):
            top = QTreeWidgetItem([page.name])
            top.setData(0, Qt.UserRole, (pi, -1))
            f = top.font(0)
            f.setBold(pi == current)
            top.setFont(0, f)
            for oi, frame in enumerate(page.frames):
                label = OBJECT_TYPES[frame.obj.type_name][0]
                extra = ""
                spec = frame.obj.spec
                if frame.obj.type_name == "diagram":
                    names = [c["name"] for c in spec.get("curves", [])]
                    extra = ": " + ", ".join(names[:4]) + (" ..." if len(names) > 4 else "")
                elif frame.obj.type_name == "text":
                    extra = ": " + spec.get("text", "")[:24]
                child = QTreeWidgetItem([label + extra])
                child.setData(0, Qt.UserRole, (pi, oi))
                top.addChild(child)
            self.addTopLevelItem(top)
            top.setExpanded(pi == current)

    def _clicked(self, item, _col) -> None:
        pi, oi = item.data(0, Qt.UserRole)
        self.objectSelected.emit(pi, oi)


class ObjectLibrary(QListWidget):
    """Predefined objects; double-click inserts one on the current page."""

    objectRequested = Signal(dict)

    def __init__(self, entries: list, parent=None):
        """entries: [(label, description, spec), ...]"""
        super().__init__(parent)
        for label, desc, spec in entries:
            it = QListWidgetItem(label)
            it.setToolTip(desc)
            it.setData(Qt.UserRole, spec)
            self.addItem(it)
        self.itemDoubleClicked.connect(lambda it: self.objectRequested.emit(it.data(Qt.UserRole)))
        self.setToolTip("Double-click to insert the object on the current page")
