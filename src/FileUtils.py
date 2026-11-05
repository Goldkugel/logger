"""
File helpers for runs that share files, e.g. the jobs of a Slurm job array:
an exclusive lock on a file (lockedFile()), and writes that replace a file
in one operation (writeAtomically(), writeJSON()), so an interrupted write
never leaves a partially written file.

They depend on nothing but the standard library and the Logger, so every
package using the logger can use them without further dependencies.
"""

from __future__ import annotations

import contextlib
import json
import os

from typing  import Callable, Iterator

from .Logger import Logger

# File locks (see lockedFile()) are only available on POSIX systems
try:
    import fcntl
except ImportError:
    fcntl = None


def makeParentFolder(path: str) -> None:
    """Creates the folder of the file at `path` if it does not exist yet."""
    parent_dir = os.path.dirname(path)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)


def writeAtomically(path: str, write: Callable[[str], None]) -> None:
    """
    Calls write(tmp_path) to write the file to "<path>.tmp", which then
    replaces `path` in a single operation, so a killed job never leaves a
    partially written file. The folder of `path` is created if needed.

    :param path: Destination file path.
    :param write: Function writing the complete file to the path it is given.
    """
    makeParentFolder(path)

    # Next to the target, so os.replace() stays within one file system
    tmp_path = path + ".tmp"
    try:
        write(tmp_path)
        # os.replace is atomic on both POSIX and Windows
        os.replace(tmp_path, path)
    finally:
        # Only left behind if writing or replacing failed
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def writeJSON(data, path: str) -> None:
    """
    Writes a JSON-serializable object atomically (see writeAtomically()),
    indented for reading.

    :param data: Object to write.
    :param path: Destination file path.
    """
    def write(tmp_path: str) -> None:
        with open(tmp_path, mode="w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    writeAtomically(path, write)


@contextlib.contextmanager
def lockedFile(path: str) -> Iterator[None]:
    """
    Holds an exclusive lock on "<path>.lock" while the block runs, so that
    processes updating the same file (e.g. the jobs of a Slurm job array)
    take turns. Where file locks are not supported (Windows, or a cluster
    file system mounted without lock support), this is logged and the block
    runs without a lock.

    Usage:
        with lockedFile("./data/output/result.csv"):
            ...

    :param path: Path of the file the lock protects.
    """
    makeParentFolder(path)

    # The lock file is never deleted: a process still waiting on it would
    # then hold a lock on a different file than the next process.
    lock_path = path + ".lock"
    with open(lock_path, mode="a", encoding="utf-8") as handle:
        locked = False
        if fcntl is None:
            Logger().log(f"File locks are not supported here; updating '{path}' without a lock.")
        else:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX)
                locked = True
            except OSError as e:
                Logger().log(f"Could not lock '{lock_path}' ({e!r}); updating '{path}' without a lock.")
        try:
            yield
        finally:
            if locked:
                fcntl.flock(handle, fcntl.LOCK_UN)
