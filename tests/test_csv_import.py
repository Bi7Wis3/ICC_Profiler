import pytest

from icc_profiler import cgats, csv_import
from icc_profiler.errors import PipelineError

from .conftest import DATA

TI2 = DATA / "rgb20.ti2"
LOCS = [r["SAMPLE_LOC"] for r in cgats.read(TI2).rows]


def _csv(tmp_path, lines, *, encoding="utf-8", name="export.csv", newline="\n"):
    path = tmp_path / name
    path.write_bytes((newline.join(lines) + newline).encode(encoding))
    return path


def _rows(sep=",", dec="."):
    rows = []
    for i, loc in enumerate(LOCS):
        values = [f"{50 + i / 10:.1f}", f"{i - 5:.1f}", f"{5 - i:.1f}"]
        rows.append(sep.join([loc, *(v.replace(".", dec) for v in values)]))
    return rows


def test_merge_writes_one_lab_row_per_patch(tmp_path):
    path = _csv(tmp_path, ["Name,L*,a*,b*", *_rows()])
    table = csv_import.merge(TI2, path)
    assert len(table.rows) == len(LOCS)
    assert table.fields[-3:] == ["LAB_L", "LAB_A", "LAB_B"]
    assert "SAMPLE_LOC" not in table.fields and "XYZ_X" not in table.fields
    assert 'COLOR_REP "iRGB_LAB"' in table.preamble


def test_cmyk_color_rep_matches_what_colprof_accepts(tmp_path):
    ti2 = DATA / "cmyk20.ti2"
    locs = [r["SAMPLE_LOC"] for r in cgats.read(ti2).rows]
    path = _csv(tmp_path, ["Name,L*,a*,b*", *[f"{loc},50,0,0" for loc in locs]])
    # printtarg writes plain "CMYK" (no leading i); colprof rejects "iCMYK_LAB".
    assert 'COLOR_REP "CMYK_LAB"' in csv_import.merge(ti2, path).preamble


def test_missing_reading_is_reported(tmp_path):
    path = _csv(tmp_path, ["Name,L*,a*,b*", *_rows()[1:]])
    with pytest.raises(PipelineError, match="missing measurements"):
        csv_import.merge(TI2, path)


def test_extra_reading_is_reported(tmp_path):
    path = _csv(tmp_path, ["Name,L*,a*,b*", *_rows(), "ZZ99,50,0,0"])
    with pytest.raises(PipelineError, match="ZZ99"):
        csv_import.merge(TI2, path)


def test_duplicate_reading_is_reported(tmp_path):
    rows = _rows()
    path = _csv(tmp_path, ["Name,L*,a*,b*", *rows, rows[0]])
    with pytest.raises(PipelineError, match="more than one row"):
        csv_import.merge(TI2, path)


def test_utf8_bom_header(tmp_path):
    path = _csv(tmp_path, ["Name,L*,a*,b*", *_rows()], encoding="utf-8-sig", newline="\r\n")
    assert len(csv_import.merge(TI2, path).rows) == len(LOCS)


def test_utf16_export(tmp_path):
    path = _csv(tmp_path, ["Name\tL*\ta*\tb*", *_rows(sep="\t")], encoding="utf-16")
    assert len(csv_import.merge(TI2, path).rows) == len(LOCS)


def test_semicolons_and_decimal_commas(tmp_path):
    path = _csv(tmp_path, ["Name;L*;a*;b*", *_rows(sep=";", dec=",")])
    table = csv_import.merge(TI2, path)
    first = next(r for r in table.rows if r["SAMPLE_ID"] == cgats.read(TI2).rows[0]["SAMPLE_ID"])
    assert float(first["LAB_L"]) == pytest.approx(50.0)


def test_decimal_comma_in_comma_separated_file_is_caught(tmp_path):
    path = _csv(tmp_path, ["Name,L*,a*,b*", *[f"{loc},50,3,1,2,-3,4" for loc in LOCS]])
    with pytest.raises(PipelineError, match="decimal commas"):
        csv_import.merge(TI2, path)


def test_headers_match_case_insensitively_and_ignore_spaces(tmp_path):
    path = _csv(tmp_path, [" name , l* ,A*, B* ", *_rows()])
    assert len(csv_import.merge(TI2, path).rows) == len(LOCS)


def test_missing_column_names_the_column(tmp_path):
    path = _csv(tmp_path, ["Sample,L,a,b", *_rows()])
    with pytest.raises(PipelineError, match="missing expected column"):
        csv_import.merge(TI2, path)


@pytest.mark.parametrize("bad", ["abc,0,0", "120,0,0", "-1,0,0", "50,200,0", "50,0,-151", ",0,0"])
def test_non_numeric_or_out_of_range_values_rejected(tmp_path, bad):
    rows = _rows()
    rows[0] = f"{LOCS[0]},{bad}"
    path = _csv(tmp_path, ["Name,L*,a*,b*", *rows])
    with pytest.raises(PipelineError, match=LOCS[0]):
        csv_import.merge(TI2, path)


def test_d65_readings_are_adapted_to_d50(tmp_path):
    path = _csv(tmp_path, ["Name,L*,a*,b*", *[f"{loc},100,0,0" for loc in LOCS]])
    d50 = csv_import.merge(TI2, path, lab_reference="D50_2")
    d65 = csv_import.merge(TI2, path, lab_reference="D65_2")
    # A perfect white stays white under adaptation; a chromatic color moves.
    assert float(d65.rows[0]["LAB_A"]) == pytest.approx(0, abs=0.05)
    path = _csv(tmp_path, ["Name,L*,a*,b*", *[f"{loc},50,40,-30" for loc in LOCS]])
    d50 = csv_import.merge(TI2, path, lab_reference="D50_2")
    d65 = csv_import.merge(TI2, path, lab_reference="D65_2")
    assert float(d50.rows[0]["LAB_A"]) == 40
    # Lab is already white-relative, so the Bradford shift is small but real.
    moved = (float(d65.rows[0]["LAB_A"]) - 40, float(d65.rows[0]["LAB_B"]) + 30)
    assert 0.05 < max(abs(m) for m in moved) < 3


def test_unknown_lab_reference_rejected(tmp_path):
    path = _csv(tmp_path, ["Name,L*,a*,b*", *_rows()])
    with pytest.raises(PipelineError):
        csv_import.merge(TI2, path, lab_reference="D65_10")


# -- SQCX (3nh PS110) exports: xlsx, measuring order, SCI/SCE, Ill/Obs ---------

SQCX = DATA / "sqcx_ps110_sample.xlsx"


def test_sqcx_xlsx_is_read_without_openpyxl():
    readings = csv_import.read_readings(
        SQCX, csv_import.DEFAULT_COLUMN_MAP, specular="SCE", ignore_prefixes=["Standard"], lab_reference="D50_2"
    )
    assert [name for name, _ in readings] == ["Sample1", "Sample2", "Sample3"]
    assert readings[0][1] == pytest.approx((39.5, -0.2, -16.4))  # text-stored "-16.40" parsed


def test_sqcx_sci_rows_when_asked():
    readings = csv_import.read_readings(SQCX, csv_import.DEFAULT_COLUMN_MAP, specular="SCI", ignore_prefixes=["Standard"])
    assert readings[0][1] == pytest.approx((41.76, -0.15, -16.13))


def test_ill_obs_column_must_match_job_setting():
    with pytest.raises(PipelineError, match="D50/2"):
        csv_import.read_readings(SQCX, csv_import.DEFAULT_COLUMN_MAP, specular="SCE", lab_reference="D65_2")


def test_missing_specular_mode_is_explained(tmp_path):
    path = _csv(tmp_path, ["Name,Specular Component,L*,a*,b*", "Sample1,SCI,50,0,0"])
    with pytest.raises(PipelineError, match="no SCE readings"):
        csv_import.read_readings(path, csv_import.DEFAULT_COLUMN_MAP, specular="SCE")


def test_loc_sort_key_walks_down_each_column():
    locs = ["B1", "A10", "A2", "A1", "J27", "B10"]
    assert sorted(locs, key=csv_import.loc_sort_key) == ["A1", "A2", "A10", "B1", "B10", "J27"]


def test_merge_by_measuring_order(tmp_path):
    ordered = sorted(LOCS, key=csv_import.loc_sort_key)
    lines = ["Name,Specular Component,Ill/Obs,L*,a*,b*", "Standard1,SCE,D50/2°,95,0,0"]
    for i, _ in enumerate(ordered):
        lines.append(f"Sample{i + 1},SCI,D50/2°,99,0,0")
        lines.append(f"Sample{i + 1},SCE,D50/2°,{10 + i},0,0")
    path = _csv(tmp_path, lines)
    table = csv_import.merge(
        TI2, path, lab_reference="D50_2", match_by="order", specular="SCE", ignore_prefixes=["Standard"]
    )
    loc_by_id = {r["SAMPLE_ID"]: r["SAMPLE_LOC"] for r in cgats.read(TI2).rows}
    for row in table.rows:
        assert float(row["LAB_L"]) == 10 + ordered.index(loc_by_id[row["SAMPLE_ID"]])


def test_measuring_order_count_mismatch_names_next_patch(tmp_path):
    ordered = sorted(LOCS, key=csv_import.loc_sort_key)
    path = _csv(tmp_path, ["Name,L*,a*,b*", "Sample1,50,0,0", "Sample2,50,0,0"])
    with pytest.raises(PipelineError, match=f"next one to measure would be {ordered[2]}"):
        csv_import.merge(TI2, path, match_by="order")
