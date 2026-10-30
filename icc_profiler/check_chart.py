"""Check chart: an independent accuracy test of a finished profile.

`verify` runs profcheck against the very patches the profile was built
from, so it only shows how well colprof fitted its own training data. A
check chart is a second, smaller chart with *different* patches, printed
and measured the same way. Comparing the profile's prediction with those
fresh readings gives a real-world error figure.

Colorimeter jobs only: the readings are imported with the job's own
colorimeter settings (column map, match_by, SCI/SCE, illuminant).

Files live in 06_check/, apart from the main pipeline, so making or
measuring a check chart never touches the profile or state.json:

    <job>_check.ti1/.ti2/.cht/.tif   the chart (targen + printtarg)
    check_export.<csv|xlsx>          the colorimeter export of it
    <job>_check.ti3                  that export merged with the .ti2
    <job>_check_report.txt           profcheck -v2 output
    check.json                       which chart/export/profile a result belongs to
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from icc_profiler import argyll_tools, cgats, csv_import, joblock, stages, state
from icc_profiler.config import JobConfig
from icc_profiler.errors import PipelineError

DEFAULT_PATCH_COUNT = 40
PATCH_COUNT_RANGE = (10, 200)
# Strip letters printed on the check chart. Different from the main chart's
# A, B, C, ... so the two printed sheets can't be mixed up.
STRIP_PATTERN = "P-Z"

_STATE_FILENAME = "check.json"

#   Profile check complete, errors: max. = 14.938801, avg. = 7.640107, RMS = 8.800242
# -- confirmed against real ArgyllCMS 3.1.0 profcheck -v2 output.
_SUMMARY_RE = re.compile(
    r"errors:\s*max\.\s*=\s*([0-9.]+),\s*avg\.\s*=\s*([0-9.]+),\s*RMS\s*=\s*([0-9.]+)",
    re.IGNORECASE,
)
# Per-patch line: [8.607638] 1: ... should be ...
_PATCH_RE = re.compile(r"^\[([0-9.]+)\]\s*(\S+):", re.MULTILINE)


def parse_profcheck_report(text: str) -> dict:
    m = _SUMMARY_RE.search(text)
    patch_matches = _PATCH_RE.findall(text)
    return {
        "maximum_de": m.group(1) if m else None,
        "average_de": m.group(2) if m else None,
        "rms_de": m.group(3) if m else None,
        "patch_count": len(patch_matches),
        "raw": text,
    }


# -- layout -------------------------------------------------------------------


def check_dir(job_dir: Path) -> Path:
    return job_dir / "06_check"


def basename(job_dir: Path, config: JobConfig) -> Path:
    return check_dir(job_dir) / f"{config.job_name}_check"


def ti2_path(job_dir: Path, config: JobConfig) -> Path:
    return basename(job_dir, config).with_suffix(".ti2")


def ti3_path(job_dir: Path, config: JobConfig) -> Path:
    return basename(job_dir, config).with_suffix(".ti3")


def report_path(job_dir: Path, config: JobConfig) -> Path:
    return check_dir(job_dir) / f"{config.job_name}_check_report.txt"


def chart_tiffs(job_dir: Path) -> list[Path]:
    d = check_dir(job_dir)
    return sorted(d.glob("*.tif*")) if d.is_dir() else []


def _fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _load_state(job_dir: Path) -> dict:
    path = check_dir(job_dir) / _STATE_FILENAME
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(job_dir: Path, data: dict) -> None:
    path = check_dir(job_dir) / _STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def _require_colorimeter(config: JobConfig) -> None:
    if config.measurement.method != "colorimeter_csv":
        raise PipelineError("The check chart is only available for colorimeter jobs.")


# -- actions ------------------------------------------------------------------


def generate(job_dir: Path, config: JobConfig, patch_count: int = DEFAULT_PATCH_COUNT) -> None:
    """Make a new check chart. Patches are perceptual-space random points
    (targen -R) rather than the main chart's optimised spread, so they fall
    between the training patches instead of on top of them."""
    _require_colorimeter(config)
    lo, hi = PATCH_COUNT_RANGE
    if not lo <= patch_count <= hi:
        raise PipelineError(f"The check chart needs between {lo} and {hi} patches.")
    devclass = stages._DEVCLASS_BY_COLORSPACE.get(config.chart.colorspace)
    if devclass is None:
        raise PipelineError(f"No targen device-class mapping for colorspace {config.chart.colorspace!r}")

    with joblock.job_lock(job_dir):
        d = check_dir(job_dir)
        d.mkdir(parents=True, exist_ok=True)
        base = basename(job_dir, config)
        # A previous check chart's pages and results no longer apply. The
        # old export is kept (renamed), never deleted: it is the user's data.
        for old in [*chart_tiffs(job_dir), ti3_path(job_dir, config), report_path(job_dir, config)]:
            old.unlink(missing_ok=True)
        old_state = _load_state(job_dir)
        if old_state.get("export"):
            old_export = d / old_state["export"]
            if old_export.is_file():
                old_export.rename(d / f"{old_export.stem}.old-{old_state.get('chart_id', 'x')}{old_export.suffix}")

        log = stages.logs_dir(job_dir) / "check-chart.log"
        targen_argv = [
            stages._bin("targen", config),
            "-v",
            f"-d{devclass}",
            "-e2",  # two paper-white patches
            "-s0",
            "-g0",
            "-R",
            f"-f{patch_count}",
            *stages.targen_ink_args(config),
            str(base),
        ]
        argyll_tools.run(targen_argv, log_file=log, cwd=d)
        stages._require_file(base.with_suffix(".ti1"), "targen output (.ti1)")
        stages.print_chart(config, base, log, extra=[f"-x{STRIP_PATTERN}"])
        _save_state(job_dir, {"chart_id": _fingerprint(ti2_path(job_dir, config)), "patch_count": patch_count})


def save_export(job_dir: Path, config: JobConfig, data: bytes, suffix: str) -> Path:
    """Store the colorimeter export of the check chart."""
    _require_colorimeter(config)
    st = _load_state(job_dir)
    if not st.get("chart_id") or _fingerprint(ti2_path(job_dir, config)) != st["chart_id"]:
        raise PipelineError("Make the check chart first, then upload the measurements of it.")
    suffix = suffix.lower()
    if suffix == ".txt":
        suffix = ".csv"
    if suffix not in (".csv", ".xlsx"):
        raise PipelineError("Upload a .csv or .xlsx export from the colorimeter software.")
    with joblock.job_lock(job_dir):
        dest = check_dir(job_dir) / f"check_export{suffix}"
        dest.write_bytes(data)
        st.update(export=dest.name, export_chart_id=st["chart_id"])
        st.pop("result", None)
        report_path(job_dir, config).unlink(missing_ok=True)
        _save_state(job_dir, st)
    return dest


def run(job_dir: Path, config: JobConfig) -> dict:
    """Import the check chart readings and compare them with the profile."""
    _require_colorimeter(config)
    if not state.is_done(stages.load_state(job_dir, config), "build-profile"):
        raise PipelineError("Build the profile first; the check chart tests a finished profile.")
    icc = stages.icc_path(job_dir, config)
    stages._require_file(icc, "built .icc profile")
    st = _load_state(job_dir)
    chart_id = _fingerprint(ti2_path(job_dir, config))
    if not chart_id or chart_id != st.get("chart_id"):
        raise PipelineError("Make the check chart first.")
    if not st.get("export") or st.get("export_chart_id") != chart_id:
        raise PipelineError("Upload the colorimeter export of the check chart first.")
    m = config.measurement.colorimeter_csv
    if not m.lab_reference:
        raise PipelineError("Set the illuminant/observer your colorimeter measured in on the job page first.")

    with joblock.job_lock(job_dir):
        table = csv_import.merge(
            ti2_path(job_dir, config),
            check_dir(job_dir) / st["export"],
            m.column_map,
            lab_reference=m.lab_reference,
            match_by=m.match_by,
            specular=m.specular,
            ignore_prefixes=m.ignore_name_prefixes,
        )
        ti3 = ti3_path(job_dir, config)
        table.write(ti3)
        log = stages.logs_dir(job_dir) / "check-chart.log"
        proc = argyll_tools.run(
            [stages._bin("profcheck", config), "-v2", str(ti3), str(icc)], log_file=log, cwd=job_dir
        )
        text = (proc.stdout or "") + (proc.stderr or "")
        report_path(job_dir, config).write_text(text)
        summary = parse_profcheck_report(text)
        result = {k: summary[k] for k in ("maximum_de", "average_de", "rms_de", "patch_count")}
        result["profile_id"] = _fingerprint(icc)
        st["result"] = result
        _save_state(job_dir, st)
    return result


def status(job_dir: Path, config: JobConfig) -> dict:
    """Everything the job page needs to show the check chart section."""
    st = _load_state(job_dir)
    chart_id = _fingerprint(ti2_path(job_dir, config))
    has_chart = bool(chart_id) and chart_id == st.get("chart_id")
    export_ok = has_chart and bool(st.get("export")) and st.get("export_chart_id") == chart_id
    result = st.get("result") if export_ok else None
    if result:
        report = report_path(job_dir, config)
        result = {**result, "raw": report.read_text() if report.is_file() else ""}
        # The profile was rebuilt after this check ran.
        result["outdated"] = result.get("profile_id") != _fingerprint(stages.icc_path(job_dir, config))
    locs = []
    if has_chart:
        locs = sorted((r["SAMPLE_LOC"] for r in cgats.read(ti2_path(job_dir, config)).rows), key=csv_import.loc_sort_key)
    return {
        "has_chart": has_chart,
        "patch_count": len(locs),
        "first_loc": locs[0] if locs else None,
        "last_loc": locs[-1] if locs else None,
        "pages": [p.name for p in chart_tiffs(job_dir)] if has_chart else [],
        "export": st.get("export") if export_ok else None,
        "result": result,
    }

