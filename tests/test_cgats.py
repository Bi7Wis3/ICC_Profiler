from icc_profiler import cgats

from .conftest import DATA


def test_multi_table_ti1_round_trip(tmp_path):
    tables = cgats.read_tables(DATA / "rgb20.ti1")
    assert len(tables) == 3
    out = tmp_path / "copy.ti1"
    cgats.write_tables(out, tables)
    again = cgats.read_tables(out)
    assert [t.fields for t in again] == [t.fields for t in tables]
    assert [t.rows for t in again] == [t.rows for t in tables]


def test_quoted_fields_are_unquoted_on_read_and_requoted_on_write(tmp_path):
    table = cgats.read(DATA / "rgb20.ti2")
    assert table.quoted_fields == {"SAMPLE_LOC"}
    assert not table.rows[0]["SAMPLE_LOC"].startswith('"')
    out = tmp_path / "copy.ti2"
    table.write(out)
    first_row = out.read_text().split("BEGIN_DATA\n")[1].splitlines()[0]
    assert f'"{table.rows[0]["SAMPLE_LOC"]}"' in first_row
    assert cgats.read(out).rows == table.rows


def test_quoted_string_with_spaces_stays_one_value(tmp_path):
    path = tmp_path / "t.ti3"
    path.write_text(
        "CTI3\nBEGIN_DATA_FORMAT\nSAMPLE_ID LAB_L\nEND_DATA_FORMAT\n"
        'NUMBER_OF_SETS 1\nBEGIN_DATA\n"logo red" 50.0\nEND_DATA\n'
    )
    table = cgats.read(path)
    assert table.rows == [{"SAMPLE_ID": "logo red", "LAB_L": "50.0"}]
    table.write(path)
    assert cgats.read(path).rows == table.rows


def test_render_quotes_values_with_whitespace_and_empty_values():
    table = cgats.CgatsTable("CTI1", [], ["A", "B", "C"], [{"A": "x y", "B": "", "C": "1"}])
    assert '"x y" "" 1' in table.render()
