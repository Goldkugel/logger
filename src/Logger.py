"""
Singleton logging utility shared by the pipeline packages (adapter,
normalizer, embedder, model, ...).

Provides the Logger class, which reads its configuration from a YAML file,
writes timestamped log messages to a file, and optionally mirrors them to
stdout. A singleton pattern ensures that only one Logger instance (and one
loaded config) exists per process, and that the runtime clock starts at the
moment the very first Logger() call is made.
"""

from __future__ import annotations

from datetime       import datetime as dt
from typing         import TYPE_CHECKING
from .LoggerConfig  import LoggerConfig
import yaml
import os
import time

# pandas is only needed for the type hint of printDataFrameRowCount(), which
# itself only calls len(). Importing it for type checkers only keeps pandas
# out of the runtime dependencies.
if TYPE_CHECKING:
    import pandas   as pd

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
        first instantiation and overwritten only if Logger() is called
        again with an explicit config path.
    start_time : float
        Monotonic clock value (time.monotonic()) recorded when the singleton
        is first created. Used to compute elapsed runtime in log messages;
        unaffected by system clock changes.
    _instance : Logger or None
        The single shared instance, managed by __new__.
    """

    # Class-level attributes are intentional here: because Logger is a
    # singleton, config and start_time must be shared across all references
    # to the same instance. start_time is set once in __new__; config is set
    # in __init__ on the first call and replaced whenever a config path is
    # passed explicitly. Both are always assigned on the class, never on the
    # instance.
    config    : LoggerConfig | None = None
    start_time: float               = 0.0
    _instance : Logger | None       = None

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
            cls.start_time = time.monotonic()

        # On subsequent calls, return the existing instance instead of a new one.
        return cls._instance

    def __init__(self, config: str | None = None):
        """
        Load and validate logger configuration from a YAML file.

        Note: __init__ runs every time Logger(...) is called, even though
        __new__ returns the same singleton instance. The configuration is
        therefore only (re)loaded when
          - no configuration has been loaded yet (first call), or
          - a config path is passed explicitly.
        A bare Logger() call after the first one keeps the current
        configuration, so modules can fetch the singleton without knowing
        which config file the entry point used. Passing a path explicitly
        reloads and overwrites the shared config — intentional, to allow
        reconfiguration without restarting the process.

        Parameters
        ----------
        config : str or None, optional
            Path to the YAML configuration file. If None, the current
            configuration is kept, or ./config/config.yaml (relative to the
            working directory) is loaded if none has been loaded yet.
        """
        # Without an explicit path, keep the configuration that is already
        # loaded; only the very first call falls back to the default path.
        if config is None:
            if Logger.config is not None:
                return
            config = standard_directory

        # Open and parse the YAML config file.
        with open(config, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        # Extract the "logger" section and validate it into a LoggerConfig
        # model. Missing fields fall back to the model defaults. Raises a
        # KeyError if the file has no "logger" section, and a ValidationError
        # if a value is invalid or an unknown key is present (per the model's
        # ConfigDict(extra="forbid") setting). The result is stored on the
        # class so that all references to the singleton share it.
        Logger.config = LoggerConfig.model_validate(data[configuration_section])

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
            minutes = int((time.monotonic() - self.start_time) // 60)
            message = f"{message}({minutes} Minutes) "

        # Append the actual log content after the timestamp/runtime prefix.
        message = f"{message}{string}"

        # Mirror the message to the console if requested.
        if cmdline:
            print(message)

        # Create the log folder if it does not exist yet.
        os.makedirs(self.config.folder, exist_ok=True)

        # Append the message to the log file. Opening in "a" (append) mode
        # ensures that previous log entries are never overwritten. The
        # encoding is set explicitly so the output does not depend on the
        # system locale.
        with open(str(path), mode="a", encoding=self.config.encoding) as log_file:
            log_file.write(message + "\n")

    def printHeader(self, text: str = "") -> int:
        """
        Print a formatted header block to the log and console.

        The header consists of three lines:
          1. A top border of `header_char` repeated `header_length` times.
          2. A body line: `header_char`, the given text centered within
             `header_length - 2` characters, then another `header_char`.
             Text longer than `header_length - 2` is truncated so that
             the body is always exactly as wide as the borders.
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

        # Build the body line: header character on each side, text truncated
        # and centered within the remaining width so the body matches the
        # border's total width.
        width = self.config.header_length - 2
        body  = (
            f"{self.config.header_char}"
            f"{text[:width].center(width)}"
            f"{self.config.header_char}"
        )

        # Log the three lines in order: top border, body, bottom border.
        self.log(head_foot)
        self.log(body)
        self.log(head_foot)

        # Return the combined character length of all three lines logged
        # (always 3 * header_length, since the body is padded/truncated).
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

    def printDataFrameRowCount(self, data: pd.DataFrame | None = None) -> int:
        """
        Log the number of rows in a DataFrame.

        If `data` is None, nothing is logged and 0 is returned.

        Parameters
        ----------
        data : pd.DataFrame or None, optional
            The DataFrame whose row count to log. Any object supporting
            len() works, since only len(data) is used.

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