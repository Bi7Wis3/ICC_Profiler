import json
import time

import pytest
from fastapi.testclient import TestClient

from icc_profiler import state
from icc_profiler.webapp import branding, tasks
from icc_profiler.webapp.app import app
from icc_profiler.webapp.common import JOBS_ROOT

from .conftest import write_csv_for_chart

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def client(fake_argyll):
    return TestClient(app)


def _create(client, name, method="colorimeter_csv"):
    return client.post(
        "/jobs/new",
        data={"job_name": name, "method": method, "patch_count": "20", "grey_steps": "4"},
        headers=ORIGIN,
        follow_redirects=False,
    )


def _wait_idle(name, timeout=5):
    deadline = time.time() + timeout
    while tasks.is_busy(name) and time.time() < deadline:
        time.sleep(0.02)
    return tasks.status(name)


def _run_stage(client, name, stage):
    r = client.post(f"/jobs/{name}/stage/{stage}", headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    return _wait_idle(name)


def test_create_job_and_reject_unsafe_name(client):
    assert _create(client, "web_ok").status_code == 303
    r = _create(client, "../escape")
    assert r.status_code == 400
    assert not (JOBS_ROOT.parent / "escape").exists()
    assert _create(client, "web_ok").status_code == 400  # duplicate


@pytest.mark.parametrize("path", ["/jobs/.hidden", "/jobs/no_such_job", "/jobs/a%20b"])
def test_bad_job_names_are_404(client, path):
    assert client.get(path).status_code == 404


def test_cross_site_post_is_refused(client):
    _create(client, "web_csrf")
    r = client.post("/jobs/web_csrf/delete", headers={"origin": "https://evil.example"}, follow_redirects=False)
    assert r.status_code == 403
    assert (JOBS_ROOT / "web_csrf").is_dir()
    r = client.post("/jobs/web_csrf/delete", headers={"referer": "https://evil.example/x"}, follow_redirects=False)
    assert r.status_code == 403
    r = client.post("/jobs/web_csrf/delete", headers=ORIGIN, follow_redirects=False)
    assert r.status_code == 303
    assert not (JOBS_ROOT / "web_csrf").exists()


def test_unknown_host_is_refused(client):
    assert client.get("/", headers={"host": "attacker.example"}).status_code == 400


def test_log_and_preview_names_are_checked(client):
    _create(client, "web_paths")
    assert client.get("/jobs/web_paths/logs/..%2Fconfig").status_code == 404
    assert client.get("/jobs/web_paths/logs/not-a-stage").status_code == 404
    assert client.get("/jobs/web_paths/chart/preview/config.json").status_code == 404


def test_stage_runs_in_background_and_refuses_double_start(client, monkeypatch):
    _create(client, "web_bg")
    status = _run_stage(client, "web_bg", "generate-chart")
    assert status["state"] == "ok", status
    jd = JOBS_ROOT / "web_bg"
    assert state.is_done(json.loads((jd / "state.json").read_text()), "generate-chart")

    # Simulate a stage still running: a second start is refused.
    monkeypatch.setattr(tasks, "_busy", {"web_bg"})
    r = client.post("/jobs/web_bg/stage/generate-chart", headers=ORIGIN, follow_redirects=False)
    assert "already%20running" in r.headers["location"]
    r = client.post("/jobs/web_bg/edit", data={"patch_count": "30"}, headers=ORIGIN)
    assert "running" in r.text
    assert client.get("/jobs/web_bg/task").json()["state"] in ("ok", "running")


def test_upload_csv_binds_it_to_the_chart_and_full_run_completes(client):
    _create(client, "web_csv")
    r = client.post(
        "/jobs/web_csv/measurements/upload-csv",
        files={"file": ("x.csv", b"Name,L*,a*,b*\n")},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"]  # no chart yet

    _run_stage(client, "web_csv", "generate-chart")
    jd = JOBS_ROOT / "web_csv"
    from icc_profiler.config import JobConfig

    config = JobConfig.load(jd / "config.json")
    csv_path = write_csv_for_chart(jd, config, dest=jd / "upload.csv")
    r = client.post(
        "/jobs/web_csv/measurements/upload-csv",
        files={"file": ("my export.csv", csv_path.read_bytes())},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert "ok=" in r.headers["location"]
    saved = json.loads((jd / "config.json").read_text())["measurement"]["colorimeter_csv"]["csv_file"]
    assert saved == "02_measurements/colorimeter_export.csv"

    # Import is blocked until the illuminant/observer is confirmed.
    status = _run_stage(client, "web_csv", "import-measurements")
    assert status["state"] == "error" and "D50" in status["message"]
    r = client.post("/jobs/web_csv/measurements/lab-reference", data={"lab_reference": "D50_2"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in r.headers["location"]  # checkbox required
    client.post(
        "/jobs/web_csv/measurements/lab-reference",
        data={"lab_reference": "D50_2", "confirmed": "true"},
        headers=ORIGIN,
    )
    r = client.post("/jobs/web_csv/run", data={}, headers=ORIGIN, follow_redirects=False)
    status = _wait_idle("web_csv")
    assert status["state"] == "ok" and "All stages complete" in status["message"], status
    assert client.get("/jobs/web_csv/profile.icc").status_code == 200
    page = client.get("/jobs/web_csv")
    assert "All stages complete" in page.text
    assert client.get("/jobs/web_csv/results").status_code == 200

    # Changing chart settings marks everything out of date and hides the stale profile.
    client.post("/jobs/web_csv/edit", data={"patch_count": "40", "grey_steps": "4"}, headers=ORIGIN)
    st = state.load_state(jd, state.COLORIMETER_CSV_STAGES)
    assert not any(state.is_done(st, s) for s in state.COLORIMETER_CSV_STAGES)
    assert client.get("/jobs/web_csv/profile.icc").status_code == 404


def test_custom_color_change_invalidates_chart(client):
    _create(client, "web_colors")
    _run_stage(client, "web_colors", "generate-chart")
    r = client.post(
        "/jobs/web_colors/colors/add",
        data={"name": "logo red", "input_mode": "hex", "hex_value": "#cc0000"},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert "error=" in r.headers["location"]  # space not allowed
    r = client.post(
        "/jobs/web_colors/colors/add",
        data={"name": "logo_red", "input_mode": "hex", "hex_value": "#cc0000"},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert "regenerate" in r.headers["location"] or "re-run" in r.headers["location"]
    assert not state.is_done(state.load_state(JOBS_ROOT / "web_colors"), "generate-chart")


def test_branding_falls_back_to_defaults(tmp_path):
    assert branding.load_branding(tmp_path / "missing.json") == branding.DEFAULT_BRANDING
    cfg = tmp_path / "app_config.json"
    cfg.write_text('{"branding_file": "nope.json"}')
    assert branding.load_branding(cfg)["app_name"] == "ICC Profiler"
    (tmp_path / "b.json").write_text('{"app_name": "X"}')
    cfg.write_text('{"branding_file": "b.json"}')
    assert branding.load_branding(cfg)["app_name"] == "X"


def test_check_chart_flow(client):
    _create(client, "web_check")
    _run_stage(client, "web_check", "generate-chart")
    jd = JOBS_ROOT / "web_check"
    from icc_profiler.config import JobConfig

    config = JobConfig.load(jd / "config.json")
    r = client.post("/jobs/web_check/check/upload", files={"file": ("c.csv", b"x")}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in r.headers["location"]  # no check chart yet
    r = client.post("/jobs/web_check/check/generate", data={"patch_count": "5"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in r.headers["location"]  # too few patches
    r = client.post("/jobs/web_check/check/generate", data={"patch_count": "40"}, headers=ORIGIN, follow_redirects=False)
    assert "ok=" in r.headers["location"]

    check_ti2 = jd / "06_check" / "web_check_check.ti2"
    csv_bytes = write_csv_for_chart(jd, config, dest=jd / "c.csv").read_bytes()
    assert check_ti2.is_file()  # fake printtarg reuses the main chart's labels, so the main CSV fits
    r = client.post("/jobs/web_check/check/upload", files={"file": ("check.csv", csv_bytes)}, headers=ORIGIN, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    r = client.post("/jobs/web_check/check/run", headers=ORIGIN, follow_redirects=False)
    assert "error=" in r.headers["location"] and "profile" in r.headers["location"]  # not built yet

    client.post("/jobs/web_check/measurements/upload-csv", files={"file": ("m.csv", csv_bytes)}, headers=ORIGIN)
    client.post(
        "/jobs/web_check/measurements/lab-reference",
        data={"lab_reference": "D50_2", "confirmed": "true"},
        headers=ORIGIN,
    )
    client.post("/jobs/web_check/run", data={}, headers=ORIGIN)
    assert _wait_idle("web_check")["state"] == "ok"
    r = client.post("/jobs/web_check/check/run", headers=ORIGIN, follow_redirects=False)
    assert "ok=" in r.headers["location"], r.headers["location"]
    page = client.get("/jobs/web_check").text
    assert "average ΔE 0.50" in page and "Check chart" in page

    # A new check chart sets the old export aside rather than deleting it.
    client.post("/jobs/web_check/check/generate", data={"patch_count": "40"}, headers=ORIGIN)
    assert list((jd / "06_check").glob("check_export.old-*.csv"))
    assert "average ΔE" not in client.get("/jobs/web_check").text


def test_spot_colors_page(client):
    _create(client, "web_spot")
    _run_stage(client, "web_spot", "generate-chart")
    jd = JOBS_ROOT / "web_spot"
    from icc_profiler.config import JobConfig

    config = JobConfig.load(jd / "config.json")
    r = client.post("/jobs/web_spot/spot/add-typed", data={"lines": "Red; 50; 40; 30"}, headers=ORIGIN, follow_redirects=False)
    assert "error=" in r.headers["location"] and r.headers["location"].startswith("/jobs/web_spot/spot")

    csv_bytes = write_csv_for_chart(jd, config, dest=jd / "m.csv").read_bytes()
    client.post("/jobs/web_spot/measurements/upload-csv", files={"file": ("m.csv", csv_bytes)}, headers=ORIGIN)
    client.post(
        "/jobs/web_spot/measurements/lab-reference", data={"lab_reference": "D50_2", "confirmed": "true"}, headers=ORIGIN
    )
    client.post("/jobs/web_spot/run", data={}, headers=ORIGIN)
    assert _wait_idle("web_spot")["state"] == "ok"
    assert "/jobs/web_spot/spot" in client.get("/jobs/web_spot").text

    r = client.post(
        "/jobs/web_spot/spot/add-typed",
        data={"lines": "PANTONE 185 C; 50; 40; 30\nBlue; 40; 10; -50"},
        headers=ORIGIN,
        follow_redirects=False,
    )
    assert "ok=" in r.headers["location"]
    page = client.get("/jobs/web_spot/spot").text
    assert "PANTONE 185 C" in page and "out of gamut" in page
    assert client.get("/jobs/web_spot/spot/recipes.csv").text.startswith("Name,")
    r = client.post("/jobs/web_spot/spot/proof", headers=ORIGIN, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    assert "Proof sheet export" in client.get("/jobs/web_spot/spot").text
    r = client.post("/jobs/web_spot/spot/remove", data={"name": "Blue"}, headers=ORIGIN, follow_redirects=False)
    assert "Blue" not in client.get("/jobs/web_spot/spot").text.split("Add colors")[0]
