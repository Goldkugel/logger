# Logger

A lightweight, singleton-based logging utility for Python projects that writes timestamped messages to a log file and (optionally) to the console. Configuration is driven by a YAML file and validated using the `LoggerConfig` pydantic model.

## Features

- **Singleton pattern** — only one `Logger` instance exists per process, and it tracks the time since the instance was first created (`start_time`).
- **YAML-based configuration** — reads settings such as log folder, file name, header formatting, and timestamp format from a config file.
- **Console + file logging** — every message is written to the log file, with optional mirroring to stdout.
- **Optional runtime tracking** — prepend each log entry with elapsed minutes since the logger started.
- **Formatted headers** — print a bordered header block to visually separate sections in the log.
- **Convenience helpers** — pre-built methods for common logging patterns (file processing, reading, writing, DataFrame row counts).

## Requirements

- Python 3.8+
- [`PyYAML`](https://pyyaml.org/)
- [`pydantic`](https://docs.pydantic.dev/) 2.x

These runtime dependencies are declared in `pyproject.toml` and are installed automatically with the package. [`pandas`](https://pandas.pydata.org/) is not required: `printDataFrameRowCount()` accepts a `pandas.DataFrame`, but only calls `len()` on it.

```bash
pip install git+https://github.com/Goldkugel/logger.git
```

## Configuration

The `Logger` expects a YAML config file (default path: `./config/config.yaml`, relative to the working directory) with a top-level `logger` section:

```yaml
logger:
  folder: "./data/logs/"
  file_name: "output.log"
  header_char: "-"
  header_length: 60
  log_runtime: True
  format: "%Y-%m-%d %H:%M:%S"
  encoding: "utf-8"
```

See [`config/README.md`](./config/README.md) for a full field reference.

## Usage

```python
from logger import Logger

# Initialize (loads config from the default or specified path)
logger = Logger(config="./config/config.yaml")

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

Because `Logger` is a singleton, calling `Logger()` again anywhere else in the codebase returns the same instance with the configuration already loaded — no need to pass the config path more than once per process:

```python
# In another module of the same process
from logger import Logger

Logger().log("Uses the configuration loaded by the entry point.")
```

## API Reference

| Method | Description | Returns |
|---|---|---|
| `log(string, cmdline=True)` | Writes a timestamped message to the log file; optionally prints to console. `None` is ignored. | `None` |
| `printHeader(text)` | Logs a bordered header block containing `text`. | `int` — combined length of header lines |
| `printFileProcessingStart(file)` | Logs the start of processing for `file`. | `int` — message length |
| `printFileProcessingEnd(file)` | Logs completion of processing for `file`. | `int` — message length |
| `printDataFrameRowCount(data)` | Logs the row count of a `pandas.DataFrame` (or any object supporting `len()`). | `int` — message length, or `0` if `data` is `None` |
| `printReadFileStart(file)` | Logs the start of reading `file`. | `int` — message length |
| `printReadFileEnd(file)` | Logs completion of reading `file`. | `int` — message length |
| `printWriteFileStart(file)` | Logs the start of writing `file`. | `int` — message length |
| `printWriteFileEnd(file)` | Logs completion of writing `file`. | `int` — message length |

## Notes & Potential Gotchas

- **Config loading**: the configuration is loaded on the first `Logger()` call, or whenever a path is passed explicitly. A bare `Logger()` call after that keeps the current configuration; `Logger(config=...)` with a path reloads it and replaces the configuration for all references to the singleton.
- **Log folder**: the folder configured in `folder` is created automatically on the first `log()` call.
- **Encoding**: the log file is written with the configured `encoding` (default UTF-8), independent of the system locale.
- **Header width**: `printHeader` centers `text` within `header_length - 2` characters; longer text is truncated so the header always has the configured width.

## Tests

The test suite in `tests/` is only meant for testing this repository and is not part of the installed package. Run it from the repository root:

```bash
pip install -e ".[test]"
pytest tests/LoggerTest.py -v
```

The tests use `./config/config.yaml` and remove the log file configured there before and after each test. The `test` extra installs `pytest` and `pandas` (used to build test DataFrames).

The tests also run automatically on GitHub Actions for every push and pull request (Python 3.8, 3.11 and 3.14 on Ubuntu 24.04); see [`.github/workflows/tests.yml`](./.github/workflows/tests.yml).

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

The AGPL-3.0 is a strong copyleft license: you are free to use, modify, and distribute this software, but if you run a modified version of it as a network-accessible service, you must make the corresponding modified source code available to users of that service. See the [`LICENSE`](./LICENSE) file for the full license text and terms.

This program is distributed in the hope that it will be useful, but **WITHOUT ANY WARRANTY**; without even the implied warranty of merchantability or fitness for a particular purpose. See the GNU Affero General Public License for more details.