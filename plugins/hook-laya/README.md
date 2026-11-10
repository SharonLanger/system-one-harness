# System One Harness — Claude Code plugin

Current version: **1.3.2** (declared in `.claude-plugin/plugin.json`).

Connects Claude Code hooks to System One Harness through the existing CLI gateway and dispatcher.

**[Install and configure the plugin →](../README.md)** — CLI installation, logging
check, enabling enrichment, disabling hooks and upgrading an existing copy.

The plugin identifier remains `hook-laya`. The primary CLI is `system-one-harness`;
`LAYA_CONTEXT_ROUTER_CLI` is a compatibility setting that must point to that
installed executable. Keep the setting's name as written in the setup guide.

## Hooks

- `UserPromptSubmit`: runs the existing prompt-context flow.
- `PostCompact`: runs `hook --hook PostCompact --log-state` after manual or automatic compaction, appending one shared `PostCompact` event and timestamp to the session’s SQLite history.

The shipped prompt hook uses `--real`. The setup guide explains how to check it
with `--observe-only` first; this does not disable `PostCompact`. Source extraction
requires the built TypeScript helper and Node on Claude's PATH. Session persistence
uses Python only.

Sensitive session state lives at `<SOH root>/.soh/session-state.sqlite3`, independent of the target project/cwd. Old JSONL is not imported or deleted. No automatic retention applies. Never commit or upload the database or its sidecars. See [session storage](../../docs/session-state.md).

To inspect hook activity, run `system-one-harness logs --ui` or
`system-one-harness logs --last 20` from your installed environment.
See the [log viewer guide](../../docs/log-viewer/README.md).

## Required versioning rule

**Every change to this plugin must include a new version in `.claude-plugin/plugin.json` in the same PR before merge.** This includes hook configuration, plugin metadata, documentation, and changes to the runtime behavior exposed by the plugin. Never publish changed plugin contents under an existing version.

Use semantic versioning: increment the patch for fixes or documentation, the minor for backward-compatible features, and the major for breaking changes. Keep this README's current version and changelog synchronized with the manifest. If additional plugin version declarations are introduced, update them together. A PR may group related edits into one release version; subsequent published changes require another version.

## Changelog

### 1.3.2

- Clarify SOH naming, CLI installation, hook enable/disable and upgrade steps.
- Link to the setup and log viewer guides; explain that prompt observe-only mode
  leaves PostCompact active. Hook commands and protocol are unchanged.
- Link maintained block contracts after removing historical research reports.

### 1.3.1

- Document System One Harness naming and the retained `LAYA_CONTEXT_ROUTER_CLI` setting. The hook command and protocol are unchanged.

### 1.3.0

- Adopt Method Selection V2's Q0 / combined-strict profile: confidence ≥ 0.70, probability gap ≥ 0.25, default temperature 0.5.
- Retain the experiment's multiple-method guard and explicit caller overrides.
- Current inputs, defaults and limitations are documented in the [method-selection contract](../../src/building_blocks/questions/method_selection_v2/README.md).

### 1.2.0

- Adopt requested-action combination #3: C1 short choices, block-default temperature 1.0, and probability gap ≥ 0.10.
- Add the four-choice `unclear` option and preserve the public `undecided` fallback; question overrides must contain all four choices.
- Preserve explicit temperature overrides and other blocks’ settings.
- Current decisions, overrides and limitations are documented in the [requested-action contract](../../src/building_blocks/questions/requested_action/README.md).

### 1.1.0

- Add `PostCompact --log-state` registration and session-state logging.
- Document mandatory version bumps for all plugin changes.

### 1.0.0

- Initial UserPromptSubmit plugin integration.


