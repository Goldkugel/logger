from pydantic import BaseModel

class LoggingConfig(BaseModel):
    folder: str = "../data/logs/"
    file_name: str = "output.log"
    header_char: str = "="
    log_runtime: bool = False
    header_length: int = 60
    format: str = "%Y-%m-%d %H:%M:%S"