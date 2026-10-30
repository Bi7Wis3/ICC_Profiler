"""Name and path sanitising shared by the CLI, the stages and the web UI.

Job names become folder names and URL segments, custom color names become
part of a CGATS SAMPLE_ID (written unquoted, whitespace-separated), and
uploaded file names become paths under jobs/ or shared/. One strict
character set covers all three, so nothing can escape its folder, break a
URL, or split a CGATS row.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from icc_profiler.errors import PipelineError

MAX_NAME_LENGTH = 100
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]*$")
_UNSAFE_CHARS_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def is_safe_name(value: str) -> bool:
    return bool(value) and len(value) <= MAX_NAME_LENGTH and bool(_SAFE_NAME_RE.match(value))


def safe_name(value: str, what: str = "Name") -> str:
    """Return value unchanged if it is safe, otherwise raise PipelineError.

    Allowed: letters, digits, '_', '-', '.', no leading dot, at most
    MAX_NAME_LENGTH characters. That rules out '/', '\\', '..' as a whole
    name, spaces and quotes.
    """
    value = (value or "").strip()
    if not is_safe_name(value):
        raise PipelineError(
            f"{what} {value!r} is not allowed. Use only letters, digits, '_', '-' and '.', "
            f"don't start with a dot, and keep it under {MAX_NAME_LENGTH} characters."
        )
    return value


def slugify(value: str) -> str:
    """Best-effort conversion of an arbitrary string to a safe name.

    Used where an existing (pre-validation) value must keep working, e.g. a
    custom color named "logo red" saved before names were validated.
    """
    slug = _UNSAFE_CHARS_RE.sub("_", (value or "").strip()).lstrip(".")
    slug = slug[:MAX_NAME_LENGTH]
    return slug or "unnamed"


def safe_upload_name(filename: str | None) -> str:
    """Reduce a browser-supplied upload filename to a safe bare file name.

    Browsers may send a full client path (old IE/Edge send C:\\...\\x.cie),
    so take the last component under both path conventions before cleaning.
    """
    raw = (filename or "").strip()
    base = PureWindowsPath(PurePosixPath(raw).name).name
    cleaned = slugify(base)
    if not base or cleaned in (".", "..", "unnamed"):
        raise PipelineError(f"Uploaded file name {filename!r} is not usable")
    return cleaned


def ensure_within(path: Path, root: Path) -> Path:
    """Resolve path and make sure it stays inside root (also resolved)."""
    resolved = path.resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise PipelineError(f"{path} is outside {root}")
    return resolved
