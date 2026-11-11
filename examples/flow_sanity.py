"""Exercise the current method-context flow with two explicitly scripted model answers."""
import argparse
import json
from _support import PROMPT, SOURCE, ScriptedModel, entrypoint, workspace


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    from flows.first_method_context import run
    from hooks.contracts import Context
    from laya.config import RunConfig
    from laya.registry import AgentRegistry
    from log_utils import log
    fake = ScriptedModel()
    with workspace() as (project, _, records):
        result = run(PROMPT, project_root=project, config=RunConfig(),
                     registry=AgentRegistry(loader=fake.load),
                     logger=lambda level, event, **details: log(level, 'flow', event, **details))
        if not isinstance(result, Context) or SOURCE.strip() not in result.text or fake.calls != 2:
            raise RuntimeError(f'Unexpected flow result: {result}')
        print(json.dumps({'mode': 'scripted_model', 'passed': True,
                          'prediction_calls': fake.calls, 'context': result.text}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
