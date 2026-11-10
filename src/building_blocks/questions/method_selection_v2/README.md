# Method selection V2

Select one callable from a validated TypeScript outline using the shared block
runtime. This block makes a semantic choice; it does not read or execute source.

## Default profile and limitations

The default question wording is Q0, with requested temperature **0.5** when the
caller leaves it unset. A selected method must meet **confidence ≥ 0.70** and
**probability gap ≥ 0.25**, plus the separate multiple-method guard. Explicit
caller overrides remain supported. `block.json` references definitions under
`src/defaults/questions/` and shared rules under `src/defaults/rules/`.

Development evaluation on 120 inputs produced 65 correct picks, one wrong pick,
19 missed targets out of 84 valid targets, and 35 correct refusals out of 36
no-single-target requests. Pick precision was 98.5%; overall correctness including
refusals was 83.3%. These observations apply to those exact settings and data;
held-out validation is pending and stricter rules can miss valid targets.
Independent default-profile expectations remain in the production test fixtures.

## Input and runtime contract

Supply `{"text": string, "source_outline": outline}` to `run`, together with
explicit `config` and `registry`. `source_outline` must be the full validated
TypeScript outline, including schema version, source identity, ranges,
locations, diagnostics and limitations. The block performs no filesystem reads.

```python
from building_blocks.typescript import outline, extract_callable
from building_blocks.questions.method_selection_v2.block import run
source = outline("/project/src/invoice.ts")  # deterministic utility
selection = run({"text": "Explain calculateTotal", "source_outline": source},
                config=config, registry=registry)  # one model request
if selection.status == "completed" and selection.data["selected_id"] is not None:
    code = extract_callable(source["path"], selection.data["selected_id"])
```

Readable names/signatures and `none_or_unclear` are sent to the model; opaque
source IDs remain in `data.candidate_map` and `data.label_map`. Selection requires
an accepted method answer and an accepted `multiple_relevant=no` answer.
Rejected decisions remain `completed` with a domain reason; expected no-work
conditions return `skipped`, a nonempty reason and empty decisions.

`max_candidates=64` and `max_input_chars=16000` retain existing counting rules:
prompt length plus compact JSON question length, measured in Unicode characters.
No source/prompt truncation occurs. Empty candidates, candidate overflow, repeated
signatures and character overflow skip prediction. Invalid outlines/limits raise.

`rule_overrides`, `question_overrides`, `prepare_input`, and `catalog` are explicit
per-call options. Custom preparation receives validated input and copied question
definitions and must return `PreparedInput(text, questions, state)`. Preserve
candidate and label maps in state; source identity and budgets are revalidated.
To read a file during preparation, use a thin adapter that calls `outline` before
passing this input contract. Original `method_selection.block.run(text, outline,
...)` remains the compatibility API returning its original dictionary shape.

The compatibility adapter is migrated for versioned compiler outlines. Older
unversioned abbreviated outlines continue through `method_selection.block.run_legacy`,
which remains callable explicitly for comparison or rollback. A present but invalid
`schema_version` raises through V2 validation; it never triggers legacy fallback.
Source identities are never synthesized to make an abbreviated outline look valid.
