"""
Pydantic configuration model for the Logger class.

Defines and validates all settings read from the "logger" section of
the YAML configuration file. Loaded by Logger.__init__; also exported
from the package so that callers can validate a configuration themselves.
"""

import codecs

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    # ignoring them.
    model_config        = ConfigDict(extra="forbid")

    # Directory where the log file will be written (relative or absolute path).
    # The directory is created automatically on the first log() call.
    folder: str         = "./data/logs/"

    # Name of the log file to write to within `folder`.
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

    # Whether to prefix each log message (after the timestamp) with the
    # elapsed runtime in whole minutes since the Logger singleton was first
    # created. Useful for tracking how
    # long a pipeline step takes.
    # Note: config/config.yaml sets this to True; the model default False
    # is only used when the config file does not set it.
    log_runtime: bool   = False

    # strftime-style format string controlling how timestamps are rendered in
    # each log message. See Python's datetime.strftime documentation for the
    # full list of format codes.
    format: str         = "%Y-%m-%d %H:%M:%S"

    # Text encoding used when writing the log file. Set explicitly so the
    # output does not depend on the system locale.
    encoding: str       = "utf-8"

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