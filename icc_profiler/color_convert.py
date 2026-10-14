"""CIE Lab -> sRGB conversion, for rendering swatches/scatter plots in the UI.

Standard D65 Lab->XYZ->linear-sRGB->sRGB pipeline. Display-only: never used
as input to the profiling pipeline itself, so unlike the ArgyllCMS flags
elsewhere in this project, there's nothing here that needs verifying against
real tool output -- it's a fixed, well-known published formula.
"""
from __future__ import annotations

# D65 reference white (CIE 1931 2-degree observer), 0-100 scale.
_XN, _YN, _ZN = 95.047, 100.0, 108.883


def _finv(t: float) -> float:
    return t**3 if t**3 > 0.008856 else (t - 16 / 116) / 7.787


def _gamma(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def xyz_to_srgb(X: float, Y: float, Z: float) -> tuple[int, int, int]:
    """X/Y/Z on the usual 0-100 scale (D65 white ~= 95.047, 100.0, 108.883),
    matching both this module's Lab conversion and the XYZ_* columns ArgyllCMS
    writes into .ti1/.ti2 files."""
    X, Y, Z = X / 100, Y / 100, Z / 100

    r = X * 3.2406 + Y * -1.5372 + Z * -0.4986
    g = X * -0.9689 + Y * 1.8758 + Z * 0.0415
    bl = X * 0.0557 + Y * -0.2040 + Z * 1.0570

    r, g, bl = _gamma(r), _gamma(g), _gamma(bl)
    clamp = lambda v: max(0, min(255, round(v * 255)))  # noqa: E731
    return clamp(r), clamp(g), clamp(bl)


def xyz_to_hex(X: float, Y: float, Z: float) -> str:
    r, g, b = xyz_to_srgb(X, Y, Z)
    return f"#{r:02x}{g:02x}{b:02x}"


def lab_to_srgb(L: float, a: float, b: float) -> tuple[int, int, int]:
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200

    X = _XN * _finv(fx)
    Y = _YN * _finv(fy)
    Z = _ZN * _finv(fz)

    return xyz_to_srgb(X, Y, Z)


def lab_to_hex(L: float, a: float, b: float) -> str:
    r, g, b2 = lab_to_srgb(L, a, b)
    return f"#{r:02x}{g:02x}{b2:02x}"
