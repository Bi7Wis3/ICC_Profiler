"""Per-job pipeline state, for resumable multi-session runs.

jobs/<job_name>/state.json maps stage name -> {"status": "done", "timestamp": ...}.
A stage absent from the dict (or not "done") is treated as pending.
"""
from __future__ import annotations

import json
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


def state_path(job_dir: Path) -> Path:
    return job_dir / STATE_FILENAME


def load_state(job_dir: Path) -> dict:
    path = state_path(job_dir)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(job_dir: Path, state: dict) -> None:
    state_path(job_dir).write_text(json.dumps(state, indent=2))


def is_done(state: dict, stage: str) -> bool:
    return state.get(stage, {}).get("status") == "done"


def mark_done(job_dir: Path, stage: str) -> dict:
    state = load_state(job_dir)
    state[stage] = {
        "status": "done",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    save_state(job_dir, state)
    return state


def mark_pending(job_dir: Path, stage: str) -> dict:
    """Used by --force reruns: clears a stage's done marker before re-running it."""
    state = load_state(job_dir)
    state.pop(stage, None)
    save_state(job_dir, state)
    return state
