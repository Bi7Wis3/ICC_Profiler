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


def _parse_percent(text: str, channel: str) -> float:
    try:
        value = float(text.strip())
    except ValueError as exc:
        raise PipelineError(f"{channel} value {text!r} is not a number") from exc
    if not (0 <= value <= 100):
        raise PipelineError(f"{channel} value {value} must be between 0 and 100 (device percent)")
    return value


@router.post("/jobs/{job_name}/colors/add")
def add_color(
    job_name: str,
    name: str = Form(...),
    input_mode: str = Form("device"),  # "hex" (RGB only), "cmyk", "gray", or "device" (raw CSV, any colorspace)
    hex_value: str = Form(""),
    cmyk_c: str = Form(""),
    cmyk_m: str = Form(""),
    cmyk_y: str = Form(""),
    cmyk_k: str = Form(""),
    gray_value: str = Form(""),
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
                    "use the CMYK/Gray fields instead."
                )
            values = target_colors.hex_to_device_rgb(hex_value)
        elif input_mode == "cmyk":
            if config.chart.colorspace != "CMYK":
                raise PipelineError("CMYK input only makes sense for a CMYK chart colorspace.")
            values = [_parse_percent(v, ch) for v, ch in zip([cmyk_c, cmyk_m, cmyk_y, cmyk_k], "CMYK")]
        elif input_mode == "gray":
            if config.chart.colorspace != "GRAY":
                raise PipelineError("Gray input only makes sense for a Gray chart colorspace.")
            values = [_parse_percent(gray_value, "K")]
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
