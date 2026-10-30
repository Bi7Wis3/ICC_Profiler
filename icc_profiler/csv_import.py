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
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from icc_profiler.cgats import CgatsTable
from icc_profiler.cgats import read as cgats_read
from icc_profiler.color_convert import lab_d65_to_d50
from icc_profiler.errors import PipelineError

DEFAULT_COLUMN_MAP = {"patch_id": "Name", "L": "L*", "a": "a*", "b": "b*"}

# Plausible ranges for a reflective print measurement. L* is 0-100 by
# definition; a*/b* of real printed colors stay well inside +/-150. Values
# outside these are a mis-mapped column, a unit mix-up or a typo, and would
# quietly wreck the profile if passed to colprof.
L_RANGE = (0.0, 100.0)
AB_LIMIT = 150.0

_CANDIDATE_DELIMITERS = ",;\t"


def parse_number(text: str) -> float:
    """float() that also accepts a decimal comma ("52,3") and surrounding
    whitespace, as EU-locale exports write numbers."""
    cleaned = str(text).strip().replace("\u00a0", "").replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")
    return float(cleaned)


def parse_lab(L: str, a: str, b: str, where: str = "") -> tuple[float, float, float]:
    """Parse and range-check one L*a*b* reading. Raises PipelineError."""
    prefix = f"{where}: " if where else ""
    try:
        values = parse_number(L), parse_number(a), parse_number(b)
    except (TypeError, ValueError) as exc:
        raise PipelineError(
            f"{prefix}L*/a*/b* must be numbers (got L={L!r}, a={a!r}, b={b!r})"
        ) from exc
    L_f, a_f, b_f = values
    if not (L_RANGE[0] <= L_f <= L_RANGE[1]):
        raise PipelineError(f"{prefix}L*={L_f:g} is outside {L_RANGE[0]:g}-{L_RANGE[1]:g}")
    if abs(a_f) > AB_LIMIT or abs(b_f) > AB_LIMIT:
        raise PipelineError(
            f"{prefix}a*={a_f:g} / b*={b_f:g} is outside \u00b1{AB_LIMIT:g}, which no printed "
            f"color reaches -- check the column mapping and units"
        )
    return values


def _decode(raw: bytes) -> str:
    """Decode a vendor CSV export: UTF-8 (with or without BOM), UTF-16 with
    BOM (some Windows apps), else Windows-1252 as a last resort."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv_module.Sniffer().sniff(sample, delimiters=_CANDIDATE_DELIMITERS).delimiter
    except csv_module.Error:
        header = text.splitlines()[0] if text else ""
        counts = {d: header.count(d) for d in _CANDIDATE_DELIMITERS}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def _match_header(wanted: str, headers: list[str]) -> str | None:
    if wanted in headers:
        return wanted
    folded = {h.strip().casefold(): h for h in headers}
    return folded.get(wanted.strip().casefold())


def loc_sort_key(loc: str) -> tuple:
    """Physical reading order of a printed patch label: strip letters first,
    then the patch number as a number -- A1, A2, ... A27, B1, ... -- i.e.
    down each printed column in turn. (A plain string sort would put A10
    before A2.)"""
    m = re.match(r"^([A-Za-z]*)(\d*)(.*)$", loc)
    letters, digits, rest = m.groups() if m else (loc, "", "")
    return (len(letters), letters.upper(), int(digits) if digits else -1, rest)


# -- reading the export file --------------------------------------------------
#
# Vendor software exports CSV or Excel. 3nh's SQCX (PS110) exports an .xlsx
# with columns: Color Simulation | Name | Specular Component | Date Time |
# Ill/Obs | L* | a* | b* | dL* ... | Judgement -- confirmed against a real
# export. Its readings are named Sample1, Sample2, ... (matched by order,
# see merge()), it lists a "Standard<n>" reference row first, it can hold
# both an SCI and an SCE row per reading, and some numbers are stored as
# text. Ill/Obs reads e.g. "D50/2\u00b0".

_XLSX_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

SPECULAR_HEADERS = ("Specular Component", "SCI/SCE", "Specular", "Mode")
ILLUMINANT_HEADERS = ("Ill/Obs", "Illuminant/Observer", "Illuminant")


def _xlsx_rows(path: Path) -> list[list[str]]:
    """Cell text of the first worksheet of an .xlsx, using only the standard
    library (no openpyxl dependency). Numbers come back as their stored text."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise PipelineError(f"{path.name} is not a valid .xlsx file") from exc
    with zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", _XLSX_NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{_XLSX_NS['m']}}}t")))
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        first = workbook.find("m:sheets/m:sheet", _XLSX_NS)
        target = "xl/worksheets/sheet1.xml"
        if first is not None and "xl/_rels/workbook.xml.rels" in zf.namelist():
            rel_id = first.get(f"{{{_REL_NS}}}id")
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            for rel in rels:
                if rel.get("Id") == rel_id:
                    t = rel.get("Target", "")
                    target = t.lstrip("/") if t.startswith("/") else "xl/" + t
        sheet = ET.fromstring(zf.read(target))

    rows: list[list[str]] = []
    for row in sheet.iter(f"{{{_XLSX_NS['m']}}}row"):
        cells: dict[int, str] = {}
        for c in row.findall("m:c", _XLSX_NS):
            ref = c.get("r", "")
            col_letters = "".join(ch for ch in ref if ch.isalpha())
            col = 0
            for ch in col_letters:
                col = col * 26 + (ord(ch.upper()) - 64)
            col = (col - 1) if col_letters else len(cells)
            kind = c.get("t")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{{{_XLSX_NS['m']}}}t"))
            else:
                v = c.find("m:v", _XLSX_NS)
                text = v.text if v is not None and v.text is not None else ""
                if kind == "s" and text:
                    text = shared[int(text)]
            cells[col] = text
        if cells:
            rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
    return rows


def _load_table(path: Path) -> tuple[list[str], list[tuple[int, dict]]]:
    """Header names plus (row number, {header: text}) for each data row."""
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        raw = _xlsx_rows(path)
        if not raw:
            raise PipelineError(f"{path.name} is empty")
        headers = [h.strip() for h in raw[0]]
        out = []
        for n, values in enumerate(raw[1:], start=2):
            out.append((n, {h: (values[i] if i < len(values) else "") for i, h in enumerate(headers)}))
        return headers, out

    text = _decode(path.read_bytes())
    if not text.strip():
        raise PipelineError(f"{path} is empty")
    delimiter = _sniff_delimiter(text)
    reader = csv_module.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    if reader.fieldnames is None:
        raise PipelineError(f"{path} has no header row")
    headers = [h.strip() if h else "" for h in reader.fieldnames]
    reader.fieldnames = headers
    out = []
    for n, row in enumerate(reader, start=2):
        if row.get(None):
            name = next(iter(row.values()), "")
            raise PipelineError(
                f"{path.name} line {n} ({name}) has more values than there are "
                f"column headers. This usually means decimal commas (52,3) in a comma-separated "
                f"file; export with ';' as the separator or '.' as the decimal mark."
            )
        out.append((n, row))
    return headers, out


def _first_header(candidates: tuple[str, ...], headers: list[str]) -> str | None:
    for c in candidates:
        found = _match_header(c, headers)
        if found:
            return found
    return None


def _normalize_ill_obs(text: str) -> str:
    """'D50/2\u00b0', 'D50 / 2 deg', 'd50-2' -> 'D50_2'."""
    t = re.sub(r"[\s\u00b0]|deg", "", text, flags=re.IGNORECASE).upper()
    t = re.sub(r"[/\-_,]+", "_", t)
    return t


def read_readings(
    path: Path,
    column_map: dict,
    *,
    specular: str = "",
    ignore_prefixes: list[str] | tuple[str, ...] = (),
    lab_reference: str = "",
) -> list[tuple[str, tuple[float, float, float]]]:
    """(name, Lab) for each usable reading, in file order.

    specular ("SCE"/"SCI"): if the file has a specular-component column,
    keep only rows in that mode. ignore_prefixes: drop rows whose name
    starts with one of these (e.g. SQCX's "Standard" reference rows).
    lab_reference: if the file states its illuminant/observer, it must match.
    """
    required = ["patch_id", "L", "a", "b"]
    missing_keys = [k for k in required if k not in column_map]
    if missing_keys:
        raise PipelineError(
            f"csv_import column_map is missing required keys {missing_keys}; "
            f"needs an entry for each of {required}"
        )
    headers, rows = _load_table(path)
    columns = {k: _match_header(column_map[k], headers) for k in required}
    missing_headers = [column_map[k] for k in required if columns[k] is None]
    if missing_headers:
        raise PipelineError(
            f"{path} is missing expected column(s) {missing_headers} "
            f"(have: {headers}). Adjust measurement.colorimeter_csv."
            f"column_map to match this export's real headers."
        )
    spec_col = _first_header(SPECULAR_HEADERS, headers) if specular else None
    ill_col = _first_header(ILLUMINANT_HEADERS, headers) if lab_reference else None

    readings = []
    modes_seen: set[str] = set()
    for line_no, row in rows:
        name = (row.get(columns["patch_id"]) or "").strip()
        if not name or any(name.startswith(p) for p in ignore_prefixes):
            continue
        if spec_col:
            mode = (row.get(spec_col) or "").strip().upper()
            modes_seen.add(mode)
            if mode and mode != specular.upper():
                continue
        where = f"{path.name} line {line_no} ({name})"
        if ill_col:
            stated = (row.get(ill_col) or "").strip()
            if stated and _normalize_ill_obs(stated) != lab_reference:
                raise PipelineError(
                    f"{where} was measured under {stated}, but this job is set to "
                    f"{lab_reference.replace('_', '/')}\u00b0. Change the illuminant/observer in the "
                    f"colorimeter software (or the setting on the job page) and export again."
                )
        lab = parse_lab(
            row.get(columns["L"]) or "",
            row.get(columns["a"]) or "",
            row.get(columns["b"]) or "",
            where=where,
        )
        readings.append((name, lab))
    if spec_col and not readings and modes_seen - {"", specular.upper()}:
        raise PipelineError(
            f"{path.name} has no {specular.upper()} readings, only "
            f"{', '.join(sorted(m for m in modes_seen if m))}. Switch the colorimeter software "
            f"to {specular.upper()} and export again, or change the SCI/SCE setting on the job page."
        )
    return readings


def _read_csv_rows(csv_path: Path, column_map: dict, **options) -> dict[str, tuple[float, float, float]]:
    by_patch_id: dict[str, tuple[float, float, float]] = {}
    for name, lab in read_readings(csv_path, column_map, **options):
        if name in by_patch_id:
            raise PipelineError(
                f"{csv_path} has more than one row for patch id {name!r} "
                f"-- duplicate or mis-named reading"
            )
        by_patch_id[name] = lab
    return by_patch_id


_FALLBACK_COLOR_REP = {"RGB_": "iRGB", "CMYK_": "CMYK", "GRAY_": "K"}


def _device_color_rep(ti2: CgatsTable, device_fields: list[str]) -> str:
    for line in ti2.preamble:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] == "COLOR_REP":
            return parts[1].strip().strip('"')
    for prefix, rep in _FALLBACK_COLOR_REP.items():
        if any(f.startswith(prefix) for f in device_fields):
            return rep
    raise PipelineError(f"Can't tell the chart's device colorspace from fields {device_fields}")


def merge(
    ti2_path: Path,
    csv_path: Path,
    column_map: dict | None = None,
    lab_reference: str = "D50_2",
    match_by: str = "name",
    specular: str = "",
    ignore_prefixes: list[str] | tuple[str, ...] = (),
) -> CgatsTable:
    """Build a .ti3 from the chart's .ti2 plus the colorimeter CSV/XLSX.

    lab_reference says what the readings are relative to (see
    config.LAB_REFERENCES). "D50_2" is written as-is; "D65_2" is converted
    to D50 first, since ArgyllCMS/ICC treat .ti3 LAB as D50 / 2 degree.

    match_by "name": each reading is named after its printed patch label.
    match_by "order": readings are in measuring order (names like Sample1,
    Sample2 are ignored) and are assigned to the patches in physical order,
    loc_sort_key: A1, A2, ... A27, B1, ...
    """
    column_map = column_map or DEFAULT_COLUMN_MAP
    if lab_reference not in ("D50_2", "D65_2"):
        raise PipelineError(f"Unsupported Lab illuminant/observer {lab_reference!r}")
    ti2 = cgats_read(ti2_path)
    if "SAMPLE_LOC" not in ti2.fields:
        raise PipelineError(f"{ti2_path} has no SAMPLE_LOC field; has {ti2.fields}")

    options = {"specular": specular, "ignore_prefixes": ignore_prefixes, "lab_reference": lab_reference}
    if match_by == "order":
        readings = read_readings(csv_path, column_map, **options)
        locs = sorted((row["SAMPLE_LOC"] for row in ti2.rows), key=loc_sort_key)
        if len(readings) != len(locs):
            got = len(readings)
            raise PipelineError(
                f"{csv_path.name} has {got} usable reading(s) but the chart has {len(locs)} "
                f"patches. In measuring-order mode every patch must be measured exactly once, "
                f"in order {locs[0]}, {locs[1]}, ... (down each printed column). "
                + (f"The next one to measure would be {locs[got]}." if got < len(locs) else
                   "Delete accidental extra readings in the colorimeter software and export again.")
            )
        measured = {loc: lab for loc, (_, lab) in zip(locs, readings)}
    elif match_by == "name":
        measured = _read_csv_rows(csv_path, column_map, **options)
    else:
        raise PipelineError(f"Unknown match_by {match_by!r}; use 'name' or 'order'")

    expected_locs = {row["SAMPLE_LOC"] for row in ti2.rows}
    missing = sorted(expected_locs - measured.keys(), key=loc_sort_key)
    extra = sorted(measured.keys() - expected_locs, key=loc_sort_key)
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
        if lab_reference == "D65_2":
            L, a, b = lab_d65_to_d50(L, a, b)
        out_row = {"SAMPLE_ID": row["SAMPLE_ID"], **{f: row.get(f, "") for f in device_fields}}
        out_row["LAB_L"] = f"{L:.4f}"
        out_row["LAB_A"] = f"{a:.4f}"
        out_row["LAB_B"] = f"{b:.4f}"
        out_rows.append(out_row)

    dev_rep = _device_color_rep(ti2, device_fields)

    # Preamble tags (DEVICE_CLASS, COLOR_REP) mirror what real scanin -c
    # output uses for a printer-characterization .ti3 (confirmed against
    # ArgyllCMS 3.1.0), substituting LAB for XYZ since the colorimeter
    # reports Lab directly. The device half of COLOR_REP is copied from the
    # chart's own .ti2: printtarg writes "iRGB" for RGB but plain "CMYK"
    # and "K" for CMYK and gray (confirmed), and colprof rejects a made-up
    # "iCMYK_LAB" with "unhandled color representation".
    return CgatsTable(
        file_type="CTI3",
        preamble=[
            'DESCRIPTOR "Argyll Calibration Target chart information 3"',
            'ORIGINATOR "icc_profiler csv_import"',
            'DEVICE_CLASS "OUTPUT"',
            f'COLOR_REP "{dev_rep}_LAB"',
        ],
        fields=out_fields,
        rows=out_rows,
    )
