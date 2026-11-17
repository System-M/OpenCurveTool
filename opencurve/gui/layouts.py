"""Layout files (.oclayout, JSON) and the standard layout."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from .. import APP_SHORT
from .diagram import default_curve

LAYOUT_VERSION = 1
BUILTIN_DIR = Path(__file__).resolve().parent.parent / "layouts"
SUFFIX = ".oclayout"


def user_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    p = Path(base) if base else Path.home() / f".{APP_SHORT}"
    if p.name.lower() != APP_SHORT:
        p = p / APP_SHORT
    return p


def available_layouts() -> list:
    """[(label, path)] of the built-in and the user's layouts."""
    out = []
    for folder, tag in ((BUILTIN_DIR, ""), (user_dir() / "layouts", " (user)")):
        if folder.is_dir():
            for f in sorted(folder.glob(f"*{SUFFIX}")):
                out.append((f.stem + tag, f))
    return out


def _diagram(rect, curves, x=None, title="", **kw) -> dict:
    d = {"type": "diagram", "rect": rect, "curves": curves, "title": title,
         "x": {"auto": True, "min": 0.0, "max": 1.0} if x is None
         else {"auto": False, "min": float(x[0]), "max": float(x[1])}}
    d.update(kw)
    return d


def _c(ds, name, strip=0, axis=0, **kw) -> dict:
    return default_curve(ds, name, strip=strip, axis=axis, **kw)


def standard_layout() -> dict:
    """Seven pages: Indicating Diagram, PI Current Diagram, Cycle Diagram,
    Heat Release Diagram, Knock Diagram, High Low Comparison, Raw Data."""
    hl = ("H", "L")
    pages = []

    # full page diagram with the parameter table placed inside the plot area
    pages.append({"name": "Indicating Diagram", "objects": [
        _diagram([0.0, 0.0, 1.0, 1.0], [_c("CA", f"PCYL_{c}") for c in hl], x=(-100, 130)),
        {"type": "params", "rect": [0.105, 0.035, 0.12, 0.1], "title": "Test Parameter:",
         "names": ["BORE", "STROKE", "CONROD", "EPSILON", "POLY_COEFF", "NRSTROKE"]},
    ]})

    pages.append({"name": "PI Current Diagram", "objects": [
        _diagram([0.0, 0.0, 0.6, 1.0],
                 [_c("CA", f"PI_{c}") for c in hl] + [_c("CA", f"PCYL_{c}", 1) for c in hl],
                 x=(-90, 60)),
        _diagram([0.61, 0.0, 0.39, 0.5],
                 [_c("CY", f"PI_SOE_1_{c}") for c in hl] + [_c("CY", f"PI_DUR_1_{c}", 1) for c in hl],
                 title="Start and duration of energizing"),
        {"type": "table", "rect": [0.61, 0.51, 0.39, 0.49],
         "columns": ["PI_SOE_1_H", "PI_DUR_1_H", "PI_IPK_1_H", "PI_SOE_1_L", "PI_DUR_1_L",
                     "PI_IPK_1_L"]},
    ]})

    pages.append({"name": "Cycle Diagram", "objects": [
        _diagram([0.0, 0.0, 0.55, 1.0],
                 [_c("CY", f"IMEP_{c}") for c in hl] + [_c("CY", f"PMAX_{c}", 1) for c in hl]
                 + [_c("CY", f"AI50_{c}", 2) for c in hl]),
        {"type": "text", "rect": [0.56, 0.0, 0.44, 0.17], "size": 9,
         "text": "{FILENAME} - cycle {CYCLE} of {NCYCLES}\n"
                 "IMEP High {STAT:IMEP_H} bar / Low {STAT:IMEP_L} bar\n"
                 "PMAX High {STAT:PMAX_H} bar / Low {STAT:PMAX_L} bar\n"
                 "AI50 High {STAT:AI50_H} deg / Low {STAT:AI50_L} deg\n"
                 "Knocking cycles High {STAT:KNOCK_FREQ_H} % / Low {STAT:KNOCK_FREQ_L} %"},
        {"type": "stats", "rect": [0.56, 0.18, 0.44, 0.82]},
    ]})

    pages.append({"name": "Heat Release Diagram", "objects": [
        _diagram([0.0, 0.0, 0.62, 1.0],
                 [_c("CA", f"PCYL_{c}") for c in hl] + [_c("CA", f"Q_{c}", 1) for c in hl]
                 + [_c("CA", f"I_{c}", 2) for c in hl], x=(-40, 100)),
        _diagram([0.63, 0.0, 0.37, 0.5],
                 [_c("CA", f"PCYL_{c}", mode="mean", xname=f"V_{c}") for c in hl],
                 title="p-V, mean cycle (log-log)", logx=True,
                 axes={"0:0": {"auto": True, "min": 1.0, "max": 200.0, "log": True}}),
        {"type": "table", "rect": [0.63, 0.51, 0.37, 0.49],
         "columns": ["PMAX_H", "IMEP_H", "AI50_H", "PMAX_L", "IMEP_L", "AI50_L"]},
    ]})

    pages.append({"name": "Knock Diagram", "objects": [
        _diagram([0.0, 0.0, 0.6, 1.0],
                 [_c("CA", f"PCYL_{c}") for c in hl] + [_c("CA", f"PKNK_{c}", 1) for c in hl]
                 + [_c("CA", f"KLS_{c}", 2) for c in hl], x=(-20, 80)),
        _diagram([0.61, 0.0, 0.39, 0.5],
                 [_c("CY", f"KP_PK_{c}") for c in hl] + [_c("CY", f"KLS_PK_{c}", 1) for c in hl],
                 title="Knock peak per cycle: pressure and knock sensor"),
        {"type": "table", "rect": [0.61, 0.51, 0.39, 0.49],
         "columns": ["KP_PK_H", "KNOCK_H", "KLS_PK_H", "KLS_RATIO_H",
                     "KP_PK_L", "KNOCK_L", "KLS_PK_L", "KLS_RATIO_L"]},
    ]})

    pages.append({"name": "High Low Comparison", "objects": [
        _diagram([0.0, 0.0, 1.0, 0.62],
                 [_c("CA", f"PCYL_{c}", mode="envelope") for c in hl]
                 + [_c("CA", f"Q_{c}", 1, mode="mean") for c in hl], x=(-90, 120),
                 title="Mean cycles with min/max envelope"),
        {"type": "table", "rect": [0.0, 0.63, 1.0, 0.37]},
    ]})

    pages.append({"name": "Raw Data", "objects": [
        _diagram([0.0, 0.0, 1.0, 1.0],
                 [_c("TIME", f"P_{c}") for c in hl] + [_c("TIME", f"PI_RM_{c}", 1) for c in hl]
                 + [_c("TIME", f"KLS_{c}", 2) for c in hl]
                 + [_c("TIME", f"CA_{c}", 3) for c in hl]),
    ]})
    return {"version": LAYOUT_VERSION, "name": "Standard", "pages": pages, "formulas": []}


def read_layout(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "pages" not in data:
        raise ValueError("not a layout file")
    return data


def write_layout(data: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(data, indent=1), encoding="utf-8")


def ensure_builtin() -> Path:
    """Write the standard layout file if it is missing; returns its path."""
    path = BUILTIN_DIR / f"Standard{SUFFIX}"
    if not path.is_file():
        try:
            BUILTIN_DIR.mkdir(parents=True, exist_ok=True)
            write_layout(standard_layout(), path)
        except OSError:
            pass
    return path
