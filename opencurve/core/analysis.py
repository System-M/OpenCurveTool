"""Complete evaluation of one measurement: cycles, pressure, knock, PI, statistics."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from .angle import AngleError, CycleMap, build_cycle_map
from .config import CYL_NAMES, CYLINDERS, Config, parse_cycle_selection
from .io import Measurement
from .knock import evaluate_kls, evaluate_knock
from .pi import evaluate_pi
from .thermo import Geometry, evaluate_pressure

# name -> (unit, description); cylinder suffix is appended by the data model
SCALAR_INFO = {
    "SPEED": ("1/min", "mean engine speed of the cycle"),
    "TSTART": ("s", "start time of the cycle"),
    "VALID": ("-", "cycle is valid (1) or excluded (0)"),
    "PMAX": ("bar", "peak cylinder pressure"),
    "APMAX": ("deg", "crank angle of peak pressure"),
    "RMAX": ("bar/deg", "maximum pressure rise"),
    "ARMAX": ("deg", "crank angle of maximum pressure rise"),
    "POFFS": ("bar", "zero level correction"),
    "IMEP": ("bar", "indicated mean effective pressure"),
    "IMEPH": ("bar", "IMEP high pressure loop"),
    "IMEPL": ("bar", "IMEP gas exchange loop"),
    "QMAX": ("J/deg", "maximum rate of heat release"),
    "AQMAX": ("deg", "crank angle of maximum heat release"),
    "IMAX": ("J", "integral heat release"),
    "AI05": ("deg", "5 % mass fraction burned"),
    "AI10": ("deg", "10 % mass fraction burned"),
    "AI50": ("deg", "50 % mass fraction burned"),
    "AI90": ("deg", "90 % mass fraction burned"),
    "SOC": ("deg", "start of combustion (AI05)"),
    "BD": ("deg", "burn duration AI10-AI90"),
    "POLY_C": ("-", "polytropic exponent compression"),
    "POLY_E": ("-", "polytropic exponent expansion"),
    "KP_PK": ("bar", "knock peak"),
    "AKP_PK": ("deg", "crank angle of knock peak"),
    "KP_INT": ("bar*deg", "knock integral"),
    "KP_REF": ("bar", "knock reference peak (noise)"),
    "KP_RATIO": ("-", "knock peak / reference"),
    "KNOCK": ("-", "knocking cycle (1) or not (0)"),
    "KLS_PK": ("V", "knock sensor peak in the knock band"),
    "AKLS_PK": ("deg", "crank angle of knock sensor peak"),
    "KLS_INT": ("V*deg", "knock sensor integral"),
    "KLS_REF": ("V", "knock sensor reference peak (noise)"),
    "KLS_RATIO": ("-", "knock sensor peak / reference"),
    "PI_N": ("-", "number of PI events"),
    "PI_OFFS": ("A", "PI current offset (baseline)"),
    "IGD": ("deg", "ignition delay SOC - PI_SOE_1"),
    "IGD_T": ("ms", "ignition delay SOC - PI_SOE_1"),
}
EVENT_INFO = {
    "PI_SOE": ("deg", "PI start of energizing"),
    "PI_EOE": ("deg", "PI end of energizing"),
    "PI_TSOE": ("s", "PI start of energizing (time)"),
    "PI_TEOE": ("s", "PI end of energizing (time)"),
    "PI_DUR": ("ms", "PI energizing duration"),
    "PI_DURA": ("deg", "PI energizing duration (angle)"),
    "PI_IPK": ("A", "PI peak current"),
    "PI_TPK": ("ms", "PI time to peak current"),
    "PI_IHOLD": ("A", "PI hold current"),
    "PI_RISE": ("ms", "PI rise time 10-90 %"),
    "PI_CHG": ("A*ms", "PI current integral"),
    "PI_SOI": ("deg", "PI hydraulic start (SOE + delay)"),
}
CURVE_INFO = {
    "PCYL": ("bar", "cylinder pressure"),
    "PFILT": ("bar", "cylinder pressure, filtered"),
    "DP": ("bar/deg", "pressure rise"),
    "V": ("l", "cylinder volume"),
    "Q": ("J/deg", "rate of heat release"),
    "I": ("J", "integral heat release"),
    "MFB": ("-", "mass fraction burned"),
    "PI": ("A", "PI current, offset corrected"),
    "PKNK": ("bar", "cylinder pressure, knock band"),
    "KLS": ("V", "knock sensor signal"),
    "KLSF": ("V", "knock sensor signal, knock band"),
}
TIME_INFO = {
    "DMR": ("V", "pressure raw signal"),
    "P": ("bar", "cylinder pressure, zero level corrected"),
    "PI_RM": ("A", "PI current raw signal"),
    "PI": ("A", "PI current, offset corrected"),
    "CA": ("deg", "crank angle from file"),
    "ALPHA": ("deg", "reconstructed crank angle, 0 = firing TDC"),
    "SPEED": ("1/min", "engine speed"),
    "PKNK": ("bar", "cylinder pressure, knock band"),
    "KLS": ("V", "knock sensor signal"),
    "KLSF": ("V", "knock sensor signal, knock band"),
}


def scalar_info(name: str) -> tuple[str, str]:
    if name in SCALAR_INFO:
        return SCALAR_INFO[name]
    m = re.match(r"^(PI_[A-Z]+)_(\d+)$", name)
    if m and m.group(1) in EVENT_INFO:
        u, d = EVENT_INFO[m.group(1)]
        return u, f"{d}, event {m.group(2)}"
    return "", ""


def describe(values: np.ndarray) -> dict:
    """Statistics over the valid cycles (specification section 9)."""
    v = np.asarray(values, dtype=np.float64)
    v = v[~np.isnan(v)]
    n = int(v.size)
    if n == 0:
        return {"mean": np.nan, "std": np.nan, "min": np.nan, "max": np.nan,
                "cov": np.nan, "n": 0}
    mean = float(v.mean())
    std = float(v.std(ddof=1)) if n > 1 else 0.0
    cov = abs(std / mean) * 100.0 if mean != 0 else np.nan
    return {"mean": mean, "std": std, "min": float(v.min()), "max": float(v.max()),
            "cov": cov, "n": n}


@dataclass
class CylResult:
    cyl: str
    cmap: CycleMap
    has_dmr: bool
    has_pi: bool
    has_kls: bool = False
    scalars: dict = field(default_factory=dict)       # name -> array per cycle
    curves: dict = field(default_factory=dict)        # name -> (n_cycles, n_points) or (n_points,)
    time_channels: dict = field(default_factory=dict) # name -> array per sample
    valid: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    selected: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    excluded: set = field(default_factory=set)        # manually excluded cycle positions
    stats: dict = field(default_factory=dict)
    curve_stats: dict = field(default_factory=dict)   # name -> dict(mean,min,max,std)
    events: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return CYL_NAMES[self.cyl]

    @property
    def n_cycles(self) -> int:
        return self.cmap.n_cycles

    @property
    def grid(self) -> np.ndarray:
        return self.cmap.grid

    def used(self) -> np.ndarray:
        """Cycles that enter the statistics."""
        m = self.valid & self.selected
        if self.excluded:
            idx = [i for i in self.excluded if 0 <= i < m.size]
            m = m.copy()
            m[idx] = False
        return m

    def update_stats(self) -> None:
        used = self.used()
        self.scalars["VALID"] = used.astype(float)
        self.stats = {}
        for name, arr in self.scalars.items():
            if name in ("VALID", "TSTART"):
                continue
            st = describe(arr[used])
            if name == "KP_PK" and st["n"]:
                v = arr[used]
                st["p95"] = float(np.nanpercentile(v, 95))
            self.stats[name] = st
        if "KNOCK" in self.scalars:
            k = self.scalars["KNOCK"][used]
            k = k[~np.isnan(k)]
            self.stats["KNOCK_FREQ"] = {
                "mean": float(k.mean() * 100.0) if k.size else np.nan, "std": np.nan,
                "min": np.nan, "max": np.nan, "cov": np.nan, "n": int(k.size)}
        self.curve_stats = {}
        for name, arr in self.curves.items():
            if arr.ndim != 2:
                continue
            a = arr[used]
            if a.shape[0] == 0:
                continue
            self.curve_stats[name] = {
                "mean": np.nanmean(a, axis=0), "min": np.nanmin(a, axis=0),
                "max": np.nanmax(a, axis=0),
                "std": np.nanstd(a, axis=0, ddof=1) if a.shape[0] > 1 else np.zeros(a.shape[1])}


@dataclass
class Result:
    meas: Measurement
    cfg: Config
    cyl: dict = field(default_factory=dict)       # 'H' / 'L' -> CylResult
    warnings: list = field(default_factory=list)
    info: dict = field(default_factory=dict)

    @property
    def n_cycles(self) -> int:
        return max((c.n_cycles for c in self.cyl.values()), default=0)

    def parameters(self) -> list:
        """Rows (name, unit, value, description) of the PAR data set."""
        e = self.cfg.engine
        rows = [
            ("FILENAME", "", self.meas.name, "file name"),
            ("ENGINE", "", e.name, "engine name"),
            ("NRSTROKE", "", int(e.strokes), "number of strokes"),
            ("BORE", "mm", e.bore_mm, "bore diameter"),
            ("STROKE", "mm", e.stroke_mm, "stroke length"),
            ("CONROD", "mm", e.conrod_mm, "conrod length"),
            ("EPSILON", "", e.compression, "compression"),
            ("PINOFF", "mm", e.pin_offset_mm, "pinoff"),
            ("POLY_COEFF", "", e.kappa, "polytropic coefficient"),
            ("FSAMPLE", "Hz", round(self.meas.fs, 3), "sample rate"),
            ("DURATION", "s", round(self.meas.duration, 6), "duration of measurement"),
            ("NSAMPLES", "", self.meas.n, "number of samples"),
            ("CARES", "deg", self.info.get("res", np.nan), "crank angle resolution"),
        ]
        for c in CYLINDERS:
            cc = self.cfg.cyl[c]
            rows += [
                (f"CAOFFS_{c}", "deg", cc.ca_offset_deg, f"TDC offset {CYL_NAMES[c]}"),
                (f"PSENS_{c}", "mV/bar", cc.dmr_sens_mv_per_bar,
                 f"pressure sensitivity {CYL_NAMES[c]}"),
                (f"NCYCLES_{c}", "", self.cyl[c].n_cycles if c in self.cyl else 0,
                 f"number of cycles {CYL_NAMES[c]}"),
            ]
        return rows


def scale_pressure(volts: np.ndarray, cfg: Config, cyl: str) -> np.ndarray:
    cc = cfg.cyl[cyl]
    return volts * (1000.0 / cc.dmr_sens_mv_per_bar) + cc.dmr_offset_bar


def analyze(meas: Measurement, cfg: Config, progress=None) -> Result:
    """Run the complete evaluation.  progress(fraction, text) is optional."""
    def step(frac, text):
        if progress is not None:
            progress(frac, text)

    res = Result(meas=meas, cfg=cfg, warnings=list(meas.warnings))
    period = cfg.engine.period
    cyls = meas.cylinders()
    res.info["geometry"] = Geometry.from_config(cfg.engine) is not None
    if cfg.engine.assumed and res.info["geometry"]:
        res.warnings.append("engine data are assumptions (assumed engine data) - "
                            "thermodynamic results are indicative only")

    for ci, c in enumerate(cyls):
        base = ci / max(1, len(cyls))
        span = 1.0 / max(1, len(cyls))
        label = CYL_NAMES[c]
        step(base, f"{label}: crank angle")
        try:
            cmap = build_cycle_map(meas.time, meas.raw[f"CA_{c}"], period,
                                   cfg.cyl[c].ca_offset_deg, cfg.ca.resolution_deg,
                                   cfg.ca.angle_recon, cfg.ca.angle_smooth_deg,
                                   cfg.ca.step_mode)
        except AngleError as exc:
            res.warnings.append(f"{label}: {exc}")
            continue
        if "resolution_warning" in cmap.info:
            res.warnings.append(f"{label}: {cmap.info['resolution_warning']}")
        res.info["res"] = cmap.res
        nc = cmap.n_cycles
        cr = CylResult(cyl=c, cmap=cmap, has_dmr=meas.has(f"DMR_{c}"),
                       has_pi=meas.has(f"PI_RM_{c}"), has_kls=meas.has(f"KLS_{c}"))
        cr.valid = cmap.valid.copy()
        cr.scalars["SPEED"] = cmap.speed
        cr.scalars["TSTART"] = cmap.t_start
        cr.time_channels["CA"] = meas.raw[f"CA_{c}"]
        cr.time_channels["ALPHA"] = cmap.phi - period * np.floor(
            (cmap.phi + period / 2.0) / period)
        cr.time_channels["SPEED"] = np.gradient(cmap.phi, meas.time) / 6.0
        if nc == 0:
            res.warnings.append(f"{label}: no complete cycle in the measurement")
        elif (~cmap.valid).any():
            res.warnings.append(f"{label}: {int((~cmap.valid).sum())} cycle(s) with "
                                "implausible duration marked invalid")

        # ------------------------------------------------------ pressure
        if cr.has_dmr:
            step(base + 0.15 * span, f"{label}: pressure")
            volts = meas.raw[f"DMR_{c}"]
            p_time = scale_pressure(volts, cfg, c)
            cr.time_channels["DMR"] = volts
            p_grid = cmap.resample(p_time, cfg.ca.resample)
            bad = np.isnan(p_grid).any(axis=1)
            if bad.any():
                cr.valid &= ~bad
                res.warnings.append(f"{label}: {int(bad.sum())} cycle(s) with gaps in the "
                                    "pressure signal marked invalid")
            scal, curves, warns = evaluate_pressure(p_grid, cmap.grid, cmap.res,
                                                    cmap.speed, cfg)
            cr.scalars.update(scal)
            cr.curves.update(curves)
            for w in warns:
                if w not in res.warnings:
                    res.warnings.append(w)
            # zero level corrected pressure per sample (offset of the sample's cycle)
            offs_s = np.zeros(meas.n)
            if nc:
                pos, _ = cmap.locate(meas.time)
                pos = np.where(pos < 0, np.where(meas.time < cmap.t_start[0], 0, nc - 1), pos)
                offs_s = scal["POFFS"][pos]
            cr.time_channels["P"] = p_time + offs_s

            if cfg.knock.enabled:
                step(base + 0.45 * span, f"{label}: knock")
                kscal, pk, warns = evaluate_knock(p_time, cmap, cfg, meas.fs)
                for w in warns:
                    if w not in res.warnings:
                        res.warnings.append(w)
                if pk is not None:
                    cr.scalars.update(kscal)
                    cr.time_channels["PKNK"] = pk
                    cr.curves["PKNK"] = cmap.resample(pk)

        # ------------------------------------------------- knock sensor
        if cr.has_kls:
            step(base + 0.55 * span, f"{label}: knock sensor")
            kls = meas.raw[f"KLS_{c}"]
            cr.time_channels["KLS"] = kls
            cr.curves["KLS"] = cmap.resample(kls)
            if cfg.knock.enabled:
                kscal, kf, warns = evaluate_kls(kls, cmap, cfg, meas.fs)
                for w in warns:
                    if w and w not in res.warnings:
                        res.warnings.append(w)
                if kf is not None:
                    cr.scalars.update(kscal)
                    cr.time_channels["KLSF"] = kf
                    cr.curves["KLSF"] = cmap.resample(kf)

        # ------------------------------------------------------------ PI
        if cr.has_pi:
            step(base + 0.65 * span, f"{label}: PI current")
            cur = (meas.raw[f"PI_RM_{c}"] * cfg.cyl[c].pi_factor + cfg.cyl[c].pi_offset)
            cr.time_channels["PI_RM"] = meas.raw[f"PI_RM_{c}"]
            pscal, x, events, warns = evaluate_pi(meas.time, cur, cmap, cfg, meas.fs)
            cr.events = events
            cr.scalars.update(pscal)
            cr.time_channels["PI"] = x
            cr.curves["PI"] = cmap.resample(x)
            for w in warns:
                res.warnings.append(f"{label}: {w}")
            if cr.has_dmr and "SOC" in cr.scalars and "PI_SOE_1" in cr.scalars and nc:
                soc = cr.scalars["SOC"]
                soe = cr.scalars["PI_SOE_1"]
                cr.scalars["IGD"] = soc - soe
                t_soc = np.full(nc, np.nan)
                ok = ~np.isnan(soc)
                t_soc[ok] = np.interp(period * cmap.k[ok] + soc[ok], cmap.phi, meas.time)
                cr.scalars["IGD_T"] = (t_soc - cr.scalars["PI_TSOE_1"]) * 1000.0

        sel = np.zeros(nc, dtype=bool)
        sel[parse_cycle_selection(cfg.output.cycles, nc)] = True
        cr.selected = sel
        cr.update_stats()
        res.cyl[c] = cr

    step(1.0, "done")
    return res
