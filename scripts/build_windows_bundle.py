"""Build a portable Windows bundle: embeddable Python + dependencies + ArgyllCMS.

The result needs nothing installed on the target PC: unzip it and
double-click start_profiler.bat. Runs on Linux/WSL or Windows; the
Windows wheels are fetched for the target platform, so it does not matter
which OS builds the bundle.

    python scripts/build_windows_bundle.py            # -> dist/ICC_Profiler_Windows_<version>.zip
    python scripts/build_windows_bundle.py --no-zip   # folder only

Downloads are cached in build/cache and checked against pinned SHA256 sums.
To move to a newer Python or ArgyllCMS, update the constants below (the
Python hash is listed on python.org; for ArgyllCMS, download the zip and
pin its sha256sum).
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.14.10"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"

ARGYLL_VERSION = "3.5.0"
ARGYLL_URL = f"https://www.argyllcms.com/Argyll_V{ARGYLL_VERSION}_win64_exe.zip"
ARGYLL_SHA256 = "c80f78ba30e715079a00a14c2e7c9f533c58e6c8fdbc27f2d32b3d7813f9d132"

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build" / "cache"
DIST = ROOT / "dist"
BUNDLE_NAME = "ICC_Profiler_Windows"


def project_version() -> str:
    match = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    return match.group(1) if match else "dev"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(url: str, expected_sha256: str) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    target = CACHE / url.rsplit("/", 1)[1]
    if not target.exists() or sha256(target) != expected_sha256:
        print(f"Downloading {url}")
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        tmp.replace(target)
    actual = sha256(target)
    if actual != expected_sha256:
        target.unlink()
        sys.exit(f"error: SHA256 mismatch for {url}\n  expected {expected_sha256}\n  got      {actual}")
    return target


def safe_extract(zip_path: Path, dest: Path, strip_top: bool = False) -> None:
    """Extract a zip, refusing entries that would land outside dest."""
    dest = dest.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            parts = Path(info.filename).parts
            if strip_top:
                parts = parts[1:]
            if not parts:
                continue
            out = (dest / Path(*parts)).resolve()
            if dest not in out.parents and out != dest:
                sys.exit(f"error: unsafe path in {zip_path.name}: {info.filename}")
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, out.open("wb") as dst:
                    shutil.copyfileobj(src, dst)


def setup_python(bundle: Path) -> None:
    pydir = bundle / "python"
    safe_extract(fetch(PYTHON_URL, PYTHON_SHA256), pydir)
    # The ._pth file fixes sys.path for the embeddable build. Add our
    # site-packages and the app folder, and enable `import site`.
    (pth,) = pydir.glob("python*._pth")
    lines = [ln for ln in pth.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    lines += ["Lib\\site-packages", "..\\app", "import site"]
    pth.write_text("\r\n".join(lines) + "\r\n")

    site_packages = pydir / "Lib" / "site-packages"
    major_minor = "".join(PYTHON_VERSION.split(".")[:2])
    print("Installing Windows wheels for the requirements")
    subprocess.run(
        [
            sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
            "--target", str(site_packages),
            "--platform", "win_amd64", "--python-version", major_minor,
            "--implementation", "cp", "--only-binary=:all:",
            "-r", str(ROOT / "requirements.txt"),
        ],
        check=True,
    )
    # pip drops console-script launchers into bin/; they point at the build
    # machine's Python and are useless in the bundle.
    shutil.rmtree(site_packages / "bin", ignore_errors=True)


def setup_argyll(bundle: Path) -> None:
    argyll = bundle / "argyll"
    safe_extract(fetch(ARGYLL_URL, ARGYLL_SHA256), argyll, strip_top=True)
    if not (argyll / "bin" / "targen.exe").is_file():
        sys.exit("error: ArgyllCMS zip layout changed: bin/targen.exe not found")


def copy_app(bundle: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "icc_profiler", bundle / "app" / "icc_profiler", ignore=ignore)
    shutil.copytree(ROOT / "configs", bundle / "configs", ignore=ignore)
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    (bundle / "jobs").mkdir()
    (bundle / "shared" / "it8_references").mkdir(parents=True)


def write_text_crlf(path: Path, text: str) -> None:
    path.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("ascii"))


START_BAT = r"""@echo off
rem ICC Profiler (portable Windows bundle). No installation needed.
rem Double-click to start; the browser opens on http://localhost:8000.
rem Extra arguments are passed to uvicorn, e.g. start_profiler.bat --port 8080
setlocal
cd /d "%~dp0"
set "PATH=%~dp0argyll\bin;%PATH%"
set "ICC_PROFILER_JOBS_ROOT=%~dp0jobs"

if "%~1"=="" start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000"
echo Starting ICC Profiler on http://localhost:8000 - close this window to stop.
"%~dp0python\python.exe" -m uvicorn icc_profiler.webapp.app:app %*
if errorlevel 1 pause
endlocal
"""

CLI_BAT = r"""@echo off
rem Command-line interface of the portable ICC Profiler bundle.
rem   icc_profiler.bat doctor
rem   icc_profiler.bat --help
setlocal
set "PATH=%~dp0argyll\bin;%PATH%"
set "ICC_PROFILER_JOBS_ROOT=%~dp0jobs"
"%~dp0python\python.exe" -m icc_profiler %*
endlocal
"""

BUNDLE_README = """ICC Profiler - portable Windows bundle
======================================

Nothing needs to be installed. Unzip this folder anywhere (e.g. your
Desktop) and double-click start_profiler.bat. Your browser opens on
http://localhost:8000; close the black window to stop the profiler.

Folders:
  jobs\\       your profiling jobs (charts, measurements, profiles)
  shared\\     shared IT8 reference files
  configs\\    example job configs for the command line
  app\\        the ICC Profiler program
  python\\     a private copy of Python {python}
  argyll\\     ArgyllCMS {argyll} (does the actual profiling)

Command line: icc_profiler.bat doctor   (checks that everything works)

To update, unzip the new version and copy your jobs\\ and shared\\
folders into it.

ArgyllCMS is (c) Graeme W. Gill and distributed under the GNU AGPL v3;
see argyll\\License.txt. Its source code is available at
https://www.argyllcms.com/. Python is distributed under the PSF License;
see python\\LICENSE.txt.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-zip", action="store_true", help="Leave the folder, skip making the zip")
    args = parser.parse_args()

    bundle = DIST / BUNDLE_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    setup_python(bundle)
    setup_argyll(bundle)
    copy_app(bundle)
    write_text_crlf(bundle / "start_profiler.bat", START_BAT)
    write_text_crlf(bundle / "icc_profiler.bat", CLI_BAT)
    write_text_crlf(bundle / "README.txt", BUNDLE_README.format(python=PYTHON_VERSION, argyll=ARGYLL_VERSION))
    print(f"Bundle folder: {bundle}")

    if not args.no_zip:
        zip_path = DIST / f"{BUNDLE_NAME}_{project_version()}.zip"
        zip_path.unlink(missing_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
            for path in sorted(bundle.rglob("*")):
                arc = Path(BUNDLE_NAME) / path.relative_to(bundle)
                if path.is_dir():
                    zf.write(path, arc.as_posix() + "/")
                else:
                    zf.write(path, arc.as_posix())
        print(f"Bundle zip:    {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
