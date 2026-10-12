"""Read patch dimensions out of a .cht chart-recognition file.

The .cht format (see /usr/share/doc/argyll-doc/cht_format.html, or
cht_format.html in any ArgyllCMS source tree) is NOT CGATS -- it has its own
simple "BOXES" section where each sample-box line is:

    kl lxs lxe lys lye w h xo yo xi yi

kl is a key letter ("X"/"Y" for a sample box, "F" for fiducial marks, "D"
for diagnostic-only). w/h (fields 6/7) are the box's width/height, in the
same physical units printtarg used when it wrote the file -- confirmed
empirically to be millimeters for printtarg-generated charts (a 20-patch
A4 chart's boxes read "10.500000 7.000000", matching 10.5mm patches that
line up with the chart's known margins).
"""
from __future__ import annotations

from pathlib import Path


def read_patch_size_mm(cht_path: Path) -> tuple[float, float]:
    """Return (width, height) in mm of the first sample patch box found."""
    for line in cht_path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 11 and parts[0] in ("X", "Y"):
            return float(parts[5]), float(parts[6])
    raise ValueError(f"{cht_path}: no sample box ('X'/'Y') line found")
