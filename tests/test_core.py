"""Tests of the calculation core against synthetic data with known reference values."""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from opencurve.cli import main as cli_main  # noqa: E402
from opencurve.core import (Config, CsvFormatError, analyze, load_config,  # noqa: E402
                            read_measurement, save_config, sniff_csv)
from opencurve.core.config import parse_cycle_selection  # noqa: E402
from opencurve.core.io import CsvFormat, Measurement, canonical_name  # noqa: E402
from opencurve.core.synth import SynthSpec, generate, make_file, write_csv  # noqa: E402

RENAME = {"CA High": "CA_H", "CA Low": "CA_L"}


def measure(spec: SynthSpec):
    """Synthetic measurement in memory (no file round trip)."""
    df, truth = generate(spec)
    raw = {RENAME.get(c, c): df[c].to_numpy(dtype=float) for c in df.columns if c != "Time"}
    m = Measurement(Path("synthetic.csv"), df["Time"].to_numpy(), raw, CsvFormat(), spec.fs, 0.0)
    return m, truth


def max_err(calc, ref, relative=False):
    d = np.asarray(calc) - np.asarray(ref)
    if relative:
        d = d / np.asarray(ref) * 100.0
    return float(np.nanmax(np.abs(d)))


@pytest.fixture(scope="module")
def ref30():
    m, truth = measure(SynthSpec())
    return analyze(m, Config()), truth


# ---------------------------------------------------------------- accuracy
@pytest.mark.parametrize("fs", [30000.0, 50000.0, 100000.0, 150000.0])
def test_accuracy_against_reference(fs):
    m, truth = measure(SynthSpec(fs=fs))
    res = analyze(m, Config())
    dt = 1.0 / fs
    for c in ("H", "L"):
        cr, tr = res.cyl[c], truth.cyl[c]
        assert cr.n_cycles == tr["N_CYCLES"]
        assert cr.used().all()
        assert max_err(cr.scalars["IMEP"], tr["IMEP"], True) < 0.5
        assert max_err(cr.scalars["PMAX"], tr["PMAX"], True) < 0.1
        assert max_err(cr.scalars["APMAX"], tr["APMAX"]) < 0.3
        assert max_err(cr.scalars["AI50"], tr["AI50"]) < 0.2
        assert max_err(cr.scalars["AI05"], tr["AI05"]) < 0.5
        assert max_err(cr.scalars["AI10"], tr["AI10"]) < 0.5
        assert max_err(cr.scalars["AI90"], tr["AI90"]) < 0.5
        tol_t = max(0.2 * dt, 2e-6)
        assert max_err(cr.scalars["PI_TSOE_1"], tr["PI_TSOE"]) < tol_t
        assert max_err(cr.scalars["PI_TEOE_1"], tr["PI_TEOE"]) < 1.5 * tol_t
        assert max_err(cr.scalars["PI_SOE_1"], tr["PI_SOE"]) < 0.1
        assert (cr.scalars["PI_N"] == 1).all()
        # knock: every cycle classified correctly, peak within the band-pass attenuation
        assert (cr.scalars["KNOCK"] == np.array(tr["KNOCK"])).all()
        kn = np.array(tr["KNOCK"]) == 1
        rel = (cr.scalars["KP_PK"][kn] - np.array(tr["KP_PK"])[kn]) / np.array(tr["KP_PK"])[kn]
        assert rel.min() > -0.25 and rel.max() < 0.05
        assert cr.scalars["KP_PK"][~kn].max() < 0.3
        assert cr.stats["KNOCK_FREQ"]["mean"] == pytest.approx(kn.mean() * 100.0)
        # knock sensor: the burst of the knocking cycles stands clearly out of the noise
        kls = cr.scalars["KLS_PK"]
        assert kls[kn].min() > 5.0 * kls[~kn].max()
        assert 0.3 < kls[kn].mean() < 0.55
        assert cr.scalars["KLS_RATIO"][kn].min() > 5.0


def test_grid_follows_sample_density():
    assert analyze(measure(SynthSpec(fs=30000.0))[0], Config()).info["res"] == 0.5
    assert analyze(measure(SynthSpec(fs=150000.0))[0], Config()).info["res"] == 0.1


def test_speed_offsets_and_plausibility(ref30):
    res, _ = ref30
    for cr in res.cyl.values():
        assert cr.stats["SPEED"]["mean"] == pytest.approx(1497.0, abs=0.5)
        # amplifier offset 1.10 V = 110 bar is removed by the zero level correction
        assert cr.stats["POFFS"]["mean"] == pytest.approx(-110.0, abs=0.2)
        assert cr.stats["PI_OFFS"]["mean"] == pytest.approx(2.0, abs=0.01)
        assert cr.stats["PI_IHOLD_1"]["mean"] == pytest.approx(3.0, abs=0.05)
        assert cr.stats["PI_DUR_1"]["mean"] == pytest.approx(2.11, abs=0.03)
        assert cr.stats["POLY_C"]["mean"] == pytest.approx(1.32, abs=0.01)
        assert 0 < cr.stats["APMAX"]["mean"] < 40      # combustion is not split over two cycles
        assert cr.stats["IGD"]["mean"] > 0
    d = np.median(res.cyl["L"].cmap.phi - res.cyl["H"].cmap.phi)
    assert d == pytest.approx(-240.0, abs=0.05)


def test_locked_speed_worst_case():
    """Sample rate and speed exactly commensurate: angle is known only to a fraction of a sample."""
    m, truth = measure(SynthSpec(speed_rpm=1500.0, ca_start_h=275.0))
    res = analyze(m, Config())
    assert max_err(res.cyl["H"].scalars["IMEP"], truth.cyl["H"]["IMEP"], True) < 0.5
    assert max_err(res.cyl["H"].scalars["AI50"], truth.cyl["H"]["AI50"]) < 0.2


def test_rounded_crank_angle_steps():
    """If the file value is the rounded angle, step_mode 'round' removes the 0.5 deg shift."""
    m, truth = measure(SynthSpec(ca_round=True))
    cfg = Config()
    cfg.ca.step_mode = "round"
    res = analyze(m, cfg)
    assert max_err(res.cyl["H"].scalars["AI50"], truth.cyl["H"]["AI50"]) < 0.2
    assert max_err(res.cyl["H"].scalars["IMEP"], truth.cyl["H"]["IMEP"], True) < 0.5
    wrong = analyze(m, Config())                      # default 'floor' on rounded data
    # peak pressure position is purely geometric: it moves by the full half degree
    shift = np.nanmean(wrong.cyl["H"].scalars["APMAX"] - np.array(truth.cyl["H"]["APMAX"]))
    assert shift == pytest.approx(0.5, abs=0.1)


def test_continuous_crank_angle():
    m, truth = measure(SynthSpec(stepped=False))
    res = analyze(m, Config())
    assert not res.cyl["H"].cmap.info["stepped"]
    assert max_err(res.cyl["H"].scalars["IMEP"], truth.cyl["H"]["IMEP"], True) < 0.5


def test_pressure_sensitivity_is_a_parameter(ref30):
    res, _ = ref30
    cfg = Config()
    cfg.cyl["H"].dmr_sens_mv_per_bar = 20.0
    res2 = analyze(res.meas, cfg)
    assert res2.cyl["H"].stats["PMAX"]["mean"] == pytest.approx(
        res.cyl["H"].stats["PMAX"]["mean"] / 2.0, rel=1e-6)
    assert res2.cyl["L"].stats["PMAX"]["mean"] == pytest.approx(
        res.cyl["L"].stats["PMAX"]["mean"], rel=1e-9)


def test_tdc_offset_shifts_results(ref30):
    res, _ = ref30
    cfg = Config()
    cfg.cyl["H"].ca_offset_deg = 2.0
    res2 = analyze(res.meas, cfg)
    assert (res2.cyl["H"].stats["APMAX"]["mean"] - res.cyl["H"].stats["APMAX"]["mean"]
            == pytest.approx(2.0, abs=0.05))


# -------------------------------------------------- missing columns (3.3)
@pytest.mark.parametrize("missing, has_dmr, has_pi", [
    ((), True, True),
    (("PI_RM_H",), True, False),
    (("DMR_H",), False, True),
    (("KLS_H",), True, True),
])
def test_missing_signal_reduces_scope(missing, has_dmr, has_pi):
    m, truth = measure(SynthSpec(missing=missing, duration=1.0))
    res = analyze(m, Config())
    cr = res.cyl["H"]
    assert cr.has_dmr == has_dmr and cr.has_pi == has_pi
    assert ("PMAX" in cr.scalars) == has_dmr
    assert ("KP_PK" in cr.scalars) == has_dmr
    assert ("PI_SOE_1" in cr.scalars) == has_pi
    assert ("IGD" in cr.scalars) == (has_dmr and has_pi)
    assert ("KLS_PK" in cr.scalars) == ("KLS_H" not in missing) == cr.has_kls
    assert ("KLS" in cr.curves) == cr.has_kls
    assert "PMAX" in res.cyl["L"].scalars       # the other cylinder is unaffected
    assert cr.n_cycles == truth.cyl["H"]["N_CYCLES"]


def test_cylinder_without_signal_is_skipped():
    m, _ = measure(SynthSpec(missing=("DMR_L", "PI_RM_L", "KLS_L"), duration=1.0))
    res = analyze(m, Config())
    assert list(res.cyl) == ["H"]


def test_knock_sensor_only():
    """A cylinder with nothing but the knock sensor column is still evaluated."""
    m, truth = measure(SynthSpec(missing=("DMR_H", "PI_RM_H"), duration=1.0))
    res = analyze(m, Config())
    cr = res.cyl["H"]
    assert cr.has_kls and not cr.has_dmr and not cr.has_pi
    assert cr.n_cycles == truth.cyl["H"]["N_CYCLES"] and cr.used().all()
    assert {"KLS_PK", "AKLS_PK", "KLS_INT", "KLS_REF", "KLS_RATIO", "SPEED"} <= set(cr.scalars)
    assert "PMAX" not in cr.scalars and "KNOCK" not in cr.scalars
    cfg = Config()
    cfg.knock.enabled = False
    cr = analyze(m, cfg).cyl["H"]
    assert "KLS" in cr.curves and "KLS_PK" not in cr.scalars


def test_no_signal_at_all_is_an_error(tmp_path):
    spec = SynthSpec(missing=("DMR_H", "DMR_L", "PI_RM_H", "PI_RM_L", "KLS_H", "KLS_L"),
                     duration=0.2)
    f = tmp_path / "nosignal.csv"
    make_file(f, spec)
    with pytest.raises(CsvFormatError, match="no signal column"):
        read_measurement(f)


def test_missing_crank_angle_with_signal_is_an_error(tmp_path):
    f = tmp_path / "noca.csv"
    make_file(f, SynthSpec(missing=("CA_L",), duration=0.2))
    with pytest.raises(CsvFormatError, match="CA Low"):
        read_measurement(f)


def test_missing_geometry_gives_reduced_scope(ref30):
    res, _ = ref30
    cfg = Config()
    cfg.engine.compression = 0.0
    res2 = analyze(res.meas, cfg)
    cr = res2.cyl["H"]
    assert "PMAX" in cr.scalars and "KP_PK" in cr.scalars and "PI_SOE_1" in cr.scalars
    assert "IMEP" not in cr.scalars and "AI50" not in cr.scalars
    assert any("geometry" in w for w in res2.warnings)
    assert any("'fixed'" in w for w in res2.warnings)
    # fixed zero level correction still removes the 110 bar offset roughly
    assert cr.stats["PMAX"]["mean"] < 200.0


# ------------------------------------------------------------ file formats
def test_reference_format_roundtrip(tmp_path):
    f = tmp_path / "ref.csv"
    truth = make_file(f, SynthSpec(duration=1.0))
    head = f.read_text().splitlines()
    assert head[0] == "Time,CA High,CA Low,DMR_L,DMR_H,PI_RM_L,PI_RM_H,KLS_H,KLS_L"
    assert head[1].startswith("0.0,275,35,")
    assert head[2].startswith("3.3333333333333335e-05,")
    fmt = sniff_csv(f)
    assert (fmt.delimiter, fmt.decimal, fmt.units_row) == (",", ".", False)
    m = read_measurement(f)
    assert m.fs == pytest.approx(30000.0)
    assert m.time[1] == 3.3333333333333335e-05          # full float64 precision
    assert not m.warnings
    res = analyze(m, Config())
    assert res.cyl["H"].n_cycles == truth.cyl["H"]["N_CYCLES"]


def test_semicolon_decimal_comma_and_units_row(tmp_path):
    spec = SynthSpec(duration=0.5, delimiter=";", decimal=",", units_row=True)
    f = tmp_path / "german.csv"
    make_file(f, spec)
    fmt = sniff_csv(f)
    assert (fmt.delimiter, fmt.decimal, fmt.units_row) == (";", ",", True)
    m = read_measurement(f)
    ref, _ = measure(spec)
    assert np.allclose(m.time, ref.time, rtol=0, atol=1e-15)
    assert np.allclose(m.raw["DMR_H"], ref.raw["DMR_H"], rtol=0, atol=1e-12)


def test_tolerant_column_names(tmp_path):
    assert canonical_name(" ca_high ") == "CA_H"
    assert canonical_name("CA Low") == "CA_L"
    assert canonical_name("DMR_H [V]") == "DMR_H"
    assert canonical_name("pi rm l (A)") == "PI_RM_L"
    assert canonical_name("TIME") == "TIME"
    assert canonical_name("kls_h") == "KLS_H" and canonical_name("KLS Low [V]") == "KLS_L"
    assert canonical_name("Temperature") is None
    df, _ = generate(SynthSpec(duration=0.3))
    df = df.rename(columns={"CA High": "ca_high", "DMR_H": "DMR_H [V]", "Time": "time",
                            "KLS_H": "kls_h", "KLS_L": "kls_l"})
    df["Extra"] = 1.0
    f = tmp_path / "names.csv"
    write_csv(df, f)
    m = read_measurement(f)
    assert m.has("CA_H") and m.has("DMR_H") and m.has("KLS_H") and m.has("KLS_L")
    assert any("Extra" in w for w in m.warnings)


def test_comma_as_delimiter_and_decimal_is_rejected(tmp_path):
    f = tmp_path / "bad.csv"
    f.write_text("Time,CA High,DMR_H\n0,0,275,1,12\n0,001,275,1,13\n")
    with pytest.raises(CsvFormatError, match="both as column delimiter and as decimal"):
        read_measurement(f)


def test_non_monotonic_time_is_rejected_with_line_number(tmp_path):
    df, _ = generate(SynthSpec(duration=0.1))
    df.loc[100, "Time"] = df.loc[99, "Time"]
    f = tmp_path / "time.csv"
    write_csv(df, f)
    with pytest.raises(CsvFormatError, match="line 102"):
        read_measurement(f)


def test_gaps_small_are_interpolated_large_invalidate_cycle(tmp_path):
    df, _ = generate(SynthSpec(duration=1.0))
    df.loc[5000:5001, "DMR_H"] = np.nan          # 2 samples: interpolated
    f = tmp_path / "gap_small.csv"
    write_csv(df, f)
    m = read_measurement(f)
    assert not np.isnan(m.raw["DMR_H"]).any()
    assert any("interpolated" in w for w in m.warnings)

    df.loc[9000:9100, "DMR_H"] = np.nan          # long gap: cycle invalid
    f = tmp_path / "gap_large.csv"
    write_csv(df, f)
    m = read_measurement(f)
    res = analyze(m, Config())
    cr = res.cyl["H"]
    assert (~cr.used()).sum() == 1
    assert res.cyl["L"].used().all()
    assert cr.stats["PMAX"]["n"] == cr.n_cycles - 1


# ------------------------------------------------------------ settings, CLI
def test_config_toml_roundtrip(tmp_path):
    cfg = Config()
    cfg.engine.compression = 10.5
    cfg.cyl["L"].dmr_sens_mv_per_bar = 12.5
    cfg.ca.resolution_deg = 0.2
    cfg.knock.band_hz = [2500.0, 9000.0]
    cfg.pi.baseline = 1.95
    f = tmp_path / "engine.toml"
    save_config(cfg, f)
    assert load_config(f).to_dict() == cfg.to_dict()
    assert load_config(None).to_dict() == Config().to_dict()
    (tmp_path / "bad.toml").write_text("[engine]\nbore = 1\n")
    with pytest.raises(ValueError, match="unknown setting"):
        load_config(tmp_path / "bad.toml")


def test_cycle_selection():
    assert parse_cycle_selection("all", 10) == slice(0, 10)
    assert parse_cycle_selection("3:5", 10) == slice(2, 5)
    assert parse_cycle_selection("8:", 10) == slice(7, 10)
    m, _ = measure(SynthSpec(duration=1.0))
    cfg = Config()
    cfg.output.cycles = "2:4"
    res = analyze(m, cfg)
    assert res.cyl["H"].used().sum() == 3
    assert res.cyl["H"].stats["PMAX"]["n"] == 3


def test_batch_run_writes_all_outputs(tmp_path, capsys):
    f = tmp_path / "meas.csv"
    make_file(f, SynthSpec(duration=1.0))
    out = tmp_path / "out"
    assert cli_main(["--batch", str(f), "--out", str(out), "--save-cycles"]) == 0
    names = {p.name for p in out.iterdir()}
    for n in ("meas_cycles.csv", "meas_stats.csv", "meas_curves_H.csv", "meas_curves_L.csv",
              "meas_allcycles_H.npz", "meas_log.txt", "meas_report.html"):
        assert n in names
    assert "assumed engine data" in (out / "meas_report.html").read_text()
    header = (out / "meas_cycles.csv").read_text().splitlines()[0].split(";")
    assert header[:4] == ["cyl", "cycle", "valid", "t_start"]
    assert {"PMAX", "IMEP", "AI50", "KP_PK", "KNOCK", "KLS_PK", "PI_SOE_1", "POFFS",
            "PI_OFFS"} <= set(header)
    assert "High:" in capsys.readouterr().out


def test_batch_exit_codes(tmp_path):
    assert cli_main(["--batch", str(tmp_path / "missing.csv")]) == 1
    f = tmp_path / "short.csv"
    make_file(f, SynthSpec(duration=0.03))          # shorter than one cycle
    assert cli_main(["--batch", str(f), "--out", str(tmp_path / "o"), "--no-plots"]) == 2
    f = tmp_path / "only_h.csv"
    make_file(f, SynthSpec(duration=0.5))
    assert cli_main(["--batch", str(f), "--cyl", "H", "--out", str(tmp_path / "h"),
                     "--no-plots"]) == 0
    rows = (tmp_path / "h" / "only_h_cycles.csv").read_text().splitlines()[1:]
    assert rows and all(r.startswith("High;") for r in rows)
