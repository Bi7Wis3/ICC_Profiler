from __future__ import annotations

import shutil
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from icc_profiler import stages, state, target_colors
from icc_profiler.config import ChartConfig, JobConfig, MeasurementConfig, OutputConfig
from icc_profiler.errors import PipelineError
from icc_profiler.stages import icc_path
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
        request,
        "jobs_list.html",
        {
            "jobs": list_jobs(),
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
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
    min_patch_size_mm: str = Form(""),
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
            min_patch_size_mm=float(min_patch_size_mm) if min_patch_size_mm.strip() else None,
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


def _unique_job_name(base: str) -> str:
    candidate = base
    n = 2
    while (JOBS_ROOT / candidate).exists():
        candidate = f"{base}_{n}"
        n += 1
    return candidate


# Sane defaults for a colorimeter-CSV job's chart: lower patch count than a
# typical scanner job's, since every patch here means one manual measurement
# rather than being captured all at once in a single scan; and a minimum
# patch size large enough to comfortably clear a handheld colorimeter's
# measurement aperture (10mm confirmed as the right size for the user's
# CHNSpec DS-173).
_COLORIMETER_DEFAULT_PATCH_COUNT = 120
_COLORIMETER_DEFAULT_GREY_STEPS = 12
_COLORIMETER_DEFAULT_MIN_PATCH_SIZE_MM = 10.0


@router.post("/jobs/{job_name}/duplicate-as-colorimeter")
def duplicate_as_colorimeter(job_name: str):
    source = load_config(job_name)
    new_name = _unique_job_name(f"{job_name}_colorimeter")

    new_config = JobConfig(
        job_name=new_name,
        printer_name=source.printer_name,
        printer_notes=source.printer_notes,
        paper_name=source.paper_name,
        paper_notes=source.paper_notes,
        chart=ChartConfig(
            colorspace=source.chart.colorspace,
            patch_count=min(source.chart.patch_count, _COLORIMETER_DEFAULT_PATCH_COUNT),
            grey_steps=min(source.chart.grey_steps, _COLORIMETER_DEFAULT_GREY_STEPS),
            page_size=source.chart.page_size,
            reader_type=source.chart.reader_type,
            raster_dpi=source.chart.raster_dpi,
            min_patch_size_mm=_COLORIMETER_DEFAULT_MIN_PATCH_SIZE_MM,
        ),
        measurement=MeasurementConfig(method="colorimeter_csv"),
        output=OutputConfig(
            profile_description=f"{source.printer_name} / {source.paper_name} (colorimeter-measured)",
            quality=source.output.quality,
        ),
    )
    new_config.validate()
    new_job_dir = stages.init_job(JOBS_ROOT, new_config)

    # Custom target colors are about the printer+paper combo, not the
    # measurement method -- carry them straight over.
    colors = target_colors.load(job_dir(job_name))
    if colors:
        target_colors.save(new_job_dir, colors)

    return RedirectResponse(
        f"/jobs/{new_name}?ok={quote('Created as a colorimeter-CSV job, duplicated from ' + job_name + '.')}",
        status_code=303,
    )


def _parse_args_list(text: str) -> list[str]:
    return text.split()


@router.get("/jobs/{job_name}/edit")
def edit_job_form(request: Request, job_name: str):
    config = load_config(job_name)
    return templates.TemplateResponse(
        request, "job_edit.html", {"config": config, "job_name": job_name, "error": None}
    )


@router.post("/jobs/{job_name}/edit")
def edit_job(
    request: Request,
    job_name: str,
    printer_name: str = Form(""),
    printer_notes: str = Form(""),
    paper_name: str = Form(""),
    paper_notes: str = Form(""),
    colorspace: str = Form("RGB"),
    patch_count: int = Form(250),
    grey_steps: int = Form(16),
    page_size: str = Form("A4"),
    reader_type: str = Form("SS"),
    raster_dpi: int = Form(300),
    min_patch_size_mm: str = Form(""),
    extra_targen_args: str = Form(""),
    extra_printtarg_args: str = Form(""),
    profile_description: str = Form(""),
    copyright: str = Form(""),
    quality: str = Form("medium"),
    algorithm: str = Form("auto"),
    extra_colprof_args: str = Form(""),
    bin_dir: str = Form(""),
):
    config = load_config(job_name)
    config.printer_name = printer_name
    config.printer_notes = printer_notes
    config.paper_name = paper_name
    config.paper_notes = paper_notes
    config.chart.colorspace = colorspace
    config.chart.patch_count = patch_count
    config.chart.grey_steps = grey_steps
    config.chart.page_size = page_size
    config.chart.reader_type = reader_type
    config.chart.raster_dpi = raster_dpi
    config.chart.min_patch_size_mm = float(min_patch_size_mm) if min_patch_size_mm.strip() else None
    config.chart.extra_targen_args = _parse_args_list(extra_targen_args)
    config.chart.extra_printtarg_args = _parse_args_list(extra_printtarg_args)
    config.output.profile_description = profile_description
    config.output.copyright = copyright
    config.output.quality = quality
    config.output.algorithm = algorithm
    config.output.extra_colprof_args = _parse_args_list(extra_colprof_args)
    config.argyll.bin_dir = bin_dir or None

    try:
        config.validate()
    except PipelineError as exc:
        return templates.TemplateResponse(
            request, "job_edit.html", {"config": config, "job_name": job_name, "error": str(exc)}, status_code=400
        )
    config.save(job_dir(job_name) / "config.json")
    return RedirectResponse(
        f"/jobs/{job_name}?{'ok=' + quote('Settings updated.')}", status_code=303
    )


@router.post("/jobs/{job_name}/delete")
def delete_job(job_name: str):
    jd = job_dir(job_name)
    if jd.is_dir() and jd.parent == JOBS_ROOT:  # guard against any path trickery
        shutil.rmtree(jd)
    return RedirectResponse(
        f"/?ok={quote(f'Deleted job {job_name!r}.')}", status_code=303
    )


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
            "icc_exists": icc_path(jd, config).is_file(),
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
    )
