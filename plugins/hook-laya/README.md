# hook-laya

Current version: **1.3.1** (declared in `.claude-plugin/plugin.json`).

Connects Claude Code hooks to System One Harness through the existing CLI gateway and dispatcher.

## Hooks

- `UserPromptSubmit`: runs the existing prompt-context flow.
- `PostCompact`: runs `hook --hook PostCompact --log-state` after manual or automatic compaction, appending one shared `PostCompact` event and timestamp to the session’s SQLite history.

Build the TypeScript helper with `npm --prefix typescript-helper run build`, ensure Node is on PATH, and configure `LAYA_CONTEXT_ROUTER_CLI` to point to the installed SOH executable. Sensitive session state lives at `<SOH root>/.soh/session-state.sqlite3`, independent of the target project/cwd. Session persistence uses Python only; Node is used by source extraction. Old JSONL is not imported or deleted. No automatic retention applies. Never commit or upload the database or its sidecars. See [session storage](../../docs/session-state.md).

## Required versioning rule

**Every change to this plugin must include a new version in `.claude-plugin/plugin.json` in the same PR before merge.** This includes hook configuration, plugin metadata, documentation, and changes to the runtime behavior exposed by the plugin. Never publish changed plugin contents under an existing version.

Use semantic versioning: increment the patch for fixes or documentation, the minor for backward-compatible features, and the major for breaking changes. Keep this README's current version and changelog synchronized with the manifest. If additional plugin version declarations are introduced, update them together. A PR may group related edits into one release version; subsequent published changes require another version.

## Changelog

### 1.3.1

- Document System One Harness naming and the retained `LAYA_CONTEXT_ROUTER_CLI` setting. The hook command and protocol are unchanged.

### 1.3.0

- Adopt Method Selection V2's Q0 / combined-strict profile: confidence ≥ 0.70, probability gap ≥ 0.25, default temperature 0.5.
- Retain the experiment's multiple-method guard and explicit caller overrides.
- Add the [method-selection-200 research report](../../src/building_blocks/questions/method_selection_v2/research/method-selection-200-results.md), with five-profile comparisons, noise/difficulty breakdowns and committed development evidence.

### 1.2.0

- Adopt requested-action combination #3: C1 short choices, block-default temperature 1.0, and probability gap ≥ 0.10.
- Add the four-choice `unclear` option and preserve the public `undecided` fallback; three-choice question overrides must migrate to four choices.
- Preserve explicit temperature overrides and other blocks’ settings.
- Document the [500-case results and selection](../../docs/requested-action/requested-action-500-results.md), including 26.2% overall error among accepted answers.

### 1.1.0

- Add `PostCompact --log-state` registration and session-state logging.
- Document mandatory version bumps for all plugin changes.

### 1.0.0

- Initial UserPromptSubmit plugin integration.


