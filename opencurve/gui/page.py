"""A layout page: a sheet with freely placed objects; design mode moves and resizes them."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QFrame, QScrollArea, QVBoxLayout, QWidget

from .diagram import DiagramObject
from .objects import ImageObject, ParamObject, StatsObject, TableObject, TextObject
from .session import Session

OBJECT_TYPES = {
    "diagram": ("Diagram", "2D diagram for crank angle, cycle or time based channels",
                DiagramObject),
    "table": ("Result Table", "Cycle results, one row per cycle", TableObject),
    "stats": ("Statistics Table", "Mean, deviation, min, max and COV of the results",
              StatsObject),
    "params": ("Parameter Table", "Engine and measurement parameters", ParamObject),
    "text": ("Text", "Text with placeholders such as {FILENAME} or {STAT:PMAX_H}", TextObject),
    "image": ("Image", "Picture, e.g. a company logo", ImageObject),
}
GRID = 0.01          # snap grid of the design mode (fraction of the page)
MARGIN = 6           # page margin in pixels
HANDLE = 8


def create_object(session: Session, spec: dict):
    cls = OBJECT_TYPES.get(spec.get("type", ""), (None, None, None))[2]
    if cls is None:
        raise ValueError(f"unknown object type '{spec.get('type')}'")
    return cls(session, spec)


class _Overlay(QWidget):
    """Covers an object in design mode: selection border, move and resize handles."""

    def __init__(self, frame: "ObjectFrame"):
        super().__init__(frame)
        self.frame = frame
        self.setMouseTracking(True)
        self._mode = None
        self._start = QPoint()
        self._rect0 = None

    def _hit(self, pos) -> str:
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        m = HANDLE + 2
        horiz = "l" if x < m else "r" if x > w - m else ""
        vert = "t" if y < m else "b" if y > h - m else ""
        return (vert + horiz) or "move"

    def mouseMoveEvent(self, ev):
        if self._mode is None:
            hit = self._hit(ev.position().toPoint())
            cur = {"move": Qt.SizeAllCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                   "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "tl": Qt.SizeFDiagCursor,
                   "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                   "bl": Qt.SizeBDiagCursor}[hit]
            self.setCursor(cur)
            return
        page = self.frame.page
        pw, ph = page.usable()
        d = ev.globalPosition().toPoint() - self._start
        dx, dy = d.x() / pw, d.y() / ph
        x, y, w, h = self._rect0
        if self._mode == "move":
            x, y = x + dx, y + dy
        else:
            if "l" in self._mode:
                x, w = x + dx, w - dx
            if "r" in self._mode:
                w = w + dx
            if "t" in self._mode:
                y, h = y + dy, h - dy
            if "b" in self._mode:
                h = h + dy
        snap = lambda v: round(v / GRID) * GRID  # noqa: E731
        x, y, w, h = snap(x), snap(y), max(0.05, snap(w)), max(0.04, snap(h))
        x = min(max(0.0, x), 1.0 - w) if self._mode == "move" else max(0.0, x)
        y = min(max(0.0, y), 1.0 - h) if self._mode == "move" else max(0.0, y)
        w, h = min(w, 1.0 - x), min(h, 1.0 - y)
        self.frame.rect = [x, y, w, h]
        page.place(self.frame)

    def mousePressEvent(self, ev):
        self.frame.page.select(self.frame)
        if ev.button() == Qt.LeftButton:
            self._mode = self._hit(ev.position().toPoint())
            self._start = ev.globalPosition().toPoint()
            self._rect0 = list(self.frame.rect)
        elif ev.button() == Qt.RightButton:
            self.frame.page.contextRequested.emit(self.frame, ev.globalPosition().toPoint())

    def mouseReleaseEvent(self, ev):
        if self._mode is not None:
            self._mode = None
            if self._rect0 != self.frame.rect:
                self.frame.page.modified.emit()

    def mouseDoubleClickEvent(self, ev):
        self.frame.page.propertiesRequested.emit(self.frame)

    def paintEvent(self, ev):
        p = QPainter(self)
        sel = self.frame.page.selected is self.frame
        p.setPen(QPen(QColor("#1464c8" if sel else "#9a9a9a"), 2 if sel else 1,
                      Qt.SolidLine if sel else Qt.DashLine))
        p.drawRect(self.rect().adjusted(1, 1, -2, -2))
        if sel:
            p.setBrush(QColor("#1464c8"))
            w, h = self.width(), self.height()
            for x in (0, (w - HANDLE) // 2, w - HANDLE):
                for y in (0, (h - HANDLE) // 2, h - HANDLE):
                    if x == (w - HANDLE) // 2 and y == (h - HANDLE) // 2:
                        continue
                    p.drawRect(x, y, HANDLE - 1, HANDLE - 1)


class ObjectFrame(QFrame):
    """Holds one object widget at a relative position on the page."""

    def __init__(self, page: "Page", obj: QWidget, rect: list):
        super().__init__(page)
        self.page = page
        self.obj = obj
        self.rect = [float(v) for v in rect]
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(obj)
        self.overlay = _Overlay(self)
        self.overlay.setVisible(page.design)
        obj.activated.connect(lambda: page.select(self))
        obj.changed.connect(page.modified)
        obj.propertiesRequested.connect(lambda: page.propertiesRequested.emit(self))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.overlay.setGeometry(self.rect_px())
        self.overlay.raise_()

    def rect_px(self) -> QRect:
        return QRect(0, 0, self.width(), self.height())

    def to_spec(self) -> dict:
        s = self.obj.to_spec()
        s["rect"] = [round(v, 4) for v in self.rect]
        return s


class Page(QWidget):
    modified = Signal()
    selectionChanged = Signal()
    propertiesRequested = Signal(object)            # ObjectFrame
    contextRequested = Signal(object, object)       # ObjectFrame, global position
    cursorMoved = Signal(object, int, float)        # source diagram, cursor index, x

    def __init__(self, session: Session, name: str = "Page 1", parent=None):
        super().__init__(parent)
        self.session = session
        self.name = name
        self.frames: list[ObjectFrame] = []
        self.design = False
        self.selected: ObjectFrame | None = None
        self.link_cursors = True
        self.setAutoFillBackground(False)

    # ----------------------------------------------------------- geometry
    def usable(self) -> tuple[int, int]:
        return max(50, self.width() - 2 * MARGIN), max(50, self.height() - 2 * MARGIN)

    def place(self, frame: ObjectFrame) -> None:
        pw, ph = self.usable()
        x, y, w, h = frame.rect
        wp, hp = max(20, round(w * pw)), max(20, round(h * ph))
        if hasattr(frame.obj, "preferred_size"):
            # e.g. the parameter table: never smaller than its content
            mw, mh = frame.obj.preferred_size()
            wp, hp = max(wp, mw), max(hp, mh)
        frame.setGeometry(MARGIN + round(x * pw), MARGIN + round(y * ph), wp, hp)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        for f in self.frames:
            self.place(f)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("white"))
        p.setPen(QPen(QColor("#8c8c8c"), 1, Qt.DotLine))
        p.drawRect(self.rect().adjusted(2, 2, -3, -3))
        if self.design:
            pw, ph = self.usable()
            p.setPen(QPen(QColor("#c8c8c8"), 1))
            for i in range(0, 21):
                for j in range(0, 21):
                    p.drawPoint(MARGIN + round(i / 20 * pw), MARGIN + round(j / 20 * ph))

    def mousePressEvent(self, ev):
        self.select(None)

    # ------------------------------------------------------------ objects
    def add_object(self, spec: dict, select: bool = False) -> ObjectFrame:
        rect = spec.get("rect") or self.free_rect()
        obj = create_object(self.session, spec)
        frame = ObjectFrame(self, obj, rect)
        self.frames.append(frame)
        self.place(frame)
        frame.show()
        if isinstance(obj, DiagramObject):
            obj.cursorMoved.connect(lambda ci, x, o=obj: self._relay_cursor(o, ci, x))
        if select:
            self.select(frame)
        return frame

    def free_rect(self) -> list:
        """Default position of a new object: staggered so that it does not hide others."""
        k = len(self.frames) % 6
        return [0.05 + 0.03 * k, 0.05 + 0.03 * k, 0.5, 0.45]

    def remove(self, frame: ObjectFrame) -> None:
        if frame in self.frames:
            self.frames.remove(frame)
            if self.selected is frame:
                self.selected = None
            frame.setParent(None)
            frame.deleteLater()
            self.selectionChanged.emit()
            self.modified.emit()

    def select(self, frame: ObjectFrame | None) -> None:
        if frame is not self.selected:
            self.selected = frame
            for f in self.frames:
                f.overlay.update()
            self.selectionChanged.emit()

    def set_design(self, on: bool) -> None:
        self.design = bool(on)
        for f in self.frames:
            f.overlay.setVisible(self.design)
            f.overlay.raise_()
        self.update()

    def diagrams(self) -> list:
        return [f.obj for f in self.frames if isinstance(f.obj, DiagramObject)]

    def active_diagram(self) -> DiagramObject | None:
        if self.selected is not None and isinstance(self.selected.obj, DiagramObject):
            return self.selected.obj
        d = self.diagrams()
        return d[0] if d else None

    def _relay_cursor(self, source: DiagramObject, ci: int, x: float) -> None:
        if not self.link_cursors:
            return
        kind = source.x_kind()
        for d in self.diagrams():
            if d is not source and d.x_kind() == kind and not d.is_xy():
                d.set_cursor(ci, x, emit=False, snap=False)

    # -------------------------------------------------------------- store
    def to_dict(self) -> dict:
        return {"name": self.name, "objects": [f.to_spec() for f in self.frames]}

    def load(self, data: dict) -> None:
        for f in list(self.frames):
            f.setParent(None)
            f.deleteLater()
        self.frames = []
        self.selected = None
        self.name = data.get("name", self.name)
        for spec in data.get("objects", []):
            self.add_object(spec)


class PageView(QScrollArea):
    """Scrollable view on a page; the zoom factor scales the sheet."""

    def __init__(self, page: Page, parent=None):
        super().__init__(parent)
        self.page = page
        self.zoom = 1.0
        self.setWidget(page)
        self.setWidgetResizable(False)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("QScrollArea{background:#aab4c4;border:none;}")

    def set_zoom(self, zoom: float) -> None:
        self.zoom = min(4.0, max(0.25, float(zoom)))
        self._fit()

    def _fit(self):
        vp = self.viewport().size()
        self.page.resize(max(200, int((vp.width() - 12) * self.zoom)),
                         max(150, int((vp.height() - 12) * self.zoom)))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit()
