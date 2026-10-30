from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from icc_profiler import stages
from icc_profiler.webapp import tasks
from icc_profiler.webapp.common import job_dir, load_config, redirect_to_job

router = APIRouter()


def _start(job_name: str, label: str, work) -> object:
    if not tasks.start(job_name, label, work):
        return redirect_to_job(job_name, False, "A stage is already running for this job.")
    return redirect_to_job(job_name, True, f"Started {label}. This page updates when it finishes.")


@router.post("/jobs/{job_name}/stage/{stage_name}")
def run_single_stage(job_name: str, stage_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    if stage_name not in stages.order_for(config):
        return redirect_to_job(job_name, False, f"Unknown stage: {stage_name}")

    def work() -> str:
        # Re-load the config in the worker so it sees the latest saved settings.
        result = stages.run_stage(jd, load_config(job_name), stage_name)
        if stage_name == "verify":
            return f"Stage '{stage_name}' complete. Report: {result}"
        return f"Stage '{stage_name}' complete."

    return _start(job_name, f"'{stage_name}'", work)


@router.post("/jobs/{job_name}/run")
def run_all_remaining(job_name: str, force: bool = Form(False), regenerate_chart: bool = Form(False)):
    load_config(job_name)
    jd = job_dir(job_name)

    def work() -> str:
        result = stages.run_all(jd, load_config(job_name), force=force, regenerate_chart=regenerate_chart)
        ran = f"Ran: {', '.join(result['ran'])}. " if result["ran"] else ""
        if result["stopped_at"]:
            return f"{ran}Waiting before '{result['stopped_at']}': {result['message']}"
        return ran + result["message"]

    return _start(job_name, "all remaining stages", work)


@router.get("/jobs/{job_name}/task")
def task_status(job_name: str):
    job_dir(job_name)
    return JSONResponse(tasks.status(job_name) or {"state": "idle"})


@router.get("/jobs/{job_name}/logs/{stage_name}", response_class=PlainTextResponse)
def stage_log(job_name: str, stage_name: str):
    if stage_name not in stages.STAGE_FUNCS:
        raise HTTPException(404, "Unknown stage")
    log_path = stages.logs_dir(job_dir(job_name)) / f"{stage_name}.log"
    if not log_path.exists():
        return PlainTextResponse("(no log yet)", status_code=404)
    return PlainTextResponse(log_path.read_text(errors="replace"))
