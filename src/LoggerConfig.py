"""
Pydantic configuration model for the Logger class.

Validates the settings of the "logger" section of the YAML config file.
Used by Logger for any config it is given; also exported so that callers
can validate a configuration themselves.
"""

import codecs

from datetime import datetime
from typing   import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Log levels, from lowest to highest severity.
LogLevel  = Literal["info", "warning", "error"]

# Console colors; "none" prints without color.
ColorName = Literal[
    "none", "black", "red", "green", "yellow",
    "blue", "magenta", "cyan", "white",
]

# Runtime display: "minutes" as "(3 Minutes)", "hms" as "(00:03:12)".
RuntimeFormat = Literal["minutes", "hms"]


class LevelColors(BaseModel):
    """
    Console color per log level (the `colors` subsection of the "logger"
    section). Only applied to console output, never to the log file.
    """

    # Rejects unknown levels (e.g. "warn") and invalid later assignments.
    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    info: ColorName    = "white"
    warning: ColorName = "yellow"
    error: ColorName   = "red"


class LoggerConfig(BaseModel):
    """
    Pydantic model of the "logger" config section.

    Typically created via `LoggerConfig.model_validate(data)` with the
    "logger" section of a YAML file; missing fields get the defaults below.
    The project's config/config.yaml overrides some of them (header_char,
    log_runtime).
    """

    # extra="forbid" catches typos and outdated keys; validate_assignment
    # checks later assignments (logger.config.level = ...) right away
    # instead of on the next log() call.
    model_config        = ConfigDict(extra="forbid", validate_assignment=True)

    # Folder of the log file; created on the first log() call, and again if
    # it is deleted during the run.
    folder: str         = "./data/logs/"

    # Log file name within `folder`. May contain {start}, the moment the
    # singleton was created, with an optional strftime format, e.g.
    # "run_{start:%Y%m%d_%H%M%S}.log" for one log file per run.
    file_name: str      = "output.log"

    # Border character of printHeader(); exactly one character, otherwise
    # borders and body would differ in width.
    header_char: str    = Field(default="=", min_length=1, max_length=1)

    # Width of all printHeader() lines; at least one header_char on each
    # side of the body.
    header_length: int  = Field(default=60, ge=2)

    # Whether each line shows the runtime since the singleton was created
    # (after timestamp and level), as set by `runtime_format`.
    log_runtime: bool   = False

    # strftime format of the timestamps.
    format: str         = "%Y-%m-%d %H:%M:%S"

    # Encoding of the log file, so it does not depend on the system locale.
    encoding: str       = "utf-8"

    # Minimum level a message must have to be logged: "info" logs
    # everything, "warning" only warnings and errors, "error" only errors.
    level: LogLevel     = "info"

    runtime_format: RuntimeFormat = "minutes"

    # Whether lines are also printed to the console (info to stdout,
    # warnings and errors to stderr). False overrides `cmdline` of all
    # calls; useful for batch jobs whose stdout is saved anyway.
    console: bool       = True

    # Only used if the stream is a terminal and NO_COLOR is not set, so
    # redirected output never contains color codes.
    colors: LevelColors = Field(default_factory=LevelColors)

    @field_validator("encoding")
    @classmethod
    def check_encoding(cls, value: str) -> str:
        """
        Reject unknown encodings when the config is loaded, instead of
        failing later on the first log() call.
        """
        # Pydantic only turns ValueError into a ValidationError.
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

        A trailing "%" is rejected on every platform: Linux would write it
        literally, Windows would raise. ("%%" is a literal percent sign.)
        """
        trailing_percents = len(value) - len(value.rstrip("%"))
        if trailing_percents % 2 == 1:
            raise ValueError(
                f"Invalid timestamp format '{value}': '%' at the end has no format code"
            )

        # E.g. unknown codes on Windows, or a NUL character.
        try:
            datetime.now().strftime(value)
        except ValueError as e:
            raise ValueError(f"Invalid timestamp format '{value}': {e}") from e
        return value
