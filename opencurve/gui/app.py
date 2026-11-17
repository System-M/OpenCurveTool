"""GUI start-up."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .. import APP_NAME, APP_SHORT, __version__
from ..core import load_config
from .panels import make_icon

STYLE = """
QMainWindow::separator { width: 4px; height: 4px; background: #c9d2e0; }
QDockWidget::title { background: #dfe6f1; padding: 4px; border: 1px solid #b7c2d6; }
QToolBar { border: none; spacing: 3px; padding: 2px; }
QTabBar::tab { padding: 4px 10px; }
QStatusBar::item { border: none; }
"""


def create_app(argv=None) -> QApplication:
    app = QApplication.instance()
    if app is None:
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts, False)
        app = QApplication(argv if argv is not None else sys.argv[:1])
    app.setApplicationName(APP_SHORT)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_SHORT)
    # numbers are always written with a decimal point, independent of the system locale
    QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    app.setWindowIcon(make_icon("display", 64))
    return app


def run_gui(args) -> int:
    from .mainwindow import MainWindow

    app = create_app()
    win = MainWindow()
    if getattr(args, "config", None):
        try:
            win.session.cfg = load_config(args.config)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read settings '{args.config}': {exc}", file=sys.stderr)
            return 1
    win.restore_window()
    if getattr(args, "layout", None):
        win.confirm, keep = False, win.confirm
        ok = win.open_layout(args.layout)
        win.confirm = keep
        if not ok:
            win.load_default_layout()
    else:
        win.load_default_layout()
    win.show()
    files = [f for f in getattr(args, "files", []) if Path(f).is_file()]
    for f in getattr(args, "files", []):
        if f not in files:
            win.session.log(f"Error: file not found: {f}")
    if files:
        win.open_measurement(files)
    return app.exec()
