from datetime       import datetime as dt
import yaml
import LoggerModel
import os

class Logger:

    config : LoggerModel = None

    def __init__(self, config: str = "../config/config.yaml"):
        with open(config, "r") as f:
            data = yaml.safe_load(f)

        self.config = LoggerModel(**data)
        

    def log(self, string: str = "", cmdline: bool = True) -> None:
        """
        Log a timestamped message to file and optionally to stdout.
        """
        if string is not None:
            path = os.path.join(self.config.folder, self.config.file_name)
            log_file = open(file = str(path), mode = "a")

            # Prefix log message with timestamp
            string = (
                "[" + dt.now().strftime(self.config.format) + "] "
                + string
            )

            if cmdline:
                print(string)

            log_file.write(string + "\n")