"""
Pydantic configuration model for the Logger class.

Defines and validates all settings read from the "logger" section of
the YAML configuration file. Used exclusively by Logger.__init__.
"""

from pydantic import BaseModel, ConfigDict


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
    # The directory must already exist; Logger does not create it automatically.
    folder: str         = "./data/logs/"

    # Name of the log file to write to within `folder`.
    file_name: str      = "output.log"

    # Character used to build the header/footer separator line printed by
    # printHeader(). Repeated `header_length` times for the top/bottom border.
    # Note: config/config.yaml defaults this to "-"; the model default "=" is
    # only used when no config file sets it.
    header_char: str    = "="

    # Total width (in characters) of the header/footer separator line printed
    # by printHeader(). The body line is padded/truncated to this width too.
    header_length: int  = 60

    # Whether to prepend each log message with the elapsed runtime in minutes
    # since the Logger singleton was first created. Useful for tracking how
    # long a pipeline step takes.
    # Note: config/config.yaml defaults this to True; the model default False
    # is only used when no config file sets it.
    log_runtime: bool   = False

    # strftime-style format string controlling how timestamps are rendered in
    # each log message. See Python's datetime.strftime documentation for the
    # full list of format codes.
    format: str         = "%Y-%m-%d %H:%M:%S"