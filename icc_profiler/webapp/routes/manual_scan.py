from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from icc_profiler import manual_scan, state
from icc_profiler.errors import PipelineError
from icc_profiler.webapp.common import job_dir, load_config, templates

router = APIRouter()


def _redirect(job_name: str, ok: bool, message: str) -> RedirectResponse:
    status = "ok" if ok else "error"
    return RedirectResponse(f"/jobs/{job_name}?{status}={quote(message)}", status_code=303)


def _scan_redirect(job_name: str, patch_loc: str | None = None, error: str | None = None) -> RedirectResponse:
    url = f"/jobs/{job_name}/measurements/scan"
    params = []
    if patch_loc:
        params.append(f"patch={quote(patch_loc)}")
    if error:
        params.append(f"error={quote(error)}")
    if params:
        url += "?" + "&".join(params)
    return RedirectResponse(url, status_code=303)


def _pick_current(rows: list[dict], requested_loc: str | None) -> dict:
    if requested_loc:
        for row in rows:
            if row["loc"] == requested_loc:
                return row
    for row in rows:
        if not row["done"]:
            return row
    return rows[-1]


@router.get("/jobs/{job_name}/measurements/scan")
def scan_page(request: Request, job_name: str, patch: str | None = None, error: str | None = None):
    jd = job_dir(job_name)
    config = load_config(job_name)
    if not state.is_done(state.load_state(jd), "generate-chart"):
        return _redirect(job_name, False, "Generate the chart before starting a manual scan.")

    try:
        rows = manual_scan.build_rows(jd, config)
    except PipelineError as exc:
        return _redirect(job_name, False, str(exc))

    current = _pick_current(rows, patch)
    current_index = rows.index(current)
    done_count = sum(1 for r in rows if r["done"])

    return templates.TemplateResponse(
        request,
        "manual_scan.html",
        {
            "job_name": job_name,
            "rows": rows,
            "current": current,
            "current_index": current_index,
            "prev_row": rows[current_index - 1] if current_index > 0 else None,
            "next_row": rows[current_index + 1] if current_index + 1 < len(rows) else None,
            "done_count": done_count,
            "total_count": len(rows),
            "error": error,
        },
    )


@router.post("/jobs/{job_name}/measurements/scan/submit")
def submit_patch(
    job_name: str,
    patch_loc: str = Form(...),
    L: str = Form(...),
    a: str = Form(...),
    b: str = Form(...),
    next_patch: str = Form(""),
):
    jd = job_dir(job_name)
    try:
        manual_scan.record_reading(jd, patch_loc, L, a, b)
    except PipelineError as exc:
        return _scan_redirect(job_name, patch_loc=patch_loc, error=str(exc))
    return _scan_redirect(job_name, patch_loc=next_patch or None)


@router.post("/jobs/{job_name}/measurements/scan/clear")
def clear_patch(job_name: str, patch_loc: str = Form(...)):
    jd = job_dir(job_name)
    manual_scan.clear_reading(jd, patch_loc)
    return _scan_redirect(job_name, patch_loc=patch_loc)


@router.post("/jobs/{job_name}/measurements/scan/finish")
def finish_scan(job_name: str):
    jd = job_dir(job_name)
    config = load_config(job_name)
    try:
        dest = manual_scan.write_csv(jd, config, config.measurement.colorimeter_csv.column_map)
    except PipelineError as exc:
        return _scan_redirect(job_name, error=str(exc))
    config.measurement.colorimeter_csv.csv_file = str(dest)
    config.save(jd / "config.json")
    return _redirect(job_name, True, "Manual scan complete; readings written to CSV.")
