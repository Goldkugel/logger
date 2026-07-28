from datetime           import datetime as dt
from LoggerModel        import LoggingConfig
import pandas                           as pd
import yaml
import os

class Logger:

    config : LoggingConfig = None
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config: str = "../config/config.yaml"):
        with open(config, "r") as f:
            data = yaml.safe_load(f)

        self.config = LoggingConfig(**data)
        

    def log(self, string: str = "", cmdline: bool = True) -> None:
        """
        Log a timestamped message to file and optionally to stdout.
        """
        if string is not None:
            path = os.path.join(self.config.folder, self.config.file_name)
            log_file = open(file = str(path), mode = "a")

            # Prefix log message with timestamp
            message = (
                "[" + dt.now().strftime(self.config.format) + "] "
                + string
            )

            if cmdline:
                print(message)

            log_file.write(message + "\n")

    def printHeader(self, text: str = "") -> int:
        """
        Print a formatted header block to the log/console.
        """
        head_foot = self.config.header_char * self.config.header_length
        
        body =  self.config.header_char + \
                text.center(self.config.header_length - 2) + \
                self.config.header_char
        
        self.log(head_foot)
        self.log(body)
        self.log(head_foot)

        return 2 * len(head_foot) + len(body)

    def printFileProcessingStart(self, file: str = "") -> int:
        """
        Log the start of file processing.
        """
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
        ret = 0
        
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