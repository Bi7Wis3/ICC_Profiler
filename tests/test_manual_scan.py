import csv
import json

import pytest

from icc_profiler import manual_scan, stages
from icc_profiler.errors import PipelineError


def test_write_csv_in_physical_order_and_bound_to_chart(colorimeter_job):
    job_dir, config = colorimeter_job
    patches = manual_scan.chart_patches(job_dir, config)
    for i, p in enumerate(patches):
        manual_scan.record_reading(job_dir, config, p["loc"], str(40 + i), "1,5", "-2")
    dest = manual_scan.write_csv(job_dir, config)
    rows = list(csv.DictReader(dest.open()))
    assert [r["Name"] for r in rows] == [p["loc"] for p in patches]
    assert rows[0]["a*"] == "1.5"
    assert stages.input_matches_chart(job_dir, config, "colorimeter_csv", dest)


def test_write_csv_refuses_incomplete_scan(colorimeter_job):
    job_dir, config = colorimeter_job
    loc = manual_scan.chart_patches(job_dir, config)[0]["loc"]
    manual_scan.record_reading(job_dir, config, loc, "50", "0", "0")
    with pytest.raises(PipelineError, match="still need a reading"):
        manual_scan.write_csv(job_dir, config)


@pytest.mark.parametrize("L, a, b", [("101", "0", "0"), ("50", "-151", "0"), ("x", "0", "0")])
def test_readings_are_range_checked(colorimeter_job, L, a, b):
    job_dir, config = colorimeter_job
    loc = manual_scan.chart_patches(job_dir, config)[0]["loc"]
    with pytest.raises(PipelineError):
        manual_scan.record_reading(job_dir, config, loc, L, a, b)


def test_unknown_patch_label_rejected(colorimeter_job):
    job_dir, config = colorimeter_job
    with pytest.raises(PipelineError):
        manual_scan.record_reading(job_dir, config, "ZZ99", "50", "0", "0")


def test_readings_from_an_older_chart_are_set_aside(colorimeter_job):
    job_dir, config = colorimeter_job
    loc = manual_scan.chart_patches(job_dir, config)[0]["loc"]
    manual_scan.record_reading(job_dir, config, loc, "50", "0", "0")
    stages.run_stage(job_dir, config, "generate-chart")
    assert manual_scan.is_stale(job_dir, config)
    assert manual_scan.load_progress(job_dir, config) == {}
    manual_scan.record_reading(job_dir, config, loc, "60", "0", "0")
    assert not manual_scan.is_stale(job_dir, config)
    kept = list(stages.measurements_in_dir(job_dir).glob("manual_scan_progress.stale-*.json"))
    assert len(kept) == 1 and json.loads(kept[0].read_text())["readings"][loc]["L"] == 50


def test_legacy_flat_progress_file_still_loads(colorimeter_job):
    job_dir, config = colorimeter_job
    loc = manual_scan.chart_patches(job_dir, config)[0]["loc"]
    manual_scan.progress_path(job_dir).write_text(json.dumps({loc: {"L": 50, "a": 0, "b": 0}}))
    assert manual_scan.load_progress(job_dir, config) == {loc: {"L": 50, "a": 0, "b": 0}}
