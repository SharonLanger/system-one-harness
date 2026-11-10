# Documentation, experiments and sanity-example cleanup proposal

Status: proposal for review, November 10, 2026. This document is part of PR #58 alongside the log-viewer README improvements. **No cleanup, file deletion, experiment migration or example replacement has been performed.** The scripts and target paths below describe proposed work, not commands that are available yet.

Inventory baseline: `ad38e3c6d2beee396d2397e1e1ddf3599461456c`. The inventory covers every tracked file under `docs/`, `experiments/` and `examples/`, plus their references in production code, Python/TypeScript tests, packaging and repository documentation. It does not assert that historical experiments still run or that live model compatibility has been verified.

## Recommendation

Keep documentation that helps a user run SOH or helps a maintainer understand a current component contract. Rewrite those documents around tasks, inputs, outputs and troubleshooting. Remove PR-phase narratives, obsolete migration handoffs and external-backup pointers after relocating any useful instructions. Keep one guide per maintained flow.

Remove the entire tracked `experiments/` tree from this repository after preserving its research in the experiments project and resolving the dependencies listed below. Keep production tests and their independently specified fixtures. Replace the current example assortment with **seven standalone sanity scripts** and an `examples/README.md` that says exactly what each script checks and whether it loads a real model.

Do not retain a parallel experiment framework under `examples/`. This proposal itself is temporary review material: remove it when the approved cleanup is implemented and the final user/maintainer documentation contains the resulting decisions.

## 1. Documentation inventory: all 21 tracked files

There are 13 Markdown documents and eight supporting files under `docs/` before adding this proposal. “Remove” below is a recommendation for the implementation pass, not an action performed by this PR update.

| Current document | Decision | Reason and proposed destination |
|---|---|---|
| [flows/method-context.md](flows/method-context.md) | **Keep and correct** | A flow guide belongs here. Preserve the execution walkthrough, supported/skipped cases and diagram. Correct its stale claim that PostCompact uses JSONL under `logs/session-state/` and does not use a database: the current store is SQLite under `.soh/session-state.sqlite3`. Link to the session-state contract; move extended experiment comparisons out. |
| [hook-usage.md](hook-usage.md) | **Keep and rewrite** | Users and agents need installation, enable/disable, input/output and troubleshooting instructions. Link to the plugin setup and maintained flow guide. Remove historical sandbox/phase/cleanup-document references. Its registration example is exercised by an active test, so update that test deliberately if the documented recipe changes. |
| [hook-logging.md](hook-logging.md) | **Consolidate, then remove this file** | The schema and event meanings are useful; the current narrative/event dump is a poor entry point. Move stable record fields and event context into proposed `docs/log-viewer/DATA_MODEL.md`; keep viewing, roots and operational limits in the viewer README. Update every link before removing the old path. |
| [log-viewer/README.md](log-viewer/README.md) | **Keep** | The user entry point: launch, first search, session/invocation history, JSON inspection, file locations and troubleshooting. Add a link to the data-model guide once it exists. |
| [log-viewer/API.md](log-viewer/API.md) | **Keep** | Maintainers need request/filter semantics, cursors, snapshots, diagnostics and limits. Keep HTTP details here rather than mixing them into the user guide. |
| [session-state.md](session-state.md) | **Keep and simplify** | This describes active SQLite behavior, its location, stored fields, compaction and failure semantics. Organize around those tasks/contracts. Fold contributor-only Git hygiene details into CONTRIBUTING/SECURITY where appropriate; retain the sensitive-data boundary and commit-before-emission limitation. Session state is distinct from diagnostic logs. |
| [configuration-conventions.md](configuration-conventions.md) | **Keep and rewrite headings** | Current JSON structure, policy conditions and duplicate-key behavior matter. Replace “V1/F08” project-history framing with current behavior and examples. Do not imply duplicate-key rejection exists where it is still deferred. |
| [real-laya-mac.md](real-laya-mac.md) | **Keep and rewrite** | Real-model setup is essential. Lead with prerequisites and the new basic real-model sanity example. The plugin documentation update in this PR corrects the removed module namespace to `python -m live_check` and adds the browser-test prerequisite. The larger rewrite remains proposed: remove Phase20 handoff language and keep the existing larger probe documented separately as a compatibility diagnostic while it remains supported. |
| [token-budget-migration.md](token-budget-migration.md) | **Replace, then remove the migration file** | “Current PR23 contract” is obsolete packaging for a real API contract. Proposed `docs/input-budgets.md` should explain ordinary `predict`, opt-in `predict_strict`/block `strict=True`, supported capabilities, refusal types and caller handling. Update runtime error hints and source references before deleting the old path. |
| [system-one-harness-migration.md](system-one-harness-migration.md) | **Fold useful compatibility notes into setup, then remove** | Preserve the old/new distribution collision warning, compatibility executable and `LAYA_CONTEXT_ROUTER_CLI` setting in the root/plugin setup guides. Remove the separate migration narrative and historical-design pointers. Do not remove compatibility code as part of documentation cleanup. |
| [cleanup-local-files.md](cleanup-local-files.md) | **Remove** | One-time pull/backup instructions and a Drive archive link are not product documentation. Current Git-hook/security instructions already have maintained homes. Replace inbound references with useful current docs or verified research references; do not carry the Drive backup link into the new guides. This does not delete anything from Drive. |
| [requested-action/requested-action-500-results.md](requested-action/requested-action-500-results.md) | **Move to experiments project, then remove here** | Detailed profile comparisons, campaign methodology and evidence belong with the research. Keep a concise current selected-profile contract and honest limitations in the requested-action block README, plus independent test-owned profile expectations. Update root/plugin/block links. |
| [readme-assets/README.md](readme-assets/README.md) | **Keep** | Short, useful instructions for maintaining diagrams and retaining attribution. This is asset maintenance documentation, not a PR handoff. |

The eight non-Markdown files are accounted for separately:

| Files | Decision | Reason |
|---|---|---|
| `readme-assets/build-with-blocks.svg`, `event-routing.svg`, `method-context.svg` | **Keep all three** | Used by the root/flow README. Keep planned routing clearly labeled as planned. |
| `readme-assets/build-with-blocks.spec.json`, `event-routing.spec.json`, `method-context.spec.json` | **Keep all three** | Editable sources for maintained diagrams. |
| `readme-assets/animdiagram-LICENSE.txt` | **Keep** | Required attribution for the retained assets/tooling. |
| `requested-action/data/requested-action-500-top-five.csv` | **Move with its research report** | Research evidence, not runtime configuration or a required product fixture. |

### Proposed log documentation structure

Keep three documents with clear audiences:

- `docs/log-viewer/README.md`: how to view logs, choose a root, filter history, inspect JSON and interpret warnings.
- `docs/log-viewer/DATA_MODEL.md`: what the logs mean. Include a complete synthetic JSON record; a field/type/nullability table for schema version, UTC timestamp, level, event, message, PID, session, correlation, flow, component/type, hook and details; session-versus-invocation examples; event families; hourly partitioning; incomplete records; and the distinction between logs and SQLite session state.
- `docs/log-viewer/API.md`: the service/query contract for maintainers and alternative readers.

The data-model guide must explain that early records can have null session context, details do not promote trusted fields, unknown values are preserved, and a missing finish event is not evidence of success. Distinguish the writer's normal schema from the viewer's handling of historical missing fields/nonobject details. Keep retention facts explicit: there is no automatic cleanup, the viewer is read-only, and deleting local logs is a separate user action. Do not turn the old “cleanup local files” backup memo into a log-deletion feature.

## 2. Experiments inventory and removal scope

The tracked `experiments/` tree contains **268 files, approximately 2.46 MB**. All seven groups below should leave the harness repository; only selected scenario ideas and tiny synthetic fixtures should be adapted into the new examples/tests.

| Group | Tracked files | Content and recommendation |
|---|---:|---|
| Top-level scripts and Markdown | 23 | Manual Laya/block/flow/hook calls, repeat runs, report/trace helpers and setup notes. Extract small sanity scenarios; move the original research tools out. |
| `experiments/batch/` | 6 | Older batch runner, reports and configurations. Move out entirely. |
| `experiments/batch_v2/` | 185 | Engine/adapters/reporting, research orchestration, campaign protocols/cases and fixtures. Move out entirely. Breakdown: 15 top-level files, 33 docs, 117 campaign files, 13 research files, six generic-choice example files and one research-demo check. |
| `experiments/fixtures/` | 21 | Connected TypeScript project, synthetic method and demo rules. Retain only independent test/example-owned fixtures still needed; move the research originals out. |
| `experiments/inputs/` | 21 | Synthetic experiment prompts, hook events and run configurations. Inline small inputs or use a tiny example fixture; do not copy the whole catalog into examples. |
| `experiments/tokenizer_smoke/` | 7 | One useful strict-input check family spread over several scripts. Consolidate the valid/refusal scenario into one proposed sanity entry point; move the original family out. |
| `experiments/tools/` | 5 | Export, analysis and scale tooling. Move to the experiments project, not examples. |

The existing `SharonLanger/system-one-experiments` repository is accessible and private. **Its existence was checked; completeness of the migrated content was not.** Before removing the source tree, compare the tracked inventory against the intended destination, preserve source revision/history or a verifiable snapshot, and record which items are copied, superseded or intentionally retired. Do not invent an archive location or describe an unverified copy as complete. This proposal does not authorize publishing private destination content.

Scope the eventual removal to tracked repository content. Do not recursively wipe ignored local run results, captured inputs, model caches or logs just because they are under an experiments working directory. No changes to the separate experiments repository or external backups are made in this proposal pass.

### Dependencies that must be resolved before deletion

| Dependency found | Required action | Risk if ignored |
|---|---|---|
| [typescript-helper/test/support-contract.test.cjs](../typescript-helper/test/support-contract.test.cjs) reads `experiments/fixtures/connected_project/src/billing/invoice-service.ts` | Point it to a test-owned fixture. The existing `tests/fixtures/first_flow_project/src/billing/invoice-service.ts` is byte-identical in this checkout; retain the current outline/extraction assertions. | **High:** an active TypeScript test breaks when experiments are removed. |
| [tests/deferred](../tests/deferred/README.md): 21 tracked files, 105 test-function definitions; [tests/fixtures/deferred](../tests/fixtures/deferred/) has 11 tracked files | Disposition every entry in the 17-entry manifest. Move experiment-engine/report/export tests and their fixtures with the research; keep or establish explicit active counterparts for any harness behavior still needed here. Update the test guide and pytest exclusion only when the resulting ownership is settled. | **High:** silent loss of scenarios, or broken deferred imports concealed by skips. A green ordinary suite does not validate deferred coverage. |
| [tests/fixtures/selected_profiles](../tests/fixtures/selected_profiles/README.md) and the requested-action/method-selection selected-profile tests | Keep these independent expectations and provenance. They already avoid reading experiment suites at runtime. | **High:** removing production contract fixtures along with research would weaken active behavior checks. |
| `src/laya/budget.py` error hint and `src/laya/predict.py` docstring point to `docs/token-budget-migration.md` | Update them to the maintained input-budget guide. | **Medium:** runtime errors direct users to a deleted document. |
| [tests/test_hook_scenarios.py](../tests/test_hook_scenarios.py) parses JSON examples from `docs/hook-usage.md` | Preserve executable registration/input/output examples, or adapt the recipe test without weakening its enable/disable and protocol assertions. | **High:** a prose rewrite can break a meaningful active integration contract. |
| Examples are imported/executed by active tests | Resolve the concrete dependencies in section 3 before renaming or replacing them. | **High:** ordinary tests could break or accidentally start real inference. |
| Root README, block/plugin READMEs, flow guide and fixture docs link to research/cleanup documents | Replace links with current contracts or verified research destinations. Keep production settings/limitations visible locally even if the research repository requires access. | **Medium:** broken onboarding and lost explanation of selected defaults. |
| `src/building_blocks/questions/method_selection_v2/research/method-selection-200-results.md` is research outside `experiments/` | Move this report with the research and shorten the block README to its current profile, inputs, outputs and caveats. Update its links from the root/flow docs. | **Medium:** deleting the experiments directory alone leaves the same research clutter and broken references elsewhere. |
| `src/live_check.py` still uses `flows.file_method_context`, the compatibility alias for the retired sandbox | Keep this published diagnostic and compatibility modules during this cleanup; document its scope honestly. Review their eventual retirement separately if desired. | **Medium:** deleting production modules based only on experiment-oriented names would exceed the requested cleanup. |

Source inspection found no Python production import or hard-coded path into `experiments/`. That is useful evidence, not a substitute for checking dynamic paths, packaging, both language test suites and documentation after the actual removal. Several old experiment scripts still assume a single `log_utils.LOG_PATH`; production now normally sets that compatibility override to `None`. Extract scenario intent rather than copying those report/trace wrappers unchanged.

## 3. Current examples: what to keep or replace

There are five Python files and no `examples/README.md`.

| Current file | Assessment | Proposed action |
|---|---|---|
| [basic_question.py](../examples/basic_question.py) | Uses a fixed `MockAgent`; it does not load a model. An active prediction test executes it and expects the synthetic response. | Replace the user-facing script with a deliberate real-model sanity call. Preserve the existing mock assertion under tests; do not let ordinary CI start inference. |
| [file_method_context.py](../examples/file_method_context.py) | Uses the old sandbox compatibility flow and a controlled SDK response. | Replace with a small example of the current `first_method_context` flow, explicitly labeled offline when its SDK is simulated. |
| [user_prompt_submit_scenario.py](../examples/user_prompt_submit_scenario.py) | Historical SDK injection and sandbox-era expectations; not the current first-flow acceptance command. | Replace with a current gateway replay that clearly identifies its offline/live boundary. |
| [budget_diagnostics.py](../examples/budget_diagnostics.py) | A callable helper, not a runnable sanity script. `tests/test_token_budget_blocks.py` imports it and checks its diagnostic behavior. | Fold the useful handling into the budget guide and runnable strict-budget example. Preserve the helper's failure-report assertions in tests or an intentionally retained small helper. |
| [_plumbing_inspection.py](../examples/_plumbing_inspection.py) | Synthetic inspection provider imported by examples and three active token-budget test modules. | Move the test-owned capability fixture into `tests/support/` and update imports together. Keep any example-only offline support small and explicit; real examples must not use this provider. |

The three direct test consumers of `_plumbing_inspection` are `test_token_budget_compatibility.py`, `test_token_budget_enforcement.py` and `test_token_budget_registry.py`. Separately, `tests/test_token_budget_blocks.py` imports `budget_diagnostics`, and `tests/test_predict.py` executes `examples/basic_question.py`. Recheck these references during implementation before moving the helpers or changing entry-point behavior.

## 4. Proposed examples: seven standalone sanity scripts

Use the existing **Laya / `laya_mlx` adapter**, not a new generic LLaMA/Ollama/llama.cpp integration. Names and flags below are proposed. Each script should have one obvious command, a short explanation, a meaningful exit code and its own `main`; it should not import another runnable example or depend on `experiments/` or `tests/`.

| Proposed entry point | What it verifies | Mode / prerequisites | Source to adapt |
|---|---|---|---|
| `examples/basic_question.py --real` | Ask one simple named choice question (for example, which fruit was mentioned), load the configured model and print the actual validated answer. | **Real model**; compatible Laya-MLX environment. One prediction; no mock fallback. | `experiments/laya_choice.py` and its tiny input, stripped of report generation. |
| `examples/building_block.py --real` | Run one current question block, showing its actual result and decision. Use the requested-action block with a simple prompt and its production defaults. | **Real model**; no Node requirement for this block. A legitimate rule rejection is shown as such. | Block invocation pattern from `experiments/block_method_selection.py`/`block_v2_ticket_defaults.py`; current public requested-action contract. Do not copy permissive demo rules. |
| `examples/typescript_extract.py` | Outline a tiny synthetic TypeScript file and extract a known function with exact source/locations. | **No model**; Node and built helper. | Deterministic extraction portions of the current file example and connected-project fixture. |
| `examples/flow_sanity.py` | Exercise the current method-context flow through real blocks and extraction with explicitly scripted SDK answers. | **Offline SDK simulation**; Node and built helper. Demonstrates harness behavior, not live model readiness. | Current first-flow integration-test pattern; replace the sandbox entry point in `examples/file_method_context.py`. |
| `examples/hook_sanity.py` | Replay one synthetic UserPromptSubmit through the current gateway, checking protocol JSON and stderr separation. | **Offline SDK simulation**; Node, temporary project/state/logs. Does not install a Claude hook. | `examples/user_prompt_submit_scenario.py` and `experiments/hook_user_prompt_submit.py`, rebuilt around the current gateway without report wrappers. |
| `examples/hook_real.py --real` | Send one synthetic event through the current gateway using the actual model, showing returned context or the real no-op/error outcome. | **Real model** plus Node. One invocation may make multiple block/model calls. No automatic settings registration. | `experiments/hook_user_prompt_submit.py`; small source/event only, no trace/report pipeline. |
| `examples/token_budget.py --real --model-path /absolute/checkpoint --case valid` | Check a valid strict request or an explicit oversized-input refusal before inference; print the measured result and prediction count. | **Real tokenizer/model capability**; an existing compatible local checkpoint. One script with `--case valid` or `--case oversized-state`, rather than five near-duplicate launchers. | `experiments/tokenizer_smoke/run.py` and `examples/budget_diagnostics.py`. |

Do not add wrappers for tasks that already have clear commands: document `system-one-harness doctor`, `logs --last 20` and `logs --ui` in the example README's related commands. Keep model-comparison matrices, campaign scoring, repeated runs, exports and large datasets in the experiments project.

### Required examples README and script behavior

`examples/README.md` should start with “These are small sanity checks, not benchmarks.” Give a copyable command, purpose, prerequisites, expected successful output/exit status, whether a model is real or simulated, and any log/state/model-loading side effects for each script. Group the commands into no-model checks and explicit real-model checks.

Real-model scripts must never silently replace a failed load with a mock. `--help` should not load a model. Print the requested configuration separately from observed results; a structurally valid response establishes that the adapter/model responded, not model accuracy, token savings or Claude delivery. For live block/hook examples, report accepted/rejected/no-op outcomes honestly; a technical error is not a successful no-op. Missing prerequisites must produce an actionable nonzero exit.

Use tiny synthetic inputs and example-owned fixtures, or materialize them in a temporary directory. Keep mock support in at most one small private helper if needed; it is not an eighth runnable example. Isolate synthetic session state and logs from normal user runs, including child gateway processes, using the existing test/embedding seams. Do not invent a new persistent state mechanism or silently modify Claude settings. Real model loading may use/download a configured checkpoint; the local-checkpoint budget example must retain its explicit local-only behavior.

## 5. Implementation order after review

1. **Preserve and classify.** Recheck the source revision, full tracked inventory and destination completeness; disposition all deferred checks and research evidence. Keep runtime defaults and selected-profile fixtures unchanged.
2. **Decouple active consumers.** Relocate the TypeScript invoice reference and test-owned example helpers; update the basic-question/diagnostic tests before introducing real calls at those example paths. Preserve all existing assertions that still protect harness behavior.
3. **Write current documentation.** Correct the SQLite and module-path errors; create the log data-model and input-budget guides; consolidate setup/compatibility notes; remove historical links. Maintain executable hook recipe coverage.
4. **Build the seven examples and README.** Extract minimal scenarios using current entry points. Validate offline paths and command/help behavior with meaningful smoke checks. Run real-model checks only in the supported environment and report any still-unverified cases.
5. **Remove approved tracked research and obsolete documents.** Remove `experiments/`, moved research reports and their evidence, and superseded migration/cleanup docs. Move/remove deferred experiment tests only according to their reviewed ownership map. Update links, `.gitignore` commentary, pytest exclusions, packaging inputs and test documentation where needed.
6. **Validate and finish this PR.** Run the checks below on the resulting tree. Update PR #58 around the final scope, remove this temporary proposal once implemented, and retain durable usage/contracts. Do not merge until review and final-commit CI are complete.

## 6. Acceptance checks and risks

| Check | Required result |
|---|---|
| Documentation inventory | Every current docs path above has a deliberate disposition. Guides describe present behavior; no obsolete module paths, PR23/Phase20 instructions or backup-link dependency remains. |
| Links and command examples | Relative links/anchors resolve; intended commands exist; hook registration/input/output examples still pass their behavioral check. Research destinations are verified or links are omitted honestly. |
| Research ownership | No tracked `experiments/` remains in the final harness tree. Necessary research/provenance is accounted for at its destination. Active test fixtures and production question/rule defaults remain intact. |
| Deferred coverage | All 105 recorded test-function definitions have an explicit move/retain/replace disposition. Account for the separate browser check and fixtures too. Do not claim a skipped test passed or weaken an oracle to make cleanup green. |
| Examples | Seven runnable entry points, no more than ten. All are documented; offline/live distinction is explicit; scripts have no experiment/test-directory dependency and do not import other entry points. |
| Isolation and real execution | Offline CI never loads a real SDK/model. Live checks have real results or an explicit unverified status; no fake “model is working” output. Temporary project/session/log artifacts are cleaned up. |
| Repository verification | Build TypeScript; run full active Python and TypeScript suites, wheel/package checks, relevant example smoke checks, link/reference audits and Git hygiene against the final commit. Any changed test count must be explained by the disposition map. |

Highest risks are losing a test-owned fixture, replacing a mocked example that CI executes with an automatic live call, and deleting research/deferred coverage before confirming ownership. Documentation consolidation is low risk when links and executable examples are checked. Rewriting the real-model examples has medium compatibility risk because old experiment wrappers assume obsolete logging and sandbox behavior; extraction should be verified against current production APIs rather than copied wholesale.

Validation for **this proposal only**: source/document review, tracked-file counts, reference searches, deferred-function inventory and an exact comparison of the two invoice fixture files. No experiment/model execution or deletion was performed. Runtime test success will be assessed during implementation, not inferred from this plan.
