"""Knock evaluation on the time based pressure signal (specification 7.4)."""

from __future__ import annotations

import numpy as np
from scipy.signal import butter, sosfiltfilt

from .angle import CycleMap
from .config import Config

KNOCK_SCALARS = ("KP_PK", "AKP_PK", "KP_INT", "KP_REF", "KP_RATIO", "KNOCK")
KLS_SCALARS = ("KLS_PK", "AKLS_PK", "KLS_INT", "KLS_REF", "KLS_RATIO")


def knock_band(cfg: Config, fs: float) -> tuple[float, float, str | None]:
    """Usable band for the given sample rate: (low, high, warning). high <= low: not usable."""
    lo, hi = float(cfg.knock.band_hz[0]), float(cfg.knock.band_hz[1])
    limit = fs / 2.5
    warn = None
    if hi > limit:
        warn = (f"knock band {lo:g}-{hi:g} Hz reduced to {lo:g}-{limit:g} Hz "
                f"(sample rate {fs:g} Hz)")
        hi = limit
    if hi <= lo * 1.2:
        return lo, lo, (f"knock evaluation skipped: sample rate {fs:g} Hz is too low for "
                        f"the band {cfg.knock.band_hz[0]:g}-{cfg.knock.band_hz[1]:g} Hz")
    return lo, hi, warn


def band_statistics(x: np.ndarray, cmap: CycleMap, cfg: Config, fs: float, lo: float,
                    hi: float) -> tuple[dict, np.ndarray]:
    """Band-pass a time signal and evaluate it per cycle in the knock and the reference
    window.  Returns ({'PK','APK','INT','REF','RATIO'} per cycle, filtered signal)."""
    kc = cfg.knock
    nc = cmap.n_cycles
    nan = np.isnan(x)
    if nan.any():
        idx = np.arange(x.size)
        x = np.interp(idx, idx[~nan], x[~nan])
    sos = butter(int(kc.filter_order), [lo, hi], btype="bandpass", fs=fs, output="sos")
    xf = sosfiltfilt(sos, x)
    ax = np.abs(xf)
    out = {k: np.full(nc, np.nan) for k in ("PK", "APK", "INT", "REF", "RATIO")}
    w0, w1 = kc.window_deg
    r0, r1 = kc.ref_window_deg
    for i in range(nc):
        sl = cmap.sample_range(i, w0, w1)
        if sl.stop - sl.start < 3:
            continue
        seg = ax[sl]
        j = int(np.argmax(seg))
        out["PK"][i] = seg[j]
        out["APK"][i] = cmap.phi[sl.start + j] - cmap.period * cmap.k[i]
        out["INT"][i] = np.trapezoid(seg, cmap.phi[sl])
        rs = cmap.sample_range(i, r0, r1)
        if rs.stop - rs.start >= 3:
            out["REF"][i] = ax[rs].max()
    with np.errstate(divide="ignore", invalid="ignore"):
        out["RATIO"] = np.where(out["REF"] > 0, out["PK"] / out["REF"], np.nan)
    return out, xf


def evaluate_knock(p_bar: np.ndarray, cmap: CycleMap, cfg: Config,
                   fs: float) -> tuple[dict, np.ndarray | None, list]:
    """Knock from the cylinder pressure.
    Returns (scalars per cycle, band-pass filtered pressure per sample, warnings)."""
    warnings: list[str] = []
    kc = cfg.knock
    lo, hi, warn = knock_band(cfg, fs)
    if warn:
        warnings.append(warn)
    if hi <= lo:
        return {}, None, warnings
    st, pk = band_statistics(p_bar, cmap, cfg, fs, lo, hi)
    scal = {"KP_PK": st["PK"], "AKP_PK": st["APK"], "KP_INT": st["INT"], "KP_REF": st["REF"],
            "KP_RATIO": st["RATIO"]}
    knock = scal["KP_PK"] > kc.threshold_bar
    if kc.threshold_ratio > 0:
        knock &= scal["KP_RATIO"] > kc.threshold_ratio
    scal["KNOCK"] = np.where(np.isnan(scal["KP_PK"]), np.nan, knock.astype(float))
    return scal, pk, warnings


def evaluate_kls(sig: np.ndarray, cmap: CycleMap, cfg: Config,
                 fs: float) -> tuple[dict, np.ndarray | None, list]:
    """Knock sensor signal (KLS): same band and windows as the pressure based evaluation.
    No knock decision is derived from it - only peak, integral and the ratio to the
    reference window."""
    lo, hi, warn = knock_band(cfg, fs)
    if hi <= lo:
        return {}, None, [warn] if warn else []
    st, xf = band_statistics(sig, cmap, cfg, fs, lo, hi)
    scal = {"KLS_PK": st["PK"], "AKLS_PK": st["APK"], "KLS_INT": st["INT"],
            "KLS_REF": st["REF"], "KLS_RATIO": st["RATIO"]}
    return scal, xf, []
