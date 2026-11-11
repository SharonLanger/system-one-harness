# Configuration conventions

Define every key only once within each JSON object, including nested objects.
When changing a setting, edit its existing value rather than adding the same key
again. Identical key names in different objects are allowed.

## Model configuration

Use one model per configuration object. Replace the illustrative model ID below
with the actual model you intend to load.

```json
{
  "model": "your-model-id",
  "temperature": 0.2,
  "lang": "en"
}
```

Use separate configuration files or separate RunConfig instances for different
model runs; do not repeat the model key in one object.

## Standalone acceptance policy

Put all universal conditions in one conditions array. Every condition must pass.
Use conditions_by_type for conditions specific to a prediction type; these are
added to the universal conditions. Probability gap belongs to choice processing,
not noul processing.

```json
{
  "name": "selection-policy",
  "conditions": [
    {"field": "answer_confidence", "op": "gte", "value": 0.5}
  ],
  "conditions_by_type": {
    "choice": [
      {"field": "probability_gap", "op": "gte", "value": 0.15}
    ]
  }
}
```

The thresholds above illustrate structure, not validated accuracy guarantees.
An empty conditions array adds no universal restrictions. Do not add a second
conditions key to extend or override the first array; edit that array instead.
For question blocks, place each policy inside the existing rules_by_question
envelope, under its matching question name.

## Duplicate-key handling

Duplicate-key rejection in the general model-config and standalone-policy loaders
is deferred. Their current JSON parser silently retains the last occurrence, so
a repeated conditions key can discard earlier restrictions. Correctly authored
files must follow the unique-key convention above.

Tests for the desired general-loader rejection behavior are explicitly skipped until it is implemented. Existing duplicate-key
checks in question/block configuration remain active and unchanged. This document
does not introduce loader validation or promise that all loaders reject duplicates.
