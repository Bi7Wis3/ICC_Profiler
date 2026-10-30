from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

from icc_profiler import spot_colors, stages, state
from icc_profiler.errors import PipelineError
from icc_profiler.webapp import tasks
from icc_profiler.webapp.common import job_dir, load_config, redirect_to_job, require_idle, templates
from icc_profiler.webapp.routes.chart import pdf_response, preview_png

router = APIRouter()


def _back(job_name: str, ok: bool, message: str) -> RedirectResponse:
    return RedirectResponse(
        f"/jobs/{job_name}/spot?{'ok' if ok else 'error'}={quote(message)}", status_code=303
    )


@router.get("/jobs/{job_name}/spot")
def spot_page(request: Request, job_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    profile_ready = state.is_done(stages.load_state(jd, config), "build-profile")
    return templates.TemplateResponse(
        request,
        "spot_colors.html",
        {
            "config": config,
            "job_name": job_name,
            "profile_ready": profile_ready,
            "spot": spot_colors.status(jd, config),
            "gamut_de": spot_colors.OUT_OF_GAMUT_DE,
            "ok": request.query_params.get("ok"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/jobs/{job_name}/spot/add-measured")
async def add_measured(job_name: str, file: UploadFile, names: str = Form("")):
    config = load_config(job_name)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".txt", ".xlsx"):
        return _back(job_name, False, "Upload a .csv or .xlsx export from the colorimeter software.")
    try:
        require_idle(job_name)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"book{'.csv' if suffix == '.txt' else suffix}"
            path.write_bytes(await file.read())
            colors = spot_colors.import_measured(config, path, names)
        count = spot_colors.add_colors(job_dir(job_name), config, colors, "measured")
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _back(job_name, True, f"Added {count} color(s) and looked up their recipes.")


@router.post("/jobs/{job_name}/spot/add-typed")
def add_typed(job_name: str, lines: str = Form("")):
    config = load_config(job_name)
    try:
        require_idle(job_name)
        colors = spot_colors.parse_typed(lines)
        if not colors:
            raise PipelineError("Enter at least one line: name; L; a; b")
        count = spot_colors.add_colors(job_dir(job_name), config, colors, "typed")
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _back(job_name, True, f"Added {count} color(s) and looked up their recipes.")


@router.post("/jobs/{job_name}/spot/remove")
def remove(job_name: str, name: str = Form(...)):
    try:
        require_idle(job_name)
        spot_colors.remove_color(job_dir(job_name), name)
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _back(job_name, True, f"Removed {name}.")


@router.post("/jobs/{job_name}/spot/recompute")
def recompute(job_name: str):
    try:
        require_idle(job_name)
        spot_colors.recompute(job_dir(job_name), load_config(job_name))
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _back(job_name, True, "Recipes updated from the current profile.")


@router.post("/jobs/{job_name}/spot/proof")
def make_proof(job_name: str):
    try:
        require_idle(job_name)
        spot_colors.make_proof(job_dir(job_name), load_config(job_name))
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _back(job_name, True, "Proof sheet ready. Print it the same way as the main chart.")


@router.get("/jobs/{job_name}/spot/preview/{filename}")
def proof_preview(job_name: str, filename: str):
    pages = {p.name: p for p in spot_colors.proof_tiffs(job_dir(job_name))}
    if filename not in pages:
        raise HTTPException(404, "Proof image not found")
    return preview_png(pages[filename])


@router.get("/jobs/{job_name}/spot/proof.pdf")
def proof_pdf(job_name: str):
    pages = spot_colors.proof_tiffs(job_dir(job_name))
    if not pages:
        raise HTTPException(404, "No proof sheet made yet")
    return pdf_response(pages, f"{job_name}_spot_proof.pdf")


@router.post("/jobs/{job_name}/spot/proof-upload")
async def proof_upload(job_name: str, file: UploadFile):
    config = load_config(job_name)
    jd = job_dir(job_name)
    try:
        require_idle(job_name)
        spot_colors.save_proof_export(jd, config, await file.read(), Path(file.filename or "").suffix)
        count = spot_colors.read_proof(jd, config)
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _back(job_name, True, f"Read {count} proof patch(es). See the measured ΔE column.")


@router.post("/jobs/{job_name}/spot/correct")
def correct(job_name: str):
    try:
        require_idle(job_name)
        count = spot_colors.apply_corrections(job_dir(job_name), load_config(job_name))
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    if not count:
        return _back(job_name, True, "Nothing to correct: every measured color printed as expected.")
    return _back(job_name, True, f"Corrected {count} recipe(s). Make a new proof sheet to check them.")


@router.get("/jobs/{job_name}/spot/recipes.csv")
def recipes_csv(job_name: str):
    text = spot_colors.recipes_csv(job_dir(job_name), load_config(job_name))
    return Response(
        content=text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{job_name}_spot_colors.csv"'},
    )


def _rebuild(job_name: str, label: str, done: str):
    """Rebuild the profile in the background, keeping the old .icc, then
    look the recipes up again in the new one."""
    jd = job_dir(job_name)

    def work() -> str:
        config = load_config(job_name)
        backup = spot_colors.backup_profile(jd, config)
        stages.build_profile(jd, config)
        if spot_colors.load(jd)["colors"]:
            spot_colors.recompute(jd, config)
        kept = f" The previous profile was kept as {backup.name}." if backup else ""
        return f"{done}{kept} Run the check chart again to see the new accuracy."

    if not tasks.start(job_name, label, work):
        return _back(job_name, False, "A stage is already running for this job.")
    return redirect_to_job(job_name, True, f"Started {label}. This page updates when it finishes.")


@router.post("/jobs/{job_name}/spot/add-to-profile")
def add_to_profile(job_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    try:
        require_idle(job_name)
        if not state.is_done(stages.load_state(jd, config), "build-profile"):
            raise PipelineError("Build the profile first.")
        count = spot_colors.add_proof_to_profile(jd, config)
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _rebuild(
        job_name,
        "rebuilding the profile with the spot color readings",
        f"Profile rebuilt with {count} spot color reading(s) added.",
    )


@router.post("/jobs/{job_name}/spot/remove-extra")
def remove_extra(job_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    try:
        require_idle(job_name)
        spot_colors.remove_extra_patches(jd, config)
    except PipelineError as exc:
        return _back(job_name, False, str(exc))
    return _rebuild(job_name, "rebuilding the profile from the chart alone", "Profile rebuilt from the chart alone.")
