"""
Pydantic configuration model for the Logger class.

Defines and validates all settings read from the "logger" section of
the YAML configuration file. Used by Logger to validate any config it is
given (YAML file, dict or LoggerConfig); also exported
from the package so that callers can validate a configuration themselves.
"""

import codecs

from datetime import datetime
from typing   import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Supported log levels, from lowest to highest severity. Logger.log() drops
# messages whose level is below the configured minimum `level`.
LogLevel  = Literal["info", "warning", "error"]

# Colors available for console output. "none" prints without color.
ColorName = Literal[
    "none", "black", "red", "green", "yellow",
    "blue", "magenta", "cyan", "white",
]

# Ways of displaying the elapsed runtime when `log_runtime` is enabled:
# "minutes" as "(3 Minutes)", "hms" as "(00:03:12)".
RuntimeFormat = Literal["minutes", "hms"]


class LevelColors(BaseModel):
    """
    Console color per log level, set in the `colors` subsection of the
    "logger" config section. Colors are only applied to console output,
    never to the log file.
    """

    # Reject unknown levels, e.g. a typo such as "warn". Values assigned
    # later (logger.config.colors.error = ...) are validated as well.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    info: ColorName    = "white"
    warning: ColorName = "yellow"
    error: ColorName   = "red"


class LoggerConfig(BaseModel):
    """
    Pydantic model defining and validating the configuration schema for the
    Logger class.

    Instances are typically created via `LoggerConfig.model_validate(data)`,
    where `data` is a dict loaded from the "logger" section of a YAML config
    file. Pydantic validates field types and enforces defaults for any fields
    not present in the supplied data.

    Note: the defaults below are conservative fallbacks. The project's own
    config/config.yaml overrides several of them (e.g. header_char, log_runtime)
    — always check that file for the values actually used at runtime.
    """

    # Reject any keys in the input data that aren't explicitly defined below.
    # This catches typos or outdated config keys early, instead of silently
    # ignoring them. validate_assignment also checks values assigned later
    # (logger.config.level = ...), which would otherwise only fail on the
    # next log() call.
    model_config        = ConfigDict(extra="forbid", validate_assignment=True)

    # Directory where the log file will be written (relative or absolute path).
    # The directory is created automatically on the first log() call, and
    # again if it is deleted during the run.
    folder: str         = "./data/logs/"

    # Name of the log file to write to within `folder`. May contain the
    # placeholder {start}, the moment the Logger singleton was first created,
    # with an optional strftime format, e.g. "run_{start:%Y%m%d_%H%M%S}.log",
    # to start a new log file per run. Checked by check_file_name below.
    file_name: str      = "output.log"

    # Character used to build the header/footer separator line printed by
    # printHeader(). Repeated `header_length` times for the top/bottom border.
    # Must be exactly one character, otherwise borders and body would differ
    # in width.
    # Note: config/config.yaml sets this to "-"; the model default "=" is
    # only used when the config file does not set it.
    header_char: str    = Field(default="=", min_length=1, max_length=1)

    # Total width (in characters) of the header/footer separator line printed
    # by printHeader(). The body line is padded/truncated to this width too.
    # Must be at least 2 (one header_char on each side of the body).
    header_length: int  = Field(default=60, ge=2)

    # Whether to prefix each log message (after the timestamp and level)
    # with the elapsed runtime since the Logger singleton was first created,
    # displayed as set by `runtime_format`. Useful for tracking how long a
    # pipeline step takes.
    # Note: config/config.yaml sets this to True; the model default False
    # is only used when the config file does not set it.
    log_runtime: bool   = False

    # strftime-style format string controlling how timestamps are rendered in
    # each log message. See Python's datetime.strftime documentation for the
    # full list of format codes. Checked by check_format below.
    format: str         = "%Y-%m-%d %H:%M:%S"

    # Text encoding used when writing the log file. Set explicitly so the
    # output does not depend on the system locale.
    encoding: str       = "utf-8"

    # Minimum level a message must have to be logged: "info" logs
    # everything, "warning" only warnings and errors, "error" only errors.
    level: LogLevel     = "info"

    # How the elapsed runtime is displayed when `log_runtime` is enabled:
    # "minutes" -> "(3 Minutes)", "hms" -> "(00:03:12)".
    runtime_format: RuntimeFormat = "minutes"

    # Whether log messages are also printed to stdout. False keeps all
    # output in the log file, whatever `cmdline` the individual calls pass;
    # useful for batch jobs whose stdout is saved anyway.
    console: bool       = True

    # Console color per log level (see LevelColors). Colors are only used
    # when stdout is a terminal and the NO_COLOR environment variable is not
    # set, so redirected output and log files never contain color codes.
    colors: LevelColors = Field(default_factory=LevelColors)

    @field_validator("encoding")
    @classmethod
    def check_encoding(cls, value: str) -> str:
        """
        Reject unknown encodings when the config is loaded, instead of
        failing later on the first log() call.
        """
        # Pydantic only turns ValueError into a ValidationError, so the
        # LookupError raised for unknown encodings is converted here.
        try:
            codecs.lookup(value)
        except LookupError as e:
            raise ValueError(f"Unknown encoding '{value}'") from e
        return value

    @field_validator("file_name")
    @classmethod
    def check_file_name(cls, value: str) -> str:
        """
        Reject file names with placeholders other than {start}, so that a
        typo is caught when the config is loaded instead of on the first
        log() call. Literal braces are written as {{ and }}.
        """
        try:
            name = value.format(start=datetime.now())
        except (KeyError, IndexError, ValueError, AttributeError) as e:
            raise ValueError(
                f"Invalid file_name '{value}': only the placeholder {{start}} is "
                f"supported, optionally with a strftime format such as "
                f"{{start:%Y%m%d_%H%M%S}} ({type(e).__name__}: {e})"
            ) from e
        if not name:
            raise ValueError("file_name must not be empty")
        return value

    @field_validator("format")
    @classmethod
    def check_format(cls, value: str) -> str:
        """
        Reject timestamp formats that strftime cannot use, instead of
        failing (or writing a broken timestamp) on every log() call.

        A "%" at the end, without a format code after it, is rejected on
        every platform: Linux would write it out literally, Windows would
        raise on each log() call. ("%%" is a literal percent sign.)
        """
        trailing_percents = len(value) - len(value.rstrip("%"))
        if trailing_percents % 2 == 1:
            raise ValueError(
                f"Invalid timestamp format '{value}': '%' at the end has no format code"
            )

        # strftime raises ValueError for formats the platform rejects
        # (e.g. unknown codes on Windows, or a NUL character).
        try:
            datetime.now().strftime(value)
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format '{value}': {e}") from e
        return value
