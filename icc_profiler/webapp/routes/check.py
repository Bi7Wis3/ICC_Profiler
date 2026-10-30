from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, UploadFile

from icc_profiler import check_chart
from icc_profiler.errors import PipelineError
from icc_profiler.webapp.common import job_dir, load_config, redirect_to_job, require_idle
from icc_profiler.webapp.routes.chart import pdf_response, preview_png

router = APIRouter()


@router.post("/jobs/{job_name}/check/generate")
def generate_check_chart(job_name: str, patch_count: int = Form(check_chart.DEFAULT_PATCH_COUNT)):
    config = load_config(job_name)
    try:
        require_idle(job_name)
        check_chart.generate(job_dir(job_name), config, patch_count)
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    return redirect_to_job(job_name, True, "Check chart ready. Print it the same way as the main chart.")


@router.get("/jobs/{job_name}/check/preview/{filename}")
def check_chart_preview(job_name: str, filename: str):
    jd = job_dir(job_name)
    pages = {p.name: p for p in check_chart.chart_tiffs(jd)}
    if filename not in pages:
        raise HTTPException(404, "Chart image not found")
    return preview_png(pages[filename])


@router.get("/jobs/{job_name}/check/download.pdf")
def check_chart_pdf(job_name: str):
    pages = check_chart.chart_tiffs(job_dir(job_name))
    if not pages:
        raise HTTPException(404, "No check chart made yet for this job")
    return pdf_response(pages, f"{job_name}_check_chart.pdf")


@router.post("/jobs/{job_name}/check/upload")
async def upload_check_export(job_name: str, file: UploadFile):
    config = load_config(job_name)
    try:
        require_idle(job_name)
        check_chart.save_export(job_dir(job_name), config, await file.read(), Path(file.filename or "").suffix)
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    return redirect_to_job(job_name, True, "Check chart readings uploaded. Click \"Check the profile\".")


@router.post("/jobs/{job_name}/check/run")
def run_check(job_name: str):
    config = load_config(job_name)
    try:
        require_idle(job_name)
        result = check_chart.run(job_dir(job_name), config)
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    return redirect_to_job(
        job_name,
        True,
        f"Check done: average ΔE {float(result['average_de'] or 0):.2f}, "
        f"largest {float(result['maximum_de'] or 0):.2f} on {result['patch_count']} new patches.",
    )
