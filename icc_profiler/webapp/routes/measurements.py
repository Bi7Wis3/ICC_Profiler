from __future__ import annotations

from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Form, UploadFile
from fastapi.responses import RedirectResponse

from icc_profiler import argyll_tools
from icc_profiler.stages import measurements_in_dir
from icc_profiler.webapp.common import JOBS_ROOT, job_dir, load_config

router = APIRouter()

SHARED_IT8_DIR = JOBS_ROOT.parent / "shared" / "it8_references"


def _redirect(job_name: str, ok: bool, message: str) -> RedirectResponse:
    status = "ok" if ok else "error"
    return RedirectResponse(f"/jobs/{job_name}?{status}={quote(message)}", status_code=303)


def _save_upload(upload: UploadFile, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as f:
        f.write(upload.file.read())


def list_bundled_cht_templates() -> list[str]:
    return argyll_tools.list_bundled_cht_templates()


def bundled_cht_path(name: str) -> Path | None:
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
        if p.is_file() and p.suffix.lower() not in (".cht",)
    )


@router.post("/jobs/{job_name}/measurements/upload-it8-scan")
async def upload_it8_scan(job_name: str, file: UploadFile):
    jd = job_dir(job_name)
    dest = measurements_in_dir(jd) / "it8_reference_scan.tif"
    _save_upload(file, dest)
    config = load_config(job_name)
    config.measurement.scanner.it8_reference_scan = str(dest)
    config.save(jd / "config.json")
    return _redirect(job_name, True, "IT8 reference scan uploaded.")


@router.post("/jobs/{job_name}/measurements/upload-test-chart-scan")
async def upload_test_chart_scan(job_name: str, file: UploadFile):
    jd = job_dir(job_name)
    dest = measurements_in_dir(jd) / "test_chart_scan.tif"
    _save_upload(file, dest)
    config = load_config(job_name)
    config.measurement.scanner.test_chart_scan = str(dest)
    config.save(jd / "config.json")
    return _redirect(job_name, True, "Test chart scan uploaded.")


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

    # -- vendor CGATS reference data (the per-patch known values for THIS
    # physical target) -- always comes from the target's vendor, either
    # uploaded now or reused from a prior job's upload.
    if cgats_source == "reuse":
        if not existing_cgats_name:
            return _redirect(job_name, False, "No existing reference file selected to reuse.")
        cgats_dest = SHARED_IT8_DIR / existing_cgats_name
        if not cgats_dest.is_file():
            return _redirect(job_name, False, f"{existing_cgats_name} no longer exists in shared/it8_references/.")
    else:
        if cgats_file is None or not cgats_file.filename:
            return _redirect(job_name, False, "No vendor CGATS reference file uploaded.")
        SHARED_IT8_DIR.mkdir(parents=True, exist_ok=True)
        cgats_dest = SHARED_IT8_DIR / cgats_file.filename
        _save_upload(cgats_file, cgats_dest)
    config.measurement.scanner.it8_cgats_reference_file = str(cgats_dest)

    # -- chart-recognition template (.cht) -- either one of ArgyllCMS's own
    # bundled standard templates (covers most commercial IT8 targets, e.g.
    # it8.cht / it8Wolf.cht) or a custom upload for a non-standard target.
    if cht_source == "bundled":
        if not bundled_cht_name:
            return _redirect(job_name, False, "No bundled chart template selected.")
        cht_path = bundled_cht_path(bundled_cht_name)
        if cht_path is None:
            return _redirect(job_name, False, f"Bundled template {bundled_cht_name!r} not found.")
    else:
        if cht_file is None or not cht_file.filename:
            return _redirect(job_name, False, "No .cht chart template uploaded.")
        SHARED_IT8_DIR.mkdir(parents=True, exist_ok=True)
        cht_path = SHARED_IT8_DIR / cht_file.filename
        _save_upload(cht_file, cht_path)
    config.measurement.scanner.it8_cht_file = str(cht_path)

    config.save(jd / "config.json")
    return _redirect(job_name, True, "IT8 reference data set.")


@router.post("/jobs/{job_name}/measurements/upload-csv")
async def upload_csv(job_name: str, file: UploadFile):
    jd = job_dir(job_name)
    dest = measurements_in_dir(jd) / "colorimeter_export.csv"
    _save_upload(file, dest)
    config = load_config(job_name)
    config.measurement.colorimeter_csv.csv_file = str(dest)
    config.save(jd / "config.json")
    return _redirect(job_name, True, "Colorimeter CSV export uploaded.")
