# Private-method testing blocker

Classify whether a subagent's final response says that at least one requested test was not created because its target method is private. This is a response-only question; the block does not inspect code or verify the reported cause.

```python
from building_blocks.questions.private_method_testing_blocker.block import run

result = run(
    {"response": "I could not add the last two tests because the method is private."},
    config=config,
    registry=registry,
)
answer = result.data["private_method_testing_blocker"]
```

An accepted `yes` identifies this reported, unresolved cause. An accepted `no` means the response does not establish this cause; it does not mean the task succeeded. Rule rejection or explicit caller skip returns `undecided`, which is not a model answer. `result.status == "completed"` means the classifier ran, not that the agent completed its assignment. Invalid inputs and model failures raise existing runtime exceptions.

The default question and confidence rule are provisional. This block does not classify tests that were authored but fail to compile or run. Controlled contract tests live in `tests/unit/building_blocks/questions/test_private_method_testing_blocker.py`; live semantic accuracy remains unverified.
