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
import threading

from typing  import Any, Callable, Iterator, Union

from .Logger import Logger

# File locks (see lockedFile()) are only available on POSIX systems.
try:
    import fcntl
except ImportError:
    fcntl = None

# A file path as the helpers accept it.
PathType = Union[str, "os.PathLike[str]"]


def makeParentFolder(path: PathType) -> None:
    """Create the folder of the file at `path` if it does not exist yet."""
    parent_dir = os.path.dirname(os.fspath(path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)


def writeAtomically(path: PathType, write: Callable[[str], None]) -> None:
    """
    Call write(tmp_path) to write the complete file to a temporary file
    next to `path`, which then replaces `path` in one operation, so a killed
    job never leaves a partially written file. The folder of `path` is
    created if needed, and the temporary file is removed if writing fails.

    Parameters
    ----------
    path : str or os.PathLike
        Destination file.
    write : callable
        Function writing the complete file to the path (a str) it is given.
    """
    path = os.fspath(path)
    makeParentFolder(path)

    # Next to the target, so os.replace() stays within one file system. The
    # process and thread in the name keep two writers of the same file
    # from writing to (and moving) each other's temporary file.
    tmp_path = f"{path}.{os.getpid()}-{threading.get_ident()}.tmp"
    try:
        write(tmp_path)
        # Atomic on both POSIX and Windows.
        os.replace(tmp_path, path)
    finally:
        # Only left behind if writing or replacing failed.
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def writeJSON(data: Any, path: PathType) -> None:
    """
    Write a JSON-serializable object as indented UTF-8 JSON with
    writeAtomically().

    Parameters
    ----------
    data : Any
        Object to write.
    path : str or os.PathLike
        Destination file.
    """
    def write(tmp_path: str) -> None:
        with open(tmp_path, mode="w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    writeAtomically(path, write)


@contextlib.contextmanager
def lockedFile(path: PathType) -> Iterator[None]:
    """
    Hold an exclusive lock on "<path>.lock" while the block runs, so that
    processes updating the same file (e.g. the jobs of a Slurm job array)
    take turns. Where file locks are not supported (Windows, or a cluster
    file system mounted without lock support), a warning is logged and the
    block runs without a lock.

    Usage:
        with lockedFile("./data/output/result.csv"):
            ...

    Parameters
    ----------
    path : str or os.PathLike
        Path of the file the lock protects.
    """
    path = os.fspath(path)
    makeParentFolder(path)

    # The lock file is never deleted: a process still waiting on it would
    # then hold a lock on a different file than the next process.
    lock_path = path + ".lock"
    with open(lock_path, mode="a", encoding="utf-8") as handle:
        locked = False
        if fcntl is None:
            Logger().warning(f"File locks are not supported here; updating '{path}' without a lock.")
        else:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX)
                locked = True
            except OSError as e:
                Logger().warning(f"Could not lock '{lock_path}' ({e!r}); updating '{path}' without a lock.")
        try:
            yield
        finally:
            if locked:
                fcntl.flock(handle, fcntl.LOCK_UN)
