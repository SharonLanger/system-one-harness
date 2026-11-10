# Contributing to System One Harness

Thanks for helping improve System One Harness. This is an experimental, maintainer-led project. Small, well-explained changes are easiest to review. You do not need write access to the repository: fork it, create a branch, and open a pull request.

## Before you start

Check existing issues and pull requests for related work. For a bug, open an issue with the version or commit, platform, steps to reproduce, expected behavior, and actual behavior. Include a small, sanitized example when possible. For a substantial feature or a change to public hook behavior, discuss the intended scope in an issue before implementing it.

Report suspected vulnerabilities privately using [SECURITY.md](SECURITY.md). Do not post exploit details, access tokens, private prompts, source code from another project, or raw logs in a public issue. Logs and experiment outputs may contain sensitive data.

## Set up and test

Follow the [fresh-checkout setup](README.md#set-up-a-development-checkout). Use Python 3.11 or newer and Node/npm. The offline tests use scripted Laya responses; running them does not require a model download or Apple Silicon. Real inference has separate requirements in [the Mac guide](docs/real-laya-mac.md).

From an installed development environment:

```bash
npm run build --prefix typescript-helper
python -m pytest -q
npm test --prefix typescript-helper
python scripts/check_repository_hygiene.py
```

The TypeScript build is needed for Python tests that inspect TypeScript source. See [the test guide](tests/README.md) for unit, integration, system and example test boundaries. If a check cannot run locally, say which one and why in the PR. GitHub Actions runs the checks on PRs; include the run link when useful.

## Prepare a pull request

Keep each PR focused and describe the problem, what behavior changes, and how you verified it. Add or update tests for meaningful behavior changes. Preserve existing test and experiment coverage; do not silence a failing test or remove a scenario just to make CI green. For process and hook behavior, test observable output and errors. Use temporary directories for tests that exercise real file persistence.

Link any relevant issue or design. Mention changes to CLI output, hook protocol, config formats, session storage, or TypeScript extraction because downstream users may rely on those contracts. Do not commit generated builds, model files, secrets, raw experiment reports, or debug logs. Run the repository hygiene check before pushing. Install local preventive hooks with `git config core.hooksPath .githooks`. Pre-commit scans staged Git blobs; pre-push scans outgoing trees and commits. Fetch the remote first: new branches exclude history reachable from that remote’s local tracking refs, and missing bases or shallow history fail explicitly. A clean final tree cannot excuse a database introduced and deleted earlier in a PR. See [session state](docs/session-state.md) for the sensitive-data boundary.

A maintainer reviews and decides what to merge. Review time and acceptance are not guaranteed; requests for changes are part of keeping the project reliable.
