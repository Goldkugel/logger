import sys

# Prevent Python from generating .pyc files (compiled bytecode files)
sys.dont_write_bytecode = True

from .Logger import Logger
from .LoggerConfig import LoggerConfig

__all__ = ["Logger", "LoggerConfig"]