# Logger Project

A lightweight, singleton-based logging utility for Python projects. It writes timestamped messages to a log file (and optionally to stdout), with configuration validated through a `pydantic` model and loaded from a YAML file.

## Project Structure


├── config/
│ └── config.yaml # Logger configuration (see config README)
├── Logger.py # Main Logger class
├── LoggerModel.py # LoggingConfig pydantic model
├── LoggerTest.py # Test suite (pytest)
└── README.md # This file

## Requirements

- Python 3.x
- [`pydantic`](https://docs.pydantic.dev/)
- [`PyYAML`](https://pyyaml.org/)
- [`pandas`](https://pandas.pydata.org/)
- [`pytest`](https://docs.pytest.org/) (for running tests)

---

## `LoggerModel.py` — `LoggingConfig`

A `pydantic.BaseModel` that defines and validates the logger's configuration schema. Used internally by `Logger` to parse the `"logger"` section of a YAML config file.

### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `folder` | `str` | `"../data/logs/"` | Directory where the log file is written. |
| `file_name` | `str` | `"output.log"` | Name of the log output file. |
| `header_char` | `str` | `"="` | Character used to build header/footer separator lines. |
| `log_runtime` | `bool` | `False` | Whether to prefix log messages with elapsed runtime in minutes. |
| `header_length` | `int` | `60` | Total width (in characters) of the header/footer separator line. |
| `format` | `str` | `"%Y-%m-%d %H:%M:%S"` | [`strftime`](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes)-style timestamp format for log entries. |

### Notes

- `model_config = ConfigDict(extra="forbid")` — any unrecognized keys in the input data will cause validation to fail. This helps catch typos or outdated keys in the config file early.
- All fields have defaults, so `LoggingConfig()` is valid on its own — but in practice, values are supplied via `LoggingConfig.model_validate(data)`, where `data` comes from the loaded YAML.

---

## `Logger.py` — `Logger`

The main logging class. Implements the singleton pattern so that only one `Logger` instance (and one loaded configuration) exists per process.

### Key Behaviors

- **Singleton**: The first call to `Logger(...)` creates the instance and records `start_time`. Every subsequent call to `Logger(...)` returns the *same* instance — but note that `__init__` still re-runs each time, so calling `Logger(config=...)` again with a different path will reload and overwrite the shared `config`.
- **Configuration loading**: Reads a YAML file, extracts the `logger` section (key name set by `configuration_section`), and validates it into a `LoggingConfig`.
- **File + console logging**: `log()` writes every message to the configured log file in append mode, and optionally prints it to stdout.
- **Runtime tracking**: If `log_runtime` is enabled, each message is prefixed with the elapsed minutes since the Logger was first instantiated.
- **Formatted headers**: `printHeader()` builds a three-line bordered block using `header_char` and `header_length`.
- **Convenience helpers**: Pre-built methods for logging common events — file processing start/end, file read/write start/end, and DataFrame row counts.

### Usage

```python
from Logger import Logger

# Initialize (loads config from the default or specified path)
logger = Logger(config="../config/config.yaml")

# Print a formatted header
logger.printHeader("Starting Process")

# Log a simple message
logger.log("Doing some work...")

# Log file processing lifecycle
logger.printFileProcessingStart("input_data.csv")
# ... processing ...
logger.printFileProcessingEnd("input_data.csv")

# Log DataFrame details
import pandas as pd
df = pd.read_csv("input_data.csv")
logger.printDataFrameRowCount(df)

# Log read/write operations
logger.printReadFileStart("input_data.csv")
logger.printReadFileEnd("input_data.csv")
logger.printWriteFileStart("output_data.csv")
logger.printWriteFileEnd("output_data.csv")
```

### API Reference

| Method | Description | Returns |
|---|---|---|
| `log(string, cmdline=True)` | Writes a timestamped message to the log file; optionally prints to console. | `None` |
| `printHeader(text)` | Logs a bordered header block containing `text`. | `int` — combined length of header lines |
| `printFileProcessingStart(file)` | Logs the start of processing for `file`. | `int` — message length |
| `printFileProcessingEnd(file)` | Logs completion of processing for `file`. | `int` — message length |
| `printDataFrameRowCount(data)` | Logs the row count of a `pandas.DataFrame`. | `int` — message length, or `0` if `data` is `None` |
| `printReadFileStart(file)` | Logs the start of reading `file`. | `int` — message length |
| `printReadFileEnd(file)` | Logs completion of reading `file`. | `int` — message length |
| `printWriteFileStart(file)` | Logs the start of writing `file`. | `int` — message length |
| `printWriteFileEnd(file)` | Logs completion of writing `file`. | `int` — message length |

### Known Gotchas

- **File handles aren't closed**: `log()` opens the log file on every call but never explicitly closes it — may cause resource-leak or file-lock issues on some systems (notably Windows).
- **Shared config across calls**: since `config` is a class attribute, re-instantiating `Logger(...)` with a different path overwrites the configuration for *all* references to the singleton, not just the new one.
- **Header width**: `printHeader()` centers `text` within `header_length - 2` characters — long header text may not fit cleanly.

---

## `LoggerTest.py` — Test Suite

A `pytest`-based suite covering the `Logger` class. Run with:

```bash
pytest LoggerTest.py -v
```

### Coverage

| Test class | What it verifies |
|---|---|
| `TestSingleton` | Repeated `Logger(...)` calls return the same instance; `start_time` is only set once; config is shared/overwritten across calls. |
| `TestConfigLoading` | Valid config loads expected field values; missing config file and missing `logger` section both raise errors. |
| `TestLog` | Messages are written to file with correct timestamp/runtime prefixes; `None` messages are skipped; `cmdline` correctly toggles console output; multiple calls append rather than overwrite. |
| `TestPrintHeader` | Header block writes three lines; border lines use `header_char`/`header_length`; body contains centered text; returned length matches expectation. |
| `TestFileProcessingHelpers` | Each `print*Start`/`print*End` helper logs the correct message and strips directory paths via `os.path.basename`. |
| `TestPrintDataFrameRowCount` | Row count is logged correctly for populated and empty DataFrames; `None` input logs nothing and returns `0`. |

### Fixtures

- **`reset_singleton`** (autouse): Resets `Logger._instance`, `Logger.config`, and `Logger.start_time` before and after every test, since these are class-level attributes that would otherwise leak state between tests.
- **`config_file`**: Creates a temporary, valid YAML config file (and corresponding log folder) using `pytest`'s `tmp_path`.
- **`logger_instance`**: Convenience fixture that returns an initialized `Logger` plus its log folder path.

### Notes

- Tests assume `LoggingConfig.model_validate` (or the surrounding `Logger.__init__` logic) raises a `KeyError` when the `"logger"` section is missing from the YAML — adjust `test_missing_logger_section_raises` if your validation behaves differently.
- Tests write to isolated temporary directories (via `tmp_path`), so they don't touch real project log files.