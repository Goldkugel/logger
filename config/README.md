# Logger Configuration

This file defines the configuration for the application's logging system.

## Configuration Reference

| Key | Description | Example Value |
|---|---|---|
| `folder` | Directory where log files will be written, relative to the config file location. | `../data/logs/` |
| `file_name` | Name of the log output file. | `output.log` |
| `header_char` | Character used to build the visual separator/header line printed at the start of each log run. | `-` |
| `header_length` | Number of characters used for the header separator line. | `60` |
| `log_runtime` | Whether to log the total runtime of the process/script at the end of execution. | `True` |
| `format` | Timestamp format used for log entries, following [`strftime`](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) conventions. | `%Y-%m-%d %H:%M:%S` |

## Example

```yaml
logger:
  folder: "../data/logs/"
  file_name: "output.log"
  header_char: "-"
  header_length: 60
  log_runtime: True
  format: "%Y-%m-%d %H:%M:%S"
```

## Notes

- Ensure the `folder` path exists or that your logging setup creates it automatically, otherwise log writes may fail.
- `header_char` and `header_length` are combined to produce a separator line (e.g. `------------------------------------------------------------`) at the top of each logging session, useful for visually distinguishing separate runs in the same log file.
- If `log_runtime` is enabled, the elapsed execution time is appended to the log output — useful for performance tracking across runs.
- The `format` string controls how timestamps appear in each log line; adjust it if you need date-only, 24-hour vs. 12-hour time, or millisecond precision.