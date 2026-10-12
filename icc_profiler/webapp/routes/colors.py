from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Form
from fastapi.responses import RedirectResponse

from icc_profiler import target_colors
from icc_profiler.errors import PipelineError
from icc_profiler.webapp.common import job_dir, load_config

router = APIRouter()


def _redirect(job_name: str, ok: bool, message: str) -> RedirectResponse:
    status = "ok" if ok else "error"
    return RedirectResponse(f"/jobs/{job_name}?{status}={quote(message)}", status_code=303)


def _parse_floats(text: str) -> list[float]:
    return [float(x.strip()) for x in text.split(",") if x.strip()]


@router.post("/jobs/{job_name}/colors/add")
def add_color(
    job_name: str,
    name: str = Form(...),
    input_mode: str = Form("device"),  # "device" or "hex"
    hex_value: str = Form(""),
    device_value: str = Form(""),
    target_lab: str = Form(""),
):
    jd = job_dir(job_name)
    config = load_config(job_name)
    try:
        if input_mode == "hex":
            if config.chart.colorspace != "RGB":
                raise PipelineError(
                    "Hex input only makes sense for an RGB chart colorspace; "
                    "enter raw device values instead for CMYK/GRAY."
                )
            values = target_colors.hex_to_device_rgb(hex_value)
        else:
            values = _parse_floats(device_value)
        lab = _parse_floats(target_lab) if target_lab.strip() else None
        target_colors.add(jd, name.strip(), values, lab)
    except (PipelineError, ValueError) as exc:
        return _redirect(job_name, False, str(exc))
    return _redirect(job_name, True, f"Added target color '{name}'.")


@router.post("/jobs/{job_name}/colors/remove")
def remove_color(job_name: str, name: str = Form(...)):
    jd = job_dir(job_name)
    try:
        target_colors.remove(jd, name)
    except PipelineError as exc:
        return _redirect(job_name, False, str(exc))
    return _redirect(job_name, True, f"Removed target color '{name}'.")
