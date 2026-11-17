"""Evaluation settings (specification section 4).

All settings live in plain dataclasses so that they can be edited by generic
dialogs, written to TOML and compared for equality.  Field metadata carries the
label, unit and choices used by the GUI.
"""

from __future__ import annotations

import copy
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

CYLINDERS = ("H", "L")
CYL_NAMES = {"H": "High", "L": "Low"}


def _f(default, label, unit="", choices=None, help=""):
    md = {"label": label, "unit": unit, "choices": choices, "help": help}
    if isinstance(default, (list, dict)):
        return field(default_factory=lambda: copy.deepcopy(default), metadata=md)
    return field(default=default, metadata=md)


@dataclass
class EngineConfig:
    name: str = _f("Jenbacher Type 6 (assumed)", "Engine name")
    strokes: int = _f(4, "Number of strokes", choices=[4, 2])
    bore_mm: float = _f(190.0, "Bore", "mm")
    stroke_mm: float = _f(220.0, "Stroke", "mm")
    conrod_mm: float = _f(425.0, "Conrod length", "mm", help="assumed, not published")
    compression: float = _f(11.0, "Compression ratio", "-", help="version dependent")
    pin_offset_mm: float = _f(0.0, "Pin offset", "mm")
    kappa: float = _f(1.32, "Polytropic coefficient (heat release)", "-")
    assumed: bool = _f(True, "Engine data are assumptions")

    @property
    def has_geometry(self) -> bool:
        return (self.bore_mm > 0 and self.stroke_mm > 0 and self.conrod_mm > 0
                and self.compression > 1.0)

    @property
    def period(self) -> float:
        return 720.0 if int(self.strokes) == 4 else 360.0


@dataclass
class CylConfig:
    ca_offset_deg: float = _f(0.0, "TDC offset", "deg", help="added to the file crank angle")
    dmr_sens_mv_per_bar: float = _f(10.0, "Pressure sensitivity", "mV/bar")
    dmr_offset_bar: float = _f(0.0, "Pressure offset", "bar")
    pi_factor: float = _f(1.0, "PI current factor", "A/unit")
    pi_offset: float = _f(0.0, "PI current offset", "A")


@dataclass
class CaConfig:
    resolution_deg: Any = _f("auto", "Crank angle resolution", "deg",
                             choices=["auto", 0.1, 0.2, 0.5, 1.0])
    resample: str = _f("interp", "Resampling", choices=["interp", "binmean"])
    angle_recon: str = _f("smooth", "Angle reconstruction", choices=["smooth", "linear"])
    angle_smooth_deg: float = _f(30.0, "Angle smoothing window", "deg")
    step_mode: str = _f("floor", "Stepped angle value means", choices=["floor", "round"],
                        help="floor: value N is shown from N to N+1 deg; "
                             "round: value N is shown from N-0.5 to N+0.5 deg")
    max_gap_samples: int = _f(3, "Max. interpolated gap", "samples")


@dataclass
class PeggingConfig:
    method: str = _f("thermo", "Zero level correction", choices=["thermo", "fixed", "none"])
    ca1_deg: float = _f(-100.0, "Thermo: first angle", "deg")
    ca2_deg: float = _f(-65.0, "Thermo: second angle", "deg")
    window_deg: float = _f(2.0, "Averaging window", "deg")
    kappa: float = _f(1.32, "Thermo: polytropic coefficient", "-")
    fixed_ca_deg: float = _f(-180.0, "Fixed: angle", "deg")
    fixed_p_bar: float = _f(1.0, "Fixed: pressure", "bar")


@dataclass
class FilterConfig:
    pressure: str = _f("savgol", "Pressure filter",
                       choices=["savgol", "moving_average", "butter", "none"])
    window_deg: float = _f(3.0, "Filter window", "deg")
    butter_cutoff_hz: float = _f(5000.0, "Butterworth cut-off", "Hz")


@dataclass
class HeatReleaseConfig:
    start_deg: float = _f(-30.0, "Start of evaluation", "deg")
    end_deg: float = _f(90.0, "End of evaluation", "deg")


@dataclass
class PiConfig:
    threshold_mode: str = _f("relative", "Threshold mode", choices=["relative", "absolute"])
    threshold: float = _f(0.10, "Threshold", "- / A")
    hysteresis: float = _f(0.05, "Hysteresis", "- / A")
    min_duration_ms: float = _f(0.05, "Min. duration", "ms")
    min_gap_ms: float = _f(0.10, "Min. gap", "ms")
    baseline: Any = _f("auto", "Baseline (offset)", "A", help='"auto" or fixed value')
    hydraulic_delay_ms: float = _f(0.0, "Hydraulic delay", "ms")
    max_events: int = _f(4, "Max. events per cycle")


@dataclass
class KnockConfig:
    enabled: bool = _f(True, "Knock evaluation")
    band_hz: list = _f([2000.0, 10000.0], "Band", "Hz")
    filter_order: int = _f(4, "Filter order")
    window_deg: list = _f([-10.0, 70.0], "Evaluation window", "deg")
    ref_window_deg: list = _f([-90.0, -40.0], "Reference window", "deg")
    threshold_bar: float = _f(1.0, "Knock threshold", "bar")
    threshold_ratio: float = _f(0.0, "Knock ratio threshold (0 = off)", "-")


@dataclass
class OutputConfig:
    cycles: str = _f("all", "Cycle selection", help='"all" or "10:200"')
    csv_delimiter: str = _f(";", "CSV delimiter")
    csv_decimal: str = _f(".", "CSV decimal")
    plots: list = _f(["png"], "Plot formats")


@dataclass
class Config:
    engine: EngineConfig = field(default_factory=EngineConfig)
    cyl: dict = field(default_factory=lambda: {c: CylConfig() for c in CYLINDERS})
    ca: CaConfig = field(default_factory=CaConfig)
    pegging: PeggingConfig = field(default_factory=PeggingConfig)
    filter: FilterConfig = field(default_factory=FilterConfig)
    heat_release: HeatReleaseConfig = field(default_factory=HeatReleaseConfig)
    pi: PiConfig = field(default_factory=PiConfig)
    knock: KnockConfig = field(default_factory=KnockConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    def copy(self) -> "Config":
        return copy.deepcopy(self)

    # ------------------------------------------------------------------ dict
    def to_dict(self) -> dict:
        out: dict = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if f.name == "cyl":
                out["cyl"] = {c: _dc_to_dict(v[c]) for c in CYLINDERS}
            else:
                out[f.name] = _dc_to_dict(v)
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        cfg = cls()
        for f in fields(cfg):
            if f.name not in d:
                continue
            if f.name == "cyl":
                for c in CYLINDERS:
                    if c in d["cyl"]:
                        _dict_to_dc(cfg.cyl[c], d["cyl"][c])
            else:
                _dict_to_dc(getattr(cfg, f.name), d[f.name])
        return cfg


def _dc_to_dict(obj) -> dict:
    return {f.name: copy.deepcopy(getattr(obj, f.name)) for f in fields(obj)}


def _dict_to_dc(obj, d: dict) -> None:
    known = {f.name: f for f in fields(obj)}
    for k, v in d.items():
        if k not in known:
            raise ValueError(f"unknown setting '{k}' in section {type(obj).__name__}")
        cur = getattr(obj, k)
        if isinstance(cur, bool):
            v = bool(v)
        elif isinstance(cur, int) and not isinstance(v, str):
            v = int(v)
        elif isinstance(cur, float) and not isinstance(v, str):
            v = float(v)
        elif isinstance(cur, list):
            v = [float(x) if isinstance(x, (int, float)) else x for x in v]
        setattr(obj, k, v)


# --------------------------------------------------------------------- TOML
def load_config(path: str | Path | None) -> Config:
    if path is None:
        return Config()
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    return Config.from_dict(data)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    raise TypeError(f"cannot write {type(v)} to TOML")


def config_to_toml(cfg: Config) -> str:
    d = cfg.to_dict()
    lines: list[str] = []
    for section, content in d.items():
        if section == "cyl":
            for c in CYLINDERS:
                lines.append(f"[cyl.{c}]")
                lines += [f"{k} = {_toml_value(v)}" for k, v in content[c].items()]
                lines.append("")
        else:
            lines.append(f"[{section}]")
            lines += [f"{k} = {_toml_value(v)}" for k, v in content.items()]
            lines.append("")
    return "\n".join(lines)


def save_config(cfg: Config, path: str | Path) -> None:
    Path(path).write_text(config_to_toml(cfg), encoding="utf-8")


def parse_cycle_selection(sel: str, n: int) -> slice:
    """'all' or 'a:b' (1-based, inclusive) -> slice over cycle indices."""
    sel = (sel or "all").strip().lower()
    if sel in ("all", "", ":"):
        return slice(0, n)
    if ":" in sel:
        a, b = sel.split(":", 1)
        lo = int(a) if a.strip() else 1
        hi = int(b) if b.strip() else n
    else:
        lo = hi = int(sel)
    lo = max(1, lo)
    hi = min(n, hi)
    return slice(lo - 1, max(lo - 1, hi))


def is_settings_dataclass(obj) -> bool:
    return is_dataclass(obj) and not isinstance(obj, type)
