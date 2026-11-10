# UserPromptSubmit: first method-context flow

The default hook routes through the gateway, common dispatcher and UserPromptSubmit dispatcher to `flows.first_method_context.handle_prompt`. The flow enriches explanation and test-generation prompts referring to one production TypeScript file.

It returns the complete selected method and one level of called-method navigation: project-relative path, signature and inclusive definition lines. Called-method bodies, recursive dependencies and incoming callers are not included.

## Try the flow

Use [plugin setup](../plugins/README.md) for normal installation and lifecycle
instructions, and the [flow guide](flows/method-context.md) for supported cases.
To exercise the gateway with real source extraction, real SQLite persistence and
scripted model answers, run `python examples/hook_sanity.py` after building the
TypeScript helper. See [all examples](../examples/README.md) for real-model checks.

Offline integration tests are under `tests/integration/flows/`; process and
session tests are under `tests/system/`. They do not establish live model accuracy
or actual Claude delivery. See the [test guide](../tests/README.md).

## Manual registration recipe

Install the package in the selected interpreter and build the pinned helper. Replace the absolute placeholder paths and supply the configured model environment. This document does not activate hooks or modify Claude settings.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "\"/absolute/system-one-harness/.venv/bin/python\" -m cli hook --hook UserPromptSubmit --repository-root \"$CLAUDE_PROJECT_DIR\" --config \"/absolute/run-config.json\" --real --deadline 10",
            "timeout": 15
          }
        ]
      }
    ]
  }
}
```

Use a synchronous command hook. This manual alternative uses the selected Python's installed CLI module. Do not register both this recipe and the plugin for the same event. The legacy `hooks/user_prompt_submit.py` forwarding wrapper remains supported, but is not needed here.

To disable it, remove this UserPromptSubmit entry while preserving unrelated settings. Omitting `--real` produces no enrichment without loading the model. Tests exercise the recipe with public block mocks, including opt-out behavior, without installing settings.

`--deadline` defaults to 10 seconds; the outer timeout should allow additional grace. Cold model loading and live latency require separate measurements in the supported environment. Source/config lookup uses the explicitly configured absolute `--repository-root`, supplied here from `CLAUDE_PROJECT_DIR`; event/process cwd never supplies a fallback.

## Context and budgets

`--max-context-chars` lowers the first flow's hard limit of 8,000 UTF-16 code units; a larger setting cannot raise that cap. The count includes headers, source, locations, separators, notes and footer. The selected implementation is never truncated. Complete call-list entries are omitted with an explicit count when needed. If no valid complete-method representation fits, no enrichment is emitted.

The source string retains its exact formatting and line endings. The flow logs that exact final enrichment through the existing logger. Decoding its `additional_context` log field reproduces the hook's returned text. Prepared context is not proof of successful emission or Claude consumption.

Legacy dependency-depth/source-budget flags remain accepted but do not request dependency-body collection in the new flow. Strict tokenizer inspection is a separate opt-in [prediction/block API](input-budgets.md); this flow does not invoke it.

## Hook protocol

Synthetic input:

```json
{"session_id":"example","transcript_path":"/repo/transcript.jsonl","cwd":"/repo","hook_event_name":"UserPromptSubmit","prompt":"Explain `src/service.ts`: how does calculate work?"}
```

Illustrative successful output:

```json
{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Enriched Prompt for System 1\nFile: src/service.ts | Method: calculate | Lines: 1–1\nfunction calculate(x: number) { return x * 2; }\n\nMethods called by calculate:\nNo project methods were reported.\n\nContext scope: selected method only."}}
```

The original user prompt is preserved. Success emits one JSON object with `hookSpecificOutput.hookEventName` and `hookSpecificOutput.additionalContext`. No selection, unsupported action and handled errors emit zero stdout bytes and exit with the existing nonblocking behavior; diagnostics remain in logs/stderr.

The gateway validates common fields, owns final serialization and isolates ordinary Python/fd1 noise from protocol stdout. It requires session_id, transcript_path, cwd, hook_event_name and prompt for UserPromptSubmit; permission_mode is optional. Transcript contents are not read. Input `hook_event_name` and output `hookEventName` intentionally have different spelling.

## Reusable components

The default flow is `flows.first_method_context`. Its selected-method presentation
uses the TypeScript outgoing-navigation utility; it does not collect dependency
bodies. `context_format.format_context` and the compatibility
`flows.file_method_context.run` API have different output contracts and are not
invoked by this hook route. The compatibility flow remains available to the
larger `live_check` diagnostic.

For persistence, read [session storage](session-state.md). For startup failures,
correlation IDs and event fields, read the [log data model](log-viewer/DATA_MODEL.md).
