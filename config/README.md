# Logger Configuration

All logger settings are stored in `config.yaml` under the top-level `logger`
section and map directly to the `LoggerConfig` Pydantic model. Fields not
explicitly set fall back to the defaults defined in `LoggerConfig`. Unknown
keys in the `logger` section are rejected, so typos are caught when the
configuration is loaded. Values changed later through `logger.config` are
validated the same way.

`config.yaml` in this repository only contains the `logger` section. Packages
that use the logger keep their own `config.yaml` with a `logger` section next
to their other settings; `Logger` reads only that section and ignores the rest.

---

## Configuration Reference

| Field | Type | Default | Description |
|---|---|---|---|
| `folder` | string | `"./data/logs/"` | Directory where the log file will be written, relative to the working directory at runtime. Created automatically on the first `log()` call if it does not exist. |
| `file_name` | string | `"output.log"` | Name of the log file to write to within `folder`. May contain `{start}`, the moment the run started, with an optional `strftime` format (e.g. `"run_{start:%Y%m%d_%H%M%S}.log"`) to start a new file per run; other placeholders are rejected when the config is loaded. Write literal braces as `{{` and `}}`. |
| `header_char` | string | `"="` | Exactly one character, used to build the border and body lines of a header block printed by `printHeader()`. Repeated `header_length` times for the top and bottom border. |
| `header_length` | integer | `60` | Total width in characters (at least 2) of the header block printed by `printHeader()`. The body line is padded or truncated to this width too. |
| `log_runtime` | boolean | `false` | If `true`, each log message contains the elapsed time since the Logger singleton was first created, displayed as set by `runtime_format`. Useful for tracking how long individual pipeline steps take. |
| `format` | string | `"%Y-%m-%d %H:%M:%S"` | `strftime`-style format string controlling how timestamps appear in each log line. See the [Python strftime docs](https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes) for available format codes. Formats that `strftime` cannot use, or that end in a single `%`, are rejected when the config is loaded; use `%%` for a literal percent sign. |
| `encoding` | string | `"utf-8"` | Text encoding used when writing the log file. Must be an encoding known to Python (e.g. `"utf-8"`, `"latin-1"`); unknown values are rejected when the config is loaded. |
| `level` | string | `"info"` | Minimum level a message must have to be logged: `"info"` logs everything, `"warning"` only warnings and errors, `"error"` only errors. Messages below this level are written neither to the file nor to the console. |
| `runtime_format` | string | `"minutes"` | How the elapsed runtime is displayed when `log_runtime` is enabled: `"minutes"` as `(3 Minutes)` (or `(1 Minute)`), `"hms"` as `(00:03:12)`. |
| `console` | boolean | `true` | Whether log messages are also printed to stdout. `false` keeps all output in the log file, whatever `cmdline` the individual calls pass. |
| `colors` | mapping | `info: "white"`, `warning: "yellow"`, `error: "red"` | Console color per level. Allowed colors: `none`, `black`, `red`, `green`, `yellow`, `blue`, `magenta`, `cyan`, `white`; `none` prints without color. Only the levels `info`, `warning` and `error` are allowed as keys. |

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
  encoding: "utf-8"
  level: "info"
  runtime_format: "minutes"
  console: true
  colors:
    info: "white"
    warning: "yellow"
    error: "red"
```

---

## Notes

- **`folder`** — the path is resolved relative to the working directory at
  runtime (i.e. wherever you invoke Python from), not relative to the config
  file itself. The directory is created automatically if it is missing.

- **`header_char` and `header_length`** — these are used together by
  `printHeader()` to produce a bordered block that can be called at any point
  during a run to visually separate sections in the log output, for example:

  ```
  ------------------------------------------------------------
  -                     Starting Process                     -
  ------------------------------------------------------------
  ```

- **Log line format** — every line has the form
  `[<timestamp>] [<LEVEL>] (<runtime>) <message>`, e.g.
  `[2026-09-17 10:32:01] [INFO   ] (3 Minutes) Loading completed.` The level
  tag is padded to a fixed width so messages of all levels line up. The
  runtime part is only present if `log_runtime` is enabled.

- **`log_runtime` and `runtime_format`** — the runtime counts from the moment
  the Logger singleton was first instantiated. `"minutes"` is enough for long
  pipeline runs; `"hms"` shows seconds as well. For exact timings of single
  steps, use `Logger().step(...)` instead (see the main README).

- **`level`** — set it to `"warning"` to hide routine progress messages and
  only keep warnings and errors, e.g. for long batch runs.

- **`colors`** — colors are only applied to console output, never to the log
  file. They are also left out when the output is redirected to a file or
  pipe (for example `python run.py > out.txt`, or a batch job's output file)
  and when the [`NO_COLOR`](https://no-color.org) environment variable is set
  to a non-empty value.

- **`encoding`** — set explicitly so the log output does not depend on the
  system locale; UTF-8 handles any non-ASCII text in log messages.

- **`file_name` with `{start}`** — `{start}` is the moment the Logger
  singleton was first created, so all lines of a run go to one file, also
  if the configuration is reloaded during the run. Without `{start}`, every
  run appends to the same file.

- **`console`** — set it to `false` for batch jobs whose stdout is saved to
  a file anyway, so that every line is not stored twice.

- **`format`** — adjust the timestamp format to suit your needs, for example
  `"%H:%M:%S"` for time-only, or `"%Y-%m-%dT%H:%M:%S"` for ISO 8601.