"""Manual patch-by-patch colorimeter scan: an alternative to CSV upload.

For colorimeters with no CSV export (or just a more guided workflow), this
lets the user type in one patch's L*a*b* reading at a time instead of
collecting every reading externally and uploading a CSV cold. Progress is
kept in its own scratch file, separate from the job config, keyed by
SAMPLE_LOC -- the same patch label (e.g. "A14") csv_import.py matches CSV
rows against.

On "finish", the accumulated readings are written out as a real CSV at the
exact path upload_csv() uses, and config.measurement.colorimeter_csv.csv_file
is set the same way -- so stages.import_measurements()/csv_import.merge()
need no changes at all; they can't tell a manually-built CSV from an
uploaded one.
"""
from __future__ import annotations

import csv as csv_module
import json
from pathlib import Path

from icc_profiler.cgats import read as cgats_read
from icc_profiler.color_convert import xyz_to_hex
from icc_profiler.config import JobConfig
from icc_profiler.csv_import import DEFAULT_COLUMN_MAP
from icc_profiler.errors import PipelineError
from icc_profiler.stages import measurements_in_dir, ti2_path

MANUAL_SCAN_FILENAME = "manual_scan_progress.json"
CSV_FILENAME = "colorimeter_export.csv"


def progress_path(job_dir: Path) -> Path:
    return measurements_in_dir(job_dir) / MANUAL_SCAN_FILENAME


def load_progress(job_dir: Path) -> dict[str, dict]:
    path = progress_path(job_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_progress(job_dir: Path, progress: dict[str, dict]) -> None:
    path = progress_path(job_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, indent=2))


def record_reading(job_dir: Path, patch_loc: str, L: str, a: str, b: str) -> dict[str, dict]:
    try:
        L_f, a_f, b_f = float(L), float(a), float(b)
    except ValueError as exc:
        raise PipelineError(
            f"L*/a*/b* must be numbers (got L={L!r}, a={a!r}, b={b!r})"
        ) from exc
    progress = load_progress(job_dir)
    progress[patch_loc] = {"L": L_f, "a": a_f, "b": b_f}
    save_progress(job_dir, progress)
    return progress


def clear_reading(job_dir: Path, patch_loc: str) -> dict[str, dict]:
    progress = load_progress(job_dir)
    progress.pop(patch_loc, None)
    save_progress(job_dir, progress)
    return progress


def chart_patches(job_dir: Path, config: JobConfig) -> list[dict]:
    """Ordered list of this job's chart patches, in the order .ti2 (i.e.
    printtarg) lists them, each with an approximate target-color swatch
    computed from the row's own XYZ_* estimate."""
    path = ti2_path(job_dir, config)
    if not path.is_file():
        raise PipelineError(f"{path} does not exist yet -- run 'generate-chart' first")
    table = cgats_read(path)
    has_xyz = all(f in table.fields for f in ("XYZ_X", "XYZ_Y", "XYZ_Z"))
    patches = []
    for row in table.rows:
        swatch_hex = None
        if has_xyz:
            try:
                swatch_hex = xyz_to_hex(float(row["XYZ_X"]), float(row["XYZ_Y"]), float(row["XYZ_Z"]))
            except ValueError:
                swatch_hex = None
        patches.append({"loc": row["SAMPLE_LOC"], "sample_id": row["SAMPLE_ID"], "swatch_hex": swatch_hex})
    return patches


def build_rows(job_dir: Path, config: JobConfig) -> list[dict]:
    """chart_patches() joined with the readings recorded so far."""
    progress = load_progress(job_dir)
    rows = []
    for patch in chart_patches(job_dir, config):
        reading = progress.get(patch["loc"])
        rows.append(
            {
                **patch,
                "done": reading is not None,
                "L": reading["L"] if reading else None,
                "a": reading["a"] if reading else None,
                "b": reading["b"] if reading else None,
            }
        )
    return rows


def write_csv(job_dir: Path, config: JobConfig, column_map: dict | None = None) -> Path:
    column_map = column_map or DEFAULT_COLUMN_MAP
    required = ["patch_id", "L", "a", "b"]
    missing_keys = [k for k in required if k not in column_map]
    if missing_keys:
        raise PipelineError(f"column_map is missing required keys {missing_keys}")

    patches = chart_patches(job_dir, config)
    progress = load_progress(job_dir)
    missing = sorted(
        (p["loc"] for p in patches if p["loc"] not in progress),
        key=lambda s: (len(s), s),
    )
    if missing:
        raise PipelineError(
            f"{len(missing)} chart patch(es) still need a reading before finishing: "
            f"{missing[:20]}{'...' if len(missing) > 20 else ''}"
        )

    dest = measurements_in_dir(job_dir) / CSV_FILENAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = [column_map["patch_id"], column_map["L"], column_map["a"], column_map["b"]]
    with dest.open("w", newline="") as f:
        writer = csv_module.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        for patch in patches:
            reading = progress[patch["loc"]]
            writer.writerow(
                {
                    column_map["patch_id"]: patch["loc"],
                    column_map["L"]: reading["L"],
                    column_map["a"]: reading["a"],
                    column_map["b"]: reading["b"],
                }
            )
    return dest
