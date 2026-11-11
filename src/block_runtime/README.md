# Block runtime

`Block` composes a declaration with Python input/output validators, a preparer,
and a reducer. Config and registry are required for each run. Catalog loading
never creates an agent. Each execution predicts once and enriches each requested
answer once, then evaluates one typed rule. Rules do not change the SDK choice.

```python
from block_runtime import Block, PreparedInput
from laya.config import RunConfig
from laya.registry import AgentRegistry

def validate_input(value):
    if not isinstance(value, dict) or not isinstance(value.get('text'), str):
        raise ValueError('text required')
    return value

block = Block(
    {'name': 'risk', 'questions': {'risk': {
        'question_ref': 'churn_risk', 'rule_ref': 'ticket_churn_default'}}},
    validate_input=validate_input,
    prepare_input=lambda value, questions: PreparedInput(value['text'], questions),
    reduce=lambda decisions, state: decisions,
    validate_output=lambda data: data,
)
# A fake loader in tests; real=True only for deliberate live execution.
registry = AgentRegistry(real=True)
result = block.run({'text': 'Please cancel'}, config=RunConfig(model='model-A'),
                   registry=registry)
result = block.run({'text': 'Please cancel'}, config=RunConfig(model='model-A'),
                   registry=registry,
                   rule_overrides={'risk': 'multiple_methods_default'})
```

A declaration binds shared names to local answer keys. Exactly one of
`question_ref` and `definition` is required, along with `rule_ref`. A question
override is a complete inline question body, shared reference string, or
`{'question_ref': name}`; its type must match the declaration. Rule overrides
replace the complete default rule. Unknown override keys fail before preparation.

`PreparedInput(text, questions, state={})` contains complete local SDK bodies.
Only text/questions reach the model. `validate_prepared(prepared, validated_input)`
can enforce domain budgets and state invariants after custom preparation.
`prepare_input=` on `run` replaces preparation, while retaining validators.
Inputs, definitions, model payloads and reduction arguments are copied.
Blank text is preserved by the generic runtime; blocks may validate more strictly.

Reducers accept `(decisions, state)` and output validators may return normalized
data or None to preserve the existing data. A successful run returns
`BlockResult(status='completed', reason=None, decisions=..., data=...)`, including
when a rule rejects with an undecided answer. Preparation may raise
`SkipBlock(reason, state)` for expected no-work. The reducer receives empty
decisions and state with `reason`; output validation still runs. The result has
status `skipped` and no prediction occurs.

Configuration/input errors are ValueError-compatible `BlockConfigurationError`
and `BlockInputError`. Unexpected preparation/reduction exceptions become
`BlockExecutionError` with their cause. Prediction exceptions and `ResponseError`
retain their classes, and `TimeoutError` propagates unchanged. There is no retry.
Wrong-type rules (for example `ticket_department_default` on `churn_risk`) log
ERROR `rule_type_mismatch` with block/question/rule/types and fail before loading.
Prepared questions, rules/config and enriched metrics log at DEBUG; final decisions
log at INFO. ERROR summaries contain no prompt or source.

See sibling `method_selection_v2` for dynamic choices, candidate mapping and
outline input validation. A flow obtains an outline using a utility, then passes
it into that block; runtime execution does not independently read source files.

## Strict input budgets

Pass `strict=True` to `Block.run` to inspect the final prepared request with the
registry's supported loaded-tokenizer capability before prediction. A fitting
request keeps the same result contract. An oversized or uninspectable request
raises `InputBudgetError` with a diagnostic report, without calling inference or
the reducer. Model initialization can occur before inspection. Ordinary execution
does not guarantee token fit, and supplying a provider alone does not enable
strict mode. See [input budgets](../../docs/input-budgets.md) for supported
capabilities, refusal types and caller policy.
