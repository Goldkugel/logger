from pydantic import BaseModel, ConfigDict
import sys

# Prevent Python from generating .pyc files (compiled bytecode files)
sys.dont_write_bytecode = True

class LoggerConfig(BaseModel):
    """
    Pydantic model defining and validating the configuration schema for the
    Logger class.

    Instances are typically created via `LoggingConfig.model_validate(data)`,
    where `data` is a dict loaded from the "logger" section of a YAML config
    file. Pydantic validates field types and enforces defaults for any
    fields not present in the supplied data.
    """

    # Reject any keys in the input data that aren't explicitly defined below.
    # This catches typos or outdated config keys early, instead of silently
    # ignoring them.
    model_config        = ConfigDict(extra = "forbid")

    # Directory where the log file will be written (relative or absolute path).
    folder: str         = "../data/logs/"

    # Name of the log file to write to within `folder`.
    file_name: str      = "output.log"

    # Character used to build the header/footer separator line
    # (e.g. printHeader() repeats this `header_length` times).
    header_char: str    = "="

    # Whether to prepend each log message with the elapsed runtime
    # (in minutes) since the Logger instance was first created.
    log_runtime: bool   = False

    # Total width (in characters) of the header/footer separator line
    # printed by printHeader().
    header_length: int  = 60

    # strftime-style format string controlling how timestamps are rendered
    # in each log message (see Python's datetime.strftime docs).
    format: str         = "%Y-%m-%d %H:%M:%S"