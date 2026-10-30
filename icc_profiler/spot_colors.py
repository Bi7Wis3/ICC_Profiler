"""Spot colors: CMYK (or RGB) recipes for named colors such as Pantone swatches.

The profile isn't changed. It is run backwards: given the Lab a spot color
should have, `xicclu -fif` (inverse of the profile's forward table, the
most accurate direction) returns the device values that print it, or the
closest the printer can reach. The recipe is then looked up forwards again
to get the Lab it is expected to print, and the CIEDE2000 between that and
the target is the expected error -- large when the color is outside the
printer's gamut.

Recipes can be refined with a proof sheet: print the recipes as patches,
measure them, and the difference between where the profile said each
patch would land and where it did is added to that color's correction
before the recipe is looked up again. That removes the profile's local
error for exactly those colors.

Target Lab values come from measuring the swatch book with the same
colorimeter and settings as the chart (imported like the chart readings,
so D65/2 readings are adapted to D50), or are typed in as D50/2 values
such as Pantone publishes.

Everything lives in 07_spot/:
    spot_colors.json                 the colors, recipes and proof state
    <job>_proof.ti1/.ti2/.cht/.tif   the proof sheet
    proof_export.<csv|xlsx>          the colorimeter export of it
"""
from __future__ import annotations

import csv as csv_module
import hashlib
import io
import json
import re
import shutil
import time
from pathlib import Path

from icc_profiler import argyll_tools, cgats, csv_import, joblock, stages, state, target_colors
from icc_profiler.color_convert import delta_e_2000, lab_d50_to_hex, lab_d65_to_d50
from icc_profiler.config import JobConfig
from icc_profiler.errors import PipelineError

MAX_NAME_LENGTH = 60
MAX_COLORS = 200
# Strip letters of the proof sheet, distinct from the main chart (A...) and
# the check chart (P...).
STRIP_PATTERN = "S-Z"
# Expected CIEDE2000 above this means the color is outside what the printer
# can reach; the recipe is then the closest printable color.
OUT_OF_GAMUT_DE = 1.0

_DATA_FILENAME = "spot_colors.json"
_DEVICE_FIELDS = target_colors.FIELD_NAMES
_SAMPLE_NAME_RE = re.compile(r"^(sample|standard)\s*\d*$", re.IGNORECASE)


# -- storage ------------------------------------------------------------------


def spot_dir(job_dir: Path) -> Path:
    return job_dir / "07_spot"


def proof_basename(job_dir: Path, config: JobConfig) -> Path:
    return spot_dir(job_dir) / f"{config.job_name}_proof"


def proof_ti2_path(job_dir: Path, config: JobConfig) -> Path:
    return proof_basename(job_dir, config).with_suffix(".ti2")


def proof_tiffs(job_dir: Path) -> list[Path]:
    d = spot_dir(job_dir)
    return sorted(d.glob("*.tif*")) if d.is_dir() else []


def _fingerprint(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def load(job_dir: Path) -> dict:
    path = spot_dir(job_dir) / _DATA_FILENAME
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("colors", [])
    data.setdefault("proof", {})
    return data


def _save(job_dir: Path, data: dict) -> None:
    path = spot_dir(job_dir) / _DATA_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def clean_name(name: str) -> str:
    name = " ".join((name or "").split())
    if not name:
        raise PipelineError("Every spot color needs a name.")
    if len(name) > MAX_NAME_LENGTH or any(ch in name for ch in "\"<>\\"):
        raise PipelineError(f"Spot color name {name!r} is too long or contains \" < > or \\.")
    return name


def _require_profile(job_dir: Path, config: JobConfig) -> Path:
    if not state.is_done(stages.load_state(job_dir, config), "build-profile"):
        raise PipelineError("Build the profile first; spot color recipes are looked up in it.")
    icc = stages.icc_path(job_dir, config)
    stages._require_file(icc, "built .icc profile")
    return icc


# -- adding colors --------------------------------------------------------------


def parse_typed(text: str) -> list[tuple[str, tuple[float, float, float]]]:
    """Lines of 'name; L; a; b' (or tab/comma separated with '.' decimals)."""
    out = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        delimiter = ";" if ";" in line else ("\t" if "\t" in line else ",")
        parts = [p.strip() for p in next(csv_module.reader(io.StringIO(line), delimiter=delimiter))]
        if len(parts) != 4:
            raise PipelineError(f"Line {n} ({line.strip()!r}) needs 4 values: name; L; a; b")
        name = clean_name(parts[0])
        out.append((name, csv_import.parse_lab(*parts[1:], where=f"Line {n} ({name})")))
    return out


def import_measured(
    config: JobConfig, export_path: Path, names_text: str = ""
) -> list[tuple[str, tuple[float, float, float]]]:
    """Read the swatch book measurements from a colorimeter export, with the
    job's SCI/SCE and illuminant settings. Readings named Sample1, Sample2...
    get their names from names_text (one per line, in measuring order)."""
    m = config.measurement.colorimeter_csv
    if not m.lab_reference:
        raise PipelineError("Set the illuminant/observer your colorimeter measured in on the job page first.")
    readings = csv_import.read_readings(
        export_path,
        m.column_map,
        specular=m.specular,
        ignore_prefixes=m.ignore_name_prefixes,
        lab_reference=m.lab_reference,
    )
    if not readings:
        raise PipelineError(f"{export_path.name} has no usable readings.")
    names = [line for line in (n.strip() for n in names_text.splitlines()) if line]
    if names:
        if len(names) != len(readings):
            raise PipelineError(
                f"The export has {len(readings)} reading(s) but you listed {len(names)} name(s). "
                f"List one name per reading, in the order you measured them."
            )
    elif any(_SAMPLE_NAME_RE.match(name) for name, _ in readings):
        raise PipelineError(
            "The readings are named Sample1, Sample2, ... List the color names, one per line "
            "in the order you measured them, in the names box."
        )
    else:
        names = [name for name, _ in readings]
    out = []
    for name, lab in zip(names, (lab for _, lab in readings)):
        if m.lab_reference == "D65_2":
            lab = lab_d65_to_d50(*lab)
        out.append((clean_name(name), tuple(lab)))
    return out


def add_colors(job_dir: Path, config: JobConfig, colors: list[tuple[str, tuple[float, float, float]]], source: str) -> int:
    """Add or replace colors by name and look up their recipes."""
    with joblock.job_lock(job_dir):
        data = load(job_dir)
        by_name = {c["name"].casefold(): c for c in data["colors"]}
        for name, lab in colors:
            by_name[name.casefold()] = {
                "name": name,
                "source": source,
                "target_lab": [round(v, 3) for v in lab],
                "correction": [0.0, 0.0, 0.0],
                "rounds": 0,
            }
        if len(by_name) > MAX_COLORS:
            raise PipelineError(f"At most {MAX_COLORS} spot colors per job.")
        data["colors"] = list(by_name.values())
        _save(job_dir, data)
    recompute(job_dir, config)
    return len(colors)


def remove_color(job_dir: Path, name: str) -> None:
    with joblock.job_lock(job_dir):
        data = load(job_dir)
        data["colors"] = [c for c in data["colors"] if c["name"] != name]
        _save(job_dir, data)


# -- recipes --------------------------------------------------------------------


def _ink_args(config: JobConfig) -> list[str]:
    args = []
    if config.chart.colorspace == "CMYK":
        if config.chart.ink_limit:
            args.append(f"-l{config.chart.ink_limit:g}")
        if config.output.black_ink_limit:
            args.append(f"-L{config.output.black_ink_limit:g}")
    return args


def _lookup(config: JobConfig, icc: Path, argv_flags: list[str], values: list[list[float]], log: Path) -> list[list[float]]:
    """Run xicclu on a batch of values; returns the output values per line."""
    if not values:
        return []
    argv = [stages._bin("xicclu", config), "-v0", *argv_flags, str(icc)]
    stdin = "\n".join(" ".join(f"{v:.6f}" for v in row) for row in values) + "\n"
    proc = argyll_tools.run(argv, log_file=log, input=stdin, cwd=icc.parent)
    rows = [line.split() for line in (proc.stdout or "").splitlines() if line.strip()]
    if len(rows) != len(values):
        raise PipelineError(f"xicclu returned {len(rows)} result(s) for {len(values)} color(s); see {log}")
    try:
        return [[float(x) for x in row] for row in rows]
    except ValueError as exc:
        raise PipelineError(f"Unexpected xicclu output; see {log}") from exc


def recompute(job_dir: Path, config: JobConfig) -> None:
    """Look up every color's recipe in the current profile.

    First the target itself: the profile's forward prediction for that
    recipe is the closest printable color ("reachable"), equal to the
    target unless it is out of gamut. Colors corrected on a proof sheet are
    then looked up again at reachable + correction, where the correction is
    the profile's local error measured on the proof; the print is expected
    to land at predicted - correction.
    """
    icc = _require_profile(job_dir, config)
    log = stages.logs_dir(job_dir) / "spot-colors.log"
    # Relative colorimetric: paper white maps to paper white, as for
    # proofing; -fif inverts the accurate forward (A2B) table.
    backward = ["-fif", "-ir", "-pl", *_ink_args(config)]
    forward = ["-ff", "-ir", "-pl"]
    with joblock.job_lock(job_dir):
        data = load(job_dir)
        colors = data["colors"]
        device = _lookup(config, icc, backward, [c["target_lab"] for c in colors], log)
        reachable = _lookup(config, icc, forward, device, log)
        corrected = [i for i, c in enumerate(colors) if any(c.get("correction", [0, 0, 0]))]
        if corrected:
            aims = [
                [min(max(r + d, lo), hi) for r, d, lo, hi in zip(reachable[i], colors[i]["correction"], (0, -128, -128), (100, 127, 127))]
                for i in corrected
            ]
            new_device = _lookup(config, icc, backward, aims, log)
            new_predicted = _lookup(config, icc, forward, new_device, log)
            for i, dev, lab in zip(corrected, new_device, new_predicted):
                device[i] = dev
                reachable[i] = [p - d for p, d in zip(lab, colors[i]["correction"])]
        for c, dev, lab in zip(colors, device, reachable):
            c["device"] = [round(min(max(v, 0.0), 1.0) * 100, 1) for v in dev]
            c["expected_lab"] = [round(v, 2) for v in lab]
            c["expected_de"] = round(delta_e_2000(tuple(c["target_lab"]), tuple(lab)), 2)
        data["profile_id"] = _fingerprint(icc)
        _save(job_dir, data)


# -- proof sheet ------------------------------------------------------------------


def make_proof(job_dir: Path, config: JobConfig) -> None:
    """A chart with one patch per spot color recipe (plus paper white)."""
    data = load(job_dir)
    if not data["colors"]:
        raise PipelineError("Add some spot colors first.")
    if data.get("profile_id") != _fingerprint(stages.icc_path(job_dir, config)):
        recompute(job_dir, config)
        data = load(job_dir)
    devclass = stages._DEVCLASS_BY_COLORSPACE.get(config.chart.colorspace)
    if devclass is None or config.chart.colorspace not in _DEVICE_FIELDS:
        raise PipelineError(f"Spot colors aren't supported for colorspace {config.chart.colorspace!r}")

    with joblock.job_lock(job_dir):
        d = spot_dir(job_dir)
        d.mkdir(parents=True, exist_ok=True)
        base = proof_basename(job_dir, config)
        for old in proof_tiffs(job_dir):
            old.unlink(missing_ok=True)
        proof = data["proof"]
        if proof.get("export"):
            old_export = d / proof["export"]
            if old_export.is_file():
                old_export.rename(d / f"{old_export.stem}.old-{proof.get('chart_id', 'x')}{old_export.suffix}")

        log = stages.logs_dir(job_dir) / "spot-colors.log"
        # targen can't make an empty chart (and crashes on very small ones),
        # so make the smallest regular one and keep only its white patch.
        argyll_tools.run(
            [stages._bin("targen", config), "-v", f"-d{devclass}", "-e1", "-s0", "-g0", "-f0", "-m2", str(base)],
            log_file=log,
            cwd=d,
        )
        ti1 = base.with_suffix(".ti1")
        stages._require_file(ti1, "targen output (.ti1)")
        tables = cgats.read_tables(ti1)
        fields = _DEVICE_FIELDS[config.chart.colorspace]
        white = [r for r in tables[0].rows if all(float(r[f]) == 0 for f in fields)][:1]
        tables[0].rows = white
        cgats.write_tables(ti1, tables)
        patches = [{"name": c["name"], "device_value": c["device"]} for c in data["colors"]]
        target_colors.merge_into_ti1(ti1, patches, config.chart.colorspace)
        stages.print_chart(config, base, log, extra=[f"-x{STRIP_PATTERN}"])

        data["proof"] = {
            "chart_id": _fingerprint(proof_ti2_path(job_dir, config)),
            "names": {target_colors.sample_id_for(c["name"]): c["name"] for c in data["colors"]},
            # What each patch was printed with, so readings are credited to
            # that recipe even after the recipes have since been corrected.
            "recipes": {
                c["name"]: {"device": list(c["device"]), "expected_lab": list(c.get("expected_lab", c["target_lab"]))}
                for c in data["colors"]
            },
        }
        _save(job_dir, data)


def save_proof_export(job_dir: Path, config: JobConfig, content: bytes, suffix: str) -> None:
    data = load(job_dir)
    proof = data["proof"]
    if not proof.get("chart_id") or proof["chart_id"] != _fingerprint(proof_ti2_path(job_dir, config)):
        raise PipelineError("Make the proof sheet first, then upload the measurements of it.")
    suffix = ".csv" if suffix.lower() == ".txt" else suffix.lower()
    if suffix not in (".csv", ".xlsx"):
        raise PipelineError("Upload a .csv or .xlsx export from the colorimeter software.")
    with joblock.job_lock(job_dir):
        dest = spot_dir(job_dir) / f"proof_export{suffix}"
        dest.write_bytes(content)
        proof.update(export=dest.name, export_chart_id=proof["chart_id"])
        _save(job_dir, data)


def read_proof(job_dir: Path, config: JobConfig) -> int:
    """Store each color's measured Lab from the proof sheet export."""
    data = load(job_dir)
    proof = data["proof"]
    if not proof.get("export") or proof.get("export_chart_id") != _fingerprint(proof_ti2_path(job_dir, config)):
        raise PipelineError("Upload the colorimeter export of the current proof sheet first.")
    m = config.measurement.colorimeter_csv
    if not m.lab_reference:
        raise PipelineError("Set the illuminant/observer your colorimeter measured in on the job page first.")
    with joblock.job_lock(job_dir):
        table = csv_import.merge(
            proof_ti2_path(job_dir, config),
            spot_dir(job_dir) / proof["export"],
            m.column_map,
            lab_reference=m.lab_reference,
            match_by=m.match_by,
            specular=m.specular,
            ignore_prefixes=m.ignore_name_prefixes,
        )
        fields = _DEVICE_FIELDS[config.chart.colorspace]
        measured = {}
        printed = {}
        for row in table.rows:
            name = proof["names"].get(row["SAMPLE_ID"])
            if name:
                measured[name] = (float(row["LAB_L"]), float(row["LAB_A"]), float(row["LAB_B"]))
                printed[name] = [round(float(row[f]), 1) for f in fields]
        recipes = proof.get("recipes", {})
        for c in data["colors"]:
            lab = measured.get(c["name"])
            if lab is None:
                continue
            recipe = recipes.get(c["name"])
            c["measured_lab"] = [round(v, 2) for v in lab]
            if recipe:
                c["measured_device"] = list(recipe["device"])
                c["measured_expected_lab"] = list(recipe["expected_lab"])
            else:
                # Proof made before recipes were recorded: the sheet itself
                # says what was printed. If that is no longer the current
                # recipe, the expected Lab of it isn't known any more.
                c["measured_device"] = printed[c["name"]]
                same = c["measured_device"] == c.get("device")
                c["measured_expected_lab"] = list(c.get("expected_lab", c["target_lab"])) if same else None
            c["measured_de"] = round(delta_e_2000(tuple(c["target_lab"]), lab), 2)
        proof["read"] = True
        _save(job_dir, data)
    return len(measured)


def _correctable(c: dict) -> bool:
    """Measured on the current recipe, off from the expected print by enough
    to be worth another round (below 0.5 is measuring noise), and not
    already closer to the target than expected -- which happens with
    out-of-gamut colors, where "correcting" would only move the print back
    to the profile's idea of the closest color."""
    if not c.get("measured_lab") or not c.get("measured_expected_lab") or c.get("measured_device") != c.get("device"):
        return False
    if c.get("measured_de", 0) <= c.get("expected_de", 0):
        return False
    return delta_e_2000(tuple(c["measured_lab"]), tuple(c["measured_expected_lab"])) >= 0.5


def apply_corrections(job_dir: Path, config: JobConfig) -> int:
    """Add the error found on the proof to each measured color's correction,
    then look the recipes up again."""
    with joblock.job_lock(job_dir):
        data = load(job_dir)
        changed = 0
        for c in data["colors"]:
            lab = c.get("measured_lab")
            if not _correctable(c):
                continue
            # The print missed where the profile said it would land by
            # (expected - measured); aim that much further next time.
            c["correction"] = [
                round(k + e - m, 3)
                for k, e, m in zip(c.get("correction", [0.0, 0.0, 0.0]), c["measured_expected_lab"], lab)
            ]
            c["rounds"] = c.get("rounds", 0) + 1
            changed += 1
        _save(job_dir, data)
    if changed:
        recompute(job_dir, config)
    return changed


# -- feeding readings back into the profile ------------------------------------------


def add_proof_to_profile(job_dir: Path, config: JobConfig) -> int:
    """Add the proof sheet readings to the profile's measurements as extra
    patches, so a rebuilt profile is accurate around these colors for
    everything printed with it, not just these recipes.

    Readings of the same device values replace older ones. The colors'
    corrections are reset: the rebuilt profile already knows how these
    recipes print, so a correction on top would count the error twice.
    The caller rebuilds the profile and recomputes the recipes."""
    data = load(job_dir)
    proof = data["proof"]
    chart_id = _fingerprint(proof_ti2_path(job_dir, config))
    if not proof.get("read") or not chart_id or proof.get("export_chart_id") != chart_id:
        raise PipelineError("Measure the current proof sheet and upload it first.")
    m = config.measurement.colorimeter_csv
    with joblock.job_lock(job_dir):
        table = csv_import.merge(
            proof_ti2_path(job_dir, config),
            spot_dir(job_dir) / proof["export"],
            m.column_map,
            lab_reference=m.lab_reference,
            match_by=m.match_by,
            specular=m.specular,
            ignore_prefixes=m.ignore_name_prefixes,
        )
        new_rows = [dict(r) for r in table.rows if r["SAMPLE_ID"] in proof["names"]]
        measured_names = {proof["names"][r["SAMPLE_ID"]] for r in new_rows}
        if not new_rows:
            raise PipelineError("No spot color patches found in the proof sheet readings.")
        fields = _DEVICE_FIELDS[config.chart.colorspace]
        path = stages.extra_patches_path(job_dir, config)
        if path.is_file():
            extra = cgats.read(path)
            new_keys = {tuple(round(float(r[f]), 2) for f in fields) for r in new_rows}
            kept = [r for r in extra.rows if tuple(round(float(r[f]), 2) for f in fields) not in new_keys]
        else:
            extra, kept = table, []
        extra.rows = [*kept, *new_rows]
        for i, r in enumerate(extra.rows, 1):
            r["SAMPLE_ID"] = f"SPOT{i}"
        path.parent.mkdir(parents=True, exist_ok=True)
        extra.write(path)

        for c in data["colors"]:
            if c["name"] in measured_names:
                c["correction"] = [0.0, 0.0, 0.0]
                c["rounds"] = 0
                c["in_profile"] = True
        _save(job_dir, data)
    return len(new_rows)


def extra_patch_count(job_dir: Path, config: JobConfig) -> int:
    path = stages.extra_patches_path(job_dir, config)
    return len(cgats.read(path).rows) if path.is_file() else 0


def remove_extra_patches(job_dir: Path, config: JobConfig) -> None:
    """Take the extra patches out of the next profile build. The file is
    renamed, never deleted: it is the user's measurement data."""
    path = stages.extra_patches_path(job_dir, config)
    if path.is_file():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path.rename(path.with_name(f"{path.stem}.removed-{stamp}.ti3"))


def backup_profile(job_dir: Path, config: JobConfig) -> Path | None:
    """Keep a copy of the current .icc before a rebuild replaces it."""
    icc = stages.icc_path(job_dir, config)
    if not icc.is_file():
        return None
    dest = icc.with_name(f"{icc.stem}.before-{time.strftime('%Y%m%d-%H%M%S')}.icc")
    shutil.copyfile(icc, dest)
    return dest


# -- views --------------------------------------------------------------------------


def status(job_dir: Path, config: JobConfig) -> dict:
    data = load(job_dir)
    icc_id = _fingerprint(stages.icc_path(job_dir, config))
    proof = data["proof"]
    proof_ok = bool(proof.get("chart_id")) and proof["chart_id"] == _fingerprint(proof_ti2_path(job_dir, config))
    labels = {}
    if proof_ok:
        for row in cgats.read(proof_ti2_path(job_dir, config)).rows:
            name = proof.get("names", {}).get(row["SAMPLE_ID"])
            if name:
                labels[name] = row["SAMPLE_LOC"]
    fields = _DEVICE_FIELDS.get(config.chart.colorspace, [])
    colors = []
    for c in data["colors"]:
        row = dict(c)
        row["target_hex"] = lab_d50_to_hex(*c["target_lab"])
        row["measured_hex"] = lab_d50_to_hex(*c["measured_lab"]) if c.get("measured_lab") else None
        row["out_of_gamut"] = c.get("expected_de", 0) > OUT_OF_GAMUT_DE
        row["proof_label"] = labels.get(c["name"])
        row["channels"] = [f.split("_")[1] for f in fields]
        colors.append(row)
    return {
        "colors": colors,
        "outdated": bool(colors) and data.get("profile_id") != icc_id,
        "proof": {
            "ready": proof_ok,
            "pages": [p.name for p in proof_tiffs(job_dir)] if proof_ok else [],
            "labels": sorted(labels.values(), key=csv_import.loc_sort_key),
            "export": proof.get("export") if proof_ok and proof.get("export_chart_id") == proof.get("chart_id") else None,
            "read": proof_ok and proof.get("read", False),
        },
        "can_correct": any(_correctable(c) for c in data["colors"]),
        "can_add_to_profile": proof_ok and bool(proof.get("read")) and proof.get("export_chart_id") == proof.get("chart_id"),
        "extra_patches": extra_patch_count(job_dir, config),
        # Recipes changed since the proof sheet was made: it no longer shows them.
        "proof_stale": proof_ok
        and any(
            proof.get("recipes", {}).get(c["name"], {}).get("device", c["device"]) != c["device"]
            or (c.get("measured_device") and c["measured_device"] != c["device"])
            for c in data["colors"]
        ),
    }


def recipes_csv(job_dir: Path, config: JobConfig) -> str:
    fields = _DEVICE_FIELDS.get(config.chart.colorspace, [])
    out = io.StringIO()
    w = csv_module.writer(out)
    w.writerow(
        ["Name", *[f.split("_")[1] + " %" for f in fields], "Target L*", "Target a*", "Target b*",
         "Expected dE2000", "Measured dE2000", "Out of gamut"]
    )
    for c in load(job_dir)["colors"]:
        w.writerow(
            [c["name"], *c.get("device", []), *c["target_lab"], c.get("expected_de", ""),
             c.get("measured_de", ""), "yes" if c.get("expected_de", 0) > OUT_OF_GAMUT_DE else ""]
        )
    return out.getvalue()
