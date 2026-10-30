from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Form, UploadFile

from icc_profiler import argyll_tools, stages
from icc_profiler.config import LAB_REFERENCES, store_path
from icc_profiler.errors import PipelineError
from icc_profiler.names import safe_upload_name
from icc_profiler.stages import measurements_in_dir
from icc_profiler.webapp.common import (
    JOBS_ROOT,
    cleared_note,
    job_dir,
    load_config,
    redirect_to_job,
    require_idle,
)

router = APIRouter()

SHARED_IT8_DIR = JOBS_ROOT.parent / "shared" / "it8_references"


def _save_upload(upload: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        f.write(upload.file.read())


def list_bundled_cht_templates() -> list[str]:
    return argyll_tools.list_bundled_cht_templates()


def bundled_cht_path(name: str) -> Path | None:
    # Only names from the listing are accepted, so a crafted value can't
    # point outside Argyll's ref folder.
    if name not in list_bundled_cht_templates():
        return None
    ref_dir = argyll_tools.find_ref_dir()
    if ref_dir is None:
        return None
    candidate = ref_dir / name
    return candidate if candidate.is_file() else None


def list_existing_reference_files() -> list[str]:
    """Previously-uploaded vendor CGATS reference files (.cie/.txt), reusable
    across jobs since the physical IT8 target doesn't change per job."""
    if not SHARED_IT8_DIR.is_dir():
        return []
    return sorted(
        p.name for p in SHARED_IT8_DIR.iterdir()
        if p.is_file() and p.suffix.lower() not in (".cht",) and not p.name.startswith(".")
    )


@router.post("/jobs/{job_name}/measurements/upload-it8-scan")
async def upload_it8_scan(job_name: str, file: UploadFile):
    jd = job_dir(job_name)
    config = load_config(job_name)
    try:
        require_idle(job_name)
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    dest = measurements_in_dir(jd) / "it8_reference_scan.tif"
    _save_upload(file, dest)
    config.measurement.scanner.it8_reference_scan = store_path(dest, jd)
    config.save(jd / "config.json")
    cleared = stages.invalidate_from(jd, config, "build-scanner-profile")
    return redirect_to_job(job_name, True, "IT8 reference scan uploaded." + cleared_note(cleared))


@router.post("/jobs/{job_name}/measurements/upload-test-chart-scan")
async def upload_test_chart_scan(job_name: str, file: UploadFile):
    jd = job_dir(job_name)
    config = load_config(job_name)
    try:
        require_idle(job_name)
        if stages.chart_id(jd, config) is None:
            raise PipelineError("Generate and print the chart before uploading a scan of it.")
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    dest = measurements_in_dir(jd) / "test_chart_scan.tif"
    _save_upload(file, dest)
    stages.bind_input(jd, config, "test_chart_scan")
    config.measurement.scanner.test_chart_scan = store_path(dest, jd)
    config.save(jd / "config.json")
    cleared = stages.invalidate_from(jd, config, "read-chart")
    return redirect_to_job(job_name, True, "Test chart scan uploaded." + cleared_note(cleared))


@router.post("/jobs/{job_name}/measurements/set-it8-reference-data")
async def set_it8_reference_data(
    job_name: str,
    cgats_source: str = Form(...),  # "upload" or "reuse"
    cgats_file: Optional[UploadFile] = None,
    existing_cgats_name: str = Form(""),
    cht_source: str = Form(...),  # "bundled" or "upload"
    bundled_cht_name: str = Form(""),
    cht_file: Optional[UploadFile] = None,
):
    jd = job_dir(job_name)
    config = load_config(job_name)
    try:
        require_idle(job_name)
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))

    # -- vendor CGATS reference data (the per-patch known values for THIS
    # physical target) -- always comes from the target's vendor, either
    # uploaded now or reused from a prior job's upload.
    if cgats_source == "reuse":
        if not existing_cgats_name:
            return redirect_to_job(job_name, False, "No existing reference file selected to reuse.")
        if existing_cgats_name not in list_existing_reference_files():
            return redirect_to_job(
                job_name, False, f"{existing_cgats_name} is not in shared/it8_references/."
            )
        cgats_dest = SHARED_IT8_DIR / existing_cgats_name
    else:
        if cgats_file is None or not cgats_file.filename:
            return redirect_to_job(job_name, False, "No vendor CGATS reference file uploaded.")
        try:
            cgats_dest = SHARED_IT8_DIR / safe_upload_name(cgats_file.filename)
        except PipelineError as exc:
            return redirect_to_job(job_name, False, str(exc))
        _save_upload(cgats_file, cgats_dest)
    config.measurement.scanner.it8_cgats_reference_file = store_path(cgats_dest, jd)

    # -- chart-recognition template (.cht) -- either one of ArgyllCMS's own
    # bundled standard templates (covers most commercial IT8 targets, e.g.
    # it8.cht / it8Wolf.cht) or a custom upload for a non-standard target.
    if cht_source == "bundled":
        if not bundled_cht_name:
            return redirect_to_job(job_name, False, "No bundled chart template selected.")
        cht_path = bundled_cht_path(bundled_cht_name)
        if cht_path is None:
            return redirect_to_job(job_name, False, f"Bundled template {bundled_cht_name!r} not found.")
    else:
        if cht_file is None or not cht_file.filename:
            return redirect_to_job(job_name, False, "No .cht chart template uploaded.")
        try:
            cht_path = SHARED_IT8_DIR / safe_upload_name(cht_file.filename)
        except PipelineError as exc:
            return redirect_to_job(job_name, False, str(exc))
        _save_upload(cht_file, cht_path)
    config.measurement.scanner.it8_cht_file = store_path(cht_path, jd)

    config.save(jd / "config.json")
    cleared = stages.invalidate_from(jd, config, "build-scanner-profile")
    return redirect_to_job(job_name, True, "IT8 reference data set." + cleared_note(cleared))


@router.post("/jobs/{job_name}/measurements/upload-csv")
async def upload_csv(job_name: str, file: UploadFile):
    jd = job_dir(job_name)
    config = load_config(job_name)
    try:
        require_idle(job_name)
        if stages.chart_id(jd, config) is None:
            raise PipelineError("Generate and print the chart before uploading measurements of it.")
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in (".csv", ".txt", ".xlsx"):
        return redirect_to_job(job_name, False, "Upload a .csv or .xlsx export from the colorimeter software.")
    dest = measurements_in_dir(jd) / f"colorimeter_export{'.csv' if suffix == '.txt' else suffix}"
    _save_upload(file, dest)
    stages.bind_input(jd, config, "colorimeter_csv")
    config.measurement.colorimeter_csv.csv_file = store_path(dest, jd)
    config.save(jd / "config.json")
    cleared = stages.invalidate_from(jd, config, "import-measurements")
    return redirect_to_job(job_name, True, "Colorimeter export uploaded." + cleared_note(cleared))


@router.post("/jobs/{job_name}/measurements/import-options")
def set_import_options(job_name: str, match_by: str = Form("name"), specular: str = Form("SCE")):
    jd = job_dir(job_name)
    config = load_config(job_name)
    m = config.measurement.colorimeter_csv
    before = (m.match_by, m.specular)
    m.match_by, m.specular = match_by, specular
    try:
        require_idle(job_name)
        config.validate()
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    config.save(jd / "config.json")
    cleared = stages.invalidate_from(jd, config, "import-measurements") if before != (match_by, specular) else []
    return redirect_to_job(job_name, True, "Import settings saved." + cleared_note(cleared))


@router.post("/jobs/{job_name}/measurements/lab-reference")
def set_lab_reference(job_name: str, lab_reference: str = Form(""), confirmed: bool = Form(False)):
    jd = job_dir(job_name)
    config = load_config(job_name)
    try:
        require_idle(job_name)
        if lab_reference not in LAB_REFERENCES:
            raise PipelineError("Pick the illuminant/observer your colorimeter is set to.")
        if not confirmed:
            raise PipelineError(
                "Tick the box to confirm you checked this setting on the colorimeter itself."
            )
    except PipelineError as exc:
        return redirect_to_job(job_name, False, str(exc))
    changed = config.measurement.colorimeter_csv.lab_reference != lab_reference
    config.measurement.colorimeter_csv.lab_reference = lab_reference
    config.save(jd / "config.json")
    cleared = stages.invalidate_from(jd, config, "import-measurements") if changed else []
    return redirect_to_job(
        job_name, True, f"Lab reference set to {LAB_REFERENCES[lab_reference]}." + cleared_note(cleared)
    )
