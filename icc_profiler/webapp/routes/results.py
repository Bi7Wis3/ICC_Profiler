from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from icc_profiler import cgats, stages, state, target_colors
from icc_profiler.check_chart import parse_profcheck_report
from icc_profiler.color_convert import lab_d50_to_hex
from icc_profiler.stages import icc_path, profile_dir, ti3_path
from icc_profiler.webapp.common import job_dir, load_config, templates

router = APIRouter()


@router.get("/jobs/{job_name}/profile.icc")
def download_icc_profile(job_name: str):
    config = load_config(job_name)
    jd = job_dir(job_name)
    path = icc_path(jd, config)
    if not path.is_file() or not state.is_done(stages.load_state(jd, config), "build-profile"):
        raise HTTPException(404, "No .icc profile built yet for this job -- run 'build-profile' first.")
    return FileResponse(
        path, media_type="application/vnd.iccprofile", filename=f"{job_name}.icc"
    )

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
                    # .ti3 Lab is D50-relative (ArgyllCMS/ICC convention).
                    "hex": lab_d50_to_hex(L, a, b),
                    "is_custom": row["SAMPLE_ID"].startswith("CUSTOM_"),
                    # SVG viewBox is 400x400; a*/b* roughly span -128..127.
                    "svg_x": round(200 + a * 1.5, 1),
                    "svg_y": round(200 - b * 1.5, 1),
                }
            )

    custom_rows = [r for r in rows if r["is_custom"]]
    custom_colors_by_id = {target_colors.sample_id_for(c["name"]): c for c in target_colors.load(jd)}
    for r in custom_rows:
        color = custom_colors_by_id.get(r["sample_id"], {})
        r["name"] = color.get("name", r["sample_id"][len("CUSTOM_") :])
        r["target_lab"] = color.get("target_lab")

    st = stages.load_state(jd, config)
    measure_stage = "read-chart" if config.measurement.method == "scanner" else "import-measurements"
    report_path = profile_dir(jd) / f"{config.job_name}_verify.txt"
    report = parse_profcheck_report(report_path.read_text()) if report_path.exists() else None
    # Files left over from an earlier run whose inputs have since changed.
    outdated = {
        "measurements": bool(rows) and not state.is_done(st, measure_stage),
        "profile": icc_path(jd, config).exists() and not state.is_done(st, "build-profile"),
        "report": report is not None and not state.is_done(st, "verify"),
    }

    return templates.TemplateResponse(
        request,
        "results.html",
        {
            "config": config,
            "job_name": job_name,
            "rows": rows,
            "custom_rows": custom_rows,
            "report": report,
            "icc_exists": icc_path(jd, config).exists() and state.is_done(st, "build-profile"),
            "outdated": outdated,
        },
    )
