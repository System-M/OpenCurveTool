#!/usr/bin/env python3
"""Create synthetic measurement files with known reference values.

    python tests/make_testdata.py data/demo_30k.csv
    python tests/make_testdata.py data/demo_150k.csv --fs 150000 --duration 4
    python tests/make_testdata.py data/no_pressure_low.csv --missing DMR_L
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opencurve.core.synth import SynthSpec, make_file  # noqa: E402


def main(argv=None) -> int:
    d = SynthSpec()
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("output", help="CSV file to write")
    p.add_argument("--fs", type=float, default=d.fs, help="sample rate [Hz]")
    p.add_argument("--duration", type=float, default=d.duration, help="duration [s]")
    p.add_argument("--speed", type=float, default=d.speed_rpm, help="engine speed [1/min]")
    p.add_argument("--missing", nargs="*", default=[],
                   help="columns to leave out: DMR_H DMR_L PI_RM_H PI_RM_L")
    p.add_argument("--knock", type=float, default=d.knock_fraction,
                   help="share of knocking cycles (0..1)")
    p.add_argument("--semicolon", action="store_true",
                   help="semicolon delimiter with decimal comma")
    p.add_argument("--units-row", action="store_true", help="add a second header row with units")
    p.add_argument("--continuous-ca", action="store_true",
                   help="crank angle as continuous value instead of 1 deg steps")
    p.add_argument("--seed", type=int, default=d.seed)
    a = p.parse_args(argv)
    spec = SynthSpec(fs=a.fs, duration=a.duration, speed_rpm=a.speed, missing=tuple(a.missing),
                     knock_fraction=a.knock, units_row=a.units_row, stepped=not a.continuous_ca,
                     seed=a.seed, delimiter=";" if a.semicolon else ",",
                     decimal="," if a.semicolon else ".")
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    truth = make_file(out, spec)
    n = {c: t["N_CYCLES"] for c, t in truth.cyl.items()}
    print(f"written: {out} ({a.duration:g} s at {a.fs:g} Hz, complete cycles {n})")
    print(f"reference values: {out.with_name(out.stem + '_truth.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
