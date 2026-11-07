"""Test-only launcher: real CLI/gateway, real blocks, scripted SDK, temporary state."""
import functools
import json
import os
from pathlib import Path
import runpy
import sys

from tests.support.fake_laya import ScriptedLaya
from tests.support.offline_scenarios import scenario_steps


def main():
    entry, scenario, project, state, *args = sys.argv[1:]
    project, state = Path(project), Path(state)
    if not state.is_absolute():
        raise ValueError('state must be an absolute temporary path')
    fake = ScriptedLaya(*scenario_steps(scenario, project / 'sample.ts'))
    from laya import registry, predict
    registry.real_loader = predict.real_loader = fake.load
    from hooks import session_state
    session_state.save_event = functools.partial(session_state.save_event, database_path=state / "session-state.sqlite3")
    session_state.inspect_and_save_event = functools.partial(session_state.inspect_and_save_event, database_path=state / "session-state.sqlite3")
    # A controlled pipe checkpoint documents that state can commit before
    # gateway emission. It exists only in this synthetic child launcher.
    if os.environ.get('SOH_TEST_CHECKPOINT_FD'):
        transaction = session_state.inspect_and_save_event
        def checkpoint(*a, **kw):
            decision = transaction(*a, **kw)
            os.write(int(os.environ['SOH_TEST_CHECKPOINT_FD']), b'C')
            os.read(int(os.environ['SOH_TEST_RELEASE_FD']), 1)
            return decision
        session_state.inspect_and_save_event = checkpoint
    if '--hook' in args and args[args.index('--hook') + 1] == 'PostCompact':
        from building_blocks import typescript
        def forbidden(*a, **kw):
            raise AssertionError('PostCompact source tripwire')
        typescript.outline = typescript.extract_callable = forbidden
    import log_utils
    records = []
    # Only file persistence crosses a real storage boundary. Diagnostics stay in memory.
    with log_utils.capture_records(records.append):
        if scenario == 'noise':
            original = fake.predict
            def noisy(*a, **kw):
                print('scripted SDK stdout noise')
                os.write(1, b'scripted SDK fd noise\n')
                return original(*a, **kw)
            fake.predict = noisy
        sys.argv = [entry, *(['hook'] if entry == 'cli' else []), *args]
        try:
            runpy.run_module('cli' if entry == 'cli' else 'hooks.gateway', run_name='__main__')
        except SystemExit as exc:
            code = exc.code
        else:
            code = 0
    fake.assert_complete()
    print('TEST_EVIDENCE:' + json.dumps({'calls': len(fake.calls), 'records': records}, default=str), file=sys.stderr)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
