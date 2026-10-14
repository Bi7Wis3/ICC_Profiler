"""App-wide branding, loaded from the file app_config.json points at.

Keeping brand strings (name, logo, footer credit) out of the templates means
rebranding the UI is a one-file edit instead of a grep-and-replace.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WEBAPP_DIR = Path(__file__).resolve().parent
APP_CONFIG_PATH = WEBAPP_DIR / "app_config.json"

DEFAULT_BRANDING: dict[str, Any] = {
    "app_name": "ICC Profiler",
    "short_name": "ICC Profiler",
    "header_title": "ICC Profiler",
    "tagline": "Custom ICC printer profile builder",
    "logo": "/static/images/Logo.png",
    "footer_credit_name": "",
    "footer_credit_url": "",
}


def load_branding() -> dict[str, Any]:
    app_config = json.loads(APP_CONFIG_PATH.read_text())
    branding_path = WEBAPP_DIR / app_config["branding_file"]
    branding = json.loads(branding_path.read_text())
    return {**DEFAULT_BRANDING, **branding}
