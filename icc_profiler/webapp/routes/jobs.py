from __future__ import annotations

import shutil
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from icc_profiler import check_chart, manual_scan, stages, state, target_colors
from icc_profiler.config import (
    LAB_REFERENCES,
    ChartConfig,
    JobConfig,
    MeasurementConfig,
    OutputConfig,
)
from icc_profiler.errors import PipelineError
from icc_profiler.names import ensure_within
from icc_profiler.stages import icc_path
from icc_profiler.webapp import tasks
from icc_profiler.webapp.common import (
    JOBS_ROOT,
    cleared_note,
    job_dir,
    list_jobs,
    load_config,
    require_idle,
    templates,
)
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
    try:
        config = JobConfig(
            job_name=job_name.strip(),
            printer_name=printer_name,
            paper_name=paper_name,
            chart=ChartConfig(
                colorspace=colorspace,
                patch_count=patch_count,
                grey_steps=grey_steps,
                page_size=page_size,
                min_patch_size_mm=_optional_float(min_patch_size_mm, "Minimum patch size"),
            ),
            measurement=MeasurementConfig(method=method),
            output=OutputConfig(profile_description=f"{printer_name} / {paper_name}"),
        )
        config.validate()
        if (JOBS_ROOT / config.job_name).exists():
            raise PipelineError(f"A job named {config.job_name!r} already exists.")
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


def _optional_float(text: str, what: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise PipelineError(f"{what} must be a number (got {text!r})") from exc


def _chart_settings(config: JobConfig) -> tuple:
    c = config.chart
    return (
        c.colorspace, c.patch_count, c.grey_steps, c.page_size, c.reader_type, c.raster_dpi,
        c.min_patch_size_mm, c.ink_limit, tuple(c.extra_targen_args), tuple(c.extra_printtarg_args),
    )


def _profile_settings(config: JobConfig) -> tuple:
    o = config.output
    return (
        o.profile_description, o.copyright, o.quality, o.algorithm, o.black_ink_limit,
        tuple(o.extra_colprof_args),
    )


@router.get("/jobs/{job_name}/edit")
def edit_job_form(request: Request, job_name: str):
    config = load_config(job_name)
    return templates.TemplateResponse(
        request,
        "job_edit.html",
        {"config": config, "job_name": job_name, "error": None, "lab_references": LAB_REFERENCES},
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
    ink_limit: str = Form(""),
    extra_targen_args: str = Form(""),
    extra_printtarg_args: str = Form(""),
    profile_description: str = Form(""),
    copyright: str = Form(""),
    quality: str = Form("medium"),
    algorithm: str = Form("auto"),
    black_ink_limit: str = Form(""),
    extra_colprof_args: str = Form(""),
    scanner_profile_quality: str = Form(""),
    bin_dir: str = Form(""),
):
    config = load_config(job_name)
    jd = job_dir(job_name)
    before_chart = _chart_settings(config)
    before_profile = _profile_settings(config)
    before_scanner_quality = config.measurement.scanner.profile_quality

    def _error(message: str):
        return templates.TemplateResponse(
            request,
            "job_edit.html",
            {"config": config, "job_name": job_name, "error": message, "lab_references": LAB_REFERENCES},
            status_code=400,
        )

    try:
        require_idle(job_name)
        config.chart.min_patch_size_mm = _optional_float(min_patch_size_mm, "Minimum patch size")
        config.chart.ink_limit = _optional_float(ink_limit, "Total ink limit")
        config.output.black_ink_limit = _optional_float(black_ink_limit, "Black ink limit")
    except PipelineError as exc:
        return _error(str(exc))
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
    config.chart.extra_targen_args = _parse_args_list(extra_targen_args)
    config.chart.extra_printtarg_args = _parse_args_list(extra_printtarg_args)
    config.output.profile_description = profile_description
    config.output.copyright = copyright
    config.output.quality = quality
    config.output.algorithm = algorithm
    config.output.extra_colprof_args = _parse_args_list(extra_colprof_args)
    if scanner_profile_quality:
        config.measurement.scanner.profile_quality = scanner_profile_quality
    config.argyll.bin_dir = bin_dir or None

    try:
        config.validate()
    except PipelineError as exc:
        return _error(str(exc))
    config.save(jd / "config.json")

    # Settings only affect stages that haven't run yet; mark the ones built
    # from the old settings out of date.
    cleared: list[str] = []
    if _chart_settings(config) != before_chart:
        cleared = stages.invalidate_from(jd, config, "generate-chart")
    else:
        if config.measurement.scanner.profile_quality != before_scanner_quality:
            cleared += stages.invalidate_from(jd, config, "build-scanner-profile")
        if _profile_settings(config) != before_profile:
            cleared += stages.invalidate_from(jd, config, "build-profile")
    note = cleared_note(sorted(set(cleared), key=stages.order_for(config).index))
    if cleared and cleared[0] == "generate-chart":
        note += " Chart settings changed, so the chart must be regenerated and reprinted."
    return RedirectResponse(f"/jobs/{job_name}?ok={quote('Settings updated.' + note)}", status_code=303)


@router.post("/jobs/{job_name}/delete")
def delete_job(job_name: str):
    jd = job_dir(job_name)
    if tasks.is_busy(job_name):
        return RedirectResponse(
            f"/jobs/{job_name}?error={quote('A stage is running; wait for it to finish before deleting.')}",
            status_code=303,
        )
    # job_dir() already resolved the path and refused anything unsafe; check
    # once more right before the irreversible step.
    if jd.is_dir() and ensure_within(jd, JOBS_ROOT).parent == JOBS_ROOT.resolve():
        shutil.rmtree(jd)
    return RedirectResponse(
        f"/?ok={quote(f'Deleted job {job_name!r}.')}", status_code=303
    )


@router.get("/jobs/{job_name}")
def job_detail(request: Request, job_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    order = stages.order_for(config)
    st = state.load_state(jd, order)
    stale = state.stale_stages(jd, order)
    stage_rows = [{"name": s, "done": state.is_done(st, s), "stale": s in stale} for s in order]
    colors = target_colors.load(jd)
    chart_files = list_chart_tiffs(job_name)
    has_chart = stages.chart_id(jd, config) is not None

    manual_scan_progress = None
    manual_scan_stale = False
    try:
        patches = manual_scan.chart_patches(jd, config)
        done = len(manual_scan.load_progress(jd, config))
        manual_scan_stale = manual_scan.is_stale(jd, config)
        if done:
            manual_scan_progress = {"done": done, "total": len(patches)}
    except Exception:  # noqa: BLE001 - best-effort summary, never break the page
        manual_scan_progress = None

    # Measurement inputs recorded for an older version of the chart.
    mismatched_inputs = []
    if has_chart:
        scan = config.measurement.scanner.test_chart_scan
        csv_file = config.measurement.colorimeter_csv.csv_file
        if config.measurement.method == "scanner" and scan:
            if not stages.input_matches_chart(jd, config, "test_chart_scan", config.input_path(scan, jd)):
                mismatched_inputs.append("test chart scan")
        if config.measurement.method == "colorimeter_csv" and csv_file:
            if not stages.input_matches_chart(jd, config, "colorimeter_csv", config.input_path(csv_file, jd)):
                mismatched_inputs.append("colorimeter CSV")

    # Anything that has been printed or measured would be thrown away by
    # regenerating the chart -- the job page asks before doing that.
    later_work = any(r["done"] for r in stage_rows[1:]) or bool(
        config.measurement.scanner.test_chart_scan
        or config.measurement.colorimeter_csv.csv_file
        or manual_scan_progress
    )

    task = tasks.status(job_name)
    if task and task["state"] != "running":
        tasks.mark_seen(job_name)
    icc_file_exists = icc_path(jd, config).is_file()
    icc_current = icc_file_exists and state.is_done(st, "build-profile")
    check = None
    if config.measurement.method == "colorimeter_csv":
        try:
            check = check_chart.status(jd, config)
        except Exception:  # noqa: BLE001 - best-effort summary, never break the page
            check = None

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
            "icc_exists": icc_current,
            "icc_outdated": icc_file_exists and not icc_current,
            "has_chart": has_chart,
            "later_work": later_work,
            "mismatched_inputs": mismatched_inputs,
            "lab_references": LAB_REFERENCES,
            "task": task,
            "manual_scan_progress": manual_scan_progress,
            "manual_scan_stale": manual_scan_stale,
            "check": check,
            "check_patch_default": check_chart.DEFAULT_PATCH_COUNT,
            "check_patch_range": check_chart.PATCH_COUNT_RANGE,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
    )
