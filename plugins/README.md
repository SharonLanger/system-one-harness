# System One Harness plugin setup

Connect System One Harness to Claude Code. The plugin runs the
`system-one-harness hook` command when you submit a prompt and records session
compaction through `PostCompact`. Its plugin identifier and folder remain
`hook-laya`; Laya is the model runtime, while System One Harness (SOH) is this project.

Install the CLI, register one copy of the plugin, check logging, then enable
enrichment. Installing the Python package alone does not enable any Claude hooks.
See the [plugin version and changelog](hook-laya/README.md).

## Prerequisites

- macOS/Linux and a Python 3.11+ environment with pip.
- A checkout of System One Harness that includes its CLI.
- Claude Code 2.1.280+ for the plugin-directory setting shown below.
- For enrichment: the Laya/MLX runtime and model weights in your chosen Python
  environment, plus the built TypeScript helper. See the [project setup](../README.md#requirements).
  Real Laya inference currently requires Apple Silicon. The logging check below
  does not require a model or the TypeScript helper.

## 1. Install the CLI

Use the Python interpreter you want SOH and Laya to run with. Replace the example paths
with your own; your environment can live anywhere and need not be called `.venv`.

```bash
"/path/to/environment/bin/python" -m pip install -e "/path/to/system-one-harness"
"/path/to/environment/bin/python" -m cli doctor
```

If you need a new environment, create one with `python3 -m venv /path/to/environment`
first. Install the Laya SDK into this same environment before enabling enrichment.
For an old installation, uninstall the **old distribution** with
`"/path/to/environment/bin/python" -m pip uninstall laya-context-router` before
installing SOH. The two distributions claim the same legacy console command.
The new package supplies both `system-one-harness` and the compatibility command
`laya-context-router`; use the new name in your setup.

`doctor` prints the interpreter, **CLI install location**, log directory,
and basic local checks. Copy the printed CLI path and verify it runs:

```bash
"/path/to/environment/bin/system-one-harness" doctor
```

The installed command uses the interpreter selected during installation. It does
not bundle Python or install the model. `doctor` does not check model readiness,
the TypeScript helper, or whether Claude loaded the plugin.

## 2. Register the plugin

From the repository root, copy `plugins/hook-laya`, including its hidden
`.claude-plugin` directory:

```bash
mkdir -p "$HOME/.claude/sl-plugins/hook-laya"
cp -R plugins/hook-laya/. "$HOME/.claude/sl-plugins/hook-laya/"
```

For an existing installation, back up intentional changes to its `hooks/hooks.json`
before copying: this command replaces matching files, including the hook commands.
Reapply your configuration afterward. Copying the directory contents also includes
the hidden manifest without creating a nested `hook-laya/hook-laya` directory.

Merge these entries into `env` in `~/.claude/settings.json`. Use your actual
absolute paths, with no `~` or extra quote characters inside the values:

```json
{
  "env": {
    "LAYA_CONTEXT_ROUTER_CLI": "/path/to/environment/bin/system-one-harness",
    "CLAUDE_CODE_PLUGIN_DIRS": "/Users/example/.claude/sl-plugins/hook-laya"
  }
}
```

Preserve other settings and plugins. On macOS/Linux, separate multiple plugin
paths with `:`, for example `/path/to/hook-logger:/path/to/hook-laya`.
Alternatively, keep only the CLI variable and launch
`claude --plugin-dir "/absolute/path/to/hook-laya"` for each session. Register
this plugin once; remove older manually registered SOH hooks from Claude settings
when switching to the plugin. See Claude's [plugin loading reference](https://code.claude.com/docs/en/plugins/loading)
and [environment variables](https://code.claude.com/docs/en/env-vars).

`LAYA_CONTEXT_ROUTER_CLI` is the retained compatibility key and points to the **installed SOH executable**, not a repository
folder or Python interpreter. It replaces `LAYA_CONTEXT_ROUTER_PROJECT_DIR` for
this hook. You may export it before launching Claude instead of using settings.
Settings environment values are available to the hook, not to your terminal.

## 3. Check logging first

Before launching Claude, edit the installed copy at
`~/.claude/sl-plugins/hook-laya/hooks/hooks.json`. The shipped `UserPromptSubmit`
command enables enrichment with `--real`; replace **only that command** with:

```json
"command": "\"${LAYA_CONTEXT_ROUTER_CLI}\" hook --hook UserPromptSubmit --observe-only"
```

Exit Claude and start it again in your project. Use `/hooks` to check that
both `UserPromptSubmit` and `PostCompact` are registered from this plugin, then
submit a short prompt. Read recent logs:

```bash
"/path/to/environment/bin/system-one-harness" logs --last 5
```

Look for `hook_observed` with `dispatcher_invoked: false` and `gateway_finished`
with `outcome: observed`. This mode records the hook input and project information,
calls no dispatcher or model, and sends no text to Claude. If other activity has
written more logs, increase `--last`.

This changes only the prompt hook. `PostCompact` remains active and writes session
state after compaction, without calling the model or TypeScript helper. To stop
both hooks, use [Disable the plugin](#disable-the-plugin) below.

For a browser view, run `"/path/to/environment/bin/system-one-harness" logs --ui`.
Choose filters and click **Run search**. The [log viewer guide](../docs/log-viewer/README.md)
covers complete session history, hourly files and the full JSON details.

## 4. Enable enrichment

Install the Laya/MLX runtime and model in the selected environment using the
[Mac setup guide](../docs/real-laya-mac.md). From the SOH checkout, build the source
helper and ensure Node is available on the PATH inherited by Claude:

```bash
npm ci --prefix typescript-helper
npm run build --prefix typescript-helper
```

Once the real-mode prerequisites are ready, restore the shipped prompt command:

```json
"command": "\"${LAYA_CONTEXT_ROUTER_CLI}\" hook --hook UserPromptSubmit --repository-root \"${CLAUDE_PROJECT_DIR}\" --real"
```

Restart Claude. Claude supplies `CLAUDE_PROJECT_DIR`; do not set it yourself.
It identifies the project whose code should be read, not the SOH installation.
You do not need to edit this command when starting Claude in another project.
For worktree sessions, this variable can still point to the original checkout.
Optional gateway configuration can be passed with `--config "/absolute/path/to/config.json"`.

Try a request that names an existing TypeScript file and a method, such as
`Explain src/service.ts: how does calculate work?` using names from your project.
Inspect that session's `flow_finished` and `gateway_finished` events. A registered
hook or a zero exit code alone does not prove enrichment: an ineligible request,
an already enriched session, or a failure can return no additional context.
See the [method-context flow](../docs/flows/method-context.md) for its conditions.

## Disable the plugin

- **Pause enrichment but keep logging:** switch `UserPromptSubmit` back to
  `--observe-only` as above. `PostCompact` still records session state.
- **Stop both SOH hooks:** remove this plugin's path from `CLAUDE_CODE_PLUGIN_DIRS`
  wherever you configured it, preserving other paths. If using `--plugin-dir`,
  omit that flag. Remove any separate manual SOH hook registrations too.

Exit and restart Claude, then check `/hooks` to confirm the expected commands
are present or absent. Disabling does not remove existing logs or session state.
To re-enable, restore the plugin path and the desired prompt command, then restart.

## Update an existing installation

Update the SOH checkout, reinstall it with the same Python interpreter, and rebuild
the TypeScript helper if it changed. Back up local hook configuration, copy the
plugin contents again, and reapply your configuration before restarting Claude.
A copied plugin does not update when you pull the checkout. Check its version in
`.claude-plugin/plugin.json` against the [plugin changelog](hook-laya/README.md).

## Everyday use and troubleshooting

- **Recent logs:** `system-one-harness logs` shows the last five lines;
  `logs --last 20` shows more. Use the full CLI path if it is not on your terminal's PATH.
- **Missing executable or bad interpreter:** run the configured CLI path directly.
  Reinstall with your chosen Python if the environment was moved or removed,
  then update `LAYA_CONTEXT_ROUTER_CLI` with the path printed by `doctor`.
- **Module not found:** install the project into that same Python environment.
- **No logs:** check `/hooks`, the CLI path and Claude's hook error output.
  Run `doctor` to find the actual log directory and check access. A zero exit code alone
  does not prove a log was written. Restart Claude after changing setup.
- **Python changes:** editable installation picks up source changes on the next
  hook invocation. Reinstall after entry-point or packaging changes. TypeScript
  helper source changes still require rebuilding the helper.

The offline tests cover CLI installation, hook protocol, extraction and SQLite
session state with controlled model responses. They do not verify your installed
Claude instance or real model compatibility; complete the logging and enrichment
checks above on your machine.

Use only `hook` in the hook command. `doctor` and `logs` print information intended
for a person, rather than hook responses.
