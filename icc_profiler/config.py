"""Job configuration: schema, JSON load/save, validation.

A "job" is one printer+paper(+ink) combination being profiled. Its config
lives at jobs/<job_name>/config.json and is the single source of truth the
CLI and the web UI both read/write.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from icc_profiler.errors import PipelineError

VALID_METHODS = ("scanner", "colorimeter_csv")
VALID_COLORSPACES = ("RGB", "CMYK", "GRAY")


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
    extra_targen_args: list[str] = field(default_factory=list)
    extra_printtarg_args: list[str] = field(default_factory=list)


@dataclass
class ScannerMeasurementConfig:
    it8_reference_scan: str = ""
    it8_cgats_reference_file: str = ""
    it8_cht_file: str = ""
    test_chart_scan: str = ""


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
        chart = ChartConfig(**data.pop("chart", {}))
        measurement_data = dict(data.pop("measurement", {}))
        scanner = ScannerMeasurementConfig(**measurement_data.pop("scanner", {}))
        colorimeter_csv = ColorimeterCsvMeasurementConfig(
            **measurement_data.pop("colorimeter_csv", {})
        )
        measurement = MeasurementConfig(
            scanner=scanner, colorimeter_csv=colorimeter_csv, **measurement_data
        )
        output = OutputConfig(**data.pop("output", {}))
        argyll = ArgyllConfig(**data.pop("argyll", {}))
        return cls(chart=chart, measurement=measurement, output=output, argyll=argyll, **data)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))

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
        if self.chart.colorspace not in VALID_COLORSPACES:
            problems.append(
                f"chart.colorspace must be one of {VALID_COLORSPACES}, got {self.chart.colorspace!r}"
            )
        if self.chart.patch_count < 12:
            problems.append("chart.patch_count must be at least 12 for a usable profile")
        if self.measurement.method not in VALID_METHODS:
            problems.append(
                f"measurement.method must be one of {VALID_METHODS}, got {self.measurement.method!r}"
            )
        if problems:
            raise PipelineError(
                f"Job config for {self.job_name!r} is invalid:\n- " + "\n- ".join(problems)
            )
