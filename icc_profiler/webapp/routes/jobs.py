from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from icc_profiler import stages, state, target_colors
from icc_profiler.config import ChartConfig, JobConfig, MeasurementConfig, OutputConfig
from icc_profiler.errors import PipelineError
from icc_profiler.webapp.common import JOBS_ROOT, job_dir, list_jobs, load_config, templates
from icc_profiler.webapp.routes.chart import list_chart_tiffs
from icc_profiler.webapp.routes.measurements import (
    list_bundled_cht_templates,
    list_existing_reference_files,
)

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        request, "jobs_list.html", {"jobs": list_jobs()}
    )


@router.get("/jobs/new")
def new_job_form(request: Request):
    return templates.TemplateResponse(request, "job_new.html", {"error": None})


@router.post("/jobs/new")
def create_job(
    request: Request,
    job_name: str = Form(...),
    printer_name: str = Form(""),
    paper_name: str = Form(""),
    method: str = Form("scanner"),
    colorspace: str = Form("RGB"),
    patch_count: int = Form(250),
    grey_steps: int = Form(16),
    page_size: str = Form("A4"),
):
    config = JobConfig(
        job_name=job_name.strip(),
        printer_name=printer_name,
        paper_name=paper_name,
        chart=ChartConfig(
            colorspace=colorspace,
            patch_count=patch_count,
            grey_steps=grey_steps,
            page_size=page_size,
        ),
        measurement=MeasurementConfig(method=method),
        output=OutputConfig(profile_description=f"{printer_name} / {paper_name}"),
    )
    try:
        config.validate()
        stages.init_job(JOBS_ROOT, config)
    except PipelineError as exc:
        return templates.TemplateResponse(
            request, "job_new.html", {"error": str(exc)}, status_code=400
        )
    return RedirectResponse(f"/jobs/{config.job_name}", status_code=303)


@router.get("/jobs/{job_name}")
def job_detail(request: Request, job_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    st = state.load_state(jd)
    order = state.stage_order(config.measurement.method)
    stage_rows = [{"name": s, "done": state.is_done(st, s)} for s in order]
    colors = target_colors.load(jd)
    chart_files = list_chart_tiffs(job_name)
    return templates.TemplateResponse(
        request,
        "job_detail.html",
        {
            "config": config,
            "job_name": job_name,
            "stage_rows": stage_rows,
            "colors": colors,
            "chart_files": chart_files,
            "bundled_cht_templates": list_bundled_cht_templates(),
            "existing_reference_files": list_existing_reference_files(),
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
    )
