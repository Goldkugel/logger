"""
Singleton logging utility for the adapter framework.

Provides the Logger class, which reads its configuration from a YAML file,
writes timestamped log messages to a file, and optionally mirrors them to
stdout. A singleton pattern ensures that only one Logger instance (and one
loaded config) exists per process, and that the runtime clock starts at the
moment the very first Logger() call is made.
"""

from __future__ import annotations

from datetime       import datetime as dt
from .LoggerConfig  import LoggerConfig
import pandas       as pd
import yaml
import os
import time

# Key under which logger settings are expected to live in the YAML config file.
configuration_section: str = "logger"

# Default path to the config file, used if no path is explicitly passed in.
standard_directory: str    = "./config/config.yaml"


class Logger:
    """
    Singleton logging utility.

    Reads its configuration from a YAML file (validated via LoggerConfig)
    and writes timestamped log messages to a file, optionally mirroring
    them to stdout. Because it's a singleton, only one instance (and one
    loaded config) exists per process, and `start_time` is fixed at the
    moment the first instance is created.

    Class-level attributes
    ----------------------
    config : LoggerConfig or None
        The validated configuration object shared by all callers. Set on
        first instantiation and overwritten if Logger() is called again
        with a different config path.
    start_time : float
        Seconds since epoch recorded when the singleton is first created.
        Used to compute elapsed runtime in log messages.
    _instance : Logger or None
        The single shared instance, managed by __new__.
    """

    # Class-level attributes are intentional here: because Logger is a
    # singleton, config and start_time must be shared across all references
    # to the same instance. They are set once in __new__ / __init__ and
    # never duplicated per-instance.
    config    : LoggerConfig = None
    start_time: float        = 0.0
    _instance : Logger       = None

    def __new__(cls, *args, **kwargs):
        """
        Ensure only one instance of Logger is ever created (singleton pattern).

        Records the creation time on first instantiation only. On all
        subsequent calls, the existing instance is returned unchanged so
        that start_time is never reset mid-run.
        """
        if cls._instance is None:
            # No instance exists yet: create it and start the runtime clock.
            cls._instance  = super().__new__(cls)
            cls.start_time = time.time()

        # On subsequent calls, return the existing instance instead of a new one.
        return cls._instance

    def __init__(self, config: str = standard_directory):
        """
        Load and validate logger configuration from a YAML file.

        Note: __init__ runs every time Logger(...) is called, even though
        __new__ returns the same singleton instance. Re-instantiating with
        a different path will therefore reload and overwrite the shared
        config — intentional, to allow reconfiguration without restarting
        the process.

        Parameters
        ----------
        config : str, optional
            Path to the YAML configuration file. Defaults to
            ./config/config.yaml relative to the working directory.
        """
        # Open and parse the YAML config file.
        with open(config, "r") as f:
            data = yaml.safe_load(f)

        # Extract the "logger" section and validate/coerce it into a
        # LoggerConfig model. Raises ValidationError if required fields are
        # missing or invalid, or if unexpected keys are present (per the
        # model's ConfigDict(extra="forbid") setting).
        self.config = LoggerConfig.model_validate(data[configuration_section])

    def log(self, string: str = "", cmdline: bool = True) -> None:
        """
        Log a timestamped message to file and optionally to stdout.

        If `string` is None, the call is silently ignored — this allows
        callers to pass through optional values without extra None checks.
        An empty string is logged as a timestamp-only line, which is useful
        for adding blank separators to the log without a visible message.

        Parameters
        ----------
        string : str, optional
            The message to log. None is silently ignored.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).
        """
        # Silently ignore None — allows callers to pass optional values
        # without wrapping every call in a None check.
        if string is None:
            return

        # Build the full path to the log file from the config.
        path = os.path.join(self.config.folder, self.config.file_name)

        # Start the log line with a timestamp, formatted per config.
        message = f"[{dt.now().strftime(self.config.format)}] "

        # Optionally prepend elapsed runtime (in whole minutes) since the
        # Logger singleton was first created. Useful for tracking pipeline
        # step durations without external profiling tools.
        if self.config.log_runtime:
            minutes = int((time.time() - self.start_time) // 60)
            message = f"{message}({minutes} Minutes) "

        # Append the actual log content after the timestamp/runtime prefix.
        message = f"{message}{string}"

        # Mirror the message to the console if requested.
        if cmdline:
            print(message)

        # Append the message to the log file. Opening in "a" (append) mode
        # ensures that previous log entries are never overwritten.
        with open(str(path), mode="a") as log_file:
            log_file.write(message + "\n")

    def printHeader(self, text: str = "") -> int:
        """
        Print a formatted header block to the log and console.

        The header consists of three lines:
          1. A top border of `header_char` repeated `header_length` times.
          2. A body line: `header_char`, the given text centered within
             `header_length - 2` characters, then another `header_char`.
          3. A bottom border identical to the top border.

        Parameters
        ----------
        text : str, optional
            Text to display in the center of the header body.

        Returns
        -------
        int
            Combined character length of all three lines logged (excluding
            timestamps and newlines).
        """
        # Build the top/bottom border by repeating the header character.
        head_foot = self.config.header_char * self.config.header_length

        # Build the body line: header character on each side, text centered
        # within the remaining width so the body matches the border's total width.
        body = (
            f"{self.config.header_char}"
            f"{text.center(self.config.header_length - 2)}"
            f"{self.config.header_char}"
        )

        # Log the three lines in order: top border, body, bottom border.
        self.log(head_foot)
        self.log(body)
        self.log(head_foot)

        # Return the combined character length of all three lines logged,
        # which callers can use for layout alignment or progress reporting.
        return 2 * len(head_foot) + len(body)

    def printFileProcessingStart(self, file: str = "") -> int:
        """
        Log the start of processing for a file.

        Only the basename of `file` is logged — the directory path is
        stripped to keep messages concise.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file being processed.

        Returns
        -------
        int
            Length of the logged message string.
        """
        message = f"Processing file '{os.path.basename(file)}'..."
        self.log(message)
        return len(message)

    def printFileProcessingEnd(self, file: str = "") -> int:
        """
        Log the completion of processing for a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file that was processed.

        Returns
        -------
        int
            Length of the logged message string.
        """
        message = f"Processing file '{os.path.basename(file)}' completed."
        self.log(message)
        return len(message)

    def printDataFrameRowCount(self, data: pd.DataFrame = None) -> int:
        """
        Log the number of rows in a DataFrame.

        If `data` is None, nothing is logged and 0 is returned.

        Parameters
        ----------
        data : pd.DataFrame or None, optional
            The DataFrame whose row count to log.

        Returns
        -------
        int
            Length of the logged message string, or 0 if no DataFrame
            was provided.
        """
        ret = 0

        if data is not None:
            message = f"Row count: {len(data)}"
            self.log(message)
            ret = len(message)

        return ret

    def printReadFileStart(self, file: str = "") -> int:
        """
        Log the start of reading a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file being read.

        Returns
        -------
        int
            Length of the logged message string.
        """
        message = f"Reading file '{os.path.basename(file)}'..."
        self.log(message)
        return len(message)

    def printWriteFileStart(self, file: str = "") -> int:
        """
        Log the start of writing a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file being written.

        Returns
        -------
        int
            Length of the logged message string.
        """
        message = f"Writing file '{os.path.basename(file)}'..."
        self.log(message)
        return len(message)

    def printReadFileEnd(self, file: str = "") -> int:
        """
        Log the completion of reading a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file that was read.

        Returns
        -------
        int
            Length of the logged message string.
        """
        message = f"Reading file '{os.path.basename(file)}' completed."
        self.log(message)
        return len(message)

    def printWriteFileEnd(self, file: str = "") -> int:
        """
        Log the completion of writing a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file that was written.

        Returns
        -------
        int
            Length of the logged message string.
        """
        message = f"Writing file '{os.path.basename(file)}' completed."
        self.log(message)
        return len(message)