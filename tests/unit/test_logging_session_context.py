"""Trust session metadata at common validation, before real project enrichment."""
from contextlib import nullcontext
import io
import json
import signal
import sys
from types import SimpleNamespace

import pytest

import log_utils
from hooks import gateway, project_context
from hooks.contracts import Noop, ValidationError
from hooks.dispatcher import dispatch_hook
from soh_logging import (FlowName, HookEvent, bind, capture_records,
                         current_component, current_context, flow_scope,
                         invocation, set_session)


def _payload(tmp_path, **fields):
    return {'hook_event_name': 'UserPromptSubmit', 'session_id': 'validated',
            'cwd': str(tmp_path), 'transcript_path': 'unused.jsonl', 'prompt': '',
            **fields}


def _event(records, name):
    return next(record for record in records if record['event'] == name)


@pytest.fixture
def run_gateway(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(log_utils, 'LOG_ROOT', tmp_path / '.logs')
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))

    def run(payload, *, hook='UserPromptSubmit', flags=(), **kwargs):
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
        before, component = current_context(), current_component()
        records = []
        with capture_records(records.append):
            assert gateway.main(['--hook', hook, *flags], **kwargs) == 0
        assert current_context() == before
        assert current_component() == component
        return records, capfd.readouterr()

    return run


def test_default_gateway_promotes_before_real_project_context_and_flow(tmp_path, run_gateway):
    records, output = run_gateway(_payload(tmp_path),
                                  flags=['--real', '--repository-root', str(tmp_path)])
    assert output.out == ''
    assert output.err == 'laya hook noop: ineligible_request\n'
    events = [record['event'] for record in records]
    boundary = events.index('project_context_ingested')
    assert events[:boundary] == ['gateway_started', 'gateway_input_received',
                                'gateway_received', 'gateway_dispatch_started',
                                'hook_dispatcher_started']
    assert all(record['session_id'] is None for record in records[:boundary])
    assert all(record['session_id'] == 'validated' for record in records[boundary:])
    assert events[boundary + 1:boundary + 3] == ['common_input_validated',
                                              'hook_dispatcher_routed']
    correlation = records[0]['correlation_id']
    assert correlation and {record['correlation_id'] for record in records} == {correlation}
    assert {record['hook_event'] for record in records} == {'UserPromptSubmit'}
    project = records[boundary]
    assert project['component'] == 'project_context'
    assert project['flow_name'] is None
    assert project['details']['project_dir'] == str(tmp_path.resolve())
    flow = _event(records, 'flow_finished')
    assert flow['component'] == 'first_method_context'
    assert flow['flow_name'] == 'first_method_context'
    assert records[-1]['event'] == 'gateway_finished'
    assert records[-1]['flow_name'] is None
    assert records[-1]['details']['session_id'] == 'validated'


@pytest.mark.parametrize('kind', ['missing', 'nonstring', 'blank', 'invalid_cwd',
                                  'unsupported', 'mismatch'])
def test_unvalidated_input_never_promotes_or_enriches(tmp_path, run_gateway, kind):
    payload = _payload(tmp_path)
    hook = 'UserPromptSubmit'
    if kind == 'missing':
        del payload['session_id']
    elif kind == 'nonstring':
        payload['session_id'] = 42
    elif kind == 'blank':
        payload['session_id'] = ' '
    elif kind == 'invalid_cwd':
        payload['cwd'] = 'relative'
    elif kind == 'unsupported':
        payload['hook_event_name'] = hook = 'Unknown'
    else:
        payload['hook_event_name'] = 'PostCompact'
    records, output = run_gateway(payload, hook=hook)
    assert output.out == ''
    assert all(record['session_id'] is None for record in records)
    assert not any(record['event'] in {'project_context_ingested', 'common_input_validated'}
                   for record in records)
    if kind == 'unsupported':
        assert _event(records, 'hook_dispatcher_route_skipped')['details']['reason'] == 'unsupported_event'
        assert {record['hook_event'] for record in records} == {None}
    else:
        expected = 'event_mismatch' if kind == 'mismatch' else 'invalid_field'
        assert records[-1]['details']['code'] == expected


@pytest.mark.parametrize('error', [ValidationError('invalid_enrichment', 'common'),
                                  RuntimeError('enrichment failed'),
                                  TimeoutError('enrichment deadline')],
                         ids=['validation', 'exception', 'timeout'])
def test_enrichment_failure_keeps_validated_session(tmp_path, run_gateway, monkeypatch, error):
    def fail(project_dir):
        raise error

    monkeypatch.setattr(project_context, 'resolve_project_directory', fail)
    records, output = run_gateway(_payload(tmp_path))
    assert output.out == ''
    boundary = next(index for index, record in enumerate(records)
                    if record['event'] == 'hook_dispatcher_started') + 1
    assert all(record['session_id'] is None for record in records[:boundary])
    assert all(record['session_id'] == 'validated' for record in records[boundary:])
    assert not any(record['event'] in {'project_context_ingested', 'common_input_validated',
                                      'hook_dispatcher_routed'} for record in records)
    assert records[-1]['event'] == 'gateway_finished'
    assert records[-1]['flow_name'] is None
    assert records[-1]['details']['session_id'] == 'validated'
    assert records[-1]['details']['code'] == (
        error.code if isinstance(error, ValidationError) else
        'deadline_exceeded' if isinstance(error, TimeoutError) else 'gateway_error')


def test_sequential_gateway_sessions_and_rejection_are_isolated(tmp_path, run_gateway):
    runs = [run_gateway(_payload(tmp_path, session_id=session))[0]
            for session in ['one', 'two', None]]
    assert len({records[0]['correlation_id'] for records in runs}) == 3
    assert all(records[0]['session_id'] is None for records in runs)
    assert [records[-1]['session_id'] for records in runs] == ['one', 'two', None]
    assert all(record['session_id'] is None for record in runs[-1])


def test_post_compact_session_has_no_flow(tmp_path, run_gateway, monkeypatch):
    from hooks.post_compact import dispatcher
    saved = []
    monkeypatch.setattr(dispatcher, 'save_event',
                        lambda session, event, **kwargs: saved.append((session, event)))
    records, output = run_gateway(_payload(tmp_path, hook_event_name='PostCompact', trigger='auto'),
                                  hook='PostCompact', flags=['--log-state'])
    assert output.out == ''
    assert output.err == 'laya hook noop: session_state_logged\n'
    assert saved == [('validated', {'source': {'kind': 'hook', 'name': 'PostCompact'},
                                    'hook_name': 'PostCompact', 'type': 'PostCompact', 'payload': {}})]
    boundary = next(index for index, record in enumerate(records)
                    if record['event'] == 'common_input_validated')
    assert all(record['session_id'] is None for record in records[:boundary])
    assert all(record['session_id'] == 'validated' for record in records[boundary:])
    assert _event(records, 'session_state_saved')['component'] == 'post_compact'
    assert {record['hook_event'] for record in records} == {'PostCompact'}
    assert {record['flow_name'] for record in records} == {None}
    assert len({record['correlation_id'] for record in records}) == 1


def test_observe_only_keeps_raw_session_in_details(tmp_path, run_gateway):
    def no_dispatch(*args):
        pytest.fail('observe-only invoked common dispatch')

    records, output = run_gateway(_payload(tmp_path), flags=['--observe-only'],
                                  dispatcher=no_dispatch)
    assert output.out == output.err == ''
    assert {record['session_id'] for record in records} == {None}
    assert _event(records, 'hook_observed')['details']['session_id'] == 'validated'
    assert records[-1]['details']['session_id'] is None
    assert not any(record['event'] in {'hook_dispatcher_started', 'project_context_ingested',
                                      'common_input_validated'} for record in records)


@pytest.mark.parametrize('active', [False, True], ids=['standalone', 'mismatched'])
def test_direct_dispatch_preserves_context_and_logger_arguments(tmp_path, active):
    calls = []
    scope = invocation('other', HookEvent.POSTCOMPACT) if active else nullcontext()
    with scope:
        if active:
            set_session('outer-session', correlation_id='other')
        before = current_context()

        def logger(level, event, **details):
            assert current_context() == before
            calls.append((event, details))

        def enrich(envelope):
            assert current_context() == before
            return project_context.enrich_project_context(envelope, str(tmp_path), logger)

        assert dispatch_hook(_payload(tmp_path), SimpleNamespace(hook='UserPromptSubmit'),
                             'standalone', logger, enricher=enrich,
                             dispatchers={'UserPromptSubmit': lambda *args: Noop('done')}) == Noop('done')
        assert current_context() == before
    by_event = dict(calls)
    assert by_event['hook_dispatcher_started']['session_id'] == 'validated'
    assert by_event['project_context_ingested']['session_id'] == 'validated'
    assert by_event['common_input_validated']['session_id'] == 'validated'
    assert by_event['hook_dispatcher_routed']['common']['session_id'] == 'validated'
    assert current_context().session_id is None


def test_log_event_name_cannot_promote_a_session(tmp_path, run_gateway):
    def dispatch(payload, args, invocation_id, logger):
        logger('INFO', 'common_input_validated', session_id='unvalidated')
        return Noop('done')

    records, _ = run_gateway(_payload(tmp_path), dispatcher=dispatch)
    assert {record['session_id'] for record in records} == {None}
    assert _event(records, 'common_input_validated')['details']['session_id'] == 'unvalidated'
    assert records[-1]['details']['session_id'] is None


@pytest.mark.parametrize('error', [RuntimeError('flow failed'), TimeoutError('flow deadline'),
                                  SystemExit('flow exit')], ids=['exception', 'timeout', 'exit'])
def test_gateway_error_restores_enclosing_context_and_finishes_without_flow(
        tmp_path, run_gateway, monkeypatch, error):
    from hooks.user_prompt_submit import dispatcher

    @flow_scope(FlowName.FIRST_METHOD_CONTEXT)
    def fail(request, logger):
        log_utils.log('INFO', 'block_runtime', 'prepared')
        raise error

    monkeypatch.setattr(dispatcher, '_default_handler', fail)
    with invocation('outer', HookEvent.POSTCOMPACT), bind(flow_name=FlowName.FIRST_METHOD_CONTEXT):
        set_session('outer-session', correlation_id='outer')
        records, output = run_gateway(_payload(tmp_path))
        assert current_context().session_id == 'outer-session'
    assert output.out == ''
    prepared = _event(records, 'prepared')
    assert prepared['session_id'] == 'validated'
    assert prepared['flow_name'] == 'first_method_context'
    assert records[0]['session_id'] is None and records[0]['flow_name'] is None
    assert records[-1]['event'] == 'gateway_finished'
    assert records[-1]['session_id'] == 'validated'
    assert records[-1]['details']['session_id'] == 'validated'
    assert records[-1]['flow_name'] is None
    assert records[-1]['details']['code'] == (
        'deadline_exceeded' if isinstance(error, TimeoutError) else 'gateway_error')


def test_actual_gateway_alarm_restores_context_after_validated_enrichment(
        tmp_path, run_gateway, monkeypatch):
    observed = []
    previous = signal.getsignal(signal.SIGALRM)

    def expire(project_dir):
        observed.append(current_context())
        signal.raise_signal(signal.SIGALRM)
        pytest.fail('gateway deadline did not interrupt enrichment')

    monkeypatch.setattr(project_context, 'resolve_project_directory', expire)
    with invocation('outer', HookEvent.POSTCOMPACT):
        set_session('outer-session', correlation_id='outer')
        records, output = run_gateway(_payload(tmp_path))
        assert current_context().session_id == 'outer-session'
    assert observed[0].session_id == 'validated'
    assert observed[0].correlation_id == records[0]['correlation_id']
    assert observed[0].hook_event == HookEvent.USERPROMPTSUBMIT
    assert output.out == '' and 'deadline_exceeded' in output.err
    # Expiration suppresses further logging under the gateway's existing policy.
    assert records[-1]['event'] == 'hook_dispatcher_started'
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0)
