from datetime           import datetime         as dt
from LoggerModel        import LoggingConfig
import pandas                                   as pd
import yaml
import os
import time
import sys

# Prevent Python from generating .pyc files (compiled bytecode files)
sys.dont_write_bytecode     = True

# Key under which logger settings are expected to live in the YAML config file.
configuration_section: str  = "logger"

# Default path to the config file, used if no path is explicitly passed in.
standard_directory: str     = "../config/config.yaml"

class Logger:
    """
    Singleton logging utility.

    Reads its configuration from a YAML file (validated via LoggingConfig)
    and writes timestamped log messages to a file, optionally mirroring
    them to stdout. Because it's a singleton, only one instance (and one
    loaded config) exists per process, and `start_time` is fixed at the
    moment the first instance is created.
    """

    # Validated configuration object (folder, file name, header settings, etc.)
    # Class-level attribute: shared by all "instances" since this is a singleton.
    config: LoggingConfig = None

    # Timestamp (seconds since epoch) recorded when the singleton is first created.
    # Used later to compute elapsed runtime for log messages.
    start_time = 0

    # Holds the single shared instance of this class.
    _instance = None

    def __new__(cls, *args, **kwargs):
        """
        Ensure only one instance of Logger is ever created (singleton pattern).
        Records the creation time on first instantiation only.
        """
        if cls._instance is None:
            # No instance exists yet: create it and start the runtime clock.
            cls._instance = super().__new__(cls)
            cls.start_time = time.time()
        # On subsequent calls, return the existing instance instead of a new one.
        return cls._instance

    def __init__(self, config: str = standard_directory):
        """
        Load and validate logger configuration from a YAML file.

        Note: __init__ runs every time Logger(...) is called, even though
        __new__ returns the same singleton instance - so re-instantiating
        with a different path will reload/overwrite the shared config.
        """
        # Open and parse the YAML config file.
        with open(config, "r") as f:
            data = yaml.safe_load(f)

        # Extract the "logger" section and validate/coerce it into a
        # LoggingConfig model (raises if required fields are missing/invalid,
        # or if unexpected keys are present, per the model's config).
        self.config = LoggingConfig.model_validate(data[configuration_section])

    def log(self, string: str = "", cmdline: bool = True) -> None:
        """
        Log a timestamped message to file and optionally to stdout.
        """
        # Skip logging entirely if no message was given.
        if string is not None:
            # Build the full path to the log file from the config.
            path = os.path.join(self.config.folder, self.config.file_name)
            # Open in append mode so previous log entries are preserved.
            log_file = open(file=str(path), mode="a")

            # Prefix log message with timestamp, formatted per config.
            message = "[" + dt.now().strftime(self.config.format) + "] "

            # Optionally prepend elapsed runtime (in whole minutes) since
            # the Logger singleton was first created.
            if self.config.log_runtime:
                minutes = str(int((time.time() - self.start_time) // 60))
                message = message + "(" + minutes + " Minutes) "

            # Append the actual log content after the timestamp/runtime prefix.
            message = message + string

            # Mirror the message to the console if requested.
            if cmdline:
                print(message)

            # Write the message to the log file, followed by a newline.
            # Note: the file handle is not explicitly closed here.
            with open(file = str(path), mode = "a") as log_file:
                log_file.write(message + "\n")

    def printHeader(self, text: str = "") -> int:
        """
        Print a formatted header block to the log/console.
        """
        # Build the top/bottom border line by repeating header_char.
        head_foot = self.config.header_char * self.config.header_length

        # Build the middle line: header_char, then the given text centered
        # within (header_length - 2) characters, then another header_char,
        # so the body line matches the border's overall width.
        body = self.config.header_char + \
               text.center(self.config.header_length - 2) + \
               self.config.header_char

        # Log the three lines in order: top border, body, bottom border.
        self.log(head_foot)
        self.log(body)
        self.log(head_foot)

        # Return the combined character length of all three lines logged.
        return 2 * len(head_foot) + len(body)

    def printFileProcessingStart(self, file: str = "") -> int:
        """
        Log the start of file processing.
        """
        # Use only the file's base name (strip directory path) in the message.
        message = "Processing file '" + os.path.basename(file) + "'."
        self.log(message)
        return len(message)

    def printFileProcessingEnd(self, file: str = "") -> int:
        """
        Log completion of file processing.
        """
        message = "Processing file '" + os.path.basename(file) + "' completed."
        self.log(message)
        return len(message)

    def printDataFrameRowCount(self, data: pd.DataFrame = None) -> int:
        """
        Log the number of rows in a DataFrame.
        """
        # Default return value if no DataFrame is provided.
        ret = 0

        # Only attempt to log a row count if a DataFrame was actually passed.
        if data is not None:
            message = "Row count: " + str(len(data.index))
            self.log(message)
            ret = len(message)

        return ret

    def printReadFileStart(self, file: str = "") -> int:
        """
        Log file read start.
        """
        message = "Reading file '" + os.path.basename(file) + "'."
        self.log(message)
        return len(message)

    def printWriteFileStart(self, file: str = "") -> int:
        """
        Log file write start.
        """
        message = "Writing file '" + os.path.basename(file) + "'."
        self.log(message)
        return len(message)

    def printReadFileEnd(self, file: str = "") -> int:
        """
        Log file read completion.
        """
        message = "Reading file '" + os.path.basename(file) + "' completed."
        self.log(message)
        return len(message)

    def printWriteFileEnd(self, file: str = "") -> int:
        """
        Log file write completion.
        """
        message = "Writing file '" + os.path.basename(file) + "' completed."
        self.log(message)
        return len(message)