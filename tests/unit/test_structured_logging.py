"""The local log contract, context and coordinated file writes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

import log_utils
from soh_logging import (ComponentName, ComponentRef, ComponentType, FlowName,
                         HookEvent, LogEvent, LogLevel, LogWriter, WriterConfig,
                         bind, capture_records, current_context, flow_scope,
                         invocation, set_session)


def _records(root):
    return [json.loads(line) for path in sorted(root.glob('*/*.jsonl'))
            for line in path.read_text().splitlines()]


def _writer(root, *, wait=0.05):
    return LogWriter(WriterConfig(root, lock_wait_seconds=wait))


def _emit(writer, **details):
    return writer.emit(LogLevel.INFO,
                       ComponentRef(ComponentType.GATEWAY, ComponentName.HOOK_GATEWAY),
                       LogEvent.GATEWAY_STARTED, 'Gateway started.', details)


def test_complete_schema_and_hourly_utc_file(tmp_path):
    root = tmp_path / '.logs'
    _emit(_writer(root), prompt='Hello\nworld', count=2, nested={'ok': True})
    paths = list(root.glob('*/*.jsonl'))
    assert len(paths) == 1
    record, = _records(root)
    assert set(record) == {'schema_version', 'timestamp', 'level', 'message', 'event',
                           'component_type', 'component', 'hook_event', 'session_id',
                           'correlation_id', 'flow_name', 'pid', 'details'}
    assert record['schema_version'] == 1
    assert record['timestamp'].endswith('Z')
    assert re.fullmatch(r'\d{4}-\d\d-\d\d', paths[0].parent.name)
    assert re.fullmatch(r'\d\d-00\.jsonl', paths[0].name)
    assert paths[0].parent.name == record['timestamp'][:10]
    assert paths[0].name[:2] == record['timestamp'][11:13]
    assert record['pid'] == os.getpid()
    assert record['component_type'] == 'gateway'
    assert record['component'] == 'hook_gateway'
    assert record['level'] == 'INFO' and record['event'] == 'gateway_started'
    assert all(record[name] is None for name in ('session_id', 'correlation_id',
                                                 'hook_event', 'flow_name'))
    assert record['details'] == {'prompt': 'Hello\nworld', 'count': 2,
                                 'nested': {'ok': True}}
    assert len(paths[0].read_text().splitlines()) == 1


@pytest.mark.parametrize('stamp', ['2024-02-29T23:59:59+00:00',
                                   '2024-03-01T00:00:00+00:00',
                                   '2025-01-01T09:00:00+00:00'])
def test_utc_boundary_paths_use_the_record_timestamp(tmp_path, monkeypatch, stamp):
    import soh_logging.writer as writer_module
    captured = datetime.fromisoformat(stamp)

    class FrozenDateTime:
        @staticmethod
        def now(zone):
            assert zone is timezone.utc
            return captured

    monkeypatch.setattr(writer_module, 'datetime', FrozenDateTime)
    _emit(_writer(tmp_path / '.logs'))
    path, = (tmp_path / '.logs').glob('*/*.jsonl')
    assert path.parent.name == stamp[:10]
    assert path.name == f'{stamp[11:13]}-00.jsonl'
    assert _records(tmp_path / '.logs')[0]['timestamp'].startswith(stamp[:19])


def test_context_propagates_and_resets_across_component_scopes(tmp_path, monkeypatch):
    monkeypatch.setattr(log_utils, 'LOG_ROOT', tmp_path / '.logs')
    from soh_logging import component_scope, current_component

    @component_scope(ComponentType.DISPATCHER, ComponentName.HOOK_DISPATCHER)
    def dispatched():
        from hooks.gateway import _default_log
        _default_log('INFO', 'hook_dispatcher_started')

    @flow_scope(FlowName.FIRST_METHOD_CONTEXT)
    def flowing():
        assert current_context().flow_name == FlowName.FIRST_METHOD_CONTEXT
        from hooks.gateway import _default_log
        _default_log('INFO', 'flow_finished')
        log_utils.log('INFO', 'extractor', 'request_started')

    with invocation('inv-1', HookEvent.USERPROMPTSUBMIT):
        log_utils.log('INFO', 'hook_gateway', 'gateway_started')
        set_session('session-1', correlation_id='inv-1')
        dispatched()
        flowing()
        assert current_component() is None
        log_utils.log('INFO', 'hook_gateway', 'gateway_finished')
    assert current_context().session_id is None
    rows = _records(tmp_path / '.logs')
    assert [r['component'] for r in rows] == [
        'hook_gateway', 'hook_dispatcher', 'first_method_context',
        'extractor', 'hook_gateway']
    assert {r['correlation_id'] for r in rows} == {'inv-1'}
    assert rows[0]['session_id'] is None
    assert {r['session_id'] for r in rows[1:]} == {'session-1'}
    assert {r['hook_event'] for r in rows} == {'UserPromptSubmit'}
    assert [r['flow_name'] for r in rows] == [None, None, 'first_method_context',
                                             'first_method_context', None]


def test_real_gateway_dispatcher_flow_and_library_records_share_context(tmp_path):
    root = tmp_path / '.logs'
    script = '''
import sys
from hooks.gateway import main
from hooks.dispatcher import dispatch_hook
from hooks.contracts import Noop
from soh_logging import ComponentName, ComponentType, FlowName, component_scope, flow_scope
from log_utils import log

@flow_scope(FlowName.FIRST_METHOD_CONTEXT)
def flow():
    log('INFO', 'block_runtime', 'prepared')
    log('INFO', 'laya', 'prediction_started')
    log('INFO', 'extractor', 'request_started')
    return Noop('synthetic')

@component_scope(ComponentType.DISPATCHER, ComponentName.USER_PROMPT_SUBMIT)
def route(envelope, args, logger):
    logger('INFO', 'event_dispatcher_started')
    return flow()

def dispatch(payload, args, invocation_id, logger):
    return dispatch_hook(payload, args, invocation_id, logger,
                         dispatchers={'UserPromptSubmit': route})

raise SystemExit(main(['--hook', 'UserPromptSubmit'], dispatcher=dispatch))
'''
    payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'session-7',
               'cwd': str(tmp_path), 'transcript_path': str(tmp_path / 'synthetic.jsonl')}
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'),
           'SOH_LOG_ROOT': str(root)}
    result = subprocess.run([sys.executable, '-c', script], input=json.dumps(payload),
                            text=True, capture_output=True, env=env, timeout=10)
    assert result.returncode == 0 and result.stdout == ''
    assert result.stderr == 'laya hook noop: synthetic\n'
    records = _records(root)
    by_event = {row['event']: row for row in records}
    correlation = by_event['gateway_started']['correlation_id']
    assert correlation and {r['correlation_id'] for r in records} == {correlation}
    assert {r['hook_event'] for r in records} == {'UserPromptSubmit'}
    assert by_event['gateway_started']['session_id'] is None
    assert all(r['session_id'] == 'session-7' for r in records
               if r['event'] in {'hook_dispatcher_routed', 'event_dispatcher_started',
                                 'prepared', 'prediction_started', 'request_started',
                                 'gateway_finished'})
    assert by_event['hook_dispatcher_routed']['component'] == 'hook_dispatcher'
    assert by_event['event_dispatcher_started']['component'] == 'user_prompt_submit'
    for event, component in [('prepared', 'block_runtime'),
                             ('prediction_started', 'laya'),
                             ('request_started', 'extractor')]:
        assert by_event[event]['component'] == component
        assert by_event[event]['flow_name'] == 'first_method_context'
    assert by_event['gateway_finished']['flow_name'] is None


def test_context_resets_after_exception_and_nested_invocation():
    with invocation('outer', HookEvent.POSTCOMPACT):
        set_session('old', correlation_id='outer')
        with pytest.raises(RuntimeError):
            with invocation('inner', HookEvent.USERPROMPTSUBMIT):
                assert current_context().session_id is None
                with bind(flow_name=FlowName.FIRST_METHOD_CONTEXT):
                    raise RuntimeError('done')
        assert current_context().correlation_id == 'outer'
        assert current_context().session_id == 'old'
        assert current_context().flow_name is None
    assert current_context().correlation_id is None


def test_nested_capture_restores_sinks_and_snapshots_details(tmp_path):
    writer = _writer(tmp_path / '.logs')
    first, second = [], []
    details = {'nested': [1]}
    with capture_records(first.append):
        _emit(writer, item=details)
        with pytest.raises(RuntimeError), capture_records(second.append):
            _emit(writer, item=details)
            raise RuntimeError('exit')
        _emit(writer)
    details['nested'].append(2)
    assert len(first) == 2 and len(second) == 1
    assert first[0]['details']['item'] == {'nested': [1]}
    assert not (tmp_path / '.logs').exists()


def test_serialization_preserves_siblings_and_diagnoses_bad_values(tmp_path):
    cyclic = []
    cyclic.append(cyclic)
    class BadRepr:
        def __repr__(self):
            raise ValueError('broken repr')
    details = {'ok': {'number': 4}, 'nonfinite': float('nan'),
               'cycle': cyclic, 'bad': BadRepr()}
    _emit(_writer(tmp_path / '.logs'), **details)
    actual = _records(tmp_path / '.logs')[0]['details']
    assert actual['ok'] == {'number': 4}
    assert actual['cycle'] == ['<cycle>']
    assert 'nonfinite' in ' '.join(actual['_serialization_errors'])
    assert 'bad' in ' '.join(actual['_serialization_errors'])


def test_large_detail_is_not_truncated(tmp_path):
    prompt = 'synthetic prompt ' * 40000
    _emit(_writer(tmp_path / '.logs'), prompt=prompt)
    assert _records(tmp_path / '.logs')[0]['details']['prompt'] == prompt


def test_exception_and_common_key_collision(tmp_path):
    try:
        raise ValueError('bad value')
    except ValueError as exc:
        _emit(_writer(tmp_path / '.logs'), error=exc,
              session_id='untrusted', level='ERROR', event='untrusted')
    actual = _records(tmp_path / '.logs')[0]
    assert actual['session_id'] is None and actual['level'] == 'INFO'
    assert actual['event'] == 'gateway_started'
    assert actual['details']['session_id'] == 'untrusted'
    assert actual['details']['error']['type'] == 'ValueError'
    assert 'raise ValueError' in actual['details']['error']['traceback']


def test_invalid_enums_and_message_never_write(tmp_path):
    writer = _writer(tmp_path / '.logs')
    ref = ComponentRef(ComponentType.GATEWAY, ComponentName.HOOK_GATEWAY)
    with pytest.raises(ValueError):
        writer.emit(LogLevel.INFO, ref, LogEvent.GATEWAY_STARTED, '  ')
    with pytest.raises(ValueError):
        writer.emit(LogLevel.INFO, ref, 'invented', 'hello')
    with pytest.raises(ValueError):
        writer.emit(LogLevel.INFO, ComponentRef('gateway', None),
                    LogEvent.GATEWAY_STARTED, 'hello')
    assert not (tmp_path / '.logs').exists()


def test_warn_alias_is_canonical(tmp_path, monkeypatch):
    monkeypatch.setattr(log_utils, 'LOG_ROOT', tmp_path / '.logs')
    log_utils.log('WARN', 'hook_gateway', 'gateway_error')
    assert _records(tmp_path / '.logs')[0]['level'] == 'WARNING'


def test_append_and_permissions_and_no_cleanup(tmp_path):
    root = tmp_path / '.logs'
    writer = _writer(root)
    _emit(writer, index=0)
    old = root / '2001-01-01' / '00-00.jsonl'
    old.parent.mkdir()
    old.write_text('{"old":true}\n')
    _emit(writer, index=1)
    now = [r for r in _records(root) if r.get('event') == 'gateway_started']
    assert [r['details']['index'] for r in now] == [0, 1]
    assert old.exists()
    assert root.stat().st_mode & 0o777 == 0o700
    assert (root / '.write.lock').stat().st_mode & 0o777 == 0o600
    path = next(p for p in root.glob('*/*.jsonl') if p != old)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('target', ['lock', 'file'])
def test_symlink_destination_is_rejected(tmp_path, target):
    root = tmp_path / '.logs'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.write_text('unchanged')
    if target == 'lock':
        (root / '.write.lock').symlink_to(outside)
    else:
        now = datetime.now(timezone.utc)
        day = root / now.strftime('%Y-%m-%d')
        day.mkdir()
        (day / now.strftime('%H-00.jsonl')).symlink_to(outside)
    with pytest.raises(OSError):
        _emit(_writer(root))
    assert outside.read_text() == 'unchanged'


def test_lock_timeout_is_bounded_and_followup_can_write(tmp_path):
    import fcntl
    root = tmp_path / '.logs'
    root.mkdir()
    with (root / '.write.lock').open('w+') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(OSError, match='lock timeout'):
            _emit(_writer(root, wait=0.01))
        fcntl.flock(held, fcntl.LOCK_UN)
    _emit(_writer(root))
    assert len(_records(root)) == 1


def test_partial_write_completes_and_write_failure_releases_lock(tmp_path, monkeypatch):
    import soh_logging.writer as writer_module
    original = writer_module.os.write
    calls = 0

    def short_write(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(fd, data[:max(1, len(data) // 2)])
        return original(fd, data)

    monkeypatch.setattr(writer_module.os, 'write', short_write)
    root = tmp_path / '.logs'
    _emit(_writer(root), body='x' * 1000)
    assert calls >= 2 and _records(root)[0]['details']['body'] == 'x' * 1000

    def fail_write(fd, data):
        raise OSError('synthetic disk failure')

    monkeypatch.setattr(writer_module.os, 'write', fail_write)
    with pytest.raises(OSError, match='synthetic disk failure'):
        _emit(_writer(root))
    monkeypatch.setattr(writer_module.os, 'write', original)
    _emit(_writer(root))
    assert len(_records(root)) == 2


def test_multiple_processes_append_complete_records(tmp_path):
    root = tmp_path / '.logs'
    script = '''
import sys
from pathlib import Path
from soh_logging import ComponentName, ComponentRef, ComponentType, LogEvent, LogLevel, LogWriter, WriterConfig
w = LogWriter(WriterConfig(Path(sys.argv[1]), lock_wait_seconds=1))
for i in range(20):
    w.emit(LogLevel.INFO, ComponentRef(ComponentType.GATEWAY, ComponentName.HOOK_GATEWAY),
           LogEvent.GATEWAY_STARTED, 'Gateway started.', {'worker': sys.argv[2], 'i': i, 'body': 'x'*3000})
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}
    workers = [subprocess.Popen([sys.executable, '-c', script, str(root), str(n)],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
               for n in range(4)]
    for worker in workers:
        _, stderr = worker.communicate(timeout=15)
        assert worker.returncode == 0, stderr.decode()
    records = _records(root)
    assert len(records) == 80
    assert {(r['details']['worker'], r['details']['i']) for r in records} == {
        (str(worker), i) for worker in range(4) for i in range(20)}


def test_failed_io_is_best_effort_except_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(log_utils, 'LOG_ROOT', tmp_path / 'blocked')
    (tmp_path / 'blocked').write_text('file')
    assert log_utils.log('INFO', 'hook_gateway', 'gateway_started') is None
    with pytest.raises(OSError):
        log_utils.log('INFO', 'hook_gateway', 'gateway_started', raise_on_error=True)


def test_no_implicit_file_creation_until_write(tmp_path):
    writer = _writer(tmp_path / '.logs')
    assert not writer.config.root.exists()


def test_hygiene_rejects_nested_generated_logs_and_allows_fixtures():
    from scripts.check_repository_hygiene import prohibited
    assert prohibited('.logs/2026-11-07/09-00.jsonl')
    assert prohibited('nested/.logs/.write.lock')
    assert not prohibited('tests/fixtures/example.jsonl')


def test_hygiene_checker_rejects_force_added_logs_in_index_and_tree(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    generated = repo / 'nested' / '.logs' / '2026-11-07' / '09-00.jsonl'
    generated.parent.mkdir(parents=True)
    generated.write_text('{"synthetic":true}\n')
    (repo / '.gitignore').write_text('.logs/\n')
    subprocess.run(['git', 'add', '.gitignore'], cwd=repo, check=True)
    subprocess.run(['git', 'add', '-f', str(generated.relative_to(repo))], cwd=repo, check=True)
    checker = Path(__file__).resolve().parents[2] / 'scripts' / 'check_repository_hygiene.py'
    indexed = subprocess.run([sys.executable, str(checker)], cwd=repo,
                             text=True, capture_output=True)
    assert indexed.returncode == 1 and 'nested/.logs/' in indexed.stderr
    assert 'synthetic' not in indexed.stderr
    subprocess.run(['git', '-c', 'user.name=SOH test', '-c', 'user.email=soh@example.invalid',
                    'commit', '-qm', 'synthetic'], cwd=repo, check=True)
    outgoing = subprocess.run([sys.executable, str(checker), '--tree', 'HEAD'], cwd=repo,
                              text=True, capture_output=True)
    assert outgoing.returncode == 1 and 'nested/.logs/' in outgoing.stderr
