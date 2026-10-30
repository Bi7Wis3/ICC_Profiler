import json

from icc_profiler import state

ORDER = state.SCANNER_STAGES


def _write(job_dir, data):
    (job_dir / "state.json").write_text(json.dumps(data))


def test_mark_done_clears_every_later_stage(tmp_path):
    for s in ORDER:
        state.mark_done(tmp_path, s, ORDER)
    state.mark_done(tmp_path, "build-scanner-profile", ORDER)
    st = state.load_state(tmp_path, ORDER)
    assert [s for s in ORDER if state.is_done(st, s)] == ["generate-chart", "build-scanner-profile"]


def test_out_of_order_timestamps_are_not_trusted(tmp_path):
    # The real jobs/scanner_test state: build-profile stamped before
    # read-chart, so build-profile and verify are stale.
    _write(
        tmp_path,
        {
            "generate-chart": {"status": "done", "timestamp": "2026-10-13T05:57:08.731374+00:00"},
            "build-scanner-profile": {"status": "done", "timestamp": "2026-10-13T05:57:11.925210+00:00"},
            "read-chart": {"status": "done", "timestamp": "2026-10-13T05:57:14.943241+00:00"},
            "build-profile": {"status": "done", "timestamp": "2026-10-13T05:56:14.750263+00:00"},
            "verify": {"status": "done", "timestamp": "2026-10-13T05:57:22.504047+00:00"},
        },
    )
    st = state.load_state(tmp_path, ORDER)
    assert state.is_done(st, "read-chart")
    assert not state.is_done(st, "build-profile")
    assert not state.is_done(st, "verify")
    assert state.stale_stages(tmp_path, ORDER) == ["build-profile", "verify"]


def test_done_after_a_pending_stage_is_not_trusted(tmp_path):
    _write(tmp_path, {"generate-chart": {"status": "done", "timestamp": "a"}, "verify": {"status": "done", "timestamp": "b"}})
    assert not state.is_done(state.load_state(tmp_path, ORDER), "verify")


def test_invalidate_from_reports_what_it_cleared(tmp_path):
    for s in ORDER[:3]:
        state.mark_done(tmp_path, s, ORDER)
    assert state.invalidate_from(tmp_path, "build-scanner-profile", ORDER) == ["build-scanner-profile", "read-chart"]
    assert state.invalidate_from(tmp_path, "build-scanner-profile", ORDER) == []
    assert state.is_done(state.load_state(tmp_path, ORDER), "generate-chart")
