"""Observation is a process boundary: no dispatch, model, source or protocol output."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 65536


def run_observer(tmp_path, raw=None, *, project=None, argv=None, mode='normal'):
    records_path = tmp_path / 'observed.json'
    script = '''
import importlib.abc, json, os, sys, time
class BlockWork(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.startswith(('laya_mlx', 'mlx', 'torch',
            'hooks.dispatcher',
            'hooks.user_prompt_submit',
            'laya', 'flows',
            'building_blocks')):
            raise AssertionError('observation imported work: ' + fullname)
sys.meta_path.insert(0, BlockWork())
from hooks.gateway import main
records = []
def dispatch(*args):
    raise AssertionError('dispatcher was invoked')
def logger(level, event, **details):
    if MODE == 'noisy':
        print('python logger noise')
        os.write(1, b'native logger noise\\n')
    if event == 'hook_observed' and MODE == 'failed':
        raise OSError('cannot save observation')
    if event == 'observation_input_received' and MODE == 'failed_input':
        raise OSError('cannot save original input')
    if event == 'hook_observed' and MODE == 'deadline':
        time.sleep(2)
    records.append({'event': event, **details})
code = main(ARGS, dispatcher=dispatch, logger=logger)
open(RECORDS, 'w', encoding='utf-8').write(json.dumps(records, ensure_ascii=False))
raise SystemExit(code)
'''.replace('MODE', repr(mode)).replace('ARGS', repr(argv or [
        '--hook', 'UserPromptSubmit', '--observe-only',
        '--config', '/missing/config.json', '--repository-root', '/missing/source',
    ])).replace('RECORDS', repr(str(records_path)))
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src')}
    env.pop('CLAUDE_PROJECT_DIR', None)
    if project is not None:
        env['CLAUDE_PROJECT_DIR'] = project
    proc = subprocess.run([sys.executable, '-c', script],
        input=raw if raw is not None else json.dumps({
            'hook_event_name': 'UserPromptSubmit', 'session_id': 'one',
            'cwd': '/reported/project/src', 'prompt': 'hello',
        }), text=True, capture_output=True, timeout=5, env=env, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout == ''
    return proc, json.loads(records_path.read_text())


def event(records, name):
    return next(row for row in records if row['event'] == name)


def test_full_input_and_metadata_without_dispatch_or_optional_prerequisites(tmp_path):
    project = tmp_path / 'project'; project.mkdir()
    payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'unique',
        'cwd': '/different/worktree', 'prompt': 'שלום 😀',
        'unknown': {'nested': [True, None, {'n': 7}]}}
    raw = ' \n' + json.dumps(payload, ensure_ascii=False, indent=3) + '\n  '
    proc, records = run_observer(tmp_path, raw, project=str(project))
    original = event(records, 'observation_input_received')
    assert original['raw_input'] == raw
    assert original['raw_input_truncated'] is False
    observed = event(records, 'hook_observed')
    assert observed['raw_payload'] == payload
    assert observed['derived'] == {'project_dir': str(project)}
    assert observed['claude_project_dir'] == str(project)
    assert observed['process_cwd'] == str(tmp_path)
    assert observed['payload_cwd'] == '/different/worktree'
    assert observed['session_id'] == 'unique'
    assert observed['repository_root_argument'] == '/missing/source'
    assert observed['dispatcher_invoked'] is False
    assert observed['supported_event'] is True
    assert observed['would_invoke_dispatcher'].endswith('.dispatch_hook')
    assert observed['event_dispatcher'].endswith('.dispatch_user_prompt_submit')
    final = records[-1]
    assert final['session_id'] is None
    assert final['outcome'] == 'observed' and final['reason'] == 'observe_only'
    assert final['stdout_bytes_written'] == 0 and final['response_present'] is False
    assert not proc.stderr


@pytest.mark.parametrize('kind', ['missing', 'empty', 'relative', 'nonexistent', 'file', 'symlink'])
def test_project_statuses(tmp_path, kind):
    project = tmp_path / 'project'; project.mkdir()
    alias = tmp_path / 'alias'; alias.symlink_to(project)
    file = tmp_path / 'file'; file.write_text('x')
    value = {'missing': None, 'empty': '', 'relative': 'relative',
        'nonexistent': str(tmp_path / 'absent'), 'file': str(file), 'symlink': str(alias)}[kind]
    _, rows = run_observer(tmp_path, project=value)
    observed = event(rows, 'hook_observed')
    assert observed['claude_project_dir'] == value
    assert observed['project_dir_status'] == (
        'available' if kind == 'symlink' else 'missing' if kind == 'missing' else 'invalid')
    assert observed['derived']['project_dir'] == (str(project) if kind == 'symlink' else None)


def test_alternating_projects_and_sessions_in_same_process(tmp_path):
    a = tmp_path / 'A'; b = tmp_path / 'B'; a.mkdir(); b.mkdir()
    script = '''
import io, json, os, sys
from hooks.gateway import main
rows=[]
for index, project in enumerate(PROJECTS):
    os.environ['CLAUDE_PROJECT_DIR']=project
    sys.stdin=io.StringIO(json.dumps({'hook_event_name':'UserPromptSubmit',
        'session_id':str(index), 'cwd':project+'/src'}))
    main(['--hook','UserPromptSubmit','--observe-only'],
        logger=lambda l,e,**d: rows.append({'event':e,**d}))
open(OUTPUT,'w').write(json.dumps(rows))
'''.replace('PROJECTS', repr([str(a), str(b), str(a), str(b)])).replace(
        'OUTPUT', repr(str(tmp_path / 'rows.json')))
    proc = subprocess.run([sys.executable, '-c', script], capture_output=True,
        text=True, timeout=5, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})
    assert proc.returncode == 0 and proc.stdout == '' and proc.stderr == ''
    observed = [r for r in json.loads((tmp_path / 'rows.json').read_text()) if r['event'] == 'hook_observed']
    assert [r['derived']['project_dir'] for r in observed] == [str(a), str(b), str(a), str(b)]
    assert [r['session_id'] for r in observed] == ['0', '1', '2', '3']
    assert len({r['invocation_id'] for r in observed}) == 4


def test_unsupported_event_has_no_event_route(tmp_path):
    _, rows = run_observer(tmp_path, '{"hook_event_name":"Stop","extra":true}',
        argv=['--hook', 'Stop', '--observe-only'])
    observed = event(rows, 'hook_observed')
    assert observed['event_dispatcher'] is None and observed['supported_event'] is False
    assert observed['dispatcher_invoked'] is False


@pytest.mark.parametrize('raw,code', [('{', 'invalid_json'),
    ('{"hook_event_name":"UserPromptSubmit","x":1,"x":2}', 'invalid_json'),
    ('[]', 'invalid_object'), ('{"hook_event_name":"Stop"}', 'event_mismatch'),
    ('{}', 'invalid_field'), ('x' * (LIMIT + 20), 'input_budget_exceeded')])
def test_rejected_input_keeps_bounded_original_and_error(tmp_path, raw, code):
    _, rows = run_observer(tmp_path, raw)
    capture = event(rows, 'observation_input_received')
    assert capture['raw_input'] == raw[:LIMIT]
    assert capture['raw_input_truncated'] == (len(raw) > LIMIT)
    assert capture['input_chars_read'] <= LIMIT + 1
    assert rows[-1]['code'] == code and rows[-1]['outcome'] == 'error'
    assert not any(r['event'] == 'hook_observed' for r in rows)


@pytest.mark.parametrize('extra', [['--real'], ['--deadline', 'nan'], ['--bad']])
def test_invalid_arguments_do_not_dispatch(tmp_path, extra):
    proc, rows = run_observer(tmp_path,
        argv=['--hook', 'UserPromptSubmit', '--observe-only', *extra])
    assert 'invalid_' in proc.stderr
    assert not any(r['event'] == 'hook_observed' for r in rows)


def test_noisy_logger_cannot_contaminate_stdout(tmp_path):
    proc, rows = run_observer(tmp_path, mode='noisy')
    assert 'python logger noise' in proc.stderr and 'native logger noise' in proc.stderr
    assert rows[-1]['outcome'] == 'observed'


def test_primary_log_failure_is_error_not_observation_success(tmp_path):
    proc, rows = run_observer(tmp_path, mode='failed')
    assert 'logging failed' in proc.stderr
    assert rows[-1]['outcome'] == 'error' and rows[-1]['code'] == 'observation_log_failed'


def test_raw_input_log_failure_cannot_be_hidden_by_successful_metadata_log(tmp_path):
    proc, rows = run_observer(tmp_path, mode='failed_input')
    assert 'logging failed' in proc.stderr
    assert event(rows, 'hook_observed')['dispatcher_invoked'] is False
    assert rows[-1]['outcome'] == 'error' and rows[-1]['code'] == 'observation_log_failed'


def test_observation_logging_obeys_deadline(tmp_path):
    proc, rows = run_observer(tmp_path, mode='deadline', argv=[
        '--hook', 'UserPromptSubmit', '--observe-only', '--deadline', '.1'])
    assert 'deadline_exceeded' in proc.stderr
    assert not any(r.get('outcome') == 'observed' for r in rows)


def test_default_sink_records_complete_observation(tmp_path):
    path = tmp_path / 'debug.jsonl'
    script = '''
from pathlib import Path
import log_utils
log_utils.LOG_PATH=Path(__LOG_FILE__)
from hooks.gateway import main
raise SystemExit(main(['--hook','UserPromptSubmit','--observe-only']))
'''.replace('__LOG_FILE__', repr(str(path)))
    raw = '{ "hook_event_name": "UserPromptSubmit", "extra": "hello" }'
    proc = subprocess.run([sys.executable, '-c', script], input=raw, text=True,
        capture_output=True, timeout=5, env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})
    assert proc.returncode == 0 and proc.stdout == '' and proc.stderr == ''
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert next(r for r in records if r['event'] == 'observation_input_received')['details']['raw_input'] == raw
    assert all(r['pid'] and r['correlation_id'] for r in records)
    assert records[-1]['details']['outcome'] == 'observed'


def test_default_sink_unwritable_path_is_visible_without_protocol_output(tmp_path):
    blocker = tmp_path / 'not-a-directory'
    blocker.write_text('existing file')
    path = blocker / 'debug.jsonl'
    script = '''
from pathlib import Path
import log_utils
log_utils.LOG_PATH=Path(__LOG_FILE__)
from hooks.gateway import main
raise SystemExit(main(['--hook','UserPromptSubmit','--observe-only']))
'''.replace('__LOG_FILE__', repr(str(path)))
    proc = subprocess.run([sys.executable, '-c', script],
        input='{"hook_event_name":"UserPromptSubmit"}', text=True,
        capture_output=True, timeout=5,
        env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')})
    assert proc.returncode == 0 and proc.stdout == ''
    assert 'logging failed' in proc.stderr and 'observation_log_failed' in proc.stderr
    assert not path.exists()
