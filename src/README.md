# Source

This directory contains the `logger` package.

| File | Description |
|---|---|
| `__init__.py` | Package entry point; exports `Logger`, `LoggerConfig`, `LevelColors`, `Progress` and the file helpers `lockedFile`, `makeParentFolder`, `writeAtomically` and `writeJSON`. |
| `Logger.py` | The singleton `Logger` class: loads the configuration, writes timestamped messages to the log file and (optionally) to the console (stdout, or stderr for warnings and errors), keeps console problems from losing lines, can capture Python's standard `logging` module (`StandardLoggingHandler`), Python warnings and uncaught exceptions, tracks the progress of loops (`Progress`, returned by `progress()`), and provides helper methods such as `printHeader()` and `printReadFileStart()`. |
| `LoggerConfig.py` | The `LoggerConfig` pydantic model (with `LevelColors` for the `colors` subsection) that validates the `logger` section of the YAML configuration file. See [`config/README.md`](../config/README.md) for the field reference. |
| `FileUtils.py` | File helpers for runs that share files: `lockedFile()` (an exclusive lock on `<path>.lock`), `writeAtomically()` and `writeJSON()` (writes that replace the file in one operation) and `makeParentFolder()`. |

The tests live in [`tests/`](../tests/) and are not part of the installed package.
