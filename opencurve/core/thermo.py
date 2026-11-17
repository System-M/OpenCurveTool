"""Geometry, zero level correction and pressure based results (specification 5.4-7.3)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.integrate import cumulative_trapezoid
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, savgol_filter, sosfiltfilt

from .config import Config, EngineConfig


# ----------------------------------------------------------------- geometry
@dataclass
class Geometry:
    bore: float      # m
    stroke: float    # m
    conrod: float    # m
    eps: float
    pinoff: float    # m

    @classmethod
    def from_config(cls, e: EngineConfig) -> "Geometry | None":
        if not e.has_geometry:
            return None
        return cls(e.bore_mm / 1000.0, e.stroke_mm / 1000.0, e.conrod_mm / 1000.0,
                   float(e.compression), e.pin_offset_mm / 1000.0)

    @property
    def area(self) -> float:
        return np.pi / 4.0 * self.bore ** 2

    @property
    def vh(self) -> float:
        """Displacement [m3]."""
        return self.area * self.stroke

    @property
    def vc(self) -> float:
        """Compression volume [m3]."""
        return self.vh / (self.eps - 1.0)

    def piston_travel(self, alpha_deg) -> np.ndarray:
        a = np.deg2rad(np.asarray(alpha_deg, dtype=np.float64))
        r, l, e = self.stroke / 2.0, self.conrod, self.pinoff
        return (np.sqrt((l + r) ** 2 - e ** 2) - r * np.cos(a)
                - np.sqrt(l ** 2 - (r * np.sin(a) - e) ** 2))

    def volume(self, alpha_deg) -> np.ndarray:
        """Cylinder volume [m3]; alpha = 0 at TDC."""
        return self.vc + self.area * self.piston_travel(alpha_deg)

    def dvolume(self, alpha_deg) -> np.ndarray:
        """dV/dalpha [m3/deg]."""
        a = np.deg2rad(np.asarray(alpha_deg, dtype=np.float64))
        r, l, e = self.stroke / 2.0, self.conrod, self.pinoff
        x = r * np.sin(a) - e
        ds = r * np.sin(a) + x * r * np.cos(a) / np.sqrt(l ** 2 - x ** 2)
        return self.area * ds * np.pi / 180.0


# ------------------------------------------------------------------ helpers
def _window_mean(p: np.ndarray, grid: np.ndarray, center: float, width: float) -> np.ndarray:
    m = np.abs(grid - center) <= width / 2.0 + 1e-9
    if not m.any():
        m = np.zeros(grid.size, dtype=bool)
        m[int(np.argmin(np.abs(grid - center)))] = True
    return p[:, m].mean(axis=1)


def pegging_offsets(p: np.ndarray, grid: np.ndarray, geo: Geometry | None,
                    cfg: Config) -> tuple[np.ndarray, str, str | None]:
    """Offset [bar] to add per cycle. Returns (offsets, method used, warning)."""
    pc = cfg.pegging
    method, warn = pc.method, None
    if method == "thermo" and geo is None:
        method = "fixed"
        warn = ("zero level correction 'thermo' needs engine geometry - "
                f"using 'fixed' ({pc.fixed_p_bar:g} bar at {pc.fixed_ca_deg:g} deg) instead")
    if method == "none":
        return np.zeros(p.shape[0]), method, warn
    if method == "fixed":
        ref = _window_mean(p, grid, pc.fixed_ca_deg, pc.window_deg)
        return pc.fixed_p_bar - ref, method, warn
    p1 = _window_mean(p, grid, pc.ca1_deg, pc.window_deg)
    p2 = _window_mean(p, grid, pc.ca2_deg, pc.window_deg)
    v1n = float(geo.volume(pc.ca1_deg)) ** pc.kappa
    v2n = float(geo.volume(pc.ca2_deg)) ** pc.kappa
    return (p2 * v2n - p1 * v1n) / (v1n - v2n), "thermo", warn


def filter_pressure(p: np.ndarray, res: float, cfg: Config, speed_rpm: float) -> np.ndarray:
    """Zero phase smoothing used only for the derivative and the heat release."""
    fc = cfg.filter
    kind = fc.pressure
    if kind == "none" or p.shape[0] == 0 or p.shape[1] < 7:
        return p
    if kind == "savgol":
        w = max(5, int(round(fc.window_deg / res)) | 1)
        return savgol_filter(p, w, 3, axis=1, mode="interp")
    if kind == "moving_average":
        w = max(3, int(round(fc.window_deg / res)) | 1)
        return uniform_filter1d(p, w, axis=1, mode="nearest")
    if kind == "butter":
        fs_ca = speed_rpm * 6.0 / res           # grid points per second
        wn = fc.butter_cutoff_hz / (fs_ca / 2.0)
        if not (0.0 < wn < 1.0):
            return p
        sos = butter(4, wn, output="sos")
        return sosfiltfilt(sos, p, axis=1)
    raise ValueError(f"unknown pressure filter '{kind}'")


def _parabolic_peak(y: np.ndarray, idx: np.ndarray, grid: np.ndarray, res: float):
    """Refine the position/value of a maximum with a parabola through 3 points."""
    n = y.shape[1]
    rows = np.arange(y.shape[0])
    i = np.clip(idx, 1, n - 2)
    y0, y1, y2 = y[rows, i - 1], y[rows, i], y[rows, i + 1]
    den = y0 - 2.0 * y1 + y2
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(den < 0, 0.5 * (y0 - y2) / den, 0.0)
    d = np.clip(d, -0.5, 0.5)
    inner = (idx >= 1) & (idx <= n - 2)
    pos = np.where(inner, grid[i] + d * res, grid[idx])
    val = np.where(inner, y1 - 0.25 * (y0 - y2) * d, y[rows, idx])
    return pos, val


def _fit_peak_position(y: np.ndarray, idx: np.ndarray, grid: np.ndarray, res: float,
                       half_width_deg: float = 3.0) -> np.ndarray:
    """Position of a flat maximum from a least squares parabola over +-half_width_deg.

    A 3-point parabola is dominated by noise near a flat pressure peak; the wider fit
    is not.  Falls back to the 3-point result at the borders or for a degenerate fit.
    """
    pos3, _ = _parabolic_peak(y, idx, grid, res)
    n = y.shape[1]
    w = max(2, int(round(half_width_deg / res)))
    if n < 2 * w + 1:
        return pos3
    xr = np.arange(-w, w + 1) * res
    pinv = np.linalg.pinv(np.vander(xr, 3))            # columns: x^2, x, 1
    cols = idx[:, None] + np.arange(-w, w + 1)[None, :]
    inner = (cols[:, 0] >= 0) & (cols[:, -1] <= n - 1)
    yy = y[np.arange(y.shape[0])[:, None], np.clip(cols, 0, n - 1)]
    coef = yy @ pinv.T
    c2, c1 = coef[:, 0], coef[:, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        d = np.where(c2 < 0, -c1 / (2.0 * c2), np.nan)
    ok = inner & ~np.isnan(d) & (np.abs(d) <= w * res)
    return np.where(ok, grid[np.clip(idx, 0, n - 1)] + np.where(ok, d, 0.0), pos3)


def _crossing(x: np.ndarray, grid: np.ndarray, level: float) -> float:
    """First upward crossing of level in x (linear interpolation)."""
    above = np.flatnonzero(x >= level)
    if above.size == 0:
        return np.nan
    j = int(above[0])
    if j == 0:
        return float(grid[0])
    x0, x1 = x[j - 1], x[j]
    f = (level - x0) / (x1 - x0) if x1 != x0 else 0.0
    return float(grid[j - 1] + f * (grid[j] - grid[j - 1]))


def _poly_exponent(p: np.ndarray, v: np.ndarray, grid: np.ndarray, a0: float, a1: float):
    m = (grid >= a0) & (grid <= a1)
    if m.sum() < 3:
        return np.full(p.shape[0], np.nan)
    x = np.log(v[m])
    pp = p[:, m]
    ok = (pp > 0).all(axis=1)
    y = np.log(np.where(pp > 0, pp, 1.0))
    xm = x - x.mean()
    slope = (y * xm).sum(axis=1) / (xm ** 2).sum()
    return np.where(ok, -slope, np.nan)


# ------------------------------------------------------------------ results
def evaluate_pressure(p_raw: np.ndarray, grid: np.ndarray, res: float, speed: np.ndarray,
                      cfg: Config) -> tuple[dict, dict, list]:
    """Pressure based results for all cycles of one cylinder.

    p_raw: scaled pressure [bar] on the crank angle grid, shape (n_cycles, n_points),
    without zero level correction.
    Returns (scalars per cycle, curves, warnings).
    """
    warnings: list[str] = []
    nc = p_raw.shape[0]
    geo = Geometry.from_config(cfg.engine)
    period = cfg.engine.period
    scal: dict = {}
    curves: dict = {}

    offs, method, warn = pegging_offsets(p_raw, grid, geo, cfg)
    if warn:
        warnings.append(warn)
    p = p_raw + offs[:, None]
    scal["POFFS"] = offs
    curves["PCYL"] = p

    med_speed = float(np.nanmedian(speed)) if nc else 0.0
    pf = filter_pressure(p, res, cfg, med_speed)
    dp = np.gradient(pf, res, axis=1) if p.shape[1] > 2 else np.zeros_like(p)
    curves["PFILT"] = pf
    curves["DP"] = dp

    if nc:
        idx = np.nanargmax(np.where(np.isnan(p), -np.inf, p), axis=1)
        _, pmax = _parabolic_peak(p, idx, grid, res)
        scal["PMAX"], scal["APMAX"] = pmax, _fit_peak_position(p, idx, grid, res)
        m = (grid >= -90.0) & (grid <= 90.0)
        gi = np.flatnonzero(m)
        ridx = gi[np.argmax(np.where(np.isnan(dp[:, m]), -np.inf, dp[:, m]), axis=1)]
        scal["RMAX"] = dp[np.arange(nc), ridx]
        scal["ARMAX"] = grid[ridx]
    else:
        for k in ("PMAX", "APMAX", "RMAX", "ARMAX"):
            scal[k] = np.zeros(0)

    if geo is None:
        warnings.append("engine geometry not set - IMEP and heat release are not calculated")
        return scal, curves, warnings

    v = geo.volume(grid)
    dv = geo.dvolume(grid)
    curves["V"] = v * 1000.0                              # litres, 1-D
    scal["IMEP"] = (p * dv).sum(axis=1) * res / geo.vh
    if period == 720.0:
        hp = (grid >= -180.0) & (grid < 180.0)
        scal["IMEPH"] = (p[:, hp] * dv[hp]).sum(axis=1) * res / geo.vh
        scal["IMEPL"] = scal["IMEP"] - scal["IMEPH"]

    kap = cfg.engine.kappa
    q = (kap / (kap - 1.0) * pf * dv + 1.0 / (kap - 1.0) * v * dp) * 1e5     # J/deg
    curves["Q"] = q
    hr = cfg.heat_release
    w = np.flatnonzero((grid >= hr.start_deg - 1e-9) & (grid <= hr.end_deg + 1e-9))
    integ = np.zeros_like(q)
    mfb = np.full_like(q, np.nan)
    names = ("QMAX", "AQMAX", "IMAX", "AI05", "AI10", "AI50", "AI90", "SOC", "BD")
    for k in names:
        scal[k] = np.full(nc, np.nan)
    if w.size >= 3 and nc:
        i0, i1 = int(w[0]), int(w[-1])
        iw = cumulative_trapezoid(q[:, i0:i1 + 1], dx=res, axis=1, initial=0.0)
        integ[:, i0:i1 + 1] = iw
        integ[:, i1 + 1:] = iw[:, -1:]
        gw = grid[i0:i1 + 1]
        qw = q[:, i0:i1 + 1]
        qi = np.argmax(np.where(np.isnan(qw), -np.inf, qw), axis=1)
        scal["AQMAX"], scal["QMAX"] = _parabolic_peak(qw, qi, gw, res)
        for c in range(nc):
            row = iw[c]
            if np.isnan(row).any():
                continue
            jmax = int(np.argmax(row))
            if jmax < 2:
                continue
            jmin = int(np.argmin(row[:jmax + 1]))
            span = row[jmax] - row[jmin]
            if span <= 0 or jmax - jmin < 2:
                continue
            x = (row[jmin:jmax + 1] - row[jmin]) / span
            g = gw[jmin:jmax + 1]
            scal["IMAX"][c] = span
            m = np.clip((integ[c] - row[jmin]) / span, 0.0, 1.0)
            m[:i0 + jmin] = 0.0
            m[i0 + jmax:] = 1.0
            mfb[c] = m
            for lvl, name in ((0.05, "AI05"), (0.10, "AI10"), (0.50, "AI50"), (0.90, "AI90")):
                scal[name][c] = _crossing(x, g, lvl)
        scal["SOC"] = scal["AI05"].copy()
        scal["BD"] = scal["AI90"] - scal["AI10"]
    curves["I"] = integ
    curves["MFB"] = mfb

    scal["POLY_C"] = _poly_exponent(p, v, grid, -100.0, -50.0)
    scal["POLY_E"] = _poly_exponent(p, v, grid, 60.0, 110.0)
    return scal, curves, warnings
