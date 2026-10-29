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

from contextlib     import contextmanager
from datetime       import datetime as dt
from typing         import TYPE_CHECKING, Iterator, Union
from .LoggerConfig  import LoggerConfig
import yaml
import os
import sys
import threading
import time
import traceback

# pandas is only needed for the type hint of printDataFrameRowCount(), which
# itself only calls len(). Importing it for type checkers only keeps pandas
# out of the runtime dependencies.
if TYPE_CHECKING:
    import pandas   as pd

# Key under which logger settings are expected to live in the YAML config file.
configuration_section: str = "logger"

# Default path to the config file, used if no path is explicitly passed in.
standard_directory: str    = "./config/config.yaml"

# Log levels mapped to their severity. A message is logged only if its
# severity is at least that of the configured minimum level.
levels: dict[str, int]     = {"info": 0, "warning": 1, "error": 2}

# Width of the level tag in each log line ("[INFO   ]", "[WARNING]"), so
# that messages of all levels start in the same column.
level_width: int           = max(len(level) for level in levels)

# ANSI escape codes for the console colors that can be configured per level.
ansi_colors: dict[str, str] = {
    "none"   : "",
    "black"  : "\033[30m",
    "red"    : "\033[31m",
    "green"  : "\033[32m",
    "yellow" : "\033[33m",
    "blue"   : "\033[34m",
    "magenta": "\033[35m",
    "cyan"   : "\033[36m",
    "white"  : "\033[37m",
}

# ANSI escape code that resets the console color after a colored message.
ansi_reset: str            = "\033[0m"

# Everything Logger(config=...) accepts: a path to a YAML file, the "logger"
# section as a dict, or an already validated LoggerConfig.
ConfigSource = Union[str, "os.PathLike[str]", dict, LoggerConfig]


class Logger:
    """
    Singleton logging utility.

    Reads its configuration from a YAML file (validated via LoggerConfig)
    and writes timestamped log messages to a file, optionally mirroring
    them to stdout. Because it's a singleton, only one instance (and one
    loaded config) exists per process, and `start_time` is fixed at the
    moment the first instance is created.

    All public methods are thread-safe: lines written by different threads
    are never interleaved, and multi-line output (headers, tracebacks) is
    written as one block.

    Class-level attributes
    ----------------------
    config : LoggerConfig or None
        The validated configuration object shared by all callers. Set on
        first instantiation and overwritten only if Logger() is called
        again with an explicit config.
    start_time : float
        Monotonic clock value (time.monotonic()) recorded when the singleton
        is first created. Used to compute elapsed runtime in log messages;
        unaffected by system clock changes.
    _instance : Logger or None
        The single shared instance, managed by __new__.
    _lock : threading.RLock
        Lock guarding singleton creation, configuration loading and writing.
    """

    # Class-level attributes are intentional here: because Logger is a
    # singleton, config and start_time must be shared across all references
    # to the same instance. start_time is set once in __new__; config is set
    # in __init__ on the first call and replaced whenever a config is passed
    # explicitly. Both are always assigned on the class, never on the
    # instance.
    config    : LoggerConfig | None = None
    start_time: float               = 0.0
    _instance : Logger | None       = None

    # Re-entrant, because printHeader() and exception() hold the lock while
    # calling log(), so that their lines stay together.
    _lock     : threading.RLock     = threading.RLock()

    def __new__(cls, *args, **kwargs):
        """
        Ensure only one instance of Logger is ever created (singleton pattern).

        Records the creation time on first instantiation only. On all
        subsequent calls, the existing instance is returned unchanged so
        that start_time is never reset mid-run. The lock ensures that two
        threads calling Logger() at the same time get the same instance.
        """
        with cls._lock:
            if cls._instance is None:
                # No instance exists yet: create it and start the runtime clock.
                cls._instance  = super().__new__(cls)
                cls.start_time = time.monotonic()

        # On subsequent calls, return the existing instance instead of a new one.
        return cls._instance

    def __init__(self, config: ConfigSource | None = None):
        """
        Load and validate the logger configuration.

        Note: __init__ runs every time Logger(...) is called, even though
        __new__ returns the same singleton instance. The configuration is
        therefore only (re)loaded when
          - no configuration has been loaded yet (first call), or
          - a config is passed explicitly.
        A bare Logger() call after the first one keeps the current
        configuration, so modules can fetch the singleton without knowing
        which config the entry point used. Passing a config explicitly
        reloads and overwrites the shared config — intentional, to allow
        reconfiguration without restarting the process.

        Parameters
        ----------
        config : str, os.PathLike, dict, LoggerConfig or None, optional
            - Path to a YAML file; its "logger" section is loaded.
            - A dict with the contents of the "logger" section.
            - A LoggerConfig, used as is (a copy is stored).
            - None: the current configuration is kept, or
              ./config/config.yaml (relative to the working directory) is
              loaded if none has been loaded yet.
        """
        with Logger._lock:
            # Without an explicit config, keep the configuration that is
            # already loaded; only the very first call falls back to the
            # default path.
            if config is None:
                if Logger.config is not None:
                    return
                config = standard_directory

            # Stored on the class so that all references to the singleton
            # share it.
            Logger.config = self._loadConfig(config)

    @staticmethod
    def _loadConfig(config: ConfigSource) -> LoggerConfig:
        """
        Turn any supported config source into a validated LoggerConfig.

        Missing fields fall back to the model defaults. Raises a KeyError if
        a YAML file has no "logger" section, and a ValidationError if a value
        is invalid or an unknown key is present (per the model's
        ConfigDict(extra="forbid") setting).
        """
        # Copy a given LoggerConfig so later changes by the caller do not
        # silently change the logger's configuration.
        if isinstance(config, LoggerConfig):
            return config.model_copy(deep=True)

        # A dict is taken to be the contents of the "logger" section.
        if isinstance(config, dict):
            return LoggerConfig.model_validate(config)

        # Otherwise it is a path: open and parse the YAML config file and
        # validate its "logger" section.
        with open(config, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        return LoggerConfig.model_validate(data[configuration_section])

    @classmethod
    def reset(cls) -> None:
        """
        Discard the singleton, its configuration and its start time.

        The next Logger() call creates a fresh instance, loads the
        configuration again and restarts the runtime clock. Mainly useful
        in tests and notebooks. References to the old instance share the
        class-level state, so they work again once Logger() has been called.
        """
        with cls._lock:
            cls._instance  = None
            cls.config     = None
            cls.start_time = 0.0

    @staticmethod
    def _formatDuration(seconds: float, precise: bool = False) -> str:
        """
        Format a duration in seconds as HH:MM:SS.

        With `precise`, seconds are shown with two decimals (HH:MM:SS.ss),
        so that short steps do not all show up as 00:00:00.
        """
        hours, rest    = divmod(seconds, 3600)
        minutes, secs  = divmod(rest, 60)

        if precise:
            return f"{int(hours):02d}:{int(minutes):02d}:{secs:05.2f}"

        return f"{int(hours):02d}:{int(minutes):02d}:{int(secs):02d}"

    def _formatRuntime(self) -> str:
        """
        Format the elapsed runtime since the singleton was created, in the
        configured `runtime_format`: "3 Minutes" or "00:03:12".
        """
        elapsed = time.monotonic() - self.start_time

        if self.config.runtime_format == "hms":
            return self._formatDuration(elapsed)

        return f"{int(elapsed // 60)} Minutes"

    @staticmethod
    def _useColors() -> bool:
        """
        Whether console output may contain color codes.

        Only true if stdout is a terminal (not redirected to a file or pipe)
        and the NO_COLOR environment variable (https://no-color.org) is not
        set. Some stdout replacements (e.g. in notebooks) lack isatty(),
        which is treated as "not a terminal".
        """
        isatty = getattr(sys.stdout, "isatty", None)
        return bool(isatty and isatty()) and "NO_COLOR" not in os.environ

    def _colorize(self, message: str, level: str) -> str:
        """Wrap `message` in the color configured for `level`, if enabled."""
        color = ansi_colors[getattr(self.config.colors, level)]

        if not color or not self._useColors():
            return message

        return f"{color}{message}{ansi_reset}"

    def log(self, string: str = "", cmdline: bool = True, level: str = "info") -> None:
        """
        Log a timestamped message to file and optionally to stdout.

        Each line has the form
            [<timestamp>] [<LEVEL>] (<runtime>) <message>
        where the runtime part is only present if `log_runtime` is enabled.
        Messages below the configured minimum `level` are dropped. On the
        console, the line is shown in the color configured for its level.

        If `string` is None, the call is silently ignored — this allows
        callers to pass through optional values without extra None checks.
        An empty string is logged as a line without a message, which is
        useful for adding blank separators to the log.

        Parameters
        ----------
        string : str, optional
            The message to log. None is silently ignored.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).
        level : str, optional
            "info" (default), "warning" or "error".

        Raises
        ------
        ValueError
            If `level` is not one of the supported levels.
        """
        if level not in levels:
            raise ValueError(
                f"Unknown log level '{level}', expected one of {list(levels)}"
            )

        # Silently ignore None — allows callers to pass optional values
        # without wrapping every call in a None check.
        if string is None:
            return

        # Drop messages below the configured minimum level.
        if levels[level] < levels[self.config.level]:
            return

        # Build the full path to the log file from the config.
        path = os.path.join(self.config.folder, self.config.file_name)

        # Start the log line with a timestamp, formatted per config, and the
        # level tag padded to a fixed width.
        message = (
            f"[{dt.now().strftime(self.config.format)}] "
            f"[{level.upper():<{level_width}}] "
        )

        # Optionally add the elapsed runtime since the Logger singleton was
        # first created. Useful for tracking pipeline step durations without
        # external profiling tools.
        if self.config.log_runtime:
            message = f"{message}({self._formatRuntime()}) "

        # Append the actual log content after the prefix.
        message = f"{message}{string}"

        with Logger._lock:
            # Mirror the message to the console if requested; only the
            # console output is colored.
            if cmdline:
                print(self._colorize(message, level))

            # Create the log folder if it does not exist yet.
            os.makedirs(self.config.folder, exist_ok=True)

            # Append the message to the log file. Opening in "a" (append)
            # mode ensures that previous log entries are never overwritten.
            # The encoding is set explicitly so the output does not depend
            # on the system locale.
            with open(str(path), mode="a", encoding=self.config.encoding) as log_file:
                log_file.write(message + "\n")

    def info(self, string: str = "", cmdline: bool = True) -> None:
        """Log a message with level "info". See log()."""
        self.log(string, cmdline, level="info")

    def warning(self, string: str = "", cmdline: bool = True) -> None:
        """Log a message with level "warning". See log()."""
        self.log(string, cmdline, level="warning")

    def error(self, string: str = "", cmdline: bool = True) -> None:
        """Log a message with level "error". See log()."""
        self.log(string, cmdline, level="error")

    def exception(
        self,
        string: str = "",
        exc: BaseException | None = None,
        cmdline: bool = True,
    ) -> None:
        """
        Log an error message followed by the traceback of an exception.

        Call it inside an `except` block to log the exception currently
        being handled, or pass the exception explicitly via `exc`. The
        traceback lines follow the message line in the log file, without a
        timestamp prefix of their own. If there is no exception, only the
        message is logged.

        Parameters
        ----------
        string : str, optional
            Message describing what failed.
        exc : BaseException or None, optional
            The exception to log. Defaults to the exception currently being
            handled.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).
        """
        if exc is None:
            exc = sys.exc_info()[1]

        # Format the full traceback, including chained exceptions, without
        # the trailing newline (log() adds one).
        trace = ""
        if exc is not None:
            trace = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip("\n")

        # Message and traceback form a single, multi-line log entry.
        parts = [part for part in (string, trace) if part]
        self.log("\n".join(parts), cmdline, level="error")

    @contextmanager
    def step(self, name: str, cmdline: bool = True) -> Iterator[None]:
        """
        Context manager that logs the start, end and duration of a step.

        Usage:
            with logger.step("Loading SCT"):
                ...

        Logs "Step 'Loading SCT' started." on entry and
        "Step 'Loading SCT' completed in 00:01:23.45." on exit. If the block
        raises, an error "Step 'Loading SCT' failed after ... (<error>)." is
        logged and the exception is re-raised unchanged.

        Parameters
        ----------
        name : str
            Name of the step, shown in the log messages.
        cmdline : bool, optional
            Whether to also print the messages to stdout (default True).
        """
        self.log(f"Step '{name}' started.", cmdline)
        start = time.monotonic()

        try:
            yield
        except BaseException as e:
            duration = self._formatDuration(time.monotonic() - start, precise=True)
            self.log(
                f"Step '{name}' failed after {duration} ({type(e).__name__}: {e}).",
                cmdline,
                level="error",
            )
            raise

        duration = self._formatDuration(time.monotonic() - start, precise=True)
        self.log(f"Step '{name}' completed in {duration}.", cmdline)

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
        # Holding the lock keeps the three lines together when several
        # threads log at the same time.
        with Logger._lock:
            self.log(head_foot)
            self.log(body)
            self.log(head_foot)

        # Return the combined character length of all three lines logged
        # (always 3 * header_length, since the body is padded/truncated).
        return 2 * len(head_foot) + len(body)

    def _logFileEvent(self, action: str, file: str, completed: bool) -> int:
        """
        Log "<action> file '<name>'..." or "<action> file '<name>' completed.".

        Shared implementation of the printFileProcessing*, printReadFile*
        and printWriteFile* helpers. Only the basename of `file` is logged —
        the directory path is stripped to keep messages concise.

        Returns
        -------
        int
            Length of the logged message string.
        """
        suffix  = " completed." if completed else "..."
        message = f"{action} file '{os.path.basename(file)}'{suffix}"
        self.log(message)
        return len(message)

    def printFileProcessingStart(self, file: str = "") -> int:
        """
        Log the start of processing for a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file being processed.

        Returns
        -------
        int
            Length of the logged message string.
        """
        return self._logFileEvent("Processing", file, completed=False)

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
        return self._logFileEvent("Processing", file, completed=True)

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
        return self._logFileEvent("Reading", file, completed=False)

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
        return self._logFileEvent("Writing", file, completed=False)

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
        return self._logFileEvent("Reading", file, completed=True)

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
        return self._logFileEvent("Writing", file, completed=True)
