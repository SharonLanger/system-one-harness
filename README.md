# System One Harness

[![Offline tests](https://github.com/SharonLanger/system-one-harness/actions/workflows/offline-tests.yml/badge.svg)](https://github.com/SharonLanger/system-one-harness/actions/workflows/offline-tests.yml)
[![Repository hygiene](https://github.com/SharonLanger/system-one-harness/actions/workflows/repository-hygiene.yml/badge.svg)](https://github.com/SharonLanger/system-one-harness/actions/workflows/repository-hygiene.yml)

System One Harness (SOH) is an experimental Python tool that chooses relevant TypeScript method context for a Claude Code `UserPromptSubmit` hook. It asks a local Laya model structured questions, applies explicit rules, and uses a Node/TypeScript helper to inspect source code. It can return context for a request such as:

```text
Explain `src/service.ts`: how does calculate work?
```

The hook emits a `hookSpecificOutput` JSON response with the selected method context, or emits no context when the request is ineligible. It does not install or enable itself in Claude Code. The project is evolving; the offline tests use scripted model responses and do not prove live model quality or compatibility with every Laya runtime.

## Requirements

- Python 3.11 or newer on macOS or Linux. The hook gateway uses POSIX timer APIs.
- Node.js and npm to build the TypeScript source helper. CI uses Node 24.
- For real inference: a separately configured `laya_mlx`/MLX environment, model access, and an Apple Silicon Mac. Installing SOH does not install Laya or download model weights. Their licenses and support terms are separate from this repository's [Apache-2.0 license](LICENSE).

## Set up a development checkout

From the repository root, in a fresh Python environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[test]'
npm ci --prefix typescript-helper
npm run build --prefix typescript-helper
python -m cli doctor
```

`doctor` checks the local Python, gateway timer, and log path. It does **not** check the model, Node build, or Claude plugin. The TypeScript build creates the helper under `build/typescript/`; rebuild it after editing `typescript-helper/src/`.

To see the CLI interface without a model:

```bash
python -m cli --help
```

For real model setup and an explicit first probe, follow [the Mac guide](docs/real-laya-mac.md). To connect the hook to Claude Code, follow [the plugin setup guide](plugins/hook-laya/README.md). The installed CLI is `system-one-harness`; `laya-context-router` remains a compatibility alias. No setup command above registers the hook or modifies Claude settings.

## Run tests

After the setup above:

```bash
python -m pytest -q
npm test --prefix typescript-helper
python scripts/check_repository_hygiene.py
```

The Python suite includes unit, integration, system, and existing active tests. See [the test guide](tests/README.md) for suite boundaries and how to find each job's results on GitHub. The experiment-derived checks in `tests/deferred/` are deliberately excluded from the ordinary test command; they are retained for later migration and are not counted as passing coverage. The TypeScript tests run separately with Node. GitHub Actions runs these checks on pull requests.

## How it works

The public hook gateway reads one JSON event from stdin. It validates the hook name and event, dispatches `UserPromptSubmit` or `PostCompact`, and writes only the hook protocol response to stdout. The current prompt flow resolves one production TypeScript file, asks the model to classify the requested action and choose a callable, then extracts bounded method context. A session store suppresses repeated enrichment until compaction. Diagnostics go to stderr and `.logs/YYYY-MM-DD/HH-00.jsonl` in UTC. Installed copies use the user-local log directory, or an absolute `SOH_LOG_ROOT` override. Session state uses Python’s standard-library SQLite at `<SOH root>/.soh/session-state.sqlite3`, shared by sessions in this installation. The database path stays anchored to the installed SOH package/checkout, independent of the target project and cwd. Logs may contain prompts, model responses, or source code, so handle them as potentially sensitive. Session state is also sensitive local data: never commit, merge, or upload its database or sidecars. SQLite starts empty; existing JSONL is neither imported nor deleted. There is no retention, pruning, automatic vacuum, encryption, or database size cap. Node remains required for TypeScript source analysis; session persistence uses Python only. See [session storage](docs/session-state.md).

For the gateway contract and manual registration, see [hook usage](docs/hook-usage.md). [Configuration conventions](docs/configuration-conventions.md) explain the question and policy JSON formats. [TypeScript helper documentation](typescript-helper/README.md) covers source parsing and extraction. [Plugin documentation](plugins/README.md) describes the optional Claude Code integration.

## Project status and community

SOH is a research prototype, not a stable published package or hosted service. Real inference currently requires local setup. Tests exercise the harness with controlled responses; they do not establish the model's answer quality. Some historical experiment checks remain deferred, and the experiments are planned for a separately reviewed move to another repository. No experiments are moved or removed by the documentation work here.

For bugs and changes, read [CONTRIBUTING.md](CONTRIBUTING.md). To report a vulnerability privately, read [SECURITY.md](SECURITY.md). Project-owned source is offered under [Apache License 2.0](LICENSE); separately obtained runtimes and model weights have their own terms.
