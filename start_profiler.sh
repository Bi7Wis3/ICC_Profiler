#!/usr/bin/env bash
# Starts the ICC Profiler web UI using the project's .venv.
# Any extra arguments are passed straight through to uvicorn,
# e.g. ./start_profiler.sh --port 8080
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x ".venv/bin/uvicorn" ]; then
    echo "error: .venv/bin/uvicorn not found. Set up the venv first:" >&2
    echo "  python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt" >&2
    exit 1
fi

echo "Starting ICC Profiler on http://localhost:8000 (Ctrl+C to stop)"
exec .venv/bin/uvicorn icc_profiler.webapp.app:app --reload "$@"
