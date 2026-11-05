"""
Tests for the file helpers (src/FileUtils.py):
- makeParentFolder() creates the folder of a file.
- writeAtomically() and writeJSON() replace the target in one operation and
  leave the previous file and no temporary file behind if writing fails.
- lockedFile() makes processes take turns, and runs the block without a lock
  (with a warning) where file locks are not supported.
- All of them accept os.PathLike paths.

Run from the repository root with:
    pytest -v
"""

from __future__ import annotations

import json
import multiprocessing
import os
import pathlib
import threading

import pytest

import logger.FileUtils as file_utils
from logger import Logger, lockedFile, makeParentFolder, writeAtomically, writeJSON


def _temporary_files(folder) -> list[str]:
    """The temporary files writeAtomically() left in `folder`."""
    return [name for name in os.listdir(folder) if name.endswith(".tmp")]


class TestMakeParentFolder:

    def test_creates_missing_folders(self, tmp_path):
        path = tmp_path / "a" / "b" / "file.txt"

        makeParentFolder(str(path))

        assert path.parent.is_dir()

    def test_file_name_without_folder_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        makeParentFolder("file.txt")

        assert os.listdir(tmp_path) == []


class TestWriteAtomically:

    def test_writes_the_file_and_its_folder(self, tmp_path):
        path = str(tmp_path / "out" / "file.txt")

        writeAtomically(path, lambda tmp: open(tmp, "w").write("content"))

        assert open(path).read() == "content"
        assert _temporary_files(os.path.dirname(path)) == []

    def test_failed_write_keeps_the_old_file(self, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("old")

        def write(tmp):
            open(tmp, "w").write("half")
            raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            writeAtomically(str(path), write)

        assert path.read_text() == "old"
        assert _temporary_files(tmp_path) == []


    def test_concurrent_writers_do_not_share_the_temporary_file(self, tmp_path):
        """
        Two threads writing the same file at once each replace it with their
        complete content; with one shared temporary file, one would move the
        other's half-written file into place, or fail to find its own.
        """
        path = str(tmp_path / "file.txt")
        both_written = threading.Barrier(2)
        errors = []

        def writer(text):
            def write(tmp):
                with open(tmp, "w") as f:
                    f.write(text)
                both_written.wait(timeout=10)
            try:
                writeAtomically(path, write)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t * 1000,)) for t in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert open(path).read() in ("a" * 1000, "b" * 1000)
        assert _temporary_files(tmp_path) == []


class TestPathLike:

    def test_helpers_accept_path_objects(self, tmp_path):
        path = pathlib.Path(tmp_path) / "out" / "data.json"

        with lockedFile(path):
            writeJSON({"a": 1}, path)
        writeAtomically(path.with_suffix(".txt"), lambda tmp: open(tmp, "w").write("x"))

        assert json.loads(path.read_text()) == {"a": 1}
        assert path.with_suffix(".txt").read_text() == "x"
        assert (path.parent / "data.json.lock").is_file()


class TestWriteJSON:

    def test_writes_indented_json(self, tmp_path):
        path = str(tmp_path / "data.json")

        writeJSON({"name": "Größe", "values": [1, 2]}, path)

        text = open(path, encoding="utf-8").read()
        assert json.loads(text) == {"name": "Größe", "values": [1, 2]}
        assert "Größe" in text and text.endswith("\n") and "\n  " in text

    def test_unserializable_data_keeps_the_old_file(self, tmp_path):
        path = str(tmp_path / "data.json")
        writeJSON({"version": 1}, path)

        with pytest.raises(TypeError):
            writeJSON({"version": 2, "broken": object()}, path)

        assert json.loads(open(path, encoding="utf-8").read()) == {"version": 1}
        assert _temporary_files(tmp_path) == []


def _count_up(path: str, times: int) -> None:
    """Increments the number in `path` `times` times, each under the lock."""
    for _ in range(times):
        with lockedFile(path):
            value = int(open(path).read()) if os.path.exists(path) else 0
            writeAtomically(path, lambda tmp: open(tmp, "w").write(str(value + 1)))


class TestLockedFile:

    def test_creates_the_lock_file_and_its_folder(self, tmp_path):
        path = str(tmp_path / "out" / "data.json")

        with lockedFile(path):
            assert os.path.isfile(path + ".lock")

    @pytest.mark.skipif(
        file_utils.fcntl is None or "fork" not in multiprocessing.get_all_start_methods(),
        reason="needs file locks and fork",
    )
    def test_processes_take_turns(self, tmp_path):
        path = str(tmp_path / "counter.txt")
        context = multiprocessing.get_context("fork")
        workers = [context.Process(target=_count_up, args=(path, 25)) for _ in range(4)]
        for process in workers:
            process.start()
        for process in workers:
            process.join()

        assert all(process.exitcode == 0 for process in workers)
        assert open(path).read() == "100"

    def test_runs_without_a_lock_where_locks_are_not_supported(self, tmp_path, monkeypatch):
        messages = []
        monkeypatch.setattr(file_utils, "fcntl", None)
        monkeypatch.setattr(
            Logger, "log",
            lambda self, message, cmdline=True, level="info": messages.append((level, message)),
        )
        ran = False

        with lockedFile(str(tmp_path / "data.json")):
            ran = True

        assert ran
        assert any(level == "warning" and "without a lock" in message for level, message in messages)

    @pytest.mark.skipif(file_utils.fcntl is None, reason="needs file locks")
    def test_runs_without_a_lock_where_locking_fails(self, tmp_path, monkeypatch):
        """E.g. a cluster file system mounted without lock support (ENOLCK)."""
        messages = []

        def fail(handle, operation):
            raise OSError(37, "No locks available")

        monkeypatch.setattr(file_utils.fcntl, "flock", fail)
        monkeypatch.setattr(
            Logger, "log",
            lambda self, message, cmdline=True, level="info": messages.append((level, message)),
        )
        ran = False

        with lockedFile(str(tmp_path / "data.json")):
            ran = True

        assert ran
        assert any(level == "warning" and "Could not lock" in message for level, message in messages)
