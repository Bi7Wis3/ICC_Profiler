import json

import pytest

from icc_profiler.config import JobConfig, resolve_path, store_path
from icc_profiler.errors import PipelineError

from .conftest import make_config


def test_unknown_keys_are_ignored_and_new_fields_default():
    config = JobConfig.from_dict({"job_name": "x", "future_key": 1, "chart": {"colorspace": "RGB", "nope": 2}})
    assert config.chart.ink_limit is None
    assert config.measurement.colorimeter_csv.lab_reference == ""
    assert config.measurement.scanner.profile_quality == "medium"


@pytest.mark.parametrize("name", ["../evil", "a b", ".x"])
def test_unsafe_job_names_fail_validation(name):
    with pytest.raises(PipelineError, match="job_name"):
        make_config(name).validate()


def test_ink_limit_range_validated():
    config = make_config(colorspace="CMYK")
    config.chart.ink_limit = 500
    with pytest.raises(PipelineError, match="ink_limit"):
        config.validate()


def test_paths_saved_relative_to_the_job(tmp_path):
    job_dir = tmp_path / "project" / "jobs" / "j"
    shared = tmp_path / "project" / "shared" / "it8_references" / "it8.cie"
    scan = job_dir / "02_measurements" / "scan.tif"
    for f in (shared, scan):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")
    config = make_config("j", method="scanner")
    config.measurement.scanner.it8_cgats_reference_file = str(shared)
    config.measurement.scanner.test_chart_scan = str(scan)
    config.save(job_dir / "config.json")
    saved = json.loads((job_dir / "config.json").read_text())["measurement"]["scanner"]
    assert saved["test_chart_scan"] == "02_measurements/scan.tif"
    assert saved["it8_cgats_reference_file"] == "../../shared/it8_references/it8.cie"
    assert resolve_path(saved["it8_cgats_reference_file"], job_dir) == shared.resolve()
    assert store_path(scan, job_dir) == "02_measurements/scan.tif"


def test_old_absolute_wsl_paths_are_rebased_onto_this_project(tmp_path):
    job_dir = tmp_path / "project" / "jobs" / "scanner_test"
    scan = job_dir / "02_measurements" / "test_chart_scan.tif"
    scan.parent.mkdir(parents=True)
    scan.write_text("x")
    old = "/mnt/c/Users/someone/Desktop/ICC_profiler/jobs/scanner_test/02_measurements/test_chart_scan.tif"
    assert resolve_path(old, job_dir) == scan
    assert resolve_path("C:\\Users\\x\\ICC_profiler\\jobs\\scanner_test\\02_measurements\\test_chart_scan.tif", job_dir) == scan
