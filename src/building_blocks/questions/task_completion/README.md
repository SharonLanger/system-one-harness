# Task Completion

Classifies what a subagent's final response **reports** about completion. It does not inspect files or verify the report.

```python
from building_blocks.questions.task_completion.block import run

result = run({'response': 'I created all five requested tests.'}, config=config, registry=registry)
completion = result.data['completion']
```

The model chooses only `yes` or `no`. `yes` means the response affirmatively reports completed assigned work; `no` means it does not, including partial work or an unresolved required step. The existing rule may reject a prediction, producing `undecided` in the reduced result; exceptions remain exceptions. `result.status == 'completed'` only means the classifier executed. Do not treat a `no` decision as proof that deliverables are missing.

The input must contain exactly one nonblank string field named `response`. The default preparer passes the text unchanged. The caller provides `RunConfig` and `AgentRegistry`; optional question/rule overrides use the existing block runtime. The default confidence and probability-gap thresholds are provisional and need live evaluation before production use. Input validation, output contracts and controlled model responses are exercised in `tests/unit/building_blocks/questions/test_task_completion_block.py`.
