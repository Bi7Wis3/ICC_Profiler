"""Build the portable ICC Profiler: runs from a USB drive on Windows and Linux.

Nothing needs to be installed on the PC. One folder holds a private Python
and ArgyllCMS for each platform, the app, and one shared jobs folder, so a
job started on Windows can be finished on Linux and the other way round.

    python scripts/build_portable.py                    # both platforms -> dist/ICC_Profiler_Portable_<ver>.zip
    python scripts/build_portable.py --only windows     # one platform
    python scripts/build_portable.py --no-zip           # folder only

Copy dist/ICC_Profiler_Portable/ (or the unzipped zip) to the USB drive.

The bundle can be built on Linux/WSL or Windows: the dependency wheels are
fetched for the target platform. On Linux the build also copies the X11
libraries ArgyllCMS links against, used only on PCs that lack them.

Downloads are cached in build/cache and checked against pinned SHA256 sums.
To upgrade Python or ArgyllCMS, change the constants below (python.org and
python-build-standalone publish their hashes; for ArgyllCMS, download the
archive and pin its sha256sum).
"""
from __future__ import annotations

import argparse
import hashlib
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

PYTHON_VERSION = "3.14.10"
ARGYLL_VERSION = "3.5.0"

DOWNLOADS = {
    "windows": {
        "python": (
            f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip",
            "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159",
        ),
        "argyll": (
            f"https://www.argyllcms.com/Argyll_V{ARGYLL_VERSION}_win64_exe.zip",
            "c80f78ba30e715079a00a14c2e7c9f533c58e6c8fdbc27f2d32b3d7813f9d132",
        ),
    },
    "linux": {
        # Relocatable CPython build from astral-sh/python-build-standalone.
        "python": (
            "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/"
            f"cpython-{PYTHON_VERSION}%2B20261110-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz",
            "c2ff2e97422f5d3896a16e0317fd212ba977bbf2688d93a6f7a03d817506bd30",
        ),
        "argyll": (
            f"https://www.argyllcms.com/Argyll_V{ARGYLL_VERSION}_linux_x86_64_bin.tgz",
            "0d1da052de22396f4141c5ca3ee0c17a434bc142048a327dd86565a9f20d0c13",
        ),
    },
}

# pip --platform tags for the Linux wheels (glibc 2.28+, i.e. any distro since 2018).
LINUX_WHEEL_PLATFORMS = ["manylinux_2_28_x86_64", "manylinux_2_17_x86_64", "manylinux2014_x86_64"]

# Libraries that are part of glibc itself: always present, never bundled.
GLIBC_LIBS = {"libc", "libm", "libdl", "librt", "libpthread", "ld-linux-x86-64", "linux-vdso"}

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "build" / "cache"
DIST = ROOT / "dist"
BUNDLE_NAME = "ICC_Profiler_Portable"
PY_MM = ".".join(PYTHON_VERSION.split(".")[:2])  # "3.14"


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
    target = CACHE / url.rsplit("/", 1)[1].replace("%2B", "+")
    if not target.exists() or sha256(target) != expected_sha256:
        print(f"  downloading {url}")
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
            shutil.copyfileobj(resp, out)
        tmp.replace(target)
    actual = sha256(target)
    if actual != expected_sha256:
        target.unlink()
        sys.exit(f"error: SHA256 mismatch for {url}\n  expected {expected_sha256}\n  got      {actual}")
    return target


def _checked_dest(dest: Path, parts: tuple[str, ...], archive: Path, name: str) -> Path:
    out = (dest / Path(*parts)).resolve()
    if out != dest and dest not in out.parents:
        sys.exit(f"error: unsafe path in {archive.name}: {name}")
    return out


def extract_zip(archive: Path, dest: Path, strip_top: bool = False) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            parts = Path(info.filename).parts[1 if strip_top else 0:]
            if not parts:
                continue
            out = _checked_dest(dest, parts, archive, info.filename)
            if info.is_dir():
                out.mkdir(parents=True, exist_ok=True)
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, out.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def extract_tar(archive: Path, dest: Path, strip_top: bool = False, skip: tuple[str, ...] = ()) -> None:
    """Extract a tar archive; members whose path starts with one of `skip` are left out."""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    seen: dict[str, str] = {}
    with tarfile.open(archive) as tf:
        members = []
        for m in tf.getmembers():
            parts = Path(m.name).parts[1 if strip_top else 0:]
            if not parts or "/".join(parts).startswith(skip):
                continue
            # Windows and FAT/exFAT drives ignore case: two names differing
            # only in case would overwrite each other.
            other = seen.setdefault("/".join(parts).lower(), m.name)
            if other != m.name:
                sys.exit(f"error: {archive.name} has names differing only in case: {other} / {m.name}")
            _checked_dest(dest, parts, archive, m.name)
            m.name = str(Path(*parts))
            members.append(m)
        # The "data" filter also rejects absolute paths, devices and links
        # that point outside dest.
        tf.extractall(dest, members=members, filter="data")


def replace_symlinks(tree: Path) -> None:
    """USB sticks are usually FAT/exFAT, which cannot store symlinks: copy the targets instead."""
    for link in sorted((p for p in tree.rglob("*") if p.is_symlink()), key=lambda p: len(p.parts), reverse=True):
        target = link.resolve()
        link.unlink()
        if target.is_dir():
            shutil.copytree(target, link)
        elif target.exists():
            shutil.copy2(target, link)


def pip_install(target: Path, platforms: list[str]) -> None:
    cmd = [
        sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "--upgrade",
        "--target", str(target), "--python-version", PY_MM.replace(".", ""),
        "--implementation", "cp", "--only-binary=:all:", "-r", str(ROOT / "requirements.txt"),
    ]
    for plat in platforms:
        cmd += ["--platform", plat]
    subprocess.run(cmd, check=True)
    # Console-script launchers point at the build machine's Python: useless here.
    shutil.rmtree(target / "bin", ignore_errors=True)


def build_windows(rt: Path) -> None:
    urls = DOWNLOADS["windows"]
    pydir = rt / "python"
    extract_zip(fetch(*urls["python"]), pydir)
    # The ._pth file fixes sys.path for the embeddable build (PYTHONPATH is
    # ignored): add site-packages and the shared app folder, enable site.
    (pth,) = pydir.glob("python*._pth")
    lines = [ln for ln in pth.read_text().splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    lines += ["Lib\\site-packages", "..\\..\\..\\app", "import site"]
    pth.write_text("\r\n".join(lines) + "\r\n")
    pip_install(pydir / "Lib" / "site-packages", ["win_amd64"])

    extract_zip(fetch(*urls["argyll"]), rt / "argyll", strip_top=True)
    if not (rt / "argyll" / "bin" / "targen.exe").is_file():
        sys.exit("error: ArgyllCMS zip layout changed: bin/targen.exe not found")


def bundle_argyll_libs(argyll: Path) -> None:
    """Copy the non-glibc shared libraries Argyll links against (X11 & co)."""
    if platform.system() != "Linux" or not shutil.which("ldd"):
        print("  note: not building on Linux, so no fallback X11 libraries are bundled")
        return
    out = subprocess.run(["ldd", str(argyll / "bin" / "targen")], capture_output=True, text=True).stdout
    libdir = argyll / "lib"
    libdir.mkdir(exist_ok=True)
    for line in out.splitlines():
        m = re.match(r"\s*(\S+)\s+=>\s+(\S+)", line)
        if not m or m.group(2) == "not":
            continue
        soname, path = m.groups()
        if soname.split(".so")[0] in GLIBC_LIBS:
            continue
        shutil.copy2(Path(path).resolve(), libdir / soname)


# Parts of python-build-standalone the app never uses (GUI toolkit, IDE,
# pip, headers, extra names of the interpreter). The python3 binary links
# libpython statically, so the shared libpython is only for embedding.
LINUX_PYTHON_PRUNE = [
    "bin/python", f"bin/python{PY_MM}", "bin/idle3", f"bin/idle{PY_MM}", "bin/pip", "bin/pip3",
    f"bin/pip{PY_MM}", "bin/pydoc3", f"bin/pydoc{PY_MM}", "bin/python3-config", f"bin/python{PY_MM}-config",
    "include", "share", "lib/pkgconfig", f"lib/libpython{PY_MM}.so", f"lib/libpython{PY_MM}.so.1.0",
    "lib/libpython3.so", "lib/libtcl*", "lib/itcl*", "lib/tcl*", "lib/tk*", "lib/thread*",
    f"lib/python{PY_MM}/idlelib", f"lib/python{PY_MM}/tkinter", f"lib/python{PY_MM}/turtledemo",
    f"lib/python{PY_MM}/ensurepip", f"lib/python{PY_MM}/test", f"lib/python{PY_MM}/lib-dynload/_tkinter*",
    f"lib/python{PY_MM}/site-packages/pip", f"lib/python{PY_MM}/site-packages/pip-*",
]


def prune_linux_python(pydir: Path) -> None:
    for pattern in LINUX_PYTHON_PRUNE:
        for path in pydir.glob(pattern):
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    if not (pydir / "bin" / "python3").exists():
        sys.exit("error: python-build-standalone layout changed: bin/python3 not found")


def build_linux(rt: Path) -> None:
    urls = DOWNLOADS["linux"]
    # The archive's top folder is python/. terminfo (only used by curses) has
    # entries that differ only in case, which a USB drive cannot store.
    extract_tar(fetch(*urls["python"]), rt, skip=("python/share/terminfo",))
    pip_install(rt / "python" / "lib" / f"python{PY_MM}" / "site-packages", LINUX_WHEEL_PLATFORMS)

    extract_tar(fetch(*urls["argyll"]), rt / "argyll", strip_top=True)
    if not (rt / "argyll" / "bin" / "targen").is_file():
        sys.exit("error: ArgyllCMS archive layout changed: bin/targen not found")
    shutil.rmtree(rt / "argyll" / "usb", ignore_errors=True)  # udev rules for instruments: not used
    bundle_argyll_libs(rt / "argyll")
    replace_symlinks(rt)
    prune_linux_python(rt / "python")
    (rt / "BUILD_ID").write_text(f"{project_version()}-{PYTHON_VERSION}-{ARGYLL_VERSION}-{int(time.time())}\n")


# --------------------------------------------------------------------------- launchers

WIN_ENV = r"""set "ICC_HOME=%~dp0"
set "PATH=%ICC_HOME%runtime\windows\argyll\bin;%PATH%"
set "ICC_PROFILER_JOBS_ROOT=%ICC_HOME%jobs"
set "ICC_PY=%ICC_HOME%runtime\windows\python\python.exe"
"""

START_WINDOWS = r"""@echo off
rem ICC Profiler (portable). Double-click to start; the browser opens on
rem http://localhost:8000. Close this window to stop.
rem Extra arguments are passed to uvicorn, e.g. Start_Windows.bat --port 8080
setlocal
cd /d "%~dp0"
""" + WIN_ENV + r"""
if "%~1"=="" start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://localhost:8000"
echo Starting ICC Profiler on http://localhost:8000 - close this window to stop.
"%ICC_PY%" -m uvicorn icc_profiler.webapp.app:app %*
if errorlevel 1 pause
endlocal
"""

CLI_WINDOWS = r"""@echo off
rem Command line of the portable ICC Profiler:  icc_profiler.bat doctor
setlocal
""" + WIN_ENV + r"""
"%ICC_PY%" -m icc_profiler %*
endlocal
"""

LINUX_ENV = r"""# Sourced by Start_Linux.sh and icc_profiler.sh: sets up the bundled
# Python and ArgyllCMS. Needs bash.
ICC_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ICC_RT="$ICC_HOME/runtime/linux"

_icc_works() {
    # Exit code 126 = not allowed to execute (noexec mount or missing x bit).
    "$1/python/bin/python3" -c "" >/dev/null 2>&1 || return 1
    local rc=0
    "$1/argyll/bin/targen" >/dev/null 2>&1 || rc=$?
    [ "$rc" -ne 126 ] && [ "$rc" -ne 127 ]
}

# Use the bundled X11 libraries only when this PC lacks them.
if ldd "$ICC_RT/argyll/bin/targen" 2>/dev/null | grep -q "not found"; then
    export LD_LIBRARY_PATH="$ICC_RT/argyll/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

if ! _icc_works "$ICC_RT"; then
    # USB drives formatted FAT32/exFAT are often mounted without permission
    # to run programs. Run Python and ArgyllCMS from a copy on this PC
    # instead; the jobs stay on the drive.
    _icc_cache="${XDG_CACHE_HOME:-$HOME/.cache}/icc_profiler_portable/$(cat "$ICC_RT/BUILD_ID")"
    if ! _icc_works "$_icc_cache"; then
        echo "Copying Python and ArgyllCMS to $_icc_cache (first run on this PC only)..."
        rm -rf "$_icc_cache" "$_icc_cache.tmp"
        mkdir -p "$_icc_cache.tmp"
        cp -r "$ICC_RT/python" "$ICC_RT/argyll" "$_icc_cache.tmp/"
        chmod -R u+x "$_icc_cache.tmp/python/bin" "$_icc_cache.tmp/argyll/bin"
        mv "$_icc_cache.tmp" "$_icc_cache"
    fi
    ICC_RT="$_icc_cache"
    if [ -n "${LD_LIBRARY_PATH:-}" ]; then
        export LD_LIBRARY_PATH="$ICC_RT/argyll/lib:$LD_LIBRARY_PATH"
    fi
    if ! _icc_works "$ICC_RT"; then
        echo "error: cannot run the bundled Python/ArgyllCMS on this PC (x86_64 Linux, glibc 2.28+ needed)." >&2
        exit 1
    fi
fi

ICC_DATA="$ICC_HOME"
if ! ( touch "$ICC_HOME/jobs/.write_test" && rm -f "$ICC_HOME/jobs/.write_test" ) 2>/dev/null; then
    # e.g. an Ubuntu live session mounts its own USB stick read-only.
    ICC_DATA="$HOME/ICC_Profiler"
    mkdir -p "$ICC_DATA/jobs" "$ICC_DATA/shared/it8_references"
    cp -rn "$ICC_HOME/jobs/." "$ICC_DATA/jobs/" 2>/dev/null || true
    cp -rn "$ICC_HOME/shared/." "$ICC_DATA/shared/" 2>/dev/null || true
    echo "note: $ICC_HOME is read-only; jobs are saved in $ICC_DATA/jobs" >&2
fi

export PATH="$ICC_RT/argyll/bin:$PATH"
export PYTHONPATH="$ICC_HOME/app"
export ICC_PROFILER_JOBS_ROOT="$ICC_DATA/jobs"
ICC_PY="$ICC_RT/python/bin/python3"
"""

START_LINUX = r"""#!/usr/bin/env bash
# ICC Profiler (portable). Start with:  bash Start_Linux.sh
# The browser opens on http://localhost:8000; press Ctrl+C to stop.
# Extra arguments are passed to uvicorn, e.g. bash Start_Linux.sh --port 8080
set -eu
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runtime/linux/env.sh"
cd "$ICC_HOME"
if [ $# -eq 0 ] && command -v xdg-open >/dev/null 2>&1; then
    (sleep 3; xdg-open http://localhost:8000 >/dev/null 2>&1 || true) &
fi
echo "Starting ICC Profiler on http://localhost:8000 (Ctrl+C to stop)"
exec "$ICC_PY" -m uvicorn icc_profiler.webapp.app:app "$@"
"""

CLI_LINUX = r"""#!/usr/bin/env bash
# Command line of the portable ICC Profiler:  bash icc_profiler.sh doctor
set -eu
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/runtime/linux/env.sh"
exec "$ICC_PY" -m icc_profiler "$@"
"""

README_TXT = """ICC Profiler - portable version (Windows and Linux)
===================================================

Nothing needs to be installed. Copy this folder to a USB drive (or
anywhere else) and start it:

  Windows:  double-click Start_Windows.bat
  Linux:    open a terminal in this folder and run:  bash Start_Linux.sh

Your browser opens on http://localhost:8000. To stop, close the black
window (Windows) or press Ctrl+C in the terminal (Linux).

Both systems use the same jobs\\ folder, so you can start a job on one PC
and continue on another.

Command line:  icc_profiler.bat doctor   (Windows)
               bash icc_profiler.sh doctor   (Linux)
"doctor" checks that everything works.

Folders:
  jobs\\       your profiling jobs (charts, measurements, profiles)
  shared\\     shared IT8 reference files
  configs\\    example job configs for the command line
  app\\        the ICC Profiler program
  runtime\\    private copies of Python {python} and ArgyllCMS {argyll}

Linux notes: needs a 64-bit (x86_64) PC with a distribution from 2018 or
later. If the USB drive does not allow running programs (common with
FAT32/exFAT), the first start copies Python and ArgyllCMS to
~/.cache/icc_profiler_portable on that PC; your jobs stay on the drive.
If the drive is read-only (an Ubuntu live session mounts its own boot
stick that way), jobs are saved in ~/ICC_Profiler/jobs instead and the
terminal says so. In a live session without persistence that folder is
gone after a reboot: copy your profiles somewhere safe first.

To update, copy your jobs\\ and shared\\ folders into the new version.

Don't pull the USB drive out while the profiler is running: wait until
you have stopped it.

ArgyllCMS is (c) Graeme W. Gill and distributed under the GNU AGPL v3;
see runtime\\<system>\\argyll\\License.txt. Its source code is available at
https://www.argyllcms.com/. Python is distributed under the PSF License;
see the LICENSE file in runtime\\<system>\\python.
"""


def write_text(path: Path, text: str, crlf: bool, executable: bool = False) -> None:
    text = text.replace("\r\n", "\n")
    path.write_bytes((text.replace("\n", "\r\n") if crlf else text).encode("ascii"))
    if executable:
        path.chmod(0o755)


def make_zip(bundle: Path, zip_path: Path) -> None:
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(bundle.rglob("*")):
            arc = (Path(bundle.name) / path.relative_to(bundle)).as_posix()
            # zipfile keeps the Unix mode, so Linux unzip restores the x bits.
            zf.write(path, arc + "/" if path.is_dir() else arc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", choices=["windows", "linux"], help="Build for one platform only")
    parser.add_argument("--no-zip", action="store_true", help="Leave the folder, skip making the zip")
    args = parser.parse_args()
    platforms = [args.only] if args.only else ["windows", "linux"]

    bundle = DIST / BUNDLE_NAME
    if bundle.exists():
        shutil.rmtree(bundle)
    bundle.mkdir(parents=True)

    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    shutil.copytree(ROOT / "icc_profiler", bundle / "app" / "icc_profiler", ignore=ignore)
    shutil.copytree(ROOT / "configs", bundle / "configs", ignore=ignore)
    shutil.copy2(ROOT / "README.md", bundle / "README.md")
    (bundle / "jobs").mkdir()
    (bundle / "shared" / "it8_references").mkdir(parents=True)
    write_text(bundle / "README.txt", README_TXT.format(python=PYTHON_VERSION, argyll=ARGYLL_VERSION), crlf=True)

    if "windows" in platforms:
        print("Windows runtime")
        build_windows(bundle / "runtime" / "windows")
        write_text(bundle / "Start_Windows.bat", START_WINDOWS, crlf=True)
        write_text(bundle / "icc_profiler.bat", CLI_WINDOWS, crlf=True)
    if "linux" in platforms:
        print("Linux runtime")
        build_linux(bundle / "runtime" / "linux")
        write_text(bundle / "runtime" / "linux" / "env.sh", LINUX_ENV, crlf=False)
        write_text(bundle / "Start_Linux.sh", START_LINUX, crlf=False, executable=True)
        write_text(bundle / "icc_profiler.sh", CLI_LINUX, crlf=False, executable=True)
    print(f"Folder: {bundle}")

    if not args.no_zip:
        suffix = f"_{args.only}" if args.only else ""
        zip_path = DIST / f"{BUNDLE_NAME}_{project_version()}{suffix}.zip"
        make_zip(bundle, zip_path)
        print(f"Zip:    {zip_path} ({zip_path.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
