import pytest

from icc_profiler import color_convert as cc


# Reference pairs from Sharma, Wu & Dalal (2005), "The CIEDE2000 color-difference formula".
@pytest.mark.parametrize(
    "lab1, lab2, expected",
    [
        ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
        ((50.0, 2.5, 0.0), (50.0, 0.0, -2.5), 4.3065),
        ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
        ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
    ],
)
def test_delta_e_2000_reference_values(lab1, lab2, expected):
    assert cc.delta_e_2000(lab1, lab2) == pytest.approx(expected, abs=1e-4)


def test_d50_white_renders_as_srgb_white():
    assert cc.lab_d50_to_hex(100, 0, 0) == "#ffffff"


def test_d50_swatches_use_chromatic_adaptation():
    # Without adaptation, D50 white rendered with a D65 white point comes out
    # visibly yellow-tinted rather than neutral.
    assert cc.lab_to_hex(100, 0, 0) == "#ffffff"
    assert cc.lab_d50_to_hex(50, 0, 0) == cc.lab_to_hex(50, 0, 0)


def test_lab_d65_to_d50_keeps_neutrals_neutral():
    L, a, b = cc.lab_d65_to_d50(60, 0, 0)
    assert (L, a, b) == pytest.approx((60, 0, 0), abs=0.05)


def test_lab_xyz_round_trip():
    lab = (42.0, 18.0, -33.0)
    assert cc.xyz_to_lab(*cc.lab_to_xyz(*lab, cc.D50_WHITE), white=cc.D50_WHITE) == pytest.approx(lab, abs=1e-6)
