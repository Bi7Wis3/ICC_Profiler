"""End-to-end colorimeter run against a real ArgyllCMS install.

Skipped when targen isn't on PATH. Readings are synthesized from the
chart's own XYZ estimates, so this checks that every command line and
file format is accepted by the real tools, not profile accuracy.
Run just these with: pytest -m argyll
"""
import shutil

import pytest

from icc_profiler import cgats, check_chart, cht_geometry, csv_import, spot_colors, stages, state
from icc_profiler.color_convert import D50_WHITE, xyz_to_lab

from .conftest import make_config

pytestmark = [
    pytest.mark.argyll,
    pytest.mark.skipif(shutil.which("targen") is None, reason="ArgyllCMS not installed"),
]


@pytest.mark.parametrize("colorspace", ["RGB", "CMYK"])
def test_colorimeter_pipeline_with_real_argyll(jobs_root, colorspace):
    config = make_config(f"e2e_{colorspace.lower()}", colorspace=colorspace)
    config.chart.patch_count = 40
    config.output.quality = "low"
    if colorspace == "CMYK":
        config.chart.ink_limit = 300
        config.output.black_ink_limit = 90
    config.measurement.colorimeter_csv.lab_reference = "D50_2"
    job_dir = stages.init_job(jobs_root, config)

    result = stages.run_all(job_dir, config)
    assert result["stopped_at"] == "import-measurements"

    table = cgats.read(stages.ti2_path(job_dir, config))
    lines = ["Name;L*;a*;b*"]
    for row in table.rows:
        L, a, b = xyz_to_lab(float(row["XYZ_X"]), float(row["XYZ_Y"]), float(row["XYZ_Z"]), white=D50_WHITE)
        lines.append(f"{row['SAMPLE_LOC']};{L:.2f};{a:.2f};{b:.2f}".replace(".", ","))
    csv_path = stages.measurements_in_dir(job_dir) / "export.csv"
    csv_path.write_bytes(b"\xef\xbb\xbf" + "\r\n".join(lines).encode())
    stages.bind_input(job_dir, config, "colorimeter_csv")
    config.measurement.colorimeter_csv.csv_file = str(csv_path)

    result = stages.run_all(job_dir, config)
    assert result["stopped_at"] is None, result
    assert stages.icc_path(job_dir, config).stat().st_size > 1000
    report = (stages.profile_dir(job_dir) / f"{config.job_name}_verify.txt").read_text()
    assert "Profile check complete" in report
    st = stages.load_state(job_dir, config)
    assert all(state.is_done(st, s) for s in stages.order_for(config))

    # Check chart: different patches, read back in measuring order.
    config.chart.min_patch_size_mm = 10
    check_chart.generate(job_dir, config, patch_count=30)
    info = check_chart.status(job_dir, config)
    assert info["has_chart"] and info["pages"]
    assert info["first_loc"].startswith("P")  # can't be mixed up with the main chart's A, B, ...
    check_rows = cgats.read(check_chart.ti2_path(job_dir, config)).rows
    by_loc = {r["SAMPLE_LOC"]: r for r in check_rows}
    lines = ["Name,L*,a*,b*"]
    for i, loc in enumerate(sorted(by_loc, key=csv_import.loc_sort_key), start=1):
        row = by_loc[loc]
        L, a, b = xyz_to_lab(float(row["XYZ_X"]), float(row["XYZ_Y"]), float(row["XYZ_Z"]), white=D50_WHITE)
        lines.append(f"Sample{i},{L:.2f},{a:.2f},{b:.2f}")
    config.measurement.colorimeter_csv.match_by = "order"
    check_chart.save_export(job_dir, config, "\n".join(lines).encode(), ".csv")
    result = check_chart.run(job_dir, config)
    assert result["patch_count"] == len(check_rows) and float(result["average_de"]) < 10
    assert check_chart.status(job_dir, config)["result"]["outdated"] is False


    # Spot colors: recipes from the real profile, and a printable proof sheet.
    spot_colors.add_colors(job_dir, config, spot_colors.parse_typed("Mid gray; 60; 0; 0\nToo bright; 60; 0; 110"), "typed")
    colors = {c["name"]: c for c in spot_colors.status(job_dir, config)["colors"]}
    assert len(colors["Mid gray"]["device"]) == len(colorspace)
    assert colors["Too bright"]["out_of_gamut"]
    spot_colors.make_proof(job_dir, config)
    proof = spot_colors.status(job_dir, config)["proof"]
    assert proof["ready"] and proof["pages"] and all(label.startswith("S") for label in proof["labels"])

    # Feed the proof readings back into the profile: real colprof must accept
    # the extra SPOT patches and the recipes must be looked up again.
    data = spot_colors.load(job_dir)
    by_id = {r["SAMPLE_ID"]: r for r in cgats.read(spot_colors.proof_ti2_path(job_dir, config)).rows}
    lab_by_name = {c["name"]: c["expected_lab"] for c in data["colors"]}
    lines = ["Name,L*,a*,b*"]
    for i, (sid, row) in enumerate(sorted(by_id.items(), key=lambda kv: csv_import.loc_sort_key(kv[1]["SAMPLE_LOC"])), 1):
        name = data["proof"]["names"].get(sid)
        L, a, b = lab_by_name[name] if name else (95.0, 0.0, -2.0)
        lines.append(f"Sample{i},{L - 3:.2f},{a:.2f},{b:.2f}")
    spot_colors.save_proof_export(job_dir, config, "\n".join(lines).encode(), ".csv")
    assert spot_colors.read_proof(job_dir, config) == 2
    assert spot_colors.add_proof_to_profile(job_dir, config) == 2
    stages.build_profile(job_dir, config)
    assert (stages.ti3_dir(job_dir) / f"{config.job_name}_plus.icc").is_file()
    spot_colors.recompute(job_dir, config)
    assert not spot_colors.status(job_dir, config)["outdated"]


def test_regenerated_chart_leaves_no_old_pages(jobs_root):
    """A 2-page chart regenerated as 1 page must not keep the old pages
    (they'd be printed with the new chart), and the patch size must come
    from the new layout, not a leftover .cht."""
    config = make_config("e2e_pages", colorspace="CMYK")
    config.chart.patch_count = 240
    config.chart.page_size = "A4"
    config.chart.min_patch_size_mm = 14
    config.chart.raster_dpi = 100
    job_dir = stages.init_job(jobs_root, config)
    stages.generate_chart(job_dir, config)
    chart_dir = stages.chart_basename(job_dir, config).parent
    assert sorted(p.name for p in chart_dir.glob("*.tif")) == ["e2e_pages_01.tif", "e2e_pages_02.tif"]

    config.chart.page_size = "A3"
    config.chart.min_patch_size_mm = 12
    stages.generate_chart(job_dir, config)
    assert [p.name for p in chart_dir.glob("*.tif")] == ["e2e_pages.tif"]
    assert not list(chart_dir.glob("*_0[0-9].cht"))
    assert min(cht_geometry.read_patch_size_mm(stages.cht_path(job_dir, config))) == pytest.approx(12, abs=0.01)
