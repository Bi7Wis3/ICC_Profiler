"""CIE Lab/XYZ -> sRGB conversion for UI swatches, plus white-point adaptation.

The swatch functions are display-only: never used as input to the profiling
pipeline itself. lab_d65_to_d50() is the one exception -- csv_import uses it
to bring colorimeter readings taken under D65 / 2 degree to the D50 / 2
degree that ArgyllCMS and ICC profiles expect.

Measured Lab from ArgyllCMS (.ti3 LAB_* columns) is relative to D50, while
sRGB is defined for D65, so lab_d50_to_hex() adapts D50 -> D65 with the
Bradford transform before encoding. All formulas are the standard published
ones (CIE Lab, IEC 61966-2-1 sRGB, Bradford chromatic adaptation).
"""
from __future__ import annotations

# Reference whites (CIE 1931 2-degree observer), 0-100 scale.
D65_WHITE = (95.047, 100.0, 108.883)
D50_WHITE = (96.422, 100.0, 82.521)
_XN, _YN, _ZN = D65_WHITE

_BRADFORD = (
    (0.8951, 0.2664, -0.1614),
    (-0.7502, 1.7135, 0.0367),
    (0.0389, -0.0685, 1.0296),
)
_BRADFORD_INV = (
    (0.9869929, -0.1470543, 0.1599627),
    (0.4323053, 0.5183603, 0.0492912),
    (-0.0085287, 0.0400428, 0.9684867),
)


def _matvec(m, v):
    return tuple(m[i][0] * v[0] + m[i][1] * v[1] + m[i][2] * v[2] for i in range(3))


def adapt_xyz(xyz: tuple[float, float, float], src_white, dst_white) -> tuple[float, float, float]:
    """Bradford chromatic adaptation of an XYZ value from src_white to dst_white."""
    rho_s = _matvec(_BRADFORD, src_white)
    rho_d = _matvec(_BRADFORD, dst_white)
    cone = _matvec(_BRADFORD, xyz)
    scaled = tuple(c * d / s for c, d, s in zip(cone, rho_d, rho_s))
    return _matvec(_BRADFORD_INV, scaled)


def _f(t: float) -> float:
    return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116


def _finv(t: float) -> float:
    return t**3 if t**3 > 0.008856 else (t - 16 / 116) / 7.787


def lab_to_xyz(L: float, a: float, b: float, white=D65_WHITE) -> tuple[float, float, float]:
    fy = (L + 16) / 116
    fx = fy + a / 500
    fz = fy - b / 200
    return white[0] * _finv(fx), white[1] * _finv(fy), white[2] * _finv(fz)


def xyz_to_lab(X: float, Y: float, Z: float, white=D65_WHITE) -> tuple[float, float, float]:
    fx, fy, fz = _f(X / white[0]), _f(Y / white[1]), _f(Z / white[2])
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def lab_d65_to_d50(L: float, a: float, b: float) -> tuple[float, float, float]:
    """Lab measured relative to D65 -> Lab relative to D50 (same observer)."""
    xyz_d65 = lab_to_xyz(L, a, b, D65_WHITE)
    return xyz_to_lab(*adapt_xyz(xyz_d65, D65_WHITE, D50_WHITE), white=D50_WHITE)


def _gamma(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def xyz_to_srgb(X: float, Y: float, Z: float) -> tuple[int, int, int]:
    """X/Y/Z on the usual 0-100 scale relative to D65 (white ~= 95.047,
    100.0, 108.883), matching the XYZ_* estimates targen writes into
    .ti1/.ti2 files (their APPROX_WHITE_POINT is D65-like)."""
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
    """Lab relative to D65 -> sRGB."""
    return xyz_to_srgb(*lab_to_xyz(L, a, b, D65_WHITE))


def lab_to_hex(L: float, a: float, b: float) -> str:
    r, g, b2 = lab_to_srgb(L, a, b)
    return f"#{r:02x}{g:02x}{b2:02x}"


def lab_d50_to_hex(L: float, a: float, b: float) -> str:
    """Lab relative to D50 (as ArgyllCMS/ICC measurements are) -> sRGB hex,
    with Bradford adaptation to sRGB's D65 white."""
    xyz_d65 = adapt_xyz(lab_to_xyz(L, a, b, D50_WHITE), D50_WHITE, D65_WHITE)
    r, g, b2 = xyz_to_srgb(*xyz_d65)
    return f"#{r:02x}{g:02x}{b2:02x}"


def delta_e_2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """CIEDE2000 color difference (Sharma, Wu & Dalal 2005 formulation)."""
    from math import atan2, cos, degrees, exp, radians, sin, sqrt

    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    c1 = sqrt(a1 * a1 + b1 * b1)
    c2 = sqrt(a2 * a2 + b2 * b2)
    c_bar = (c1 + c2) / 2
    g = 0.5 * (1 - sqrt(c_bar**7 / (c_bar**7 + 25**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = sqrt(a1p * a1p + b1 * b1), sqrt(a2p * a2p + b2 * b2)
    h1p = degrees(atan2(b1, a1p)) % 360 if c1p else 0.0
    h2p = degrees(atan2(b2, a2p)) % 360 if c2p else 0.0

    dLp = L2 - L1
    dCp = c2p - c1p
    if c1p * c2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * sqrt(c1p * c2p) * sin(radians(dhp / 2))

    Lp_bar = (L1 + L2) / 2
    Cp_bar = (c1p + c2p) / 2
    if c1p * c2p == 0:
        hp_bar = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hp_bar = (h1p + h2p) / 2
    elif h1p + h2p < 360:
        hp_bar = (h1p + h2p + 360) / 2
    else:
        hp_bar = (h1p + h2p - 360) / 2

    t = (
        1
        - 0.17 * cos(radians(hp_bar - 30))
        + 0.24 * cos(radians(2 * hp_bar))
        + 0.32 * cos(radians(3 * hp_bar + 6))
        - 0.20 * cos(radians(4 * hp_bar - 63))
    )
    d_theta = 30 * exp(-(((hp_bar - 275) / 25) ** 2))
    rc = 2 * sqrt(Cp_bar**7 / (Cp_bar**7 + 25**7))
    sl = 1 + (0.015 * (Lp_bar - 50) ** 2) / sqrt(20 + (Lp_bar - 50) ** 2)
    sc = 1 + 0.045 * Cp_bar
    sh = 1 + 0.015 * Cp_bar * t
    rt = -sin(radians(2 * d_theta)) * rc
    return sqrt(
        (dLp / sl) ** 2 + (dCp / sc) ** 2 + (dHp / sh) ** 2 + rt * (dCp / sc) * (dHp / sh)
    )
