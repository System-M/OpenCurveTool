#!/usr/bin/env python3
"""Open Curve Tool - start script.

    python opencurve.py                       start the GUI
    python opencurve.py measurement.csv       start the GUI with a measurement
    python opencurve.py --batch measurement.csv [--config engine.toml] [--out results/]
"""

import sys

from opencurve.cli import main

if __name__ == "__main__":
    sys.exit(main())
