# View and search local logs

Use the log viewer to investigate a hook invocation, follow a session across hours or days, and inspect the full JSON behind an event. It reads existing local files without changing them or sending log data to an external service.

## Open the viewer

After [setting up SOH](../../README.md#set-up-a-development-checkout), activate the environment where it is installed and run:

```bash
system-one-harness logs --ui
```

The command prints a local URL and opens your default browser. Leave the terminal running while you use the viewer; press Ctrl-C to stop it. Viewing logs does not require a model, Docker, Node or Playwright. Playwright/Chromium installation is only needed for browser tests.

To open the browser yourself, run:

```bash
system-one-harness logs --ui --no-open
```

Open the complete URL printed in that terminal. It includes the authorization token for this launch; keep it local. If the browser does not open automatically, the same printed URL remains usable. Opening the packaged HTML file directly does not start the viewer's local reader.

## Run your first search

1. The page starts with **Past hour** prepared and no level restriction. It shows only filters and does not scan until you click **Run search**.
2. Set the filters you need, then click **Run search**. Leave a field blank to impose no restriction on it.
3. Select an event's message to open the **Record inspector**. Read the full message, expand **Original JSON**, or use **Copy JSON**.

| What you want to see | Filters to set before clicking Run search |
|---|---|
| Recent errors | Time range: **Relative time**; Relative window: **Past hour**; Levels: `ERROR`. |
| A session's entire stored history | Time range: **All history**; Session ID: the exact session ID. Clear any other filters that would narrow it. |
| One hook invocation, including early records without a session | Time range: **All history**; Correlation ID: the invocation ID. Clear other restrictions. |
| Records in a date range | Time range: **Custom UTC range**; fill **At or after (UTC)** and/or **Before (UTC)**. |
| A word or phrase in messages or details | Enter **Literal text**; select **Also search details** if needed. |

A session can span many hourly files and dates. **All history** includes those files even when some hours or days have no logs. From a selected event, **Prepare session history** or **Prepare invocation history** sets up an all-history query and clears other restrictions. Click **Run search** to apply it; **Back to previous draft** restores the prior filters.

## Filters and results

Session, correlation, flow, component/type, level, event and hook values match exactly, including letter case. Enter multiple values on separate lines to match any of them within that field; different fields combine with AND. For example, `ERROR` and `WARNING` on separate lines match either level within the selected session.

**Include null** selects records without that context. The literal text `null` is a different value. Early invocation records may have no session ID; use their correlation ID to follow the invocation. Text search is a case-insensitive literal substring, with no regular expressions. Dates use UTC, including the start instant and excluding the end instant.

Changing filters or clicking **Clear filters** only changes the draft. Existing results stay attached to the last submitted query until you click **Run search** again.

- **Load more** continues that query without applying unsent edits. A full page does not mean the search has reached all matching history.
- **Refresh** starts a fresh snapshot and includes newly written logs. It is disabled while filter edits are pending; use Run search to apply those edits.
- **Cancel** stops the current search and marks the results incomplete.
- **Retry current page** recovers an interrupted request against the same snapshot, retaining buffered matches.

Results use newest file and append position first; they are not strictly sorted by event timestamps. **Coverage diagnostics** explains unreadable files, malformed records and incomplete tails. Readable records remain searchable. This viewer has no automatic live tail; use Refresh for new data.

## Which logs are read?

SOH writes one file per active UTC hour: `YYYY-MM-DD/HH-00.jsonl`. A single file can contain multiple sessions, flows and components. Missing hours and days are normal when no events were written.

The viewer uses the same root selection as the writer, in this order:

| Configuration | Log root |
|---|---|
| `SOH_LOG_ROOT` is set | That absolute path. |
| Running an installation from a source checkout | The checkout's `.logs` directory. |
| Installed outside a checkout | `$XDG_STATE_HOME/system-one-harness/.logs`, or `~/.local/state/system-one-harness/.logs` when XDG_STATE_HOME is unset. |

To inspect a specific existing log root:

```bash
SOH_LOG_ROOT=/absolute/path/to/.logs system-one-harness logs --ui
```

Choose the root containing the date directories, not a single hourly file. The viewer does not create a missing root or combine multiple roots. The root follows the SOH installation or explicit override; changing the terminal's working directory does not select another project's logs. Legacy `logs/debug.jsonl` is not included. See [hook invocation logs](../hook-logging.md) for the record schema and event meanings.

## View logs in the terminal

```bash
system-one-harness logs --last 20
```

This prints the last 20 raw log lines in file/append order and exits. Without `--last`, the default is five lines. Use the browser viewer for search, filtering and JSON inspection. `--last` cannot be combined with `--ui`; `--no-open` requires `--ui`.

## Troubleshooting and limits

| What you see | What to do |
|---|---|
| `system-one-harness: command not found` | Activate the environment where SOH is installed. In that environment, `python -m cli logs --ui` is an alternative entry point. |
| Filters but no results table | Click **Run search**; opening the page does not run a query. |
| No matches | Try **All history** and clear restrictive fields. Check that the writer and viewer use the same log root. |
| Missing or empty log root | Check `SOH_LOG_ROOT` and whether this SOH installation has written logs. Viewing does not create them. |
| Disconnected or authorization required after reloading | Keep the CLI running and reopen its complete printed URL. If it stopped, launch the command again and use the new URL. |
| Expired query or evicted history | Run a new search, narrowing the time range or filters if useful. |
| Complete with warnings | Expand Coverage diagnostics; skipped or deferred data means the results may be incomplete. |

Queries expire after 15 idle minutes. The viewer retains at most five pages/1,000 summaries and diagnoses lines larger than 4 MiB instead of loading them. Broad searches scan file contents, so latency depends on log volume; startup performs no scan. Real-log performance and macOS viewer behavior have not yet been validated; automated viewer browser coverage uses Linux/Chromium and synthetic logs.

Logs can contain prompts, model output and source code. Keep copied records and the launch URL local unless you intend to share them. The viewer does not redact logs or perform retention/cleanup.

For maintenance, see [API and query semantics](API.md) and [scenario contracts and test commands](../../tests/log_viewer_contracts/README.md).
