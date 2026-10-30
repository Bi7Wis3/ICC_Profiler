"""Job configuration: schema, JSON load/save, validation.

A "job" is one printer+paper(+ink) combination being profiled. Its config
lives at jobs/<job_name>/config.json and is the single source of truth the
CLI and the web UI both read/write.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from icc_profiler.errors import PipelineError
from icc_profiler.names import is_safe_name

VALID_METHODS = ("scanner", "colorimeter_csv")
VALID_COLORSPACES = ("RGB", "CMYK", "GRAY")
VALID_QUALITIES = ("low", "medium", "high", "ultra")

# Illuminant/observer the colorimeter's L*a*b* readings were taken under.
# ArgyllCMS and ICC profiles assume D50 / 2 degree; anything else is either
# converted (D65/2 -> D50/2 via Bradford chromatic adaptation, an accepted
# approximation) or refused (a 10 degree observer can't be converted to
# 2 degree without spectral data). "" means "not confirmed yet", which
# blocks import-measurements until the user picks one.
LAB_REFERENCES = {
    "D50_2": "D50 / 2\u00b0 (ICC standard, used as-is)",
    "D65_2": "D65 / 2\u00b0 (converted to D50 with Bradford adaptation)",
}


@dataclass
class ChartConfig:
    colorspace: str = "RGB"
    patch_count: int = 250
    grey_steps: int = 16
    page_size: str = "A4"
    # printtarg -i value; "SS" (SpectroScan layout) confirmed to work for
    # scanner-read charts when combined with -s (see stages.generate_chart).
    reader_type: str = "SS"
    raster_dpi: int = 300  # printtarg -t<dpi>: resolution of the printable TIFF
    # Minimum patch width/height in mm, e.g. to comfortably clear a
    # colorimeter's measurement aperture. None = use printtarg's natural
    # size for the given patch count/page size. When set, generate_chart
    # runs printtarg twice: once to discover the natural size from the
    # resulting .cht, then again with -a<scale> computed to reach this
    # minimum (confirmed empirically: printtarg's -a scales patch size
    # uniformly, and a chart whose natural patches happened to be square
    # produced exactly the requested size after scaling).
    min_patch_size_mm: float | None = None
    # Total ink limit in percent (CMYK only), e.g. 300. Passed to targen -l
    # so the chart never asks for more ink than the paper can take, and to
    # colprof -l so the profile respects the same limit. None = no limit.
    ink_limit: float | None = None
    extra_targen_args: list[str] = field(default_factory=list)
    extra_printtarg_args: list[str] = field(default_factory=list)


@dataclass
class ScannerMeasurementConfig:
    it8_reference_scan: str = ""
    it8_cgats_reference_file: str = ""
    it8_cht_file: str = ""
    test_chart_scan: str = ""
    # colprof -q for the scanner correction profile. Kept separate from
    # output.quality: a shaper+matrix input profile gains little from
    # high/ultra, and medium keeps this step fast.
    profile_quality: str = "medium"


@dataclass
class ColorimeterCsvMeasurementConfig:
    csv_file: str = ""
    # Maps our logical field names to this device's actual CSV column
    # headers. Confirm/adjust against a real DS-173 export -- do not trust
    # these defaults until verified.
    column_map: dict = field(
        default_factory=lambda: {
            "patch_id": "Name",
            "L": "L*",
            "a": "a*",
            "b": "b*",
        }
    )
    lab_reference: str = ""  # a key of LAB_REFERENCES, or "" = not confirmed
    # "name": each reading is named after its printed patch label (A14).
    # "order": readings are in measuring order (A1, A2, ... A27, B1, ...)
    # and their names are ignored -- for software like 3nh SQCX that names
    # readings Sample1, Sample2, ... with no way to rename them.
    match_by: str = "name"
    # Which specular mode to use when the export has a column for it (SQCX
    # "Specular Component"). SCE matches the 45/0 instruments printer
    # profiles are normally measured with; "" = don't filter.
    specular: str = "SCE"
    # Rows to skip by name prefix, e.g. SQCX's "Standard1" reference row.
    ignore_name_prefixes: list[str] = field(default_factory=lambda: ["Standard"])


@dataclass
class MeasurementConfig:
    method: str = "scanner"
    scanner: ScannerMeasurementConfig = field(default_factory=ScannerMeasurementConfig)
    colorimeter_csv: ColorimeterCsvMeasurementConfig = field(
        default_factory=ColorimeterCsvMeasurementConfig
    )


@dataclass
class OutputConfig:
    profile_description: str = ""
    copyright: str = ""
    quality: str = "medium"  # low|medium|high|ultra -> colprof -q l|m|h|u
    algorithm: str = "auto"  # colprof -a value; verify against `colprof -?`
    black_ink_limit: float | None = None  # colprof -L, percent (CMYK only)
    extra_colprof_args: list[str] = field(default_factory=list)


@dataclass
class ArgyllConfig:
    bin_dir: str | None = None


@dataclass
class JobConfig:
    job_name: str
    printer_name: str = ""
    printer_notes: str = ""
    paper_name: str = ""
    paper_notes: str = ""
    chart: ChartConfig = field(default_factory=ChartConfig)
    measurement: MeasurementConfig = field(default_factory=MeasurementConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    argyll: ArgyllConfig = field(default_factory=ArgyllConfig)

    # -- (de)serialization -------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobConfig":
        data = dict(data)
        chart = _build(ChartConfig, data.pop("chart", {}))
        measurement_data = dict(data.pop("measurement", {}))
        scanner = _build(ScannerMeasurementConfig, measurement_data.pop("scanner", {}))
        colorimeter_csv = _build(
            ColorimeterCsvMeasurementConfig, measurement_data.pop("colorimeter_csv", {})
        )
        measurement = _build(
            MeasurementConfig,
            {**measurement_data, "scanner": scanner, "colorimeter_csv": colorimeter_csv},
        )
        output = _build(OutputConfig, data.pop("output", {}))
        argyll = _build(ArgyllConfig, data.pop("argyll", {}))
        return _build(
            cls, {**data, "chart": chart, "measurement": measurement, "output": output, "argyll": argyll}
        )

    def save(self, path: Path) -> None:
        """Write config.json. Measurement input paths are re-stored relative
        to the job folder (path.parent) where possible -- see store_path()."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.to_dict()
        job_dir = path.parent
        for section, key in _PATH_FIELDS:
            value = data["measurement"][section][key]
            if value and (Path(value).is_absolute() or _is_windows_abs(value)):
                resolved = resolve_path(value, job_dir)
                if resolved.exists():
                    data["measurement"][section][key] = store_path(resolved, job_dir)
        path.write_text(json.dumps(data, indent=2))

    def input_path(self, value: str, job_dir: Path) -> Path:
        """Shorthand for resolve_path() on one of this config's path fields."""
        return resolve_path(value, job_dir)

    @classmethod
    def load(cls, path: Path) -> "JobConfig":
        if not path.exists():
            raise PipelineError(f"Job config not found: {path}")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            raise PipelineError(f"Job config at {path} is not valid JSON: {exc}") from exc
        return cls.from_dict(data)

    # -- validation ----------------------------------------------------------

    def validate(self) -> None:
        problems = []
        if not self.job_name or not self.job_name.strip():
            problems.append("job_name must not be empty")
        elif not is_safe_name(self.job_name):
            problems.append(
                f"job_name {self.job_name!r} may only use letters, digits, '_', '-' and '.', "
                f"and must not start with a dot (it becomes a folder name and part of URLs)"
            )
        if self.chart.colorspace not in VALID_COLORSPACES:
            problems.append(
                f"chart.colorspace must be one of {VALID_COLORSPACES}, got {self.chart.colorspace!r}"
            )
        if self.chart.patch_count < 12:
            problems.append("chart.patch_count must be at least 12 for a usable profile")
        if self.output.quality not in VALID_QUALITIES:
            problems.append(f"output.quality must be one of {VALID_QUALITIES}")
        if self.measurement.scanner.profile_quality not in VALID_QUALITIES:
            problems.append(f"measurement.scanner.profile_quality must be one of {VALID_QUALITIES}")
        if self.chart.ink_limit is not None and not (0 < self.chart.ink_limit <= 400):
            problems.append("chart.ink_limit must be between 0 and 400 (percent total ink)")
        if self.output.black_ink_limit is not None and not (0 < self.output.black_ink_limit <= 100):
            problems.append("output.black_ink_limit must be between 0 and 100 (percent)")
        lab_ref = self.measurement.colorimeter_csv.lab_reference
        if lab_ref and lab_ref not in LAB_REFERENCES:
            problems.append(
                f"measurement.colorimeter_csv.lab_reference must be one of "
                f"{tuple(LAB_REFERENCES)} (or empty), got {lab_ref!r}"
            )
        csv_cfg = self.measurement.colorimeter_csv
        if csv_cfg.match_by not in ("name", "order"):
            problems.append("measurement.colorimeter_csv.match_by must be 'name' or 'order'")
        if csv_cfg.specular not in ("", "SCE", "SCI"):
            problems.append("measurement.colorimeter_csv.specular must be 'SCE', 'SCI' or empty")
        if self.measurement.method not in VALID_METHODS:
            problems.append(
                f"measurement.method must be one of {VALID_METHODS}, got {self.measurement.method!r}"
            )
        if problems:
            raise PipelineError(
                f"Job config for {self.job_name!r} is invalid:\n- " + "\n- ".join(problems)
            )


_PATH_FIELDS = [
    ("scanner", "it8_reference_scan"),
    ("scanner", "it8_cgats_reference_file"),
    ("scanner", "it8_cht_file"),
    ("scanner", "test_chart_scan"),
    ("colorimeter_csv", "csv_file"),
]


def _build(cls, data: dict):
    """Construct a config dataclass, ignoring keys it doesn't know about, so
    a config.json written by a newer/older version still loads."""
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in known})


# -- paths stored in config.json ----------------------------------------------
#
# Paths to measurement inputs are stored relative to the job folder where
# possible (e.g. "02_measurements/test_chart_scan.tif", or
# "../../shared/it8_references/it8.cht"), so a job keeps working if the
# project folder is moved or run from native Windows instead of WSL. Older
# configs hold absolute paths like /mnt/c/.../jobs/<job>/...; resolve_path()
# re-bases those onto the current project when the original no longer exists.


def store_path(path: Path, job_dir: Path) -> str:
    """The string to save in config.json for `path`."""
    path = Path(path).resolve()
    project_root = job_dir.resolve().parent.parent
    if path == project_root or project_root in path.parents:
        return Path(os.path.relpath(path, job_dir.resolve())).as_posix()
    return str(path)


def resolve_path(value: str, job_dir: Path) -> Path:
    """Turn a path from config.json back into a usable absolute path.

    Relative paths are tried against the job folder first, then the current
    directory (older CLI configs were written relative to wherever the
    command ran). Absolute paths that no longer exist are re-based onto this
    project by their jobs/<job>/... or shared/... tail.
    """
    if not value or not value.strip():
        return Path(value or "")
    p = Path(value)
    job_dir = job_dir.resolve()
    if not p.is_absolute() and not _is_windows_abs(value):
        for candidate in (job_dir / p, Path.cwd() / p):
            if candidate.exists():
                return candidate.resolve()
        return (job_dir / p).resolve()
    if p.exists():
        return p
    parts = value.replace("\\", "/").split("/")
    project_root = job_dir.parent.parent
    for anchor in ("jobs", "shared"):
        if anchor in parts:
            idx = len(parts) - 1 - parts[::-1].index(anchor)
            candidate = project_root.joinpath(*parts[idx:])
            if candidate.exists():
                return candidate
    return p


def _is_windows_abs(value: str) -> bool:
    """True for a Windows absolute path like C:\\x or C:/x, on any OS."""
    return len(value) > 2 and value[1] == ":" and value[2] in "\\/"
