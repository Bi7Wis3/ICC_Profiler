from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, RedirectResponse

from icc_profiler import stages
from icc_profiler.errors import PipelineError
from icc_profiler.webapp.common import job_dir, load_config

router = APIRouter()


def _redirect_with_message(job_name: str, ok: bool, message: str) -> RedirectResponse:
    status = "ok" if ok else "error"
    return RedirectResponse(
        f"/jobs/{job_name}?{status}={quote(message)}", status_code=303
    )


@router.post("/jobs/{job_name}/stage/{stage_name}")
def run_single_stage(job_name: str, stage_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    func = stages.STAGE_FUNCS.get(stage_name)
    if func is None:
        return _redirect_with_message(job_name, False, f"Unknown stage: {stage_name}")
    try:
        func(jd, config)
    except PipelineError as exc:
        return _redirect_with_message(job_name, False, str(exc))
    return _redirect_with_message(job_name, True, f"Stage '{stage_name}' complete.")


@router.post("/jobs/{job_name}/run")
def run_all_remaining(job_name: str, force: bool = False):
    config = load_config(job_name)
    jd = job_dir(job_name)
    try:
        result = stages.run_all(jd, config, force=force)
    except PipelineError as exc:
        return _redirect_with_message(job_name, False, str(exc))
    if result["stopped_at"]:
        msg = f"Waiting before '{result['stopped_at']}': {result['message']}"
        return _redirect_with_message(job_name, True, msg)
    return _redirect_with_message(job_name, True, result["message"])


@router.get("/jobs/{job_name}/logs/{stage_name}", response_class=PlainTextResponse)
def stage_log(job_name: str, stage_name: str):
    log_path = job_dir(job_name) / "logs" / f"{stage_name}.log"
    if not log_path.exists():
        return PlainTextResponse("(no log yet)", status_code=404)
    return PlainTextResponse(log_path.read_text())
