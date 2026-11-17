"""PI current evaluation (specification section 8).

Edge times are threshold crossings interpolated between two samples, using the
original time stamps.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .angle import CycleMap
from .config import Config

EVENT_FIELDS = ("PI_SOE", "PI_EOE", "PI_TSOE", "PI_TEOE", "PI_DUR", "PI_DURA", "PI_IPK",
                "PI_TPK", "PI_IHOLD", "PI_RISE", "PI_CHG")


@dataclass
class PiEvent:
    cycle: int          # position in the cycle map, -1 if outside a complete cycle
    t_soe: float
    t_eoe: float
    a_soe: float
    a_eoe: float
    i0: int             # first sample above threshold
    i1: int             # first sample below threshold again
    ipk: float = np.nan
    tpk_ms: float = np.nan
    ihold: float = np.nan
    rise_ms: float = np.nan
    chg: float = np.nan

    @property
    def dur_ms(self) -> float:
        return (self.t_eoe - self.t_soe) * 1000.0


def _schmitt(x: np.ndarray, hi: np.ndarray, lo: np.ndarray) -> np.ndarray:
    """Vectorised Schmitt trigger: 1 above hi, 0 below lo, otherwise the previous state."""
    s = np.where(x > hi, 1, np.where(x < lo, 0, -1)).astype(np.int8)
    idx = np.where(s >= 0, np.arange(s.size), 0)
    np.maximum.accumulate(idx, out=idx)
    s = s[idx]
    s[s < 0] = 0
    return s


def _cross_time(t, x, i, level) -> float:
    """Time at which x crosses level between samples i-1 and i."""
    x0, x1 = x[i - 1], x[i]
    if x1 == x0:
        return float(t[i])
    f = min(1.0, max(0.0, (level - x0) / (x1 - x0)))
    return float(t[i - 1] + f * (t[i] - t[i - 1]))


def evaluate_pi(t: np.ndarray, cur: np.ndarray, cmap: CycleMap, cfg: Config,
                fs: float) -> tuple[dict, np.ndarray, list, list]:
    """Returns (scalars per cycle, offset corrected current per sample, events, warnings)."""
    pc = cfg.pi
    warnings: list[str] = []
    nc = cmap.n_cycles
    n = cur.size
    x_in = cur
    nan = np.isnan(x_in)
    if nan.any():
        idx = np.arange(n)
        x_in = np.interp(idx, idx[~nan], x_in[~nan])

    # ---- per cycle baseline, noise, peak -> per sample threshold
    if nc:
        bounds = [cmap.cycle_slice(i) for i in range(nc)]
    else:
        bounds = [slice(0, n)]
        warnings.append("no complete cycle - PI events are evaluated over the whole file")
    nseg = len(bounds)
    base = np.zeros(nseg)
    noise = np.zeros(nseg)
    peak = np.zeros(nseg)
    fixed = None if isinstance(pc.baseline, str) else float(pc.baseline)
    for i, sl in enumerate(bounds):
        seg = x_in[sl]
        if seg.size == 0:
            continue
        b = float(np.median(seg)) if fixed is None else fixed
        base[i] = b
        noise[i] = 1.4826 * float(np.median(np.abs(seg - np.median(seg))))
        peak[i] = float(seg.max() - b)

    # segment index per sample (samples outside the complete cycles use the nearest one)
    seg_of = np.zeros(n, dtype=np.int64)
    for i, sl in enumerate(bounds):
        seg_of[sl] = i
    if nc:
        seg_of[:bounds[0].start] = 0
        seg_of[bounds[-1].stop:] = nseg - 1

    x = x_in - base[seg_of]
    if pc.threshold_mode == "relative":
        thr = pc.threshold * peak
        hyst = pc.hysteresis * peak
    else:
        thr = np.full(nseg, float(pc.threshold))
        hyst = np.full(nseg, float(pc.hysteresis))
    # cycles without a clear pulse: no detection
    quiet = (peak <= np.maximum(6.0 * noise, 1e-9)) | (thr <= 0)
    thr = np.where(quiet, np.inf, thr)
    thr_lo = np.where(quiet, np.inf, np.maximum(thr - hyst, 0.2 * np.where(quiet, 1.0, thr)))
    hi_s, lo_s = thr[seg_of], thr_lo[seg_of]

    state = _schmitt(x, hi_s, lo_s)
    d = np.diff(state)
    rises = np.flatnonzero(d == 1) + 1
    falls = np.flatnonzero(d == -1) + 1
    if state[0] == 1 and falls.size:
        falls = falls[1:] if (rises.size == 0 or falls[0] < rises[0]) else falls
    m = min(rises.size, falls.size)
    rises, falls = rises[:m], falls[:m]

    raw: list[tuple] = []
    for i, j in zip(rises, falls):
        level = hi_s[i]
        t_r = _cross_time(t, x, i, level)
        seg = x[i:j + 1]
        above = np.flatnonzero(seg >= level)
        mlast = i + int(above[-1]) if above.size else i
        t_f = _cross_time(t, x, min(mlast + 1, n - 1), level)
        raw.append([t_r, t_f, int(i), int(j)])

    # merge short gaps, drop short events
    merged: list[list] = []
    for ev in raw:
        if merged and (ev[0] - merged[-1][1]) * 1000.0 < pc.min_gap_ms:
            merged[-1][1] = ev[1]
            merged[-1][3] = ev[3]
        else:
            merged.append(ev)
    merged = [ev for ev in merged if (ev[1] - ev[0]) * 1000.0 >= pc.min_duration_ms]

    events: list[PiEvent] = []
    if merged:
        tr = np.array([e[0] for e in merged])
        tf = np.array([e[1] for e in merged])
        pos, a_r = cmap.locate(tr)
        ph_f = np.interp(tf, cmap.t, cmap.phi)
        ph_r = np.interp(tr, cmap.t, cmap.phi)
        a_f = a_r + (ph_f - ph_r)
        prev_end = 0
        for (t_r, t_f, i, j), p_, ar, af in zip(merged, pos, a_r, a_f):
            ev = PiEvent(int(p_), t_r, t_f, float(ar), float(af), i, j)
            seg = x[i:j + 1]
            jp = int(np.argmax(seg))
            ev.ipk = float(seg[jp])
            ev.tpk_ms = (t[i + jp] - t_r) * 1000.0
            half = seg[seg.size // 2:]
            ev.ihold = float(np.median(half)) if half.size else np.nan
            ev.chg = float(np.trapezoid(seg, t[i:j + 1])) * 1000.0
            # rise time 10 -> 90 % of the peak on the leading flank
            lo_i = max(prev_end, i - 400, 1)
            flank = x[lo_i:i + jp + 1]
            a90 = np.flatnonzero(flank >= 0.9 * ev.ipk)
            if a90.size:
                k90 = lo_i + int(a90[0])
                below = np.flatnonzero(x[lo_i:k90 + 1] <= 0.1 * ev.ipk)
                if below.size and k90 > lo_i:
                    k10 = lo_i + int(below[-1])
                    t10 = _cross_time(t, x, min(k10 + 1, n - 1), 0.1 * ev.ipk)
                    t90 = _cross_time(t, x, max(k90, 1), 0.9 * ev.ipk)
                    ev.rise_ms = (t90 - t10) * 1000.0
            prev_end = j
            events.append(ev)

    # ---- per cycle table
    scal: dict = {}
    counts = np.zeros(nc, dtype=np.int64)
    for ev in events:
        if ev.cycle >= 0:
            counts[ev.cycle] += 1
    kmax = int(min(pc.max_events, counts.max())) if nc and counts.size else 0
    if nc and counts.max(initial=0) > pc.max_events:
        warnings.append(f"more than {pc.max_events} PI events in a cycle - "
                        "only the first ones are listed")
    scal["PI_N"] = counts.astype(float)
    scal["PI_OFFS"] = base[:nc].copy() if nc else np.zeros(0)
    for k in range(1, kmax + 1):
        for f in EVENT_FIELDS:
            scal[f"{f}_{k}"] = np.full(nc, np.nan)
        if pc.hydraulic_delay_ms > 0:
            scal[f"PI_SOI_{k}"] = np.full(nc, np.nan)
    seen = np.zeros(nc, dtype=np.int64)
    for ev in events:
        c = ev.cycle
        if c < 0:
            continue
        seen[c] += 1
        k = int(seen[c])
        if k > kmax:
            continue
        scal[f"PI_SOE_{k}"][c] = ev.a_soe
        scal[f"PI_EOE_{k}"][c] = ev.a_eoe
        scal[f"PI_TSOE_{k}"][c] = ev.t_soe
        scal[f"PI_TEOE_{k}"][c] = ev.t_eoe
        scal[f"PI_DUR_{k}"][c] = ev.dur_ms
        scal[f"PI_DURA_{k}"][c] = ev.a_eoe - ev.a_soe
        scal[f"PI_IPK_{k}"][c] = ev.ipk
        scal[f"PI_TPK_{k}"][c] = ev.tpk_ms
        scal[f"PI_IHOLD_{k}"][c] = ev.ihold
        scal[f"PI_RISE_{k}"][c] = ev.rise_ms
        scal[f"PI_CHG_{k}"][c] = ev.chg
        if pc.hydraulic_delay_ms > 0:
            _, a = cmap.locate(np.array([ev.t_soe + pc.hydraulic_delay_ms / 1000.0]))
            scal[f"PI_SOI_{k}"][c] = float(a[0]) + (
                cmap.period if a[0] < ev.a_soe - cmap.period / 2 else 0.0)
    return scal, x, events, warnings
