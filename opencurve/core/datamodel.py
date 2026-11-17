"""Channel oriented view on a result: data sets CA, CY, TIME, STAT, PAR and formula channels.

GUI independent, so that layouts and formulas can be tested without a display.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

import numpy as np

from .analysis import CURVE_INFO, TIME_INFO, Result, scalar_info
from .config import CYL_NAMES
from .export import ordered_scalars

DATASETS = ("CA", "CY", "TIME", "STAT", "PAR")
DATASET_LABEL = {
    "CA": "CA - Crank Angle", "CY": "CY - Cycle Based", "TIME": "TIME - Time Based",
    "STAT": "STAT - Statistics", "PAR": "PAR - Parameters",
}
XAXIS = {"CA": ("Crank Angle", "deg"), "CY": ("Cycle", "-"), "TIME": ("Time", "s")}
_CA_ORDER = ("PCYL", "PI", "KLS", "PFILT", "DP", "V", "Q", "I", "MFB", "PKNK", "KLSF")
_TIME_ORDER = ("DMR", "P", "PKNK", "PI_RM", "PI", "KLS", "KLSF", "CA", "ALPHA", "SPEED")


class FormulaError(ValueError):
    pass


@dataclass
class ChannelInfo:
    name: str
    unit: str
    desc: str
    ds: str
    value: str = ""          # STAT / PAR: text shown instead of data


@dataclass
class ChannelData:
    name: str
    unit: str
    desc: str
    ds: str
    x: np.ndarray
    y: np.ndarray                 # 1-D, or 2-D (n_cycles, n_points) for cycle resolved CA data
    cyl: str | None = None
    used: np.ndarray | None = None      # CA / CY: cycles that enter the statistics
    stats: dict | None = None           # CA 2-D: mean / min / max / std curves

    @property
    def per_cycle(self) -> bool:
        return self.y.ndim == 2

    @property
    def n_cycles(self) -> int:
        if self.ds == "CA":
            return self.y.shape[0] if self.y.ndim == 2 else 0
        return self.y.size if self.ds == "CY" else 0


@dataclass
class Formula:
    name: str
    expr: str
    unit: str = ""
    desc: str = ""

    def to_dict(self) -> dict:
        return {"name": self.name, "expr": self.expr, "unit": self.unit, "desc": self.desc}


def split_cyl(name: str) -> tuple[str, str | None]:
    """'PCYL_H' -> ('PCYL', 'H'); names without cylinder suffix -> (name, None)."""
    if len(name) > 2 and name[-2] == "_" and name[-1] in CYL_NAMES:
        return name[:-2], name[-1]
    return name, None


# ------------------------------------------------------------------ formulas
_FUNCS = {
    "abs": np.abs, "sqrt": np.sqrt, "exp": np.exp, "log": np.log, "log10": np.log10,
    "sin": np.sin, "cos": np.cos, "tan": np.tan, "minimum": np.minimum, "maximum": np.maximum,
    "clip": np.clip, "gradient": np.gradient, "cumsum": np.cumsum,
}
_REDUCE = {"mean": np.nanmean, "std": np.nanstd, "min": np.nanmin, "max": np.nanmax,
           "median": np.nanmedian}
_CONST = {"pi": np.pi}
_BINOPS = {ast.Add: np.add, ast.Sub: np.subtract, ast.Mult: np.multiply, ast.Div: np.divide,
           ast.Pow: np.power, ast.Mod: np.mod}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def formula_names(expr: str) -> list:
    """Channel names used in an expression (without functions and constants)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"syntax error: {exc.msg}") from exc
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in _FUNCS and node.id not in _REDUCE \
                and node.id not in _CONST and node.id not in names:
            names.append(node.id)
    return names


def evaluate_formula(expr: str, variables: dict):
    """Evaluate an arithmetic expression on numpy arrays. Only a fixed set of
    operators and functions is allowed - no attribute access, no arbitrary calls."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"syntax error: {exc.msg}") from exc

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id in variables:
                return variables[node.id]
            if node.id in _CONST:
                return _CONST[node.id]
            raise FormulaError(f"unknown channel '{node.id}'")
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            with np.errstate(divide="ignore", invalid="ignore"):
                return _BINOPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            args = [ev(a) for a in node.args]
            if node.func.id in _FUNCS:
                with np.errstate(divide="ignore", invalid="ignore"):
                    return _FUNCS[node.func.id](*args)
            if node.func.id in _REDUCE and len(args) == 1:
                a = np.asarray(args[0], dtype=float)
                if a.ndim == 2:      # cycle resolved curve: reduce over the cycles
                    return _REDUCE[node.func.id](a, axis=0)
                return float(_REDUCE[node.func.id](a))
            raise FormulaError(f"unknown function '{node.func.id}'")
        raise FormulaError("only numbers, channel names, + - * / ** % and functions are allowed")

    return ev(tree)


# --------------------------------------------------------------- file entry
@dataclass
class FileEntry:
    """One loaded measurement with its result and the channel access for the GUI."""

    result: Result
    formulas: list = field(default_factory=list)       # shared list of Formula
    _cache: dict = field(default_factory=dict, repr=False)

    @property
    def meas(self):
        return self.result.meas

    @property
    def name(self) -> str:
        return self.result.meas.name

    @property
    def n_cycles(self) -> int:
        return self.result.n_cycles

    def invalidate(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------ listing
    def dataset_count(self, ds: str) -> int:
        if ds == "CA":
            return max((cr.cmap.n_points for cr in self.result.cyl.values()), default=0)
        if ds == "CY":
            return self.n_cycles
        if ds == "TIME":
            return self.meas.n
        return 0

    def channels(self, ds: str) -> list:
        key = ("list", ds)
        if key in self._cache:
            return self._cache[key]
        out: list = []
        res = self.result
        if ds == "CA":
            for base in _CA_ORDER:
                for c, cr in res.cyl.items():
                    if base in cr.curves:
                        unit, desc = CURVE_INFO[base]
                        out.append(ChannelInfo(f"{base}_{c}", unit, f"{desc} {cr.name}", ds))
        elif ds == "CY":
            names: list = []
            for cr in res.cyl.values():
                for n in cr.scalars:
                    if n not in names:
                        names.append(n)
            for base in ordered_scalars(names) + ["VALID", "TSTART"]:
                for c, cr in res.cyl.items():
                    if base in cr.scalars:
                        unit, desc = scalar_info(base)
                        out.append(ChannelInfo(f"{base}_{c}", unit, f"{desc} {cr.name}", ds))
        elif ds == "TIME":
            for base in _TIME_ORDER:
                for c, cr in res.cyl.items():
                    if base in cr.time_channels:
                        unit, desc = TIME_INFO[base]
                        out.append(ChannelInfo(f"{base}_{c}", unit, f"{desc} {cr.name}", ds))
        elif ds == "STAT":
            for c, cr in res.cyl.items():
                for n in ordered_scalars(cr.stats.keys()):
                    st = cr.stats[n]
                    unit, desc = ("%", "share of knocking cycles") if n == "KNOCK_FREQ" \
                        else scalar_info(n)
                    if st["n"] == 0:
                        val = "-"
                    elif np.isnan(st["std"]):
                        val = f"{st['mean']:.5g} (n={st['n']})"
                    else:
                        val = f"{st['mean']:.5g} ± {st['std']:.3g} (n={st['n']})"
                    out.append(ChannelInfo(f"{n}_{c}", unit, f"{desc} {cr.name}", ds, val))
        elif ds == "PAR":
            for n, u, v, d in res.parameters():
                val = v if isinstance(v, str) else f"{v:g}" if isinstance(v, float) else str(v)
                out.append(ChannelInfo(n, u, d, ds, val))
        if ds in ("CA", "CY", "TIME"):
            for f in self.formulas:
                try:
                    if self.formula_dataset(f) == ds:
                        out.append(ChannelInfo(f.name, f.unit, f.desc or f"= {f.expr}", ds))
                except FormulaError:
                    pass
        self._cache[key] = out
        return out

    def has(self, ds: str, name: str) -> bool:
        return any(ch.name == name for ch in self.channels(ds))

    # --------------------------------------------------------------- data
    def get(self, ds: str, name: str) -> ChannelData | None:
        """Channel data or None if the channel does not exist in this measurement."""
        key = (ds, name)
        if key not in self._cache:
            self._cache[key] = self._get(ds, name)
        return self._cache[key]

    def _base(self, ds: str, name: str) -> ChannelData | None:
        base, c = split_cyl(name)
        cr = self.result.cyl.get(c) if c else None
        if cr is None:
            return None
        if ds == "CA" and base in cr.curves:
            unit, desc = CURVE_INFO[base]
            return ChannelData(name, unit, f"{desc} {cr.name}", ds, cr.grid, cr.curves[base],
                               c, cr.used(), cr.curve_stats.get(base))
        if ds == "CY" and base in cr.scalars:
            unit, desc = scalar_info(base)
            return ChannelData(name, unit, f"{desc} {cr.name}", ds,
                               np.arange(1, cr.n_cycles + 1, dtype=float), cr.scalars[base],
                               c, cr.used())
        if ds == "TIME" and base in cr.time_channels:
            unit, desc = TIME_INFO[base]
            return ChannelData(name, unit, f"{desc} {cr.name}", ds, self.meas.time,
                               cr.time_channels[base], c)
        return None

    def _get(self, ds: str, name: str) -> ChannelData | None:
        if ds not in ("CA", "CY", "TIME"):
            return None
        ch = self._base(ds, name)
        if ch is not None:
            return ch
        for f in self.formulas:
            if f.name == name:
                try:
                    return self._formula(f, ds)
                except FormulaError:
                    return None
        return None

    # ----------------------------------------------------------- formulas
    def formula_dataset(self, f: Formula) -> str:
        names = formula_names(f.expr)
        if not names:
            raise FormulaError("the formula uses no channel")
        for ds in ("CY", "CA", "TIME"):
            if all(self._base(ds, n) is not None for n in names):
                return ds
        missing = [n for n in names
                   if not any(self._base(ds, n) is not None for ds in ("CY", "CA", "TIME"))]
        if missing:
            raise FormulaError(f"unknown channel '{missing[0]}'")
        raise FormulaError("all channels of a formula must belong to the same data set")

    def _formula(self, f: Formula, ds: str) -> ChannelData:
        if self.formula_dataset(f) != ds:
            raise FormulaError("wrong data set")
        chans = {n: self._base(ds, n) for n in formula_names(f.expr)}
        first = next(iter(chans.values()))
        variables = {}
        if ds == "TIME":
            variables = {n: c.y for n, c in chans.items()}
            x, used, cyl = first.x, None, None
        else:
            # High and Low can differ by one cycle: use the common number of cycles
            n = min(c.y.shape[0] for c in chans.values() if c.y.ndim == (2 if ds == "CA" else 1)
                    ) if any(c.y.ndim == (2 if ds == "CA" else 1) for c in chans.values()) else 0
            for nm, c in chans.items():
                variables[nm] = c.y[:n] if c.y.ndim == (2 if ds == "CA" else 1) else c.y
            used = None
            for c in chans.values():
                if c.used is not None:
                    used = c.used[:n] if used is None else (used & c.used[:n])
            x = first.x if ds == "CA" else np.arange(1, n + 1, dtype=float)
            cyls = {c.cyl for c in chans.values()}
            cyl = cyls.pop() if len(cyls) == 1 else None
        y = np.asarray(evaluate_formula(f.expr, variables), dtype=float)
        if y.ndim == 0:
            y = np.full(x.shape, float(y))
        if ds == "CA" and y.ndim == 2 and y.shape[1] != x.size:
            raise FormulaError("result does not fit the crank angle axis")
        if y.ndim == 1 and y.size != x.size:
            raise FormulaError("result does not fit the axis of the data set")
        stats = None
        if y.ndim == 2 and used is not None and used.any():
            a = y[used]
            stats = {"mean": np.nanmean(a, axis=0), "min": np.nanmin(a, axis=0),
                     "max": np.nanmax(a, axis=0), "std": np.nanstd(a, axis=0)}
        return ChannelData(f.name, f.unit, f.desc or f"= {f.expr}", ds, x, y, cyl, used, stats)

    def check_formula(self, f: Formula) -> str:
        """Returns the data set of a valid formula, raises FormulaError otherwise."""
        if not NAME_RE.match(f.name or ""):
            raise FormulaError("the name must start with a letter and contain only "
                               "letters, digits and '_'")
        for ds in ("CY", "CA", "TIME"):
            if self._base(ds, f.name) is not None:
                raise FormulaError(f"'{f.name}' is already the name of a measured channel")
        ds = self.formula_dataset(f)
        self._formula(f, ds)
        return ds
