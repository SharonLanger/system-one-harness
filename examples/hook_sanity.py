"""Replay a synthetic prompt through the gateway with scripted model answers and temporary state."""
import argparse
import sys
from _support import ScriptedModel, entrypoint, replay_hook, workspace


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    from laya.registry import AgentRegistry
    fake = ScriptedModel()
    print('Offline: scripted model; stdout is the actual hook protocol response.', file=sys.stderr)
    with workspace() as (project, database, records):
        code = replay_hook(project, database, AgentRegistry(loader=fake.load),
                           model='aac6fef/laya-typed-decisions-mlx', deadline=30)
        finished = [r for r in records if r['event'] == 'gateway_finished']
        if (code != 0 or fake.calls != 2 or not database.is_file() or not finished
                or finished[-1]['details']['outcome'] != 'context'
                or not finished[-1]['details']['response_present']):
            raise RuntimeError('Hook did not emit context and save isolated session state')
    print('PASS: gateway emitted context; temporary files cleaned up.', file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
