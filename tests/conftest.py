"""Shared fixtures: temporary jobs and a fake ArgyllCMS.

The fake stands in for argyll_tools.run so the pipeline logic (stage
order, invalidation, chart binding, locking) can be tested without
ArgyllCMS installed. It writes the files each real tool would produce,
using real targen/printtarg 3.1.0 output from tests/data/.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from icc_profiler import argyll_tools, stages
from icc_profiler.config import JobConfig

DATA = Path(__file__).parent / "data"


def pytest_configure(config):
    # The web app reads its jobs folder once, at import; point it at a
    # throwaway folder so tests never touch the real jobs/.
    root = Path(tempfile.mkdtemp(prefix="icc_profiler_web_")) / "jobs"
    root.mkdir()
    os.environ["ICC_PROFILER_JOBS_ROOT"] = str(root)

FAKE_CHT = "BOXES 1\n  X 01 01 A A 7.0 7.0 10 10 0 0\n"


class FakeArgyll:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.generation = 0
        self.ti1_source = DATA / "rgb20.ti1"
        self.ti2_source = DATA / "rgb20.ti2"

    def __call__(self, argv, *, log_file=None, cwd=None, timeout=None, input=None):
        self.calls.append(list(argv))
        tool = Path(argv[0]).name
        if tool == "targen":
            shutil.copyfile(self.ti1_source, argv[-1] + ".ti1")
        elif tool == "printtarg":
            self.generation += 1
            base = argv[-1]
            # Each run gets a distinct .ti2, as real printtarg does (its
            # CREATED line holds the time).
            text = self.ti2_source.read_text().replace(
                "CREATED", f'GENERATION "{self.generation}"\nCREATED', 1
            )
            Path(base + ".ti2").write_text(text)
            Path(base + ".cht").write_text(FAKE_CHT)
        elif tool == "scanin":
            if "-c" in argv:
                pbase = argv[-1]
                shutil.copyfile(DATA / "measured_rgb20.ti3", pbase + ".ti3")
            else:
                out = argv[argv.index("-O") + 1]
                Path(out).write_text("CTI3\n")
        elif tool == "colprof":
            Path(argv[-1] + ".icc").write_bytes(b"fake icc")
        elif tool == "xicclu":
            # A toy 3-channel profile: channel 1 = darkness, 2 = redness,
            # 3 = yellowness.
            # Negative a*/b* can't be printed, which makes those colors
            # "out of gamut".
            out = []
            for line in (input or "").splitlines():
                v = [float(x) for x in line.split()]
                if "-fif" in argv:
                    out.append(f"{(100 - v[0]) / 100:.6f} {max(v[1], 0) / 128:.6f} {max(v[2], 0) / 128:.6f}")
                else:
                    out.append(f"{100 - 100 * v[0]:.6f} {128 * v[1]:.6f} {128 * v[2]:.6f}")
            return subprocess.CompletedProcess(argv, 0, "\n".join(out) + "\n", "")
        elif tool == "profcheck":
            return subprocess.CompletedProcess(
                argv, 0, "Profile check complete, errors: max. = 1.0, avg. = 0.5, RMS = 0.6\n", ""
            )
        if log_file is not None:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            with log_file.open("a") as f:
                f.write("$ " + " ".join(argv) + "\n")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def tools_called(self) -> list[str]:
        return [Path(c[0]).name for c in self.calls]


@pytest.fixture
def fake_argyll(monkeypatch):
    fake = FakeArgyll()
    monkeypatch.setattr(argyll_tools, "run", fake)
    monkeypatch.setattr(argyll_tools, "find_bin", lambda tool, bin_dir=None: tool)
    return fake


@pytest.fixture
def jobs_root(tmp_path) -> Path:
    root = tmp_path / "project" / "jobs"
    root.mkdir(parents=True)
    return root


def make_config(name: str = "job1", method: str = "colorimeter_csv", colorspace: str = "RGB") -> JobConfig:
    data = {
        "job_name": name,
        "chart": {"colorspace": colorspace, "patch_count": 20, "grey_steps": 4},
        "measurement": {"method": method},
    }
    return JobConfig.from_dict(data)


@pytest.fixture
def colorimeter_job(jobs_root, fake_argyll):
    """An initialized colorimeter job with its chart generated (fake Argyll)."""
    config = make_config()
    job_dir = stages.init_job(jobs_root, config)
    stages.run_stage(job_dir, config, "generate-chart")
    return job_dir, config


def write_csv_for_chart(job_dir: Path, config: JobConfig, dest: Path | None = None, text_fn=None) -> Path:
    """Write a valid CSV with a plausible reading for every patch label."""
    from icc_profiler import cgats

    table = cgats.read(stages.ti2_path(job_dir, config))
    lines = ["Name,L*,a*,b*"]
    for i, row in enumerate(table.rows):
        lines.append(f"{row['SAMPLE_LOC']},{20 + i},{i - 10},{10 - i}")
    text = "\n".join(lines) + "\n"
    if text_fn:
        text = text_fn(text)
    dest = dest or stages.measurements_in_dir(job_dir) / "colorimeter_export.csv"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text) if isinstance(text, str) else dest.write_bytes(text)
    return dest
