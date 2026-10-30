"""
Test suite for the Logger class and LoggerConfig model.

Tests cover:
- Singleton behaviour (__new__, start_time, shared config, bare Logger() calls)
- Configuration loading (valid YAML, missing file, missing or empty section,
  empty file, invalid values)
- log() — file writing, timestamps, runtime, stdout, None handling, encoding,
  folder creation
- printHeader() — line count, border characters, return value, truncation,
  cmdline
- File-processing helper methods (printFileProcessingStart/End, etc.)
- One common structure for all messages the logger writes itself
- printDataFrameRowCount() — row count logging, None handling, cmdline
- Logger.reset() and passing the config as a dict (section or whole
  configuration), path object or LoggerConfig
- Log levels, minimum level, level helpers and console colors
- Runtime display formats ("minutes", "hms")
- exception() and step()
- Thread safety
- Multiprocessing (fork and spawn workers, fork while another thread logs)
- LoggerConfig defaults, validated assignments and package exports
- Console problems (unencodable characters, broken pipe)
- console setting and the {start} placeholder in file_name (one file per run)
- Reconfiguration: replace=False and the logged change notice
- Capturing Python's standard logging module

Run from the repository root with:
    pytest -v
(pyproject.toml points pytest at tests/*Test.py.)

The real ./config/config.yaml is used as the default configuration for most
tests. Run pytest from the repository root so that relative paths resolve
correctly.
"""

from __future__ import annotations

import io
import logging
import multiprocessing
import os
import re
import sys
import threading
import time

from datetime import datetime
from types import MappingProxyType, SimpleNamespace

import pandas   as pd
import pytest
import yaml

from pydantic import ValidationError

import logger as logger_package
from logger import LevelColors, Logger, LoggerConfig


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """
    Reset the Logger singleton before and after every test.

    Logger stores its instance, config, and start_time as class-level
    attributes, so without this reset, state leaks between tests and the
    order in which tests run would affect their outcome.
    """
    Logger.reset()
    yield
    Logger.reset()


@pytest.fixture
def config_file():
    """
    Point tests at the project's real logger configuration file
    (./config/config.yaml).

    The log file named in that config is removed before and after each
    test so that tests asserting on exact line counts always start with
    a clean slate — the same guarantee a fresh tmp_path would give.

    Yields
    ------
    tuple[str, str]
        (config_path, log_folder) — the path to the YAML config file and
        the log output directory read from it.
    """
    config_path = "./config/config.yaml"
    with open(config_path, "r") as f:
        config_data = yaml.safe_load(f)

    logger_section = config_data["logger"]
    log_folder     = logger_section["folder"]
    file_name      = logger_section["file_name"]

    os.makedirs(log_folder, exist_ok=True)
    log_path = os.path.join(log_folder, file_name)

    # Remove any leftover log file from a previous run before the test starts.
    if os.path.exists(log_path):
        os.remove(log_path)

    yield config_path, log_folder

    # Clean up after the test as well so the log directory stays tidy.
    if os.path.exists(log_path):
        os.remove(log_path)


@pytest.fixture
def logger_instance(config_file):
    """
    Build a Logger using the real config file and return it together with
    the log folder path (needed by helper read_log_lines).

    Returns
    -------
    tuple[Logger, str]
        (logger, log_folder)
    """
    config_path, log_folder = config_file
    logger = Logger(config=config_path)
    return logger, log_folder


def read_log_lines(log_folder: str, file_name: str = "output.log") -> list[str]:
    """
    Read all lines from the log file and return them as a list.

    Returns an empty list if the file does not yet exist, which lets
    tests that assert on an empty log avoid a FileNotFoundError.
    """
    path = os.path.join(log_folder, file_name)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return f.readlines()


# --------------------------------------------------------------------------
# Singleton behaviour
# --------------------------------------------------------------------------

class TestSingleton:
    """
    Tests for the singleton pattern implemented in Logger.__new__.

    Verifies that only one instance is ever created, that start_time is
    recorded exactly once, and that config is shared (and overwritten)
    across all references to the singleton.
    """

    def test_returns_same_instance(self, config_file):
        config_path, _ = config_file
        logger1 = Logger(config=config_path)
        logger2 = Logger(config=config_path)
        assert logger1 is logger2

    def test_start_time_set_once(self, config_file):
        config_path, _ = config_file
        logger1 = Logger(config=config_path)
        first_start_time = Logger.start_time
        time.sleep(0.05)
        logger2 = Logger(config=config_path)
        # start_time must not be updated on the second call.
        assert Logger.start_time == first_start_time
        assert logger1 is logger2

    def test_config_shared_across_instances(self, config_file, tmp_path):
        """
        Because `config` is a class attribute, calling Logger() a second
        time with a different config path overwrites the shared config.
        This test documents that behaviour explicitly so it is not
        accidentally "fixed" in a way that breaks callers relying on it.
        """
        config_path, _ = config_file
        logger1 = Logger(config=config_path)

        # Build a second, deliberately different config in a temp directory
        # so this test does not depend on or modify the real config file.
        other_folder = tmp_path / "other_logs"
        other_folder.mkdir()
        other_config_path = tmp_path / "other_config.yaml"
        with open(other_config_path, "w") as f:
            yaml.safe_dump({
                "logger": {
                    "folder"       : str(other_folder) + os.sep,
                    "file_name"    : "other.log",
                    "header_char"  : "=",
                    "header_length": 10,
                    "log_runtime"  : False,
                    "format"       : "%H:%M:%S",
                }
            }, f)

        logger2 = Logger(config=str(other_config_path))

        # Still the same instance — but the shared config now reflects the
        # second call's settings.
        assert logger1 is logger2
        assert logger1.config.file_name == "other.log"

    def test_bare_call_keeps_loaded_config(self, config_file, tmp_path):
        """
        Logger() without a path after the first call must keep the
        configuration loaded earlier instead of re-reading the default
        ./config/config.yaml.
        """
        config_path, _ = config_file
        other_config_path = tmp_path / "other_config.yaml"
        with open(other_config_path, "w") as f:
            yaml.safe_dump({
                "logger": {
                    "folder"   : str(tmp_path) + os.sep,
                    "file_name": "other.log",
                }
            }, f)

        Logger(config=str(other_config_path))
        logger = Logger()
        assert logger.config.file_name == "other.log"

    def test_bare_first_call_loads_default_config(self, config_file):
        """The first Logger() call without a path loads ./config/config.yaml."""
        with open("./config/config.yaml", "r") as f:
            expected = yaml.safe_load(f)["logger"]
        logger = Logger()
        assert logger.config.file_name == expected["file_name"]


# --------------------------------------------------------------------------
# Configuration loading
# --------------------------------------------------------------------------

class TestConfigLoading:
    """
    Tests for Logger.__init__ and the YAML → LoggerConfig loading path.

    Verifies that valid config files are parsed correctly, that an empty
    "logger" section falls back to the defaults, and that invalid, empty
    or missing files raise the expected exceptions.
    """

    def test_loads_expected_fields(self, logger_instance):
        """
        Config fields loaded from YAML must match what the file actually
        contains — checked dynamically so this test stays correct even if
        the config file is updated.
        """
        logger, _ = logger_instance
        with open("./config/config.yaml", "r") as f:
            expected = yaml.safe_load(f)["logger"]
        assert logger.config.file_name     == expected["file_name"]
        assert logger.config.header_char   == expected["header_char"]
        assert logger.config.header_length == expected["header_length"]
        assert logger.config.log_runtime   == expected["log_runtime"]
        assert logger.config.format        == expected["format"]
        assert logger.config.encoding      == expected["encoding"]
        assert logger.config.level          == expected["level"]
        assert logger.config.runtime_format == expected["runtime_format"]
        assert logger.config.console        == expected["console"]
        assert logger.config.colors         == LevelColors(**expected["colors"])

    @pytest.mark.parametrize("override", [
        {"header_char"  : "=-"},
        {"header_char"  : ""},
        {"header_length": 1},
        {"encoding"     : "not-an-encoding"},
        {"level"        : "debug"},
        {"runtime_format": "seconds"},
        {"colors"       : {"info": "pink"}},
        {"colors"       : {"warn": "yellow"}},
        {"format"       : "%Y-%m-%d %"},
        {"format"       : "%H:%M %%%"},
        {"file_name"    : "run_{run}.log"},
        {"file_name"    : "run_{0}.log"},
        {"file_name"    : "run_{start.nope}.log"},
        {"file_name"    : "run_{start.log"},
        {"file_name"    : ""},
        {"console"      : "sometimes"},
    ])
    def test_invalid_values_raise(self, tmp_path, override):
        """Invalid values and unknown keys are rejected when the config is loaded."""
        bad_config_path = tmp_path / "bad_config.yaml"
        with open(bad_config_path, "w") as f:
            yaml.safe_dump({"logger": {"folder": str(tmp_path), **override}}, f)
        with pytest.raises(ValidationError):
            Logger(config=str(bad_config_path))

    def test_missing_config_file_raises(self, tmp_path):
        missing_path = str(tmp_path / "does_not_exist.yaml")
        with pytest.raises(FileNotFoundError):
            Logger(config=missing_path)

    def test_missing_logger_section_raises(self, tmp_path):
        """
        A YAML file that has no "logger" key should raise a KeyError naming
        the missing section.
        """
        bad_config_path = tmp_path / "bad_config.yaml"
        with open(bad_config_path, "w") as f:
            yaml.safe_dump({"not_logger": {}}, f)
        with pytest.raises(KeyError, match="no 'logger' section"):
            Logger(config=str(bad_config_path))

    def test_empty_config_file_raises(self, tmp_path):
        """An empty YAML file has no "logger" section either."""
        empty_path = tmp_path / "empty.yaml"
        empty_path.write_text("")
        with pytest.raises(KeyError, match="no 'logger' section"):
            Logger(config=str(empty_path))

    def test_empty_logger_section_uses_defaults(self, tmp_path):
        """
        "logger:" without keys falls back to the model defaults, as an empty
        section in a configuration dict does.
        """
        config_path = tmp_path / "empty_section.yaml"
        config_path.write_text("logger:\n")
        logger = Logger(config=str(config_path))
        assert logger.config == LoggerConfig()


    def test_non_mapping_yaml_raises(self, tmp_path):
        """A YAML file whose top level is not a mapping has no "logger" section."""
        config_path = tmp_path / "list.yaml"
        config_path.write_text("- logger\n- other\n")
        with pytest.raises(KeyError, match="no 'logger' section"):
            Logger(config=str(config_path))

    def test_full_configuration_file_ignores_other_sections(self, tmp_path):
        """Only the "logger" section is validated; other packages' sections are ignored."""
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump({
                "logger"  : {"folder": str(tmp_path), "level": "warning"},
                "embedder": {"model_id": ["model-a"], "unknown": True},
            }, f)
        logger = Logger(config=str(config_path))
        assert logger.config.level == "warning"

    def test_bare_first_call_without_default_file_raises(self, tmp_path, monkeypatch):
        """Without ./config/config.yaml in the working directory, Logger() fails."""
        monkeypatch.chdir(tmp_path)
        with pytest.raises(FileNotFoundError):
            Logger()
        # A failed load leaves no configuration behind.
        assert Logger.config is None


    def test_literal_percent_in_format_is_allowed(self, make_logger):
        logger, lines = make_logger(format="%H:%M %%")
        logger.log("x", cmdline=False)
        assert re.match(r"^\[\d{2}:\d{2} %\] ", lines()[0])


# --------------------------------------------------------------------------
# log()
# --------------------------------------------------------------------------

class TestLog:
    """
    Tests for Logger.log() — file writing, timestamp format, runtime
    prefix, stdout output, and edge cases (None, empty string).
    """

    def test_log_writes_to_file(self, logger_instance):
        logger, log_folder = logger_instance
        logger.log("Hello world", cmdline=False)
        lines = read_log_lines(log_folder)
        assert len(lines) == 1
        assert "Hello world" in lines[0]

    def test_log_prefixes_timestamp(self, logger_instance):
        logger, log_folder = logger_instance
        logger.log("Timestamped message", cmdline=False)
        lines = read_log_lines(log_folder)
        # Timestamps are wrapped in square brackets: "[YYYY-MM-DD HH:MM:SS]"
        assert lines[0].startswith("[")
        assert "]" in lines[0]

    def test_log_includes_runtime_when_enabled(self, logger_instance):
        logger, log_folder = logger_instance
        if not logger.config.log_runtime:
            pytest.skip("log_runtime is disabled in ./config/config.yaml")
        logger.log("Runtime check", cmdline=False)
        lines = read_log_lines(log_folder)
        assert "Minutes" in lines[0]

    def test_log_excludes_runtime_when_disabled(self, config_file, tmp_path):
        """
        Build a disposable config with log_runtime=False and verify that
        the "Minutes" token is absent from the log line.
        """
        _, log_folder = config_file
        disabled_config_path = tmp_path / "disabled_config.yaml"
        folder_value = log_folder if log_folder.endswith(os.sep) else log_folder + os.sep
        with open(disabled_config_path, "w") as f:
            yaml.safe_dump({
                "logger": {
                    "folder"       : folder_value,
                    "file_name"    : "output.log",
                    "header_char"  : "-",
                    "header_length": 20,
                    "log_runtime"  : False,
                    "format"       : "%Y-%m-%d %H:%M:%S",
                }
            }, f)
        logger = Logger(config=str(disabled_config_path))
        logger.log("No runtime", cmdline=False)
        lines = read_log_lines(log_folder)
        assert "Minutes" not in lines[0]
        assert "No runtime" in lines[0]

    def test_log_none_string_does_nothing(self, logger_instance):
        """None is silently ignored — no line should be written."""
        logger, log_folder = logger_instance
        logger.log(None, cmdline=False)
        lines = read_log_lines(log_folder)
        assert lines == []

    def test_log_empty_string_still_writes_timestamp(self, logger_instance):
        """
        An empty string produces a timestamp-only line, useful for adding
        blank separators to the log without a visible message body.
        """
        logger, log_folder = logger_instance
        logger.log("", cmdline=False)
        lines = read_log_lines(log_folder)
        assert len(lines) == 1
        assert lines[0].startswith("[")

    def test_log_prints_to_stdout_when_cmdline_true(self, logger_instance, capsys):
        logger, _ = logger_instance
        logger.log("Printed message", cmdline=True)
        captured = capsys.readouterr()
        assert "Printed message" in captured.out

    def test_log_does_not_print_when_cmdline_false(self, logger_instance, capsys):
        logger, _ = logger_instance
        logger.log("Silent message", cmdline=False)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_multiple_log_calls_append(self, logger_instance):
        """Each log() call appends a new line; previous lines are not overwritten."""
        logger, log_folder = logger_instance
        logger.log("First", cmdline=False)
        logger.log("Second", cmdline=False)
        lines = read_log_lines(log_folder)
        assert len(lines) == 2
        assert "First"  in lines[0]
        assert "Second" in lines[1]

    def test_log_writes_non_ascii(self, logger_instance):
        """Non-ASCII text is written with the configured encoding."""
        logger, log_folder = logger_instance
        logger.log("Morbus Crohn – Ödem, Hämatom", cmdline=False)
        path = os.path.join(log_folder, logger.config.file_name)
        with open(path, "r", encoding="utf-8") as f:
            assert "Morbus Crohn – Ödem, Hämatom" in f.read()

    def test_log_creates_missing_folder(self, tmp_path):
        """The log folder is created on the first log() call if missing."""
        log_folder  = tmp_path / "nested" / "logs"
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w") as f:
            yaml.safe_dump({"logger": {"folder": str(log_folder)}}, f)
        logger = Logger(config=str(config_path))
        logger.log("Created", cmdline=False)
        assert "Created" in read_log_lines(str(log_folder))[0]

    def test_log_recreates_deleted_folder(self, make_logger, tmp_path):
        """A folder deleted during the run is created again on the next line."""
        import shutil
        logger, lines = make_logger(folder=str(tmp_path / "logs"))
        logger.log("Before", cmdline=False)
        shutil.rmtree(tmp_path / "logs")
        logger.log("After", cmdline=False)
        assert len(lines()) == 1
        assert "After" in lines()[0]

    def test_log_does_not_check_folder_when_it_exists(self, make_logger, monkeypatch):
        """os.makedirs is only called when opening the file shows the folder is missing."""
        logger, lines = make_logger()
        logger.log("First", cmdline=False)
        calls = []
        monkeypatch.setattr(os, "makedirs", lambda *a, **k: calls.append(a))
        logger.log("Second", cmdline=False)
        assert calls == []
        assert len(lines()) == 2

    def test_folder_that_is_a_file_raises(self, make_logger, tmp_path):
        """A path that cannot be a folder is reported, not silently skipped."""
        blocker = tmp_path / "blocker"
        blocker.write_text("")
        logger, _ = make_logger(folder=str(blocker))
        with pytest.raises(OSError):
            logger.log("x", cmdline=False)


    def test_line_format_matches_documentation(self, make_logger):
        """
        A line has the documented form
        [<timestamp>] [<LEVEL>] (<runtime>) <message>.
        """
        logger, lines = make_logger(log_runtime=True)
        logger.warning("Documented", cmdline=False)
        pattern = (
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] "
            r"\[WARNING\] \(0 Minutes\) Documented\n$"
        )
        assert re.match(pattern, lines()[0])

    def test_custom_timestamp_format(self, make_logger):
        logger, lines = make_logger(format="%H:%M:%S")
        logger.log("Time only", cmdline=False)
        assert re.match(r"^\[\d{2}:\d{2}:\d{2}\] \[INFO   \] Time only$", lines()[0])

    def test_console_line_equals_file_line(self, make_logger, capsys):
        """Without colors, stdout shows exactly the line written to the file."""
        logger, lines = make_logger()
        logger.log("Same on both")
        assert capsys.readouterr().out == lines()[0]

    def test_configured_encoding_is_used(self, make_logger):
        logger, _ = make_logger(encoding="latin-1")
        logger.log("Ödem", cmdline=False)
        path = os.path.join(logger.config.folder, logger.config.file_name)
        with open(path, "rb") as f:
            assert "Ödem".encode("latin-1") in f.read()

    def test_unknown_level_raises_even_for_none(self, make_logger):
        """The level is checked before None is ignored, so typos are never hidden."""
        logger, _ = make_logger()
        with pytest.raises(ValueError):
            logger.log(None, level="warn")

    @pytest.mark.parametrize("method", ["info", "warning", "error"])
    def test_level_helpers_ignore_none(self, make_logger, method):
        logger, lines = make_logger()
        getattr(logger, method)(None, cmdline=False)
        assert lines() == []


    def test_built_in_messages_share_one_structure(self, make_logger):
        """
        Every message the logger writes itself is one line of the form
        [<timestamp>] [<LEVEL>] (<runtime>) <sentence ending in ".">.
        (Header lines and exception() tracebacks are tested separately.)
        """
        logger, lines = make_logger(log_runtime=True)
        logger.printFileProcessingStart("/a/in.csv", cmdline=False)
        logger.printFileProcessingEnd("/a/in.csv", cmdline=False)
        logger.printReadFileStart("in.csv", cmdline=False)
        logger.printReadFileEnd("in.csv", cmdline=False)
        logger.printWriteFileStart("out.csv", cmdline=False)
        logger.printWriteFileEnd("out.csv", cmdline=False)
        logger.printDataFrameRowCount([1, 2, 3], cmdline=False)
        logger.exception(cmdline=False)
        with logger.step("Load", cmdline=False):
            pass
        for error in (ValueError("bad\nrow"), KeyboardInterrupt()):
            with pytest.raises(BaseException):
                with logger.step("Map", cmdline=False):
                    raise error
        pattern = re.compile(
            r"^\[[^\]]+\] \[(INFO   |WARNING|ERROR  )\] \(\d+ Minutes?\) "
            r"[A-Za-z(][^\n]*[^.]\.\n$"
        )
        content = lines()
        assert len(content) == 14
        for line in content:
            assert pattern.match(line), line


# --------------------------------------------------------------------------
# printHeader()
# --------------------------------------------------------------------------

class TestPrintHeader:
    """
    Tests for Logger.printHeader() — line count, border characters,
    centered body text, the integer length returned, and cmdline.
    """

    def test_writes_three_lines(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printHeader("Section")
        lines = read_log_lines(log_folder)
        assert len(lines) == 3

    def test_header_lines_use_header_char(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printHeader("Section")
        lines = read_log_lines(log_folder)
        header_char   = logger.config.header_char
        header_length = logger.config.header_length
        # First and last lines are the top/bottom border.
        assert header_char * header_length in lines[0]
        assert header_char * header_length in lines[2]

    def test_body_contains_centered_text(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printHeader("Title")
        lines = read_log_lines(log_folder)
        assert "Title" in lines[1]

    def test_returns_expected_length(self, logger_instance):
        """
        The return value should equal the combined character length of all
        three logged lines (excluding timestamps and newlines).
        """
        logger, _ = logger_instance
        header_length = logger.config.header_length
        header_char   = logger.config.header_char
        head_foot     = header_char * header_length
        body          = header_char + "Body".center(header_length - 2) + header_char
        expected      = 2 * len(head_foot) + len(body)
        result        = logger.printHeader("Body")
        assert result == expected

    def test_long_text_is_truncated(self, logger_instance):
        """Text longer than header_length - 2 must not widen the body line."""
        logger, _     = logger_instance
        header_length = logger.config.header_length
        result        = logger.printHeader("x" * (header_length * 2))
        assert result == 3 * header_length

    def test_cmdline_false_writes_file_only(self, logger_instance, capsys):
        logger, log_folder = logger_instance
        logger.printHeader("Quiet", cmdline=False)
        assert capsys.readouterr().out == ""
        assert len(read_log_lines(log_folder)) == 3


    def test_custom_header_char_and_length(self, make_logger):
        logger, lines = make_logger(header_char="*", header_length=12)
        logger.printHeader("Hi", cmdline=False)
        content = [line.rstrip("\n") for line in lines()]
        assert content[0].endswith("] " + "*" * 12)
        assert content[1].endswith("] *    Hi    *")
        assert content[2].endswith("] " + "*" * 12)

    def test_empty_text_gives_blank_body(self, make_logger):
        logger, lines = make_logger(header_char="#", header_length=6)
        logger.printHeader(cmdline=False)
        assert lines()[1].rstrip("\n").endswith("] #    #")

    def test_header_is_info_and_hidden_above_info(self, make_logger):
        """
        Headers are logged with level "info", so a minimum level of
        "warning" hides them. The return value does not depend on that.
        """
        logger, lines = make_logger(level="warning", header_length=10)
        assert logger.printHeader("Hidden", cmdline=False) == 30
        assert lines() == []


# --------------------------------------------------------------------------
# File-processing helper methods
# --------------------------------------------------------------------------

class TestFileProcessingHelpers:
    """
    Tests for the file-processing helper methods:
    printFileProcessingStart/End, printReadFileStart/End,
    printWriteFileStart/End.

    Verifies that only the basename of the path is logged (not the full
    directory), that the return value equals the message length, and that
    cmdline=False keeps the message off stdout.
    """

    def test_print_file_processing_start(self, logger_instance):
        logger, log_folder = logger_instance
        ret = logger.printFileProcessingStart("/path/to/input_data.csv")
        lines = read_log_lines(log_folder)
        assert lines[0].endswith(" Processing file 'input_data.csv' started.\n")
        assert ret == len("Processing file 'input_data.csv' started.")

    def test_print_file_processing_end(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printFileProcessingEnd("/path/to/input_data.csv")
        lines = read_log_lines(log_folder)
        assert "Processing file 'input_data.csv' completed." in lines[0]

    def test_print_read_file_start(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printReadFileStart("data.csv")
        lines = read_log_lines(log_folder)
        assert lines[0].endswith(" Reading file 'data.csv' started.\n")

    def test_print_read_file_end(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printReadFileEnd("data.csv")
        lines = read_log_lines(log_folder)
        assert "Reading file 'data.csv' completed." in lines[0]

    def test_print_write_file_start(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printWriteFileStart("data.csv")
        lines = read_log_lines(log_folder)
        assert lines[0].endswith(" Writing file 'data.csv' started.\n")

    def test_print_write_file_end(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printWriteFileEnd("data.csv")
        lines = read_log_lines(log_folder)
        assert "Writing file 'data.csv' completed." in lines[0]

    def test_basename_extracted_from_full_path(self, logger_instance):
        """Directory components of the path must not appear in the log message."""
        logger, log_folder = logger_instance
        logger.printFileProcessingStart("/some/nested/dir/report.xlsx")
        lines = read_log_lines(log_folder)
        assert "report.xlsx" in lines[0]
        assert "nested" not in lines[0]

    @pytest.mark.parametrize("method", [
        "printFileProcessingStart", "printFileProcessingEnd",
        "printReadFileStart", "printReadFileEnd",
        "printWriteFileStart", "printWriteFileEnd",
    ])
    def test_cmdline_false_writes_file_only(self, logger_instance, capsys, method):
        logger, log_folder = logger_instance
        getattr(logger, method)("data.csv", cmdline=False)
        assert capsys.readouterr().out == ""
        assert len(read_log_lines(log_folder)) == 1


# --------------------------------------------------------------------------
# printDataFrameRowCount()
# --------------------------------------------------------------------------

class TestPrintDataFrameRowCount:
    """
    Tests for Logger.printDataFrameRowCount() — logs the row count of a
    DataFrame, or does nothing when None is passed; cmdline=False keeps
    the message off stdout.
    """

    def test_logs_row_count_for_non_empty_dataframe(self, logger_instance):
        logger, log_folder = logger_instance
        df  = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        ret = logger.printDataFrameRowCount(df)
        lines = read_log_lines(log_folder)
        assert lines[0].endswith(" Row count: 3.\n")
        assert ret == len("Row count: 3.")

    def test_logs_row_count_for_empty_dataframe(self, logger_instance):
        logger, log_folder = logger_instance
        df = pd.DataFrame()
        logger.printDataFrameRowCount(df)
        lines = read_log_lines(log_folder)
        assert "Row count: 0" in lines[0]

    def test_none_dataframe_does_not_log(self, logger_instance):
        """None is silently ignored; no line is written and 0 is returned."""
        logger, log_folder = logger_instance
        ret = logger.printDataFrameRowCount(None)
        lines = read_log_lines(log_folder)
        assert lines == []
        assert ret == 0

    def test_cmdline_false_writes_file_only(self, logger_instance, capsys):
        logger, log_folder = logger_instance
        logger.printDataFrameRowCount(pd.DataFrame({"a": [1]}), cmdline=False)
        assert capsys.readouterr().out == ""
        assert "Row count: 1" in read_log_lines(log_folder)[0]


    def test_any_sized_object_works(self, make_logger):
        """Only len() is used, so pandas is not required."""
        logger, lines = make_logger()
        logger.printDataFrameRowCount([1, 2, 3, 4], cmdline=False)
        assert "Row count: 4" in lines()[0]


# --------------------------------------------------------------------------
# reset() and config sources
# --------------------------------------------------------------------------

class TestResetAndConfigSources:
    """
    Tests for Logger.reset() and for passing the configuration as a dict,
    a path object or a LoggerConfig instead of a YAML path string.
    """

    def test_reset_discards_instance_and_config(self, logger_instance):
        logger, _ = logger_instance
        Logger.reset()
        assert Logger._instance  is None
        assert Logger.config     is None
        assert Logger.start_time == 0.0
        assert Logger(config={"folder": "./x/"}) is not logger

    def test_reset_restarts_runtime_clock(self, logger_instance):
        logger, _ = logger_instance
        Logger.start_time -= 600
        Logger.reset()
        Logger(config={})
        assert time.monotonic() - Logger.start_time < 60

    def test_config_from_dict(self, tmp_path):
        logger = Logger(config={"folder": str(tmp_path), "level": "warning"})
        assert logger.config.folder == str(tmp_path)
        assert logger.config.level  == "warning"
        # Fields not given fall back to the model defaults.
        assert logger.config.file_name == "output.log"

    def test_config_from_dict_is_validated(self):
        with pytest.raises(ValidationError):
            Logger(config={"unknown_key": 1})

    def test_config_from_full_configuration_dict(self, tmp_path):
        """A parsed YAML file with a "logger" key uses that section."""
        logger = Logger(config={
            "logger"  : {"folder": str(tmp_path), "level": "error"},
            "embedder": {"model_id": ["model-a"]},
        })
        assert logger.config.folder == str(tmp_path)
        assert logger.config.level  == "error"

    def test_config_from_full_configuration_dict_with_empty_section(self):
        """An empty "logger" section falls back to the model defaults."""
        logger = Logger(config={"logger": None})
        assert logger.config == LoggerConfig()

    def test_config_from_logger_config_is_copied(self, tmp_path):
        """Later changes to the passed object must not affect the logger."""
        config = LoggerConfig(folder=str(tmp_path), file_name="a.log")
        logger = Logger(config=config)
        config.file_name = "changed.log"
        assert logger.config.file_name == "a.log"

    def test_config_from_path_object(self, config_file):
        """os.PathLike paths are accepted as well as strings."""
        import pathlib
        config_path, _ = config_file
        logger = Logger(config=pathlib.Path(config_path))
        assert logger.config.file_name == "output.log"


    def test_full_configuration_dict_is_validated(self):
        with pytest.raises(ValidationError):
            Logger(config={"logger": {"unknown_key": 1}})

    def test_old_reference_follows_new_configuration(self, tmp_path):
        """
        References kept from before reset() share the class-level state,
        so they log with the configuration loaded afterwards.
        """
        old = Logger(config={"folder": str(tmp_path / "old")})
        Logger.reset()
        Logger(config={"folder": str(tmp_path / "new")})
        old.log("After reset", cmdline=False)
        assert "After reset" in read_log_lines(str(tmp_path / "new"))[0]
        assert not (tmp_path / "old").exists()


    def test_config_from_other_mapping(self, tmp_path):
        """
        Mappings that are not dicts (e.g. OmegaConf configs) are accepted,
        including nested ones, instead of being mistaken for a path.
        """
        logger = Logger(config=MappingProxyType({
            "logger": MappingProxyType({
                "folder": str(tmp_path),
                "colors": MappingProxyType({"info": "green"}),
            }),
        }))
        assert logger.config.folder      == str(tmp_path)
        assert logger.config.colors.info == "green"

    def test_old_reference_without_new_call_raises(self, logger_instance):
        """Logging through an old reference after reset() explains the problem."""
        logger, _ = logger_instance
        Logger.reset()
        with pytest.raises(RuntimeError, match="no configuration"):
            logger.log("x", cmdline=False)
        with pytest.raises(RuntimeError, match="no configuration"):
            logger.printHeader("x", cmdline=False)


# --------------------------------------------------------------------------
# Log levels and colors
# --------------------------------------------------------------------------

@pytest.fixture
def make_logger(tmp_path):
    """
    Factory building a Logger that writes to a temporary folder, with the
    given config overrides. Returns the logger and a function reading the
    lines of its log file.
    """
    def factory(**overrides):
        config = {"folder": str(tmp_path), **overrides}
        logger = Logger(config=config)
        return logger, lambda: read_log_lines(config["folder"], logger.config.file_name)
    return factory


@pytest.fixture
def force_colors(monkeypatch):
    """Pretend stdout is a terminal, so that colors are applied."""
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(Logger, "_useColors", staticmethod(lambda: True))


class TestLevels:
    """
    Tests for log levels: the level tag in each line, the minimum level,
    the info()/warning()/error() helpers and the console colors.
    """

    @pytest.mark.parametrize("level", ["info", "warning", "error"])
    def test_level_tag_in_line(self, make_logger, level):
        logger, lines = make_logger()
        logger.log("Message", cmdline=False, level=level)
        assert f"[{level.upper():<7}] Message" in lines()[0]

    def test_default_level_is_info(self, make_logger):
        logger, lines = make_logger()
        logger.log("Message", cmdline=False)
        assert "[INFO   ]" in lines()[0]

    def test_helpers_use_their_level(self, make_logger):
        logger, lines = make_logger()
        logger.info("a", cmdline=False)
        logger.warning("b", cmdline=False)
        logger.error("c", cmdline=False)
        assert "[INFO   ]" in lines()[0]
        assert "[WARNING]" in lines()[1]
        assert "[ERROR  ]" in lines()[2]

    def test_unknown_level_raises(self, make_logger):
        logger, _ = make_logger()
        with pytest.raises(ValueError):
            logger.log("Message", level="debug")

    def test_minimum_level_filters_file_and_console(self, make_logger, capsys):
        logger, lines = make_logger(level="warning")
        logger.info("hidden")
        logger.warning("shown")
        logger.error("also shown")
        out = capsys.readouterr().out
        assert len(lines()) == 2
        assert "hidden" not in out
        assert "shown" in out and "also shown" in out

    def test_level_error_only_logs_errors(self, make_logger):
        logger, lines = make_logger(level="error")
        logger.info("a", cmdline=False)
        logger.warning("b", cmdline=False)
        logger.error("c", cmdline=False)
        assert len(lines()) == 1
        assert "c" in lines()[0]

    @pytest.mark.parametrize("level, code", [
        ("info", "\033[37m"), ("warning", "\033[33m"), ("error", "\033[31m"),
    ])
    def test_default_colors_on_console(self, make_logger, capsys, force_colors, level, code):
        logger, _ = make_logger()
        logger.log("Colored", level=level)
        out = capsys.readouterr().out
        assert out.startswith(code)
        assert out.rstrip("\n").endswith("\033[0m")

    def test_colors_never_in_file(self, make_logger, force_colors):
        logger, lines = make_logger()
        logger.error("Colored")
        assert "\033[" not in lines()[0]

    def test_custom_color(self, make_logger, capsys, force_colors):
        logger, _ = make_logger(colors={"info": "green"})
        logger.info("Green")
        assert capsys.readouterr().out.startswith("\033[32m")

    def test_color_none_disables_color(self, make_logger, capsys, force_colors):
        logger, _ = make_logger(colors={"error": "none"})
        logger.error("Plain")
        assert "\033[" not in capsys.readouterr().out

    def test_no_colors_when_not_a_terminal(self, make_logger, capsys):
        """capsys replaces stdout with a non-terminal stream."""
        logger, _ = make_logger()
        logger.error("Plain")
        assert "\033[" not in capsys.readouterr().out

    def test_no_color_env_disables_colors(self, monkeypatch):
        class FakeTerminal:
            def isatty(self):
                return True
        monkeypatch.setattr("sys.stdout", FakeTerminal())
        monkeypatch.delenv("NO_COLOR", raising=False)
        assert Logger._useColors() is True
        monkeypatch.setenv("NO_COLOR", "1")
        assert Logger._useColors() is False
        # An empty NO_COLOR is ignored, as https://no-color.org specifies.
        monkeypatch.setenv("NO_COLOR", "")
        assert Logger._useColors() is True

    def test_stdout_without_isatty_has_no_colors(self, monkeypatch):
        monkeypatch.setattr("sys.stdout", object())
        assert Logger._useColors() is False


# --------------------------------------------------------------------------
# Runtime formats
# --------------------------------------------------------------------------

class TestRuntimeFormat:
    """Tests for the "minutes" and "hms" runtime display formats."""

    def test_minutes_format(self, make_logger):
        logger, lines = make_logger(log_runtime=True, runtime_format="minutes")
        Logger.start_time = time.monotonic() - 125
        logger.log("x", cmdline=False)
        assert "(2 Minutes) x" in lines()[0]

    def test_minutes_format_singular(self, make_logger):
        logger, lines = make_logger(log_runtime=True, runtime_format="minutes")
        Logger.start_time = time.monotonic() - 65
        logger.log("x", cmdline=False)
        assert "(1 Minute) x" in lines()[0]

    def test_hms_format(self, make_logger):
        logger, lines = make_logger(log_runtime=True, runtime_format="hms")
        Logger.start_time = time.monotonic() - 3723
        logger.log("x", cmdline=False)
        assert "(01:02:03) x" in lines()[0]

    def test_format_duration(self):
        assert Logger._formatDuration(3723.456)               == "01:02:03"
        assert Logger._formatDuration(3723.456, precise=True) == "01:02:03.46"
        assert Logger._formatDuration(0.5, precise=True)      == "00:00:00.50"
        assert Logger._formatDuration(125.9)                  == "00:02:05"

    @pytest.mark.parametrize("seconds, expected", [
        (59.999,   "00:01:00.00"),
        (3599.996, "01:00:00.00"),
        (0.004,    "00:00:00.00"),
        (59.994,   "00:00:59.99"),
    ])
    def test_format_duration_rounding_carries_over(self, seconds, expected):
        """Rounding up to a full minute or hour never shows 60 seconds."""
        assert Logger._formatDuration(seconds, precise=True) == expected


    def test_runtime_counts_from_first_call(self, make_logger):
        """Later Logger() calls with a new config do not restart the clock."""
        logger, lines = make_logger(log_runtime=True)
        Logger.start_time = time.monotonic() - 180
        Logger(config={"folder": logger.config.folder, "log_runtime": True})
        logger.log("x", cmdline=False)
        assert "(3 Minutes) x" in lines()[0]


# --------------------------------------------------------------------------
# exception()
# --------------------------------------------------------------------------

class TestException:
    """Tests for Logger.exception() — error level and traceback output."""

    def test_logs_current_exception_with_traceback(self, make_logger):
        logger, lines = make_logger()
        try:
            raise ValueError("bad value")
        except ValueError:
            logger.exception("Mapping failed", cmdline=False)
        content = "".join(lines())
        assert "[ERROR  ] Mapping failed" in lines()[0]
        assert "Traceback (most recent call last):" in content
        assert "ValueError: bad value" in content

    def test_logs_explicit_exception(self, make_logger):
        logger, lines = make_logger()
        try:
            raise KeyError("missing")
        except KeyError as e:
            error = e
        logger.exception("Lookup failed", exc=error, cmdline=False)
        assert "KeyError: 'missing'" in "".join(lines())

    def test_without_exception_logs_message_only(self, make_logger):
        logger, lines = make_logger()
        logger.exception("Nothing raised", cmdline=False)
        assert len(lines()) == 1
        assert "[ERROR  ] Nothing raised" in lines()[0]

    def test_without_message_or_exception_logs_note(self, make_logger):
        """No empty error line: a note says what happened instead."""
        logger, lines = make_logger()
        logger.exception(cmdline=False)
        assert lines()[0].endswith(
            "] exception() was called without a message or an active exception.\n"
        )

    def test_without_message_logs_traceback(self, make_logger):
        logger, lines = make_logger()
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            logger.exception(cmdline=False)
        assert "[ERROR  ] Traceback" in lines()[0]


    def test_logs_chained_exceptions(self, make_logger):
        logger, lines = make_logger()
        try:
            try:
                raise KeyError("HP:0000001")
            except KeyError as e:
                raise RuntimeError("Mapping failed") from e
        except RuntimeError:
            logger.exception("Pipeline stopped", cmdline=False)
        content = "".join(lines())
        assert "KeyError: 'HP:0000001'" in content
        assert "direct cause of the following exception" in content
        assert "RuntimeError: Mapping failed" in content

    def test_traceback_printed_to_console(self, make_logger, capsys):
        logger, lines = make_logger()
        try:
            raise ValueError("bad value")
        except ValueError:
            logger.exception("Mapping failed")
        out = capsys.readouterr().out
        assert "ValueError: bad value" in out
        assert out == "".join(lines())

    def test_logged_even_with_minimum_level_error(self, make_logger):
        logger, lines = make_logger(level="error")
        logger.exception("Still logged", cmdline=False)
        assert "[ERROR  ] Still logged" in lines()[0]


# --------------------------------------------------------------------------
# step()
# --------------------------------------------------------------------------

class TestStep:
    """Tests for the Logger.step() context manager."""

    def test_logs_start_and_completion(self, make_logger):
        logger, lines = make_logger()
        with logger.step("Loading", cmdline=False):
            pass
        assert "Step 'Loading' started." in lines()[0]
        assert "[INFO   ] Step 'Loading' completed in 00:00:0" in lines()[1]

    def test_failure_is_logged_and_reraised(self, make_logger):
        logger, lines = make_logger()
        with pytest.raises(ValueError):
            with logger.step("Parsing", cmdline=False):
                raise ValueError("bad row")
        assert len(lines()) == 2
        assert "[ERROR  ] Step 'Parsing' failed after" in lines()[1]
        assert "(ValueError: bad row)" in lines()[1]

    def test_measures_duration(self, make_logger, monkeypatch):
        logger, lines = make_logger()
        # Replace the clock seen by the Logger module only, so that the
        # step appears to take exactly 65.5 seconds.
        clock = iter([100.0, 165.5])
        module = sys.modules["logger.Logger"]
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        with logger.step("Embedding", cmdline=False):
            pass
        assert "completed in 00:01:05.50." in lines()[1]


    def test_multi_line_error_stays_on_one_line(self, make_logger):
        logger, lines = make_logger()
        with pytest.raises(ValueError):
            with logger.step("Parsing", cmdline=False):
                raise ValueError("bad row\n  in line 12")
        assert len(lines()) == 2
        assert lines()[1].endswith("(ValueError: bad row in line 12).\n")

    def test_describe_error(self):
        assert Logger._describeError(KeyError("HP:1"))     == "KeyError: 'HP:1'"
        assert Logger._describeError(ValueError(""))       == "ValueError"
        assert Logger._describeError(ValueError("a\n\nb")) == "ValueError: a b"

    def test_keyboard_interrupt_is_logged_and_reraised(self, make_logger):
        """BaseExceptions such as KeyboardInterrupt are logged as failures too."""
        logger, lines = make_logger()
        with pytest.raises(KeyboardInterrupt):
            with logger.step("Waiting", cmdline=False):
                raise KeyboardInterrupt
        assert "Step 'Waiting' failed after" in lines()[1]
        assert lines()[1].endswith("(KeyboardInterrupt).\n")

    def test_prints_to_console(self, make_logger, capsys):
        logger, _ = make_logger()
        with logger.step("Loading"):
            pass
        out = capsys.readouterr().out
        assert "Step 'Loading' started." in out
        assert "Step 'Loading' completed in" in out

    def test_minimum_level_hides_progress_but_not_failure(self, make_logger):
        logger, lines = make_logger(level="error")
        with logger.step("Quiet", cmdline=False):
            pass
        with pytest.raises(ValueError):
            with logger.step("Broken", cmdline=False):
                raise ValueError("bad row")
        assert len(lines()) == 1
        assert "Step 'Broken' failed after" in lines()[0]


# --------------------------------------------------------------------------
# Thread safety
# --------------------------------------------------------------------------

class TestThreadSafety:
    """
    Tests that concurrent use from several threads yields one instance and
    complete, non-interleaved log lines and headers.
    """

    def test_concurrent_instantiation_gives_one_instance(self):
        instances = []
        def create():
            instances.append(Logger(config={"folder": "./data/logs/"}))
        threads = [threading.Thread(target=create) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert all(instance is instances[0] for instance in instances)

    def test_concurrent_logging_keeps_lines_intact(self, make_logger):
        logger, lines = make_logger(header_length=10)
        def work(n):
            for i in range(100):
                logger.log(f"thread {n} line {i}", cmdline=False)
            logger.printHeader(f"T{n}")
        threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        content = lines()
        assert len(content) == 8 * 103
        assert all(line.endswith("\n") and line.startswith("[") for line in content)
        # Every header body line is directly surrounded by its two borders.
        for i, line in enumerate(content):
            if line.rstrip("\n").endswith("=") and "T" in line:
                assert content[i - 1].rstrip("\n").endswith("=" * 10)
                assert content[i + 1].rstrip("\n").endswith("=" * 10)


# --------------------------------------------------------------------------
# console setting
# --------------------------------------------------------------------------

class TestConsoleSetting:
    """Tests for console: false, which keeps all output in the log file."""

    def test_console_false_prints_nothing(self, make_logger, capsys):
        logger, lines = make_logger(console=False, header_length=10)
        logger.log("a")
        logger.warning("b")
        logger.printHeader("c")
        logger.printReadFileStart("d.csv")
        with logger.step("e"):
            pass
        try:
            raise ValueError("f")
        except ValueError:
            logger.exception("g")
        assert capsys.readouterr().out == ""
        # Two messages, three header lines, one file event, two step lines
        # and one exception() entry (its traceback lines have no prefix).
        entries = [line for line in lines() if line.startswith("[")]
        assert len(entries) == 2 + 3 + 1 + 2 + 1

    def test_console_true_still_respects_cmdline(self, make_logger, capsys):
        logger, _ = make_logger(console=True)
        logger.log("quiet", cmdline=False)
        logger.log("loud")
        out = capsys.readouterr().out
        assert "quiet" not in out
        assert "loud" in out

    def test_console_can_be_switched_at_runtime(self, make_logger, capsys):
        logger, lines = make_logger()
        logger.config.console = False
        logger.log("file only")
        assert capsys.readouterr().out == ""
        assert "file only" in lines()[0]


# --------------------------------------------------------------------------
# {start} placeholder in file_name
# --------------------------------------------------------------------------

class TestFileNamePlaceholder:
    """Tests for a new log file per run via {start} in file_name."""

    def test_start_placeholder_names_file_after_run_start(self, tmp_path):
        logger = Logger(config={
            "folder"   : str(tmp_path),
            "file_name": "run_{start:%Y%m%d_%H%M%S}.log",
        })
        Logger.start_datetime = datetime(2026, 10, 30, 9, 5, 7)
        logger.log("x", cmdline=False)
        expected = tmp_path / "run_20261030_090507.log"
        assert logger.logFilePath() == str(expected)
        assert "x" in expected.read_text()

    def test_all_lines_of_a_run_go_to_one_file(self, tmp_path):
        """The name is fixed at the start of the run, also across reconfiguration."""
        config = {"folder": str(tmp_path), "file_name": "run_{start:%Y%m%d_%H%M%S_%f}.log"}
        logger = Logger(config=config)
        logger.log("first", cmdline=False)
        time.sleep(0.01)
        Logger(config=dict(config, level="info"))
        logger.log("second", cmdline=False)
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert len(files[0].read_text().splitlines()) == 2

    def test_reset_starts_a_new_file(self, tmp_path):
        config = {"folder": str(tmp_path), "file_name": "run_{start:%H%M%S_%f}.log"}
        Logger(config=config).log("first", cmdline=False)
        time.sleep(0.01)
        Logger.reset()
        Logger(config=config).log("second", cmdline=False)
        assert len(list(tmp_path.iterdir())) == 2

    def test_name_without_placeholder_is_used_as_is(self, make_logger):
        logger, _ = make_logger(file_name="output.log")
        assert logger.logFilePath() == os.path.join(logger.config.folder, "output.log")

    def test_doubled_braces_are_literal(self, make_logger):
        logger, _ = make_logger(file_name="run_{{1}}.log")
        logger.log("x", cmdline=False)
        assert os.path.basename(logger.logFilePath()) == "run_{1}.log"
        assert os.path.exists(logger.logFilePath())

    def test_log_file_path_without_config_raises(self):
        with pytest.raises(RuntimeError, match="no configuration"):
            Logger.__new__(Logger).logFilePath()


# --------------------------------------------------------------------------
# Reconfiguration (replace option and change notice)
# --------------------------------------------------------------------------

class TestReconfiguration:
    """
    Tests that library code cannot silently override the entry point's
    configuration: replace=False only fills in a missing configuration,
    and every actual change is logged.
    """

    def test_replace_false_keeps_loaded_config(self, make_logger, tmp_path):
        logger, lines = make_logger(level="info")
        Logger(config={"folder": str(tmp_path / "other"), "level": "error"}, replace=False)
        assert logger.config.level == "info"
        assert lines() == []
        assert not (tmp_path / "other").exists()

    def test_replace_false_loads_when_nothing_is_loaded(self, tmp_path):
        logger = Logger(config={"folder": str(tmp_path)}, replace=False)
        assert logger.config.folder == str(tmp_path)

    def test_replace_false_without_config_keeps_loaded_config(self, make_logger):
        logger, _ = make_logger(level="warning")
        Logger(replace=False)
        assert logger.config.level == "warning"

    def test_change_is_logged_with_changed_settings(self, make_logger, capsys):
        logger, lines = make_logger(level="info")
        Logger(config={"folder": logger.config.folder, "level": "info", "log_runtime": True})
        assert len(lines()) == 1
        assert lines()[0].endswith(
            "Logger configuration changed (log_runtime: False -> True).\n"
        )
        assert capsys.readouterr().out.count("configuration changed") == 1

    def test_change_of_file_is_logged_in_both_files(self, make_logger, tmp_path, capsys):
        logger, old_lines = make_logger()
        new_folder = tmp_path / "new"
        Logger(config={"folder": str(new_folder)})
        new_path = os.path.join(str(new_folder), "output.log")
        assert old_lines()[0].endswith(f"Further messages go to '{new_path}'.\n")
        assert "folder: " in old_lines()[0]
        new_lines = read_log_lines(str(new_folder))
        assert len(new_lines) == 1
        assert new_lines[0].endswith(f"-> '{new_folder}').\n")
        # The note is printed once, not once per file.
        assert capsys.readouterr().out.count("configuration changed") == 1

    def test_same_config_again_logs_nothing(self, make_logger):
        logger, lines = make_logger(level="warning")
        Logger(config={"folder": logger.config.folder, "level": "warning"})
        assert lines() == []

    def test_invalid_new_config_keeps_old_and_logs_nothing(self, make_logger):
        logger, lines = make_logger()
        with pytest.raises(ValidationError):
            Logger(config={"level": "debug"})
        assert logger.config.level == "info"
        assert lines() == []


# --------------------------------------------------------------------------
# Standard logging capture
# --------------------------------------------------------------------------

class TestStandardLogging:
    """
    Tests for captureStandardLogging(): records of Python's standard logging
    module (as used by torch, transformers or vLLM) end up in the log file.
    """

    def test_warning_is_captured_with_logger_name(self, make_logger):
        logger, lines = make_logger()
        logger.captureStandardLogging(cmdline=False)
        logging.getLogger("lib.part").warning("Disk %s is %d%% full", "/data", 91)
        assert lines()[0].endswith("[WARNING] lib.part: Disk /data is 91% full\n")

    def test_levels_are_mapped(self, make_logger):
        logger, lines = make_logger()
        logger.captureStandardLogging(level="info", cmdline=False)
        library = logging.getLogger("lib.levels")
        library.info("i")
        library.warning("w")
        library.error("e")
        library.critical("c")
        tags = [line.split("] ")[1] for line in lines()]
        assert tags == ["[INFO   ", "[WARNING", "[ERROR  ", "[ERROR  "]

    def test_records_below_level_are_ignored(self, make_logger):
        logger, lines = make_logger()
        logger.captureStandardLogging(level="warning", cmdline=False)
        logging.getLogger("lib.quiet").info("hidden")
        assert lines() == []

    def test_info_level_lowers_and_restores_logger_level(self, make_logger):
        logger, lines = make_logger()
        root     = logging.getLogger()
        previous = root.level
        logger.captureStandardLogging(level="info", cmdline=False)
        logging.getLogger("lib.info").info("shown")
        assert "lib.info: shown" in lines()[0]
        Logger.releaseStandardLogging()
        assert root.level == previous

    def test_logger_minimum_level_still_applies(self, make_logger):
        logger, lines = make_logger(level="error")
        logger.captureStandardLogging(cmdline=False)
        logging.getLogger("lib.filtered").warning("dropped")
        assert lines() == []

    def test_exception_traceback_is_logged(self, make_logger):
        logger, lines = make_logger()
        logger.captureStandardLogging(cmdline=False)
        try:
            raise ValueError("bad weights")
        except ValueError:
            logging.getLogger("lib.model").exception("Loading failed")
        content = "".join(lines())
        assert "[ERROR  ] lib.model: Loading failed" in lines()[0]
        assert "ValueError: bad weights" in content

    def test_non_propagating_logger_can_be_named(self, make_logger):
        logger, lines = make_logger()
        library = logging.getLogger("lib.isolated")
        library.propagate = False
        try:
            logger.captureStandardLogging(loggers="lib.isolated", cmdline=False)
            library.warning("own handler")
            assert "lib.isolated: own handler" in lines()[0]
        finally:
            library.propagate = True

    def test_capturing_again_replaces_the_handler(self, make_logger):
        logger, lines = make_logger()
        logger.captureStandardLogging(cmdline=False)
        logger.captureStandardLogging(cmdline=False)
        handlers = [h for h in logging.getLogger().handlers if h is Logger._standard_handler]
        assert len(handlers) == 1
        logging.getLogger("lib.once").warning("once")
        assert len(lines()) == 1

    def test_release_and_reset_remove_the_handler(self, make_logger):
        logger, lines = make_logger()
        handler = logger.captureStandardLogging(cmdline=False)
        Logger.releaseStandardLogging()
        assert handler not in logging.getLogger().handlers
        logger.captureStandardLogging(cmdline=False)
        handler = Logger._standard_handler
        Logger.reset()
        assert handler not in logging.getLogger().handlers

    def test_console_output(self, make_logger, capsys):
        logger, _ = make_logger()
        logger.captureStandardLogging()
        logging.getLogger("lib.console").warning("visible")
        captured = capsys.readouterr()
        assert "lib.console: visible" in captured.out
        # Not also printed by the standard logging module's fallback.
        assert "visible" not in captured.err

    def test_unknown_level_raises(self, make_logger):
        logger, _ = make_logger()
        with pytest.raises(ValueError):
            logger.captureStandardLogging(level="debug")

    def test_failure_is_reported_not_raised(self, make_logger, capsys):
        """A handler problem goes to stderr instead of breaking the caller."""
        logger, _ = make_logger()
        logger.captureStandardLogging(cmdline=False)
        Logger.config = None
        try:
            logging.getLogger("lib.broken").warning("x")
        finally:
            Logger.reset()
        assert "Logging error" in capsys.readouterr().err


# --------------------------------------------------------------------------
# Multiprocessing
# --------------------------------------------------------------------------

def _worker_logger_state(_):
    """Report the Logger state seen by a multiprocessing worker."""
    return (
        Logger._instance is not None,
        Logger.config.folder if Logger.config else None,
        Logger.start_time,
    )


def _run_in_worker(method: str):
    """Run _worker_logger_state in one worker started with `method`."""
    with multiprocessing.get_context(method).Pool(1) as pool:
        return pool.apply_async(_worker_logger_state, (0,)).get(timeout=60)


class TestMultiprocessing:
    """
    Tests for the README note on multiprocessing: what a worker inherits
    depends on the start method, and a forked worker never hangs on the
    logger's lock.
    """

    @pytest.mark.skipif(
        "fork" not in multiprocessing.get_all_start_methods(),
        reason="fork is not available on this platform",
    )
    def test_fork_worker_inherits_config_and_clock(self, tmp_path):
        Logger(config={"folder": str(tmp_path)})
        exists, folder, start_time = _run_in_worker("fork")
        assert exists
        assert folder     == str(tmp_path)
        assert start_time == Logger.start_time

    def test_spawn_worker_starts_without_logger(self, tmp_path):
        Logger(config={"folder": str(tmp_path)})
        exists, folder, start_time = _run_in_worker("spawn")
        assert not exists
        assert folder     is None
        assert start_time == 0.0


    @pytest.mark.skipif(
        "fork" not in multiprocessing.get_all_start_methods(),
        reason="fork is not available on this platform",
    )
    def test_fork_while_another_thread_logs_does_not_hang(self, make_logger):
        """
        A worker forked while another thread holds the logger's lock gets a
        fresh lock; otherwise its first log call would wait forever.
        """
        logger, lines = make_logger()
        held, release = threading.Event(), threading.Event()

        def hold_lock():
            with Logger._lock:
                held.set()
                release.wait(10)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        held.wait(10)
        try:
            process = multiprocessing.get_context("fork").Process(
                target=logger.log, args=("From child",), kwargs={"cmdline": False},
            )
            process.start()
            process.join(timeout=20)
            hung = process.is_alive()
            if hung:
                process.kill()
                process.join()
        finally:
            release.set()
            holder.join()

        assert not hung
        assert process.exitcode == 0
        assert any("From child" in line for line in lines())


# --------------------------------------------------------------------------
# LoggerConfig and package exports
# --------------------------------------------------------------------------

class TestLoggerConfig:
    """
    Tests for the LoggerConfig model on its own: the defaults documented
    in config/README.md, validation of values assigned after loading, and
    the package exports.
    """

    def test_documented_defaults(self):
        config = LoggerConfig()
        assert config.folder         == "./data/logs/"
        assert config.file_name      == "output.log"
        assert config.header_char    == "="
        assert config.header_length  == 60
        assert config.log_runtime    is False
        assert config.format         == "%Y-%m-%d %H:%M:%S"
        assert config.encoding       == "utf-8"
        assert config.level          == "info"
        assert config.runtime_format == "minutes"
        assert config.console        is True
        assert config.colors         == LevelColors(info="white", warning="yellow", error="red")

    def test_unknown_key_rejected(self):
        with pytest.raises(ValidationError):
            LoggerConfig(file="output.log")

    def test_header_length_two_is_allowed(self, make_logger):
        """The minimum width: one header_char on each side, empty body."""
        logger, lines = make_logger(header_length=2)
        assert logger.printHeader("Too long", cmdline=False) == 6
        assert lines()[1].rstrip("\n").endswith("] ==")

    def test_package_exports(self):
        assert sorted(logger_package.__all__) == ["LevelColors", "Logger", "LoggerConfig"]
        for name in logger_package.__all__:
            assert hasattr(logger_package, name)


    @pytest.mark.parametrize("change", [
        ("level", "debug"),
        ("header_length", 1),
        ("encoding", "not-an-encoding"),
        ("format", "%Y %"),
        ("runtime_format", "seconds"),
    ])
    def test_invalid_assignment_rejected(self, make_logger, change):
        """Values assigned after loading are validated like loaded ones."""
        logger, _ = make_logger()
        field, value = change
        with pytest.raises(ValidationError):
            setattr(logger.config, field, value)

    def test_invalid_color_assignment_rejected(self, make_logger):
        logger, _ = make_logger()
        with pytest.raises(ValidationError):
            logger.config.colors.error = "pink"

    def test_valid_assignment_takes_effect(self, make_logger):
        logger, lines = make_logger()
        logger.config.level = "error"
        logger.info("hidden", cmdline=False)
        logger.error("shown", cmdline=False)
        assert len(lines()) == 1


# --------------------------------------------------------------------------
# Console problems
# --------------------------------------------------------------------------

class ClosedPipe:
    """stdout replacement behaving like a pipe whose reader has exited."""

    def write(self, text):
        raise BrokenPipeError

    def flush(self):
        raise BrokenPipeError


class TestConsoleProblems:
    """
    Tests that problems with the console never lose a line in the log file
    and are not raised to the caller.
    """

    def test_unencodable_characters_are_replaced_on_console(self, make_logger, monkeypatch):
        """The file gets the full text, the console a replacement character."""
        console = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
        monkeypatch.setattr("sys.stdout", console)
        logger, lines = make_logger(encoding="utf-8")
        logger.log("Ödem")
        console.flush()
        assert lines()[0].endswith("] Ödem\n")
        assert console.buffer.getvalue().endswith(b"] ?dem\n")

    def test_broken_pipe_switches_console_off_and_warns_once(self, make_logger, monkeypatch):
        monkeypatch.setattr("sys.stdout", ClosedPipe())
        logger, lines = make_logger()
        logger.log("First")
        logger.log("Second")
        content = lines()
        assert len(content) == 3
        assert content[0].endswith("] First\n")
        assert "[WARNING] Console output is closed (broken pipe)" in content[1]
        assert content[2].endswith("] Second\n")

    def test_reset_switches_console_on_again(self, make_logger, monkeypatch, capsys):
        monkeypatch.setattr("sys.stdout", ClosedPipe())
        logger, _ = make_logger()
        logger.log("Lost on console")
        monkeypatch.undo()
        Logger.reset()
        logger, _ = make_logger()
        logger.log("Back on console")
        assert "Back on console" in capsys.readouterr().out


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))