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

import hashlib
import json
import shutil
from pathlib import Path

from icc_profiler import argyll_tools, cgats, cht_geometry, csv_import, joblock, state, target_colors
from icc_profiler.config import LAB_REFERENCES, JobConfig
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


def extra_patches_path(job_dir: Path, config: JobConfig) -> Path:
    """Readings added on top of the chart, e.g. from a spot color proof sheet."""
    return ti3_dir(job_dir) / f"{config.job_name}_extra.ti3"


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


def order_for(config: JobConfig) -> list[str]:
    return state.stage_order(config.measurement.method)


def load_state(job_dir: Path, config: JobConfig) -> dict:
    """state.json with untrustworthy (stale) "done" markers dropped."""
    return state.load_state(job_dir, order_for(config))


def _require_done(job_dir: Path, config: JobConfig, stage: str) -> None:
    if not state.is_done(load_state(job_dir, config), stage):
        raise PipelineError(f"Stage '{stage}' must complete before this stage can run")


def _finish(job_dir: Path, config: JobConfig, stage: str) -> None:
    """Mark a stage done and clear every stage after it -- their outputs were
    built from this stage's previous output and are now out of date."""
    state.mark_done(job_dir, stage, order_for(config))


def invalidate_from(job_dir: Path, config: JobConfig, stage: str) -> list[str]:
    """Mark `stage` and everything after it pending because an input changed."""
    return state.invalidate_from(job_dir, stage, order_for(config))


# -- tying measurements to the chart they were taken from ---------------------
#
# Every generate-chart run re-randomizes which patch lands on which printed
# label (A1, A2, ...), so a CSV or a scan of an *older* print still matches
# every label and would import "successfully" into garbage. Each
# measurement input is therefore recorded with a fingerprint of the .ti2 it
# was measured against, and the stages that consume it refuse a mismatch.

BINDING_FILENAME = "chart_binding.json"


def chart_id(job_dir: Path, config: JobConfig) -> str | None:
    """Fingerprint of the current chart (.ti2), or None if there isn't one."""
    path = ti2_path(job_dir, config)
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _binding_path(job_dir: Path) -> Path:
    return measurements_in_dir(job_dir) / BINDING_FILENAME


def _load_bindings(job_dir: Path) -> dict:
    path = _binding_path(job_dir)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def bind_input(job_dir: Path, config: JobConfig, kind: str) -> None:
    """Record that the measurement input `kind` ("test_chart_scan" or
    "colorimeter_csv") was supplied for the chart as it is right now."""
    cid = chart_id(job_dir, config)
    if cid is None:
        raise PipelineError("Generate the chart first -- measurements must belong to a chart.")
    bindings = _load_bindings(job_dir)
    bindings[kind] = cid
    path = _binding_path(job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(bindings, indent=2))


def input_matches_chart(job_dir: Path, config: JobConfig, kind: str, input_path: Path) -> bool:
    """True if the measurement input was recorded against the current chart.

    Inputs set without a recorded binding (e.g. a path typed into the
    config by hand for the CLI) fall back to file times: the input must be
    newer than the chart.
    """
    cid = chart_id(job_dir, config)
    if cid is None:
        return False
    bound = _load_bindings(job_dir).get(kind)
    if bound is not None:
        return bound == cid
    try:
        return input_path.stat().st_mtime >= ti2_path(job_dir, config).stat().st_mtime
    except OSError:
        return False


def _require_matching_chart(job_dir: Path, config: JobConfig, kind: str, input_path: Path, what: str) -> None:
    if not input_matches_chart(job_dir, config, kind, input_path):
        raise PipelineError(
            f"The {what} was made for an earlier version of the chart. The chart was "
            f"regenerated since then, which reshuffles which color sits under each printed "
            f"label, so these measurements no longer line up. Print the current chart, "
            f"measure it again, and upload the new {what}."
        )


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
        *targen_ink_args(config),
        *config.chart.extra_targen_args,
        str(basename),
    ]
    argyll_tools.run(targen_argv, log_file=log, cwd=chart_dir(job_dir))
    ti1 = basename.with_suffix(".ti1")
    _require_file(ti1, "targen output (.ti1)")

    colors = target_colors.load(job_dir)
    if colors:
        target_colors.merge_into_ti1(ti1, colors, config.chart.colorspace)

    print_chart(config, basename, log)
    _finish(job_dir, config, "generate-chart")


def targen_ink_args(config: JobConfig) -> list[str]:
    if config.chart.colorspace != "CMYK" or not config.chart.ink_limit:
        return []
    # Confirmed against real targen 3.1.0: "-l300" exactly makes the
    # point-placement step spin for many minutes (60 patches, -d4, with
    # or without -G), while -l299.9, -l301 and every other value tried
    # (100-400) finish in about 1.5 s. A 0.1% nudge is far below what a
    # printer can reproduce, so sidestep the hang.
    limit = config.chart.ink_limit
    return [f"-l{299.9 if limit == 300 else limit:g}"]


def print_chart(config: JobConfig, basename: Path, log: Path, extra: list[str] = ()) -> None:
    """printtarg <basename>.ti1 -> .ti2, .cht and the printable TIFF page(s),
    scaled up to config.chart.min_patch_size_mm if set. Shared by the main
    chart and the check chart (check_chart.py)."""

    def _printtarg_argv(scale_args: list[str]) -> list[str]:
        return [
            _bin("printtarg", config),
            "-v",
            f"-i{config.chart.reader_type}",
            "-s",  # generate the .cht chart-recognition file (confirmed required -- omitting it produces no .cht)
            f"-t{config.chart.raster_dpi}",  # 8-bit TIFF output (confirmed required -- default output is PostScript, not a raster image)
            f"-p{config.chart.page_size}",
            *config.chart.extra_printtarg_args,
            *extra,
            *scale_args,
            str(basename),
        ]

    def _clear_pages() -> None:
        # printtarg names one page <base>.tif and several <base>_01.tif,
        # <base>_02.tif, ... A run with fewer pages than the last one doesn't
        # remove the extra old pages, which would then be printed with the
        # new chart. They are printtarg's output, regenerated on every run.
        # A multi-page chart also has no <base>.cht, only <base>_01.cht, ...,
        # so an old single-page one must go too.
        singles = [basename.with_suffix(".tif"), basename.with_suffix(".cht")]
        for old in [*singles, *basename.parent.glob(f"{basename.name}_[0-9][0-9].*")]:
            if old.suffix in (".tif", ".cht"):
                old.unlink(missing_ok=True)

    def _first_cht() -> Path:
        single = basename.with_suffix(".cht")
        cht = single if single.is_file() else basename.with_name(f"{basename.name}_01.cht")
        _require_file(cht, "printtarg chart-recognition template (.cht)")
        return cht

    ti2 = basename.with_suffix(".ti2")
    _clear_pages()
    argyll_tools.run(_printtarg_argv([]), log_file=log, cwd=basename.parent)
    _require_file(ti2, "printtarg output (.ti2)")
    cht = _first_cht()

    if config.chart.min_patch_size_mm:
        # Confirmed empirically: printtarg's -a scales patch size uniformly
        # relative to its natural layout for this patch count/page size;
        # re-running with the computed scale reliably hits the target size
        # (e.g. natural 7.0x7.0mm patches -> -a1.42857 -> 9.99999x9.99999mm).
        width_mm, height_mm = cht_geometry.read_patch_size_mm(cht)
        smallest = min(width_mm, height_mm)
        if smallest < config.chart.min_patch_size_mm:
            scale = config.chart.min_patch_size_mm / smallest
            _clear_pages()
            argyll_tools.run(_printtarg_argv([f"-a{scale:.6f}"]), log_file=log, cwd=basename.parent)
            _require_file(ti2, "printtarg output (.ti2)")
            _first_cht()


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
    _require_done(job_dir, config, "generate-chart")
    m = config.measurement.scanner
    _require_set(m.it8_reference_scan, "IT8 reference target scan")
    _require_set(m.it8_cht_file, "IT8 chart-recognition template (.cht)")
    _require_set(m.it8_cgats_reference_file, "IT8 vendor CGATS reference file")
    # Resolved to absolute paths (see config.resolve_path) -- the Argyll
    # subprocess below runs with cwd=out_dir, so a relative path would
    # resolve against the wrong directory.
    it8_scan = config.input_path(m.it8_reference_scan, job_dir)
    it8_cht = config.input_path(m.it8_cht_file, job_dir)
    it8_ref = config.input_path(m.it8_cgats_reference_file, job_dir)
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
        f"-q{_QUALITY_LETTER.get(m.profile_quality, 'm')}",
        "-as",
        f"-D{config.job_name} scanner correction",
        str(basename),
    ]
    argyll_tools.run(profile_argv, log_file=log, cwd=out_dir)
    _require_file(basename.with_suffix(".icc"), "scanner correction profile (.icc)")

    _finish(job_dir, config, "build-scanner-profile")


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
    _require_done(job_dir, config, "build-scanner-profile")
    m = config.measurement.scanner
    _require_set(m.test_chart_scan, "test chart scan")
    test_scan = config.input_path(m.test_chart_scan, job_dir)
    _require_file(test_scan, "test chart scan")
    _require_matching_chart(job_dir, config, "test_chart_scan", test_scan, "test chart scan")
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
    _finish(job_dir, config, "read-chart")


# -- stage 2b: import-measurements (colorimeter_csv method only) --------------


def import_measurements(job_dir: Path, config: JobConfig) -> None:
    _require_done(job_dir, config, "generate-chart")
    m = config.measurement.colorimeter_csv
    if m.lab_reference not in LAB_REFERENCES:
        raise PipelineError(
            "Confirm which illuminant/observer your colorimeter measured in before importing. "
            "ICC profiles need L*a*b* relative to D50 / 2\u00b0, but many handheld colorimeters "
            "(e.g. 3nh PS110, CHNSpec DS-173) default to D65 / 10\u00b0. Set the device to D50 / 2\u00b0 "
            "(or D65 / 2\u00b0, which this tool converts), then pick that setting on the job page "
            "(measurement.colorimeter_csv.lab_reference in the config)."
        )
    _require_set(m.csv_file, "colorimeter CSV export")
    csv_path = config.input_path(m.csv_file, job_dir)
    _require_file(csv_path, "colorimeter CSV export")
    _require_file(ti2_path(job_dir, config), ".ti2 from generate-chart")
    _require_matching_chart(job_dir, config, "colorimeter_csv", csv_path, "colorimeter CSV")

    table = csv_import.merge(
        ti2_path(job_dir, config),
        csv_path,
        m.column_map,
        lab_reference=m.lab_reference,
        match_by=m.match_by,
        specular=m.specular,
        ignore_prefixes=m.ignore_name_prefixes,
    )

    ti3_dir(job_dir).mkdir(parents=True, exist_ok=True)
    out_ti3 = ti3_path(job_dir, config)
    table.write(out_ti3)
    _require_file(out_ti3, "imported measurement output (.ti3)")
    _finish(job_dir, config, "import-measurements")


# -- stage 3: build-profile (both methods) -------------------------------------


def build_profile(job_dir: Path, config: JobConfig) -> None:
    prior_stage = "read-chart" if config.measurement.method == "scanner" else "import-measurements"
    _require_done(job_dir, config, prior_stage)
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
    if config.chart.colorspace == "CMYK":
        if config.chart.ink_limit:
            argv.append(f"-l{config.chart.ink_limit:g}")
        if config.output.black_ink_limit:
            argv.append(f"-L{config.output.black_ink_limit:g}")
    argv.extend(config.output.extra_colprof_args)
    profile_ti3 = _with_extra_patches(job_dir, config, src_ti3)
    argv.append(str(profile_ti3.with_suffix("")))

    # colprof at high/ultra quality can legitimately take many minutes (it
    # runs in the background in the web UI), so allow up to an hour.
    argyll_tools.run(argv, log_file=log, cwd=ti3_dir(job_dir), timeout=3600)

    produced_icc = profile_ti3.with_suffix(".icc")
    _require_file(produced_icc, "colprof output (.icc)")
    final_icc = icc_path(job_dir, config)
    shutil.copyfile(produced_icc, final_icc)
    _finish(job_dir, config, "build-profile")


def _with_extra_patches(job_dir: Path, config: JobConfig, src_ti3: Path) -> Path:
    """The .ti3 colprof should read: the chart readings alone, or with the
    extra patches appended (written to <job>_plus.ti3, so the chart's own
    .ti3 stays exactly what was imported)."""
    extra_path = extra_patches_path(job_dir, config)
    plus = src_ti3.with_name(f"{config.job_name}_plus.ti3")
    if not extra_path.is_file():
        plus.unlink(missing_ok=True)
        return src_ti3
    main = cgats.read(src_ti3)
    extra = cgats.read(extra_path)
    missing = [f for f in main.fields if f not in extra.fields]
    if missing:
        raise PipelineError(
            f"The extra patches in {extra_path.name} don't have the chart's fields {missing}. "
            "Remove them on the Spot colors page and add them again."
        )
    main.rows = [*main.rows, *({f: r[f] for f in main.fields} for r in extra.rows)]
    main.write(plus)
    return plus


# -- stage 4: verify (both methods, optional) ----------------------------------


def verify(job_dir: Path, config: JobConfig) -> str:
    _require_done(job_dir, config, "build-profile")
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
    _finish(job_dir, config, "verify")
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
# A measurement input from an older chart counts as "not ready": the user
# has to print and measure the current chart.
_HUMAN_INPUT_READY = {
    "build-scanner-profile": lambda jd, c: bool(c.measurement.scanner.it8_reference_scan),
    "read-chart": lambda jd, c: bool(c.measurement.scanner.test_chart_scan)
    and input_matches_chart(
        jd, c, "test_chart_scan", c.input_path(c.measurement.scanner.test_chart_scan, jd)
    ),
    "import-measurements": lambda jd, c: bool(c.measurement.colorimeter_csv.csv_file)
    and input_matches_chart(
        jd, c, "colorimeter_csv", c.input_path(c.measurement.colorimeter_csv.csv_file, jd)
    ),
}

_HUMAN_INSTRUCTIONS = {
    "build-scanner-profile": (
        "Print the chart in 01_chart/ at 100% scale with no color management. "
        "Scan your physical IT8.7 reference target and set measurement.scanner."
        "it8_reference_scan (plus it8_cgats_reference_file / it8_cht_file) in the job config."
    ),
    "read-chart": (
        "Scan the printed test chart (same scanner settings/session as the IT8 scan) "
        "and set measurement.scanner.test_chart_scan in the job config. A scan made "
        "before the chart was last regenerated doesn't count."
    ),
    "import-measurements": (
        "Print the chart in 01_chart/ at 100% scale. Measure each patch with the "
        "colorimeter, naming each reading to match the strip/row label printed next to "
        "it on the chart (e.g. 'A14') -- NOT the internal patch name -- export as CSV, "
        "and set measurement.colorimeter_csv.csv_file in the job config. Measure in "
        "D50 / 2\u00b0 and confirm that on the job page. A CSV made before the chart was "
        "last regenerated doesn't count."
    ),
}


def run_stage(job_dir: Path, config: JobConfig, stage: str):
    """Run one stage under the job's lock (so two runs never touch the same
    files at once). Returns whatever the stage function returns."""
    func = STAGE_FUNCS.get(stage)
    if func is None or stage not in order_for(config):
        raise PipelineError(f"Unknown stage for a {config.measurement.method} job: {stage}")
    with joblock.job_lock(job_dir):
        return func(job_dir, config)


def run_all(job_dir: Path, config: JobConfig, force: bool = False, regenerate_chart: bool = False) -> dict:
    """Run every remaining stage, stopping cleanly at a human step.

    force re-runs every stage *after* the chart: re-reading/importing the
    measurements, rebuilding and re-verifying the profile. It deliberately
    keeps the chart, because regenerating it reshuffles the patches and
    invalidates anything already printed and measured. regenerate_chart
    starts from scratch, chart included.
    """
    stages = order_for(config)
    with joblock.job_lock(job_dir):
        if regenerate_chart:
            state.invalidate_from(job_dir, stages[0], stages)
        elif force:
            state.invalidate_from(job_dir, stages[1], stages)

        ran: list[str] = []
        for stage in stages:
            st = load_state(job_dir, config)
            if state.is_done(st, stage):
                continue
            ready_check = _HUMAN_INPUT_READY.get(stage)
            if ready_check is not None and not ready_check(job_dir, config):
                return {
                    "ran": ran,
                    "stopped_at": stage,
                    "message": _HUMAN_INSTRUCTIONS[stage],
                }
            STAGE_FUNCS[stage](job_dir, config)
            ran.append(stage)

    return {"ran": ran, "stopped_at": None, "message": "All stages complete."}
