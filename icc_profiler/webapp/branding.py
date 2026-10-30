"""App-wide branding, loaded from the file app_config.json points at.

Keeping brand strings (name, logo, footer credit) out of the templates means
rebranding the UI is a one-file edit instead of a grep-and-replace. A
missing or broken app_config.json / branding file falls back to the
defaults below (with a warning) instead of stopping the app from starting.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

WEBAPP_DIR = Path(__file__).resolve().parent
APP_CONFIG_PATH = WEBAPP_DIR / "app_config.json"

log = logging.getLogger(__name__)

DEFAULT_BRANDING: dict[str, Any] = {
    "app_name": "ICC Profiler",
    "short_name": "ICC Profiler",
    "header_title": "ICC Profiler",
    "tagline": "Custom ICC printer profile builder",
    "logo": "/static/images/Logo.png",
    "footer_credit_name": "",
    "footer_credit_url": "",
}


def load_branding(app_config_path: Path = APP_CONFIG_PATH) -> dict[str, Any]:
    try:
        app_config = json.loads(app_config_path.read_text())
        branding_path = app_config_path.parent / app_config.get("branding_file", "branding.json")
        branding = json.loads(branding_path.read_text())
        if not isinstance(branding, dict):
            raise ValueError(f"{branding_path} must contain a JSON object")
    except (OSError, ValueError, AttributeError) as exc:
        log.warning("Using default branding: %s", exc)
        return dict(DEFAULT_BRANDING)
    return {**DEFAULT_BRANDING, **branding}
