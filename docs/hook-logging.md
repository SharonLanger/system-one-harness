# Hook invocation logs

In a source checkout, records are written to `.logs/YYYY-MM-DD/HH-00.jsonl`
using UTC dates and full zero-padded hours. Installed copies use
`$XDG_STATE_HOME/system-one-harness/.logs` (or
`~/.local/state/system-one-harness/.logs`). Set `SOH_LOG_ROOT` to an absolute
local path to override that location. `system-one-harness logs --last 20` reads
recent records in file and append order. It does not create files.

Every line is a version-1 JSON object with `schema_version`, `timestamp`,
`level`, `message`, `event`, `component_type`, `component`, `hook_event`,
`session_id`, `correlation_id`, `flow_name`, `pid`, and `details`. Contextual
fields are null until known. `correlation_id` is the gateway invocation UUID;
`pid` is only diagnostic. The `message` is a short explanation and `details`
holds structured event-specific fields, including prompt text and supplied hook
fields where existing events include them. The log writer uses a process lock
and owner-only permissions on new Unix files and directories. Power loss or
process termination during a write can leave a partial final line; there is no
retention or automatic cleanup in this version.

## Gateway

- `gateway_started`: after valid argument parsing and deadline setup, before stdin
  is read; includes mode, process cwd, supplied project environment and source-root
  argument. Interpreter/module startup failures cannot be logged by the gateway.
- `gateway_input_received`: original stdin text before JSON validation, bounded
  to 65,536 characters, plus read count and truncation flag. Available even for
  malformed/oversized input; oversized input contains only the captured prefix.
- `gateway_received`: parsed payload and field names after JSON validation.
- `gateway_dispatch_started` / `gateway_dispatch_finished`: common dispatch call
  and returned result type. An exception or timeout can prevent a finish record.
- Existing `gateway_error`, `gateway_noop`, `response_prepared` and
  `gateway_finished` show errors, response size, result and stdout bytes written.

## Common dispatcher and event dispatcher

- `hook_dispatcher_started`: entry and command hook name.
- `common_input_validated`: validated common and derived field names.
- `hook_dispatcher_routed`: event destination and common/derived metadata.
- `hook_dispatcher_route_skipped`: unsupported event, with reason.
- `hook_dispatcher_rejected`: validation stage, field and error code.
- `event_dispatcher_started`: event dispatcher entry and received envelope metadata.
- `hook_input_ingested`: prompt length, cwd and project/source-root metadata.
- `feature_started` / `feature_finished`: feature result, duration and failure code.
- `event_input_rejected`: invalid event-specific input.
- `event_dispatcher_finished` / `hook_dispatcher_finished`: returned result type,
  no-op reason or failure code, including unexpected-exception markers.

Observe-only mode logs gateway entry/input plus the existing
`observation_input_received` and `hook_observed` records. It never logs actual
 dispatch entry because no dispatcher runs. Intended dispatch destinations remain
in `hook_observed` with `dispatcher_invoked: false`.

Logs use the shared local file writer, never protocol stdout. Raw capture does
not dump the full process environment or read a transcript. Deadline expiration
can prevent subsequent logging; a missing finish record is not proof of success.
