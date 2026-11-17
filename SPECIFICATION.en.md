# Open Curve Tool – Specification

[Deutsch](SPEZIFIKATION.md) · **English**

> The German document [SPEZIFIKATION.md](SPEZIFIKATION.md) is the reference. This English version is
> a translation with the same section numbers; if the two differ, the German text applies.

Date: 2026-11-17 · Status: v1.2 – implemented (implementation status and deviations in section 16)

Python desktop application for the evaluation of indicating data (cylinder pressure + PI current
signal) for two cylinders "High" and "Low". Functionality and user interface follow AVL Concerto so
that switching is easy.

---

## 1. Goal and scope

### 1.1 Goal

From a time based CSV measurement file – as in Concerto – crank angle based working cycles are
formed, curves and characteristic values are calculated for each cycle and evaluated statistically
over the cycles. The results are shown in an interactive user interface that follows the Concerto
workflow: Channel Browser, layouts with diagram pages, curves by drag & drop, measurement cursors,
cycle navigation, export and report. The same evaluation can also be run without the user interface
as a batch run.

### 1.2 What "realistic" means here

- The same **characteristic value definitions and names** as are usual in the AVL world (PMAX,
  APMAX, IMEP, AI50 …).
- The same **standard procedures** (zero level correction, heat release by the first law,
  cumulative heat release, cycle statistics).
- The internal algorithms of Concerto are not disclosed. Numerical identity is therefore not
  claimed; instead, tolerances against a Concerto reference export are checked (section 13).
- The same **operating concept** (section 10): layout of the main window, terms, mouse operation
  and the separation of layout and data.

### 1.3 Non-goals

- Reading/writing AVL file formats (IFile, Concerto layouts etc.) – the input is CSV only.
- Graphical formula editor (CalcGraf), script automation, database / test bench data management.
- Process calculation with wall heat, gas exchange analysis, gas temperature/mass.
- Taking over AVL logos, icons or original graphics; the program appears under its own name.

### 1.4 Name

The project and program name is **Open Curve Tool**. This is how it appears in the title bar, the
"About" dialog, the report and the documentation. The technical short form is `opencurve` (start
script, package, settings folder); the file extension for layouts is `.oclayout`.

---

## 2. Terms

| Term | Meaning |
|---|---|
| High / Low | The two cylinders; abbreviations `H` / `L` |
| DMR | Cylinder pressure signal |
| PI_RM | Feedback signal of the PI current (current curve of the energizing) |
| CA / °CA | Crank angle in degrees |
| TDC | Firing TDC, internally 0 °CA |
| Cycle | One working cycle (720 °CA for 4-stroke, 360 °CA for 2-stroke) |

---

## 3. Input file

### 3.1 Columns

| Column | Required | Content | Unit (default) |
|---|---|---|---|
| `Time` | yes | Time stamp, high resolution | s |
| `CA High` | yes¹ | Crank angle cylinder High, 0–720 | °CA |
| `CA Low` | yes¹ | Crank angle cylinder Low, 0–720 | °CA |
| `DMR_H` | no | Cylinder pressure High, raw voltage of the measuring amplifier, **10 mV/bar** | V |
| `DMR_L` | no | Cylinder pressure Low, raw voltage of the measuring amplifier, **10 mV/bar** | V |
| `PI_RM_H` | no | PI current feedback High | A |
| `PI_RM_L` | no | PI current feedback Low | A |
| `KLS_H` | no | Knock sensor High (assumption: structure-borne sound signal) | V (assumption) |
| `KLS_L` | no | Knock sensor Low (assumption: structure-borne sound signal) | V (assumption) |

The pressure is converted from the raw voltage to bar when the file is read. The sensitivity is
**configurable** (`dmr_sens_mv_per_bar`, default 10 mV/bar), separately for each cylinder, through
the configuration file and the "Channel Scaling" dialog (10.9); a change recalculates everything.
All curves and characteristic values are in bar. The raw voltage remains available in the `TIME`
data set. PI_RM is given directly in amperes; factor and offset are also adjustable.

¹ The crank angle information is always present. If the CA column of a cylinder for which a DMR or
PI_RM signal exists is missing, the file is rejected with an error message.

Any column order. Tolerant name matching: upper/lower case does not matter, leading/trailing
spaces do not matter, space ≙ underscore (`CA High` = `CA_High` = `ca high`), a unit in brackets
is stripped (`DMR_H [bar]`). Unknown columns are ignored and reported in the log.

### 3.2 Format

- Delimiter detected automatically: `;`, `,`, tab. Decimal sign detected automatically: `.` or `,`
  (the combination of `,` as delimiter **and** decimal sign is rejected as an error).
- Encoding: UTF-8 (with/without BOM), fallback Latin-1.
- First line = column names. An optional second line with units is recognised and skipped.
- All values are read as `float64`. `Time` is never rounded or reduced to `float32`; all edge
  times are interpolated from the original time stamps.
- Empty cells / `NaN`: gaps ≤ `max_gap_samples` (default 3) are interpolated linearly; longer
  gaps make the affected cycle invalid.
- `Time` must increase strictly monotonically, otherwise the run aborts with the line number.
  Sample rate and jitter are determined and logged; non-equidistant sampling is permitted.
- **The sample rate differs from file to file** (30 kHz to 150 kHz) and is always determined from
  `Time`, never assumed. All quantities that depend on it (crank angle grid, filters, knock band,
  minimum durations) are derived from the determined rate.
- Typical measurement duration: a few seconds, i.e. up to about 1–2 million rows and a few dozen
  cycles per cylinder. Upper limit for the design: 10 million rows without special treatment.

### 3.3 Missing columns – scope of evaluation

Only the signal columns DMR, PI_RM and KLS can be missing. Each cylinder is treated independently;
if a signal is missing, the scope is reduced, not aborted.

| Signal present | Evaluation for this cylinder |
|---|---|
| DMR | Pressure and combustion values, knock from the pressure (7.4) |
| PI_RM | PI values in °CA and ms (section 8) |
| DMR and PI_RM | additionally ignition delay (start of energizing → start of combustion) |
| KLS | Knock sensor values (7.5) |
| none | Cylinder is skipped (message) |

If DMR, PI_RM **and** KLS are missing for both cylinders → abort with an error message.

### 3.4 Reference format (example file)

Derived from an excerpt of a real measurement file (the file itself is not part of the repository);
this format is the main use case and must be loaded without any correction in the import dialog.
The lines below come from the test data generator and only show the format:

```
Time,CA High,CA Low,DMR_L,DMR_H,PI_RM_L,PI_RM_H,KLS_H,KLS_L
0.0,275,35,2.0098258853,1.1325708446,1.9951874232,1.9802063610,…
3.3333333333333335e-05,275,35,1.9997641788,1.1328852729,2.0175835712,1.9943145317,…
6.666666666666667e-05,275,35,1.9897024722,1.1325708446,2.0047559503,2.0007805861,…
```

| Property | Finding |
|---|---|
| Delimiter / decimal sign | `,` / `.`; no units line |
| Number format | Full `float64` representation, partly exponential notation (`e-05`) |
| Column order | CA first High, then Low; signals first `_L`, then `_H` |
| Sampling | Equidistant, starts at 0; in the excerpt Δt = 1/30000 s (30 kHz), other files up to 150 kHz |
| Crank angle | **Integer in 1° steps**, about 3 samples per step; value range 0–720 (4-stroke) |
| Relation High/Low | Both CA columns switch at the same time with a fixed offset (in the test data set CA Low = CA High − 240°) |
| Signal resolution | DMR in steps of the converter resolution, order of magnitude 0.03 bar |
| Rest levels | DMR with a voltage offset of the order of 1 V (about 100 bar when calculated); PI_RM with a measurement offset of a few amperes |

Real measurement files additionally contain the columns `kls_h` and `kls_l` (upper/lower case does
not matter when reading); the test data generator appends them as `KLS_H`, `KLS_L`.

It follows that the angle between the steps has to be reconstructed (5.1), the crank angle grid
depends on the sample density (5.3), and the knock evaluation is limited to frequencies below
12 kHz at 30 kHz (7.4).
Such a rest level cannot be an absolute pressure; the pressure signal therefore carries an offset
and absolutely needs a zero level correction (5.4).

**TDC position (confirmed):** The file value 0 is the firing TDC, for High and Low each in their
own crank angle. The excerpt agrees with this: shortly after 0° the pressure is above the rest
level and falls (expansion), around 270° it is constant at the rest level (exhaust).

---

## 4. Configuration

TOML file (`--config`), all values optional; without geometry the volume dependent values are
omitted (section 7.3). Common values under `[engine]`, deviations per cylinder under `[cyl.H]` /
`[cyl.L]`.

```toml
[engine]
strokes        = 4          # 4 (CA 0–720, reference data) | 2 | "auto" (detected from the CA span)
name           = "Jenbacher Type 6 (assumed)"   # default, see 6.1
bore_mm        = 190.0      # published
stroke_mm      = 220.0      # published
conrod_mm      = 425.0      # ASSUMPTION, not published
compression    = 11.0       # compression ratio ε; version dependent (10.5 … 11.0 found)
pin_offset_mm  = 0.0        # piston pin offset; assumption
kappa          = 1.32       # polytropic/isentropic exponent for heat release

[cyl.H]
ca_offset_deg  = 0.0        # added to the CA of the file so that TDC = 0 °CA
dmr_sens_mv_per_bar = 10.0  # sensitivity of the pressure chain; p[bar] = U[V] · 1000 / value
dmr_offset_bar = 0.0        # added after the conversion
pi_factor      = 1.0        # signal is already in A
pi_offset      = 0.0

[cyl.L]
ca_offset_deg  = 0.0
dmr_sens_mv_per_bar = 10.0  # adjustable separately for each cylinder
dmr_offset_bar = 0.0
pi_factor      = 1.0
pi_offset      = 0.0

[ca]
resolution_deg = "auto"     # grid of the crank angle based curves: "auto" | 0.1 | 0.2 | 0.5 | 1.0
resample       = "interp"   # "interp" | "binmean"
angle_recon    = "smooth"   # angle between encoder steps: "smooth" | "linear"
angle_smooth_deg = 30.0     # smoothing window for "smooth"
step_mode      = "floor"    # meaning of the angle step: "floor" | "round" (see 5.1)
max_gap_samples = 3         # gaps up to this length are interpolated

[pegging]
method         = "thermo"   # "none" | "thermo" | "fixed"
ca1_deg        = -100.0
ca2_deg        = -65.0
window_deg     = 2.0        # averaging window around ca1/ca2
kappa          = 1.32
fixed_ca_deg   = -180.0     # only "fixed"
fixed_p_bar    = 1.0        # only "fixed"

[filter]
pressure       = "savgol"   # "none" | "moving_average" | "savgol" | "butter"
window_deg     = 3.0        # at least 5 grid points
butter_cutoff_hz = 5000

[heat_release]
start_deg      = -30.0
end_deg        = 90.0

[pi]
threshold_mode = "relative" # "relative" (fraction of the cycle maximum) | "absolute" (A)
threshold      = 0.10
hysteresis     = 0.05
min_duration_ms = 0.05
min_gap_ms     = 0.10
baseline       = "auto"     # measurement offset: "auto" (median per cycle) | fixed value in A
hydraulic_delay_ms = 0.0    # optional: start of energizing → hydraulic start

[knock]
enabled        = true
band_hz        = [2000, 10000]   # suited to a 190 mm bore, see 7.4
filter_order   = 4
window_deg     = [-10, 70]       # evaluation window
ref_window_deg = [-90, -40]      # reference window for the noise level (before ignition)
threshold_bar  = 1.0             # ASSUMPTION: KP_PK above this = knocking cycle
threshold_ratio = 0.0            # optionally in addition: KP_PK / KP_REF; 0 = off

[output]
cycles         = "all"      # "all" | "10:200"
csv_delimiter  = ";"
csv_decimal    = "."
plots          = ["png"]    # "png" | "pdf" | "html"
```

---

## 5. Signal conditioning

Fixed order: read → scaling → angle reference → cycle detection → crank angle grid →
zero level correction → filtering → characteristic values.

### 5.1 Angle reference

- Internal convention: 4-stroke α ∈ [−360, +360), 2-stroke α ∈ [−180, +180), **0 = TDC**.
- `α = wrap(CA_file + ca_offset_deg)`. For the reference data (0–720) and `ca_offset_deg = 0`
  this means: file values 0…359 stay, 360…719 become −360…−1. Since the file value 0 is the firing
  TDC of both cylinders, `ca_offset_deg` stays at 0 and only serves as a fine correction.
- The TDC reference is **never shifted silently** (1 °CA of TDC error falsifies IMEP by several
  percent). If the pressure maximum of the mean cycle lies more than ±40 °CA away from 0, a warning
  with a suggestion for `ca_offset_deg` is given.
- **Stepped crank angle** (the normal case, see 3.4: 1° steps with about 3 samples per degree):
  the angle of each sample is reconstructed from the step edges. The edge time is taken as the
  middle between the last sample of the old step and the first of the new step (uncertainty
  ± ½ sample interval, here about ± 0.15 °CA at 1500 1/min and 30 kHz).
  - `linear`: time-linear interpolation between neighbouring edges.
  - `smooth` (default): in addition, the edge times are smoothed over a moving window
    (`angle_smooth_deg`), because the speed hardly changes within a few degrees. This reduces the
    angle error from the time grid considerably; important for IMEP and heat release.
- If both CA columns switch at the same time, the fixed offset High/Low is determined and logged;
  it is not assumed.
- **Meaning of the step (`step_mode`):** With `floor` (default) the file value N stands for the
  range N…N+1, so the value N has just been reached at the step edge. With `round` N stands for
  N−0.5…N+0.5. Which convention the measuring system uses is not known (section 15); the wrong
  choice shifts all angles by 0.5 °CA.

### 5.2 Cycle detection

- Cycle boundary = wrap of the **internal** angle α from +360 to −360, i.e. at gas exchange TDC –
  not the wrap of the file value from 720 to 0. If the firing TDC is at the file value 0, the wrap
  of the file would fall in the middle of the combustion and split it over two cycles.
  Separate for each cylinder, since each has its own CA; the cycles of High and Low are therefore
  offset in time.
- Incomplete cycles at the start/end of the file are discarded. Numbering starts at 1.
- Plausibility: cycle duration deviates > 20 % from the median, CA not monotonic within the cycle,
  or NaN gap → cycle `valid = 0`; it stays in the table but does not enter the statistics.
- Per cycle: start time, duration, mean speed `n = (period/360) / T_cycle · 60` [1/min].

### 5.3 Crank angle grid

`resolution_deg = "auto"` selects the smallest grid width of 0.1 / 0.2 / 0.5 / 1.0 °CA that is not
finer than the mean angle spacing of two samples – so the grid is not finer than the measurement
resolves (1500 1/min: at 30 kHz 0.30 °CA per sample → grid 0.5 °CA; at 150 kHz 0.06 °CA → grid
0.1 °CA). A fixed value can be set; if it is below the sample density, a message appears.

Each cycle is brought to an equidistant grid (`resolution_deg`):
`interp` = linear interpolation, `binmean` = mean of all samples per angle step (less noisy with
strong oversampling). The raw data remain unchanged for time based evaluations (PI edges, knock).

### 5.4 Zero level correction (pressure)

- `thermo` (default): polytropic compression between α1 and α2,
  `Δp = (p2·V2^n − p1·V1^n) / (V1^n − V2^n)`, `p_corr = p + Δp`, per cycle; p1/p2 as window means.
  Needs geometry.
- `fixed`: the window mean at `fixed_ca_deg` is set to `fixed_p_bar`.
- `none`: the signal is already an absolute pressure. Unsuitable for the reference data, since the
  raw signal carries an offset of the order of 100 bar (3.4).
- If `thermo` is not possible for lack of geometry → automatically `fixed` with a warning; without
  a correction PMAX and all pressure curves would be falsified by the offset.
- The determined correction is output per cycle as the value `POFFS` [bar], so that a drift of the
  measuring amplifier over the measurement becomes visible.

### 5.5 Filtering

Filtered pressure is used **only** for the derivative and the heat release (zero phase). PMAX, IMEP
and the knock values are determined from the unfiltered signal.

---

## 6. Geometry

```
Vh   = π/4 · B² · S                Vc = Vh / (ε − 1)          r = S/2
s(α) = sqrt((l + r)² − e²) − r·cos α − sqrt(l² − (r·sin α − e)²)
V(α) = Vc + π/4 · B² · s(α)
```

`dV/dα` analytically or as a central difference quotient on the crank angle grid. The geometry
applies to High and Low together; a deviation per cylinder remains adjustable.

### 6.1 Default: Jenbacher Type 6

Until the data of the actual engine are available, a Jenbacher Type 6 (J612/J616/J620/J624) is
preset. All values can be changed in "Engine Parameters".

| Parameter | Value | Source |
|---|---|---|
| Bore `BORE` | 190 mm | Published data sheet |
| Stroke `STROKE` | 220 mm | Published data sheet |
| Displacement per cylinder | 6.24 l | Data sheet; agrees with π/4 · 190² · 220 |
| Working principle `NRSTROKE` | 4-stroke, V 60° | Data sheet; fits CA 0–720 |
| Rated speed | 1500 1/min | Data sheet; fits the example file (about 3.3 samples per degree at 30 kHz) |
| Compression `EPSILON` | 11.0 | **Version dependent:** 11.0 in data sheets of the 620 GS-N.L, 10.5 for the J620 GS-J701 |
| Conrod length `CONROD` | 425 mm | **Assumption**, not published; from the usual crank radius to conrod ratio r/l ≈ 0.26 |
| Piston pin offset `PINOFF` | 0 mm | Assumption |
| κ heat release / zero level correction | 1.32 | Assumption for lean-burn gas operation |

Classification of the two assumptions: the conrod length hardly influences IMEP and influences the
mass fraction burned points only slightly. The compression ratio, on the other hand, acts directly
on the zero level correction and the heat release; an error of 0.5 units noticeably shifts the
pressure level and the heat conversion. As long as these values are not confirmed, the report
marks the thermodynamic values with "assumed engine data".

The combustion process (spark-ignited lean-burn gas engine with scavenged pre-chamber, mean
pressure 22–24 bar) is used only for plausibility checks; it does not enter the calculation.

---

## 7. Characteristic values from the cylinder pressure (per cycle)

### 7.1 Calculable without geometry

| Name | Unit | Definition |
|---|---|---|
| `PMAX` | bar | Maximum pressure |
| `APMAX` | °CA | Position of PMAX from a least squares parabola over ± 3 °CA (insensitive to noise at the flat maximum) |
| `RMAX` | bar/°CA | Maximum pressure rise dp/dα |
| `ARMAX` | °CA | Position of RMAX |
| `SPEED` | 1/min | Mean speed of the cycle |
| `POFFS` | bar | Zero level correction of the cycle (5.4) |

In addition there are the knock values according to 7.4; these also need no geometry.

### 7.2 With geometry

| Name | Unit | Definition |
|---|---|---|
| `IMEP` | bar | `1/Vh · ∮ p dV` over the whole cycle |
| `IMEPH` | bar | High pressure loop (−180…+180 °CA), 4-stroke only |
| `IMEPL` | bar | Gas exchange loop (= IMEP − IMEPH), 4-stroke only |
| `Q(α)` | J/°CA | Heat release rate: `κ/(κ−1)·p·dV/dα + 1/(κ−1)·V·dp/dα` |
| `I(α)` | J | Cumulative heat release: `∫ Q dα` over [`start_deg`, `end_deg`] |
| `QMAX`, `AQMAX` | J/°CA, °CA | Maximum of the heat release rate and its position |
| `IMAX` | J | Maximum of the cumulative heat release (converted heat) |
| `AI05`, `AI10`, `AI50`, `AI90` | °CA | Position of 5/10/50/90 % conversion; 0 % = minimum, 100 % = maximum of `I` |
| `SOC` | °CA | Start of combustion = `AI05` |
| `BD` | °CA | Burn duration `AI90 − AI10` |
| `POLY_C`, `POLY_E` | – | Polytropic exponent compression/expansion (regression of log p / log V), diagnostic value |

Mass fraction burned points are interpolated linearly between the grid points.

### 7.3 Missing geometry

Only sections 7.1 and 7.4 are calculated; the columns from 7.2 stay empty; a single warning in the
log and in the report.

### 7.4 Knock evaluation

Active by default for every cylinder with a DMR signal. It works time based on the unfiltered
pressure signal converted to bar, not on the crank angle grid.

1. **Band-pass:** Butterworth (`filter_order`), zero phase, band `band_hz`.
2. **Evaluation window** `window_deg` per cycle (default −10…+70 °CA).
3. **Reference window** `ref_window_deg` (default −90…−40 °CA, before ignition): delivers the noise
   level of the measuring chain in the same band.

| Name | Unit | Definition |
|---|---|---|
| `KP_PK` | bar | Peak of the absolute value of the band-pass filtered pressure in the evaluation window |
| `AKP_PK` | °CA | Position of `KP_PK` |
| `KP_INT` | bar·°CA | Integral of the absolute value over the evaluation window |
| `KP_REF` | bar | Peak of the absolute value in the reference window |
| `KP_RATIO` | – | `KP_PK / KP_REF` |
| `KNOCK` | 0/1 | Knocking cycle: `KP_PK > threshold_bar`; with `threshold_ratio > 0` additionally `KP_RATIO > threshold_ratio` |

The band-pass filtered pressure is available for display as the channel `PKNK_H/L` in the data sets
`CA` and `TIME`.

**Band and sample rate.** The default 2–10 kHz is matched to the 190 mm bore (first combustion
chamber resonance about 3 kHz by our own estimate). It is evaluated only if the sample rate is
≥ 2.5 × the upper band limit, i.e. from 25 kHz here; otherwise the band is shortened with a message.

**Limits of the statement:**

- The threshold of 1.0 bar is an assumption and has to be set for the engine; `KP_PK` and `KP_INT`
  themselves do not depend on it.
- At 30 kHz, components above 15 kHz are not captured and, if the measuring chain has no
  anti-aliasing filter, can fold back into the band. For knock investigations, measurements from
  50 kHz are preferable.
- The converter resolution of about 0.031 bar (3.4) limits the smallest detectable amplitude.
- The band-pass does not distinguish between knock and other oscillations in the band, such as
  resonances of an indicating channel or of the sensor mounting. `KP_REF` and `KP_RATIO` make such
  continuous oscillations visible.

### 7.5 Knock sensor (KLS)

The columns `KLS_H` / `KLS_L` are interpreted as a structure-borne sound knock sensor (assumption,
as is the unit volt). The signal is provided unchanged as the channel `KLS_H/L` and – if the knock
evaluation is active – evaluated with the same band, evaluation window and reference window as the
pressure (7.4). Geometry and a pressure signal are not needed for this.

| Name | Unit | Definition |
|---|---|---|
| `KLS_PK` | V | Peak of the absolute value of the band-pass filtered sensor signal in the evaluation window |
| `AKLS_PK` | °CA | Position of `KLS_PK` |
| `KLS_INT` | V·°CA | Integral of the absolute value over the evaluation window |
| `KLS_REF` | V | Peak in the reference window (background noise) |
| `KLS_RATIO` | – | `KLS_PK / KLS_REF` |

The filtered signal is available for display as `KLSF_H/L`. **No** knock decision is derived from
the knock sensor; `KNOCK` and `KNOCK_FREQ` are based on the pressure alone. The travel time of the
structure-borne sound to the sensor is not corrected.

---

## 8. Characteristic values from the PI current signal

### 8.1 Event detection

1. The signal carries a measurement offset of a few amperes. It is determined per cycle as the
   median of the signal outside the energizing (`baseline = "auto"`) or set to a fixed value and
   subtracted from the signal; noise = MAD. The channel `PI` in the data set `CA` is offset
   corrected, the raw channel `PI_RM` in the data set `TIME` remains unchanged.
2. Event = range above the threshold; the end only below `threshold − hysteresis`.
3. Events shorter than `min_duration_ms` are discarded; gaps shorter than `min_gap_ms` are merged
   (boost/hold phase and current regulation must not count as several energizings).
4. Edge time = linearly interpolated threshold crossing between two samples (sub-sample accurate,
   hence the full time resolution from `Time`). Angle from the time through the α(t) assignment.

### 8.2 Characteristic values

Per cycle `PI_N` (number of energizings), per event *k* = 1…N (column suffix `_1`, `_2`, …):

| Name | Unit | Definition |
|---|---|---|
| `PI_SOE_k` | °CA | Start of energizing (rising edge) |
| `PI_EOE_k` | °CA | End of energizing (falling edge) |
| `PI_TSOE_k`, `PI_TEOE_k` | s | The same edges as absolute time |
| `PI_DUR_k` | ms | Energizing duration |
| `PI_DURA_k` | °CA | Energizing duration as an angle |
| `PI_IPK_k` | A | Peak current |
| `PI_TPK_k` | ms | Time from start of energizing to peak current |
| `PI_IHOLD_k` | A | Mean hold current (median of the second half of the event) |
| `PI_RISE_k` | ms | Rise time 10 → 90 % of the peak current |
| `PI_CHG_k` | A·ms | Current integral over the event |

All current values (`PI_IPK`, `PI_IHOLD`, `PI_CHG`) are offset corrected, i.e. real currents. In
addition, per cycle `PI_OFFS` [A]: the subtracted measurement offset, for checking for drift.

Combined with DMR: `IGD` = `SOC − PI_SOE_1` in °CA and `IGD_T` in ms (ignition delay, referred to
the electrical start of energizing; with `hydraulic_delay_ms` optionally referred to the hydraulic
start).

Note: the current signal shows the **electrical** energizing, not the hydraulic start of injection.

---

## 9. Statistics and cylinder comparison

- For every scalar characteristic value over all valid cycles: mean, standard deviation, min, max,
  COV [%], count.
- Measurements of a few seconds yield only a few dozen cycles. The number of cycles is therefore
  shown next to every statistical value; with fewer than 10 valid cycles the COV is marked with a
  note.
- For every curve (p, Q, I, PI current): mean cycle, min/max envelope, standard deviation.
- Knock: `KNOCK_FREQ` [%] = share of knocking cycles among the valid cycles; for `KP_PK` also the
  95 % percentile.
- High against Low: difference of the means per characteristic value, overlaid curves.
- Cycle selection with `--cycles`.

---

## 10. User interface

### 10.1 Principle

Anyone who knows Concerto should be able to work without re-learning: WorkFlow bar on the left,
channels on the right in the Channel Browser, display in layouts made of pages with diagrams,
curves into the diagram by drag & drop, measurement cursor with values in the legend, layout and
data separated (a layout fits every measurement).

**The model is the classic Concerto user interface** (the screenshot exists only locally and is not
part of the repository): menu bar and tool bars (no ribbon), WorkFlow bar, layout window with page
tabs, Channel Browser, cycle selection in the status bar. Arrangement, naming and order of the
controls follow this picture. What the picture does not show (menu contents, context menus,
dialogs, keyboard shortcuts) is implemented according to the usual operating logic and adapted when
further screenshots are available (section 15, item 9).

### 10.2 Main window

```
+-----------------------------------------------------------------------------+
| Open Curve Tool - <workspace> - overview.oclayout * - [Page 1 - Cycle: 1/5] |
| File  Edit  View  Components  Extras  Execute  Window  Help                 |
| New  Open  Save  Print  |  Page v  |  Design  |  Zoom Back  Show All        |
| Data Explorer | Object Wizard | Calculator | Formula Editor | Evaluation    |
+----------+---------------------------------------------+--------------------+
| WorkFlow | [Indicating Diagram 1 x]                    | Channel Browser    |
|          |  Test Parameter:  BORE, STROKE, CONROD ...  | FILE1 (meas.csv)   |
| Data     |  +----------------------------------------+ |--------------------|
| Access   |  |              :     Crank Angle  deg    | | CA    Crank Angle  |
|          |  |              :     PCYL_H       bar    | | CY    Cycle Based  |
| Data     |  |            .-:-.   PCYL_L       bar    | | TIME  Time Based   |
| Display  |  |          .'  :  '.                     | | STAT  Statistics   |
|          |  |  ______.'    :    '.________________   | | PAR   Parameters   |
| Calcu-   |  +----------------------------------------+ |--------------------|
| lations  |   -90 -60 -30   0   30   60   90   120      | <channel filter>   |
|          |              Crank Angle [deg]              | PCYL_H   bar       |
| Export   |                                             | PI_H     A         |
| Utilities|                                             | Q_H      J/deg     |
+----------+---------------------------------------------+--------------------+
| Ready                                      Zoom - ---o--- +    [ 1 ]  Cycle |
+-----------------------------------------------------------------------------+
```

- **Title bar:** program name, workspace, layout file (`*` for unsaved changes), active page and
  `Cycle: current/total`.
- **Menu bar:** "File", "Edit", "View", "Components", "Extras", "Execute", "Window", "Help".
- **Tool bar 1:** New/Open/Save/Print, page selection "Page", switch "Design" (edit mode), zoom
  and cursor tools.
- **Tool bar 2:** "Data Explorer" (load and manage measurement files), "Object Wizard" (insert
  diagram, table, text), "Calculator", "Formula Editor", "Evaluation" (evaluation settings, in the
  place of "GCA/Burn"). There is no counterpart to "CalcGraf" (section 1.3).
- **WorkFlow bar (left):** large buttons "Data Access", "Data Display", "Calculations", "Export",
  "Utilities"; each opens a fly-out menu with icon, title and a one-line description (e.g.
  "Calculations" → "Calculator", "Formula Editor"). At the bottom the tabs "WorkFlow" and
  "Layouts" (list of the available layouts).
- **Layout window (centre):** one tab per opened page; the page is shown as a sheet with a border,
  in design mode with a grid.
- **Channel Browser (right):** see 10.3. On the right edge vertical tabs "Object Library" and
  "Layout Explorer".
- **Status bar:** message/calculation status on the left, zoom slider, cycle selection on the right.
- All panels can be docked, moved and hidden; the arrangement is saved.
- In the place of the AVL logo and the Concerto lettering there is "Open Curve Tool"; a company
  logo on the page is a freely selectable image object.
- **Language: English.** All texts of the user interface – menus, dialogs, axis and legend
  labels, messages, log and report – are in English; the terms follow the English Concerto user
  interface. Crank angle is labelled `°CA`. There is no language switch. In this specification the
  controls are named with their English names in quotation marks.

### 10.3 Channel Browser and Data Explorer

The Channel Browser is divided into three parts, as in the model:

1. **File list:** loaded measurements as `FILE1 (filename.csv)`, `FILE2 (…)`, …
2. **Data sets** of the selected file, with the number of points or cycles in brackets:

   | Data set | Content | x reference |
   |---|---|---|
   | `CA – Crank Angle` | `PCYL_H/L`, `PFILT_H/L` (filtered), `DP_H/L` (pressure rise), `V_H/L`, `Q_H/L`, `I_H/L`, `MFB_H/L` (mass fraction burned 0…1), `PI_H/L`, `PKNK_H/L`, `KLS_H/L`, `KLSF_H/L` per cycle, plus mean/min/max | Crank angle |
   | `CY – Cycle Based` | All characteristic values from sections 7 and 8, e.g. `PMAX_H`, `IMEP_L`, `PI_SOE_1_H` | Cycle number |
   | `TIME – Time Based` | Raw signals `DMR_H/L` [V], `PI_RM_H/L`, `CA_H/L`; plus `P_H/L` (pressure in bar, zero level corrected), `PI_H/L` (offset corrected), `PKNK_H/L`, `KLS_H/L`, `KLSF_H/L`, `ALPHA_H/L` (reconstructed angle), `SPEED_H/L` | Time |
   | `STAT – Statistics` | Mean, deviation, COV per characteristic value | – |
   | `PAR – Parameters` | `FILENAME`, `ENGINE`, `NRSTROKE`, `BORE`, `STROKE`, `CONROD`, `EPSILON`, `PINOFF`, `POLY_COEFF`, `FSAMPLE`, `DURATION`, `NSAMPLES`, `CARES` and per cylinder `CAOFFS`, `PSENS`, `NCYCLES` | – |

3. **Channel list** of the selected data set with the columns name, unit, description; above it the
   filter field "Edit Channel Name Filter" (wildcard `*`, e.g. `P*`).

The cylinder appears as the suffix `_H` / `_L` in the channel name. The parameter names correspond
to the Concerto names; the mapping to section 4 is one to one (`BORE` = `bore_mm` etc.).

"Data Explorer" (tool bar 2 and WorkFlow "Data Access") opens the file management: load and remove
measurement files, view file information (sample rate, duration, number of cycles, recognised
columns).

- Signals that are not present, and channels that depend on them, do not appear in the channel list.
- Several measurement files can be loaded at the same time (comparison). One file is "active"; the
  layout shows the active file, others can be overlaid as comparison curves.
- Open through the menu, recently used files, or by dropping the CSV on the window.
- Search field for filtering the channels; context menu: show in new diagram, properties, export.
- The import dialog shows the detected format (delimiter/decimal sign, units line), a preview and
  the correctable column assignment. It appears only if the automatic detection fails or the
  columns cannot be assigned, and on request through "Open with Import Options". The reference
  format (3.4) loads without asking.

### 10.4 Layouts and pages

- A layout is a file (`.oclayout`, JSON) with any number of pages. A page contains freely arranged
  objects: 2D diagram, table, text field, parameter table (name, unit, value from the data set
  `PAR`, like "Test Parameter" in the model), image.
- "Layout Explorer" shows pages and objects of the layout as a tree; "Object Library" holds
  preconfigured diagrams and tables that are inserted on the page by double-click.
- Layouts refer to channels by name, not to a file. If a channel is missing in the active
  measurement, the curve stays empty and the legend entry is greyed out – not an error.
- Switch "Design": in design mode, objects can be inserted, moved, resized, copied and deleted;
  otherwise analysis mode (zoom, cursor), in which objects cannot be moved by accident.
- The layout "Standard" (`opencurve/layouts/Standard.oclayout`) is supplied, with the pages
  "Indicating Diagram", "PI Current Diagram", "Cycle Diagram", "Heat Release Diagram",
  "Knock Diagram", "High Low Comparison" and "Raw Data". Own layouts are kept in the user folder and
  appear in the "Layouts" tab.
- At start-up the most recently used layout is loaded.

### 10.5 Diagrams

- Add a curve: drag a channel from the Channel Browser into the diagram. Dropping on an existing
  axis uses it, dropping at the edge creates a new y axis.
- The x axis follows from the channel group (°CA, time, cycle number) or is any channel (x-y
  display, e.g. p over V).
- Several y axes side by side as well as axis areas stacked on top of each other with a common x
  axis.
- Axes linear/logarithmic; auto scaling or fixed limits (double-click on the axis).
- Zoom: draw a rectangle, mouse wheel, drag the axis; "Zoom Back" (history) and "Show All".
- Legend as a table in the diagram as in the model: header row with x channel, unit and cursor
  position, below it per curve name, unit and value at the cursor in the curve colour. Movable;
  further columns (file, cycle, second cursor, difference) can be switched on.
- Appearance as in the model: white drawing area, dotted grid, axis title with unit in square
  brackets, cursor as a red vertical line.
- Curve properties: colour, line style, line width, marker, visibility, axis assignment.
- Fixed colour assignment High/Low across all diagrams.
- Markers from the evaluation can be shown: AI05/AI50/AI90, PMAX, PI edges, evaluation window,
  knock and reference window, knock threshold.
- Time based raw data are reduced to min/max for display, so that peaks remain visible.

### 10.6 Measurement cursor

- Two cursors (C1, C2) per diagram; display of x, y per curve as well as Δx and Δy.
- Move with the mouse or the arrow keys (stepwise from sample point to sample point).
- Cursors optionally coupled across all diagrams of a page with the same x reference.
- For °CA diagrams the corresponding time is shown in addition, for time diagrams the angle.
- Cursor values can be copied to the clipboard.

### 10.7 Cycle navigation

- Cycle selection on the right of the status bar (number field with up/down, label "Cycle") as in
  the model; the active cycle is also shown in the title bar. In addition a slider and playback.
- Display modes for °CA curves: single cycle, cycle range overlaid, all cycles, mean cycle,
  envelope; adjustable globally and overridable per curve.
- A click on a point in the cycle trend or on a table row jumps to that cycle.
- Invalid cycles are marked and can be shown/hidden; individual cycles can be excluded from the
  statistics by hand.

### 10.8 Tables

Characteristic value table (cycles × values) and statistics table; column selection, sorting, copy
to the clipboard, export as CSV.

### 10.9 Settings and recalculation

All values from section 4 can be changed through dialogs: "Engine Parameters" (geometry, TDC
offset per cylinder), "Channel Scaling" (pressure sensitivity in mV/bar and offset per cylinder,
factor and offset for PI_RM; with a preview of the converted value range), "Evaluation" (zero level
correction, filter, heat release window, κ), "PI Detection" (threshold, hysteresis, minimum
duration), "Knock". After "Apply" the recalculation runs in the background and all diagrams are
updated. Settings can be saved and loaded as a TOML file.

### 10.10 Formula channels

Own channels through formulas from existing channels (e.g. `IMEP_H - IMEP_L`, `P_H * 1e5`), entered
as text with the usual arithmetic and statistics functions. Two ways in, as in the model:
"Calculator" (assemble the formula by clicking keys and selecting channels) and "Formula Editor"
(text editor for several formulas). Formula channels appear in the Channel Browser and are saved in
the layout.

### 10.11 Export and printing

- Page as PNG, page or whole layout as PDF (A4 landscape; "Print" also creates a PDF); diagram and
  cursor values to the clipboard.
- Data of the visible curves or of a channel as CSV.
- Result tables and report (section 11) of the active measurement into a selectable folder.
- Workspace (loaded files, layout, settings) can be saved and restored.

### 10.12 Second model: page structure only

The second model (a screenshot of an earlier program of the client, not in the repository) was
adopted completely as a trial and then taken back on request. The decision is:

- **Appearance as in 10.2–10.7:** dark blue WorkFlow bar with the tabs "WorkFlow" and "Layouts",
  tool bar 1 with text and icon, Channel Browser with separate file and data set lists, display
  mode, slider and cycle field in the status bar, dotted grid, legend without cell borders, visible
  message window.
- **Taken over from the second model:** the standard pages (10.4), in particular "Indicating
  Diagram" as a page-filling pressure diagram with the framed table "Test Parameter:" at the top
  left inside the diagram; the table is never smaller than its content. Also the channel
  `MFB_H/L` and the crank angle axis with a round division (every 10° in the normal view).

`referenz/opencurvetool_overview.png` shows the implemented state.

### 10.13 Technology and response times

- User interface with Qt (`PySide6`) and `pyqtgraph`; `matplotlib` only for PDF/report output.
- **Platforms: Windows 10/11 and Linux** (X11 and Wayland) from the same source code, without
  platform specific functional differences. Paths through `pathlib`, settings in the respective
  user directory, layout and settings files exchangeable between both systems, display independent
  of the screen scaling. Tests run on both systems.
- Calculation core without GUI dependency, so that the batch run and the tests work without a user
  interface.
- Reading and calculating run in the background with a progress display; the user interface stays
  usable.
- Zooming/panning stays smooth with 10 million raw data points; changing the cycle takes effect
  without noticeable delay.

---

## 11. Outputs

All files in `<outdir>/<measurement file name>_…`.

| File | Content |
|---|---|
| `_cycles.csv` | One row per cylinder and cycle: `cyl`, `cycle`, `valid`, `t_start`, all characteristic values |
| `_stats.csv` | Statistics per cylinder and characteristic value |
| `_curves_H.csv`, `_curves_L.csv` | Crank angle grid: mean cycle + envelopes for p, V, Q, I, PI current |
| `_allcycles_H.npz`, `_allcycles_L.npz` | All single cycles on the crank angle grid (optional, `--save-cycles`) |
| `_report.html` | Report: measurement info, configuration, warnings, statistics tables, all diagrams |
| `_log.txt` | Log including the detected format, sample rate, discarded cycles |

Diagrams (per cylinder, additionally High/Low overlaid); they are at the same time the standard
layouts of the user interface and the content of the report in the batch run:

1. Pressure over °CA: single cycles thin, mean bold; PI current on a second axis.
2. p-V diagram, linear and double logarithmic.
3. Heat release rate and cumulative heat release with markers AI05/AI50/AI90.
4. PI current in detail with marked edges (zoom on the energizing window).
5. Cycle trend: IMEP, PMAX, AI50, PI_SOE, PI_DUR over the cycle number.
6. Scatter plot PMAX over APMAX, histogram of IMEP.
7. Time based raw data view (overview + excerpt over the first cycles).
8. Knock: pressure with overlaid band-pass signal and windows, `KP_PK` over cycle number with
   threshold, histogram of `KP_PK`, `KP_PK` over AI50.

---

## 12. Invocation and structure

```
python opencurve.py                         # start the user interface
python opencurve.py measurement.csv         # user interface with a loaded measurement
python opencurve.py --batch measurement.csv [--config engine.toml] [--out results/]
                                        [--layout standard.oclayout]
                                        [--cyl H,L] [--cycles 10:200]
                                        [--no-plots] [--save-cycles] [-v]
```

- One start script `opencurve.py`; the code lives in a package:

  ```
  opencurve.py        Start (user interface or --batch)
  opencurve/core/     Reading, cycles, thermodynamics, PI, statistics, export
  opencurve/gui/      Main window, explorer, diagrams, dialogs
  opencurve/layouts/  Standard layouts
  tests/              Tests and test data generator
  ```

- Python ≥ 3.11; dependencies `numpy`, `scipy`, `pandas`, `matplotlib`, `PySide6`, `pyqtgraph`.
- Distribution for now exclusively as a Python program (source code + `requirements.txt`) for
  Windows and Linux; a finished program (EXE/AppImage) is not part of this scope.
- Batch run: exit code 0 = ok, 1 = input error (file/columns/format), 2 = no evaluable cycles; at
  the end a summary on the console (number of cycles, means of the main characteristic values per
  cylinder, number of warnings).

---

## 13. Verification and acceptance

### 13.1 Synthetic test data

The generator `make_testdata.py` creates CSV files with known reference values: motored pressure
polytropic + combustion by a Vibe function (IMEP and AI50 given), PI current with boost/hold phase
and defined edges, noise, optionally missing columns, stepped CA, `,`/`.` decimal format.
The standard output is exactly the reference format from 3.4 (30 kHz, integer CA in 1° steps, same
column order and number representation); the tolerances below apply to this format. In addition
variants with 50, 100 and 150 kHz, with measurement offset on DMR and PI_RM, and with an impressed
knock oscillation of known amplitude in some of the cycles.

| Characteristic value | Tolerance against reference value |
|---|---|
| IMEP | ± 0.5 % |
| PMAX | ± 0.1 % |
| AI50 | ± 0.2 °CA |
| APMAX | ± 0.3 °CA |
| AI05, AI10, AI90 | ± 0.5 °CA |
| PI edge times | ± 0.2 sample interval, at least ± 2 µs |
| PI_SOE (angle) | ± 0.1 °CA |
| KP_PK (impressed oscillation in the band) | −25 % … +5 % of the impressed amplitude |
| KNOCK (knocking / non-knocking cycles) | All assigned correctly |

These tolerances are the values reached by the implemented program at 30, 50, 100 and 150 kHz
(tests in `tests/test_core.py`). Two points deviate from the first draft:

- **PI edges:** The threshold depends on the sampled peak value; at kinks of the signal the linear
  interpolation between two samples is not exact. At high sample rates the signal noise
  (about 1–2 µs) limits the result, not the time grid.
- **KP_PK:** The band-pass attenuates an abruptly starting, decaying oscillation near the lower band
  limit by about 15 %. This is a property of the method; the classification of the cycles as
  knocking is not affected by it.

### 13.2 Comparison with Concerto

As soon as a Concerto export (cycle values + mean pressure curve) exists for a measurement file:
adopt the same settings (κ, zero level correction, evaluation windows, filter) and document the
deviations of the means. Target values: IMEP ± 1 %, AI50 ± 0.3 °CA, PMAX ± 0.2 %.

### 13.3 Mandatory tests

Calculation core: every row of the table in 3.3; both decimal formats; file with an incomplete
first/last cycle; non-monotonic time (must abort); missing CA column with an existing signal (must
abort); missing geometry (reduced scope, no abort).

User interface: load a file and show the standard layout; channel into a diagram by drag & drop;
cursor values against raw data; changing the cycle updates all diagrams; saving/loading a layout
gives the same display; layout on a measurement with missing signals (empty curves, no error);
changing a setting triggers a recalculation; batch run and user interface deliver identical
characteristic values.

---

## 14. Development stages

All seven stages are implemented; only the fine adjustment of the user interface on the basis of
further screenshots is open (section 15, item 9).

| Stage | Content |
|---|---|
| 1 | Calculation core: reading, format detection, cycle detection, angle reconstruction, crank angle grid, values 7.1, test data generator |
| 2 | User interface basic framework after the model: main window, menus, WorkFlow bar, Channel Browser, diagram with drag & drop, zoom, cycle selection |
| 3 | Thermodynamics (geometry, zero level correction, values 7.2), knock evaluation (7.4) and PI evaluation (section 8) including the settings dialogs |
| 4 | Measurement cursor, several axes/axis areas, tables, statistics, High/Low comparison |
| 5 | Saving/loading layouts, standard layouts, edit mode, several measurement files |
| 6 | Export/printing, report, batch run, workspace |
| 7 | Formula channels, fine adjustment of the user interface with Concerto |

---

## 15. Open points

| # | Question | Assumption until clarified |
|---|---|---|
| 1 | Conrod length, compression ratio and piston pin offset of the actual engine (Type 6 version); so far assumptions (6.1) | 425 mm, ε = 11.0, 0 mm |
| 2 | What technically distinguishes "High" and "Low"? | Two cylinders of the same engine, same evaluation |
| 4 | Does the DMR signal carry a voltage offset? The rest level in the excerpt would correspond to about 100 bar when calculated | Yes, measures relatively → zero level correction per cycle |
| 7 | Knock threshold and frequency band for the engine; does the measuring chain have an anti-aliasing filter? Does κ = 1.32 fit lean-burn gas operation? | 1.0 bar, 2–10 kHz, κ = 1.32 |
| 8 | Which Concerto characteristic values and layouts are actually used? Is a reference export available? | Scope as above |
| 9 | Further screenshots of the model: opened menus, context menus, dialogs (axis, curve, cursor), layout pages in use | What is not visible is implemented according to the usual operating logic |
| 13 | What exactly do `kls_h` / `kls_l` measure (knock sensor?), in which unit, and which frequency band makes sense for them? | Structure-borne sound knock sensor in volt, band as for the pressure (2–10 kHz) |
| 11 | Does the integer crank angle mean "value reached" (truncated) or "rounded"? | Truncated (`step_mode = "floor"`); wrong choice = 0.5 °CA offset |
| 12 | What does "PI" stand for, and where in the working cycle is the energizing? Events are assigned to the cycle in which they start; if the energizing lies around ± 360 °CA (gas exchange TDC) it can fall on two cycles | Energizing does not lie at the cycle boundary |

Clarified: CSV format, sample rate and crank angle resolution through the example file (3.4); DMR
is a raw voltage with configurable sensitivity (default 10 mV/bar), PI_RM is in amperes (3.1); the
crank angle runs from 0 to 720, i.e. 4-stroke, and the file value 0 is the firing TDC of both
cylinders (3.4); the geometry is the same for High and Low; the rest value of PI_RM is a
measurement offset (8.1); measurement duration a few seconds at 30 to 150 kHz (3.2). Numbers of items that were dropped are
not reused.

Decided: user interface in English (10.2); the model is the classic Concerto user interface
according to a locally available screenshot (10.1); target platforms Windows and Linux (10.13);
distribution for now only as a Python program (12); engine data until further notice after
Jenbacher Type 6 (6.1); knock evaluation is part of the scope and active by default (7.4); crank
angle columns are always present (3.1).

---

## 16. Implementation status (v1.2)

### 16.1 Structure

```
opencurve.py            Start: user interface or --batch
opencurve/core/         Calculation core without GUI dependency
  io.py                 CSV import, format detection, column assignment
  angle.py              Angle reconstruction, cycles, crank angle grid
  thermo.py             Geometry, zero level correction, pressure and combustion values
  knock.py, pi.py       Knock and PI evaluation
  analysis.py           Overall sequence, statistics
  datamodel.py          Data sets CA/CY/TIME/STAT/PAR, formula channels
  export.py             Result tables, report
  synth.py              Test data generator with reference values
opencurve/gui/          User interface (PySide6, pyqtgraph)
opencurve/layouts/      Standard.oclayout
tests/                  test_core.py, test_gui.py, make_testdata.py
referenz/               Screenshots of the program
```

### 16.2 State of verification

- 54 automatic tests (30 calculation core, 24 user interface), all passed; the user interface tests
  run without a screen (`QT_QPA_PLATFORM=offscreen`).
- Verification was done exclusively with synthetic measurements in the reference format, because no
  real measurement file was available. The comparison with a real measurement and with Concerto
  (13.2) is still open.
- Run time: 3 million rows (150 kHz, 20 s, 270 MB) are read and evaluated in about 2.5 s; zoom in
  the raw data about 15 ms, cursor movement about 7 ms, cycle change about 80 ms.
- Executed on Linux with Python 3.13. The program has not yet been started on Windows; the code
  uses no platform specific functions.

### 16.3 Deviations from this specification

| Section | Deviation |
|---|---|
| 10.2 | No "Undo". "Print" creates a PDF instead of opening a print dialog. |
| 10.4 | Objects from the "Object Library" are inserted by double-click, not dragged. |
| 10.5 | Logarithmic scaling only for the first y axis of an axis area and for the x axis. Additional y axes are on the right and scale automatically or to fixed limits. |
| 10.6 | There is no measurement cursor in x-y diagrams (e.g. p over V). |
| 10.7 | The exclusion of a cycle from the statistics applies to both cylinders together. |
| 11 | The report uses only one y axis per diagram; pressure and PI current are therefore stacked instead of on two axes. |
| 8 | PI events are assigned to the cycle in which they start (section 15, item 12). |
