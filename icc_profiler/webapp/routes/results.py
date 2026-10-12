from __future__ import annotations

import re

from fastapi import APIRouter, Request

from icc_profiler import cgats, target_colors
from icc_profiler.color_convert import lab_to_hex
from icc_profiler.stages import icc_path, profile_dir, ti3_path
from icc_profiler.webapp.common import job_dir, load_config, templates

router = APIRouter()

#   No of test patches = 21
#   Profile check complete, errors: max. = 14.938801, avg. = 7.640107, RMS = 8.800242
# -- confirmed against real ArgyllCMS 3.1.0 profcheck -v2 output.
_SUMMARY_RE = re.compile(
    r"errors:\s*max\.\s*=\s*([0-9.]+),\s*avg\.\s*=\s*([0-9.]+),\s*RMS\s*=\s*([0-9.]+)",
    re.IGNORECASE,
)
# Per-patch line: [8.607638] 1: ... should be ...
_PATCH_RE = re.compile(r"^\[([0-9.]+)\]\s*(\S+):", re.MULTILINE)


def _parse_profcheck_report(text: str) -> dict:
    m = _SUMMARY_RE.search(text)
    patch_matches = _PATCH_RE.findall(text)
    return {
        "maximum_de": m.group(1) if m else None,
        "average_de": m.group(2) if m else None,
        "rms_de": m.group(3) if m else None,
        "patch_count": len(patch_matches),
        "raw": text,
    }


@router.get("/jobs/{job_name}/results")
def results(request: Request, job_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)

    ti3 = ti3_path(jd, config)
    rows = []
    if ti3.exists():
        table = cgats.read(ti3)
        for row in table.rows:
            try:
                L, a, b = float(row["LAB_L"]), float(row["LAB_A"]), float(row["LAB_B"])
            except (KeyError, ValueError):
                continue
            rows.append(
                {
                    "sample_id": row["SAMPLE_ID"],
                    "L": L,
                    "a": a,
                    "b": b,
                    "hex": lab_to_hex(L, a, b),
                    "is_custom": row["SAMPLE_ID"].startswith("CUSTOM_"),
                    # SVG viewBox is 400x400; a*/b* roughly span -128..127.
                    "svg_x": round(200 + a * 1.5, 1),
                    "svg_y": round(200 - b * 1.5, 1),
                }
            )

    custom_rows = [r for r in rows if r["is_custom"]]
    custom_colors_by_name = {c["name"]: c for c in target_colors.load(jd)}
    for r in custom_rows:
        name = r["sample_id"][len("CUSTOM_") :]
        r["target_lab"] = custom_colors_by_name.get(name, {}).get("target_lab")

    report_path = profile_dir(jd) / f"{config.job_name}_verify.txt"
    report = _parse_profcheck_report(report_path.read_text()) if report_path.exists() else None

    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "config": config,
            "job_name": job_name,
            "rows": rows,
            "custom_rows": custom_rows,
            "report": report,
            "icc_exists": icc_path(jd, config).exists(),
        },
    )
