# Local viewer API implementation notes

HTTP request version 1 is independent from log schema version 1. All API routes use the launch token as `Authorization: Bearer …`. The launch URL carries it in a fragment; the browser removes it immediately and keeps it in memory. There is no browser persistence or CORS allowance. The server binds to `127.0.0.1` on an ephemeral port and validates Host and Origin. See the [log viewer guide](README.md) and [scenario tests](../../tests/log_viewer_contracts/README.md).

## Query request

```json
{
  "query_version": 1,
  "time": {"mode": "all"},
  "filters": {
    "session_id": {"values": ["session-A"], "include_null": false},
    "level": {"values": ["ERROR", "WARNING"]}
  },
  "text": {"value": "selection", "scope": "message"},
  "page_size": 200,
  "order": "file_append_desc"
}
```

- Filter keys are `session_id`, `correlation_id`, `flow_name`, `component`, `component_type`, `level`, `event` and `hook_event`. Values within a selector are OR; different filters are AND. Equality is exact and case-sensitive, including meaningful whitespace. Blank values are invalid. An omitted/null/empty selector imposes no restriction.
- `include_null` matches null context and may be combined with values. It is allowed for every filter except required `level` and `event`. The string `"null"` remains distinct from null. Details never supply trusted session or correlation context.
- Omitted/null time defaults to `{"mode":"relative","seconds":3600}`. Relative seconds must be an integer from 1 through 604800; bounds are resolved once at creation. `{"mode":"all"}` removes both bounds. Absolute mode accepts timezone-aware `start` and/or `end`; at least one is required, and start must precede end. Bounds use UTC, inclusive start/exclusive end, with up to six fractional digits.
- Text is a literal Unicode casefolded substring. Scope is `message` or `message_and_details`; details use sorted, compact JSON with unescaped Unicode. Message and details are searched separately. Empty/omitted/null text is unrestricted.
- Defaults are query version 1, page size 200 and `file_append_desc`; explicit null is invalid for those options. Page size is 1–200. Unknown keys/operators, wrong types, duplicate JSON keys and non-finite numbers fail validation before log access. Requests are limited to 64 KiB, selectors to 100 values, and individual values/text to 1,024 Unicode code points.

`QueryBuilder` produces an immutable `LogQuery` with absolute bounds and normalized selectors. `LogRecordParser` preserves original JSON and validates schema 1; malformed/unsupported records become diagnostics. `LogReader` owns file discovery, byte spans and snapshots, while `LogQueryService` owns opaque query/cursor/reference lifecycles. A replacement reader must preserve these query outcomes and lifecycle contracts; the browser does not filter log records itself.

## Search and continuation

`POST /api/searches` validates and creates a query without enumerating files. Its `query` is canonical absolute UTC data; `cursor` is opaque. `POST /api/searches/{query_id}/advance` accepts only `{ "cursor": "…" }`. Its result includes `matches`, `cursor`, `state`, monotonic `progress`, bounded `diagnostics`, exact `diagnostic_counts`, `inventory_complete`, `empty_root` and `coverage`. Continue after empty budget-limited batches. Stop automatically at a display page; explicit Load more resumes the same snapshot. Retrying the most recent input cursor returns its cached response.

Summary messages and long categorical previews explicitly identify truncation. Exact selectors and full records always use the full original values. Display order is descending path and descending byte offset, not strict timestamp order.

Discovery reads existing `YYYY-MM-DD/HH-00.jsonl` partitions in the configured root and tolerates missing hours/days. Date pruning assumes writer-generated placement. Each selected file's identity and length are captured independently; appends require a new search/Refresh. This is not an atomic snapshot across all writers. Symlinks and nonregular sources are rejected. Unterminated tails are deferred, oversized lines are diagnosed, and neither is repaired. Changed sources invalidate retained references; retrieval checks record digests. The append-only writer contract does not guarantee consistency under arbitrary rewrite-plus-growth.

Advance states are `budget_yield`, `page_ready`, `complete`, `failed` and `canceled`. `coverage` is `unknown` during discovery, `partial` while scanning or after skipped/deferred data, and `readable_complete` only at EOF without those conditions. Zero matches with a cursor is not a completed no-match result. Progress reports files discovered/examined, bytes/records scanned, matches seen, skips and pending tails. Counts are snapshot-scoped and cursor retries must not increment them again.

## Full record response and normalization overlay

`GET /api/searches/{query_id}/records/{ref}` returns:

```json
{
  "original": { "schema_version": 1, "timestamp": "2026-11-09T09:30:00Z", "level": "INFO", "message": "…", "event": "prepared", "pid": 1 },
  "fields": { "session_id": null, "correlation_id": null, "flow_name": null, "component": null, "component_type": null, "hook_event": null, "details": {} },
  "normalized_timestamp": "2026-11-09T09:30:00.000000Z",
  "source": { "path": "2026-11-09/09-00.jsonl", "offset": 0, "length": 123 }
}
```

`original` is the complete decoded persisted JSON document, including unknown keys, unmodified timestamps, nested details, empty strings and explicit nulls. `fields` is a **sparse normalization overlay**: only schema fields absent from `original` are supplied (missing context becomes null; missing details becomes `{}`). To obtain all searchable/display fields, merge `original` followed by `fields`. The controller performs this merge before building the inspector. The separate normalized timestamp is UTC with six fractional digits.

This representation avoids duplicating a maximum-sized message/details payload in the 8 MiB response. It does not truncate the original record. The parser's in-process `ParsedRecord.fields` still contains all normalized schema fields. Details never promote context: persisted top-level fields remain authoritative. `A06`, `P03/P05/P07`, `R03-max-line-detail`, and browser inspector cases cover the transport and preservation boundaries.

Diagnostic previews are separate `GET /api/searches/{query_id}/diagnostics/{ref}` responses. They are labeled `validated_record:false`; preview bytes are bounded and `truncated` is explicit. These previews are not normal query rows.

`POST …/cancel` with `{}` is idempotent; `DELETE /api/searches/{query_id}` releases query state only. Neither writes or deletes logs. Changed sources return 409; evicted/expired handles return 410 with restart guidance. One outstanding advance per query is enforced independently from cancellation.

Limits live in [limits.py](../../src/log_viewer/limits.py): four active queries, 15-minute idle expiry, 100,000 inventory entries/32 MiB metadata, 32 MiB scan budget per advance, 4 MiB lines, 256 KiB summary responses, 8 MiB detail responses, 50 representative diagnostics and 16 KiB raw previews. The browser retains up to five pages/1,000 summaries; the service retains up to 1,000 record handles per query. The 100 ms time slice is cooperative: directory calls and a single maximum-sized parse can exceed it. Resource tests enforce deterministic limits, not latency promises.

## Display pages and controls

The logical display-page allowance persists across every empty or nonempty budget/response-byte yield. Only filling the requested page resets that allowance for Load more. The browser reads client/handle limits from config and explicitly evicts whole old pages before requesting a new page, so still-visible references remain available throughout partial advances. UI datetime-local values gain required seconds and an explicit UTC suffix without changing supported fractions; backend timestamp rules are unchanged.

## Interrupted display-page recovery

The controller retains its partial page, query/generation ownership and unresolved input cursor if an advance request or response is lost. **Retry current page** retries that exact cursor against the same snapshot; a committed response is returned by the service cache and merged once. Previously buffered matches remain part of that display page, and retry does not reserve/evict another page. New Run or Cancel explicitly discards the old query's partial buffer. Expired or invalid API state stops continuation with incomplete/restart guidance. No automatic retry loop or scan restart occurs.
