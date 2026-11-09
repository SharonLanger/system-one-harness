# Offline test suites

Run Python tests after installing `.[test]` and building `typescript-helper`. The full suite also exercises the local viewer in Chromium; install the browser before running it:

```sh
python -m playwright install chromium
python -m pytest -q tests/unit
python -m pytest -q tests/integration
python -m pytest -q tests/system
python -m pytest -q
```

On a minimal Linux system, use `python -m playwright install --with-deps chromium`. See the [viewer scenario contracts](log_viewer_contracts/README.md) for focused commands, fixture expectations, and browser executable selection. These tests run the real local HTTP service and require loopback sockets and browser processes to be permitted.

`unit/` tests one component with isolated collaborators. `integration/` connects
real blocks, flows, and source utilities to a scripted Laya boundary. `system/`
starts at a hook or CLI entry point and checks the resulting behavior. Tests in
each suite mirror the relevant `src/` package. Existing root-level production
tests remain active while their coverage is moved into these suites; they are
included in the full run.

`support/` contains a scripted Laya fake, an in-memory session store, and a
recording logger. Production uses the Python SQLite session store and JSONL diagnostic logger.
Use temporary directories for tests of actual file persistence or process
boundaries. Do not use experiment modules or generated reports as test inputs.

The deferred experiment checks remain excluded by `pyproject.toml`. Each
relevant harness scenario needs an active replacement before removing a deferred
case; experiment reporting and export behavior belong to the experiment project.

The first migrated cases are file resolution, file-reference detection, and
answer rules under `unit/`. Existing first-flow tests and a new real
prompt-to-context chain are under `integration/flows/`. Prompt, duplicate
suppression, compaction, and session isolation are under `system/hooks/`.

## Find results on GitHub (including on a phone)

Open the pull request, then **Checks** (or tap the checks/status row near the merge
section). Choose **Offline tests** and a named job:

- **Python / unit**: focused component tests.
- **Python / integration**: connected flows, blocks and source utilities.
- **Python / end-to-end**: real process entry points and session lifecycle.
- **Python / existing-suite**: all remaining active Python tests, including the
  root-level, block-runtime and viewer contract tests. This keeps existing coverage active while
  the suite organization evolves.
- **TypeScript / build and tests**: compiles the helper and runs all Node tests.

The separate **Repository hygiene** workflow checks the candidate tree and introduced PR/push history for prohibited paths and SQLite headers. It reads Git blobs, including deleted-in-range files. Only XML test reports are uploaded; state directories and database fixtures must never be artifacts.
Each test job has a summary with collected, passed, failed/error and skipped
counts. Expand the test step for failure details. On the workflow run page,
**Artifacts** contains downloadable JUnit XML reports for seven days. Reports
are uploaded on test failure as well as success, when produced. A missing report
is shown as an error, never a pass. The commit displayed on the run is the code
that was checked; old green runs do not validate newer commits.

CI runs automatically on every pull request and on pushes to main. New pushes
cancel older runs for the same PR. Required-check enforcement is a separate
GitHub branch/ruleset setting; a workflow alone does not prevent merging.
This workflow verifies code; it does not publish packages or deploy services.
Standard GitHub-hosted runners are used; no paid service or model credentials
are needed by these tests.

## Coverage added after PR 46

| Behavior | Evidence |
| --- | --- |
| JSON file reading retains Unicode, nested types and empty values; duplicate/malformed JSON fails | `unit/hooks/test_json_boundaries.py` |
| Context serializes to one JSON line; Noop/Failure emit no context | JSON boundary unit tests and real-entrypoint system tests |
| Real blocks handle unsupported action, ambiguous method, missing/empty source input, invalid SDK response, SDK failure, no candidates, removed source and output budget | `integration/flows/test_offline_failure_paths.py` |
| Real CLI and gateway reach real blocks and the TypeScript extractor | `system/cli/test_real_entrypoints.py` |
| Repeat suppression, PostCompact reset and independent sessions persist across fresh child processes | Real-entrypoint lifecycle test, using isolated real temporary storage |
| SDK stdout/FD noise cannot corrupt the public protocol | Real-entrypoint noise test |
| Malformed public JSON never invokes the model | Real-entrypoint malformed-input test |

`support/cli_runner.py` is a test-only bootstrap. It replaces the external Laya
loader, captures log records in memory, and redirects the real SQLite store to a
per-test temporary directory before loading the real entry point. It leaves the
flow, blocks, dispatchers, serializer, TypeScript extraction and transaction
implementation active. Child diagnostics use stderr; tests do not read generated
log files as their result oracle. Temporary input fixtures are still read normally.

These tests do not establish model quality or full operating-system network/write
isolation. Those remain separate work. Existing persistence scenarios are exercised with SQLite; obsolete Node/JSONL transport assertions have been replaced.

`unit/hooks/test_session_store_validation.py` covers metadata, JSON, exact UTF-8 limits, initialization and filtering; `test_session_transaction.py` uses real SQLite locks and coordinated spawned processes for decision races, rollback, process termination and deadlines. `unit/test_repository_hygiene.py` creates only synthetic disposable Git repositories and databases. `system/hooks/test_sqlite_process_lifecycle.py` checks fresh CLI/gateway processes, failure recovery and the existing commit-before-emission limitation. The `SQLite / …` CI matrix supplies focused Python 3.11/Linux and macOS permissions, locks and process checks; local Linux results do not establish macOS validation.

## Next project: experiment migration

Keep all experiments and deferred cases during this change. A later, separately
planned migration will copy them to a new repository, verify completeness and
history/provenance, identify harness behavior that needs active coverage here,
and only then remove the migrated content here with explicit approval. The 105
named deferred checks are an inventory to disposition, not a requirement to run
experiment reporting infrastructure in the harness. Do not infer migration
completion or deletion permission from a green test run.
