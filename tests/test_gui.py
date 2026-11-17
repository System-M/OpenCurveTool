"""Tests of the user interface (run without a display: QT_QPA_PLATFORM=offscreen)."""

import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("PySide6")
pytest.importorskip("pyqtgraph")

from PySide6.QtCore import QByteArray, QCoreApplication, QMimeData, QPoint, QPointF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QDropEvent, QMouseEvent  # noqa: E402

from opencurve.core import Config, analyze, read_measurement  # noqa: E402
from opencurve.core.datamodel import Formula  # noqa: E402
from opencurve.core.synth import SynthSpec, make_file  # noqa: E402
from opencurve.gui.app import create_app  # noqa: E402
from opencurve.gui.diagram import MIME_CHANNEL, DiagramObject  # noqa: E402
from opencurve.gui.dialogs import parse_formula_text, sniff_or_ask  # noqa: E402
from opencurve.gui.layouts import ensure_builtin, read_layout, standard_layout  # noqa: E402
from opencurve.gui.mainwindow import MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def app():
    a = create_app([])
    QCoreApplication.setOrganizationName("opencurve-tests")
    QSettings().clear()
    yield a
    QSettings().clear()


@pytest.fixture(scope="session")
def files(tmp_path_factory):
    d = tmp_path_factory.mktemp("meas")
    out = {"full": d / "full.csv", "nopi": d / "no_pi_low.csv", "nodmr": d / "no_dmr_high.csv",
           "second": d / "second.csv"}
    make_file(out["full"], SynthSpec(duration=1.2))
    make_file(out["nopi"], SynthSpec(duration=0.8, missing=("PI_RM_L",)))
    make_file(out["nodmr"], SynthSpec(duration=0.8, missing=("DMR_H",)))
    make_file(out["second"], SynthSpec(duration=1.0, seed=5, q_total=24000.0))
    return out


def pump(app, n=6):
    for _ in range(n):
        app.processEvents()


@pytest.fixture
def win(app, files):
    w = MainWindow()
    w.confirm = False
    w.session.sync = True
    w.resize(1400, 860)
    w.set_layout(standard_layout())
    w.show()
    w.open_measurement([str(files["full"])])
    pump(app)
    yield w
    w.modified = False
    w.close()
    w.deleteLater()
    pump(app)


def diagram_with(win, *names) -> DiagramObject:
    """First diagram of the layout that shows all the given channels."""
    for page in win.pages:
        for d in page.diagrams():
            if set(names) <= {c["name"] for c in d.curves}:
                return d
    raise AssertionError(f"no diagram with {names}")


def curve_points(d: DiagramObject, i: int):
    x, y = d._items[i]["main"].getData()
    return (np.asarray(x) if x is not None else np.zeros(0),
            np.asarray(y) if y is not None else np.zeros(0))


# ------------------------------------------------------------------ basics
def test_standard_layout_shows_measurement(win, app):
    assert [p.name for p in win.pages] == [
        "Indicating Diagram", "PI Current Diagram", "Cycle Diagram", "Heat Release Diagram",
        "Knock Diagram", "High Low Comparison", "Raw Data"]
    assert win.cycle_label.text() == f"Cycle / {win.session.n_cycles}"
    assert win.cycle_slider.maximum() == win.session.n_cycles
    assert "Open Curve Tool" in win.windowTitle() and "Cycle: 1/" in win.windowTitle()
    for i, page in enumerate(win.pages):
        win.tabs.setCurrentIndex(i)
        pump(app)
        for d in page.diagrams():
            assert d.curves
            for k in range(len(d.curves)):
                x, y = curve_points(d, k)
                assert x.size > 10 and np.isfinite(y).any(), (page.name, d.curves[k]["name"])
    assert win.cycle_spin.maximum() == win.session.n_cycles
    assert not win.modified


def test_channel_browser_lists_data_sets(win):
    b = win.browser
    assert b.files.count() == 1 and b.files.item(0).text() == "FILE1 (full.csv)"
    labels = [b.sets.item(i).text() for i in range(b.sets.count())]
    assert labels[0].startswith("CA - Crank Angle (") and labels[1].startswith("CY - Cycle Based (")
    assert labels[2].startswith("TIME - Time Based (") and labels[4] == "PAR - Parameters"
    assert {"PCYL_H", "PCYL_L", "Q_H", "PI_L", "PKNK_H", "MFB_H", "KLS_H", "KLSF_L"} <= set(
        b.channel_names())
    b.filter.setText("P*")
    assert b.channel_names() and all(n.startswith("P") for n in b.channel_names())
    b.filter.setText("")
    b.select_dataset("CY")
    assert {"PMAX_H", "IMEP_L", "AI50_H", "KP_PK_L", "KLS_PK_H", "PI_SOE_1_H"} <= set(
        b.channel_names())
    b.select_dataset("PAR")
    assert {"BORE", "STROKE", "CONROD", "EPSILON", "POLY_COEFF", "NRSTROKE"} <= set(b.channel_names())
    b.select_dataset("STAT")
    assert "KNOCK_FREQ_H" in b.channel_names()


def test_drag_and_drop_channel_into_diagram(win, app):
    win.insert_object({"type": "diagram"})
    d = win.current_page().frames[-1].obj
    pump(app)
    assert d.curves == []
    b = win.browser
    b.select_dataset("CA")
    item = next(b.tree.topLevelItem(i) for i in range(b.tree.topLevelItemCount())
                if b.tree.topLevelItem(i).text(0) == "PCYL_H")
    mime = b.tree.mimeData([item])
    assert mime.hasFormat(MIME_CHANNEL)
    pos = QPointF(d.view.width() / 2, d.view.height() / 2)
    d.view.dropEvent(QDropEvent(pos, Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier))
    pump(app)
    assert [c["name"] for c in d.curves] == ["PCYL_H"]
    assert curve_points(d, 0)[0].size > 100
    assert win.modified

    # drop at the right border -> new y axis; at the bottom -> new strip
    def drop(name, fx, fy, ds="CA"):
        md = QMimeData()
        md.setData(MIME_CHANNEL, QByteArray(json.dumps([{"ds": ds, "name": name}]).encode()))
        p = QPointF(d.view.width() * fx, d.view.height() * fy)
        d.view.dropEvent(QDropEvent(p, Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier))
        pump(app)

    drop("Q_H", 0.95, 0.5)
    assert d.curves[1]["axis"] == 1 and d.curves[1]["strip"] == 0
    drop("PI_H", 0.5, 0.97)
    assert d.curves[2]["strip"] == 1 and len(d.strips) == 2
    drop("PMAX_H", 0.5, 0.5, ds="CY")           # other data set: rejected
    assert len(d.curves) == 3


def test_cursor_values_match_data(win, app):
    d = diagram_with(win, "PCYL_H", "PI_H")
    win.tabs.setCurrentIndex(1)
    pump(app)
    cr = win.session.entry.result.cyl["H"]
    win.session.set_cycle(4)
    pump(app)
    d.set_cursor(0, 12.3)                        # snaps to the 0.5 deg grid
    assert d.cursor_x[0] == pytest.approx(12.5)
    j = int(np.argmin(np.abs(cr.grid - 12.5)))
    vals = {name: v for name, _u, v, _v2 in d.cursor_values()}
    assert vals["PCYL_H"] == pytest.approx(cr.curves["PCYL"][4, j], rel=1e-12)
    assert vals["PI_H"] == pytest.approx(cr.curves["PI"][4, j], abs=1e-12)
    d.step_cursor(0, 1)
    assert d.cursor_x[0] == pytest.approx(13.0)
    # time based diagram: value at a sample
    raw = win.pages[-1].diagrams()[0]
    t = win.session.entry.meas.time
    raw.set_cursor(0, t[12345] + 1e-7)
    assert raw.cursor_x[0] == t[12345]
    vals = {name: v for name, _u, v, _v2 in raw.cursor_values()}
    assert vals["PI_RM_H"] == pytest.approx(win.session.entry.meas.raw["PI_RM_H"][12345])


def test_cursors_are_linked_on_a_page(win, app):
    win.tabs.setCurrentIndex(1)
    pump(app)
    page = win.current_page()
    a = page.diagrams()[0]
    win.insert_object({"type": "diagram", "curves": [
        {"ds": "CA", "name": "PCYL_L", "file": "active", "strip": 0, "axis": 0, "color": None,
         "width": 1.5, "style": "solid", "mode": "global", "visible": True, "xname": None}]})
    b = page.frames[-1].obj
    pump(app)
    a.set_cursor(0, 20.0)
    assert b.cursor_x[0] == pytest.approx(20.0)
    win.a_link.setChecked(False)
    win.set_cursor_link(False)
    a.set_cursor(0, 30.0)
    assert b.cursor_x[0] == pytest.approx(20.0)


def test_cycle_change_updates_all_diagrams_and_title(win, app):
    d = win.pages[0].diagrams()[0]
    cr = win.session.entry.result.cyl["H"]
    win.cycle_spin.setValue(3)
    pump(app)
    assert win.session.cycle == 2 and "Cycle: 3/" in win.windowTitle()
    assert np.allclose(curve_points(d, 0)[1], cr.curves["PCYL"][2])
    win.session.set_cycle(7)
    pump(app)
    assert win.cycle_spin.value() == 8
    assert np.allclose(curve_points(d, 0)[1], cr.curves["PCYL"][7])
    for mode, n in (("mean", cr.grid.size), ("all", cr.used().sum() * (cr.grid.size + 1))):
        win.session.set_mode(mode)
        pump(app)
        assert curve_points(d, 0)[0].size == n
    assert np.allclose(curve_points(d, 0)[1][:cr.grid.size], cr.curves["PCYL"][0])
    win.session.set_mode("single")


def test_result_table_click_selects_cycle(win, app):
    win.tabs.setCurrentIndex(1)
    pump(app)
    table = next(f.obj for f in win.current_page().frames if f.obj.type_name == "table")
    assert table.model.rowCount() == win.session.n_cycles
    table._clicked(table.proxy.index(5, 0))
    assert win.session.cycle == 5


# ------------------------------------------------------------------ layout
def test_layout_save_and_load_roundtrip(win, app, tmp_path):
    d = win.pages[0].diagrams()[0]
    d.add_curve("CA", "Q_H", strip=0, axis=1)
    win.add_page("Extra")
    win.insert_object({"type": "text", "text": "IMEP {STAT:IMEP_H}"})
    win.set_formulas([Formula("DIMEP", "IMEP_H - IMEP_L", "bar")])
    before = win.layout_dict()
    f = tmp_path / "mine.oclayout"
    assert win.save_layout(str(f))
    assert not win.modified and "mine.oclayout" in win.windowTitle()
    win.new_layout()
    assert len(win.pages) == 1
    assert win.open_layout(str(f))
    pump(app)
    after = win.layout_dict()
    assert after["pages"] == before["pages"]
    assert after["formulas"] == before["formulas"]
    assert read_layout(f)["pages"] == before["pages"]
    assert win.session.entry.get("CY", "DIMEP") is not None


def test_builtin_standard_layout_file_matches_code():
    path = ensure_builtin()
    assert path.is_file()
    assert read_layout(path)["pages"] == standard_layout()["pages"]


def test_layout_on_measurement_with_missing_signals(win, app, files):
    win.open_measurement([str(files["nopi"])])
    pump(app)
    assert win.session.entry.name == "no_pi_low.csv"
    d = diagram_with(win, "PCYL_H", "PI_H")
    names = [c["name"] for c in d.curves]
    assert curve_points(d, names.index("PI_H"))[0].size > 0
    assert curve_points(d, names.index("PI_L"))[0].size == 0        # empty, no error
    assert "color:#b0b0b0" in d.legend.toHtml() or "#b0b0b0" in d.legend.toHtml()
    win.open_measurement([str(files["nodmr"])])
    pump(app)
    for i in range(len(win.pages)):
        win.tabs.setCurrentIndex(i)
        pump(app)
    d = diagram_with(win, "PCYL_H", "PI_H")
    assert curve_points(d, names.index("PCYL_H"))[0].size == 0
    assert curve_points(d, names.index("PCYL_L"))[0].size > 0
    assert "PCYL_H" not in win.browser.channel_names()


def test_design_mode_moves_and_resizes_objects(win, app):
    page = win.pages[0]
    win.set_design(True)
    pump(app)
    frame = page.frames[-1]
    ov = frame.overlay
    assert ov.isVisible()
    rect0 = list(frame.rect)
    g0 = ov.mapToGlobal(QPoint(ov.width() // 2, ov.height() // 2))
    pw, ph = page.usable()

    def mouse(kind, local, glob, buttons=Qt.LeftButton):
        return QMouseEvent(kind, QPointF(local), QPointF(glob), Qt.LeftButton, buttons,
                           Qt.NoModifier)

    c = QPoint(ov.width() // 2, ov.height() // 2)
    ov.mousePressEvent(mouse(QMouseEvent.MouseButtonPress, c, g0))
    assert page.selected is frame
    g1 = g0 + QPoint(0, int(0.1 * ph))
    ov.mouseMoveEvent(mouse(QMouseEvent.MouseMove, c, g1))
    ov.mouseReleaseEvent(mouse(QMouseEvent.MouseButtonRelease, c, g1, Qt.NoButton))
    assert frame.rect[1] == pytest.approx(rect0[1] + 0.1, abs=0.011)
    assert frame.rect[2:] == pytest.approx(rect0[2:])
    assert win.modified
    n = len(page.frames)
    win.duplicate_object()
    assert len(page.frames) == n + 1
    win.delete_object()
    assert len(page.frames) == n
    win.set_design(False)
    assert not page.frames[0].overlay.isVisible()


# ---------------------------------------------------------------- settings
def test_settings_change_triggers_recalculation(win, app):
    s = win.session
    pmax = s.entry.result.cyl["H"].stats["PMAX"]["mean"]
    d = win.pages[0].diagrams()[0]
    y0 = curve_points(d, 0)[1].copy()
    cfg = s.cfg.copy()
    cfg.cyl["H"].dmr_sens_mv_per_bar = 20.0
    s.set_config(cfg)
    pump(app)
    assert s.entry.result.cyl["H"].stats["PMAX"]["mean"] == pytest.approx(pmax / 2, rel=1e-6)
    assert np.allclose(curve_points(d, 0)[1], y0 / 2, rtol=1e-6)
    assert "Recalculated" in win.messages.toPlainText()


def test_gui_and_batch_give_identical_results(win, files):
    ref = analyze(read_measurement(files["full"]), Config())
    for c in ("H", "L"):
        a, b = win.session.entry.result.cyl[c], ref.cyl[c]
        assert set(a.scalars) == set(b.scalars)
        for name in a.scalars:
            assert np.array_equal(a.scalars[name], b.scalars[name], equal_nan=True), name


def test_exclude_cycle_from_statistics(win, app):
    s = win.session
    n = s.entry.result.cyl["H"].stats["PMAX"]["n"]
    s.set_cycle(3)
    assert s.toggle_exclude() is True
    assert s.entry.result.cyl["H"].stats["PMAX"]["n"] == n - 1
    assert s.entry.get("CY", "VALID_H").y[3] == 0
    assert s.toggle_exclude() is False
    assert s.entry.result.cyl["H"].stats["PMAX"]["n"] == n


# ---------------------------------------------------------------- formulas
def test_formula_channels(win, app):
    fs = parse_formula_text("DIMEP [bar] = IMEP_H - IMEP_L  # difference\n"
                            "PPA = PCYL_H * 1e5\nPNORM = P_H / max(P_H)")
    assert [f.name for f in fs] == ["DIMEP", "PPA", "PNORM"] and fs[0].unit == "bar"
    win.set_formulas(fs)
    e = win.session.entry
    h, l = e.result.cyl["H"].scalars["IMEP"], e.result.cyl["L"].scalars["IMEP"]
    n = min(h.size, l.size)
    assert np.allclose(e.get("CY", "DIMEP").y, h[:n] - l[:n])
    assert np.allclose(e.get("CA", "PPA").y, e.result.cyl["H"].curves["PCYL"] * 1e5)
    assert float(np.nanmax(e.get("TIME", "PNORM").y)) == pytest.approx(1.0)
    win.browser.select_dataset("CY")
    assert "DIMEP" in win.browser.channel_names()
    for bad, msg in (("X = IMEP_H + PCYL_H", "same data set"), ("X = NOPE_H * 2", "unknown channel"),
                     ("X = __import__('os')", "unknown")):
        with pytest.raises(Exception, match=msg):
            e.check_formula(parse_formula_text(bad)[0])
    with pytest.raises(Exception, match="already the name"):
        e.check_formula(Formula("PMAX_H", "IMEP_H * 2"))


# ------------------------------------------------------- export, workspace
def test_exports(win, app, tmp_path):
    assert win.export_png(str(tmp_path / "page.png"))
    assert (tmp_path / "page.png").stat().st_size > 5000
    assert win.export_pdf(str(tmp_path / "layout.pdf"), all_pages=True)
    assert (tmp_path / "layout.pdf").read_bytes()[:4] == b"%PDF"
    win.tabs.setCurrentIndex(0)
    pump(app)
    assert win.export_curve_data(str(tmp_path / "data.csv"))
    rows = (tmp_path / "data.csv").read_text().splitlines()
    assert rows[0].split(";")[1] == "PCYL_H" and len(rows) > 100
    assert win.export_results(str(tmp_path / "res"))
    assert (tmp_path / "res" / "full_report.html").is_file()
    assert (tmp_path / "res" / "full_cycles.csv").is_file()


def test_workspace_roundtrip(win, app, files, tmp_path):
    s = win.session
    win.open_measurement([str(files["second"])])
    pump(app)
    assert len(s.files) == 2 and s.active == 1
    cfg = s.cfg.copy()
    cfg.engine.compression = 10.5
    s.set_config(cfg)
    s.set_active(0)
    s.set_cycle(6)
    wf = tmp_path / "work.ocwork"
    assert win.save_workspace(str(wf))
    layout = win.layout_dict()
    win.new_layout()
    s.remove_file(1)
    assert win.open_workspace(str(wf))
    pump(app)
    assert [e.name for e in s.files] == ["full.csv", "second.csv"]
    assert s.active == 0 and s.cycle == 6
    assert s.cfg.engine.compression == 10.5
    assert win.layout_dict()["pages"] == layout["pages"]
    assert "<work>" in win.windowTitle()


def test_second_file_as_comparison_curve(win, app, files):
    win.open_measurement([str(files["second"])])
    pump(app)
    win.session.set_active(0)
    d = win.pages[0].diagrams()[0]
    d.add_curve("CA", "PCYL_H", strip=0, file=2)
    pump(app)
    y1, y2 = curve_points(d, 0)[1], curve_points(d, len(d.curves) - 1)[1]
    assert y1.size == y2.size and float(np.max(y1)) > float(np.max(y2)) + 5.0


def test_reference_format_needs_no_import_dialog(app, files):
    fmt = sniff_or_ask(str(files["full"]))          # would block if a dialog appeared
    assert fmt is not None and fmt.delimiter == "," and set(fmt.mapping) == {
        "TIME", "CA_H", "CA_L", "DMR_H", "DMR_L", "PI_RM_H", "PI_RM_L", "KLS_H", "KLS_L"}


def test_zoom_back_and_show_all(win, app):
    d = win.pages[0].diagrams()[0]
    vb = d.strips[0].plot.vb
    pump(app, 10)
    x0 = vb.viewRange()[0]
    d._remember_state()
    vb.setXRange(0, 30, padding=0)
    vb.sigRangeChangedManually.emit([True, True])
    pump(app, 10)
    assert vb.viewRange()[0] == pytest.approx([0, 30])
    d.zoom_back()
    pump(app, 10)
    assert vb.viewRange()[0] == pytest.approx(x0, abs=1e-6)
    vb.setXRange(0, 30, padding=0)
    d.show_all()
    pump(app, 10)
    assert vb.viewRange()[0] == pytest.approx([-100, 130])


# ------------------------------------------------------ background, dialogs
def test_background_loading_of_several_files(app, files):
    w = MainWindow()
    w.confirm = False
    w.set_layout(standard_layout())
    w.show()
    assert w.session.sync is False
    w.open_measurement([str(files["full"]), str(files["second"])])
    assert w.session.busy
    assert w.session.wait(60000)
    pump(app)
    assert [e.name for e in w.session.files] == ["full.csv", "second.csv"]
    assert not w.session.busy and not w.progress.isVisible()
    cfg = w.session.cfg.copy()
    cfg.engine.kappa = 1.30
    w.session.set_config(cfg)
    assert w.session.wait(60000)
    assert all(e.result.cfg.engine.kappa == 1.30 for e in w.session.files)
    w.session.load_files([str(files["full"].with_name("does_not_exist.csv"))])
    assert w.session.wait(60000)
    assert "Error: file not found" in w.messages.toPlainText()
    assert not w.dock_msg.isHidden()                 # errors bring the message window up
    w.modified = False
    w.close()
    w.deleteLater()
    pump(app)


def test_dialogs(win, app, files, tmp_path):
    from opencurve.core.config import load_config
    from opencurve.gui.dialogs import (SETTINGS_TABS, CalculatorDialog, ChecklistDialog,
                                       DataExplorer, DiagramDialog, FormulaEditor, ImportDialog,
                                       ObjectWizard, SettingsDialog, TextDialog)
    s = win.session
    for tab in SETTINGS_TABS:
        dlg = SettingsDialog(s, tab, win)
        assert dlg.tabs.tabText(dlg.tabs.currentIndex()) == tab
        assert "bar" in dlg.preview.text()
        cfg = dlg.collect()
        assert cfg.to_dict() == s.cfg.to_dict()          # untouched dialog changes nothing
        dlg.deleteLater()
    dlg = SettingsDialog(s, "Channel Scaling", win)
    got = []
    dlg.applied.connect(got.append)
    fld = next(f for f in dlg.fields if f.obj is dlg.cfg.cyl["L"]
               and f.f.name == "dmr_sens_mv_per_bar")
    fld.w.setText("12,5")                                 # decimal comma is accepted
    assert dlg.apply() and got[0].cyl["L"].dmr_sens_mv_per_bar == 12.5
    fld.w.setText("abc")
    win.confirm = False

    d = diagram_with(win, "PCYL_H", "PI_H")
    dd = DiagramDialog(d, s, win)
    assert dd.table.rowCount() == len(d.curves) and dd.axes_table.rowCount() == 2
    dd.title.setText("My diagram")
    dd.x_min.setText("-60")
    dd.x_max.setText("60")
    dd.rows[0][1][4].setValue(3.0)                        # width of the first curve
    dd._ok()
    pump(app)
    assert d.spec["title"] == "My diagram" and d.curves[0]["width"] == 3.0
    assert d.strips[0].plot.vb.viewRange()[0] == pytest.approx([-60, 60])

    ex = DataExplorer(s, win)
    assert ex.table.rowCount() == 1 and "full.csv" in ex.info.toPlainText()
    calc = CalculatorDialog(s, parent=win)
    calc.name.setText("DPMAX")
    calc.expr.setText("PMAX_H - PMAX_L")
    calc._ok()
    assert calc.formula is not None and calc.formula.name == "DPMAX"
    calc2 = CalculatorDialog(s, parent=win)
    calc2.name.setText("BAD")
    calc2.expr.setText("PMAX_H +")
    calc2._ok()
    assert calc2.formula is None and "syntax" in calc2.status.text()
    fe = FormulaEditor(s, win)
    fe.edit.setPlainText("A = IMEP_H * 2\nB [J] = I_H + I_L")
    assert [f.name for f in fe.check()] == ["A", "B"]
    fe.edit.setPlainText("A = IMEP_H * 2\nA = 3")
    assert fe.check() is None and "more than once" in fe.status.text()
    assert ObjectWizard(win).object_type() == "diagram"
    td = TextDialog({"text": "x", "size": 14}, win)
    assert td.result_spec()["size"] == 14
    cd = ChecklistDialog("t", ["a", "b", "c"], ["b"], parent=win)
    assert cd.selected() == ["b"]

    semi = tmp_path / "semi.csv"
    make_file(semi, SynthSpec(duration=0.2, delimiter=";", decimal=","))
    from opencurve.core import sniff_csv
    imp = ImportDialog(str(semi), sniff_csv(semi), parent=win)
    assert imp.delim.currentData() == ";" and imp.dec.currentText() == ","
    assert imp.combos["CA_H"].currentData() == "CA High"
    imp._ok()
    assert imp.fmt.mapping["DMR_L"] == "DMR_L"
    win.save_settings(str(tmp_path / "s.toml"))
    assert load_config(tmp_path / "s.toml").to_dict() == s.cfg.to_dict()


def test_cycle_navigation_and_mode_combo(win, app):
    s = win.session
    win.a_last.trigger()
    assert s.cycle == s.n_cycles - 1 and win.cycle_spin.value() == s.n_cycles
    assert win.cycle_slider.value() == s.n_cycles
    win.a_prev_cycle.trigger()
    assert s.cycle == s.n_cycles - 2
    win.a_first.trigger()
    win.a_prev_cycle.trigger()                       # stays at the first cycle
    assert s.cycle == 0
    win.a_next_cycle.trigger()
    assert s.cycle == 1 and "Cycle: 2/" in win.windowTitle()
    win.cycle_slider.setValue(5)
    assert s.cycle == 4
    labels = [win.mode_combo.itemText(i) for i in range(win.mode_combo.count())]
    assert labels == ["Single", "Range", "All", "Mean", "Envelope"]
    win.mode_combo.activated.emit(labels.index("Mean"))
    assert s.mode == "mean" and win.a_modes["mean"].isChecked()
    s.set_mode("single")
    assert win.mode_combo.currentText() == "Single"
    wf = win.workflow
    assert [wf.tabs.tabText(i) for i in range(wf.tabs.count())] == ["WorkFlow", "Layouts"]
    assert any(wf.layouts.item(i).text() == "Standard" for i in range(wf.layouts.count()))
    assert list(wf.buttons) == ["Data Access", "Data Display", "Calculations", "Export",
                                "Utilities"]


def test_axis_titles_only_where_they_belong(win, app):
    """Several strips share one x axis title at the bottom; units come without hidden factors."""
    win.tabs.setCurrentIndex(4)                      # Knock Diagram: pressure, PKNK, KLS
    pump(app, 10)
    d = diagram_with(win, "PCYL_H", "PKNK_H", "KLS_H")
    assert len(d.strips) == 3
    titles = [s.plot.getAxis("bottom").labelText for s in d.strips]
    assert titles == ["", "", "Crank Angle [deg]"]
    left = [s.plot.getAxis("left") for s in d.strips]
    assert [a.labelText for a in left] == ["PCYL_H, PCYL_L [bar]", "PKNK_H, PKNK_L [bar]",
                                           "KLS_H, KLS_L [V]"]
    assert all(not a.autoSIPrefix for a in left)
