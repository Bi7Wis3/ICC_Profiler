from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from icc_profiler.webapp.routes import chart, colors, jobs, manual_scan, measurements, pipeline, results

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="ICC Profiler")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(jobs.router)
app.include_router(chart.router)
app.include_router(measurements.router)
app.include_router(pipeline.router)
app.include_router(colors.router)
app.include_router(manual_scan.router)
app.include_router(results.router)
