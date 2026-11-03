"""
Tests for the file helpers (src/FileUtils.py):
- makeParentFolder() creates the folder of a file.
- writeAtomically() and writeJSON() replace the target in one operation and
  leave the previous file and no temporary file behind if writing fails.
- lockedFile() makes processes take turns, and runs the block without a lock
  (logging why) where file locks are not supported.

Run from the repository root with:
    pytest -v
"""

from __future__ import annotations

import json
import multiprocessing
import os

import pytest

import logger.FileUtils as file_utils
from logger import Logger, lockedFile, makeParentFolder, writeAtomically, writeJSON


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
        assert not os.path.exists(path + ".tmp")

    def test_failed_write_keeps_the_old_file(self, tmp_path):
        path = tmp_path / "file.txt"
        path.write_text("old")

        def write(tmp):
            open(tmp, "w").write("half")
            raise OSError("disk full")

        with pytest.raises(OSError, match="disk full"):
            writeAtomically(str(path), write)

        assert path.read_text() == "old"
        assert not os.path.exists(str(path) + ".tmp")


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
        assert not os.path.exists(path + ".tmp")


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
        monkeypatch.setattr(Logger, "log", lambda self, message, *args, **kwargs: messages.append(message))
        ran = False

        with lockedFile(str(tmp_path / "data.json")):
            ran = True

        assert ran
        assert any("without a lock" in message for message in messages)
