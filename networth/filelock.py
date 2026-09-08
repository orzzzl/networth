"""Small ``flock`` wrapper shared by token writes and backup capture.

The lock is a filesystem boundary, not a process-local mutex: Link workers and
the backup builder run in different processes on the sync host.  Keeping the
primitive here gives both sides the same path, mode, and non-blocking semantics.
"""

from __future__ import annotations

import fcntl
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_MODE = 0o600
_THREAD_LOCKS = threading.local()


class LockUnavailable(RuntimeError):
    """A non-blocking lock request lost to work already in flight."""


@contextmanager
def exclusive_file_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """Hold an exclusive advisory lock on ``path`` for the context lifetime."""

    key = os.path.abspath(path)
    held = getattr(_THREAD_LOCKS, "held", None)
    if held is None:
        held = {}
        _THREAD_LOCKS.held = held
    count = held.get(key, 0)
    if count:
        # ``flock`` on a separately opened fd can block against the same
        # process on macOS.  Nested operations in one thread already execute
        # inside the boundary; make that ownership explicit while keeping
        # other threads and processes kernel-serialized.
        held[key] = count + 1
        try:
            yield
        finally:
            held[key] -= 1
        return

    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
    fd = os.open(path, flags, LOCK_MODE)
    try:
        os.fchmod(fd, LOCK_MODE)
        operation = fcntl.LOCK_EX
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, operation)
        except BlockingIOError:
            raise LockUnavailable(f"another operation holds {path.name!r}") from None
        try:
            held[key] = 1
            yield
        finally:
            del held[key]
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
