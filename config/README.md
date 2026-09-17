# Logger Configuration

All logger settings are stored in `config.yaml` under the top-level `logger`
section and map directly to the `LoggerConfig` Pydantic model. Fields not
explicitly set fall back to the defaults defined in `LoggerConfig`.

---

## Configuration Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `folder` | string | `"./data/logs/"` | Directory where the log file will be written, relative to the working directory at runtime. Must already exist — Logger does not create it automatically. |
| `file_name` | string | `"output.log"` | Name of the log file to write to within `folder`. |
| `header_char` | string | `"="` | Character used to build the border and body lines of a header block printed by `printHeader()`. Repeated `header_length` times for the top and bottom border. |
| `header_length` | integer | `60` | Total width in characters of the header block printed by `printHeader()`. The body line is padded or truncated to this width too. |
| `log_runtime` | boolean | `false` | If `true`, each log message is prefixed with the elapsed time in whole minutes since the Logger singleton was first created. Useful for tracking how long individual pipeline steps take. |
| `format` | string | `"%Y-%m-%d %H:%M:%S"` | `strftime`-style format string controlling how timestamps appear in each log line. See the [Python strftime docs](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) for available format codes. |

---

## Example

```yaml
logger:
  folder: "./data/logs/"
  file_name: "output.log"
  header_char: "-"
  header_length: 60
  log_runtime: true
  format: "%Y-%m-%d %H:%M:%S"
```

---

## Notes

- **`folder`** — the path is resolved relative to the working directory at
  runtime (i.e. wherever you invoke Python from), not relative to the config
  file itself. Make sure the directory exists before running the logger.

- **`header_char` and `header_length`** — these are used together by
  `printHeader()` to produce a bordered block that can be called at any point
  during a run to visually separate sections in the log output, for example:

  ```
  ------------------------------------------------------------
  -                     Starting Process                     -
  ------------------------------------------------------------
  ```

- **`log_runtime`** — when enabled, every log line is prefixed with the number
  of elapsed minutes since the Logger singleton was first instantiated, e.g.
  `[2026-09-17 10:32:01] (3 Minutes) Loading completed.` This is useful for
  rough performance tracking without external profiling tools.

- **`format`** — adjust the timestamp format to suit your needs, for example
  `"%H:%M:%S"` for time-only, or `"%Y-%m-%dT%H:%M:%S"` for ISO 8601.