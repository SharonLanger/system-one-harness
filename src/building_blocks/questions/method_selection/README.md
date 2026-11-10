# Method selection baseline

These provisional signature-only questions establish deterministic mechanics with controlled model responses; they do not claim calibrated live selection accuracy.

`block.run(text, source_outline, config=..., registry=...)` builds `method` criteria with readable method identifiers as keys and source signatures as descriptions, plus `none_or_unclear`. For example:

```json
{"calculateInvoiceTotal":"export function calculateInvoiceTotal(invoice: Invoice): number","calculateInvoiceTotalWithAdjustment":"export function calculateInvoiceTotalWithAdjustment(invoice: Invoice, adjustment: number): number","none_or_unclear":"No single matching callable, or unclear request."}
```

A unique name is its label. Same-name candidates with different signatures use `name@line:column`; identical signatures still produce `ambiguous_candidates` before inference. Opaque snapshot IDs never appear in the model criteria. `label_map` maps accepted readable answers back to internal IDs, while `candidate_map` retains range/location/path/hash keyed by ID. Both maps remain outside inference inputs.

`multiple_relevant` retains its own named policy. Only accepted method and accepted `multiple_relevant=no` select an ID. Sentinel/rejection/multiple answers produce no selection; unknown labels fail adapter validation. Result fields are `selected_id`, `reason`, named `decisions`, `candidate_map` and `label_map`.

One shared QuestionBlock call handles both questions and interpretation. Templates are copied; policies and expected answers never enter model payloads. Empty candidates skip inference. Defaults remain 64 candidates and 16000 input Unicode characters (prompt plus compact question JSON, not tokens); overflow refuses without truncation. Thresholds are unchanged and illustrative.

See [TypeScript utility contracts](../../../../typescript-helper/README.md) for the public outline boundary and snapshot identity rules; tests are `tests/test_method_selection.py`, `tests/test_readable_method_selection.py` and `tests/test_method_ambiguity_fallback.py`.

`run` remains the dictionary-returning compatibility interface with its original
signature. Versioned outlines now execute the sibling `method_selection_v2` block;
unversioned abbreviated outlines retain `run_legacy`. Explicit unsupported schema
versions fail validation. Call `run_legacy` explicitly for the preserved original
implementation, or the sibling V2 `run` for a `BlockResult` and typed input contract.
