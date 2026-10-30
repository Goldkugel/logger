"""
Singleton logging utility shared by the pipeline packages (adapter,
normalizer, embedder, model, ...).

Provides the Logger class, which reads its configuration from a YAML file
(or a mapping or LoggerConfig), writes timestamped log messages to a file,
and optionally mirrors them to stdout. A singleton pattern ensures that only one Logger instance (and one
loaded config) exists per process, and that the runtime clock starts at the
moment the very first Logger() call is made.
"""

from __future__ import annotations

from contextlib     import contextmanager
from datetime       import datetime as dt
from typing         import TYPE_CHECKING, Any, Iterable, Iterator, Mapping, Union
from .LoggerConfig  import LoggerConfig
import yaml
import logging
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

# Everything Logger(config=...) accepts: a path to a YAML file, a mapping
# such as a dict (the whole configuration or just the "logger" section), or
# an already validated LoggerConfig.
ConfigSource = Union[str, "os.PathLike[str]", Mapping[str, Any], LoggerConfig]

# Levels of Python's standard logging module matching the Logger levels,
# used by captureStandardLogging().
standard_levels: dict[str, int] = {
    "info"   : logging.INFO,
    "warning": logging.WARNING,
    "error"  : logging.ERROR,
}

# Warning logged once when the console stops accepting output, e.g. because
# the program's output was piped into `head`.
console_closed_message: str = (
    "Console output is closed (broken pipe); further messages are only "
    "written to the log file."
)


class Logger:
    """
    Singleton logging utility.

    Reads its configuration from a YAML file, a dict or a LoggerConfig
    (validated via LoggerConfig) and writes timestamped log messages to a
    file, optionally mirroring them to stdout. Because it's a singleton, only one instance (and one
    loaded config) exists per process, and `start_time` is fixed at the
    moment the first instance is created.

    All public methods are thread-safe: lines written by different threads
    are never interleaved, and multi-line output (headers, tracebacks) is
    written as one block.

    Class-level attributes
    ----------------------
    config : LoggerConfig or None
        The validated configuration object shared by all callers. Set on
        first instantiation, overwritten only if Logger() is called again
        with an explicit config, and cleared by reset().
    start_time : float
        Monotonic clock value (time.monotonic()) recorded when the singleton
        is first created. Used to compute elapsed runtime in log messages;
        unaffected by system clock changes.
    start_datetime : datetime or None
        Wall-clock time recorded together with start_time. Fills the
        {start} placeholder in `file_name`, so that all lines of a run go
        to the same file.
    _instance : Logger or None
        The single shared instance, managed by __new__.
    _lock : threading.RLock
        Lock guarding singleton creation, configuration loading and writing.
        Replaced by a fresh lock in forked child processes.
    _console_closed : bool
        Set once stdout has stopped accepting output (broken pipe); console
        output is skipped from then on.
    _standard_handler, _standard_targets
        The handler installed by captureStandardLogging() and the standard
        loggers it was added to; removed again by releaseStandardLogging().
    """

    # Class-level attributes are intentional here: because Logger is a
    # singleton, config and start_time must be shared across all references
    # to the same instance. start_time is set once in __new__; config is set
    # in __init__ on the first call and replaced whenever a config is passed
    # explicitly. Both are always assigned on the class, never on the
    # instance.
    config    : LoggerConfig | None = None
    start_time: float               = 0.0
    start_datetime: dt | None       = None
    _instance : Logger | None       = None

    # Re-entrant, because printHeader() holds the lock while calling log()
    # three times, so that the header lines stay together. (exception()
    # needs no lock: it logs message and traceback as one entry.)
    _lock     : threading.RLock     = threading.RLock()

    _console_closed: bool           = False

    # Handler installed by captureStandardLogging(), and the standard
    # loggers it was added to, each with the level it had before.
    _standard_handler: StandardLoggingHandler | None    = None
    _standard_targets: list[tuple[logging.Logger, int]] = []

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
                cls._instance       = super().__new__(cls)
                cls.start_time      = time.monotonic()
                cls.start_datetime  = dt.now()

        # On subsequent calls, return the existing instance instead of a new one.
        return cls._instance

    def __init__(self, config: ConfigSource | None = None, replace: bool = True):
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
        reconfiguration without restarting the process. Library code that
        only wants to provide a fallback passes replace=False, so that it
        never overrides the configuration of the entry point.

        When a loaded configuration is replaced by a different one, a line
        naming the changed settings is logged, so that the change is never
        silent. If the log file changes, the line is written to the old
        file as well, saying where further messages go.

        Parameters
        ----------
        config : str, os.PathLike, Mapping, LoggerConfig or None, optional
            - Path to a YAML file; its "logger" section is loaded.
            - A mapping such as a dict: either a whole configuration (the
              parsed YAML file, with a "logger" key) or just the contents
              of the "logger" section.
            - A LoggerConfig, used as is (a copy is stored).
            - None: the current configuration is kept, or
              ./config/config.yaml (relative to the working directory) is
              loaded if none has been loaded yet.
        replace : bool, optional
            Whether `config` replaces a configuration that is already
            loaded (default True). With False, `config` is only used if no
            configuration has been loaded yet.
        """
        with Logger._lock:
            # Without an explicit config, keep the configuration that is
            # already loaded; only the very first call falls back to the
            # default path.
            if config is None:
                if Logger.config is not None:
                    return
                config = standard_directory

            # A fallback configuration (replace=False) never overrides one
            # that is already loaded.
            if not replace and Logger.config is not None:
                return

            new = self._resolveConfig(config)
            old = Logger.config

            # Stored on the class so that all references to the singleton
            # share it.
            Logger.config = new

            # Report a change of an already loaded configuration, so that a
            # package reconfiguring the logger mid-run never goes unnoticed.
            if old is not None and old != new:
                self._logConfigChange(old, new)

    def _logConfigChange(self, old: LoggerConfig, new: LoggerConfig) -> None:
        """
        Log which settings changed when a configuration was replaced.

        The line is logged with the new configuration. If the log file
        changed, it is also written to the old file (file only), together
        with the path of the new one, so that a reader of the old file
        knows where the run continues.
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

        Missing fields fall back to the model defaults, also when the
        "logger" section is empty. Raises a KeyError if a YAML file is empty
        or has no "logger" section, and a ValidationError if a value is
        invalid or an unknown key is present (per the model's
        ConfigDict(extra="forbid") setting).
        """
        # Copy a given LoggerConfig so later changes by the caller do not
        # silently change the logger's configuration.
        if isinstance(config, LoggerConfig):
            return config.model_copy(deep=True)

        # A mapping is either a whole configuration (the parsed YAML file,
        # with a "logger" key) or the contents of the "logger" section.
        # "logger" is not a valid logger setting itself, so the two cannot
        # be confused. An empty section ("logger:" with no keys) means "use
        # all defaults". Mappings that are not dicts (e.g. OmegaConf configs)
        # are converted first, since pydantic only validates dicts.
        if isinstance(config, Mapping):
            config = Logger._toDict(config)
            if configuration_section in config:
                return LoggerConfig.model_validate(config[configuration_section] or {})
            return LoggerConfig.model_validate(config)

        # Otherwise it is a path: open and parse the YAML config file and
        # validate its "logger" section.
        path = os.fspath(config)
        with open(path, "r", encoding="utf-8") as f:
            # An empty file is parsed as None; treat it like a file without
            # sections so the KeyError below explains what is missing.
            data = yaml.safe_load(f) or {}

        if not isinstance(data, dict) or configuration_section not in data:
            raise KeyError(
                f"Config file '{path}' has no '{configuration_section}' section."
            )

        # An empty section falls back to the defaults, as for a dict.
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

        If another thread held the lock at the moment of the fork, the
        child's copy stays locked forever, because that thread does not
        exist in the child. Without this, the child's first log call would
        hang. Registered via os.register_at_fork below.
        """
        cls._lock = threading.RLock()

    @classmethod
    def reset(cls) -> None:
        """
        Discard the singleton, its configuration and its start time, and
        stop capturing standard logging (see releaseStandardLogging()).

        The next Logger() call creates a fresh instance, loads the
        configuration again and restarts the runtime clock. Mainly useful
        in tests and notebooks. References to the old instance share the
        class-level state, so they work again once Logger() has been called.
        """
        with cls._lock:
            cls.releaseStandardLogging()
            cls._instance       = None
            cls.config          = None
            cls.start_time      = 0.0
            cls.start_datetime  = None
            cls._console_closed = False

    @staticmethod
    def _formatDuration(seconds: float, precise: bool = False) -> str:
        """
        Format a duration in seconds as HH:MM:SS.

        With `precise`, seconds are shown with two decimals (HH:MM:SS.ss),
        so that short steps do not all show up as 00:00:00.
        """
        # Count in whole hundredths (or whole seconds) before splitting, so
        # that rounding carries over: 59.999 s is 00:01:00.00, not
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
        Format the elapsed runtime since the singleton was created, in the
        configured `runtime_format`: "3 Minutes" (or "1 Minute") or
        "00:03:12".
        """
        elapsed = time.monotonic() - self.start_time

        if config.runtime_format == "hms":
            return self._formatDuration(elapsed)

        minutes = int(elapsed // 60)
        return f"{minutes} {'Minute' if minutes == 1 else 'Minutes'}"

    @staticmethod
    def _useColors() -> bool:
        """
        Whether console output may contain color codes.

        Only true if stdout is a terminal (not redirected to a file or pipe)
        and the NO_COLOR environment variable (https://no-color.org) is not
        set to a non-empty value; as the convention says, an empty NO_COLOR
        is ignored. Some stdout replacements (e.g. in notebooks) lack
        isatty(), which is treated as "not a terminal".
        """
        isatty = getattr(sys.stdout, "isatty", None)
        return bool(isatty and isatty()) and not os.environ.get("NO_COLOR")

    def _colorize(self, message: str, level: str, config: LoggerConfig) -> str:
        """Wrap `message` in the color configured for `level`, if enabled."""
        color = ansi_colors[getattr(config.colors, level)]

        if not color or not self._useColors():
            return message

        return f"{color}{message}{ansi_reset}"

    def _currentConfig(self) -> LoggerConfig:
        """
        Return the loaded configuration, or explain why there is none.

        Each call of a logging method reads the configuration once through
        this method, so that a line is never built from two configurations
        if another thread reconfigures the logger meanwhile.

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
        """
        Path of the log file for `config`, with the {start} placeholder in
        `file_name` filled in with the start of the run.
        """
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

    def _printToConsole(self, message: str) -> bool:
        """
        Print `message` to stdout without letting the console break logging.

        Characters the console cannot encode are replaced (e.g. by "?"),
        since the log file already has the full text. If stdout is closed
        (broken pipe, e.g. when piped into `head`), console output is
        switched off for the rest of the run.

        Returns
        -------
        bool
            True if stdout has just turned out to be closed, so that the
            caller can log a warning about it.
        """
        if Logger._console_closed:
            return False

        try:
            try:
                print(message)
            except UnicodeEncodeError:
                encoding = getattr(sys.stdout, "encoding", None) or "ascii"
                print(message.encode(encoding, "replace").decode(encoding))
        except BrokenPipeError:
            Logger._console_closed = True
            return True

        return False

    @staticmethod
    def _appendToFile(path: str, message: str, encoding: str) -> None:
        """
        Append `message` as one line to the file at `path`.

        Opening in "a" (append) mode ensures that previous log entries are
        never overwritten. The encoding is set explicitly so the output does
        not depend on the system locale. The log folder is only created
        when opening the file shows that it is missing (on the first line,
        or if it was deleted during the run): checking it before every line
        made log() about a third slower (about 12 instead of 8 µs per line).
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
        Log a timestamped message to file and optionally to stdout.

        Each line has the form
            [<timestamp>] [<LEVEL>] (<runtime>) <message>
        where the runtime part is only present if `log_runtime` is enabled.
        Messages below the configured minimum `level` are dropped. The line
        is written to the file first, then (if `cmdline` and the `console`
        setting allow it) shown on the console in the
        color configured for its level. Problems with the console (closed
        pipe, characters it cannot encode) never stop the line from being
        written to the file; a closed console is reported once as a warning
        in the log file.

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

        # Silently ignore None — allows callers to pass optional values
        # without wrapping every call in a None check.
        if string is None:
            return

        # Read the configuration once, so the whole line uses the same one.
        self._logWith(self._currentConfig(), string, cmdline, level)

    def _logWith(self, config: LoggerConfig, string: str, cmdline: bool, level: str) -> None:
        """
        Write one log line using `config`; the implementation of log().

        Separate from log() so that a line can be written with a
        configuration other than the current one (see _logConfigChange())
        without changing the shared configuration in between.
        """
        # Drop messages below the configured minimum level.
        if levels[level] < levels[config.level]:
            return

        # Build the full path to the log file from the config.
        path = self._logFilePath(config)

        # Start the log line with a timestamp, formatted per config, and the
        # level tag padded to a fixed width.
        message = (
            f"[{dt.now().strftime(config.format)}] "
            f"[{level.upper():<{level_width}}] "
        )

        # Optionally add the elapsed runtime since the Logger singleton was
        # first created. Useful for tracking pipeline step durations without
        # external profiling tools.
        if config.log_runtime:
            message = f"{message}({self._formatRuntime(config)}) "

        # Append the actual log content after the prefix.
        message = f"{message}{string}"

        with Logger._lock:
            # Append the message to the log file first, so that a console
            # problem can never lose it.
            self._appendToFile(path, message, config.encoding)

            # Mirror the message to the console if requested by the call and
            # allowed by the `console` setting; only the console output is
            # colored. If the console has just closed, say so in the log
            # file (the lock is re-entrant).
            show = cmdline and config.console
            if show and self._printToConsole(self._colorize(message, level, config)):
                self._logWith(config, console_closed_message, False, "warning")

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
    def _describeError(error: BaseException) -> str:
        """
        Describe an exception on one line: "ValueError: bad row".

        Without a message, only the type is given ("KeyboardInterrupt").
        A message spanning several lines is joined with spaces, so that the
        log line keeps the common one-line structure.
        """
        text = " ".join(line.strip() for line in str(error).splitlines() if line.strip())
        name = type(error).__name__
        return f"{name}: {text}" if text else name

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
        message is logged; if there is neither, a note saying so is logged,
        so that no empty error line is written.

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

        Logs "Step 'Loading SCT' started." on entry and
        "Step 'Loading SCT' completed in 00:01:23.45." on exit. If the block
        raises, an error "Step 'Loading SCT' failed after 00:00:01.20
        (ValueError: bad row)." is logged on one line (see _describeError())
        and the exception is re-raised unchanged.

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
                f"Step '{name}' failed after {duration} ({self._describeError(e)}).",
                cmdline,
                level="error",
            )
            raise

        duration = self._formatDuration(time.monotonic() - start, precise=True)
        self.log(f"Step '{name}' completed in {duration}.", cmdline)

    def captureStandardLogging(
        self,
        level: str = "warning",
        loggers: Iterable[str] | str = ("",),
        cmdline: bool = True,
    ) -> logging.Handler:
        """
        Write messages of Python's standard logging module to this log.

        Libraries such as torch, transformers or vLLM report through the
        standard logging module, so their warnings would otherwise only
        appear on stderr and never in the log file. After this call, their
        records of at least `level` are logged here as
        "<logger name>: <message>", with the matching Logger level and the
        traceback of an exception, if any. The Logger's own minimum level
        and `console` setting apply as usual.

        Calling it again replaces the previous capture. Undo it with
        releaseStandardLogging(); Logger.reset() does so as well.

        Parameters
        ----------
        level : str, optional
            Minimum level of the captured records: "info", "warning"
            (default) or "error". If a standard logger would drop records
            of this level, its level is lowered (and restored on release).
        loggers : iterable of str or str, optional
            Names of the standard loggers to capture; the default "" is the
            root logger, which receives the records of all loggers that
            propagate to it. Add the names of loggers that do not propagate
            (e.g. "vllm"), and consider cmdline=False for them, since they
            print to the console themselves.
        cmdline : bool, optional
            Whether captured records are also printed to stdout (default
            True). Without other handlers, the standard logging module
            prints warnings to stderr only until this capture is installed,
            so they are not shown twice.

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

        # A single name is a common mistake for a list of names; iterating
        # over it would capture the loggers "v", "l", "l", "m".
        names = [loggers] if isinstance(loggers, str) else list(loggers)

        with Logger._lock:
            Logger.releaseStandardLogging()

            handler = StandardLoggingHandler(self, cmdline)
            handler.setLevel(standard_levels[level])

            for name in names:
                target = logging.getLogger(name)
                Logger._standard_targets.append((target, target.level))
                target.addHandler(handler)
                # Records below the logger's level are never created, so
                # lower it if it would hide the requested level.
                if target.getEffectiveLevel() > handler.level:
                    target.setLevel(handler.level)

            Logger._standard_handler = handler

        return handler

    @classmethod
    def releaseStandardLogging(cls) -> None:
        """
        Stop capturing standard logging (see captureStandardLogging()).

        Removes the handler from the standard loggers and restores their
        previous levels. Does nothing if nothing is captured.
        """
        with cls._lock:
            for target, previous_level in cls._standard_targets:
                target.removeHandler(cls._standard_handler)
                target.setLevel(previous_level)

            cls._standard_handler = None
            cls._standard_targets = []

    def printHeader(self, text: str = "", cmdline: bool = True) -> int:
        """
        Log a formatted header block, optionally also printing it to stdout.

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
        cmdline : bool, optional
            Whether to also print the header to stdout (default True).

        Returns
        -------
        int
            Combined character length of all three lines logged (excluding
            timestamps and newlines).
        """
        # Log the three lines in order: top border, body, bottom border.
        # Holding the lock keeps the three lines together when several
        # threads log at the same time, and keeps the configuration from
        # changing in between.
        with Logger._lock:
            config = self._currentConfig()

            # Build the top/bottom border by repeating the header character.
            head_foot = config.header_char * config.header_length

            # Build the body line: header character on each side, text
            # truncated and centered within the remaining width so the body
            # matches the border's total width.
            width = config.header_length - 2
            body  = (
                f"{config.header_char}"
                f"{text[:width].center(width)}"
                f"{config.header_char}"
            )

            self.log(head_foot, cmdline)
            self.log(body, cmdline)
            self.log(head_foot, cmdline)

        # Return the combined character length of all three lines logged
        # (always 3 * header_length, since the body is padded/truncated).
        return 2 * len(head_foot) + len(body)

    def _logFileEvent(
        self, action: str, file: str, completed: bool, cmdline: bool = True
    ) -> int:
        """
        Log "<action> file '<name>' started." or "<action> file '<name>' completed.",
        matching the wording of step().

        Shared implementation of the printFileProcessing*, printReadFile*
        and printWriteFile* helpers. Only the basename of `file` is logged —
        the directory path is stripped to keep messages concise.

        Returns
        -------
        int
            Length of the logged message string.
        """
        event   = "completed" if completed else "started"
        message = f"{action} file '{os.path.basename(file)}' {event}."
        self.log(message, cmdline)
        return len(message)

    def printFileProcessingStart(self, file: str = "", cmdline: bool = True) -> int:
        """
        Log the start of processing for a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file being processed.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).

        Returns
        -------
        int
            Length of the logged message string.
        """
        return self._logFileEvent("Processing", file, completed=False, cmdline=cmdline)

    def printFileProcessingEnd(self, file: str = "", cmdline: bool = True) -> int:
        """
        Log the completion of processing for a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file that was processed.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).

        Returns
        -------
        int
            Length of the logged message string.
        """
        return self._logFileEvent("Processing", file, completed=True, cmdline=cmdline)

    def printDataFrameRowCount(
        self, data: pd.DataFrame | None = None, cmdline: bool = True
    ) -> int:
        """
        Log the number of rows in a DataFrame.

        If `data` is None, nothing is logged and 0 is returned.

        Parameters
        ----------
        data : pd.DataFrame or None, optional
            The DataFrame whose row count to log. Any object supporting
            len() works, since only len(data) is used.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).

        Returns
        -------
        int
            Length of the logged message string, or 0 if no DataFrame
            was provided.
        """
        ret = 0

        if data is not None:
            message = f"Row count: {len(data)}."
            self.log(message, cmdline)
            ret = len(message)

        return ret

    def printReadFileStart(self, file: str = "", cmdline: bool = True) -> int:
        """
        Log the start of reading a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file being read.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).

        Returns
        -------
        int
            Length of the logged message string.
        """
        return self._logFileEvent("Reading", file, completed=False, cmdline=cmdline)

    def printWriteFileStart(self, file: str = "", cmdline: bool = True) -> int:
        """
        Log the start of writing a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file being written.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).

        Returns
        -------
        int
            Length of the logged message string.
        """
        return self._logFileEvent("Writing", file, completed=False, cmdline=cmdline)

    def printReadFileEnd(self, file: str = "", cmdline: bool = True) -> int:
        """
        Log the completion of reading a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file that was read.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).

        Returns
        -------
        int
            Length of the logged message string.
        """
        return self._logFileEvent("Reading", file, completed=True, cmdline=cmdline)

    def printWriteFileEnd(self, file: str = "", cmdline: bool = True) -> int:
        """
        Log the completion of writing a file.

        Parameters
        ----------
        file : str, optional
            Full or relative path to the file that was written.
        cmdline : bool, optional
            Whether to also print the message to stdout (default True).

        Returns
        -------
        int
            Length of the logged message string.
        """
        return self._logFileEvent("Writing", file, completed=True, cmdline=cmdline)


class StandardLoggingHandler(logging.Handler):
    """
    Handler of Python's standard logging module that writes records to a
    Logger. Installed by Logger.captureStandardLogging().
    """

    def __init__(self, logger: Logger, cmdline: bool = True):
        super().__init__()
        self._logger  = logger
        self._cmdline = cmdline

    def emit(self, record: logging.LogRecord) -> None:
        """
        Log `record` as "<logger name>: <message>" with the matching level:
        DEBUG and INFO as "info", WARNING as "warning", ERROR and CRITICAL
        as "error". The traceback of an attached exception follows on the
        next lines, as with Logger.exception().
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
        except RecursionError:
            raise
        except Exception:
            # The standard way for handlers to report their own failures
            # (prints "--- Logging error ---" to stderr) instead of raising
            # into the code that logged the record.
            self.handleError(record)


# Give forked child processes (e.g. multiprocessing workers started with
# "fork") a fresh lock, see Logger._reinitLockAfterFork(). Not available on
# Windows, which does not fork.
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=Logger._reinitLockAfterFork)
