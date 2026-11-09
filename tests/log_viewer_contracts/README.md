# Log viewer scenario contracts

This suite exercises the production query builder, parser, reader, API and browser UI. [query_cases.json](query_cases.json) contains 46 explicit queries, 26 invalid requests, 13 UI-construction cases and declarative expansions, including all 1,024 active/inactive predicate combinations. [fixtures/history](fixtures/history/README.md) holds 16 synthetic records in 9 hourly files with missing-hour/day gaps. [API notes](../../docs/log-viewer/API.md) document query semantics and lifecycle behavior.

Each test must name a distinct behavior and link to one scenario ID. Several focused tests may belong to a larger scenario, but must exercise different outcomes or boundaries. Shared setup is encouraged; duplicating the same assertion under different names is not coverage.

## Contract policy

A failing test means its documented viewer behavior may be broken. Do not weaken assertions, delete coverage, skip the test or blindly regenerate expected output to make the suite green. Diagnose the failure. Fix implementation defects while keeping the contract unchanged. For an intentional behavior change, update the scenario, implementation, fixtures where necessary, affected tests and root README change log together. Do not require a test edit when an unchanged test already proves the bug fix.

Tests should fail when the relevant behavior is broken and pass when it works. A temporary targeted defect can verify that a critical assertion detects its regression; restore the implementation before running the final checks. Never leave a deliberate defect in delivered code.

## Scenario matrix

| ID | Distinct behavior to protect | Primary test boundary |
|---|---|---|
| LV01 | Startup shows only filters and performs zero log scans, with Past hour / All levels prepared. | Browser + API activity; reader spy |
| LV02 | Editing filters does not execute; Run submits one validated query and only matching records are displayed. Existing results remain tied to their submitted query. | Browser + actual API/reader |
| LV03 | Exact session search across all history finds records in multiple hourly files and date directories, excluding other sessions. | Query service + real fixture files |
| LV04 | Date/before/after bounds and hour/day boundaries use UTC, inclusive start/exclusive end; relative bounds stay fixed during pagination. | Reader/query service with controlled clock |
| LV05 | Correlation ID includes early null-session records across files; raw details never overwrite trusted session fields. | Query service + browser navigation |
| LV06 | Flow, component, component type, levels, hook, event and text filters combine correctly; details search is opt-in. | Parameterized distinct predicates + integrated browser case |
| LV07 | Version-1 schema maps every envelope field correctly, preserves nested details, Unicode and original JSON. | Production parser + full JSON inspector |
| LV08 | Unsupported schema, invalid types and malformed lines yield explicit diagnostics; later readable records survive. | Parser/reader; browser diagnostics |
| LV09 | Partial EOF is retryable after refresh; a corrupted combined line is reported without fabricated recovery. | Reader with controlled append fixtures |
| LV10 | Load more continues the submitted snapshot without repeating the initial scan or applying pending draft filters. Interrupted requests retain the partial page and retry its exact cursor without losing or duplicating matches. | Query service + browser |
| LV11 | Cancel marks incomplete results; a new query supersedes the previous one and late old responses cannot overwrite it. | Controlled query lifecycle + browser |
| LV12 | Refresh discovers new hours/days and changed old files; replaced/deleted sources invalidate stale references safely. | Reader + query service |
| LV13 | No matches, empty root, partial scan and unreadable data are distinguishable; no false complete/all-clear state. | Service responses + browser states |
| LV14 | HTML-looking values remain text; API cannot expose arbitrary files; no outbound log requests. | Browser + HTTP/file-access boundary |
| LV15 | Installed assets and CLI startup/fallback/shutdown work without a repository checkout. | Packaged-install integration |
| LV16 | Sparse/no-match large-history searches obey scan/response/retention limits and expose progress; oversized records are diagnosed. | Synthetic fixtures + deterministic resource checks |

The [history fixture](fixtures/history/README.md) and [schema-edge fixture](fixtures/schema_edges/README.md) document their unusual records and expected memberships. Mutation, corruption and calendar schedules are materialized in temporary directories. Fixtures contain synthetic data only. Expected values must be independently specified; do not calculate them by calling the parser/filter implementation under test.

Interpretation tests exercise the real parser class. Browser contracts run the real page against the local API and reader with a temporary log root. Fault injection controls filesystem errors, clocks, timing and transport delivery; it must not invent query results or replace the parser/matcher. A reader spy can verify zero scans or continuation. A future storage adapter must preserve the query contract and pass equivalent backend cases.

## Case data and shared runners

The query suite uses one parameterized `test_query_scenario` function. [case_loader.py](case_loader.py) expands named cases at collection, so each failure identifies its scenario. Expected results use independent fixture membership sets and physical source locations. The runner compares canonical queries, exact ordered result IDs, diagnostics, progress, cursor retries and unchanged source bytes. Invalid requests must fail before opening a snapshot.

The presence matrix covers every on/off combination of ten dimensions, not every possible input value. Additional cases cover omitted/null/empty selectors, explicit null matching, impossible conjunctions, subsequent-query recovery, type/size limits, UTC boundaries and schema extraction.

| Cases | Runner | Protected behavior |
|---|---|---|
| Q/V, M, VT | [test_query_scenarios.py](test_query_scenarios.py) | Query creation and execution, validation, filter combinations, gaps and calendar boundaries. |
| B, B-FIELD, B-PRESENCE | [test_js_construction.py](test_js_construction.py) | The production JS builder creates the exact expected request. |
| P | [test_parser_scenarios.py](test_parser_scenarios.py) | Typed extraction, UTC instants, original JSON and schema diagnostics. |
| F | [test_reader_lifecycle.py](test_reader_lifecycle.py) | File gaps, corrupt/partial lines, appends, source changes, safe access and continuation. |
| A | [test_http_scenarios.py](test_http_scenarios.py) | Real HTTP, authentication, cursors, response limits, references and lifecycle. |
| U | [test_browser_scenarios.py](test_browser_scenarios.py) | Real controls, draft/applied state, safe inspection, paging and stale responses. |
| I | [test_packaging_scenarios.py](test_packaging_scenarios.py) | Installed wheel/assets, existing CLI behavior, browser fallback and shutdown. |
| R | [test_resource_scenarios.py](test_resource_scenarios.py) | Inventory/byte/line limits, sparse scans and full retained details. |
| TC backend regressions | [test_remediation_backend.py](test_remediation_backend.py) | Decoder boundaries, diagnostic locations, nonobject details and lifecycle failures. |
| CR1–CR3, TC browser regressions | [test_remediation_browser.py](test_remediation_browser.py) | Cumulative page bounds, actual date controls, hidden startup, draft actions, Refresh/Cancel and inspector fidelity. |
| Active-read interrupt | [test_remediation_interrupt.py](test_remediation_interrupt.py) | SIGINT while an installed process is actually reading, with clean shutdown. |
| CR4 transport recovery | [test_transport_recovery.py](test_transport_recovery.py) | Request loss before service access, response loss after commit, exact retry, Cancel and supersession. |

The regression IDs remain stable so failures and prior fixes can be traced. Each module declares its parameterized cases and assertions; the table is an index, not a substitute for those executable expectations.

## Running the contracts

From the checkout, install `python -m pip install -e '.[test]'`, Node for pure JS construction tests, and Chromium with `python -m playwright install chromium` (on a minimal Linux image use `--with-deps`). `SOH_TEST_CHROMIUM` may select an existing Chromium executable; a system `chromium` is otherwise used when present. Then run:

```sh
python -m pytest -q tests/log_viewer_contracts
```

The environment must permit local loopback sockets and Chromium processes. CI installs Chromium in the existing-suite job and runs these contracts with the other active tests. There are no timing-based CI pass thresholds; passing synthetic scenarios does not establish real-log latency or untested operating-system behavior. Broader repository checks and per-commit results are described in the [test guide](../README.md).
