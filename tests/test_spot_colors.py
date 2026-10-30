from pathlib import Path

import pytest

from icc_profiler import cgats, csv_import, spot_colors, stages
from icc_profiler.errors import PipelineError

from .conftest import DATA, write_csv_for_chart


@pytest.fixture
def profiled_job(colorimeter_job):
    job_dir, config = colorimeter_job
    config.measurement.colorimeter_csv.lab_reference = "D50_2"
    config.measurement.colorimeter_csv.csv_file = str(write_csv_for_chart(job_dir, config))
    stages.bind_input(job_dir, config, "colorimeter_csv")
    assert stages.run_all(job_dir, config)["stopped_at"] is None
    return job_dir, config


def test_parse_typed_accepts_semicolons_tabs_and_commas():
    colors = spot_colors.parse_typed("PANTONE 185 C; 50,2; 74,3; 46\nWarm Gray 5\t69\t2\t6\n\nBlue, 30, 10, -40")
    assert [n for n, _ in colors] == ["PANTONE 185 C", "Warm Gray 5", "Blue"]
    assert colors[0][1] == pytest.approx((50.2, 74.3, 46.0))


@pytest.mark.parametrize("bad", ["Red; 50; 60", "Red; 150; 0; 0", 'Re<d; 50; 0; 0'])
def test_parse_typed_rejects_bad_lines(bad):
    with pytest.raises(PipelineError):
        spot_colors.parse_typed(bad)


def test_measured_book_needs_names_for_sample_readings(profiled_job):
    _, config = profiled_job
    config.measurement.colorimeter_csv.specular = "SCE"
    sqcx = DATA / "sqcx_ps110_sample.xlsx"
    with pytest.raises(PipelineError, match="names"):
        spot_colors.import_measured(config, sqcx)
    with pytest.raises(PipelineError, match="3 reading"):
        spot_colors.import_measured(config, sqcx, "A\nB")
    colors = spot_colors.import_measured(config, sqcx, "PANTONE 1\nPANTONE 2\n\nPANTONE 3\n")
    assert [n for n, _ in colors] == ["PANTONE 1", "PANTONE 2", "PANTONE 3"]
    assert colors[0][1] == pytest.approx((39.5, -0.2, -16.4))


def test_measured_book_in_d65_is_adapted_to_d50(profiled_job, tmp_path):
    _, config = profiled_job
    config.measurement.colorimeter_csv.lab_reference = "D65_2"
    path = tmp_path / "book.csv"
    path.write_text("Name,L*,a*,b*\nRed,50,40,-30\n")
    (name, lab), = spot_colors.import_measured(config, path)
    assert name == "Red" and lab != pytest.approx((50, 40, -30))


def test_recipes_need_a_profile(colorimeter_job):
    job_dir, config = colorimeter_job
    with pytest.raises(PipelineError, match="Build the profile"):
        spot_colors.add_colors(job_dir, config, [("Red", (50, 40, 30))], "typed")


def test_recipes_gamut_and_csv(profiled_job):
    job_dir, config = profiled_job
    spot_colors.add_colors(job_dir, config, [("Red", (50, 40, 30)), ("Blue", (40, 10, -50))], "typed")
    colors = {c["name"]: c for c in spot_colors.status(job_dir, config)["colors"]}
    assert colors["Red"]["device"] == pytest.approx([50.0, 31.2, 23.4], abs=0.1)
    assert colors["Red"]["expected_de"] < 0.1 and not colors["Red"]["out_of_gamut"]
    assert colors["Blue"]["out_of_gamut"]  # the toy printer has no negative b*
    # Re-adding a name replaces it instead of duplicating.
    spot_colors.add_colors(job_dir, config, [("red", (60, 40, 30))], "typed")
    assert len(spot_colors.load(job_dir)["colors"]) == 2
    header, red_row, _ = spot_colors.recipes_csv(job_dir, config).splitlines()
    assert header.startswith("Name,R %,G %,B %,Target L*") and red_row.startswith("red,40.0,31.2,23.4")


def _measure_proof(job_dir, config, shift):
    """Proof readings: what the toy profile predicts for each recipe, plus shift."""
    rows = cgats.read(spot_colors.proof_ti2_path(job_dir, config)).rows
    names = spot_colors.load(job_dir)["proof"]["names"]
    recipes = {c["name"]: c["device"] for c in spot_colors.load(job_dir)["colors"]}
    lines = ["Name,L*,a*,b*"]
    for row in sorted(rows, key=lambda r: csv_import.loc_sort_key(r["SAMPLE_LOC"])):
        name = names.get(row["SAMPLE_ID"])
        if name is None:
            lab = (95, 0, -2)
        else:
            c, m, y = (v / 100 for v in recipes[name])
            lab = (100 - 100 * c + shift[0], 128 * m + shift[1], 128 * y + shift[2])
        lines.append(f"{row['SAMPLE_LOC']},{lab[0]:.2f},{lab[1]:.2f},{lab[2]:.2f}")
    return "\n".join(lines).encode()


def test_proof_sheet_round_trip_corrects_recipes(profiled_job, fake_argyll):
    job_dir, config = profiled_job
    spot_colors.add_colors(job_dir, config, [("Red", (50, 40, 30)), ("Blue", (40, 10, -50))], "typed")
    with pytest.raises(PipelineError, match="proof sheet first"):
        spot_colors.save_proof_export(job_dir, config, b"x", ".csv")
    fake_argyll.ti1_source = DATA / "rgb20.ti1"
    spot_colors.make_proof(job_dir, config)
    assert any("-xS-Z" in a for call in fake_argyll.calls if "printtarg" in call[0] for a in call)
    # The fake printtarg reuses a fixed .ti2, so map our names onto its first sample ids.
    data = spot_colors.load(job_dir)
    ids = [r["SAMPLE_ID"] for r in cgats.read(spot_colors.proof_ti2_path(job_dir, config)).rows]
    data["proof"]["names"] = {ids[0]: "Red", ids[1]: "Blue"}
    spot_colors._save(job_dir, data)

    spot_colors.save_proof_export(job_dir, config, _measure_proof(job_dir, config, (3, -2, 0)), ".csv")
    assert spot_colors.read_proof(job_dir, config) == 2
    colors = {c["name"]: c for c in spot_colors.status(job_dir, config)["colors"]}
    assert colors["Red"]["measured_de"] > 2
    assert spot_colors.status(job_dir, config)["can_correct"]

    assert spot_colors.apply_corrections(job_dir, config) >= 1
    red = {c["name"]: c for c in spot_colors.load(job_dir)["colors"]}["Red"]
    assert red["rounds"] == 1 and red["correction"] == pytest.approx([-3, 2, 0], abs=0.1)
    # The corrected recipe now prints on target.
    c, m, y = (v / 100 for v in red["device"])
    assert (100 - 100 * c + 3, 128 * m - 2) == pytest.approx((50, 40), abs=0.3)
    # Nothing more to correct until a new proof is measured.
    assert not spot_colors.status(job_dir, config)["can_correct"]
    assert spot_colors.status(job_dir, config)["proof_stale"]

    # Measuring the old sheet again after correcting: the readings belong to
    # the old recipes, so they must not be taken as a miss of the new ones.
    spot_colors.save_proof_export(job_dir, config, _measure_proof(job_dir, config, (3, -2, 0)), ".csv")
    spot_colors.read_proof(job_dir, config)
    red = {c["name"]: c for c in spot_colors.load(job_dir)["colors"]}["Red"]
    assert red["measured_device"] != red["device"]
    assert not spot_colors.status(job_dir, config)["can_correct"]
    assert spot_colors.apply_corrections(job_dir, config) == 0


def test_proof_readings_feed_the_profile(profiled_job, fake_argyll):
    job_dir, config = profiled_job
    spot_colors.add_colors(job_dir, config, [("Red", (50, 40, 30)), ("Blue", (40, 10, -50))], "typed")
    with pytest.raises(PipelineError, match="proof sheet"):
        spot_colors.add_proof_to_profile(job_dir, config)
    fake_argyll.ti1_source = DATA / "rgb20.ti1"
    spot_colors.make_proof(job_dir, config)
    data = spot_colors.load(job_dir)
    ids = [r["SAMPLE_ID"] for r in cgats.read(spot_colors.proof_ti2_path(job_dir, config)).rows]
    data["proof"]["names"] = {ids[0]: "Red", ids[1]: "Blue"}
    spot_colors._save(job_dir, data)
    spot_colors.save_proof_export(job_dir, config, _measure_proof(job_dir, config, (3, -2, 0)), ".csv")
    spot_colors.read_proof(job_dir, config)
    spot_colors.apply_corrections(job_dir, config)

    assert spot_colors.add_proof_to_profile(job_dir, config) == 2
    # Adding the same sheet again replaces its readings instead of doubling them.
    assert spot_colors.add_proof_to_profile(job_dir, config) == 2
    extra = cgats.read(stages.extra_patches_path(job_dir, config))
    assert [r["SAMPLE_ID"] for r in extra.rows] == ["SPOT1", "SPOT2"]
    assert all(c["correction"] == [0.0, 0.0, 0.0] for c in spot_colors.load(job_dir)["colors"])

    main_rows = len(cgats.read(stages.ti3_path(job_dir, config)).rows)
    old_icc = stages.icc_path(job_dir, config).read_bytes()
    backup = spot_colors.backup_profile(job_dir, config)
    assert backup.read_bytes() == old_icc
    stages.build_profile(job_dir, config)
    colprof = [c for c in fake_argyll.calls if "colprof" in c[0]][-1]
    assert colprof[-1].endswith("_plus")
    plus = cgats.read(Path(colprof[-1] + ".ti3"))
    assert len(plus.rows) == main_rows + 2
    # The chart's own measurements are untouched.
    assert len(cgats.read(stages.ti3_path(job_dir, config)).rows) == main_rows

    spot_colors.remove_extra_patches(job_dir, config)
    assert spot_colors.extra_patch_count(job_dir, config) == 0
    assert list(stages.ti3_dir(job_dir).glob("*_extra.removed-*.ti3"))
    stages.build_profile(job_dir, config)
    assert not [c for c in fake_argyll.calls if "colprof" in c[0]][-1][-1].endswith("_plus")
