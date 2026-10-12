"""Pipeline stage implementations, shared by the CLI and the web UI.

Each stage function: validates its preconditions (raising PipelineError with
an actionable message if something required is missing), builds an Argyll
command line, runs it via argyll_tools.run (which logs output and raises on
failure), confirms the expected output file actually appeared, and marks the
stage done in state.json.

All ArgyllCMS command lines below were validated against a real, installed
ArgyllCMS 3.1.0 (WSL/Ubuntu `apt install argyll`), including running the full
chart-generation -> scanner-characterization -> profile-build -> verify chain
end to end with synthetic data, plus a separate end-to-end run of the
colorimeter-CSV path. Flags/behavior notes below record what was actually
confirmed, including a few surprises relative to the original plan (see each
stage's comments). A small number of things remain unconfirmed because they
need real printer/scanner/colorimeter hardware to observe (noted inline).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from icc_profiler import argyll_tools, csv_import, state, target_colors
from icc_profiler.config import JobConfig
from icc_profiler.errors import PipelineError

# Confirmed against real `targen -?` (3.1.0): these are targen's "-d
# col_comb" codes for an ink-limited *print* device, as opposed to the
# "Video"-prefixed codes meant for displays.
_DEVCLASS_BY_COLORSPACE = {"GRAY": "0", "RGB": "2", "CMYK": "4"}
_QUALITY_LETTER = {"low": "l", "medium": "m", "high": "h", "ultra": "u"}


# -- directory layout --------------------------------------------------------


def chart_dir(job_dir: Path) -> Path:
    return job_dir / "01_chart"


def measurements_in_dir(job_dir: Path) -> Path:
    return job_dir / "02_measurements"


def scanner_profile_dir(job_dir: Path) -> Path:
    return job_dir / "03_scanner_profile"


def ti3_dir(job_dir: Path) -> Path:
    return job_dir / "04_measurements"


def profile_dir(job_dir: Path) -> Path:
    return job_dir / "05_profile"


def logs_dir(job_dir: Path) -> Path:
    return job_dir / "logs"


def chart_basename(job_dir: Path, config: JobConfig) -> Path:
    return chart_dir(job_dir) / config.job_name


def ti2_path(job_dir: Path, config: JobConfig) -> Path:
    return chart_basename(job_dir, config).with_suffix(".ti2")


def cht_path(job_dir: Path, config: JobConfig) -> Path:
    return chart_basename(job_dir, config).with_suffix(".cht")


def ti3_path(job_dir: Path, config: JobConfig) -> Path:
    return ti3_dir(job_dir) / f"{config.job_name}.ti3"


def scanner_profile_basename(job_dir: Path, config: JobConfig) -> Path:
    return scanner_profile_dir(job_dir) / f"{config.job_name}_scanner"


def icc_path(job_dir: Path, config: JobConfig) -> Path:
    return profile_dir(job_dir) / f"{config.job_name}.icc"


def _bin(tool: str, config: JobConfig) -> str:
    return argyll_tools.find_bin(tool, bin_dir=config.argyll.bin_dir)


def _require_file(path: Path, what: str) -> None:
    # Deliberately path.is_file(), not path.exists(): an unset config field
    # (empty string) becomes Path("") -> ".", which *exists* as the current
    # directory and was confirmed to slip past an exists()-only check,
    # reaching scanin as a literal "." argument instead of failing here with
    # a clear message.
    if not path.is_file() or path.stat().st_size == 0:
        raise PipelineError(f"{what} not found or empty: {path}")


def _require_set(value: str, what: str) -> None:
    if not value or not value.strip():
        raise PipelineError(
            f"{what} is not set for this job yet -- upload/configure it on the job page first."
        )


def _require_done(job_dir: Path, stage: str) -> None:
    st = state.load_state(job_dir)
    if not state.is_done(st, stage):
        raise PipelineError(f"Stage '{stage}' must complete before this stage can run")


# -- init ---------------------------------------------------------------------


def init_job(jobs_root: Path, config: JobConfig) -> Path:
    config.validate()
    job_dir = jobs_root / config.job_name
    for sub in (chart_dir, measurements_in_dir, scanner_profile_dir, ti3_dir, profile_dir, logs_dir):
        sub(job_dir).mkdir(parents=True, exist_ok=True)
    config.save(job_dir / "config.json")
    return job_dir


# -- stage 1: generate-chart (both methods) -----------------------------------


def generate_chart(job_dir: Path, config: JobConfig) -> None:
    chart_dir(job_dir).mkdir(parents=True, exist_ok=True)
    basename = chart_basename(job_dir, config)
    log = logs_dir(job_dir) / "generate-chart.log"

    devclass = _DEVCLASS_BY_COLORSPACE.get(config.chart.colorspace)
    if devclass is None:
        raise PipelineError(f"No targen device-class mapping for colorspace {config.chart.colorspace!r}")

    targen_argv = [
        _bin("targen", config),
        "-v",
        f"-d{devclass}",
        f"-f{config.chart.patch_count}",
        f"-g{config.chart.grey_steps}",
        "-G",
        *config.chart.extra_targen_args,
        str(basename),
    ]
    argyll_tools.run(targen_argv, log_file=log, cwd=chart_dir(job_dir))
    ti1 = basename.with_suffix(".ti1")
    _require_file(ti1, "targen output (.ti1)")

    colors = target_colors.load(job_dir)
    if colors:
        target_colors.merge_into_ti1(ti1, colors, config.chart.colorspace)

    printtarg_argv = [
        _bin("printtarg", config),
        "-v",
        f"-i{config.chart.reader_type}",
        "-s",  # generate the .cht chart-recognition file (confirmed required -- omitting it produces no .cht)
        f"-t{config.chart.raster_dpi}",  # 8-bit TIFF output (confirmed required -- default output is PostScript, not a raster image)
        f"-p{config.chart.page_size}",
        *config.chart.extra_printtarg_args,
        str(basename),
    ]
    argyll_tools.run(printtarg_argv, log_file=log, cwd=chart_dir(job_dir))
    _require_file(ti2_path(job_dir, config), "printtarg output (.ti2)")
    _require_file(cht_path(job_dir, config), "printtarg chart-recognition template (.cht)")

    state.mark_done(job_dir, "generate-chart")


# -- stage 2a: build-scanner-profile (scanner method only) --------------------


def build_scanner_profile(job_dir: Path, config: JobConfig) -> None:
    """Two real sub-steps, confirmed end-to-end against ArgyllCMS 3.1.0:

    1. `scanin input.tif recogin.cht valin.cie -O out.ti3` reads the IT8
       target scan against its vendor reference data, producing a
       characterization .ti3 (scanned RGB vs known XYZ per patch). -O must
       come BEFORE the positional arguments (confirmed -- after them it's
       silently misparsed and scanin dumps its usage text instead of erroring).
    2. `colprof -as <basename>` builds an actual input/scanner ICC profile
       from that .ti3 ("-as" = shaper+matrix, confirmed valid; suited to an
       input device profile, as opposed to the default Lab cLUT used for the
       final printer profile in build_profile).
    """
    _require_done(job_dir, "generate-chart")
    m = config.measurement.scanner
    _require_set(m.it8_reference_scan, "IT8 reference target scan")
    _require_set(m.it8_cht_file, "IT8 chart-recognition template (.cht)")
    _require_set(m.it8_cgats_reference_file, "IT8 vendor CGATS reference file")
    it8_scan = Path(m.it8_reference_scan)
    it8_cht = Path(m.it8_cht_file)
    it8_ref = Path(m.it8_cgats_reference_file)
    _require_file(it8_scan, "IT8 reference target scan")
    _require_file(it8_cht, "IT8 chart-recognition template (.cht)")
    _require_file(it8_ref, "IT8 vendor CGATS reference file")

    out_dir = scanner_profile_dir(job_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    basename = scanner_profile_basename(job_dir, config)
    char_ti3 = basename.with_suffix(".ti3")
    log = logs_dir(job_dir) / "build-scanner-profile.log"

    read_argv = [
        _bin("scanin", config),
        "-v",
        "-O",
        str(char_ti3),
        str(it8_scan),
        str(it8_cht),
        str(it8_ref),
    ]
    argyll_tools.run(read_argv, log_file=log, cwd=out_dir)
    _require_file(char_ti3, "scanner characterization output (.ti3)")

    profile_argv = [
        _bin("colprof", config),
        "-v",
        "-qm",
        "-as",
        f"-D{config.job_name} scanner correction",
        str(basename),
    ]
    argyll_tools.run(profile_argv, log_file=log, cwd=out_dir)
    _require_file(basename.with_suffix(".icc"), "scanner correction profile (.icc)")

    state.mark_done(job_dir, "build-scanner-profile")


# -- stage 2a cont'd: read-chart (scanner method only) -------------------------


def read_chart(job_dir: Path, config: JobConfig) -> None:
    """`scanin -c input.tif recogin.cht scanprofile.icc pbase` reads the
    scanned printed chart, applies the scanner correction profile, and
    writes pbase.ti3 (preserving each patch's original SAMPLE_ID -- e.g. a
    custom target color's name -- confirmed against real output). Unlike the
    plain mode used in build_scanner_profile, -O was confirmed NOT to
    redirect -c mode's output (it kept writing next to pbase regardless), so
    this lets it write naturally next to the chart's .ti2 and then copies the
    result into 04_measurements/ instead of fighting that.
    """
    _require_done(job_dir, "build-scanner-profile")
    m = config.measurement.scanner
    _require_set(m.test_chart_scan, "test chart scan")
    test_scan = Path(m.test_chart_scan)
    _require_file(test_scan, "test chart scan")
    _require_file(cht_path(job_dir, config), ".cht from generate-chart")
    _require_file(ti2_path(job_dir, config), ".ti2 from generate-chart")
    scanner_icc = scanner_profile_basename(job_dir, config).with_suffix(".icc")
    _require_file(scanner_icc, "scanner correction profile from build-scanner-profile")

    log = logs_dir(job_dir) / "read-chart.log"
    pbase = chart_basename(job_dir, config)
    argv = [
        _bin("scanin", config),
        "-v",
        "-c",
        str(test_scan),
        str(cht_path(job_dir, config)),
        str(scanner_icc),
        str(pbase),
    ]
    argyll_tools.run(argv, log_file=log, cwd=chart_dir(job_dir))
    produced_ti3 = pbase.with_suffix(".ti3")
    _require_file(produced_ti3, "scanin measurement output (.ti3)")

    ti3_dir(job_dir).mkdir(parents=True, exist_ok=True)
    out_ti3 = ti3_path(job_dir, config)
    shutil.copyfile(produced_ti3, out_ti3)
    state.mark_done(job_dir, "read-chart")


# -- stage 2b: import-measurements (colorimeter_csv method only) --------------


def import_measurements(job_dir: Path, config: JobConfig) -> None:
    _require_done(job_dir, "generate-chart")
    m = config.measurement.colorimeter_csv
    _require_set(m.csv_file, "colorimeter CSV export")
    csv_path = Path(m.csv_file)
    _require_file(csv_path, "colorimeter CSV export")
    _require_file(ti2_path(job_dir, config), ".ti2 from generate-chart")

    table = csv_import.merge(ti2_path(job_dir, config), csv_path, m.column_map)

    ti3_dir(job_dir).mkdir(parents=True, exist_ok=True)
    out_ti3 = ti3_path(job_dir, config)
    table.write(out_ti3)
    _require_file(out_ti3, "imported measurement output (.ti3)")
    state.mark_done(job_dir, "import-measurements")


# -- stage 3: build-profile (both methods) -------------------------------------


def build_profile(job_dir: Path, config: JobConfig) -> None:
    prior_stage = "read-chart" if config.measurement.method == "scanner" else "import-measurements"
    _require_done(job_dir, prior_stage)
    src_ti3 = ti3_path(job_dir, config)
    _require_file(src_ti3, ".ti3 measurement file")

    profile_dir(job_dir).mkdir(parents=True, exist_ok=True)
    log = logs_dir(job_dir) / "build-profile.log"

    quality = _QUALITY_LETTER.get(config.output.quality, "m")
    argv = [
        _bin("colprof", config),
        "-v",
        f"-q{quality}",
    ]
    # "auto" omits -a entirely, which defaults to "l" (Lab cLUT) -- confirmed
    # against `colprof -?` and real runs; that default is the right choice
    # for a printer output profile, so "auto" is the normal case.
    if config.output.algorithm and config.output.algorithm != "auto":
        argv.append(f"-a{config.output.algorithm}")
    if config.output.profile_description:
        argv.append(f"-D{config.output.profile_description}")
    if config.output.copyright:
        argv.append(f"-C{config.output.copyright}")
    argv.extend(config.output.extra_colprof_args)
    argv.append(str(src_ti3.with_suffix("")))

    argyll_tools.run(argv, log_file=log, cwd=ti3_dir(job_dir))

    produced_icc = src_ti3.with_suffix(".icc")
    _require_file(produced_icc, "colprof output (.icc)")
    final_icc = icc_path(job_dir, config)
    shutil.copyfile(produced_icc, final_icc)
    state.mark_done(job_dir, "build-profile")


# -- stage 4: verify (both methods, optional) ----------------------------------


def verify(job_dir: Path, config: JobConfig) -> str:
    _require_done(job_dir, "build-profile")
    src_ti3 = ti3_path(job_dir, config)
    icc = icc_path(job_dir, config)
    _require_file(src_ti3, ".ti3 measurement file")
    _require_file(icc, "built .icc profile")

    log = logs_dir(job_dir) / "verify.log"
    # -v2 confirmed against real profcheck 3.1.0: prints a per-patch delta-E
    # line plus a final "errors: max. = .., avg. = .., RMS = .." summary.
    argv = [_bin("profcheck", config), "-v2", str(src_ti3), str(icc)]
    proc = argyll_tools.run(argv, log_file=log, cwd=job_dir)

    report_path = profile_dir(job_dir) / f"{config.job_name}_verify.txt"
    report_path.write_text((proc.stdout or "") + (proc.stderr or ""))
    state.mark_done(job_dir, "verify")
    return str(report_path)


# -- orchestration --------------------------------------------------------------

STAGE_FUNCS = {
    "generate-chart": generate_chart,
    "build-scanner-profile": build_scanner_profile,
    "read-chart": read_chart,
    "import-measurements": import_measurements,
    "build-profile": build_profile,
    "verify": verify,
}

# What has to be true before a stage can run, beyond "the previous stage is
# done" -- i.e. the human step's output must be in place. Used by run_all to
# stop cleanly and tell the user what to do next, instead of failing.
_HUMAN_INPUT_READY = {
    "build-scanner-profile": lambda c: bool(c.measurement.scanner.it8_reference_scan),
    "read-chart": lambda c: bool(c.measurement.scanner.test_chart_scan),
    "import-measurements": lambda c: bool(c.measurement.colorimeter_csv.csv_file),
}

_HUMAN_INSTRUCTIONS = {
    "build-scanner-profile": (
        "Print the chart in 01_chart/ at 100% scale with no color management. "
        "Scan your physical IT8.7 reference target and set measurement.scanner."
        "it8_reference_scan (plus it8_cgats_reference_file / it8_cht_file) in the job config."
    ),
    "read-chart": (
        "Scan the printed test chart (same scanner settings/session as the IT8 scan) "
        "and set measurement.scanner.test_chart_scan in the job config."
    ),
    "import-measurements": (
        "Print the chart in 01_chart/ at 100% scale. Measure each patch with the "
        "colorimeter, naming each reading to match the strip/row label printed next to "
        "it on the chart (e.g. 'A14') -- NOT the internal patch name -- export as CSV, "
        "and set measurement.colorimeter_csv.csv_file in the job config."
    ),
}


def run_all(job_dir: Path, config: JobConfig, force: bool = False) -> dict:
    stages = state.stage_order(config.measurement.method)
    if force:
        for stage in stages:
            state.mark_pending(job_dir, stage)

    ran: list[str] = []
    for stage in stages:
        st = state.load_state(job_dir)
        if state.is_done(st, stage):
            continue
        ready_check = _HUMAN_INPUT_READY.get(stage)
        if ready_check is not None and not ready_check(config):
            return {
                "ran": ran,
                "stopped_at": stage,
                "message": _HUMAN_INSTRUCTIONS[stage],
            }
        STAGE_FUNCS[stage](job_dir, config)
        ran.append(stage)

    return {"ran": ran, "stopped_at": None, "message": "All stages complete."}
