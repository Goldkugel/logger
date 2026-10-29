"""
Test suite for the Logger class and LoggerConfig model.

Tests cover:
- Singleton behaviour (__new__, start_time, shared config, bare Logger() calls)
- Configuration loading (valid YAML, missing file, missing section, invalid values)
- log() — file writing, timestamps, runtime, stdout, None handling, encoding,
  folder creation
- printHeader() — line count, border characters, return value, truncation
- File-processing helper methods (printFileProcessingStart/End, etc.)
- printDataFrameRowCount() — row count logging, None handling

Run with:
    pytest tests/LoggerTest.py -v

The real ./config/config.yaml is used as the default configuration for most
tests. Run pytest from the repository root so that relative paths resolve
correctly.
"""

from __future__ import annotations

import os
import time

import pandas   as pd
import pytest
import yaml

from pydantic import ValidationError

from logger import Logger


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
    Logger._instance  = None
    Logger.config     = None
    Logger.start_time = 0
    yield
    Logger._instance  = None
    Logger.config     = None
    Logger.start_time = 0


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

    Verifies that valid config files are parsed correctly, and that
    invalid or missing files raise the expected exceptions.
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

    @pytest.mark.parametrize("override", [
        {"header_char"  : "=-"},
        {"header_char"  : ""},
        {"header_length": 1},
        {"encoding"     : "not-an-encoding"},
    ])
    def test_invalid_values_raise(self, tmp_path, override):
        """Invalid header settings and unknown encodings are rejected on load."""
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
        A YAML file that has no "logger" key should raise a KeyError when
        Logger tries to extract data[configuration_section].
        """
        bad_config_path = tmp_path / "bad_config.yaml"
        with open(bad_config_path, "w") as f:
            yaml.safe_dump({"not_logger": {}}, f)
        with pytest.raises(KeyError):
            Logger(config=str(bad_config_path))


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


# --------------------------------------------------------------------------
# printHeader()
# --------------------------------------------------------------------------

class TestPrintHeader:
    """
    Tests for Logger.printHeader() — line count, border characters,
    centered body text, and the integer length returned.
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


# --------------------------------------------------------------------------
# File-processing helper methods
# --------------------------------------------------------------------------

class TestFileProcessingHelpers:
    """
    Tests for the file-processing helper methods:
    printFileProcessingStart/End, printReadFileStart/End,
    printWriteFileStart/End.

    Verifies that only the basename of the path is logged (not the full
    directory), and that the return value equals the message length.
    """

    def test_print_file_processing_start(self, logger_instance):
        logger, log_folder = logger_instance
        ret = logger.printFileProcessingStart("/path/to/input_data.csv")
        lines = read_log_lines(log_folder)
        assert "Processing file 'input_data.csv'." in lines[0]
        assert ret == len("Processing file 'input_data.csv'...")

    def test_print_file_processing_end(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printFileProcessingEnd("/path/to/input_data.csv")
        lines = read_log_lines(log_folder)
        assert "Processing file 'input_data.csv' completed." in lines[0]

    def test_print_read_file_start(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printReadFileStart("data.csv")
        lines = read_log_lines(log_folder)
        assert "Reading file 'data.csv'." in lines[0]

    def test_print_read_file_end(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printReadFileEnd("data.csv")
        lines = read_log_lines(log_folder)
        assert "Reading file 'data.csv' completed." in lines[0]

    def test_print_write_file_start(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printWriteFileStart("data.csv")
        lines = read_log_lines(log_folder)
        assert "Writing file 'data.csv'." in lines[0]

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


# --------------------------------------------------------------------------
# printDataFrameRowCount()
# --------------------------------------------------------------------------

class TestPrintDataFrameRowCount:
    """
    Tests for Logger.printDataFrameRowCount() — logs the row count of a
    DataFrame, or does nothing when None is passed.
    """

    def test_logs_row_count_for_non_empty_dataframe(self, logger_instance):
        logger, log_folder = logger_instance
        df  = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        ret = logger.printDataFrameRowCount(df)
        lines = read_log_lines(log_folder)
        assert "Row count: 3" in lines[0]
        assert ret == len("Row count: 3")

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


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))