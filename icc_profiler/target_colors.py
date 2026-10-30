"""Custom target colors: colors the user specifically cares about getting right.

These are stored per job (jobs/<job_name>/target_colors.json) as *device-space*
values -- what to actually put in the printed file (RGB or CMYK, matching the
job's chart.colorspace) -- plus an optional target_lab the user supplies for
reference/comparison in the results viewer. merge_into_ti1() injects them as
real fixed patches into the generated chart, so they get genuinely measured
during profiling rather than just checked after the fact.

Device-space input is required (not just a desired Lab) because a patch chart
is printed from device values; what Lab value a given device value *actually*
produces on this printer/paper is precisely the thing profiling measures, so
it can't be an input. A user who only has a target real-world color in mind
should pick the RGB/CMYK value already used for it in their source file.
"""
from __future__ import annotations

import json
from pathlib import Path

from icc_profiler.cgats import CgatsTable, read_tables, write_tables
from icc_profiler.errors import PipelineError
from icc_profiler.names import safe_name, slugify

TARGET_COLORS_FILENAME = "target_colors.json"

# Confirmed against real targen 3.1.0 output: device-space fields are
# percentages (0-100), not 0-255 byte values.
FIELD_NAMES = {
    "RGB": ["RGB_R", "RGB_G", "RGB_B"],
    "CMYK": ["CMYK_C", "CMYK_M", "CMYK_Y", "CMYK_K"],
    "GRAY": ["GRAY_K"],
}


def target_colors_path(job_dir: Path) -> Path:
    return job_dir / TARGET_COLORS_FILENAME


def load(job_dir: Path) -> list[dict]:
    path = target_colors_path(job_dir)
    if not path.exists():
        return []
    return json.loads(path.read_text())


def save(job_dir: Path, colors: list[dict]) -> None:
    target_colors_path(job_dir).write_text(json.dumps(colors, indent=2))


def sample_id_for(name: str) -> str:
    """The chart SAMPLE_ID for a custom color. Names are validated on add(),
    but colors saved before that may contain spaces etc., which would split
    the unquoted CGATS row -- slugify keeps those working."""
    return f"CUSTOM_{slugify(name)}"


def add(job_dir: Path, name: str, device_value: list[float], target_lab: list[float] | None = None) -> list[dict]:
    name = safe_name(name, "Color name")
    if any(not (0 <= v <= 100) for v in device_value):
        raise PipelineError(f"Device values {device_value} must each be between 0 and 100 (percent)")
    if target_lab is not None and len(target_lab) != 3:
        raise PipelineError(f"Target Lab must be three numbers L,a,b (got {target_lab})")
    colors = load(job_dir)
    if any(c["name"] == name for c in colors):
        raise PipelineError(f"A target color named {name!r} already exists in this job")
    colors.append({"name": name, "device_value": device_value, "target_lab": target_lab})
    save(job_dir, colors)
    return colors


def remove(job_dir: Path, name: str) -> list[dict]:
    colors = load(job_dir)
    remaining = [c for c in colors if c["name"] != name]
    if len(remaining) == len(colors):
        raise PipelineError(f"No target color named {name!r} found in this job")
    save(job_dir, remaining)
    return remaining


def hex_to_device_rgb(hex_value: str) -> list[float]:
    """Hex (#RRGGBB, 0-255 per channel) -> device RGB percentages (0-100),
    matching targen's field convention (confirmed against real output)."""
    h = hex_value.strip().lstrip("#")
    if len(h) != 6:
        raise PipelineError(f"Hex color {hex_value!r} must be 6 hex digits (e.g. #3366CC)")
    try:
        return [round(int(h[i : i + 2], 16) / 255 * 100, 4) for i in (0, 2, 4)]
    except ValueError as exc:
        raise PipelineError(f"Hex color {hex_value!r} is not valid hex") from exc


def _approx_xyz_percent(rgb_percent: list[float]) -> tuple[float, float, float]:
    """Rough sRGB(0-100)->XYZ(0-100) estimate for the .ti1 XYZ_* columns.

    targen fills these with its own device-model estimate; ours is a generic
    placeholder (real Lab/XYZ for these patches comes from actual measurement
    later, not this column) -- just needs to be numeric and roughly sane so
    printtarg/downstream CGATS readers don't choke on a blank/garbage value.
    """
    r, g, b = (c / 100 for c in rgb_percent)
    X = (0.4124 * r + 0.3576 * g + 0.1805 * b) * 100
    Y = (0.2126 * r + 0.7152 * g + 0.0722 * b) * 100
    Z = (0.0193 * r + 0.1192 * g + 0.9505 * b) * 100
    return round(X, 4), round(Y, 4), round(Z, 4)


def merge_into_ti1(ti1_path: Path, colors: list[dict], colorspace: str) -> list[CgatsTable]:
    """Append custom target colors as fixed patches onto a .ti1's first table.

    Confirmed against real targen 3.1.0 output: a .ti1 file holds THREE
    concatenated CGATS tables (the actual patch list, plus two diagnostic
    tables). Only the first is modified; all tables are preserved on write
    (see cgats.py) -- a naive single-table rewrite was confirmed to corrupt
    the file and break printtarg.
    """
    if colorspace not in FIELD_NAMES:
        raise PipelineError(f"Unsupported colorspace for custom colors: {colorspace!r}")
    device_fields = FIELD_NAMES[colorspace]

    tables = read_tables(ti1_path)
    table = tables[0]
    missing_fields = [f for f in device_fields if f not in table.fields]
    if missing_fields:
        raise PipelineError(
            f"{ti1_path} does not have the expected {colorspace} fields {missing_fields}; "
            f"has {table.fields}. This likely means targen's field-naming convention differs "
            f"from what this project assumed -- verify against real targen output."
        )
    has_xyz = all(f in table.fields for f in ("XYZ_X", "XYZ_Y", "XYZ_Z")) and colorspace == "RGB"

    # Custom colors get a "CUSTOM_<name>" SAMPLE_ID rather than continuing the
    # numeric sequence, both so the user has a meaningful name to type when
    # manually naming a colorimeter reading, and so downstream code (the
    # results viewer, CSV import) can identify these rows by prefix alone.
    existing_names = {r["SAMPLE_ID"] for r in table.rows}
    for color in colors:
        if len(color["device_value"]) != len(device_fields):
            raise PipelineError(
                f"Target color {color['name']!r} has {len(color['device_value'])} device "
                f"values but colorspace {colorspace} expects {len(device_fields)} "
                f"({device_fields})"
            )
        sample_id = sample_id_for(color["name"])
        if sample_id in existing_names:
            raise PipelineError(f"Chart already has a patch with SAMPLE_ID {sample_id!r}")
        existing_names.add(sample_id)
        row = {"SAMPLE_ID": sample_id}
        row.update({f: str(v) for f, v in zip(device_fields, color["device_value"])})
        if has_xyz:
            X, Y, Z = _approx_xyz_percent(color["device_value"])
            row["XYZ_X"], row["XYZ_Y"], row["XYZ_Z"] = str(X), str(Y), str(Z)
        for f in table.fields:
            row.setdefault(f, "0")
        table.rows.append(row)

    write_tables(ti1_path, tables)
    return tables
