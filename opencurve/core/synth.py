"""Synthetic measurement files with known reference values (specification 13.1).

The pressure is the exact inverse of the heat release model used by the evaluation
(constant kappa, Vibe burn function), so IMEP, PMAX and the burn angles are known.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .config import Config
from .thermo import Geometry

VIBE_A = 6.908
DENSE_RES = 0.05


@dataclass
class SynthSpec:
    fs: float = 30000.0
    duration: float = 3.0
    speed_rpm: float = 1497.0
    speed_fluct: float = 0.004          # relative speed fluctuation
    ca_start_h: float = 275.4           # file crank angle of High at t = 0
    low_minus_high: float = -240.0      # CA Low = CA High - 240 deg
    stepped: bool = True                # integer crank angle (1 deg steps)
    ca_round: bool = False              # stepped value is rounded instead of truncated
    # pressure
    p_in: float = 3.6                   # bar
    p_ex: float = 3.2                   # bar
    q_total: float = 30000.0            # J
    q_scatter: float = 0.02
    soc: float = -12.0                  # Vibe start [deg]
    soc_scatter: float = 0.8
    burn_dur: float = 50.0
    vibe_m: float = 2.0
    sens_mv_per_bar: float = 10.0
    volt_offset: float = 1.10           # V, amplifier offset
    volt_lsb: float = 0.00031442833     # V, converter resolution
    p_noise: float = 0.02               # bar
    # knock
    knock_fraction: float = 0.25        # share of knocking cycles
    knock_amp: float = 3.0              # bar
    knock_freq: float = 3000.0          # Hz
    knock_decay_ms: float = 1.5
    # knock sensor (structure-borne sound), volts
    kls_amp: float = 0.5                # V, burst amplitude in knocking cycles
    kls_noise: float = 0.01             # V
    kls_delay_ms: float = 0.2           # travel time to the sensor
    # PI current
    pi_offset: float = 2.0              # A
    pi_soe_deg: float = -40.0
    pi_soe_scatter: float = 0.3
    pi_dur_ms: float = 2.0
    pi_peak: float = 8.0                # A
    pi_hold: float = 3.0                # A
    pi_rise_ms: float = 0.40
    pi_decay_ms: float = 0.20
    pi_fall_ms: float = 0.20
    pi_noise: float = 0.008             # A
    pi_threshold: float = 0.10          # relative threshold used for the reference edges
    # file
    missing: tuple = ()                 # canonical names to leave out, e.g. ("DMR_L",)
    delimiter: str = ","
    decimal: str = "."
    units_row: bool = False
    seed: int = 1


@dataclass
class Truth:
    spec: dict
    cyl: dict = field(default_factory=dict)     # 'H'/'L' -> dict of per-cycle lists

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"spec": self.spec, "cyl": self.cyl}, indent=1),
                        encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Truth":
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(d["spec"], d["cyl"])


def vibe_angle(x: float, soc: float, dur: float, m: float) -> float:
    return soc + dur * (-np.log(1.0 - x) / VIBE_A) ** (1.0 / (m + 1.0))


def _cycle_pressure(geo: Geometry, kappa: float, s: SynthSpec, q_tot: np.ndarray,
                    soc: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Dense pressure curves [bar], shape (n_cycles, 720/DENSE_RES), grid from -360."""
    n = int(round(720.0 / DENSE_RES))
    a = -360.0 + np.arange(n) * DENSE_RES
    v = geo.volume(a)
    closed = (a >= -180.0) & (a <= 180.0)
    ac, vc = a[closed], v[closed]
    y = np.clip((ac[None, :] - soc[:, None]) / s.burn_dur, 0.0, None)
    dx = (VIBE_A * (s.vibe_m + 1.0) / s.burn_dur * y ** s.vibe_m
          * np.exp(-VIBE_A * y ** (s.vibe_m + 1.0)))
    dq = q_tot[:, None] * dx                                         # J/deg
    f = vc[None, :] ** (kappa - 1.0) * dq
    integ = np.concatenate(
        (np.zeros((f.shape[0], 1)),
         np.cumsum(0.5 * (f[:, 1:] + f[:, :-1]) * DENSE_RES, axis=1)), axis=1)
    c = s.p_in * 1e5 * vc[0] ** kappa + (kappa - 1.0) * integ
    pc = c / vc[None, :] ** kappa / 1e5                              # bar
    p = np.full((q_tot.size, n), s.p_in)
    p[:, closed] = pc
    ex = a > 180.0
    p180 = pc[:, -1:]
    pe = s.p_ex + (p180 - s.p_ex) * np.exp(-(a[ex][None, :] - 180.0) / 20.0)
    blend = np.clip((a[ex] - 330.0) / 30.0, 0.0, 1.0)[None, :]
    p[:, ex] = pe * (1.0 - blend) + s.p_in * blend
    return a, p


def _pi_wave(tau: np.ndarray, s: SynthSpec) -> np.ndarray:
    """Current above baseline at time tau after start of energizing."""
    tr, td, tf = s.pi_rise_ms / 1e3, s.pi_decay_ms / 1e3, s.pi_fall_ms / 1e3
    dur = s.pi_dur_ms / 1e3
    y = np.zeros_like(tau)
    m = (tau >= 0) & (tau < tr)
    y[m] = s.pi_peak * tau[m] / tr
    m = (tau >= tr) & (tau < tr + td)
    y[m] = s.pi_peak + (s.pi_hold - s.pi_peak) * (tau[m] - tr) / td
    m = (tau >= tr + td) & (tau < dur)
    y[m] = s.pi_hold
    m = (tau >= dur) & (tau < dur + tf)
    y[m] = s.pi_hold * (1.0 - (tau[m] - dur) / tf)
    return y


def generate(spec: SynthSpec | None = None, cfg: Config | None = None):
    """Returns (DataFrame in the reference column layout, Truth)."""
    s = spec or SynthSpec()
    cfg = cfg or Config()
    geo = Geometry.from_config(cfg.engine)
    kappa = cfg.engine.kappa
    rng = np.random.default_rng(s.seed)

    n = int(round(s.duration * s.fs))
    t = np.arange(n) / s.fs
    w0 = 6.0 * s.speed_rpm                                   # deg/s
    f_fl = 3.0 * s.speed_rpm / 120.0                         # third order of the cycle
    phi_h = (s.ca_start_h + w0 * (t - s.speed_fluct / (2 * np.pi * f_fl)
                                   * (np.cos(2 * np.pi * f_fl * t) - 1.0)))
    phi = {"H": phi_h, "L": phi_h + s.low_minus_high}

    cols = {"Time": t}
    truth = Truth(spec={k: (list(v) if isinstance(v, tuple) else v)
                        for k, v in s.__dict__.items()})
    for c in ("H", "L"):
        ph = phi[c]
        if s.stepped:
            step = np.round(ph) if s.ca_round else np.floor(ph)
            cols[f"CA {'High' if c == 'H' else 'Low'}"] = np.mod(step, 720).astype(int)
        else:
            cols[f"CA {'High' if c == 'H' else 'Low'}"] = np.mod(ph, 720.0)

        kk = np.floor((ph + 360.0) / 720.0).astype(np.int64)
        k_lo, k_hi = int(kk.min()), int(kk.max())
        nk = k_hi - k_lo + 1
        q_tot = s.q_total * (1.0 + s.q_scatter * rng.standard_normal(nk))
        soc = s.soc + s.soc_scatter * rng.standard_normal(nk)
        a, pd_ = _cycle_pressure(geo, kappa, s, q_tot, soc)
        nd = a.size
        flat = pd_.ravel()
        pos = (ph - (720.0 * k_lo - 360.0)) / DENSE_RES
        i0 = np.clip(np.floor(pos).astype(np.int64), 0, flat.size - 2)
        fr = pos - i0
        p = flat[i0] * (1.0 - fr) + flat[i0 + 1] * fr

        # complete cycles as the evaluation sees them
        first = int(np.ceil((ph[0] + 360.0) / 720.0 - 1e-9))
        last = int(np.floor((ph[-1] + 360.0) / 720.0 + 1e-9)) - 1
        complete = np.arange(first, last + 1)
        ci = complete - k_lo
        v = geo.volume(a)
        dv = np.gradient(v, DENSE_RES)
        jmax = np.argmax(pd_[ci], axis=1)
        tr = {
            "PMAX": pd_[ci, jmax].tolist(),
            "APMAX": a[jmax].tolist(),
            "IMEP": ((pd_[ci] * dv[None, :]).sum(axis=1) * DENSE_RES / geo.vh).tolist(),
            "QTOT": q_tot[ci].tolist(),
        }
        for x, name in ((0.05, "AI05"), (0.10, "AI10"), (0.50, "AI50"), (0.90, "AI90")):
            tr[name] = [vibe_angle(x, so, s.burn_dur, s.vibe_m) for so in soc[ci]]

        # knock: decaying oscillation after peak pressure in some cycles
        knock = np.zeros(nk, dtype=bool)
        if s.knock_fraction > 0:
            knock[rng.random(nk) < s.knock_fraction] = True
        kp = np.zeros(nk)
        for j in np.flatnonzero(knock):
            a_on = a[int(np.argmax(pd_[j]))] + 3.0
            t_on = float(np.interp(720.0 * (k_lo + j) + a_on, ph, t))
            if not (t[0] < t_on < t[-1]):
                knock[j] = False
                continue
            tau = t - t_on
            m = (tau >= 0) & (tau < 8.0 * s.knock_decay_ms / 1e3)
            osc = (s.knock_amp * np.exp(-tau[m] / (s.knock_decay_ms / 1e3))
                   * np.sin(2 * np.pi * s.knock_freq * tau[m]))
            p[m] += osc
            tq = s.knock_decay_ms / 1e3
            td = np.linspace(0, 2.0 / s.knock_freq, 2000)
            kp[j] = float(np.max(np.abs(s.knock_amp * np.exp(-td / tq)
                                        * np.sin(2 * np.pi * s.knock_freq * td))))
        tr["KNOCK"] = knock[ci].astype(int).tolist()
        tr["KP_PK"] = kp[ci].tolist()

        # knock sensor: noise, plus a delayed burst in the knocking cycles
        kls = rng.standard_normal(n) * s.kls_noise
        for j in np.flatnonzero(knock):
            a_on = a[int(np.argmax(pd_[j]))] + 3.0
            t_on = float(np.interp(720.0 * (k_lo + j) + a_on, ph, t)) + s.kls_delay_ms / 1e3
            tau = t - t_on
            m = (tau >= 0) & (tau < 8.0 * s.knock_decay_ms / 1e3)
            kls[m] += (s.kls_amp * np.exp(-tau[m] / (s.knock_decay_ms / 1e3))
                       * np.sin(2 * np.pi * s.knock_freq * tau[m]))
        cols[f"KLS_{c}"] = kls

        volts = p * s.sens_mv_per_bar / 1000.0 + s.volt_offset
        volts += rng.standard_normal(n) * s.p_noise * s.sens_mv_per_bar / 1000.0
        if s.volt_lsb > 0:
            volts = np.round(volts / s.volt_lsb) * s.volt_lsb
        cols[f"DMR_{c}"] = volts

        # PI current: one pulse per cycle
        soe_a = s.pi_soe_deg + s.pi_soe_scatter * rng.standard_normal(nk)
        cur = np.full(n, s.pi_offset)
        thr = s.pi_threshold * s.pi_peak
        t_soe, t_eoe, a_soe = [], [], []
        for j in range(nk):
            t0 = float(np.interp(720.0 * (k_lo + j) + soe_a[j], ph, t,
                                 left=np.nan, right=np.nan))
            if np.isnan(t0):
                t_soe.append(np.nan); t_eoe.append(np.nan); a_soe.append(np.nan)
                continue
            i_a = int(np.searchsorted(t, t0)) - 2
            i_b = int(np.searchsorted(t, t0 + (s.pi_dur_ms + s.pi_fall_ms) / 1e3)) + 3
            i_a, i_b = max(i_a, 0), min(i_b, n)
            cur[i_a:i_b] += _pi_wave(t[i_a:i_b] - t0, s)
            # reference edges = threshold crossings of the ideal wave form
            ts = t0 + thr / s.pi_peak * s.pi_rise_ms / 1e3
            te = t0 + s.pi_dur_ms / 1e3 + (1.0 - thr / s.pi_hold) * s.pi_fall_ms / 1e3
            t_soe.append(ts); t_eoe.append(te)
            a_soe.append(float(np.interp(ts, t, ph)) - 720.0 * (k_lo + j))
        cur += rng.standard_normal(n) * s.pi_noise
        cols[f"PI_RM_{c}"] = cur
        tr["PI_TSOE"] = [t_soe[i] for i in ci]
        tr["PI_TEOE"] = [t_eoe[i] for i in ci]
        tr["PI_SOE"] = [a_soe[i] for i in ci]
        tr["N_CYCLES"] = int(complete.size)
        truth.cyl[c] = tr

    order = ["Time", "CA High", "CA Low", "DMR_L", "DMR_H", "PI_RM_L", "PI_RM_H",
             "KLS_H", "KLS_L"]
    drop = {"CA_H": "CA High", "CA_L": "CA Low"}
    missing = {drop.get(m, m) for m in s.missing}
    df = pd.DataFrame({k: cols[k] for k in order if k not in missing})
    return df, truth


def write_csv(df: pd.DataFrame, path: str | Path, spec: SynthSpec | None = None) -> None:
    s = spec or SynthSpec()
    path = Path(path)
    if s.units_row:
        units = {"Time": "s", "CA High": "deg", "CA Low": "deg"}
        row = [units.get(c, "A" if c.startswith("PI") else "V") for c in df.columns]
        with path.open("w", encoding="utf-8", newline="") as fh:
            fh.write(s.delimiter.join(df.columns) + "\n")
            fh.write(s.delimiter.join(row) + "\n")
            df.to_csv(fh, sep=s.delimiter, decimal=s.decimal, index=False, header=False)
    else:
        df.to_csv(path, sep=s.delimiter, decimal=s.decimal, index=False)


def make_file(path: str | Path, spec: SynthSpec | None = None,
              cfg: Config | None = None) -> Truth:
    """Write a synthetic measurement and its reference values (<name>_truth.json)."""
    path = Path(path)
    df, truth = generate(spec, cfg)
    write_csv(df, path, spec)
    truth.save(path.with_name(path.stem + "_truth.json"))
    return truth
