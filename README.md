# Logger

A lightweight, singleton-based logging utility for Python projects that writes timestamped messages to a log file and (optionally) to the console. Configuration is driven by a YAML file and validated using a `LoggingConfig` model (via `LoggerModel`).

## Features

- **Singleton pattern** — only one `Logger` instance exists per process, and it tracks the time since the instance was first created (`start_time`).
- **YAML-based configuration** — reads settings such as log folder, file name, header formatting, and timestamp format from a config file.
- **Console + file logging** — every message is written to the log file, with optional mirroring to stdout.
- **Optional runtime tracking** — prepend each log entry with elapsed minutes since the logger started.
- **Formatted headers** — print a bordered header block to visually separate sections in the log.
- **Convenience helpers** — pre-built methods for common logging patterns (file processing, reading, writing, DataFrame row counts).

## Requirements

- Python 3.x
- [`pandas`](https://pandas.pydata.org/)
- [`PyYAML`](https://pyyaml.org/)
- A `LoggerModel` module defining `LoggingConfig` (a pydantic-style model with `folder`, `file_name`, `header_char`, `header_length`, `log_runtime`, and `format` fields)

## Configuration

The `Logger` expects a YAML config file (default path: `../config/config.yaml`) with a top-level `logger` section:

```yaml
logger:
  folder: "../data/logs/"
  file_name: "output.log"
  header_char: "-"
  header_length: 60
  log_runtime: True
  format: "%Y-%m-%d %H:%M:%S"
```

See the configuration file's own README for a full field reference.

## Usage

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

Because `Logger` is a singleton, calling `Logger(...)` again anywhere else in the codebase returns the same instance — no need to pass the config path more than once per process.

## API Reference

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

## Notes & Potential Gotchas

- **File handles aren't closed**: `log()` opens the log file in append mode on every call but does not explicitly close it. Depending on your Python environment, this may rely on garbage collection to release the handle — consider using a `with` block or keeping a persistent file handle if you run into file-lock or resource-leak issues.
- **Singleton caveat**: since `config` is a class attribute, all instances share the same configuration — the first call to `Logger(...)` in a process determines the config for the entire program's lifetime, even if a different path is passed later.
- **Header width**: `printHeader` centers `text` within `header_length - 2` characters, so very long header text may not fit cleanly within the configured `header_length`.

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

The AGPL-3.0 is a strong copyleft license: you are free to use, modify, and distribute this software, but if you run a modified version of it as a network-accessible service, you must make the corresponding modified source code available to users of that service. See the [`LICENSE`](./LICENSE) file for the full license text and terms.

This program is distributed in the hope that it will be useful, but **WITHOUT ANY WARRANTY**; without even the implied warranty of merchantability or fitness for a particular purpose. See the GNU Affero General Public License for more details.