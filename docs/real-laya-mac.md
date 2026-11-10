# First real Laya check on a Mac

Phase20 real compatibility is still pending. These commands prepare a supported
Apple Silicon Mac for a first explicit real call. They do not activate a hook or
change Claude settings. Use a Python3.11+ environment in which your supplied
`laya_mlx` SDK and its compatible MLX dependencies are installed. The repository
does not guess an SDK package source/version or install model weights. The default
checkpoint comes from the supplied adapter; availability and live compatibility
have not yet been confirmed.

From your checkout, with that environment activated:

```bash
python -m pip install -e '.[test]'
python -m playwright install chromium
npm --prefix typescript-helper ci --ignore-scripts --no-audit --no-fund
npm --prefix typescript-helper run build
npm --prefix typescript-helper test
python -m pytest -q
python -m live_check --help
```

The checks above use mocks at the SDK boundary. They do not import Laya or infer.
The next command explicitly loads the default
`aac6fef/laya-typed-decisions-mlx` checkpoint. Model loading may download weights
or use your existing cache and may take time. Choose a new output path outside
the checkout; an existing evidence file is never overwritten:

```bash
python -m live_check --real --output "$HOME/laya-first-result.json"
```

This uses one checkpoint with SDK-default settings. It asks an easy named
choice/noul/score question group, repeats it with a fresh invocation-owned
registry, and runs the real method-selection block/TypeScript flow against a
small disposable synthetic file. No private project source is submitted. All
raw replies and named decisions are retained; semantic answers may vary. A
valid accepted selection must return exact source; a rule rejection or no single
match may correctly return a no-op. Runtime/source/model errors fail the probe.
The result includes independently checked enrichment arithmetic, extraction,
accepting/rejecting rule outcomes, and best-effort public attributes read from
the actual loaded agent. Missing metadata remains an evidence gap. A fresh
registry is tested here; a fresh OS invocation still needs a second command with
a different output filename.

Exit0 means this probe passed. Exit1 means compatibility/processing failed;
inspect the `error` and actual raw response before changing anything. Exit2
means a prerequisite/argument/output error blocked execution. `--real` and an
Apple Silicon Mac are required; there is no mock fallback. Do not adjust rules,
fixtures or schema merely to make a surprising answer pass. Keep the complete
mock regression suite passing after any reviewed compatibility fix.

Only after you confirm a second distinct compatible checkpoint and two supported
explicit temperature values, run the optional matrix. Replace each placeholder
with your confirmed value; these are not suggested checkpoints or settings:

```bash
python -m live_check --real \
  --second-model 'CONFIRMED_SECOND_CHECKPOINT' \
  --temperature CONFIRMED_T1 --temperature CONFIRMED_T2 \
  --output "$HOME/laya-matrix-result.json"
```

`--model` overrides the first checkpoint when needed. The matrix checks both
checkpoints at defaults and both temperatures, real A/B/A and T1/T2/T1 reuse,
re-prediction after configuration switches, and a fresh registry. Two different
names can still alias the same checkpoint. Requested values are separate from
observed attributes; neither object reuse nor echoed raw labels establishes the
resolved checkpoint or effective temperatures. Those require SDK/checkpoint
metadata and independent review. Do not count this optional run as the complete
Phase20 gate without that evidence.

To return results, review the JSON first. It contains only synthetic request
text/source from this command, but actual SDK metadata/errors may contain home
paths, cache locations, URLs or other local details. Replace private path prefixes
with `<redacted>`, remove credentials if any, and preserve answer keys, types,
values/distributions, model identifiers/revisions when shareable, requested
settings, version fields, decisions and errors. Share that sanitized JSON plus
`git rev-parse HEAD`, the exact command (with private paths redacted), and
`python -m pip show laya_mlx mlx`. If the SDK is installed from source and has no
distribution version, provide its source revision. Do not share weights or private
source. Keep the original evidence locally for follow-up diagnosis.

The full Phase20 matrix, observed identity/settings review, actual fresh-process
loads, independent real-result verification and isolated Claude harness delivery
remain pending. This readiness command is not a claim of verified Laya accuracy,
calibrated policies, context delivery, or token savings.
