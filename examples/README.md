# Sanity examples

These seven small scripts show one component at a time. Start with the offline
examples to check your installation, then deliberately opt into real inference.
They do not install hooks, edit Claude settings, run campaigns, or benchmark accuracy.

From the SOH checkout, activate the Python environment where you installed SOH:

```bash
python -m pip install -e .
python -m cli doctor
npm ci --prefix typescript-helper
npm run build --prefix typescript-helper
```

The TypeScript build is needed only for extraction, flow and hook examples. Real
inference also needs a compatible Laya-MLX runtime and Apple Silicon Mac; see the
[Mac setup guide](../docs/real-laya-mac.md). All scripts support `--help` without
loading a model. Use that environment's Python for every command below.

| Command | What it checks | Model boundary and expected result |
| --- | --- | --- |
| `python examples/basic_question.py --real` | Asks which fruit is in “I ate an apple.” | **Real model.** Prints its validated answer; succeeds only for `apple`. |
| `python examples/building_block.py --real` | Runs the requested-action block with production defaults | **Real model.** Prints the full block result and rules; succeeds if `explain` is accepted. A rejected or wrong answer remains visible and exits nonzero. |
| `python examples/typescript_extract.py` | Outlines and extracts a synthetic `calculateTotal` function | **No model.** Checks exact source and starting line; prints extracted code/location. |
| `python examples/flow_sanity.py` | Runs the current method-context flow, real blocks and TypeScript extraction | **Scripted model.** Checks two predictions and exact selected source in the context. Does not prove live model readiness. |
| `python examples/hook_sanity.py` | Runs a synthetic UserPromptSubmit through the gateway, dispatchers, flow and SQLite store | **Scripted model.** Stdout is the actual hook JSON; diagnostics and pass/fail go to stderr. Checks context emission and isolated session persistence. |
| `python examples/hook_real.py --real` | Runs the same synthetic hook with the real model | **Real model.** Prints actual protocol context, or empty stdout for a no-op. Reports the outcome on stderr; a no-op does not establish successful enrichment. |
| `python examples/token_budget.py --real --model-path /absolute/checkpoint --case valid` | Exercises strict input accounting with a loaded local tokenizer | **Real tokenizer/model.** Requires one prediction for a fitting request. Use `--case oversized-state` to require refusal with zero inference calls. |

The first two examples and `hook_real.py` accept `--model MODEL_ID`. Loading may
download weights or use your model cache. `token_budget.py` requires an existing
compatible typed-decisions MLX checkpoint and passes `local_files_only=True`;
it does not substitute a synthetic inspection provider. Model initialization may
still occur before a strict refusal. The ordinary examples do not claim strict
token fit; see [input budgets](../docs/input-budgets.md).

Exit **0** means the stated check completed, **1** means a failed check/runtime
error, and **2** means invalid arguments or missing opt-in. A single successful
answer is a sanity check, not an accuracy measurement. A model rejection is not a
reason to weaken production rules. The live hook uses a 120-second deadline for
cold loading; override it with `--deadline` up to 300 seconds. The installed plugin
has its own default deadline and is not changed by these scripts.

Synthetic source and any SQLite state live in a temporary directory and are
removed afterward. Structured SOH records are captured in memory rather than
mixed into your normal logs. The shared `_support.py` only supplies these fixtures,
isolation and explicit scripted answers; it is not another runnable example.

## Related commands

- `system-one-harness doctor`: interpreter, executable and log destination.
- `system-one-harness logs --last 20`: recent persisted records from normal runs.
- `system-one-harness logs --ui`: [local log viewer](../docs/log-viewer/README.md).

To actually connect Claude, follow [plugin setup](../plugins/README.md). To see
how the components fit together, read the [method-context flow](../docs/flows/method-context.md).
