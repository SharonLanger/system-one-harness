# Run a real Laya model on a Mac

Real inference currently requires Apple Silicon, Python 3.11+, the
[Laya-MLX runtime](https://github.com/mizorewww/laya-mlx) and compatible model
weights. SOH does not install the SDK or download weights during package setup.
Follow the runtime's installation instructions in the Python environment you
intend to use for SOH. The default model is
`aac6fef/laya-typed-decisions-mlx`; loading can download it or use your existing cache.

## Install and ask one question

From your SOH checkout, activate that environment and run:

```bash
python -m pip install -e .
python -m cli doctor
python examples/basic_question.py --real
```

The example asks which fruit is mentioned in “I ate an apple.” It prints the
actual validated model response and succeeds only for `apple`. A failed answer
or import/load error exits nonzero; there is no mock fallback. One successful
answer establishes only that this basic call worked, not general model quality.
Use `--model MODEL_ID` to explicitly select another compatible checkpoint.

`doctor` reports the chosen interpreter and executable; it does not check SDK,
weights or Claude loading. If it fails to import `cli`, reinstall SOH with the
same Python that runs the configured executable. Follow [plugin setup](../plugins/README.md)
to select the complete executable path and connect Claude after the local checks.

## Check a block, source extraction and the hook

```bash
python examples/building_block.py --real
npm ci --prefix typescript-helper
npm run build --prefix typescript-helper
python examples/typescript_extract.py
python examples/flow_sanity.py
python examples/hook_sanity.py
python examples/hook_real.py --real
```

The block and final hook commands use the real model. The flow and hook sanity
commands use explicitly scripted answers; source extraction uses no model. All
use disposable synthetic source/state and capture their SOH logs in memory.
The real hook prints the actual context or a no-op outcome; no-op is not proof of
enrichment. None of these commands installs a hook or changes Claude settings.
See [the examples guide](../examples/README.md) for expected output and exit codes.

For strict tokenizer inspection, use an existing compatible local checkpoint:

```bash
python examples/token_budget.py --real --model-path /absolute/checkpoint --case valid
python examples/token_budget.py --real --model-path /absolute/checkpoint --case oversized-state
```

The first case requires one prediction. The second requires a measured oversized
refusal with zero request-inference calls. Loading/warmup can occur first. See
[input budgets](input-budgets.md) for supported capabilities and refusal handling.

## Larger compatibility diagnostic

`python -m live_check --help` describes the optional multi-question probe. It
checks choice/noul/score answers, registry reuse, rules and the compatibility
method-context API; it is not the current Claude hook acceptance test. It requires
Node/helper plus the real runtime. Use a new output filename outside the checkout:

```bash
python -m live_check --real --output "$HOME/laya-first-result.json"
```

Exit 0 means the probe passed, 1 indicates compatibility/processing failure, and
2 indicates an argument/prerequisite/output problem. The existing output is never
overwritten. An optional matrix accepts `--second-model` and two `--temperature`
values only after you have confirmed compatible checkpoints/settings. Requested
names and settings do not prove actual loaded model identity or effective settings;
inspect the returned metadata. A fresh registry is not a fresh process; use a
separate invocation/output path if that distinction matters.

Review diagnostic JSON before sharing it: runtime metadata/errors can reveal
local paths or URLs. Preserve meaningful answer types, distributions and model
versions while removing private details. Do not change acceptance rules merely
to make a surprising answer pass. Offline tests remain useful regression checks,
but do not establish live tokenizer parity, Claude delivery or token savings.
