"""
Test suite for the Logger class.

Run with:
    pytest test_logger.py -v

Requires:
    pytest
    pyyaml
    pandas
    (a LoggerModel module defining LoggingConfig, importable from PYTHONPATH)
"""

import os
import time
import yaml
import pytest
import pandas as pd
import sys

# Prevent Python from generating .pyc files (compiled bytecode files)
sys.dont_write_bytecode = True

from .Logger import Logger


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton():
    """
    Logger is a singleton (`_instance` is a class attribute), so state leaks
    between tests unless we reset it before and after every test.
    """
    Logger._instance = None
    Logger.config = None
    Logger.start_time = 0
    yield
    Logger._instance = None
    Logger.config = None
    Logger.start_time = 0


@pytest.fixture
def config_file(tmp_path):
    """
    Creates a temporary, valid logger config YAML file and returns its path.
    Also returns the log folder path so tests can inspect written log files.
    """
    log_folder = tmp_path / "logs"
    log_folder.mkdir()

    config_path = tmp_path / "config.yaml"
    config_data = {
        "logger": {
            "folder": str(log_folder) + os.sep,
            "file_name": "output.log",
            "header_char": "-",
            "header_length": 20,
            "log_runtime": True,
            "format": "%Y-%m-%d %H:%M:%S",
        }
    }

    with open(config_path, "w") as f:
        yaml.safe_dump(config_data, f)

    return str(config_path), str(log_folder)


@pytest.fixture
def logger_instance(config_file):
    config_path, log_folder = config_file
    logger = Logger(config=config_path)
    return logger, log_folder


def read_log_lines(log_folder, file_name="output.log"):
    path = os.path.join(log_folder, file_name)
    if not os.path.exists(path):
        return []
    with open(path, "r") as f:
        return f.readlines()


# --------------------------------------------------------------------------
# Singleton behavior
# --------------------------------------------------------------------------

class TestSingleton:

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

        assert Logger.start_time == first_start_time
        assert logger1 is logger2

    def test_config_shared_across_instances(self, config_file, tmp_path):
        """
        Because `config` is a class attribute, the second Logger(...) call
        does NOT create a new config for a new instance - it overwrites the
        shared one. This test documents that behavior.
        """
        config_path, _ = config_file
        logger1 = Logger(config=config_path)

        # Build a second, different config file
        other_folder = tmp_path / "other_logs"
        other_folder.mkdir()
        other_config_path = tmp_path / "other_config.yaml"
        with open(other_config_path, "w") as f:
            yaml.safe_dump({
                "logger": {
                    "folder": str(other_folder) + os.sep,
                    "file_name": "other.log",
                    "header_char": "=",
                    "header_length": 10,
                    "log_runtime": False,
                    "format": "%H:%M:%S",
                }
            }, f)

        logger2 = Logger(config=str(other_config_path))

        assert logger1 is logger2
        # Shared config now reflects the second call's config
        assert logger1.config.file_name == "other.log"


# --------------------------------------------------------------------------
# Configuration loading
# --------------------------------------------------------------------------

class TestConfigLoading:

    def test_loads_expected_fields(self, logger_instance):
        logger, _ = logger_instance
        assert logger.config.file_name == "output.log"
        assert logger.config.header_char == "-"
        assert logger.config.header_length == 20
        assert logger.config.log_runtime is True
        assert logger.config.format == "%Y-%m-%d %H:%M:%S"

    def test_missing_config_file_raises(self, tmp_path):
        missing_path = str(tmp_path / "does_not_exist.yaml")
        with pytest.raises(FileNotFoundError):
            Logger(config=missing_path)

    def test_missing_logger_section_raises(self, tmp_path):
        bad_config_path = tmp_path / "bad_config.yaml"
        with open(bad_config_path, "w") as f:
            yaml.safe_dump({"not_logger": {}}, f)

        with pytest.raises(KeyError):
            Logger(config=str(bad_config_path))


# --------------------------------------------------------------------------
# log()
# --------------------------------------------------------------------------

class TestLog:

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
        assert lines[0].startswith("[")
        assert "]" in lines[0]

    def test_log_includes_runtime_when_enabled(self, logger_instance):
        logger, log_folder = logger_instance
        logger.log("Runtime check", cmdline=False)

        lines = read_log_lines(log_folder)
        assert "Minutes" in lines[0]

    def test_log_excludes_runtime_when_disabled(self, config_file):
        config_path, log_folder = config_file
        # Overwrite config with log_runtime disabled
        with open(config_path, "w") as f:
            yaml.safe_dump({
                "logger": {
                    "folder": log_folder + os.sep,
                    "file_name": "output.log",
                    "header_char": "-",
                    "header_length": 20,
                    "log_runtime": False,
                    "format": "%Y-%m-%d %H:%M:%S",
                }
            }, f)

        logger = Logger(config=config_path)
        logger.log("No runtime", cmdline=False)

        lines = read_log_lines(log_folder)
        assert "Minutes" not in lines[0]
        assert "No runtime" in lines[0]

    def test_log_none_string_does_nothing(self, logger_instance):
        logger, log_folder = logger_instance
        logger.log(None, cmdline=False)

        lines = read_log_lines(log_folder)
        assert lines == []

    def test_log_empty_string_still_writes_timestamp(self, logger_instance):
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
        logger, log_folder = logger_instance
        logger.log("First", cmdline=False)
        logger.log("Second", cmdline=False)

        lines = read_log_lines(log_folder)
        assert len(lines) == 2
        assert "First" in lines[0]
        assert "Second" in lines[1]


# --------------------------------------------------------------------------
# printHeader()
# --------------------------------------------------------------------------

class TestPrintHeader:

    def test_writes_three_lines(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printHeader("Section")

        lines = read_log_lines(log_folder)
        assert len(lines) == 3

    def test_header_lines_use_header_char(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printHeader("Section")

        lines = read_log_lines(log_folder)
        header_char = logger.config.header_char
        header_length = logger.config.header_length

        # First and last lines are the head/foot border
        assert header_char * header_length in lines[0]
        assert header_char * header_length in lines[2]

    def test_body_contains_centered_text(self, logger_instance):
        logger, log_folder = logger_instance
        logger.printHeader("Title")

        lines = read_log_lines(log_folder)
        assert "Title" in lines[1]

    def test_returns_expected_length(self, logger_instance):
        logger, _ = logger_instance
        header_length = logger.config.header_length
        header_char = logger.config.header_char

        head_foot = header_char * header_length
        body = header_char + "Body".center(header_length - 2) + header_char
        expected = 2 * len(head_foot) + len(body)

        result = logger.printHeader("Body")
        assert result == expected


# --------------------------------------------------------------------------
# File-processing helper methods
# --------------------------------------------------------------------------

class TestFileProcessingHelpers:

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
        logger, log_folder = logger_instance
        logger.printFileProcessingStart("/some/nested/dir/report.xlsx")

        lines = read_log_lines(log_folder)
        assert "report.xlsx" in lines[0]
        assert "nested" not in lines[0]


# --------------------------------------------------------------------------
# printDataFrameRowCount()
# --------------------------------------------------------------------------

class TestPrintDataFrameRowCount:

    def test_logs_row_count_for_non_empty_dataframe(self, logger_instance):
        logger, log_folder = logger_instance
        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})

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