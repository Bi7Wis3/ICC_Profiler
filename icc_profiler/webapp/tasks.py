"""Background execution of pipeline stages for the web UI.

colprof at high/ultra quality can take minutes, so stage routes start the
work on a background thread and return at once; the job page polls
/jobs/<job>/task for progress. At most one task runs per job (a second
click is refused, not queued), and stages.run_stage/run_all additionally
hold the job's file lock, which also keeps the CLI out while it runs.
"""
from __future__ import annotations

import threading
import time
import traceback
from typing import Callable

from icc_profiler.errors import PipelineError

_guard = threading.Lock()
_busy: set[str] = set()
_status: dict[str, dict] = {}


def is_busy(job_name: str) -> bool:
    with _guard:
        return job_name in _busy


def status(job_name: str) -> dict | None:
    with _guard:
        st = _status.get(job_name)
        return dict(st) if st else None


def mark_seen(job_name: str) -> None:
    """The finished result has been shown to the user once; don't repeat it."""
    with _guard:
        st = _status.get(job_name)
        if st and st["state"] != "running":
            st["seen"] = True


def start(job_name: str, label: str, work: Callable[[], str]) -> bool:
    """Run work() in the background. Returns False if this job already has
    a task running. work() returns a success message or raises."""
    with _guard:
        if job_name in _busy:
            return False
        _busy.add(job_name)
        _status[job_name] = {
            "state": "running",
            "label": label,
            "started": time.time(),
            "finished": None,
            "message": "",
            "seen": False,
        }

    def _run() -> None:
        try:
            message = work()
            outcome = ("ok", message)
        except PipelineError as exc:
            outcome = ("error", str(exc))
        except Exception as exc:  # noqa: BLE001 - report, never kill the server thread silently
            traceback.print_exc()
            outcome = ("error", f"Unexpected error in {label}: {exc}")
        with _guard:
            _status[job_name].update(state=outcome[0], message=outcome[1], finished=time.time())
            _busy.discard(job_name)

    threading.Thread(target=_run, name=f"stage-{job_name}", daemon=True).start()
    return True
