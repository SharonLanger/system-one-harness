"""Run the requested-action block with a real model and production decision rules."""
import argparse
from dataclasses import asdict
import json
import sys
from _support import entrypoint, workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', required=True, help='Allow model loading/inference')
    parser.add_argument('--model', default='aac6fef/laya-typed-decisions-mlx')
    args = parser.parse_args()
    from building_blocks.questions.requested_action.block import run
    from hooks.gateway import isolate_stdout
    from laya.config import RunConfig
    from laya.registry import AgentRegistry
    with workspace(), isolate_stdout():
        result = run({'prompt': 'Explain how calculateTotal works.'},
                     config=RunConfig(model=args.model), registry=AgentRegistry(real=True))
    print(json.dumps(asdict(result), indent=2, ensure_ascii=False))
    correct = result.status == 'completed' and result.data.get('action') == 'explain'
    print('PASS: explain accepted' if correct else 'FAIL: explain was not accepted; inspect decisions', file=sys.stderr)
    return 0 if correct else 1


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
