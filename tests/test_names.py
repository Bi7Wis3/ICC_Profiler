import pytest

from icc_profiler.errors import PipelineError
from icc_profiler.names import ensure_within, safe_name, safe_upload_name, slugify


@pytest.mark.parametrize("name", ["job1", "HP_PageWide_FI-1000_Scan", "a.b-c_d", "1"])
def test_safe_names_pass(name):
    assert safe_name(name) == name


@pytest.mark.parametrize("name", ["", "..", ".hidden", "a/b", "a\\b", "../x", "logo red", 'q"', "x" * 101])
def test_unsafe_names_rejected(name):
    with pytest.raises(PipelineError):
        safe_name(name)


def test_slugify_keeps_legacy_names_usable():
    assert slugify("logo red") == "logo_red"
    assert slugify("../x") == "_x"


@pytest.mark.parametrize(
    "raw, expected",
    [("it8 ref.cie", "it8_ref.cie"), ("C:\\Users\\me\\it8.cie", "it8.cie"), ("../../etc/passwd", "passwd")],
)
def test_upload_names_reduced_to_a_safe_basename(raw, expected):
    assert safe_upload_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "..", "/"])
def test_unusable_upload_names_rejected(raw):
    with pytest.raises(PipelineError):
        safe_upload_name(raw)


def test_ensure_within(tmp_path):
    (tmp_path / "jobs" / "a").mkdir(parents=True)
    assert ensure_within(tmp_path / "jobs" / "a", tmp_path / "jobs")
    with pytest.raises(PipelineError):
        ensure_within(tmp_path / "jobs" / ".." / "x", tmp_path / "jobs")
