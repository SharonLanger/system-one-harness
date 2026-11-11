"""Replay one synthetic hook using real Laya inference; does not configure Claude."""
import argparse
import json
import sys
from _support import entrypoint, replay_hook, workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', required=True, help='Allow model loading/inference')
    parser.add_argument('--model', default='aac6fef/laya-typed-decisions-mlx')
    parser.add_argument('--deadline', type=float, default=120, help='Whole invocation seconds (0 < value <= 300)')
    args = parser.parse_args()
    if not 0 < args.deadline <= 300:
        parser.error('--deadline must be greater than 0 and at most 300')
    from laya.registry import AgentRegistry
    print('Real model: stdout is the actual hook response; a no-op emits no stdout.', file=sys.stderr)
    with workspace() as (project, database, records):
        code = replay_hook(project, database, AgentRegistry(real=True), model=args.model, deadline=args.deadline)
        finished = [r for r in records if r['event'] == 'gateway_finished']
        if not finished:
            raise RuntimeError('No completion record; inspect stderr for startup/deadline errors')
        details = finished[-1]['details']
        print(json.dumps(details, ensure_ascii=False), file=sys.stderr)
        if code != 0 or details['outcome'] == 'error':
            return 1
        print('Completed: ' + details['outcome'] + ' (no-op is not proof of enrichment)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
