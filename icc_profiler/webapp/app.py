from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from icc_profiler.webapp.routes import (
    chart,
    check,
    colors,
    jobs,
    manual_scan,
    measurements,
    pipeline,
    results,
    spot,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Host names the server answers to. Refusing others blocks DNS-rebinding
# attacks (a hostile page whose domain re-resolves to 127.0.0.1). To reach
# the UI from another machine, set e.g.
#   ICC_PROFILER_ALLOWED_HOSTS="localhost,127.0.0.1,192.168.1.20"   (or "*")
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get("ICC_PROFILER_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver").split(",")
    if h.strip()
]

app = FastAPI(title="ICC Profiler")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


@app.middleware("http")
async def same_origin_posts_only(request: Request, call_next):
    """CSRF protection: refuse state-changing requests sent by another site.

    Browsers attach an Origin header (or at least a Referer) to cross-site
    form POSTs, so any page you visit could otherwise POST to
    localhost:8000/jobs/<name>/delete. Requests whose Origin/Referer host
    differs from the Host they were sent to are rejected. Requests with
    neither header (curl, scripts) aren't from a browser page and pass.
    """
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        source = request.headers.get("origin") or request.headers.get("referer")
        if source is not None:
            host = request.headers.get("host", "")
            if source == "null" or urlsplit(source).netloc != host:
                return PlainTextResponse("Cross-site request refused", status_code=403)
    return await call_next(request)


app.include_router(jobs.router)
app.include_router(chart.router)
app.include_router(measurements.router)
app.include_router(pipeline.router)
app.include_router(colors.router)
app.include_router(manual_scan.router)
app.include_router(results.router)
app.include_router(check.router)
app.include_router(spot.router)
