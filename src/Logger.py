"""
Singleton logging utility shared by the pipeline packages (adapter,
normalizer, embedder, model, ...).

The Logger reads its configuration from a YAML file, a mapping or a
LoggerConfig, writes timestamped lines to a log file and optionally mirrors
them to the console. One instance (and one configuration) exists per
process; the runtime clock starts with the first Logger() call.
"""

from __future__ import annotations

from contextlib     import contextmanager
from datetime       import datetime as dt
from typing         import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Sized, Union
from .LoggerConfig  import LoggerConfig
import yaml
import logging
import os
import sys
import textwrap
import threading
import time
import traceback
# Not "warnings": captureStandardLogging() has a parameter of that name.
import warnings as pywarnings

# pandas is only needed for a type hint, so it stays out of the runtime
# dependencies.
if TYPE_CHECKING:
    import pandas   as pd

# Key of the logger settings in the YAML config file.
configuration_section: str = "logger"

# Default config file, used if no config is passed in.
standard_directory: str    = "./config/config.yaml"

# Severity per level; messages below the configured minimum are dropped.
levels: dict[str, int]     = {"info": 0, "warning": 1, "error": 2}

# Width of the level tag ("[INFO   ]"), so all messages start in one column.
level_width: int           = max(len(level) for level in levels)

# ANSI escape codes per ColorName of LoggerConfig; "none" adds no code.
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

ansi_reset: str            = "\033[0m"

# Everything Logger(config=...) accepts: a YAML path, a mapping (whole
# configuration or just the "logger" section) or a LoggerConfig.
ConfigSource = Union[str, "os.PathLike[str]", Mapping[str, Any], LoggerConfig]

# Standard logging levels matching the Logger levels (captureStandardLogging()).
standard_levels: dict[str, int] = {
    "info"   : logging.INFO,
    "warning": logging.WARNING,
    "error"  : logging.ERROR,
}

# Logged once per stream when the console stops accepting output, e.g.
# because the output was piped into `head` ("broken pipe") or the terminal of
# an SSH session is gone.
console_closed_message: str = (
    "Console output to {stream} is closed ({reason}); further messages "
    "for {stream} are only written to the log file."
)

# Minimum level printed to stderr instead of stdout.
stderr_level: str = "warning"


class Logger:
    """
    Singleton logging utility.

    Writes timestamped log lines to a file and optionally to the console
    (stdout, or stderr for warnings and errors). All public methods are
    thread-safe: lines of different threads never interleave, and
    multi-line output (headers, tracebacks) is written as one block.

    Class-level attributes
    ----------------------
    config : LoggerConfig or None
        The configuration shared by all callers; cleared by reset().
    start_time : float
        time.monotonic() at the creation of the singleton; the base of the
        logged runtime.
    start_datetime : datetime or None
        Wall-clock time of the same moment; fills the {start} placeholder
        in `file_name`, so all lines of a run go to the same file.
    _instance : Logger or None
        The single shared instance, managed by __new__.
    _lock : threading.RLock
        Guards singleton creation, configuration, writing and the standard
        logging capture. Replaced by a fresh lock in forked children.
    _closed_streams : set of str
        Console streams ("stdout", "stderr") that stopped accepting output
        and are skipped from then on.
    _standard_handler, _standard_targets
        The handler installed by captureStandardLogging() and the standard
        loggers it was added to (with their previous levels).
    _showwarning
        (installed, previous) warnings.showwarning functions while Python
        warnings are captured, else None.
    _excepthooks
        (installed sys hook, installed threading hook, previous sys hook,
        previous threading hook) while uncaught exceptions are captured,
        else None.
    """

    # Always assigned on the class, never on the instance, so that all
    # references to the singleton share them.
    config    : LoggerConfig | None = None
    start_time: float               = 0.0
    start_datetime: dt | None       = None
    _instance : Logger | None       = None

    # Re-entrant, because printHeader() calls log() while holding it,
    # _logWith() logs the closed-stream warning while holding it, and
    # reset() and the capture methods call the release methods.
    _lock     : threading.RLock     = threading.RLock()

    _closed_streams: set[str]       = set()

    _standard_handler: StandardLoggingHandler | None    = None
    _standard_targets: list[tuple[logging.Logger, int]] = []
    _showwarning     : tuple[Any, Any] | None           = None
    _excepthooks     : tuple[Any, Any, Any, Any] | None = None

    def __new__(cls, *args, **kwargs):
        """
        Return the single instance, creating it (and starting the runtime
        clock) on the first call only.
        """
        with cls._lock:
            if cls._instance is None:
                cls._instance       = super().__new__(cls)
                cls.start_time      = time.monotonic()
                cls.start_datetime  = dt.now()

        return cls._instance

    def __init__(self, config: ConfigSource | None = None, replace: bool = True):
        """
        Load and validate the logger configuration.

        __init__ runs on every Logger(...) call. A bare Logger() keeps the
        current configuration, so modules can fetch the singleton without
        knowing which config the entry point used. An explicit config
        replaces the current one (and the change is logged), unless
        replace=False, which lets library code provide a fallback without
        overriding the entry point's config.

        Parameters
        ----------
        config : str, os.PathLike, Mapping, LoggerConfig or None, optional
            - Path to a YAML file; its "logger" section is loaded.
            - A mapping such as a dict: either a whole configuration (with
              a "logger" key) or just the contents of the "logger" section.
            - A LoggerConfig, used as is (a copy is stored).
            - None: keep the current configuration, or load
              ./config/config.yaml (relative to the working directory) if
              none is loaded yet.
        replace : bool, optional
            Whether `config` replaces an already loaded configuration
            (default True). With False, it is only used if none is loaded.
        """
        with Logger._lock:
            if config is None:
                if Logger.config is not None:
                    return
                config = standard_directory

            if not replace and Logger.config is not None:
                return

            new = self._resolveConfig(config)
            old = Logger.config
            Logger.config = new

            if old is not None and old != new:
                self._logConfigChange(old, new)

    def _logConfigChange(self, old: LoggerConfig, new: LoggerConfig) -> None:
        """
        Log which settings changed when a configuration was replaced.

        If the log file changed, the line is also written to the old file,
        with the path of the new one, so a reader knows where the run
        continues.
        """
        old_values = old.model_dump()
        new_values = new.model_dump()
        changes    = ", ".join(
            f"{key}: {old_values[key]!r} -> {new_values[key]!r}"
            for key in new_values
            if old_values[key] != new_values[key]
        )
        note = f"Logger configuration changed ({changes})."

        new_path = self._logFilePath(new)
        if self._logFilePath(old) != new_path:
            self._logWith(old, f"{note} Further messages go to '{new_path}'.", False, "info")

        self._logWith(new, note, True, "info")

    @staticmethod
    def _resolveConfig(config: ConfigSource) -> LoggerConfig:
        """
        Turn any supported config source into a validated LoggerConfig.

        Missing fields (or an empty "logger" section) fall back to the model
        defaults. Raises a KeyError if a YAML file is empty or has no
        "logger" section, and a ValidationError for invalid values or
        unknown keys.
        """
        # Copied, so later changes by the caller do not change the logger.
        if isinstance(config, LoggerConfig):
            return config.model_copy(deep=True)

        # "logger" is not a logger setting itself, so a whole configuration
        # and a bare section cannot be confused. Mappings that are not dicts
        # (e.g. OmegaConf configs) are converted, since pydantic only
        # validates dicts.
        if isinstance(config, Mapping):
            config = Logger._toDict(config)
            if configuration_section in config:
                return LoggerConfig.model_validate(config[configuration_section] or {})
            return LoggerConfig.model_validate(config)

        path = os.fspath(config)
        with open(path, "r", encoding="utf-8") as f:
            # An empty file is parsed as None; the KeyError below explains it.
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict) or configuration_section not in data:
            raise KeyError(
                f"Config file '{path}' has no '{configuration_section}' section."
            )

        return LoggerConfig.model_validate(data[configuration_section] or {})

    @staticmethod
    def _toDict(value: Any) -> Any:
        """Recursively turn mappings (e.g. OmegaConf configs) into dicts."""
        if isinstance(value, Mapping):
            return {key: Logger._toDict(item) for key, item in value.items()}
        return value

    @classmethod
    def _reinitLockAfterFork(cls) -> None:
        """
        Give a forked child process a fresh lock.

        A lock held by another thread at the moment of the fork would stay
        locked forever in the child, and its first log call would hang.
        """
        cls._lock = threading.RLock()

    @classmethod
    def reset(cls) -> None:
        """
        Discard the singleton, its configuration and its start time, and
        stop capturing standard logging, Python warnings and uncaught
        exceptions (see releaseStandardLogging() and
        releaseUncaughtExceptions()).

        The next Logger() call starts afresh. Mainly useful in tests and
        notebooks. Old references share the class-level state, so they work
        again once Logger() has been called.
        """
        with cls._lock:
            cls.releaseStandardLogging()
            cls.releaseUncaughtExceptions()
            cls._instance       = None
            cls.config          = None
            cls.start_time      = 0.0
            cls.start_datetime  = None
            cls._closed_streams = set()

    @staticmethod
    def _formatDuration(seconds: float, precise: bool = False) -> str:
        """
        Format a duration in seconds as HH:MM:SS, or with `precise` as
        HH:MM:SS.ss, so that short steps do not all show 00:00:00.
        """
        # Round before splitting, so 59.999 s becomes 00:01:00.00, not
        # 00:00:60.00.
        unit           = 100 if precise else 1
        total          = round(seconds * 100) if precise else int(seconds)
        hours, rest    = divmod(total, 3600 * unit)
        minutes, rest  = divmod(rest, 60 * unit)

        if precise:
            secs, hundredths = divmod(rest, 100)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}.{hundredths:02d}"

        return f"{hours:02d}:{minutes:02d}:{rest:02d}"

    def _formatRuntime(self, config: LoggerConfig) -> str:
        """
        Format the runtime since the singleton was created per
        `runtime_format`: "3 Minutes" (or "1 Minute") or "00:03:12".
        """
        elapsed = time.monotonic() - self.start_time

        if config.runtime_format == "hms":
            return self._formatDuration(elapsed)

        minutes = int(elapsed // 60)
        return f"{minutes} {'Minute' if minutes == 1 else 'Minutes'}"

    @staticmethod
    def _useColors(stream: Any = None) -> bool:
        """
        Whether output to `stream` (default stdout) may contain color codes:
        only if it is a terminal and NO_COLOR (https://no-color.org) is not
        set to a non-empty value. Streams without isatty() (e.g. in
        notebooks) count as no terminal.
        """
        stream = sys.stdout if stream is None else stream
        isatty = getattr(stream, "isatty", None)
        return bool(isatty and isatty()) and not os.environ.get("NO_COLOR")

    def _colorize(self, message: str, level: str, config: LoggerConfig, stream: Any = None) -> str:
        """Wrap `message` in the color configured for `level`, if enabled for `stream`."""
        color = ansi_colors[getattr(config.colors, level)]

        if not color or not self._useColors(stream):
            return message

        return f"{color}{message}{ansi_reset}"

    def _currentConfig(self) -> LoggerConfig:
        """
        Return the loaded configuration.

        Each logging call reads it once through this method, so a line is
        never built from two configurations if another thread reconfigures
        the logger meanwhile.

        Raises
        ------
        RuntimeError
            If no configuration is loaded, i.e. a reference kept from before
            reset() is used before Logger() has been called again.
        """
        config = Logger.config
        if config is None:
            raise RuntimeError(
                "Logger has no configuration. Call Logger() or "
                "Logger(config=...) after Logger.reset() before logging."
            )
        return config

    def _logFilePath(self, config: LoggerConfig) -> str:
        """Path of the log file for `config`, with {start} filled in."""
        file_name = config.file_name.format(start=Logger.start_datetime or dt.now())
        return os.path.join(config.folder, file_name)

    def logFilePath(self) -> str:
        """
        Return the path of the file log messages are written to.

        Useful when `file_name` contains the {start} placeholder, e.g. to
        attach the log file of a run to its results.

        Raises
        ------
        RuntimeError
            If no configuration is loaded (see _currentConfig()).
        """
        return self._logFilePath(self._currentConfig())

    @staticmethod
    def _consoleStream(level: str) -> str:
        """Console stream for `level`: "stderr" for warnings and errors, else "stdout"."""
        return "stderr" if levels[level] >= levels[stderr_level] else "stdout"

    @staticmethod
    def _closedReason(error: Exception) -> str:
        """Describe why a console stream stopped accepting output."""
        if isinstance(error, BrokenPipeError):
            return "broken pipe"
        return Logger._describeError(error, width=60).rstrip(".")

    def _printToConsole(
        self, message: str, level: str, config: LoggerConfig
    ) -> list[tuple[str, str]]:
        """
        Print `message` to the console stream for `level`, without letting
        the console break logging.

        stdout is flushed before writing to stderr, so lines keep their
        order when both go to one file (`> out.txt 2>&1`, where stdout is
        buffered but stderr is not). Unencodable characters are replaced,
        since the log file has the full text. A stream that fails (a broken
        pipe, a terminal that is gone, a closed file) is skipped for the
        rest of the run.

        Returns
        -------
        list of (str, str)
            Streams that have just turned out to be closed, with the reason,
            so the caller can log a warning about each.
        """
        name   = self._consoleStream(level)
        closed = []

        if name == "stderr" and "stdout" not in Logger._closed_streams:
            try:
                if sys.stdout is not None:
                    sys.stdout.flush()
            # OSError includes BrokenPipeError; ValueError is a closed file.
            except (OSError, ValueError) as e:
                Logger._closed_streams.add("stdout")
                closed.append(("stdout", self._closedReason(e)))

        stream = getattr(sys, name)
        if stream is None or name in Logger._closed_streams:
            return closed

        try:
            # Inside the try: isatty() fails on a closed stream as well.
            message = self._colorize(message, level, config, stream)
            try:
                print(message, file=stream)
            except UnicodeEncodeError:
                encoding = getattr(stream, "encoding", None) or "ascii"
                print(message.encode(encoding, "replace").decode(encoding), file=stream)
        except (OSError, ValueError) as e:
            Logger._closed_streams.add(name)
            closed.append((name, self._closedReason(e)))

        return closed

    @staticmethod
    def _appendToFile(path: str, message: str, encoding: str) -> None:
        """
        Append `message` as one line to the file at `path`.

        The folder is only created when opening shows it is missing (first
        line, or deleted during the run): checking it before every line made
        log() about a third slower.
        """
        try:
            log_file = open(path, mode="a", encoding=encoding)
        except FileNotFoundError:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            log_file = open(path, mode="a", encoding=encoding)

        with log_file:
            log_file.write(message + "\n")

    def log(self, string: str = "", cmdline: bool = True, level: str = "info") -> None:
        """
        Log a timestamped message to file and optionally to the console.

        Each line has the form
            [<timestamp>] [<LEVEL>] (<runtime>) <message>
        where the runtime part is only present if `log_runtime` is enabled.
        A message longer than `header_length` is split over several such
        lines (see _splitLine()). Messages below the configured minimum
        `level` are dropped. The line
        is written to the file first, then (if `cmdline` and the `console`
        setting allow it) printed in the color of its level: "info" to
        stdout, "warning" and "error" to stderr. Console problems never stop
        the line from reaching the file; a closed stream is reported once
        as a warning in the log file.

        Parameters
        ----------
        string : str, optional
            The message to log. None is silently ignored, so callers can
            pass optional values without a None check; "" logs a line
            without a message (a blank separator).
        cmdline : bool, optional
            Whether to also print the message to the console (default True).
            Has no effect if the `console` setting is false.
        level : str, optional
            "info" (default), "warning" or "error".

        Raises
        ------
        ValueError
            If `level` is not one of the supported levels.
        RuntimeError
            If no configuration is loaded (see _currentConfig()).
        """
        if level not in levels:
            raise ValueError(
                f"Unknown log level '{level}', expected one of {list(levels)}"
            )

        if string is None:
            return

        self._logWith(self._currentConfig(), string, cmdline, level)

    @staticmethod
    def _splitLine(text: str, width: int) -> list[str]:
        """
        Split `text` into parts of at most `width` characters: at the last
        whitespace within the limit (which is dropped), or at `width` itself
        if there is none.
        """
        parts = []
        while len(text) > width:
            # Index of the last space or tab that keeps the part within width.
            cut = max(text.rfind(" ", 0, width + 1), text.rfind("\t", 0, width + 1))
            if cut > 0:
                parts.append(text[:cut])
                text = text[cut + 1:]
            else:
                parts.append(text[:width])
                text = text[width:]
        parts.append(text)
        return parts

    def _logWith(self, config: LoggerConfig, string: str, cmdline: bool, level: str) -> None:
        """
        Write one log entry using `config`; the implementation of log().

        The first line of `string` is split at `header_length` (see
        _splitLine()), and each part gets its own timestamp and level.
        Further lines (e.g. a traceback) follow unchanged and without a
        prefix. Separate from log() so that an entry can be written with a
        configuration other than the current one (see _logConfigChange()).
        """
        if levels[level] < levels[config.level]:
            return

        path = self._logFilePath(config)

        prefix = (
            f"[{dt.now().strftime(config.format)}] "
            f"[{level.upper():<{level_width}}] "
        )

        if config.log_runtime:
            prefix = f"{prefix}({self._formatRuntime(config)}) "

        first, newline, rest = str(string).partition("\n")
        message = "\n".join(prefix + part for part in self._splitLine(first, config.header_length))
        message = f"{message}{newline}{rest}"

        with Logger._lock:
            # File first, so that a console problem can never lose the line.
            self._appendToFile(path, message, config.encoding)

            # Report a console stream that has just closed in the log file
            # (the lock is re-entrant).
            if cmdline and config.console:
                for stream, reason in self._printToConsole(message, level, config):
                    self._logWith(
                        config, console_closed_message.format(stream=stream, reason=reason),
                        False, "warning",
                    )

    def info(self, string: str = "", cmdline: bool = True) -> None:
        """Log a message with level "info". See log()."""
        self.log(string, cmdline, level="info")

    def warning(self, string: str = "", cmdline: bool = True) -> None:
        """Log a message with level "warning". See log()."""
        self.log(string, cmdline, level="warning")

    def error(self, string: str = "", cmdline: bool = True) -> None:
        """Log a message with level "error". See log()."""
        self.log(string, cmdline, level="error")

    @staticmethod
    def _describeError(error: BaseException, width: int | None = None) -> str:
        """
        Describe an exception on one line: "ValueError: bad row", or just
        the type without a message. Multi-line messages are joined with
        spaces. With `width`, the description is cut at a word to about that
        many characters, marked with " (...)"; a parenthesis the cut leaves
        open is dropped with the rest.
        """
        text = " ".join(line.strip() for line in str(error).splitlines() if line.strip())
        name = type(error).__name__
        # 2 for ": " and 6 for " (...)". A final "." is dropped, since
        # step() adds its own.
        if width is not None and text and len(name) + 2 + len(text) > width:
            text = textwrap.shorten(text.rstrip("."), width=max(width - len(name) - 2 - 6, 10),
                                    placeholder="")
            if text.count("(") > text.count(")"):
                text = text[:text.rindex("(")].rstrip()
            text = f"{text.rstrip(' .,;:')} (...)"
        return f"{name}: {text}" if text else name

    def exception(
        self,
        string: str = "",
        exc: BaseException | None = None,
        cmdline: bool = True,
    ) -> None:
        """
        Log an error message followed by the traceback of an exception.

        Call it inside an `except` block, or pass the exception via `exc`.
        The traceback lines follow the message line without a timestamp of
        their own. Without an exception only the message is logged; without
        either, a note saying so.

        Parameters
        ----------
        string : str, optional
            Message describing what failed.
        exc : BaseException or None, optional
            The exception to log. Defaults to the exception currently being
            handled.
        cmdline : bool, optional
            Whether to also print the message to the console (default True).
        """
        if exc is None:
            exc = sys.exc_info()[1]

        # Includes chained exceptions; log() adds the final newline.
        trace = ""
        if exc is not None:
            trace = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ).rstrip("\n")

        # One multi-line entry, so no lock is needed to keep it together.
        parts = [part for part in (string, trace) if part]
        if not parts:
            parts = ["exception() was called without a message or an active exception."]
        self.log("\n".join(parts), cmdline, level="error")

    @contextmanager
    def step(self, name: str, cmdline: bool = True) -> Iterator[None]:
        """
        Context manager that logs the start, end and duration of a step.

        Usage:
            with logger.step("Loading SCT"):
                ...

        Logs "Loading SCT..." on entry and "Loading SCT took 00:01:23.45." on
        exit. If the block raises, the errors "Loading SCT failed after
        00:00:01.20." and "Reason: ValueError: bad row." (cut to about 90
        characters) are logged and the exception is re-raised.

        Parameters
        ----------
        name : str
            Name of the step, shown in the log messages.
        cmdline : bool, optional
            Whether to also print the messages to the console (default True).
        """
        self.log(f"{name}...", cmdline)
        start = time.monotonic()

        try:
            yield
        except BaseException as e:
            duration = self._formatDuration(time.monotonic() - start, precise=True)
            self.log(f"{name} failed after {duration}.", cmdline, level="error")
            self.log(f"Reason: {self._describeError(e, width=90)}.", cmdline, level="error")
            raise

        duration = self._formatDuration(time.monotonic() - start, precise=True)
        self.log(f"{name} took {duration}.", cmdline)

    @contextmanager
    def progress(
        self,
        name: str,
        total: int | None = None,
        every: int | None = None,
        seconds: float | None = 60.0,
        cmdline: bool = True,
    ) -> Iterator[Progress]:
        """
        Context manager like step() that also logs the progress of a loop.

        Usage:
            with logger.progress("Encoding terms", total=len(terms)) as p:
                for chunk in chunks:
                    ...
                    p.update(len(chunk))

        Logs "Encoding terms..." on entry, then from update() lines such as
        "Encoding terms: 2000/10000 (20.0%), elapsed 00:01:02, remaining
        ~00:04:08." (the remaining time from the average speed so far), and
        "Encoding terms took 00:05:10.20." on exit. A line is logged once
        `every` more items are done or `seconds` have passed since the last
        one, and when `total` is reached; progress not logged yet is logged
        on exit, also if the block fails (then as in step()).

        Parameters
        ----------
        name : str
            Name of the task, shown in the log messages.
        total : int or None, optional
            Number of items to process; None if unknown, which logs
            "<name>: 2000 done, elapsed 00:01:02." without a percentage.
        every : int or None, optional
            Log a line every `every` items (None: no item interval).
        seconds : float or None, optional
            Log a line at most this many seconds after the last one (default
            60; None: no time interval). update() checks it, so a line can
            only appear when update() is called.
        cmdline : bool, optional
            Whether to also print the messages to the console (default True).

        Raises
        ------
        ValueError
            If `total` is negative, or `every` or `seconds` is not positive.
        """
        if total is not None and total < 0:
            raise ValueError(f"total must not be negative, got {total}")
        if every is not None and every <= 0:
            raise ValueError(f"every must be positive, got {every}")
        if seconds is not None and seconds <= 0:
            raise ValueError(f"seconds must be positive, got {seconds}")

        with self.step(name, cmdline):
            tracker = Progress(self, name, total, every, seconds, cmdline)
            try:
                yield tracker
            finally:
                tracker._finish()

    def captureStandardLogging(
        self,
        level: str = "warning",
        loggers: Iterable[str] | str = ("",),
        cmdline: bool = True,
        warnings: bool = True,
    ) -> logging.Handler:
        """
        Write messages of Python's standard logging module to this log.

        Libraries such as torch, transformers or vLLM report through the
        standard logging module, so their warnings would otherwise never
        reach the log file. Records of at least `level` are logged as
        "<logger name>: <message>" with the matching level and traceback,
        subject to the Logger's own minimum level and `console` setting.

        Python warnings (warnings.warn(), used e.g. by numpy, pandas or
        UMAP) are captured too, as warnings "UserWarning: <message>
        (<file>:<line>)". Python shows a warning only once per code
        location by default, so a loop does not repeat it.

        Calling it again replaces the previous capture. Undo it with
        releaseStandardLogging(); Logger.reset() does so as well.

        Parameters
        ----------
        level : str, optional
            Minimum level of the captured records: "info", "warning"
            (default) or "error". A standard logger that would drop records
            of this level is lowered (and restored on release).
        loggers : iterable of str or str, optional
            Names of the standard loggers to capture; the default "" is the
            root logger, which receives the records of all propagating
            loggers. Add loggers that do not propagate (e.g. "vllm"), and
            consider cmdline=False for them, since they print themselves.
        cmdline : bool, optional
            Whether captured records are also printed to the console
            (default True). Without other handlers, the standard logging
            module stops printing warnings to stderr once this capture is
            installed, so they are not shown twice.
        warnings : bool, optional
            Whether Python warnings are captured as well (default True). Not
            with level="error", since they are warnings.

        Returns
        -------
        logging.Handler
            The installed handler.

        Raises
        ------
        ValueError
            If `level` is not one of the supported levels.
        """
        if level not in standard_levels:
            raise ValueError(
                f"Unknown log level '{level}', expected one of {list(standard_levels)}"
            )

        # Iterating over a single name would capture the loggers "v", "l", ...
        names = [loggers] if isinstance(loggers, str) else list(loggers)

        with Logger._lock:
            Logger.releaseStandardLogging()

            handler = StandardLoggingHandler(self, cmdline)
            handler.setLevel(standard_levels[level])

            for name in names:
                target = logging.getLogger(name)
                Logger._standard_targets.append((target, target.level))
                target.addHandler(handler)
                # Records below the logger's level are never created.
                if target.getEffectiveLevel() > handler.level:
                    target.setLevel(handler.level)

            Logger._standard_handler = handler

            if warnings and standard_levels[level] <= logging.WARNING:
                previous = pywarnings.showwarning

                def showwarning(message, category, filename, lineno, file=None, line=None):
                    # Same signature as warnings.showwarning().
                    try:
                        self.log(
                            f"{category.__name__}: {message} "
                            f"({os.path.basename(filename)}:{lineno})",
                            cmdline, level="warning",
                        )
                    except Exception:
                        # e.g. no configuration after reset(): show it as
                        # Python would have.
                        previous(message, category, filename, lineno, file, line)

                pywarnings.showwarning = showwarning
                Logger._showwarning    = (showwarning, previous)

        return handler

    @classmethod
    def releaseStandardLogging(cls) -> None:
        """
        Stop capturing standard logging (see captureStandardLogging()):
        remove the handler, restore the previous logger levels and the
        previous display of Python warnings. Does nothing if nothing is
        captured.
        """
        with cls._lock:
            for target, previous_level in cls._standard_targets:
                target.removeHandler(cls._standard_handler)
                target.setLevel(previous_level)

            cls._standard_handler = None
            cls._standard_targets = []

            if cls._showwarning is not None:
                installed, previous = cls._showwarning
                # Unless another library has replaced it since.
                if pywarnings.showwarning is installed:
                    pywarnings.showwarning = previous
                cls._showwarning = None

    def captureUncaughtExceptions(self, cmdline: bool = False) -> None:
        """
        Write exceptions that end the program (or a thread) to this log.

        Without it, the traceback of an exception that nothing catches only
        goes to stderr, so the log file of a crashed batch job does not say
        why it ended. The exception is logged as an error, "Run failed
        after <runtime> with an uncaught exception." or "Thread '<name>'
        failed with an uncaught exception.", followed by the traceback as
        in exception(). A KeyboardInterrupt only gives the warning "Run
        interrupted after <runtime>.". Python's own handling (the traceback
        on stderr) still follows.

        Calling it again replaces the previous capture. Undo it with
        releaseUncaughtExceptions(); Logger.reset() does so as well. A job
        killed by a signal (e.g. by Slurm at its time limit) raises no
        exception, so it is not logged.

        Parameters
        ----------
        cmdline : bool, optional
            Whether the lines are also printed to the console (default
            False, since Python prints the traceback to stderr anyway).
        """
        with Logger._lock:
            Logger.releaseUncaughtExceptions()
            previous_sys, previous_thread = sys.excepthook, threading.excepthook

            def sys_hook(exc_type, exc, tb):
                self._logUncaught(exc, None, cmdline)
                previous_sys(exc_type, exc, tb)

            def thread_hook(args):
                # The default hook ignores SystemExit; so does this one.
                if not issubclass(args.exc_type, SystemExit):
                    name = args.thread.name if args.thread is not None else "unknown"
                    self._logUncaught(args.exc_value, name, cmdline)
                previous_thread(args)

            sys.excepthook, threading.excepthook = sys_hook, thread_hook
            Logger._excepthooks = (sys_hook, thread_hook, previous_sys, previous_thread)

    def _logUncaught(self, exc: BaseException | None, thread: str | None, cmdline: bool) -> None:
        """
        Log an uncaught exception for captureUncaughtExceptions(); `thread`
        is the name of the failed thread, or None for the main program. Never
        raises, so Python's own handling always follows.
        """
        try:
            if thread is not None:
                self.exception(f"Thread '{thread}' failed with an uncaught exception.", exc, cmdline)
                return
            runtime = self._formatDuration(time.monotonic() - Logger.start_time, precise=True)
            if isinstance(exc, KeyboardInterrupt):
                self.log(f"Run interrupted after {runtime}.", cmdline, level="warning")
            else:
                self.exception(f"Run failed after {runtime} with an uncaught exception.", exc, cmdline)
        except Exception:
            # e.g. no configuration after reset(); Python still reports it.
            pass

    @classmethod
    def releaseUncaughtExceptions(cls) -> None:
        """
        Stop capturing uncaught exceptions (see captureUncaughtExceptions())
        and restore the previous hooks, unless another library has replaced
        them since. Does nothing if nothing is captured.
        """
        with cls._lock:
            if cls._excepthooks is None:
                return
            sys_hook, thread_hook, previous_sys, previous_thread = cls._excepthooks
            if sys.excepthook is sys_hook:
                sys.excepthook = previous_sys
            if threading.excepthook is thread_hook:
                threading.excepthook = previous_thread
            cls._excepthooks = None

    def printHeader(self, text: str = "", cmdline: bool = True, level: str = "info") -> int:
        """
        Log a three-line header block: a border of `header_char` repeated
        `header_length` times, the text centered between two `header_char`
        (truncated to `header_length - 2` characters, so all lines are
        equally wide), and the border again.

        Parameters
        ----------
        text : str, optional
            Text to display in the center of the header body.
        cmdline : bool, optional
            Whether to also print the header to the console (default True).
        level : str, optional
            Level of the logged lines: "info" (default), "warning" or
            "error".

        Returns
        -------
        int
            Combined length of the three lines (without timestamps and
            newlines), i.e. 3 * header_length.
        """
        # The lock keeps the three lines together and the configuration
        # unchanged in between.
        with Logger._lock:
            config = self._currentConfig()

            head_foot = config.header_char * config.header_length

            width = config.header_length - 2
            body  = (
                f"{config.header_char}"
                f"{text[:width].center(width)}"
                f"{config.header_char}"
            )

            self.log(head_foot, cmdline, level)
            self.log(body, cmdline, level)
            self.log(head_foot, cmdline, level)

        return 2 * len(head_foot) + len(body)

    def _logFileEvent(
        self,
        action: str,
        file: str | os.PathLike[str],
        completed: bool,
        cmdline: bool = True,
        level: str = "info",
    ) -> int:
        """
        Log "<action> file '<name>'..." or "<action> file '<name>' completed.",
        matching the wording of step(). Shared by the printFileProcessing*,
        printReadFile* and printWriteFile* helpers.

        Only the last component of `file` is logged (a trailing separator
        is ignored, so "out/" logs "out"). Returns the message length and
        raises a ValueError if `file` is empty.
        """
        path = os.fspath(file)
        if not path:
            raise ValueError("file must not be empty")

        name    = os.path.basename(os.path.normpath(path))
        message = f"{action} file '{name}'" + (" completed." if completed else "...")
        self.log(message, cmdline, level)
        return len(message)

    def printFileProcessingStart(
        self, file: str | os.PathLike[str], cmdline: bool = True, level: str = "info"
    ) -> int:
        """
        Log "Processing file '<name>'...".

        Parameters
        ----------
        file : str or os.PathLike
            Path to the file being processed; only its name is logged.
        cmdline, level : optional
            As in log().

        Returns
        -------
        int
            Length of the logged message.
        """
        return self._logFileEvent("Processing", file, completed=False, cmdline=cmdline, level=level)

    def printFileProcessingEnd(
        self, file: str | os.PathLike[str], cmdline: bool = True, level: str = "info"
    ) -> int:
        """
        Log "Processing file '<name>' completed.".

        Parameters and return value as in printFileProcessingStart().
        """
        return self._logFileEvent("Processing", file, completed=True, cmdline=cmdline, level=level)

    def printRowCount(
        self, data: Sized | None = None, cmdline: bool = True, level: str = "info"
    ) -> int:
        """
        Log the number of rows (items) of `data`: "Row count: 3.".

        Parameters
        ----------
        data : Sized or None, optional
            Anything supporting len(), e.g. a DataFrame, list or dict. If
            None, nothing is logged.
        cmdline, level : optional
            As in log().

        Returns
        -------
        int
            Length of the logged message, or 0 if `data` is None.
        """
        ret = 0

        if data is not None:
            message = f"Row count: {len(data)}."
            self.log(message, cmdline, level)
            ret = len(message)

        return ret

    def printDataFrameRowCount(
        self, data: pd.DataFrame | None = None, cmdline: bool = True, level: str = "info"
    ) -> int:
        """Same as printRowCount(); kept for existing callers."""
        return self.printRowCount(data, cmdline, level)

    def printReadFileStart(
        self, file: str | os.PathLike[str], cmdline: bool = True, level: str = "info"
    ) -> int:
        """
        Log "Reading file '<name>'...".

        Parameters and return value as in printFileProcessingStart().
        """
        return self._logFileEvent("Reading", file, completed=False, cmdline=cmdline, level=level)

    def printWriteFileStart(
        self, file: str | os.PathLike[str], cmdline: bool = True, level: str = "info"
    ) -> int:
        """
        Log "Writing file '<name>'...".

        Parameters and return value as in printFileProcessingStart().
        """
        return self._logFileEvent("Writing", file, completed=False, cmdline=cmdline, level=level)

    def printReadFileEnd(
        self, file: str | os.PathLike[str], cmdline: bool = True, level: str = "info"
    ) -> int:
        """
        Log "Reading file '<name>' completed.".

        Parameters and return value as in printFileProcessingStart().
        """
        return self._logFileEvent("Reading", file, completed=True, cmdline=cmdline, level=level)

    def printWriteFileEnd(
        self, file: str | os.PathLike[str], cmdline: bool = True, level: str = "info"
    ) -> int:
        """
        Log "Writing file '<name>' completed.".

        Parameters and return value as in printFileProcessingStart().
        """
        return self._logFileEvent("Writing", file, completed=True, cmdline=cmdline, level=level)


class Progress:
    """
    Progress of a task, returned by Logger.progress(). Call update() after
    each item or batch; it logs a progress line when one is due.
    Thread-safe, so worker threads can report to the same instance.
    """

    def __init__(
        self,
        logger: Logger,
        name: str,
        total: int | None,
        every: int | None,
        seconds: float | None,
        cmdline: bool,
    ):
        """Track `name` for Logger.progress(); see there for the parameters."""
        self._logger   = logger
        self._name     = name
        self._every    = every
        self._seconds  = seconds
        self._cmdline  = cmdline
        self._lock     = threading.Lock()
        self.total     = total
        # Items done so far, and when and at which count the last line was
        # logged.
        self.done      = 0
        self._start    = time.monotonic()
        self._last_time = self._start
        self._last_done = 0

    def update(self, n: int = 1) -> None:
        """Mark `n` more items as done and log a progress line if one is due."""
        with self._lock:
            previous   = self.done
            self.done += n
            now        = time.monotonic()
            due = (
                (self._every is not None and self.done // self._every > previous // self._every)
                or (self._seconds is not None and now - self._last_time >= self._seconds)
                or (self.total is not None and previous < self.total <= self.done)
            )
            if due:
                self._report(now)

    def _finish(self) -> None:
        """Log the progress not logged yet, if any; called on exit."""
        with self._lock:
            if self.done != self._last_done:
                self._report(time.monotonic())

    def _report(self, now: float) -> None:
        """
        Log "<name>: <done>/<total> (<percent>%), elapsed <time>, remaining
        ~<time>.", without the remaining time once `total` is reached and
        without total and percentage if `total` is None.
        """
        elapsed = now - self._start
        text    = f"{self._name}: "
        if self.total is None:
            text += f"{self.done} done, elapsed {Logger._formatDuration(elapsed)}"
        else:
            percent = 100.0 if self.total == 0 else 100 * self.done / self.total
            text   += f"{self.done}/{self.total} ({percent:.1f}%), elapsed {Logger._formatDuration(elapsed)}"
            if 0 < self.done < self.total:
                # From the average time per item so far.
                remaining = elapsed / self.done * (self.total - self.done)
                text += f", remaining ~{Logger._formatDuration(remaining)}"
        self._logger.log(f"{text}.", self._cmdline)
        self._last_time = now
        self._last_done = self.done


class StandardLoggingHandler(logging.Handler):
    """
    Handler of Python's standard logging module that writes records to a
    Logger. Installed by Logger.captureStandardLogging().
    """

    def __init__(self, logger: Logger, cmdline: bool = True):
        """Write records to `logger`, also to the console if `cmdline`."""
        super().__init__()
        self._logger  = logger
        self._cmdline = cmdline

    def emit(self, record: logging.LogRecord) -> None:
        """
        Log `record` as "<logger name>: <message>": DEBUG and INFO as
        "info", WARNING as "warning", ERROR and CRITICAL as "error". An
        attached exception's traceback follows, as with Logger.exception().
        """
        try:
            message = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                trace   = "".join(traceback.format_exception(*record.exc_info))
                message = f"{message}\n{trace.rstrip()}"

            if record.levelno >= logging.ERROR:
                level = "error"
            elif record.levelno >= logging.WARNING:
                level = "warning"
            else:
                level = "info"

            self._logger.log(f"{record.name}: {message}", self._cmdline, level=level)
        # Re-raised as in logging.StreamHandler.emit(): a RecursionError is
        # not a handler failure to report.
        except RecursionError:
            raise
        except Exception:
            # The standard way for handlers to report their own failures
            # ("--- Logging error ---" on stderr) instead of raising.
            self.handleError(record)


# Fresh lock for forked children (e.g. multiprocessing "fork" workers), see
# Logger._reinitLockAfterFork(). Windows does not fork.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=Logger._reinitLockAfterFork)
