"""Shared helpers for the web UI routes: paths, templates, job listing."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from icc_profiler import state
from icc_profiler.config import JobConfig

PROJECT_ROOT = Path(__file__).resolve().parents[2]
JOBS_ROOT = PROJECT_ROOT / "jobs"

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def job_dir(job_name: str) -> Path:
    return JOBS_ROOT / job_name


def load_config(job_name: str) -> JobConfig:
    return JobConfig.load(job_dir(job_name) / "config.json")


def list_jobs() -> list[dict]:
    jobs = []
    if not JOBS_ROOT.exists():
        return jobs
    for d in sorted(JOBS_ROOT.iterdir()):
        config_path = d / "config.json"
        if not d.is_dir() or not config_path.exists():
            continue
        try:
            config = JobConfig.load(config_path)
        except Exception:  # noqa: BLE001 - skip unreadable job dirs in the listing
            continue
        st = state.load_state(d)
        order = state.stage_order(config.measurement.method)
        done_count = sum(1 for s in order if state.is_done(st, s))
        jobs.append(
            {
                "name": config.job_name,
                "printer_name": config.printer_name,
                "paper_name": config.paper_name,
                "method": config.measurement.method,
                "done_count": done_count,
                "total_stages": len(order),
                "complete": done_count == len(order),
            }
        )
    return jobs
