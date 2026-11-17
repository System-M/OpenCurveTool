"""2D diagram object: stacked strips, several y axes, legend table, cursors, drag & drop."""

from __future__ import annotations

import copy
import json

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import QMenu, QVBoxLayout, QWidget

from ..core.datamodel import XAXIS, split_cyl
from .session import Session

MIME_CHANNEL = "application/x-opencurve-channel"
CYL_COLOR = {"H": "#2a78d6", "L": "#eb6834"}
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#6250d6", "#e34948", "#008300", "#e87ba4",
           "#eda100"]
CURSOR_COLOR = "#e00000"
STYLES = {"solid": Qt.SolidLine, "dash": Qt.DashLine, "dot": Qt.DotLine,
          "dashdot": Qt.DashDotLine}
CURVE_MODES = ("global", "single", "range", "all", "mean", "envelope")
LEFT_AXIS_WIDTH = 64
RIGHT_AXIS_WIDTH = 60

pg.setConfigOptions(background="w", foreground="#202020", antialias=True)


def default_spec() -> dict:
    return {"type": "diagram", "title": "", "curves": [], "strips": 1, "legend": True,
            "grid": True, "cursor2": False, "x": {"auto": True, "min": 0.0, "max": 1.0},
            "axes": {}, "legend_pos": None}


def default_curve(ds: str, name: str, **kw) -> dict:
    c = {"ds": ds, "name": name, "file": "active", "strip": 0, "axis": 0, "color": None,
         "width": 1.5, "style": "solid", "mode": "global", "visible": True, "xname": None}
    c.update(kw)
    return c


class _Legend(pg.TextItem):
    """Legend table inside the plot area; can be dragged."""

    def __init__(self):
        super().__init__(anchor=(1, 0), border=pg.mkPen("#404040"),
                         fill=pg.mkBrush(255, 255, 255, 235))
        self.setZValue(1000)
        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setCursor(Qt.SizeAllCursor)
        self.user_pos = None           # fraction of the view box (x, y) or None = top right

    def mouseDragEvent(self, ev):
        if ev.button() != Qt.LeftButton:
            ev.ignore()
            return
        ev.accept()
        vb = self.parentItem()
        if vb is None:
            return
        delta = ev.pos() - ev.lastPos()
        p = self.pos() + delta
        self.setPos(p)
        r = vb.boundingRect()
        if r.width() > 0 and r.height() > 0:
            self.user_pos = (p.x() / r.width(), p.y() / r.height())

    def place(self):
        vb = self.parentItem()
        if vb is None:
            return
        r = vb.boundingRect()
        if self.user_pos is None:
            self.setPos(r.width() - 6, 6)
        else:
            self.setPos(self.user_pos[0] * r.width(), self.user_pos[1] * r.height())


class _Strip:
    """One plot area with a left axis and optional further axes on the right."""

    def __init__(self, plot: pg.PlotItem):
        self.plot = plot
        self.vbs = [plot.vb]
        self.axes = [plot.getAxis("left")]
        self.items: list = []
        plot.vb.sigResized.connect(self._sync)

    def view(self, axis: int) -> pg.ViewBox:
        while axis >= len(self.vbs):
            vb = pg.ViewBox()
            ax = pg.AxisItem("right")
            ax.enableAutoSIPrefix(False)
            self.plot.layout.addItem(ax, 2, 2 + len(self.vbs))
            self.plot.scene().addItem(vb)
            ax.linkToView(vb)
            vb.setXLink(self.plot.vb)
            vb.setMouseEnabled(x=False, y=False)
            vb.setMenuEnabled(False)
            vb.enableAutoRange(axis=pg.ViewBox.YAxis)
            vb.setAutoVisible(y=True)
            self.vbs.append(vb)
            self.axes.append(ax)
            self._sync()
        return self.vbs[axis]

    def _sync(self):
        rect = self.plot.vb.sceneBoundingRect()
        for vb in self.vbs[1:]:
            vb.setGeometry(rect)
            vb.linkedViewChanged(self.plot.vb, vb.XAxis)


class _View(pg.GraphicsLayoutWidget):
    """Graphics view that accepts channels dragged from the Channel Browser."""

    def __init__(self, owner: "DiagramObject"):
        super().__init__()
        self.owner = owner
        self.setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(owner.show_context_menu)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasFormat(MIME_CHANNEL):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasFormat(MIME_CHANNEL):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev):
        if not ev.mimeData().hasFormat(MIME_CHANNEL):
            ev.ignore()
            return
        refs = json.loads(bytes(ev.mimeData().data(MIME_CHANNEL)).decode("utf-8"))
        pos = ev.position() if hasattr(ev, "position") else QPointF(ev.pos())
        self.owner.drop_channels(refs, pos)
        ev.acceptProposedAction()

    def mouseDoubleClickEvent(self, ev):
        super().mouseDoubleClickEvent(ev)
        self.owner.propertiesRequested.emit()

    def mousePressEvent(self, ev):
        self.owner.setFocus()
        self.owner.activated.emit()
        super().mousePressEvent(ev)


class DiagramObject(QWidget):
    type_name = "diagram"
    activated = Signal()
    propertiesRequested = Signal()
    cursorMoved = Signal(int, float)          # cursor index, x
    changed = Signal()                        # specification changed (layout is modified)

    def __init__(self, session: Session, spec: dict | None = None, parent=None):
        super().__init__(parent)
        self.session = session
        self.spec = default_spec()
        if spec:
            self.spec.update(copy.deepcopy(spec))
        self.setFocusPolicy(Qt.StrongFocus)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = _View(self)
        lay.addWidget(self.view)
        self.strips: list[_Strip] = []
        self.legend: _Legend | None = None
        self.cursors: list[list] = [[], []]          # per cursor: one line per strip
        self.cursor_x = [None, None]
        self.link_cursors = True
        self._history: list = []
        self._last_state = None
        self._data: list = []                        # per curve: dict used for the legend
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(0)
        self._settle.timeout.connect(self._remember_state)
        self._blocked = False
        session.dataChanged.connect(self.refresh)
        session.cycleChanged.connect(self._on_cycle)
        session.modeChanged.connect(self.refresh)
        self.rebuild()

    # ------------------------------------------------------------ helpers
    @property
    def curves(self) -> list:
        return self.spec["curves"]

    def x_kind(self) -> str | None:
        """Data set of the diagram (CA / CY / TIME) or None while it is empty."""
        return self.curves[0]["ds"] if self.curves else None

    def is_xy(self) -> bool:
        return any(c.get("xname") for c in self.curves)

    def next_color(self, name: str, strip: int = 0) -> str:
        used = {c["color"] for c in self.curves if c.get("strip", 0) == strip}
        _, cyl = split_cyl(name)
        if cyl and CYL_COLOR[cyl] not in used:
            return CYL_COLOR[cyl]
        for col in PALETTE:
            if col not in used:
                return col
        return PALETTE[len(self.curves) % len(PALETTE)]

    def to_spec(self) -> dict:
        s = copy.deepcopy(self.spec)
        s["strips"] = len(self.strips)
        if self.legend is not None:
            s["legend_pos"] = list(self.legend.user_pos) if self.legend.user_pos else None
        return s

    # ---------------------------------------------------------- structure
    def rebuild(self) -> None:
        """Create strips, axes and curve items from the specification."""
        self._blocked = True
        self.view.clear()
        self.strips = []
        self.cursors = [[], []]
        n = max(1, int(self.spec.get("strips", 1)),
                max((c["strip"] for c in self.curves), default=0) + 1)
        self.spec["strips"] = n
        kind = self.x_kind()
        dotted = pg.mkPen("#9a9a9a", width=1, style=Qt.DotLine)
        for i in range(n):
            plot = self.view.addPlot(row=i, col=0)
            plot.setMenuEnabled(False)
            plot.hideButtons()
            plot.vb.setMouseMode(pg.ViewBox.RectMode)
            plot.showGrid(x=bool(self.spec.get("grid", True)), y=bool(self.spec.get("grid", True)),
                          alpha=0.55)
            for ax_name in ("left", "bottom"):
                ax = plot.getAxis(ax_name)
                ax.enableAutoSIPrefix(False)
                ax.setPen(pg.mkPen("#404040"))
                ax.setTextPen(pg.mkPen("#202020"))
                try:
                    ax.setTickPen(dotted)
                except AttributeError:      # older pyqtgraph: solid grid lines
                    pass
            strip = _Strip(plot)
            self.strips.append(strip)
            if i > 0:
                plot.setXLink(self.strips[0].plot)
            if i < n - 1:
                plot.getAxis("bottom").setStyle(showValues=False)
            plot.vb.sigResized.connect(lambda *_: self._update_labels())
            plot.vb.sigRangeChangedManually.connect(self._manual_range)
            plot.vb.sigRangeChanged.connect(lambda *_: self._settle.start())
            if kind == "CA" and not self.is_xy():
                plot.vb.sigXRangeChanged.connect(self._update_x_ticks)
        if self.spec.get("title"):
            self.strips[0].plot.setTitle(self.spec["title"], size="10pt", color="#202020")

        # curves
        for c in self.curves:
            c["strip"] = min(max(0, int(c.get("strip", 0))), n - 1)
            if not c.get("color"):
                c["color"] = self.next_color(c["name"], c["strip"])
        self._items = []
        for c in self.curves:
            strip = self.strips[c["strip"]]
            vb = strip.view(int(c.get("axis", 0)))
            pen = pg.mkPen(c["color"], width=float(c.get("width", 1.5)),
                           style=STYLES.get(c.get("style", "solid"), Qt.SolidLine))
            # raw time data: thin, not anti-aliased lines - a wide smooth pen needs
            # 30 ms per curve and repaint for dense zigzag paths
            raw = c["ds"] == "TIME"
            main = pg.PlotDataItem(pen=pen, connect="finite", antialias=not raw)
            lo = pg.PlotDataItem(pen=pg.mkPen(c["color"], width=1), connect="finite")
            hi = pg.PlotDataItem(pen=pg.mkPen(c["color"], width=1), connect="finite")
            col = QColor(c["color"])
            col.setAlpha(45)
            fill = pg.FillBetweenItem(lo, hi, brush=pg.mkBrush(col))
            for it in (fill, lo, hi, main):
                if int(c.get("axis", 0)) == 0:
                    strip.plot.addItem(it)
                else:
                    vb.addItem(it)
            if c["ds"] == "TIME":
                # after addItem: the plot would otherwise reset these to its own defaults.
                # Raw data are reduced to min/max per pixel, so peaks stay visible.
                main.setDownsampling(auto=True, method="peak")
                main.setClipToView(True)
            self._items.append({"main": main, "lo": lo, "hi": hi, "fill": fill})

        # same number and width of axes in every strip, so that the x axes line up
        n_axes = max(len(s.vbs) for s in self.strips)
        for si, strip in enumerate(self.strips):
            strip.view(n_axes - 1)
            strip.axes[0].setWidth(LEFT_AXIS_WIDTH)
            for ai in range(1, n_axes):
                ax = strip.axes[ai]
                ax.setWidth(RIGHT_AXIS_WIDTH)
                if not any(c["strip"] == si and int(c.get("axis", 0)) == ai for c in self.curves):
                    ax.setStyle(showValues=False, tickLength=0)
                    ax.setPen(pg.mkPen(None))

        self._labels = None
        self._update_labels(force=True)
        self._x_spacing = "unset"
        if kind == "CA" and not self.is_xy():
            self._update_x_ticks()

        # legend and cursors
        self.legend = None
        if self.spec.get("legend", True):
            self.legend = _Legend()
            self.legend.setParentItem(self.strips[0].plot.vb)
            pos = self.spec.get("legend_pos")
            self.legend.user_pos = tuple(pos) if pos else None
            self.strips[0].plot.vb.sigResized.connect(self.legend.place)
        if not self.is_xy() and kind is not None:
            for ci in (0, 1):
                if ci == 1 and not self.spec.get("cursor2"):
                    continue
                pen = pg.mkPen(CURSOR_COLOR, width=1.2,
                               style=Qt.SolidLine if ci == 0 else Qt.DashLine)
                for strip in self.strips:
                    line = pg.InfiniteLine(angle=90, movable=True, pen=pen,
                                           hoverPen=pg.mkPen(CURSOR_COLOR, width=2.5))
                    line.setZValue(900)
                    line.sigPositionChanged.connect(
                        lambda ln, ci=ci: self._cursor_dragged(ci, ln.value()))
                    strip.plot.addItem(line, ignoreBounds=True)
                    self.cursors[ci].append(line)
        self._blocked = False
        self._apply_ranges()
        self.refresh()
        self._history = []
        QTimer.singleShot(0, self._after_build)

    def _update_x_ticks(self, *_):
        """Round crank angle steps: 90/30, 10/5 or 5/1 deg depending on the visible span."""
        lo, hi = self.strips[0].plot.vb.viewRange()[0]
        span = hi - lo
        if span > 300:
            spacing = (90.0, 30.0)
        elif span > 110:
            spacing = (10.0, 5.0) if self.width() > 700 else (20.0, 10.0)
        elif span > 40:
            spacing = (10.0, 5.0)
        elif span > 12:
            spacing = (5.0, 1.0)
        else:
            spacing = None
        if spacing == getattr(self, "_x_spacing", "unset"):
            return
        self._x_spacing = spacing
        for strip in self.strips:
            ax = strip.plot.getAxis("bottom")
            if spacing is None:
                ax.setTickSpacing()
            else:
                ax.setTickSpacing(major=spacing[0], minor=spacing[1])

    def _after_build(self):
        if self.legend is not None:
            self.legend.place()
        self._remember_state()

    def _update_labels(self, force: bool = False) -> None:
        """Axis titles 'names [unit]'; units are known only once a measurement is loaded."""
        if self._blocked and not force:
            return          # strips are still being built (resize signals arrive early)
        kind = self.x_kind()
        labels = []
        if kind is not None and not self.is_xy():
            xname, xunit = XAXIS[kind]
            labels.append((-1, 0, f"{xname} [{xunit}]", "#202020"))
        elif self.is_xy():
            xs = []
            for c in self.curves:
                if c.get("xname") and c["xname"] not in xs:
                    xs.append(c["xname"])
            xu = self._unit({"ds": self.curves[0]["ds"], "name": xs[0],
                             "file": self.curves[0].get("file", "active")}) if xs else ""
            labels.append((-1, 0, f"{', '.join(xs)} [{xu}]", "#202020"))
        for si, strip in enumerate(self.strips):
            for ai in range(len(strip.vbs)):
                cs = [c for c in self.curves if c["strip"] == si and int(c.get("axis", 0)) == ai]
                if not cs:
                    continue
                units = []
                for c in cs:
                    u = self._unit(c)
                    if u not in units:
                        units.append(u)
                names = ", ".join(c["name"] for c in cs[:3]) + (" ..." if len(cs) > 3 else "")
                text = f"{names} [{', '.join(units)}]"
                room = int(strip.plot.vb.height() / 6.5)       # characters that fit
                if len(cs) > 1 and room > 0 and len(text) > room:
                    text = f"{cs[0]['name']} ... [{', '.join(units)}]"
                color = cs[0]["color"] if (ai > 0 or len(cs) == 1) else "#202020"
                labels.append((si, ai, text, color))
        if labels == getattr(self, "_labels", None):
            return
        self._labels = labels
        for si, ai, text, color in labels:
            if si < 0:
                self.strips[-1].plot.setLabel("bottom", text)
                continue
            ax = self.strips[si].axes[ai]
            ax.setLabel(text, color=color)
            if ai > 0:
                ax.setPen(pg.mkPen(color))
                ax.setTextPen(pg.mkPen(color))

    def _unit(self, c: dict) -> str:
        e = self.session.entry_for(c.get("file", "active"))
        ch = e.get(c["ds"], c["name"]) if e else None
        return ch.unit if ch is not None else "-"

    def _apply_ranges(self) -> None:
        x = self.spec.get("x", {})
        p0 = self.strips[0].plot
        if x and not x.get("auto", True):
            p0.setXRange(float(x["min"]), float(x["max"]), padding=0)
        else:
            p0.enableAutoRange(axis=pg.ViewBox.XAxis)
        for si, strip in enumerate(self.strips):
            for ai, vb in enumerate(strip.vbs):
                a = self.spec.get("axes", {}).get(f"{si}:{ai}")
                if ai == 0:
                    strip.plot.setLogMode(y=bool(a and a.get("log")),
                                          x=bool(self.spec.get("logx")))
                if a and not a.get("auto", True):
                    lo, hi = float(a["min"]), float(a["max"])
                    if a.get("log") and ai == 0 and lo > 0 and hi > 0:
                        lo, hi = np.log10(lo), np.log10(hi)
                    vb.setYRange(lo, hi, padding=0)
                else:
                    vb.enableAutoRange(axis=pg.ViewBox.YAxis)
                    vb.setAutoVisible(y=True)

    # --------------------------------------------------------------- data
    def _curve_data(self, c: dict):
        """Returns (x, y, lo, hi, cursor_x, cursor_y, channel) for a curve; arrays may be None."""
        e = self.session.entry_for(c.get("file", "active"))
        ch = e.get(c["ds"], c["name"]) if e else None
        if ch is None:
            return None, None, None, None, None, None, None
        s = self.session
        xch = e.get(c["ds"], c["xname"]) if c.get("xname") else None
        if c.get("xname") and xch is None:
            return None, None, None, None, None, None, ch
        if not ch.per_cycle:
            if ch.ds == "CY" and ch.used is not None:
                y = np.where(ch.used, ch.y, np.nan)
            else:
                y = ch.y
            x = ch.x
            if xch is not None:
                x = xch.y if not xch.per_cycle else (
                    xch.stats["mean"] if xch.stats else xch.y[0])
                if x.shape != y.shape:
                    return None, None, None, None, None, None, ch
            return x, y, None, None, x, y, ch

        mode = c.get("mode", "global")
        if mode == "global":
            mode = s.mode
        nc = ch.y.shape[0]
        cyc = min(s.cycle, nc - 1)
        used = ch.used if ch.used is not None else np.ones(nc, dtype=bool)

        def xrow(i):
            if xch is None:
                return ch.x
            return xch.y[min(i, xch.y.shape[0] - 1)] if xch.per_cycle else xch.y

        if nc == 0:
            return None, None, None, None, None, None, ch
        if mode in ("mean", "envelope") and ch.stats is not None:
            xm = ch.x if xch is None else (
                xch.stats["mean"] if xch.per_cycle and xch.stats else xrow(0))
            lo = hi = None
            if mode == "envelope":
                lo, hi = ch.stats["min"], ch.stats["max"]
            return xm, ch.stats["mean"], lo, hi, xm, ch.stats["mean"], ch
        if mode in ("all", "range"):
            if mode == "range":
                a, b = s.range
                sel = np.zeros(nc, dtype=bool)
                sel[max(0, a - 1):min(nc, b)] = True
                idx = np.flatnonzero(sel & used)
            else:
                idx = np.flatnonzero(used)
            if idx.size == 0:
                return None, None, None, None, None, None, ch
            ys = ch.y[idx]
            xs = np.vstack([xrow(i) for i in idx]) if xch is not None and xch.per_cycle \
                else np.tile(xrow(0), (idx.size, 1))
            nan = np.full((idx.size, 1), np.nan)
            x = np.hstack((xs, nan)).ravel()
            y = np.hstack((ys, nan)).ravel()
            return x, y, None, None, xrow(cyc), ch.y[cyc], ch
        return xrow(cyc), ch.y[cyc], None, None, xrow(cyc), ch.y[cyc], ch

    def refresh(self) -> None:
        if self._blocked or not hasattr(self, "_items"):
            return
        self._data = []
        for c, items in zip(self.curves, self._items):
            x, y, lo, hi, cx, cy, ch = self._curve_data(c)
            visible = bool(c.get("visible", True))
            if x is None or not visible:
                items["main"].setData([], [])
                items["lo"].setData([], [])
                items["hi"].setData([], [])
            else:
                many = ch is not None and ch.per_cycle and len(x) > ch.y.shape[1] + 1
                dense = c["ds"] == "TIME" and len(x) > 20000
                w = float(c.get("width", 1.5))
                items["main"].setPen(pg.mkPen(
                    c["color"], width=min(w, 1.0) if (many or dense) else w,
                    style=STYLES.get(c.get("style", "solid"), Qt.SolidLine)))
                items["main"].setData(x, y)
                if lo is not None:
                    items["lo"].setData(x, lo)
                    items["hi"].setData(x, hi)
                else:
                    items["lo"].setData([], [])
                    items["hi"].setData([], [])
            self._data.append({"spec": c, "ch": ch, "x": cx, "y": cy, "ok": x is not None})
        if self.cursor_x[0] is None and self.cursors[0]:
            self._init_cursors()
        self._update_labels()
        self._update_legend()

    def _on_cycle(self, _i: int) -> None:
        # only cycle resolved crank angle data depends on the active cycle
        if any(c["ds"] == "CA" for c in self.curves):
            self.refresh()

    # ------------------------------------------------------------ cursors
    def _first_x(self):
        for d in self._data:
            if d["ok"] and d["x"] is not None and len(d["x"]):
                return d["x"]
        return None

    def _init_cursors(self) -> None:
        x = self._first_x()
        if x is None:
            return
        lo, hi = float(np.nanmin(x)), float(np.nanmax(x))
        xr = self.spec.get("x", {})
        if xr and not xr.get("auto", True):
            lo, hi = max(lo, float(xr["min"])), min(hi, float(xr["max"]))
        kind = self.x_kind()
        start = 0.0 if (kind == "CA" and lo <= 0.0 <= hi) else lo + 0.5 * (hi - lo)
        self.set_cursor(0, start, emit=False)
        if self.cursors[1]:
            self.set_cursor(1, lo + 0.75 * (hi - lo), emit=False)

    def _snap(self, x: float) -> float:
        xs = self._first_x()
        if xs is None or len(xs) == 0:
            return x
        if self.x_kind() == "TIME" or len(xs) > 50000:
            i = int(np.clip(np.searchsorted(xs, x), 1, len(xs) - 1))
            return float(xs[i] if abs(xs[i] - x) < abs(xs[i - 1] - x) else xs[i - 1])
        return float(xs[int(np.nanargmin(np.abs(xs - x)))])

    def set_cursor(self, ci: int, x: float, emit: bool = True, snap: bool = True) -> None:
        if not self.cursors[ci]:
            return
        if snap:
            x = self._snap(x)
        self.cursor_x[ci] = x
        self._blocked = True
        for line in self.cursors[ci]:
            line.setValue(x)
        self._blocked = False
        self._update_legend()
        if emit:
            self.cursorMoved.emit(ci, x)

    def _cursor_dragged(self, ci: int, x: float) -> None:
        if self._blocked:
            return
        self.set_cursor(ci, x)

    def step_cursor(self, ci: int, steps: int) -> None:
        xs = self._first_x()
        if xs is None or self.cursor_x[ci] is None or not self.cursors[ci]:
            return
        i = int(np.clip(np.searchsorted(xs, self.cursor_x[ci]), 0, len(xs) - 1))
        if abs(xs[max(i - 1, 0)] - self.cursor_x[ci]) < abs(xs[i] - self.cursor_x[ci]):
            i = max(i - 1, 0)
        i = int(np.clip(i + steps, 0, len(xs) - 1))
        self.set_cursor(ci, float(xs[i]), snap=False)

    def keyPressEvent(self, ev):
        ci = 1 if (ev.modifiers() & Qt.ShiftModifier and self.cursors[1]) else 0
        big = 10 if ev.modifiers() & Qt.ControlModifier else 1
        if ev.key() == Qt.Key_Left:
            self.step_cursor(ci, -big)
        elif ev.key() == Qt.Key_Right:
            self.step_cursor(ci, big)
        else:
            super().keyPressEvent(ev)

    def value_at(self, d: dict, x: float):
        if not d["ok"] or d["x"] is None or x is None:
            return None
        xs, ys = d["x"], d["y"]
        if len(xs) == 0 or x < xs[0] or x > xs[-1]:
            return None
        return float(np.interp(x, xs, ys))

    def cursor_values(self) -> list:
        """[(name, unit, value at cursor 1, value at cursor 2)] - also used by the tests."""
        out = []
        for d in self._data:
            ch = d["ch"]
            out.append((d["spec"]["name"], ch.unit if ch else "",
                        self.value_at(d, self.cursor_x[0]), self.value_at(d, self.cursor_x[1])))
        return out

    # ------------------------------------------------------------- legend
    @staticmethod
    def _num(v, digits=4) -> str:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return ""
        a = abs(v)
        if a != 0 and (a >= 1e6 or a < 1e-3):
            return f"{v:.4g}"
        return f"{v:.{digits}f}" if a < 1000 else f"{v:.2f}"

    def _update_legend(self) -> None:
        if self.legend is None:
            return
        kind = self.x_kind()
        two = bool(self.cursors[1]) and self.cursor_x[1] is not None
        td = "style='padding:0px 5px;'"
        xd = 3 if kind != "TIME" else 6
        rows = []
        if kind is not None and not self.is_xy() and self.cursor_x[0] is not None:
            xname, xunit = XAXIS[kind]
            cells = (f"<td {td}>{xname}</td><td {td}>{xunit}</td>"
                     f"<td {td} align='right'>{self._num(self.cursor_x[0], xd)}</td>")
            if two:
                cells += (f"<td {td} align='right'>{self._num(self.cursor_x[1], xd)}</td>"
                          f"<td {td} align='right'>"
                          f"{self._num(self.cursor_x[1] - self.cursor_x[0], xd)}</td>")
            rows.append(f"<tr style='color:#202020'>{cells}</tr>")
            extra = self._cursor_extra()
            if extra:
                rows.append(f"<tr style='color:#606060'><td {td}>{extra[0]}</td>"
                            f"<td {td}>{extra[1]}</td>"
                            f"<td {td} align='right'>{extra[2]}</td></tr>")
        for d in self._data:
            c, ch = d["spec"], d["ch"]
            if not c.get("visible", True):
                continue
            color = c["color"] if d["ok"] else "#b0b0b0"
            unit = ch.unit if ch else ""
            name = c["name"]
            if c.get("file", "active") != "active":
                name += f" (F{c['file']})"
            v1 = self.value_at(d, self.cursor_x[0])
            cells = (f"<td {td}>{name}</td><td {td}>{unit}</td>"
                     f"<td {td} align='right'>{self._num(v1)}</td>")
            if two:
                v2 = self.value_at(d, self.cursor_x[1])
                dv = (v2 - v1) if (v1 is not None and v2 is not None) else None
                cells += (f"<td {td} align='right'>{self._num(v2)}</td>"
                          f"<td {td} align='right'>{self._num(dv)}</td>")
            rows.append(f"<tr style='color:{color}'>{cells}</tr>")
        if not rows:
            self.legend.setHtml("<span style='color:#808080'>drop channels here</span>")
        else:
            self.legend.setHtml("<table cellspacing='0' cellpadding='0' "
                                "style='font-size:8.5pt;font-family:monospace'>"
                                + "".join(rows) + "</table>")
        self.legend.place()

    def _cursor_extra(self):
        """Time for crank angle diagrams / crank angle for time diagrams at cursor 1."""
        kind = self.x_kind()
        x = self.cursor_x[0]
        for d in self._data:
            ch = d["ch"]
            if not d["ok"] or ch is None or ch.cyl is None:
                continue
            e = self.session.entry_for(d["spec"].get("file", "active"))
            cr = e.result.cyl.get(ch.cyl) if e else None
            if cr is None or cr.n_cycles == 0:
                continue
            cm = cr.cmap
            if kind == "CA":
                i = min(self.session.cycle, cr.n_cycles - 1)
                t = float(np.interp(cm.period * cm.k[i] + x, cm.phi, cm.t))
                return ("Time", "s", f"{t:.6f}")
            if kind == "TIME":
                ph = float(np.interp(x, cm.t, cm.phi))
                a = ph - cm.period * np.floor((ph + cm.period / 2) / cm.period)
                return (f"CA {cr.name}", "deg", f"{a:.2f}")
        return None

    # --------------------------------------------------------------- zoom
    def _state(self):
        return [[vb.viewRange() for vb in s.vbs] for s in self.strips]

    def _remember_state(self):
        self._last_state = self._state()

    def _manual_range(self, *_):
        if self._last_state is not None:
            self._history.append(self._last_state)
            del self._history[:-30]

    def zoom_back(self) -> None:
        if not self._history:
            self.show_all()
            return
        state = self._history.pop()
        for strip, ranges in zip(self.strips, state):
            for vb, (xr, yr) in zip(strip.vbs, ranges):
                vb.setRange(xRange=xr, yRange=yr, padding=0)

    def show_all(self) -> None:
        self._history = []
        self._apply_ranges()
        for strip in self.strips:
            a = self.spec.get("x", {})
            if not a or a.get("auto", True):
                strip.plot.vb.enableAutoRange(axis=pg.ViewBox.XAxis)

    # ------------------------------------------------------------ editing
    def add_curve(self, ds: str, name: str, strip: int | None = None, axis: int = 0,
                  **kw) -> bool:
        kind = self.x_kind()
        if kind is not None and ds != kind:
            self.session.log(f"{name} belongs to data set {ds}; this diagram shows {kind} data")
            return False
        if strip is None:
            strip = 0
        c = default_curve(ds, name, strip=strip, axis=axis, **kw)
        c["color"] = kw.get("color") or self.next_color(name, strip)
        self.curves.append(c)
        self.spec["strips"] = max(self.spec.get("strips", 1), strip + 1)
        self.cursor_x = [None, None] if kind is None else self.cursor_x
        self.rebuild()
        self.changed.emit()
        return True

    def remove_curve(self, index: int) -> None:
        if 0 <= index < len(self.curves):
            del self.curves[index]
            used = sorted({c["strip"] for c in self.curves})
            remap = {s: i for i, s in enumerate(used)}
            for c in self.curves:
                c["strip"] = remap[c["strip"]]
            self.spec["strips"] = max(1, len(used))
            self.rebuild()
            self.changed.emit()

    def set_spec(self, spec: dict) -> None:
        self.spec = default_spec()
        self.spec.update(copy.deepcopy(spec))
        self.cursor_x = [None, None]
        self.rebuild()
        self.changed.emit()

    def drop_channels(self, refs: list, pos) -> None:
        """Drop on a strip: use it; right border: new y axis; bottom border: new strip."""
        scene_pos = self.view.mapToScene(int(pos.x()), int(pos.y()))
        strip_i, axis = 0, 0
        h = max(1, self.view.height())
        if self.curves and pos.y() > 0.92 * h:
            strip_i = len(self.strips)
        else:
            for i, s in enumerate(self.strips):
                r = s.plot.sceneBoundingRect()
                if r.top() <= scene_pos.y() <= r.bottom():
                    strip_i = i
                    if self.curves and scene_pos.x() > r.left() + 0.88 * r.width():
                        axis = len(s.vbs)
                    break
        for ref in refs:
            self.add_curve(ref["ds"], ref["name"], strip=strip_i, axis=axis)
        self.activated.emit()

    def show_context_menu(self, pos) -> None:
        m = QMenu(self)
        m.addAction("Properties...", self.propertiesRequested.emit)
        m.addSeparator()
        m.addAction("Zoom Back", self.zoom_back)
        m.addAction("Show All", self.show_all)
        a = m.addAction("Cursor 2")
        a.setCheckable(True)
        a.setChecked(bool(self.spec.get("cursor2")))
        a.triggered.connect(self.toggle_cursor2)
        if self.curves:
            sub = m.addMenu("Remove Curve")
            for i, c in enumerate(self.curves):
                sub.addAction(c["name"], lambda i=i: self.remove_curve(i))
        m.addSeparator()
        m.addAction("Copy Diagram", self.copy_image)
        m.addAction("Copy Cursor Values", self.copy_cursor_values)
        m.exec(self.view.mapToGlobal(pos))

    def toggle_cursor2(self, on: bool | None = None) -> None:
        self.spec["cursor2"] = (not self.spec.get("cursor2")) if on is None else bool(on)
        x0 = self.cursor_x[0]
        self.cursor_x = [None, None]
        self.rebuild()
        if x0 is not None:
            self.set_cursor(0, x0, emit=False)
        self.changed.emit()

    # ------------------------------------------------------------- export
    def copy_image(self) -> None:
        QGuiApplication.clipboard().setPixmap(self.grab())

    def copy_cursor_values(self) -> None:
        kind = self.x_kind()
        lines = []
        if kind and self.cursor_x[0] is not None:
            xname, xunit = XAXIS[kind]
            lines.append(f"{xname}\t{xunit}\t{self.cursor_x[0]!r}"
                         + (f"\t{self.cursor_x[1]!r}" if self.cursor_x[1] is not None else ""))
        for name, unit, v1, v2 in self.cursor_values():
            lines.append(f"{name}\t{unit}\t{'' if v1 is None else repr(v1)}"
                         + (f"\t{'' if v2 is None else repr(v2)}" if self.cursor_x[1] is not None else ""))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def export_table(self) -> tuple[list, list]:
        """Data of the visible curves as (header, columns) for a CSV export."""
        header, cols = [], []
        for d in self._data:
            if not d["ok"] or not d["spec"].get("visible", True):
                continue
            x, y = d["x"], d["y"]
            kind = d["spec"]["ds"]
            xn = d["spec"].get("xname") or XAXIS[kind][0]
            header += [f"{xn} ({d['spec']['name']})", d["spec"]["name"]]
            cols += [np.asarray(x, dtype=float), np.asarray(y, dtype=float)]
        return header, cols
