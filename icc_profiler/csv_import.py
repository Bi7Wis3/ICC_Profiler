"""Merge a hand-measured colorimeter CSV export (e.g. CHNSpec DS-173) into a .ti3.

This is the counterpart to ArgyllCMS's own `scanin` for devices Argyll has no
native driver for. ArgyllCMS's own documentation describes importing patch
measurement results produced by other software as the supported way to use
such instruments -- this module is exactly that import step.

Workflow this assumes: the user measured each printed chart patch by hand
with the colorimeter, saving/naming each reading in the device's own app or
PC software to match that patch's SAMPLE_LOC -- the strip/row label (e.g.
"A14") that ArgyllCMS's printtarg actually prints next to each patch on the
chart (confirmed visually against real printtarg 3.1.0 output; it is NOT the
same as SAMPLE_ID, which for a custom target color is a name like
"CUSTOM_logo_red" that never appears on the printed page). Column header
names in the CSV export are vendor-specific and unconfirmed until a real
export file is in hand -- hence the configurable column_map rather than
hardcoded headers.
"""
from __future__ import annotations

import csv as csv_module
from pathlib import Path

from icc_profiler.cgats import CgatsTable, read as cgats_read
from icc_profiler.errors import PipelineError

DEFAULT_COLUMN_MAP = {"patch_id": "Name", "L": "L*", "a": "a*", "b": "b*"}


def _read_csv_rows(csv_path: Path, column_map: dict) -> dict[str, tuple[str, str, str]]:
    required = ["patch_id", "L", "a", "b"]
    missing_keys = [k for k in required if k not in column_map]
    if missing_keys:
        raise PipelineError(
            f"csv_import column_map is missing required keys {missing_keys}; "
            f"needs an entry for each of {required}"
        )

    with csv_path.open(newline="") as f:
        reader = csv_module.DictReader(f)
        if reader.fieldnames is None:
            raise PipelineError(f"{csv_path} has no header row")
        missing_headers = [
            column_map[k] for k in required if column_map[k] not in reader.fieldnames
        ]
        if missing_headers:
            raise PipelineError(
                f"{csv_path} is missing expected column(s) {missing_headers} "
                f"(have: {reader.fieldnames}). Adjust measurement.colorimeter_csv."
                f"column_map to match this export's real headers."
            )
        by_patch_id: dict[str, tuple[str, str, str]] = {}
        for row in reader:
            patch_id = row[column_map["patch_id"]].strip()
            if not patch_id:
                continue
            if patch_id in by_patch_id:
                raise PipelineError(
                    f"{csv_path} has more than one row for patch id {patch_id!r} "
                    f"-- duplicate or mis-named reading"
                )
            by_patch_id[patch_id] = (
                row[column_map["L"]],
                row[column_map["a"]],
                row[column_map["b"]],
            )
    return by_patch_id


def merge(ti2_path: Path, csv_path: Path, column_map: dict | None = None) -> CgatsTable:
    column_map = column_map or DEFAULT_COLUMN_MAP
    ti2 = cgats_read(ti2_path)
    if "SAMPLE_LOC" not in ti2.fields:
        raise PipelineError(f"{ti2_path} has no SAMPLE_LOC field; has {ti2.fields}")

    measured = _read_csv_rows(csv_path, column_map)

    expected_locs = {row["SAMPLE_LOC"] for row in ti2.rows}
    missing = sorted(expected_locs - measured.keys(), key=lambda s: (len(s), s))
    extra = sorted(measured.keys() - expected_locs, key=lambda s: (len(s), s))
    if missing:
        raise PipelineError(
            f"{csv_path} is missing measurements for {len(missing)} chart patch(es) "
            f"(by the strip/row label printed on the chart, e.g. 'A14'): "
            f"{missing[:20]}{'...' if len(missing) > 20 else ''}. "
            f"Every patch on the generated chart must be measured and named to match "
            f"the label printed next to it before import."
        )
    if extra:
        raise PipelineError(
            f"{csv_path} has {len(extra)} reading(s) whose name doesn't match any chart "
            f"patch label: {extra[:20]}{'...' if len(extra) > 20 else ''}. "
            f"Check for typos in how readings were named on the device/app."
        )

    # Keep SAMPLE_ID (the chart's own patch identity, e.g. "CUSTOM_logo_red"
    # for a custom target color) and the genuine device-value fields;
    # SAMPLE_LOC and any leftover XYZ_* estimate columns from targen are
    # metadata/placeholders, not real measurements, so they're dropped here
    # rather than carried into the .ti3 colprof will treat as measured data.
    device_fields = [f for f in ti2.fields if f not in ("SAMPLE_ID", "SAMPLE_LOC", "XYZ_X", "XYZ_Y", "XYZ_Z")]
    out_fields = ["SAMPLE_ID", *device_fields, "LAB_L", "LAB_A", "LAB_B"]
    out_rows = []
    for row in ti2.rows:
        loc = row["SAMPLE_LOC"]
        L, a, b = measured[loc]
        out_row = {"SAMPLE_ID": row["SAMPLE_ID"], **{f: row.get(f, "") for f in device_fields}}
        out_row["LAB_L"] = L
        out_row["LAB_A"] = a
        out_row["LAB_B"] = b
        out_rows.append(out_row)

    if any(f.startswith("RGB_") for f in device_fields):
        dev_rep = "RGB"
    elif any(f.startswith("CMYK_") for f in device_fields):
        dev_rep = "CMYK"
    else:
        dev_rep = "GRAY"

    # Preamble tags (DEVICE_CLASS, COLOR_REP) mirror what real scanin -c
    # output uses for a printer-characterization .ti3 (confirmed against
    # ArgyllCMS 3.1.0), substituting LAB for XYZ since the colorimeter
    # reports Lab directly.
    return CgatsTable(
        file_type="CTI3",
        preamble=[
            'DESCRIPTOR "Argyll Calibration Target chart information 3"',
            'ORIGINATOR "icc_profiler csv_import"',
            'DEVICE_CLASS "OUTPUT"',
            f'COLOR_REP "i{dev_rep}_LAB"',
        ],
        fields=out_fields,
        rows=out_rows,
    )
