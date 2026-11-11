# Context scope

Classifies which method relationships answering a user prompt calls for
understanding. It runs one shared choice question, enriches its response once,
and applies one named acceptance rule through the existing V2 runtime.

## Contract

Input is `{"prompt": "...", "requested_action": "explain"}`. `prompt` is a
nonblank string. `requested_action` is optional: `explain`, `generate_tests`,
`modify_code`, `undecided`, or null. Unknown keys and invalid values fail before
model loading. Valid prompt whitespace and Unicode are preserved.

With no action, null, or undecided, the SDK receives the original prompt unchanged.
With a known action, it receives Unicode-preserving JSON containing exactly the
prompt and requested_action. The action supplies context; it does not determine
scope. This block never calls the requested-action block and works independently.

Choices:

- `method_only`: the selected method's own implementation and behavior.
- `called_methods`: the selected method and at least one method it calls.
- `calling_methods`: the selected method and at least one caller.
- `both_directions`: the selected method, callers and called methods.

These are relationship directions, not levels of explanation length. “Detailed”
alone does not mean broader context. Calls inside callbacks can still matter.
Called methods can be in the same file or another file; neither scope means
retrieve every reachable method or recurse without limits.

## Calling it

```python
from building_blocks.questions.context_scope.block import run

# Supply your invocation's existing RunConfig and AgentRegistry.
result = run(
    {"prompt": "Where is calculateTotal used during checkout?",
     "requested_action": "explain"},
    config=config,
    registry=registry,
)
print(result.data["scope"])
print(result.decisions["context_scope"])
```

The standard BlockResult contains `data={"scope": "calling_methods"}` for an
accepted example and a decision with answer, decided and reason. Valid predictions
rejected by the rule have completed status, `data.scope="undecided"`, and the
existing rule rejection reason in decisions.context_scope. Top-level reason is
null for completed runs. Never treat undecided as method_only automatically.

The default `context_scope_default` rule requires answer_confidence >= 0.5 AND
probability_gap >= 0.15. These are provisional thresholds, not calibrated claims
about this task. The shared question is defaults/questions/context_scope.json;
the policy is defaults/rules/context_scope_default.json.

## Overrides and extension points

```python
result = run(
    {"prompt": "Explain this method and its helpers."},
    config=config, registry=registry,
    rule_overrides={"context_scope": "method_selection_default"},
)
```

The example uses an existing compatible choice policy. A caller may supply its
own named policy through `catalog=`. Each override replaces the entire policy.
`question_overrides={"context_scope": complete_body}` or a shared question
reference may change wording/descriptions but must preserve choice type and
exactly the four stable IDs. It does not merge individual properties.

`prepare_input=` can deliberately replace text preparation while returning
PreparedInput with nonblank text and the same question contract. Generic and
block-specific validation still run. Reducer output does not depend on custom
state. Explicit SkipBlock gives skipped status, its reason, empty decisions and
data.scope=undecided without inference.

Invalid input/criteria use BlockInputError; invalid configuration or mismatched
rule type uses BlockConfigurationError. Wrong-type rules log rule_type_mismatch.
ResponseError, SDK/loader errors and TimeoutError retain existing semantics.
There is no retry or fallback model. Unexpected output uses BlockExecutionError.

## Evidence and limitations

Existing logs contain submitted payload, question, config/rule, raw response,
enriched gap and final decision. BlockResult stays compact. The block reads
package resources only; it does not resolve prompt files, inspect an AST, retrieve
callers/callees, attach source, or change hook behavior.

It predicts requested scope from prompt evidence. It cannot prove the actual code
is sufficient or relationships exist. Out-of-scope and context-free requests may
be confidently misclassified; confidence rejection is not an unknown detector.
Synthetic SDK fixtures prove pipeline behavior, not live Laya accuracy.
The semantic corpus in tests/fixtures/context_scope_prompts.json records settled,
provisional and unresolved examples for later Mac evaluation.

The published typed-decisions MLX default has 1,024 total input tokens shared by
state, question, choices and formatting. Actual SDK/model limits and truncation
must be measured separately. The optional action JSON also consumes capacity.
This block preserves input rather than silently trimming it. Ordinary execution
does not enforce token fit; pass `strict=True` to use the shared inspection and
refusal boundary. See [input budgets](../../../../docs/input-budgets.md). A
long-string fake-agent test proves transport only, not live tokenizer parity.
