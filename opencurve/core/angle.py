"""Crank angle reconstruction, cycle detection and resampling (specification 5.1-5.3)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.signal import savgol_filter

RESOLUTIONS = (0.1, 0.2, 0.5, 1.0)


class AngleError(ValueError):
    pass


def unwrap_ca(ca: np.ndarray, period: float) -> np.ndarray:
    """Remove the 720 -> 0 wrap of the file crank angle."""
    d = np.diff(ca)
    wraps = np.concatenate(([0.0], np.cumsum(d < -period / 2.0))) * period
    return ca + wraps


def _extrapolating_interp(t, tp, up):
    """np.interp with linear extrapolation using the mean slope of the outer 10 points."""
    phi = np.interp(t, tp, up)
    k = min(10, tp.size - 1)
    lo = t < tp[0]
    if lo.any():
        s = (up[k] - up[0]) / (tp[k] - tp[0])
        phi[lo] = up[0] + (t[lo] - tp[0]) * s
    hi = t > tp[-1]
    if hi.any():
        s = (up[-1] - up[-1 - k]) / (tp[-1] - tp[-1 - k])
        phi[hi] = up[-1] + (t[hi] - tp[-1]) * s
    return phi


def reconstruct_angle(t: np.ndarray, ca: np.ndarray, period: float = 720.0,
                      mode: str = "smooth", smooth_deg: float = 30.0,
                      step_mode: str = "floor") -> tuple[np.ndarray, dict]:
    """Continuous, strictly increasing crank angle for every sample.

    The file angle is usually an integer staircase (1 deg steps, a few samples per
    step).  The angle value N is taken to be reached at the step edge, whose time is
    the midpoint between the last sample of the old and the first of the new step.
    step_mode "round": the file value is the rounded angle, so the edge to the value N
    lies at N - step/2.
    """
    ca = np.asarray(ca, dtype=np.float64)
    info = {"stepped": False, "step_deg": 0.0, "mode": mode}
    if np.isnan(ca).any():
        good = ~np.isnan(ca)
        if good.sum() < 2:
            raise AngleError("crank angle column contains no usable values")
        idx = np.arange(ca.size)
        # interpolate on the unwrapped angle so that gaps across a wrap stay monotonic
        u_good = unwrap_ca(ca[good], period)
        u = np.interp(idx, idx[good], u_good)
    else:
        u = unwrap_ca(ca, period)
    u = np.maximum.accumulate(u)

    edges = np.flatnonzero(np.diff(u) > 0) + 1
    if edges.size < 2:
        raise AngleError("crank angle does not change - cannot detect cycles")

    if edges.size > 0.5 * (u.size - 1):
        # angle changes (almost) every sample: use it directly
        phi = u + np.arange(u.size) * 1e-9
        info["step_deg"] = float(np.median(np.diff(u)[np.diff(u) > 0]))
        return phi, info

    info["stepped"] = True
    te = 0.5 * (t[edges - 1] + t[edges])
    ue = u[edges]
    step = float(np.median(np.diff(ue)))
    info["step_deg"] = step
    if step_mode == "round":
        ue = ue - step / 2.0

    if mode == "smooth" and ue.size >= 7:
        ug = np.arange(ue[0], ue[-1] + step / 2.0, step)
        tg = np.interp(ug, ue, te)
        w = int(round(smooth_deg / step))
        w = max(5, w | 1)
        if w > tg.size:
            w = tg.size if tg.size % 2 else tg.size - 1
        if w >= 5:
            tgs = savgol_filter(tg, w, 2, mode="interp")
            if np.all(np.diff(tgs) > 0):
                return _extrapolating_interp(t, tgs, ug), info
        info["mode"] = "linear"
    return _extrapolating_interp(t, te, ue), info


def auto_resolution(phi: np.ndarray) -> float:
    """Smallest standard grid that is not finer than the sample density."""
    dps = float(np.median(np.diff(phi)))
    for r in RESOLUTIONS:
        if r >= dps:
            return r
    return RESOLUTIONS[-1]


@dataclass
class CycleMap:
    """Maps the time based samples of one cylinder onto crank angle based cycles."""

    period: float
    res: float
    phi: np.ndarray        # per sample: continuous internal angle (0 = firing TDC of cycle 0)
    t: np.ndarray          # per sample: time
    k: np.ndarray          # cycle indices of the complete cycles (integers)
    grid: np.ndarray       # crank angle axis of one cycle, [-period/2, period/2)
    t_start: np.ndarray
    t_end: np.ndarray
    valid: np.ndarray      # bool per cycle
    deg_per_sample: float
    info: dict

    @property
    def n_cycles(self) -> int:
        return int(self.k.size)

    @property
    def n_points(self) -> int:
        return int(self.grid.size)

    @property
    def duration(self) -> np.ndarray:
        return self.t_end - self.t_start

    @property
    def speed(self) -> np.ndarray:
        """Mean speed per cycle [1/min]."""
        return (self.period / 360.0) / self.duration * 60.0

    def targets(self) -> np.ndarray:
        return self.period * self.k[:, None] + self.grid[None, :]

    def grid_time(self) -> np.ndarray:
        """Time of every grid point, shape (n_cycles, n_points)."""
        tg = self.targets()
        return np.interp(tg.ravel(), self.phi, self.t).reshape(tg.shape)

    def resample(self, sig: np.ndarray, method: str = "interp") -> np.ndarray:
        """Signal on the crank angle grid, shape (n_cycles, n_points)."""
        tg = self.targets()
        out = np.interp(tg.ravel(), self.phi, sig).reshape(tg.shape)
        if method == "binmean" and self.deg_per_sample < self.res and self.n_cycles:
            start = self.period * self.k[0] - self.period / 2.0
            g = np.rint((self.phi - start) / self.res).astype(np.int64)
            ntot = self.n_cycles * self.n_points
            ok = (g >= 0) & (g < ntot) & ~np.isnan(sig)
            cnt = np.bincount(g[ok], minlength=ntot)
            sm = np.bincount(g[ok], weights=sig[ok], minlength=ntot)
            have = cnt > 0
            flat = out.ravel()
            flat[have] = sm[have] / cnt[have]
        return out

    def sample_range(self, i: int, a0: float, a1: float) -> slice:
        """Sample index range of cycle i between crank angles a0 and a1."""
        base = self.period * self.k[i]
        lo = int(np.searchsorted(self.phi, base + a0, side="left"))
        hi = int(np.searchsorted(self.phi, base + a1, side="right"))
        return slice(lo, hi)

    def cycle_slice(self, i: int) -> slice:
        return self.sample_range(i, -self.period / 2.0, self.period / 2.0)

    def locate(self, times: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(cycle position in self.k or -1, crank angle within the cycle) for given times."""
        ph = np.interp(times, self.t, self.phi)
        kk = np.floor((ph + self.period / 2.0) / self.period).astype(np.int64)
        ang = ph - self.period * kk
        if self.n_cycles:
            pos = kk - self.k[0]
            pos[(pos < 0) | (pos >= self.n_cycles)] = -1
        else:
            pos = np.full(kk.shape, -1)
        return pos, ang


def build_cycle_map(t: np.ndarray, ca: np.ndarray, period: float = 720.0,
                    ca_offset_deg: float = 0.0, resolution="auto", recon: str = "smooth",
                    smooth_deg: float = 30.0, step_mode: str = "floor") -> CycleMap:
    """Cycle boundaries lie at the internal angle +-period/2, i.e. at gas exchange TDC,
    not at the wrap of the file value."""
    phi, info = reconstruct_angle(t, ca, period, recon, smooth_deg, step_mode)
    phi = phi + ca_offset_deg
    dps = float(np.median(np.diff(phi)))
    if isinstance(resolution, str):
        res = auto_resolution(phi)
    else:
        res = float(resolution)
        if res < dps * 0.999:
            info["resolution_warning"] = (
                f"crank angle grid {res:g} deg is finer than the sample density "
                f"({dps:.3f} deg per sample)")
    half = period / 2.0
    k0 = int(np.ceil((phi[0] + half) / period - 1e-9))
    k1 = int(np.floor((phi[-1] + half) / period + 1e-9)) - 1
    k = np.arange(k0, k1 + 1, dtype=np.int64) if k1 >= k0 else np.zeros(0, dtype=np.int64)
    n = int(round(period / res))
    grid = -half + np.arange(n) * res
    t_start = np.interp(period * k - half, phi, t)
    t_end = np.interp(period * k + half, phi, t)
    dur = t_end - t_start
    valid = np.ones(k.size, dtype=bool)
    if k.size:
        med = np.median(dur)
        valid &= np.abs(dur - med) <= 0.2 * med
    return CycleMap(period=period, res=res, phi=phi, t=t, k=k, grid=grid, t_start=t_start,
                    t_end=t_end, valid=valid, deg_per_sample=dps, info=info)
