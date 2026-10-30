"""Per-job pipeline state, for resumable multi-session runs.

jobs/<job_name>/state.json maps stage name -> {"status": "done", "timestamp": ...}.
A stage absent from the dict (or not "done") is treated as pending.

Stages form a chain: each one consumes the output of the one before it.
So whenever a stage runs (or one of its inputs changes), every stage after
it is cleared -- otherwise e.g. "verify" stays "done" against a profile
built from measurements that have since been replaced. load_state() with
an explicit order also drops any "done" marker that is older than an
earlier stage's, which repairs state files written before this rule
existed.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

STATE_FILENAME = "state.json"

# Canonical stage order for the scanner measurement method.
SCANNER_STAGES = [
    "generate-chart",
    "build-scanner-profile",
    "read-chart",
    "build-profile",
    "verify",
]

# Canonical stage order for the colorimeter-CSV measurement method.
COLORIMETER_CSV_STAGES = [
    "generate-chart",
    "import-measurements",
    "build-profile",
    "verify",
]


def stage_order(method: str) -> list[str]:
    if method == "scanner":
        return list(SCANNER_STAGES)
    if method == "colorimeter_csv":
        return list(COLORIMETER_CSV_STAGES)
    raise ValueError(f"Unknown measurement method: {method!r}")


def now_iso() -> str:
    # Fixed microsecond precision so timestamps compare correctly as strings.
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def state_path(job_dir: Path) -> Path:
    return job_dir / STATE_FILENAME


def _read_raw(job_dir: Path) -> dict:
    path = state_path(job_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def consistent(state: dict, order: list[str]) -> dict:
    """Drop every "done" marker that can't be trusted given the stage order:
    one whose predecessor isn't done, or one stamped earlier than its
    predecessor (the predecessor was re-run after it, so it's stale)."""
    result = {k: v for k, v in state.items() if k not in order}
    previous_ts = ""
    for stage in order:
        entry = state.get(stage)
        if not entry or entry.get("status") != "done":
            break
        ts = entry.get("timestamp", "")
        if ts < previous_ts:
            break
        result[stage] = entry
        previous_ts = ts
    return result


def load_state(job_dir: Path, order: list[str] | None = None) -> dict:
    """Load state.json. With `order`, return only trustworthy "done" markers
    (see consistent())."""
    state = _read_raw(job_dir)
    return consistent(state, order) if order else state


def save_state(job_dir: Path, state: dict) -> None:
    # Write then rename, so a page loading while a background stage saves
    # never reads a half-written file.
    path = state_path(job_dir)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)


def is_done(state: dict, stage: str) -> bool:
    return state.get(stage, {}).get("status") == "done"


def stale_stages(job_dir: Path, order: list[str]) -> list[str]:
    """Stages state.json marks done that consistent() doesn't trust."""
    raw = _read_raw(job_dir)
    good = consistent(raw, order)
    return [s for s in order if is_done(raw, s) and not is_done(good, s)]


def _stages_after(stage: str, order: list[str]) -> list[str]:
    return order[order.index(stage) + 1 :] if stage in order else []


def mark_done(job_dir: Path, stage: str, order: list[str] | None = None) -> dict:
    """Mark `stage` done and, given the stage order, clear every stage after it."""
    state = _read_raw(job_dir)
    state[stage] = {
        "status": "done",
        "timestamp": now_iso(),
    }
    for later in _stages_after(stage, order or []):
        state.pop(later, None)
    save_state(job_dir, state)
    return state


def mark_pending(job_dir: Path, stage: str) -> dict:
    """Clear one stage's done marker."""
    state = _read_raw(job_dir)
    state.pop(stage, None)
    save_state(job_dir, state)
    return state


def invalidate_from(job_dir: Path, stage: str, order: list[str]) -> list[str]:
    """Clear `stage` and every stage after it. Returns the stages that were
    actually marked done before (so callers can tell the user)."""
    if stage not in order:
        return []
    state = _read_raw(job_dir)
    cleared = []
    for s in order[order.index(stage) :]:
        if is_done(state, s):
            cleared.append(s)
        state.pop(s, None)
    if cleared:
        save_state(job_dir, state)
    return cleared
