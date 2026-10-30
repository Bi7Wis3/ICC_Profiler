"""One-at-a-time lock per job.

Two stage runs on the same job (a double-click in the web UI, or the CLI
and the web UI at once) would run two colprof/scanin processes over the
same files. job_lock() refuses the second run instead of waiting, both
within one process (a threading lock) and across processes (an OS file
lock on jobs/<job>/.lock, released automatically if the process dies).
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

from icc_profiler.errors import PipelineError

LOCK_FILENAME = ".lock"

_registry_guard = threading.Lock()
_thread_locks: dict[str, threading.Lock] = {}


def _thread_lock(job_dir: Path) -> threading.Lock:
    key = str(job_dir.resolve())
    with _registry_guard:
        return _thread_locks.setdefault(key, threading.Lock())


def _os_lock(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _os_unlock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def is_locked(job_dir: Path) -> bool:
    return _thread_lock(job_dir).locked()


@contextmanager
def job_lock(job_dir: Path):
    tlock = _thread_lock(job_dir)
    if not tlock.acquire(blocking=False):
        raise PipelineError("Another stage is already running for this job. Wait for it to finish.")
    fd = None
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(job_dir / LOCK_FILENAME, os.O_RDWR | os.O_CREAT, 0o644)
        if not _os_lock(fd):
            raise PipelineError(
                "Another process (the CLI or a second web server) is already running a stage "
                "for this job. Wait for it to finish."
            )
        try:
            yield
        finally:
            _os_unlock(fd)
    finally:
        if fd is not None:
            os.close(fd)
        tlock.release()
