# Model input budgets

Ordinary `laya.predict.predict()` validates the response but does not measure
token fit before inference. The SDK may truncate input. Use `predict_strict()`
when the caller needs a refusal instead of silent content loss:

```python
from laya.predict import predict_strict
from laya.budget import InputBudgetError

questions = {"fruit": {"type": "choice", "instructions": "Which fruit is mentioned?",
                       "criteria": {"apple": "apple", "pear": "pear"}}}
try:
    result = predict_strict("I ate an apple.", questions, real=True)
except InputBudgetError as error:
    print(error.code, error.report)  # inspect; do not silently retry without strict mode
```

This snippet deliberately loads a real model. For a runnable check against an
existing local checkpoint, use [the token-budget example](../examples/README.md).

## What is checked

Strict prediction measures the final private request with the loaded tokenizer,
using reviewed model limits in [model_limits.py](../src/laya/model_limits.py).
Accounting covers state, instructions, option text, the shared option head and
the full assembled request. Limits are tokenizer/model-specific, not character
counts. New models need an explicit reviewed profile and compatible loaded-agent
inspection; increasing a limit is not a remedy for an oversized request.

The supported typed-decisions profile has a 1,024-token full-request limit,
256-token head limit and 48-token rendered-option limit. The code-model profile
is a refused candidate, not supported strict execution. Inspection checks loaded
agent/tokenizer capabilities and rejects incompatible overrides or hooks. Static
limits and mock tests do not establish real SDK/tokenizer parity on your machine.

Passing an `inspection_provider` alone does not enable strict mode. With an
explicit registry, that registry owns model loading and inspection. Strict mode
never falls back to ordinary prediction, trims input, retries, or switches models.

## Blocks and flows

The shared `QuestionBlock` and `Block` runtimes and the `ticket_triage`,
`ticket_triage_v2`, `requested_action` and `context_scope` blocks accept
`strict=True`. A fitting request returns the ordinary result shape. The method
selection APIs and current method-context flow do not expose strict prediction;
their character/output limits are separate contracts. See the
[flow guide](flows/method-context.md).

## Refusals and caller policy

| Exception | Meaning |
| --- | --- |
| `InputTokenBudgetExceeded` | Measured content would exceed a supported boundary. |
| `InspectionUnavailable` | The requested model or loaded agent has no supported inspection capability. |
| `InspectionFailed` | Inspection could not complete reliably. |

All inherit `InputBudgetError` and carry an independent JSON-safe `report` with a
request correlation ID, model, inspection status, per-question measurements and
violations. A violation identifies its question, boundary, required amount,
limit and unit, and may name an option. Refusal occurs before **request inference**;
model loading and initialization may already have occurred. Malformed arguments,
loader failures and deadlines keep their own exception types.

Choose an explicit caller policy after inspecting that report: ask for less input,
change the task, or surface the refusal. Do not discard a constraint just to make
a check pass. Reports can contain input-derived data and should receive the same
care as [diagnostic logs](log-viewer/DATA_MODEL.md).
