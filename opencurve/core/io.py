"""CSV import (specification section 3).

The time column is parsed with full float64 precision (round-trip parser) and is
never rounded afterwards.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

CANONICAL = ("TIME", "CA_H", "CA_L", "DMR_H", "DMR_L", "PI_RM_H", "PI_RM_L", "KLS_H", "KLS_L")
SIGNALS = ("DMR", "PI_RM", "KLS")            # per cylinder: DMR_H, DMR_L, ...
_KEYS = {
    "time": "TIME", "t": "TIME", "zeit": "TIME",
    "cah": "CA_H", "cal": "CA_L",
    "dmrh": "DMR_H", "dmrl": "DMR_L",
    "pirmh": "PI_RM_H", "pirml": "PI_RM_L",
    "klsh": "KLS_H", "klsl": "KLS_L",
}
_NUM_RE = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)([eE][+-]?\d+)?$")


class CsvFormatError(ValueError):
    """The file cannot be interpreted as a measurement."""


@dataclass
class CsvFormat:
    encoding: str = "utf-8"
    delimiter: str = ","
    decimal: str = "."
    units_row: bool = False
    columns: list = field(default_factory=list)          # raw header names
    mapping: dict = field(default_factory=dict)          # canonical -> raw header name
    ignored: list = field(default_factory=list)          # raw names without mapping
    preview: list = field(default_factory=list)          # first raw text lines

    def describe(self) -> str:
        d = {",": "comma", ";": "semicolon", "\t": "tab"}.get(self.delimiter, self.delimiter)
        enc = "utf-8" if self.encoding.startswith("utf-8") else self.encoding
        return (f"delimiter={d}, decimal='{self.decimal}', encoding={enc}, "
                f"units row={'yes' if self.units_row else 'no'}")


@dataclass
class Measurement:
    path: Path
    time: np.ndarray
    raw: dict                       # canonical name -> float64 array (without TIME)
    fmt: CsvFormat
    fs: float                       # sample rate [Hz], derived from the time column
    jitter: float                   # max. relative deviation of dt from its median
    warnings: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def n(self) -> int:
        return int(self.time.size)

    @property
    def duration(self) -> float:
        return float(self.time[-1] - self.time[0]) if self.n > 1 else 0.0

    def has(self, name: str) -> bool:
        return name in self.raw

    def cylinders(self) -> list:
        """Cylinders that carry at least one signal."""
        return [c for c in ("H", "L") if any(self.has(f"{s}_{c}") for s in SIGNALS)]


# ------------------------------------------------------------------ helpers
def normalize_name(name: str) -> str:
    s = re.sub(r"[\[\(].*?[\]\)]", "", str(name)).strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    s = re.sub(r"_high$", "_h", s)
    s = re.sub(r"_low$", "_l", s)
    return s


def canonical_name(name: str) -> str | None:
    return _KEYS.get(normalize_name(name).replace("_", ""))


def _read_head(path: Path, nlines: int = 60) -> tuple[list, str]:
    with path.open("rb") as fh:
        raw = fh.read(200_000)
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    lines = [ln for ln in text.splitlines() if ln.strip()][:nlines]
    return lines, enc


def sniff_csv(path: str | Path) -> CsvFormat:
    """Detect delimiter, decimal sign, units row and the column mapping."""
    path = Path(path)
    if not path.is_file():
        raise CsvFormatError(f"file not found: {path}")
    lines, enc = _read_head(path)
    if len(lines) < 2:
        raise CsvFormatError("file contains no data rows")
    header = lines[0]
    counts = {d: header.count(d) for d in (";", "\t", ",")}
    delim = max(counts, key=counts.get)
    if counts[delim] == 0:
        raise CsvFormatError("no column delimiter found in the header line (expected ; , or tab)")
    columns = [c.strip().strip('"') for c in header.split(delim)]
    ncol = len(columns)

    second = [c.strip() for c in lines[1].split(delim)]
    units_row = not any(_NUM_RE.match(c) for c in second if c)
    data_lines = lines[2:] if units_row else lines[1:]
    if not data_lines:
        raise CsvFormatError("file contains no data rows")

    decimal = "."
    if delim == ",":
        nfields = [ln.count(",") + 1 for ln in data_lines]
        if any(n != ncol for n in nfields):
            if all(n > ncol for n in nfields):
                raise CsvFormatError(
                    "comma is used both as column delimiter and as decimal sign; "
                    "the file cannot be read unambiguously")
            raise CsvFormatError(
                f"data rows have a different number of columns than the header ({ncol})")
    else:
        for ln in data_lines:
            if any(re.match(r"^[+-]?\d+,\d", c.strip()) for c in ln.split(delim)):
                decimal = ","
                break

    fmt = CsvFormat(encoding=enc, delimiter=delim, decimal=decimal, units_row=units_row,
                    columns=columns, preview=lines[:8])
    for col in columns:
        canon = canonical_name(col)
        if canon and canon not in fmt.mapping:
            fmt.mapping[canon] = col
        else:
            fmt.ignored.append(col)
    return fmt


def _fill_small_gaps(x: np.ndarray, max_gap: int) -> tuple[np.ndarray, int, int]:
    """Linear interpolation of NaN runs <= max_gap. Returns (x, filled, remaining)."""
    nan = np.isnan(x)
    if not nan.any():
        return x, 0, 0
    idx = np.flatnonzero(nan)
    # split into runs
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.concatenate(([idx[0]], idx[breaks + 1]))
    ends = np.concatenate((idx[breaks], [idx[-1]]))
    x = x.copy()
    filled = 0
    for a, b in zip(starts, ends):
        ln = b - a + 1
        if ln <= max_gap and a > 0 and b < x.size - 1:
            x[a:b + 1] = np.interp(np.arange(a, b + 1), [a - 1, b + 1], [x[a - 1], x[b + 1]])
            filled += ln
    return x, filled, int(np.isnan(x).sum())


# --------------------------------------------------------------------- read
def read_measurement(path: str | Path, fmt: CsvFormat | None = None,
                     max_gap_samples: int = 3) -> Measurement:
    path = Path(path)
    if fmt is None:
        fmt = sniff_csv(path)
    if "TIME" not in fmt.mapping:
        raise CsvFormatError("required column 'Time' not found")

    usecols = list(fmt.mapping.values())
    # exact (round-trip) float parsing: explicit option before pandas 3, the default since
    extra = {} if int(pd.__version__.split(".")[0]) >= 3 else {"float_precision": "round_trip"}
    try:
        df = pd.read_csv(
            path, sep=fmt.delimiter, decimal=fmt.decimal, header=0,
            skiprows=[1] if fmt.units_row else None, encoding=fmt.encoding,
            usecols=usecols, dtype=np.float64, skipinitialspace=True, engine="c", **extra)
    except (ValueError, pd.errors.ParserError) as exc:
        raise CsvFormatError(f"cannot parse numeric data: {exc}") from exc
    if len(df) < 2:
        raise CsvFormatError("file contains fewer than two data rows")

    first_data_line = 3 if fmt.units_row else 2
    warnings: list[str] = []
    t = df[fmt.mapping["TIME"]].to_numpy(dtype=np.float64, copy=True)
    if np.isnan(t).any():
        row = int(np.flatnonzero(np.isnan(t))[0])
        raise CsvFormatError(f"time column has an empty value in line {row + first_data_line}")
    dt = np.diff(t)
    if (dt <= 0).any():
        row = int(np.flatnonzero(dt <= 0)[0]) + 1
        raise CsvFormatError(
            f"time is not strictly increasing in line {row + first_data_line} "
            f"({t[row - 1]!r} -> {t[row]!r})")
    dt_med = float(np.median(dt))
    fs = 1.0 / dt_med
    jitter = float(np.max(np.abs(dt - dt_med)) / dt_med)
    if jitter > 0.01:
        warnings.append(f"non-uniform sampling: dt deviates up to {jitter * 100:.1f} % from its median")

    raw: dict = {}
    for canon, col in fmt.mapping.items():
        if canon == "TIME":
            continue
        x = df[col].to_numpy(dtype=np.float64, copy=True)
        if np.isnan(x).all():
            warnings.append(f"column '{col}' is empty and treated as missing")
            continue
        x, filled, remaining = _fill_small_gaps(x, max_gap_samples)
        if filled:
            warnings.append(f"column '{col}': {filled} missing value(s) interpolated")
        if remaining:
            warnings.append(f"column '{col}': {remaining} missing value(s) remain; "
                            "affected cycles are marked invalid")
        raw[canon] = x
    del df

    for col in fmt.ignored:
        warnings.append(f"unknown column '{col}' ignored")

    meas = Measurement(path=path, time=t, raw=raw, fmt=fmt, fs=fs, jitter=jitter,
                       warnings=warnings)
    if not meas.cylinders():
        raise CsvFormatError("no signal column found (need at least one of "
                             "DMR_H, DMR_L, PI_RM_H, PI_RM_L, KLS_H, KLS_L)")
    for c in meas.cylinders():
        if not meas.has(f"CA_{c}"):
            name = "CA High" if c == "H" else "CA Low"
            raise CsvFormatError(f"crank angle column '{name}' is missing although a signal "
                                 f"of cylinder {'High' if c == 'H' else 'Low'} is present")
    return meas
