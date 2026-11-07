"""Actual hook gateway/dispatchers with fixed public LAYA block boundaries."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest

from .conftest import PROMPT
from flows import first_method_context as flow
from hooks.contracts import Context, Failure, Noop
from hooks.dispatcher import dispatch_hook
from hooks.gateway import serialize_response
from hooks.user_prompt_submit.dispatcher import dispatch_user_prompt_submit

ROOT = Path(__file__).resolve().parents[4]

# Executed only by these tests. Both block calls are replaced before gateway use;
# registry tripwires fail closed if either replacement is accidentally removed.
BOOTSTRAP = r'''
import json, os, sys
from block_runtime import BlockResult
from building_blocks.questions.requested_action import block as action
from building_blocks.questions.method_selection_v2 import block as selection
from laya import registry, predict
import log_utils
spec = json.loads(os.environ['FIRST_FLOW_SPEC'])
from hooks import session_state
session_state.ROOT = __import__('pathlib').Path(os.environ['FIRST_FLOW_STATE_ROOT'])
# Isolate SQLite state independently of the source extraction helper.
original_inspect = session_state.inspect_and_save_event
def isolated_inspect(*args, **kwargs):
    kwargs['database_path'] = session_state.ROOT / 'session-state.sqlite3'
    return original_inspect(*args, **kwargs)
from flows import first_method_context
first_method_context.inspect_and_save_event = isolated_inspect
log_utils.LOG_PATH = __import__('pathlib').Path(os.environ['FIRST_FLOW_LOG'])
def forbidden(*args, **kwargs):
    raise AssertionError('real model/tokenizer tripwire')
registry.real_loader = predict.real_loader = predict.predict = forbidden
registry.AgentRegistry.get_agent = forbidden
def record(kind, data):
    with open(os.environ['FIRST_FLOW_CALLS'], 'a') as handle:
        handle.write(json.dumps({'kind': kind, 'data': data}) + '\n')
def fixed_action(data, **kwargs):
    record('action', data)
    if spec.get('noise'):
        print('PYTHON_BOUNDARY_NOISE')
        os.write(1, b'FD_BOUNDARY_NOISE\n')
        sys.__stdout__.write('ORIGINAL_STDOUT_NOISE\n')
        sys.__stdout__.flush()
    if spec.get('failure') == 'action':
        raise RuntimeError('planned block failure')
    if spec.get('timeout') == 'action':
        raise TimeoutError('planned deadline')
    return BlockResult('completed', None, {}, {'action': spec.get('action', 'explain')})
def fixed_selection(data, **kwargs):
    record('selection', data)
    if spec.get('failure') == 'source':
        __import__('pathlib').Path(data['source_outline']['path']).unlink()
    return BlockResult('completed', None, {}, {'selected_id': spec['selected_id'],
                       'reason': spec.get('selection_reason')})
action.run = fixed_action
selection.run = fixed_selection
if spec.get('timeout') == 'logging':
    original_log = log_utils.log
    def logging(level, component, event, **details):
        if event == 'enrichment_prepared':
            raise TimeoutError('planned logging deadline')
        return original_log(level, component, event, **details)
    log_utils.log = logging
from hooks.gateway import main
raise SystemExit(main(sys.argv[1:]))
'''


def gateway(h, tmp_path, *, prompt=PROMPT, spec=None, extra=(), event_change=None, cwd=None, real=True):
    event = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'subprocess-session',
             'cwd': str(cwd or h.root), 'transcript_path': 'unused.jsonl', 'prompt': prompt}
    event.update(event_change or {})
    fixed = {'selected_id': h.candidate['id'], **(spec or {})}
    log = tmp_path / 'gateway.jsonl'
    calls = tmp_path / 'calls.jsonl'
    env = {**os.environ, 'PYTHONPATH': str(ROOT / 'src'),
           'FIRST_FLOW_SPEC': json.dumps(fixed), 'FIRST_FLOW_LOG': str(log),
           'FIRST_FLOW_CALLS': str(calls), 'FIRST_FLOW_STATE_ROOT': str(tmp_path / 'state')}
    command = [sys.executable, '-c', BOOTSTRAP, '--hook', 'UserPromptSubmit',
               '--repository-root', str(h.root), '--deadline', '30', *(['--real'] if real else []), *extra]
    result = subprocess.run(command, input=json.dumps(event).encode(), capture_output=True,
                            env=env, cwd=cwd or ROOT, timeout=40)
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    invoked = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
    return result, records, invoked


def test_L01_adapter_log_matches_context_and_serializer(h, golden):
    context = flow.handle_prompt(h.request(), h.logger, registry=h.registry)
    assert context == Context(golden)
    assert len(h.prepared()) == 1
    assert h.prepared()[0]['additional_context'] == context.text
    assert json.loads(serialize_response(context, 'UserPromptSubmit'))['hookSpecificOutput']['additionalContext'] == context.text


def test_L02_jsonl_decoding_preserves_exact_source_bytes(h, tmp_path):
    import log_utils
    h.path.write_bytes(b'export function chosen() {\r\n\treturn "quotes \\\""; // '+ '😀'.encode() + b'\r\n}')
    h.choose('chosen')
    def logger(level, event, **details):
        log_utils.log(level, 'first-flow-test', event, **details)
    result = h.run(logger=logger)
    assert isinstance(result, Context)
    records = [json.loads(line) for line in log_utils.LOG_PATH.read_text().splitlines()]
    payloads = [r['details']['additional_context'] for r in records if r['event'] == 'enrichment_prepared']
    assert payloads == [result.text]
    assert payloads[0].encode() == result.text.encode()
    assert h.path.read_bytes() in result.text.encode()


@pytest.mark.parametrize('kind', ['skip', 'block', 'extraction', 'budget'])
def test_L03_no_payload_logs_for_noncontext(h, monkeypatch, kind):
    if kind == 'skip':
        h.action_result.data['action'] = 'modify_code'
    elif kind == 'block':
        h.action_result = RuntimeError('RAW_ERROR')
    elif kind == 'extraction':
        def failed(*args, **kwargs):
            raise RuntimeError('RAW_ERROR')
        monkeypatch.setattr(flow.typescript, 'extract_callable', failed)
    result = h.run(max_context_chars=1 if kind == 'budget' else 8000)
    assert isinstance(result, (Failure, Noop))
    assert not h.prepared()
    assert serialize_response(result, 'UserPromptSubmit') == ''
    assert [d for _, event, d in h.logs if event == 'flow_finished']


@pytest.mark.parametrize('error', [RuntimeError, TimeoutError])
def test_L04_logger_failures_are_best_effort_except_deadline(h, golden, capsys, error):
    def broken(*args, **kwargs):
        raise error('logger unavailable')
    if error is TimeoutError:
        with pytest.raises(TimeoutError):
            h.run(logger=broken)
    else:
        assert h.run(logger=broken) == Context(golden)
    assert capsys.readouterr().out == ''


def test_L05_two_invocations_keep_exact_distinct_payloads(h):
    first = flow.handle_prompt(h.request(), h.logger, registry=h.registry)
    h.path.write_text('export function chosen() { return "SECOND_REQUEST"; }')
    h.choose('chosen')
    second = flow.handle_prompt(h.request(), h.logger, registry=h.registry)
    assert isinstance(first, Context) and isinstance(second, Context)
    assert [d['additional_context'] for d in h.prepared()] == [first.text, second.text]
    assert first.text != second.text and 'SECOND_REQUEST' not in first.text
    assert 'calculateInvoiceTotalWithAdjustment' not in second.text


def test_H01_L01_actual_default_route_exact_protocol_and_matching_log(h, tmp_path, golden):
    result, records, calls = gateway(h, tmp_path)
    assert result.returncode == 0, result.stderr.decode()
    expected = {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit', 'additionalContext': golden}}
    assert json.loads(result.stdout) == expected
    assert result.stdout.count(b'\n') == 1
    assert [x['kind'] for x in calls] == ['action', 'selection']
    events = [r['event'] for r in records]
    assert events.count('hook_dispatcher_started') == events.count('event_dispatcher_started') == 1
    prepared = [r for r in records if r['event'] == 'enrichment_prepared']
    assert len(prepared) == 1
    assert prepared[0]['details']['additional_context'] == golden
    assert prepared[0]['details']['session_id'] == 'subprocess-session'
    assert prepared[0]['details']['invocation_id']


@pytest.mark.parametrize('kind', ['no_file', 'action', 'selection', 'overflow'])
def test_H02_actual_gateway_noop_is_zero_bytes(h, tmp_path, kind):
    spec = {'action': 'modify_code'} if kind == 'action' else {}
    if kind == 'selection':
        spec = {'selected_id': None, 'selection_reason': 'no_single_match'}
    result, records, calls = gateway(h, tmp_path,
        prompt='Explain algorithms' if kind == 'no_file' else PROMPT,
        spec=spec, extra=['--max-context-chars', '1'] if kind == 'overflow' else [])
    assert result.returncode == 0 and result.stdout == b''
    assert not [r for r in records if r['event'] == 'enrichment_prepared']
    assert len(calls) == {'no_file': 0, 'action': 1, 'selection': 2, 'overflow': 2}[kind]


@pytest.mark.parametrize('kind', ['action', 'source'])
def test_H03_gateway_failures_are_zero_bytes(h, tmp_path, kind):
    result, records, calls = gateway(h, tmp_path, spec={'failure': kind})
    assert result.returncode == 0 and result.stdout == b''
    assert not [r for r in records if r['event'] == 'enrichment_prepared']


def test_H04_gateway_isolates_python_and_native_stdout_noise(h, tmp_path, golden):
    result, records, calls = gateway(h, tmp_path, spec={'noise': True})
    assert result.returncode == 0
    assert json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] == golden
    assert b'PYTHON_BOUNDARY_NOISE' in result.stderr and b'FD_BOUNDARY_NOISE' in result.stderr
    assert b'ORIGINAL_STDOUT_NOISE' in result.stderr
    assert b'BOUNDARY_NOISE' not in result.stdout


@pytest.mark.parametrize('kind', ['action', 'logging'])
def test_H05_gateway_owns_deterministic_deadline(h, tmp_path, kind):
    result, records, calls = gateway(h, tmp_path, spec={'timeout': kind})
    assert result.returncode == 0 and result.stdout == b''
    assert len(calls) == (1 if kind == 'action' else 2)
    assert b'deadline' in result.stderr


@pytest.mark.parametrize('change', [{'session_id': ''}, {'cwd': 'relative'}, {'prompt': 42}, {'hook_event_name': 'Stop'}])
def test_H06_dispatcher_validation_remains_authoritative(h, change):
    request = h.request()
    event = {**request.envelope.raw_payload, **change}
    args = deepcopy(request.config); args.hook = 'UserPromptSubmit'
    assert isinstance(dispatch_hook(event, args, 'id', h.logger), Failure)
    assert not h.calls


def test_H06_real_opt_in_required(h, tmp_path):
    result, records, calls = gateway(h, tmp_path, real=False)
    assert result.returncode == 0 and result.stdout == b'' and not calls
    assert b'model_disabled' in result.stderr


def test_H07_gateway_project_root_overrides_both_cwds(h, tmp_path, golden):
    decoy = tmp_path / 'decoy'; decoy.mkdir()
    (decoy / 'invoice-service.ts').write_text('throw new Error("DECOY");')
    (decoy / 'config.json').write_text('{"model":"wrong"}')
    (h.root / 'config.json').write_text('{"model":"offline-fixed-blocks"}')
    before = h.path.read_bytes()
    result, records, calls = gateway(h, tmp_path, cwd=decoy, extra=['--config', 'config.json'])
    assert json.loads(result.stdout)['hookSpecificOutput']['additionalContext'] == golden
    assert h.path.read_bytes() == before


def test_H07_gateway_missing_root_never_uses_valid_event_cwd(h, tmp_path):
    result, records, calls = gateway(h, tmp_path, extra=['--repository-root', str(tmp_path / 'missing')])
    assert result.returncode == 0 and result.stdout == b'' and not calls
    assert b'invalid_project_root' in result.stderr


def test_L05_gateway_invocations_keep_distinct_correlation_and_source(h, tmp_path, golden):
    first_dir = tmp_path / 'first'; first_dir.mkdir()
    second_dir = tmp_path / 'second'; second_dir.mkdir()
    first, first_logs, _ = gateway(h, first_dir, event_change={'session_id': 'first-session'})
    h.path.write_text('export function chosen() { return "SECOND_REQUEST"; }')
    h.choose('chosen')
    second, second_logs, _ = gateway(h, second_dir, event_change={'session_id': 'second-session'})
    first_payload = json.loads(first.stdout)['hookSpecificOutput']['additionalContext']
    second_payload = json.loads(second.stdout)['hookSpecificOutput']['additionalContext']
    a = [r['details'] for r in first_logs if r['event'] == 'enrichment_prepared']
    b = [r['details'] for r in second_logs if r['event'] == 'enrichment_prepared']
    assert len(a) == len(b) == 1
    assert a[0]['additional_context'] == first_payload == golden
    assert b[0]['additional_context'] == second_payload != golden
    assert a[0]['session_id'] == 'first-session' and b[0]['session_id'] == 'second-session'
    assert a[0]['invocation_id'] != b[0]['invocation_id']
    assert 'SECOND_REQUEST' in second_payload and 'SECOND_REQUEST' not in first_payload


def test_H08_existing_handler_injection_and_actual_default_registration(h, golden):
    request = h.request()
    result = dispatch_user_prompt_submit(request.envelope, request.config, h.logger,
                                          handler=lambda request, logger: Context('injected'))
    assert result == Context('injected') and not h.calls
    assert dispatch_user_prompt_submit(request.envelope, request.config, h.logger) == Context(golden)
    assert [x[0] for x in h.calls] == ['action', 'selection']


def test_session_duplicate_gateway_returns_zero_bytes_without_state_change(h, tmp_path, golden):
    first, _, _ = gateway(h, tmp_path)
    assert json.loads(first.stdout)['hookSpecificOutput']['additionalContext'] == golden
    path = tmp_path / 'state/session-state.sqlite3'
    before = path.read_bytes()
    second, records, _ = gateway(h, tmp_path)
    assert second.returncode == 0 and second.stdout == b''
    assert path.read_bytes() == before
    with __import__('sqlite3').connect(path) as db:
        assert db.execute('SELECT count(*) FROM session_events').fetchone()[0] == 1
    assert any(r['event'] == 'enrichment_skipped' for r in records)
