"""File outputs: result tables, curves, log and HTML report (specification section 11)."""

from __future__ import annotations

import base64
import datetime as _dt
import html
import io
from pathlib import Path

import numpy as np

from .. import APP_NAME, __version__
from .analysis import CURVE_INFO, Result, scalar_info
from .config import CYL_NAMES, config_to_toml

# colours: validated two-series palette (High = blue, Low = orange) on a light surface
COLORS = {"H": "#2a78d6", "L": "#eb6834"}
SURFACE = "#fcfcfb"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
TEXT = "#0b0b0b"
TEXT2 = "#52514e"
MUTED = "#898781"

SCALAR_ORDER = [
    "SPEED", "PMAX", "APMAX", "RMAX", "ARMAX", "POFFS", "IMEP", "IMEPH", "IMEPL",
    "QMAX", "AQMAX", "IMAX", "AI05", "AI10", "AI50", "AI90", "SOC", "BD", "POLY_C", "POLY_E",
    "KP_PK", "AKP_PK", "KP_INT", "KP_REF", "KP_RATIO", "KNOCK",
    "KLS_PK", "AKLS_PK", "KLS_INT", "KLS_REF", "KLS_RATIO", "PI_N", "PI_OFFS", "IGD", "IGD_T",
]


EVENT_ORDER = ["PI_SOE", "PI_EOE", "PI_SOI", "PI_DUR", "PI_DURA", "PI_IPK", "PI_TPK",
               "PI_IHOLD", "PI_RISE", "PI_CHG", "PI_TSOE", "PI_TEOE"]


def ordered_scalars(names) -> list:
    names = [n for n in names if n not in ("VALID", "TSTART")]
    head = [n for n in SCALAR_ORDER if n in names]
    def key(s):
        base, _, num = s.rpartition("_")
        rank = EVENT_ORDER.index(base) if base in EVENT_ORDER else len(EVENT_ORDER)
        return (num if num.isdigit() else "~", rank, s)
    return head + sorted((n for n in names if n not in head), key=key)


def _fmt(v, decimal=".") -> str:
    if isinstance(v, str):
        return v
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return ""
    if isinstance(v, (int, np.integer)):
        return str(int(v))
    s = f"{float(v):.10g}"
    return s.replace(".", decimal) if decimal != "." else s


def _write_table(path: Path, header: list, rows: list, delim: str, decimal: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(delim.join(header) + "\n")
        for r in rows:
            fh.write(delim.join(_fmt(v, decimal) for v in r) + "\n")


# ------------------------------------------------------------------- tables
def cycles_table(res: Result) -> tuple[list, list]:
    names: list = []
    for cr in res.cyl.values():
        for n in cr.scalars:
            if n not in names:
                names.append(n)
    names = ordered_scalars(names)
    header = ["cyl", "cycle", "valid", "t_start"] + names
    rows = []
    for c, cr in res.cyl.items():
        used = cr.used()
        for i in range(cr.n_cycles):
            row = [CYL_NAMES[c], i + 1, int(used[i]), float(cr.cmap.t_start[i])]
            row += [float(cr.scalars[n][i]) if n in cr.scalars else np.nan for n in names]
            rows.append(row)
    return header, rows


def stats_table(res: Result) -> tuple[list, list]:
    header = ["cyl", "name", "unit", "mean", "std", "min", "max", "cov_percent", "n", "p95"]
    rows = []
    for c, cr in res.cyl.items():
        for n in ordered_scalars(cr.stats.keys()) :
            st = cr.stats[n]
            unit = "%" if n == "KNOCK_FREQ" else scalar_info(n)[0]
            rows.append([CYL_NAMES[c], n, unit, st["mean"], st["std"], st["min"], st["max"],
                         st["cov"], st["n"], st.get("p95", np.nan)])
    return header, rows


def curves_table(cr) -> tuple[list, list]:
    header = ["CA"]
    cols = [cr.grid]
    for name in ("PCYL", "Q", "I", "PI", "PKNK", "KLSF"):
        if name in cr.curve_stats:
            st = cr.curve_stats[name]
            for k in ("mean", "min", "max", "std"):
                header.append(f"{name}_{k.upper()}")
                cols.append(st[k])
    if "V" in cr.curves:
        header.append("V")
        cols.append(cr.curves["V"])
    rows = np.column_stack(cols).tolist()
    return header, rows


# -------------------------------------------------------------------- plots
def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": TEXT2, "axes.titlecolor": TEXT,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": TEXT2,
        "ytick.labelcolor": TEXT2, "grid.color": GRID, "grid.linewidth": 0.8,
        "axes.grid": True, "axes.spines.top": False, "axes.spines.right": False,
        "axes.titlesize": 11, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.labelsize": 9, "xtick.labelsize": 8.5, "ytick.labelsize": 8.5,
        "legend.fontsize": 8.5, "legend.frameon": False, "font.family": "DejaVu Sans",
        "lines.linewidth": 2.0, "axes.axisbelow": True,
    })
    return plt


def _png(fig) -> bytes:
    plt = _plt()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def _thin(ax, x, ys, color, max_lines=60):
    """Individual cycles as thin, recessive lines."""
    if ys.shape[0] > max_lines:
        ys = ys[np.linspace(0, ys.shape[0] - 1, max_lines).astype(int)]
    ax.plot(x, ys.T, color=color, lw=0.6, alpha=0.18)


def _ca_window(cr) -> tuple[float, float]:
    lo, hi = -90.0, 120.0
    for k, v in cr.scalars.items():
        if k.startswith("PI_SOE_") or k.startswith("PI_EOE_"):
            v = v[~np.isnan(v)]
            if v.size:
                lo, hi = min(lo, float(v.min()) - 15.0), max(hi, float(v.max()) + 15.0)
    half = cr.cmap.period / 2.0
    return max(lo, -half), min(hi, half)


def fig_pressure(cr):
    plt = _plt()
    used = cr.used()
    col = COLORS[cr.cyl]
    panels = [n for n in ("PCYL", "PI") if n in cr.curves]
    fig, axes = plt.subplots(len(panels), 1, figsize=(9, 2.9 * len(panels)), sharex=True,
                             squeeze=False)
    for ax, name in zip(axes[:, 0], panels):
        ys = cr.curves[name][used]
        if ys.shape[0]:
            _thin(ax, cr.grid, ys, col)
            ax.plot(cr.grid, cr.curve_stats[name]["mean"], color=col, label="mean cycle")
        unit, desc = CURVE_INFO[name]
        ax.set_ylabel(f"{name} [{unit}]")
        ax.set_title(f"{_cap(desc)} - {cr.name} ({int(used.sum())} cycles)")
    axes[-1, 0].set_xlabel("Crank Angle [deg]")
    axes[-1, 0].set_xlim(*_ca_window(cr))
    fig.tight_layout()
    return fig


def fig_pv(cr):
    plt = _plt()
    v = cr.curves["V"]
    p = cr.curve_stats["PCYL"]["mean"]
    col = COLORS[cr.cyl]
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.6))
    for ax, log in zip(axes, (False, True)):
        ax.plot(np.append(v, v[0]), np.append(p, p[0]), color=col)
        if log:
            ax.set_xscale("log")
            ax.set_yscale("log")
        ax.set_xlabel("V [l]")
        ax.set_ylabel("PCYL [bar]")
        ax.set_title(f"p-V {'log-log' if log else 'linear'} - {cr.name}, mean cycle")
    fig.tight_layout()
    return fig


def fig_heat_release(cr, cfg):
    plt = _plt()
    used = cr.used()
    col = COLORS[cr.cyl]
    fig, axes = plt.subplots(2, 1, figsize=(9, 5.6), sharex=True)
    for ax, name in zip(axes, ("Q", "I")):
        ys = cr.curves[name][used]
        if ys.shape[0]:
            _thin(ax, cr.grid, ys, col)
            ax.plot(cr.grid, cr.curve_stats[name]["mean"], color=col)
        unit, desc = CURVE_INFO[name]
        ax.set_ylabel(f"{name} [{unit}]")
        ax.set_title(f"{_cap(desc)} - {cr.name}")
    for name in ("AI05", "AI50", "AI90"):
        m = cr.stats.get(name, {}).get("mean", np.nan)
        if not np.isnan(m):
            for ax in axes:
                ax.axvline(m, color=TEXT2, lw=0.9, ls=(0, (4, 3)))
            axes[1].annotate(f"{name} {m:.1f}", (m, 0.04), xycoords=("data", "axes fraction"),
                             xytext=(3, 0), textcoords="offset points", fontsize=8,
                             color=TEXT2, rotation=90, va="bottom")
    axes[1].set_xlabel("Crank Angle [deg]")
    axes[1].set_xlim(cfg.heat_release.start_deg - 10, cfg.heat_release.end_deg + 10)
    fig.tight_layout()
    return fig


def fig_pi_detail(cr, time):
    plt = _plt()
    col = COLORS[cr.cyl]
    x = cr.time_channels["PI"]
    evs = [e for e in cr.events if e.cycle >= 0][:80]
    if not evs:
        return None
    dur = float(np.median([e.dur_ms for e in evs]))
    pre, post = 0.3 * dur + 0.2, 0.5 * dur + 0.3
    fs = 1.0 / float(np.median(np.diff(time[:1000])))
    fig, ax = plt.subplots(figsize=(9, 3.4))
    for e in evs:
        a = max(0, e.i0 - int(pre / 1000 * fs) - 2)
        b = min(x.size, e.i1 + int(post / 1000 * fs) + 2)
        ax.plot((time[a:b] - e.t_soe) * 1000.0, x[a:b], color=col, lw=0.7, alpha=0.35)
    ax.axvline(0.0, color=TEXT2, lw=0.9, ls=(0, (4, 3)))
    ax.axvline(dur, color=TEXT2, lw=0.9, ls=(0, (4, 3)))
    ax.annotate("SOE", (0, 0.96), xycoords=("data", "axes fraction"), xytext=(3, 0),
                textcoords="offset points", fontsize=8, color=TEXT2, va="top")
    ax.annotate(f"EOE (median {dur:.2f} ms)", (dur, 0.96), xycoords=("data", "axes fraction"),
                xytext=(3, 0), textcoords="offset points", fontsize=8, color=TEXT2, va="top")
    ax.set_xlim(-pre, dur + post)
    ax.set_xlabel("Time after start of energizing [ms]")
    ax.set_ylabel("PI [A]")
    ax.set_title(f"PI current, offset corrected - {cr.name} ({len(evs)} events)")
    fig.tight_layout()
    return fig


def fig_trends(res: Result):
    plt = _plt()
    names = [n for n in ("IMEP", "PMAX", "AI50", "KP_PK", "KLS_PK", "PI_SOE_1", "PI_DUR_1")
             if any(n in cr.scalars for cr in res.cyl.values())]
    if not names:
        return None
    fig, axes = plt.subplots(len(names), 1, figsize=(9, 1.75 * len(names) + 0.6), sharex=True,
                             squeeze=False)
    for ax, n in zip(axes[:, 0], names):
        for c, cr in res.cyl.items():
            if n in cr.scalars:
                y = np.where(cr.used(), cr.scalars[n], np.nan)
                ax.plot(np.arange(1, cr.n_cycles + 1), y, color=COLORS[c], lw=1.4, marker="o",
                        ms=3.2, label=cr.name)
        if n == "KP_PK":
            ax.axhline(res.cfg.knock.threshold_bar, color=TEXT2, lw=0.9, ls=(0, (4, 3)))
        ax.set_ylabel(f"{n} [{scalar_info(n)[0]}]")
    axes[0, 0].set_title("Cycle trend")
    axes[0, 0].legend(loc="upper right", ncols=2)
    axes[-1, 0].set_xlabel("Cycle")
    fig.tight_layout()
    return fig


def fig_scatter(res: Result):
    plt = _plt()
    have = [c for c, cr in res.cyl.items() if "PMAX" in cr.scalars]
    if not have:
        return None
    imep = [c for c in have if "IMEP" in res.cyl[c].scalars]
    fig, axes = plt.subplots(1, 2 if imep else 1, figsize=(9, 3.6), squeeze=False)
    ax = axes[0, 0]
    for c in have:
        cr = res.cyl[c]
        u = cr.used()
        ax.scatter(cr.scalars["APMAX"][u], cr.scalars["PMAX"][u], s=22, color=COLORS[c],
                   edgecolors=SURFACE, linewidths=0.8, label=cr.name)
    ax.set_xlabel("APMAX [deg]")
    ax.set_ylabel("PMAX [bar]")
    ax.set_title("Peak pressure vs. position")
    ax.legend()
    if imep:
        ax = axes[0, 1]
        vals = [res.cyl[c].scalars["IMEP"][res.cyl[c].used()] for c in imep]
        allv = np.concatenate(vals)
        allv = allv[~np.isnan(allv)]
        if allv.size:
            bins = np.linspace(allv.min(), allv.max() + 1e-9, 13)
            ax.hist(vals, bins=bins, color=[COLORS[c] for c in imep],
                    label=[res.cyl[c].name for c in imep], rwidth=0.9)
        ax.set_xlabel("IMEP [bar]")
        ax.set_ylabel("Cycles")
        ax.set_title("IMEP distribution")
        ax.legend()
    fig.tight_layout()
    return fig


def fig_raw(res: Result):
    plt = _plt()
    t = res.meas.time
    rows = []
    for c, cr in res.cyl.items():
        if "P" in cr.time_channels:
            rows.append((c, "P", "bar"))
    for c, cr in res.cyl.items():
        if "PI" in cr.time_channels:
            rows.append((c, "PI", "A"))
    if not rows:
        return None
    step = max(1, t.size // 6000)
    t_end = t[-1]
    for cr in res.cyl.values():
        if cr.n_cycles >= 2:
            t_end = min(t_end, float(cr.cmap.t_end[min(2, cr.n_cycles - 1)]))
    nz = int(np.searchsorted(t, t_end))
    fig, axes = plt.subplots(len(rows), 2, figsize=(11, 1.9 * len(rows) + 0.6), squeeze=False,
                             sharex="col")
    for r, (c, name, unit) in enumerate(rows):
        y = res.cyl[c].time_channels[name]
        # min/max envelope keeps peaks visible in the overview
        n = (y.size // step) * step
        yy = y[:n].reshape(-1, step)
        tt = t[:n].reshape(-1, step)[:, 0]
        axes[r, 0].fill_between(tt, np.nanmin(yy, axis=1), np.nanmax(yy, axis=1),
                                color=COLORS[c], lw=0.5)
        axes[r, 1].plot(t[:nz], y[:nz], color=COLORS[c], lw=1.0)
        for ax in axes[r]:
            ax.set_ylabel(f"{name}_{c} [{unit}]")
    axes[0, 0].set_title("Whole measurement (min/max envelope)")
    axes[0, 1].set_title("First cycles")
    for ax in axes[-1]:
        ax.set_xlabel("Time [s]")
    fig.tight_layout()
    return fig


def fig_knock(cr, cfg, time):
    plt = _plt()
    if "KP_PK" not in cr.scalars or cr.n_cycles == 0:
        return None
    col = COLORS[cr.cyl]
    used = cr.used()
    kp = cr.scalars["KP_PK"]
    if not np.isfinite(np.where(used, kp, np.nan)).any():
        return None
    worst = int(np.nanargmax(np.where(used, kp, np.nan)))
    fig, axes = plt.subplots(2, 2, figsize=(11, 6.2))
    w0, w1 = cfg.knock.window_deg
    sl = cr.cmap.sample_range(worst, w0 - 10, w1 + 10)
    ang = cr.cmap.phi[sl] - cr.cmap.period * cr.cmap.k[worst]
    ax = axes[0, 0]
    ax.plot(ang, cr.time_channels["P"][sl], color=col, lw=1.2)
    ax.set_ylabel("P [bar]")
    ax.set_xlabel("Crank Angle [deg]")
    ax.set_title(f"Pressure, cycle {worst + 1} (highest KP_PK) - {cr.name}")
    ax = axes[1, 0]
    ax.plot(ang, cr.time_channels["PKNK"][sl], color=col, lw=1.0)
    for a in (w0, w1):
        ax.axvline(a, color=TEXT2, lw=0.9, ls=(0, (4, 3)))
    ax.set_ylabel("PKNK [bar]")
    ax.set_xlabel("Crank Angle [deg]")
    ax.set_title(f"Knock band {cfg.knock.band_hz[0]:g}-{cfg.knock.band_hz[1]:g} Hz, "
                 f"cycle {worst + 1}")
    ax = axes[0, 1]
    ax.plot(np.arange(1, cr.n_cycles + 1), np.where(used, kp, np.nan), color=col, lw=1.2,
            marker="o", ms=3.2)
    ax.axhline(cfg.knock.threshold_bar, color=TEXT2, lw=0.9, ls=(0, (4, 3)))
    ax.annotate(f"threshold {cfg.knock.threshold_bar:g} bar", (0.01, cfg.knock.threshold_bar),
                xycoords=("axes fraction", "data"), xytext=(0, 3), textcoords="offset points",
                fontsize=8, color=TEXT2)
    ax.set_xlabel("Cycle")
    ax.set_ylabel("KP_PK [bar]")
    ax.set_title("Knock peak per cycle")
    ax = axes[1, 1]
    if "AI50" in cr.scalars:
        ax.scatter(cr.scalars["AI50"][used], kp[used], s=22, color=col, edgecolors=SURFACE,
                   linewidths=0.8)
        ax.set_xlabel("AI50 [deg]")
        ax.set_title("Knock peak vs. AI50")
    else:
        ax.hist(kp[used & ~np.isnan(kp)], bins=12, color=col, rwidth=0.9)
        ax.set_xlabel("KP_PK [bar]")
        ax.set_title("Knock peak distribution")
    ax.set_ylabel("KP_PK [bar]" if "AI50" in cr.scalars else "Cycles")
    fig.tight_layout()
    return fig


def fig_compare(res: Result):
    plt = _plt()
    names = [n for n in ("PCYL", "Q") if sum(n in cr.curve_stats for cr in res.cyl.values()) == 2]
    if not names:
        return None
    fig, axes = plt.subplots(len(names), 1, figsize=(9, 2.9 * len(names)), sharex=True,
                             squeeze=False)
    for ax, n in zip(axes[:, 0], names):
        for c, cr in res.cyl.items():
            ax.plot(cr.grid, cr.curve_stats[n]["mean"], color=COLORS[c], label=cr.name)
        unit, desc = CURVE_INFO[n]
        ax.set_ylabel(f"{n} [{unit}]")
        ax.set_title(f"{_cap(desc)} - mean cycles High / Low")
        ax.legend()
    axes[-1, 0].set_xlabel("Crank Angle [deg]")
    axes[-1, 0].set_xlim(-90, 120)
    fig.tight_layout()
    return fig


def build_figures(res: Result) -> list:
    """List of (title, figure) for the report."""
    figs = []
    for c, cr in res.cyl.items():
        if cr.n_cycles == 0 or not cr.used().any():
            continue
        if "PCYL" in cr.curves or "PI" in cr.curves:
            figs.append((f"{cr.name}: pressure and PI current", fig_pressure(cr)))
        if "V" in cr.curves and "PCYL" in cr.curve_stats:
            figs.append((f"{cr.name}: p-V diagram", fig_pv(cr)))
        if "Q" in cr.curves:
            figs.append((f"{cr.name}: heat release", fig_heat_release(cr, res.cfg)))
        if "PKNK" in cr.time_channels:
            figs.append((f"{cr.name}: knock", fig_knock(cr, res.cfg, res.meas.time)))
        if "PI" in cr.time_channels:
            figs.append((f"{cr.name}: PI current detail", fig_pi_detail(cr, res.meas.time)))
    figs.append(("High / Low comparison", fig_compare(res)))
    figs.append(("Cycle trend", fig_trends(res)))
    figs.append(("Distribution", fig_scatter(res)))
    figs.append(("Raw data", fig_raw(res)))
    return [(t, f) for t, f in figs if f is not None]


# ------------------------------------------------------------------- report
_CSS = f"""
body{{font-family:system-ui,-apple-system,'Segoe UI',sans-serif;background:{SURFACE};color:{TEXT};
margin:0;padding:24px 16px 48px;line-height:1.45}}
main{{max-width:1000px;margin:0 auto}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:16px;margin:32px 0 8px;
border-bottom:1px solid {GRID};padding-bottom:4px}}
p.sub{{color:{TEXT2};margin:0 0 16px;font-size:13px}}
table{{border-collapse:collapse;font-size:12.5px;font-variant-numeric:tabular-nums}}
th,td{{padding:3px 10px;border-bottom:1px solid {GRID};text-align:right;white-space:nowrap}}
th{{color:{TEXT2};font-weight:600}} td:first-child,th:first-child{{text-align:left}}
td.l,th.l{{text-align:left}} .scroll{{overflow-x:auto}}
ul.warn{{color:{TEXT2};font-size:13px;padding-left:18px}}
.tag{{display:inline-block;border:1px solid {AXIS};border-radius:4px;padding:1px 6px;
font-size:11.5px;color:{TEXT2};margin-left:6px}}
img{{max-width:100%;height:auto;display:block;margin:6px 0 2px}}
pre{{background:#f3f2ee;padding:10px;font-size:12px;overflow-x:auto;border-radius:4px}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}}
"""


def _stats_html(res: Result) -> str:
    out = []
    for c, cr in res.cyl.items():
        used = int(cr.used().sum())
        out.append(f"<h3><span class='sw' style='background:{COLORS[c]}'></span>"
                   f"{cr.name} - {used} of {cr.n_cycles} cycles used</h3>")
        out.append("<div class='scroll'><table><tr><th>Name</th><th class='l'>Unit</th>"
                   "<th>Mean</th><th>Std</th><th>Min</th><th>Max</th><th>COV %</th><th>n</th>"
                   "<th class='l'>Description</th></tr>")
        for n in ordered_scalars(cr.stats.keys()):
            st = cr.stats[n]
            unit, desc = ("%", "share of knocking cycles") if n == "KNOCK_FREQ" else scalar_info(n)
            note = " (few cycles)" if 0 < st["n"] < 10 and not np.isnan(st["cov"]) else ""
            out.append(
                f"<tr><td>{n}</td><td class='l'>{html.escape(unit)}</td>"
                f"<td>{_fmt(round(st['mean'], 6))}</td><td>{_fmt(round(st['std'], 6))}</td>"
                f"<td>{_fmt(round(st['min'], 6))}</td><td>{_fmt(round(st['max'], 6))}</td>"
                f"<td>{'' if np.isnan(st['cov']) else f'{st['cov']:.2f}'}{note}</td>"
                f"<td>{st['n']}</td><td class='l'>{html.escape(desc)}</td></tr>")
        out.append("</table></div>")
    return "\n".join(out)


def report_html(res: Result, figures: list | None = None) -> str:
    """figures: list of (title, png bytes); built here if not given."""
    if figures is None:
        figures = [(t, _png(f)) for t, f in build_figures(res)]
    imgs = []
    for title, png in figures:
        b64 = base64.b64encode(png).decode("ascii")
        imgs.append(f"<h2>{html.escape(title)}</h2>"
                    f"<img alt='{html.escape(title)}' src='data:image/png;base64,{b64}'>")
    m = res.meas
    tag = "<span class='tag'>assumed engine data</span>" if res.cfg.engine.assumed else ""
    par = "".join(
        f"<tr><td>{n}</td><td class='l'>{html.escape(str(u))}</td>"
        f"<td>{html.escape(_fmt(v))}</td><td class='l'>{html.escape(d)}</td></tr>"
        for n, u, v, d in res.parameters())
    warn = "".join(f"<li>{html.escape(w)}</li>" for w in res.warnings) or "<li>none</li>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(m.name)} - {APP_NAME}</title><style>{_CSS}</style></head><body><main>
<h1>{html.escape(m.name)}{tag}</h1>
<p class="sub">{APP_NAME} {__version__} - {_dt.datetime.now():%Y-%m-%d %H:%M} -
{m.n} samples, {m.fs:g} Hz, {m.duration:.3f} s - {html.escape(m.fmt.describe())}</p>
<h2>Messages</h2><ul class="warn">{warn}</ul>
<h2>Statistics</h2>{_stats_html(res)}
{''.join(imgs)}
<h2>Parameters</h2><div class="scroll"><table><tr><th>Name</th><th class="l">Unit</th>
<th>Value</th><th class="l">Description</th></tr>{par}</table></div>
<h2>Settings</h2><pre>{html.escape(config_to_toml(res.cfg))}</pre>
</main></body></html>"""


# ------------------------------------------------------------------ outputs
def log_text(res: Result) -> str:
    m = res.meas
    lines = [f"{APP_NAME} {__version__}", f"file: {m.path}", f"format: {m.fmt.describe()}",
             f"columns: {', '.join(m.fmt.mapping.values())}",
             f"samples: {m.n}, sample rate: {m.fs:g} Hz, duration: {m.duration:.6f} s, "
             f"jitter: {m.jitter * 100:.3f} %"]
    for c, cr in res.cyl.items():
        info = cr.cmap.info
        lines.append(
            f"{cr.name}: {cr.n_cycles} cycles, {int(cr.used().sum())} used, "
            f"grid {cr.cmap.res:g} deg, {cr.cmap.deg_per_sample:.4f} deg/sample, "
            f"angle {'stepped ' + format(info['step_deg'], 'g') + ' deg' if info['stepped'] else 'continuous'}"
            f" ({info['mode']})")
        bad = np.flatnonzero(~cr.used()) + 1
        if bad.size:
            lines.append(f"{cr.name}: cycles not used: {', '.join(map(str, bad))}")
    if set(res.cyl) == {"H", "L"}:
        d = np.median(res.cyl["L"].cmap.phi - res.cyl["H"].cmap.phi)
        lines.append(f"crank angle Low - High: {d:.2f} deg")
    lines.append("messages:")
    lines += [f"  - {w}" for w in res.warnings] or ["  none"]
    return "\n".join(lines) + "\n"


def summary_text(res: Result) -> str:
    lines = [f"{res.meas.name}: {res.meas.n} samples, {res.meas.fs:g} Hz"]
    for c, cr in res.cyl.items():
        parts = [f"{cr.name}: {int(cr.used().sum())}/{cr.n_cycles} cycles"]
        for n in ("SPEED", "PMAX", "IMEP", "AI50", "KP_PK", "KLS_PK", "PI_SOE_1", "PI_DUR_1"):
            if n in cr.stats and cr.stats[n]["n"]:
                parts.append(f"{n}={cr.stats[n]['mean']:.4g}")
        if "KNOCK_FREQ" in cr.stats and cr.stats["KNOCK_FREQ"]["n"]:
            parts.append(f"KNOCK_FREQ={cr.stats['KNOCK_FREQ']['mean']:.1f}%")
        lines.append("  " + "  ".join(parts))
    lines.append(f"  {len(res.warnings)} message(s)")
    return "\n".join(lines)


def write_outputs(res: Result, outdir: str | Path, plots: bool = True,
                  save_cycles: bool = False) -> list:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    stem = res.meas.path.stem
    delim, dec = res.cfg.output.csv_delimiter, res.cfg.output.csv_decimal
    written = []

    p = outdir / f"{stem}_cycles.csv"
    _write_table(p, *cycles_table(res), delim, dec)
    written.append(p)
    p = outdir / f"{stem}_stats.csv"
    _write_table(p, *stats_table(res), delim, dec)
    written.append(p)
    for c, cr in res.cyl.items():
        if cr.curve_stats:
            p = outdir / f"{stem}_curves_{c}.csv"
            _write_table(p, *curves_table(cr), delim, dec)
            written.append(p)
        if save_cycles and cr.n_cycles:
            p = outdir / f"{stem}_allcycles_{c}.npz"
            np.savez_compressed(p, CA=cr.grid, valid=cr.used(),
                                **{k: v for k, v in cr.curves.items()})
            written.append(p)
    p = outdir / f"{stem}_log.txt"
    p.write_text(log_text(res), encoding="utf-8")
    written.append(p)
    if plots:
        fmts = [f for f in res.cfg.output.plots if f in ("png", "pdf")]
        pngs = []
        for i, (title, fig) in enumerate(build_figures(res), 1):
            slug = "_".join("".join(ch if ch.isalnum() else " " for ch in title).lower().split())
            if "pdf" in fmts:
                q = outdir / f"{stem}_{i:02d}_{slug}.pdf"
                fig.savefig(q, bbox_inches="tight")
                written.append(q)
            png = _png(fig)
            pngs.append((title, png))
            if "png" in fmts:
                q = outdir / f"{stem}_{i:02d}_{slug}.png"
                q.write_bytes(png)
                written.append(q)
        p = outdir / f"{stem}_report.html"
        p.write_text(report_html(res, pngs), encoding="utf-8")
        written.append(p)
    return written
