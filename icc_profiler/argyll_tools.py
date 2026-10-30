"""Cross-platform ArgyllCMS binary discovery and subprocess execution.

`shutil.which` already honors PATHEXT on Windows (so "targen" resolves to
"targen.exe" without us special-casing it), which is what lets the same code
run unchanged on WSL/Linux and native Windows -- the only platform knob is
an optional explicit bin_dir override for users who don't want to touch PATH.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from icc_profiler.errors import ArgyllNotFoundError, PipelineError

REQUIRED_TOOLS = ["targen", "printtarg", "scanin", "colprof", "profcheck"]

# Keep a decent tail of output in raised errors without dumping megabytes
# of Argyll verbose logging into an exception message.
_ERROR_TAIL_LINES = 20


def find_bin(tool_name: str, bin_dir: str | None = None) -> str:
    if bin_dir:
        candidate = Path(bin_dir) / tool_name
        resolved = shutil.which(str(candidate), path=bin_dir)
        if resolved:
            return resolved
        # shutil.which with an explicit dir sometimes needs the exact
        # extension on Windows if PATHEXT resolution doesn't kick in for a
        # bare `path=` lookup; fall through to a direct existence check.
        for ext in ("", ".exe", ".bat"):
            direct = Path(bin_dir) / f"{tool_name}{ext}"
            if direct.is_file():
                return str(direct)
        raise ArgyllNotFoundError(tool_name, bin_dir=bin_dir)

    resolved = shutil.which(tool_name)
    if not resolved:
        raise ArgyllNotFoundError(tool_name)
    return resolved


# Candidate locations for ArgyllCMS's bundled reference-chart templates
# (.cht files for known chart types, e.g. IT8.7 targets), tried in order.
# Confirmed present on this WSL/Ubuntu install via the `argyll-ref` package
# at /usr/share/color/argyll/ref (dpkg -L argyll-ref). The other candidates
# cover the official upstream tarball/zip layout (a `ref/` dir alongside
# `bin/`) used on native Windows and manual Linux installs -- unverified
# against an actual non-apt install, but match Argyll's documented layout.
_REF_DIR_CANDIDATES = [
    Path("/usr/share/color/argyll/ref"),
    Path("/usr/share/argyll/ref"),
]


def find_ref_dir(bin_dir: str | None = None) -> Path | None:
    """Locate ArgyllCMS's bundled reference-chart-template directory, if any."""
    candidates = list(_REF_DIR_CANDIDATES)
    if bin_dir:
        candidates.insert(0, Path(bin_dir).parent / "ref")
    else:
        try:
            targen = find_bin("targen")
            candidates.insert(0, Path(targen).resolve().parent.parent / "ref")
        except ArgyllNotFoundError:
            pass
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def list_bundled_cht_templates(bin_dir: str | None = None) -> list[str]:
    """Names of ArgyllCMS's bundled .cht chart-recognition templates, if found."""
    ref_dir = find_ref_dir(bin_dir)
    if ref_dir is None:
        return []
    return sorted(p.name for p in ref_dir.glob("*.cht"))


def doctor(bin_dir: str | None = None) -> dict[str, dict]:
    """Resolve every required tool and report path + version banner (or the error)."""
    results: dict[str, dict] = {}
    for tool in REQUIRED_TOOLS:
        try:
            path = find_bin(tool, bin_dir=bin_dir)
        except ArgyllNotFoundError as exc:
            results[tool] = {"found": False, "error": str(exc)}
            continue
        version_output = ""
        try:
            # Argyll tools print usage/version banner to stderr on a bare
            # invocation or `-?`; exit code is non-zero either way, which is
            # expected here -- we only want the banner text, not success.
            proc = subprocess.run(
                [path], capture_output=True, text=True, timeout=10
            )
            version_output = (proc.stdout + proc.stderr).strip().splitlines()
            version_output = version_output[0] if version_output else ""
        except Exception as exc:  # noqa: BLE001 - best-effort banner only
            version_output = f"(could not run for version banner: {exc})"
        results[tool] = {"found": True, "path": path, "banner": version_output}
    return results


def run(
    argv: list[str],
    *,
    log_file: Path | None = None,
    cwd: Path | None = None,
    timeout: int | None = 300,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """Run an Argyll CLI command, tee output to log_file, raise PipelineError on failure."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            input=input,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ArgyllNotFoundError(argv[0]) from exc
    except subprocess.TimeoutExpired as exc:
        raise PipelineError(
            f"Command timed out after {timeout}s: {' '.join(argv)}"
        ) from exc

    combined = (proc.stdout or "") + (proc.stderr or "")
    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with log_file.open("a") as f:
            f.write(f"$ {' '.join(argv)}\n{combined}\n")

    if proc.returncode != 0:
        tail = "\n".join(combined.strip().splitlines()[-_ERROR_TAIL_LINES:])
        raise PipelineError(
            f"Command failed (exit {proc.returncode}): {' '.join(argv)}\n"
            f"--- last {_ERROR_TAIL_LINES} lines of output ---\n{tail}"
        )
    return proc
