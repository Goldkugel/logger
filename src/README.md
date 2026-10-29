# Source

This directory contains the `logger` package.

| File | Description |
|---|---|
| `__init__.py` | Package entry point; exports `Logger` and `LoggerConfig`. |
| `Logger.py` | The singleton `Logger` class: loads the configuration, writes timestamped messages to the log file and (optionally) to stdout, and provides helper methods such as `printHeader()` and `printReadFileStart()`. |
| `LoggerConfig.py` | The `LoggerConfig` pydantic model that validates the `logger` section of the YAML configuration file. See [`config/README.md`](../config/README.md) for the field reference. |

The tests live in [`tests/`](../tests/) and are not part of the installed package.
