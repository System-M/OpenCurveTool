"""Command line entry: GUI by default, evaluation without GUI with --batch."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import APP_NAME, __version__

EXIT_OK, EXIT_INPUT, EXIT_NO_CYCLES = 0, 1, 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="opencurve.py",
        description=f"{APP_NAME} {__version__} - combustion indicating analysis "
                    "(cylinder pressure and PI current, cylinders High / Low)")
    p.add_argument("files", nargs="*", help="measurement CSV file(s)")
    p.add_argument("--batch", action="store_true", help="evaluate without GUI and write results")
    p.add_argument("--config", help="settings file (TOML)")
    p.add_argument("--out", help="output directory (default: next to the measurement)")
    p.add_argument("--layout", help="layout file (.oclayout) to open in the GUI")
    p.add_argument("--cyl", default="H,L", help="cylinders to evaluate, e.g. H or H,L")
    p.add_argument("--cycles", help='cycle selection, e.g. "10:200" (default: all)')
    p.add_argument("--no-plots", action="store_true", help="batch: no report and diagrams")
    p.add_argument("--save-cycles", action="store_true",
                   help="batch: store all single cycles (.npz)")
    p.add_argument("-v", "--verbose", action="store_true", help="print all messages")
    p.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return p


def run_batch(args) -> int:
    from .core import CsvFormatError, analyze, load_config, read_measurement
    from .core.export import summary_text, write_outputs

    if not args.files:
        print("error: --batch needs at least one measurement file", file=sys.stderr)
        return EXIT_INPUT
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read settings '{args.config}': {exc}", file=sys.stderr)
        return EXIT_INPUT
    if args.cycles:
        cfg.output.cycles = args.cycles
    wanted = {c.strip().upper()[:1] for c in args.cyl.split(",") if c.strip()}
    if not wanted or not wanted <= {"H", "L"}:
        print("error: --cyl expects H, L or H,L", file=sys.stderr)
        return EXIT_INPUT

    status = EXIT_OK
    for f in args.files:
        path = Path(f)
        try:
            meas = read_measurement(path, max_gap_samples=cfg.ca.max_gap_samples)
        except CsvFormatError as exc:
            print(f"error: {path}: {exc}", file=sys.stderr)
            status = max(status, EXIT_INPUT)
            continue
        for c in ("H", "L"):
            if c not in wanted:
                for sig in ("DMR", "PI_RM", "KLS"):
                    meas.raw.pop(f"{sig}_{c}", None)
        if not meas.cylinders():
            print(f"error: {path}: no signal for the selected cylinder(s)", file=sys.stderr)
            status = max(status, EXIT_INPUT)
            continue
        res = analyze(meas, cfg)
        outdir = Path(args.out) if args.out else path.parent / f"{path.stem}_results"
        written = write_outputs(res, outdir, plots=not args.no_plots,
                                save_cycles=args.save_cycles)
        print(summary_text(res))
        if args.verbose:
            for w in res.warnings:
                print(f"  - {w}")
        print(f"  results: {outdir} ({len(written)} files)")
        if not any(cr.used().any() for cr in res.cyl.values()):
            print(f"error: {path}: no evaluable cycle", file=sys.stderr)
            status = max(status, EXIT_NO_CYCLES)
    return status


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.batch:
        return run_batch(args)
    try:
        from .gui.app import run_gui
    except ImportError as exc:
        print(f"error: the graphical interface needs PySide6 and pyqtgraph ({exc}).\n"
              "Install them with:  pip install -r requirements.txt\n"
              "or use --batch for an evaluation without GUI.", file=sys.stderr)
        return EXIT_INPUT
    return run_gui(args)
