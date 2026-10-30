import json
import os
import threading
import time

import pytest

from icc_profiler import joblock, stages, state
from icc_profiler.errors import PipelineError

from .conftest import make_config, write_csv_for_chart


def _confirm_lab(job_dir, config):
    config.measurement.colorimeter_csv.lab_reference = "D50_2"
    config.save(job_dir / "config.json")


def _set_csv(job_dir, config, path):
    config.measurement.colorimeter_csv.csv_file = str(path)
    config.save(job_dir / "config.json")


def _done(job_dir, config):
    st = stages.load_state(job_dir, config)
    return [s for s in stages.order_for(config) if state.is_done(st, s)]


def test_run_all_stops_at_human_step_and_resumes(colorimeter_job, fake_argyll):
    job_dir, config = colorimeter_job
    result = stages.run_all(job_dir, config)
    assert result["stopped_at"] == "import-measurements"
    assert result["ran"] == []

    _confirm_lab(job_dir, config)
    csv_path = write_csv_for_chart(job_dir, config)
    stages.bind_input(job_dir, config, "colorimeter_csv")
    _set_csv(job_dir, config, csv_path)
    result = stages.run_all(job_dir, config)
    assert result == {"ran": ["import-measurements", "build-profile", "verify"], "stopped_at": None, "message": "All stages complete."}
    assert stages.icc_path(job_dir, config).read_bytes() == b"fake icc"


def test_rerunning_a_stage_clears_everything_after_it(colorimeter_job, fake_argyll):
    job_dir, config = colorimeter_job
    _confirm_lab(job_dir, config)
    _set_csv(job_dir, config, write_csv_for_chart(job_dir, config))
    stages.bind_input(job_dir, config, "colorimeter_csv")
    stages.run_all(job_dir, config)
    assert _done(job_dir, config) == stages.order_for(config)

    stages.run_stage(job_dir, config, "import-measurements")
    assert _done(job_dir, config) == ["generate-chart", "import-measurements"]


def test_csv_from_an_older_chart_is_refused(colorimeter_job, fake_argyll):
    job_dir, config = colorimeter_job
    _confirm_lab(job_dir, config)
    _set_csv(job_dir, config, write_csv_for_chart(job_dir, config))
    stages.bind_input(job_dir, config, "colorimeter_csv")
    stages.run_stage(job_dir, config, "generate-chart")  # reshuffles the patches
    with pytest.raises(PipelineError, match="earlier version of the chart"):
        stages.run_stage(job_dir, config, "import-measurements")


def test_unbound_csv_falls_back_to_file_times(colorimeter_job, fake_argyll):
    job_dir, config = colorimeter_job
    _confirm_lab(job_dir, config)
    csv_path = write_csv_for_chart(job_dir, config)
    _set_csv(job_dir, config, csv_path)
    old = time.time() - 3600
    os.utime(csv_path, (old, old))
    with pytest.raises(PipelineError, match="earlier version of the chart"):
        stages.run_stage(job_dir, config, "import-measurements")
    os.utime(csv_path, None)
    stages.run_stage(job_dir, config, "import-measurements")


def test_import_requires_confirmed_lab_reference(colorimeter_job, fake_argyll):
    job_dir, config = colorimeter_job
    _set_csv(job_dir, config, write_csv_for_chart(job_dir, config))
    stages.bind_input(job_dir, config, "colorimeter_csv")
    with pytest.raises(PipelineError, match="D50"):
        stages.run_stage(job_dir, config, "import-measurements")


def test_force_keeps_the_chart_and_regenerate_does_not(colorimeter_job, fake_argyll):
    job_dir, config = colorimeter_job
    _confirm_lab(job_dir, config)
    _set_csv(job_dir, config, write_csv_for_chart(job_dir, config))
    stages.bind_input(job_dir, config, "colorimeter_csv")
    stages.run_all(job_dir, config)
    chart_before = stages.chart_id(job_dir, config)

    result = stages.run_all(job_dir, config, force=True)
    assert result["ran"] == ["import-measurements", "build-profile", "verify"]
    assert stages.chart_id(job_dir, config) == chart_before

    result = stages.run_all(job_dir, config, regenerate_chart=True)
    assert stages.chart_id(job_dir, config) != chart_before
    assert result["ran"] == ["generate-chart"]
    assert result["stopped_at"] == "import-measurements"  # old CSV no longer matches
    with pytest.raises(PipelineError, match="earlier version"):
        stages.run_stage(job_dir, config, "import-measurements")


def test_scanner_path_order_and_scan_binding(jobs_root, fake_argyll, tmp_path):
    config = make_config("scan1", method="scanner")
    job_dir = stages.init_job(jobs_root, config)
    for name in ("it8.tif", "it8.cie", "it8.cht"):
        (tmp_path / name).write_text("x")
    m = config.measurement.scanner
    m.it8_reference_scan = str(tmp_path / "it8.tif")
    m.it8_cgats_reference_file = str(tmp_path / "it8.cie")
    m.it8_cht_file = str(tmp_path / "it8.cht")
    config.save(job_dir / "config.json")

    result = stages.run_all(job_dir, config)
    assert result["ran"] == ["generate-chart", "build-scanner-profile"]
    assert result["stopped_at"] == "read-chart"

    scan = stages.measurements_in_dir(job_dir) / "test_chart_scan.tif"
    scan.write_text("scan")
    stages.bind_input(job_dir, config, "test_chart_scan")
    m.test_chart_scan = str(scan)
    result = stages.run_all(job_dir, config)
    assert result["ran"] == ["read-chart", "build-profile", "verify"]
    assert "-qm" in next(c for c in fake_argyll.calls if c[0] == "colprof" and "-as" in c)


def test_cmyk_ink_limits_reach_targen_and_colprof(jobs_root, fake_argyll):
    config = make_config("cmyk1", colorspace="CMYK")
    config.chart.ink_limit = 300
    config.output.black_ink_limit = 90
    job_dir = stages.init_job(jobs_root, config)
    fake_argyll.ti1_source = fake_argyll.ti1_source.with_name("rgb20.ti1")
    stages.run_stage(job_dir, config, "generate-chart")
    targen = next(c for c in fake_argyll.calls if c[0] == "targen")
    assert "-l299.9" in targen  # exact 300 hangs targen 3.1.0


def test_job_lock_refuses_a_second_run(colorimeter_job, fake_argyll):
    job_dir, config = colorimeter_job
    entered, release = threading.Event(), threading.Event()

    def hold():
        with joblock.job_lock(job_dir):
            entered.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    entered.wait(5)
    try:
        with pytest.raises(PipelineError, match="already running"):
            stages.run_stage(job_dir, config, "generate-chart")
    finally:
        release.set()
        t.join()
    stages.run_stage(job_dir, config, "generate-chart")


def test_unknown_stage_rejected(colorimeter_job):
    job_dir, config = colorimeter_job
    with pytest.raises(PipelineError):
        stages.run_stage(job_dir, config, "read-chart")  # scanner-only


def test_chart_binding_file_written(colorimeter_job):
    job_dir, config = colorimeter_job
    stages.bind_input(job_dir, config, "colorimeter_csv")
    data = json.loads((stages.measurements_in_dir(job_dir) / "chart_binding.json").read_text())
    assert data == {"colorimeter_csv": stages.chart_id(job_dir, config)}
