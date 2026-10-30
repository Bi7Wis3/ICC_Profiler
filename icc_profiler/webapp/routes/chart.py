from __future__ import annotations

import io
from pathlib import Path

import img2pdf
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from PIL import Image

from icc_profiler.stages import chart_dir
from icc_profiler.webapp.common import job_dir

router = APIRouter()


def list_chart_tiffs(job_name: str) -> list[str]:
    d = chart_dir(job_dir(job_name))
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("*.tif*"))


@router.get("/jobs/{job_name}/chart/preview/{filename}")
def chart_preview_png(job_name: str, filename: str):
    if filename not in list_chart_tiffs(job_name):
        raise HTTPException(404, "Chart image not found")
    return preview_png(chart_dir(job_dir(job_name)) / filename)


def preview_png(path: Path) -> Response:
    if not path.is_file():
        raise HTTPException(404, "Chart image not found")
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((1200, 1200))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
    except Exception as exc:  # noqa: BLE001 - surface a readable error, not a 500 traceback
        raise HTTPException(500, f"Could not render preview: {exc}") from exc
    return Response(content=buf.getvalue(), media_type="image/png")


@router.get("/jobs/{job_name}/chart/download.pdf")
def chart_download_pdf(job_name: str):
    """Bundle every generated chart page into one printable PDF, using
    img2pdf rather than Pillow's own PDF writer.

    Two real problems were found and fixed here, both of which actually
    affected the user's real CMYK jobs, not just a hypothetical:

    1. Pillow's PDF plugin unconditionally JPEG-compresses (DCTDecode) RGB,
       L, and CMYK images alike -- confirmed by inspecting the raw PDF bytes
       of a previously-generated file. That's lossy, which quietly shifts
       color patch values -- exactly the kind of error this whole project
       exists to measure and correct for, so it can't be allowed to leak in
       upstream of printing. img2pdf embeds the source image losslessly
       (FlateDecode, confirmed via the same byte-level check) instead.
    2. The old code called `.convert("RGB")` on every image before
       embedding, including CMYK-colorspace charts (confirmed: both of the
       user's real jobs are CMYK). That silently remaps each patch's actual
       ink percentages through an uncontrolled CMYK->RGB formula, then the
       printer driver converts back to CMYK on its own -- two lossy,
       uncontrolled conversions corrupting the exact device values targen
       computed. img2pdf preserves the TIFF's native color mode (confirmed:
       a CMYK source TIFF produces a PDF tagged /ColorSpace /DeviceCMYK)
       instead of forcing everything through RGB.

    Physical page sizing still comes out correct automatically (confirmed
    via pdfinfo): img2pdf reads the DPI printtarg embedded in the TIFF.
    """
    files = list_chart_tiffs(job_name)
    if not files:
        raise HTTPException(404, "No chart generated yet for this job")

    d = chart_dir(job_dir(job_name))
    return pdf_response([d / name for name in files], f"{job_name}_chart.pdf")


def pdf_response(pages: list[Path], filename: str) -> Response:
    try:
        pdf_bytes = img2pdf.convert(*[str(p) for p in pages])
    except Exception as exc:  # noqa: BLE001 - surface a readable error, not a 500 traceback
        raise HTTPException(500, f"Could not build PDF: {exc}") from exc

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
