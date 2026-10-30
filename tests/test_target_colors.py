import json
import shutil

import pytest

from icc_profiler import cgats, target_colors
from icc_profiler.errors import PipelineError

from .conftest import DATA


def test_merge_appends_to_first_table_and_keeps_the_others(tmp_path):
    ti1 = tmp_path / "chart.ti1"
    shutil.copyfile(DATA / "rgb20.ti1", ti1)
    before = cgats.read_tables(ti1)
    colors = [{"name": "logo_red", "device_value": [80, 10, 10], "target_lab": None}]
    target_colors.merge_into_ti1(ti1, colors, "RGB")
    after = cgats.read_tables(ti1)
    assert len(after) == 3
    assert len(after[0].rows) == len(before[0].rows) + 1
    assert after[1].rows == before[1].rows and after[2].rows == before[2].rows
    custom = after[0].rows[-1]
    assert custom["SAMPLE_ID"] == "CUSTOM_logo_red"
    assert float(custom["RGB_R"]) == 80


def test_legacy_name_with_space_is_slugified_so_the_row_stays_intact(tmp_path):
    ti1 = tmp_path / "chart.ti1"
    shutil.copyfile(DATA / "rgb20.ti1", ti1)
    target_colors.merge_into_ti1(ti1, [{"name": "logo red", "device_value": [1, 2, 3]}], "RGB")
    row = cgats.read(ti1).rows[-1]
    assert row["SAMPLE_ID"] == "CUSTOM_logo_red"
    assert row["RGB_B"] == "3"


def test_wrong_channel_count_rejected(tmp_path):
    ti1 = tmp_path / "chart.ti1"
    shutil.copyfile(DATA / "rgb20.ti1", ti1)
    with pytest.raises(PipelineError):
        target_colors.merge_into_ti1(ti1, [{"name": "x", "device_value": [1, 2]}], "RGB")


def test_add_validates_name_and_values(tmp_path):
    with pytest.raises(PipelineError):
        target_colors.add(tmp_path, "logo red", [1, 2, 3])
    with pytest.raises(PipelineError):
        target_colors.add(tmp_path, "ok", [1, 2, 300])
    target_colors.add(tmp_path, "ok", [1, 2, 3], [50, 0, 0])
    with pytest.raises(PipelineError, match="already exists"):
        target_colors.add(tmp_path, "ok", [1, 2, 3])
    assert json.loads((tmp_path / "target_colors.json").read_text())[0]["name"] == "ok"


def test_hex_to_device_rgb():
    assert target_colors.hex_to_device_rgb("#ff0000") == [100.0, 0.0, 0.0]
    with pytest.raises(PipelineError):
        target_colors.hex_to_device_rgb("#12")
