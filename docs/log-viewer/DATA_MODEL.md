# Local log data model

SOH writes one JSON object per line. Use the [viewer guide](README.md) to launch
and search, or the [API contract](API.md) to build a reader. Logs describe activity;
the SQLite [session store](../session-state.md) separately controls repeat enrichment.

## A complete record

This is synthetic data, shown across lines for readability. The persisted record
occupies one line:

```json
{
  "schema_version": 1,
  "timestamp": "2026-11-10T12:34:56.123Z",
  "level": "INFO",
  "message": "Flow finished.",
  "event": "flow_finished",
  "component_type": "flow",
  "component": "first_method_context",
  "hook_event": "UserPromptSubmit",
  "session_id": "example-session",
  "correlation_id": "4480000e-fec5-48dc-9513-c97a967d7dc5",
  "flow_name": "first_method_context",
  "pid": 1234,
  "details": {"outcome": "Context", "reason": null, "code": null, "stage": "formatting"}
}
```

## Fields

| Field | Writer type / nullable | Meaning |
| --- | --- | --- |
| `schema_version` | integer / no | Envelope version; currently `1`. |
| `timestamp` | string / no | UTC event time in ISO 8601 format. |
| `level` | string / no | `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `message` | string / no | Human-readable event description. |
| `event` | string / no | Stable event name from the logging vocabulary. |
| `component_type` | string / yes | Kind of producer: gateway, dispatcher, flow, block, model, extractor, session store or CLI. |
| `component` | string / yes | Producing component's registered name. |
| `hook_event` | string / yes | Host event, such as `UserPromptSubmit` or `PostCompact`. |
| `session_id` | string / yes | Validated host session, once known. |
| `correlation_id` | string / yes | Gateway invocation UUID; separate prompts usually have different IDs. |
| `flow_name` | string / yes | Flow active at emission, if any. |
| `pid` | integer / no | Diagnostic process ID; not a session identifier. |
| `details` | object / no | Event-specific data; nested values can include explicit nulls. |

For example, two prompts and a compaction can share one session ID but have three
correlation IDs. Search **all history + session** for the conversation's validated
events. Search **correlation ID** to follow one invocation, including its early
records before session validation. A session-only query cannot include records
whose top-level session is null.

The common dispatcher promotes session context after validation. Gateway startup,
raw input and rejected common input can have null session IDs. Observe-only mode
does not run common validation, so its top-level session remains null even if a
supplied session appears inside `details`. Nested fields never become trusted
top-level filter values. Capturing a transcript path does not read that transcript.

## Event families

| Events | What they establish |
| --- | --- |
| `gateway_started`, `gateway_input_received`, `gateway_received` | Valid arguments/deadline setup, bounded raw input, then parsed JSON respectively. |
| `gateway_dispatch_started`, `gateway_dispatch_finished` | Dispatch was attempted, then returned a result if it completed. |
| `hook_dispatcher_started`, `common_input_validated`, `hook_dispatcher_routed` | Common ingestion, session validation and selected event handler. |
| `hook_dispatcher_rejected`, `hook_dispatcher_route_skipped` | Invalid common input or unsupported event. |
| `event_dispatcher_started`, `hook_input_ingested`, `event_input_rejected` | Event-specific validation and metadata. |
| `feature_started`, `feature_finished`, `flow_finished` | Flow/feature outcome, reason or error. |
| `prediction_started`, `response_received`, `response_validated`, `prediction_failed`, `input_budget_rejected` | Model request lifecycle and strict-input refusal. |
| `response_prepared`, `gateway_finished` | Preparation versus final outcome and reported stdout bytes. |
| `gateway_error`, `gateway_noop` | Handled failure or deliberate absence of context. |
| `observation_input_received`, `hook_observed` | Observe-only capture; `dispatcher_invoked` is false. |

`event_dispatcher_finished` and `hook_dispatcher_finished` close their respective
stages. Additional component events are defined in
[vocabulary.py](../../src/soh_logging/vocabulary.py); `details` varies by event.
Prepared context or committed session state does not prove delivery to Claude.
A missing finish event may mean interruption, timeout or a logging failure.

## Files, incomplete data and retention

In a checkout, the writer uses `.logs/YYYY-MM-DD/HH-00.jsonl` in UTC: **one file
per hour**, created when needed. Installed copies use the user-local state root;
an absolute `SOH_LOG_ROOT` overrides it. See [log locations](README.md#which-logs-are-read).
Hours and days without events need no empty files. A session can span many files.

Writers coordinate appends with a process lock and use owner-only permissions for
new Unix files/directories. A terminated write can leave an incomplete final line.
The viewer defers unterminated tails and reports malformed/oversized records;
it does not repair them. It reads snapshots, so new appends need a new search or
Refresh. Results follow reverse file/append order rather than a strict timestamp sort.

For readable historical records, missing contextual fields normalize to null and
missing details to `{}`. Original unknown fields, explicit nulls and nonobject
details are preserved in the inspector; historical values are not rewritten.
Malformed required fields become diagnostics instead of ordinary result rows.

There is no automatic retention or log cleanup. The viewer is read-only; deleting
logs is a separate local operation. Prompts, source and model replies may appear
in records. They are not suitable for public uploads without review.

## Startup failures

`gateway_started` is emitted only after application imports, argument validation
and deadline setup. A missing `cli` package, invalid arguments or failure before
that point can appear only in Claude's hook stderr, with no JSONL record. The
viewer does not ingest Claude transcripts. Run the exact configured executable
with `doctor` and check the [plugin troubleshooting steps](../../plugins/README.md#everyday-use-and-troubleshooting).
