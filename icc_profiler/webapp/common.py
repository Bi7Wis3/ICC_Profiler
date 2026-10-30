"""Shared helpers for the web UI routes: paths, templates, job listing."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from icc_profiler import state
from icc_profiler.config import JobConfig
from icc_profiler.errors import PipelineError
from icc_profiler.names import ensure_within, is_safe_name
from icc_profiler.webapp import tasks
from icc_profiler.webapp.branding import load_branding

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# ICC_PROFILER_JOBS_ROOT lets tests (or a second setup) point the UI at
# another jobs folder; shared/it8_references lives next to it.
JOBS_ROOT = Path(os.environ.get("ICC_PROFILER_JOBS_ROOT") or PROJECT_ROOT / "jobs").resolve()

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.globals["branding"] = load_branding()


def job_dir(job_name: str) -> Path:
    """The folder for job_name, refusing anything that isn't a plain safe
    name or that would resolve outside jobs/ (e.g. via a symlink)."""
    if not is_safe_name(job_name):
        raise HTTPException(404, "No such job")
    try:
        return ensure_within(JOBS_ROOT / job_name, JOBS_ROOT)
    except PipelineError as exc:
        raise HTTPException(404, "No such job") from exc


def load_config(job_name: str) -> JobConfig:
    path = job_dir(job_name) / "config.json"
    if not path.is_file():
        raise HTTPException(404, f"No job named {job_name!r}")
    return JobConfig.load(path)


def redirect_to_job(job_name: str, ok: bool, message: str) -> RedirectResponse:
    status = "ok" if ok else "error"
    return RedirectResponse(f"/jobs/{job_name}?{status}={quote(message)}", status_code=303)


def require_idle(job_name: str) -> None:
    """Refuse to change a job's inputs while one of its stages is running."""
    if tasks.is_busy(job_name):
        raise PipelineError(
            "A stage is running for this job right now. Wait for it to finish before changing anything."
        )


def cleared_note(cleared: list[str]) -> str:
    if not cleared:
        return ""
    return f" Marked out of date, re-run: {', '.join(cleared)}."


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
        order = state.stage_order(config.measurement.method)
        st = state.load_state(d, order)
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
