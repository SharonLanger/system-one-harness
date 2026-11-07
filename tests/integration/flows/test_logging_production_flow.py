"""Default gateway route, real blocks/model adapter and real TypeScript wrappers."""
import errno
import io
import json
import os
import sys

import pytest

import log_utils
from hooks.gateway import main
from soh_logging import ComponentName, ComponentRef, ComponentType, LogEvent, LogLevel, LogWriter, WriterConfig
from tests.support.fake_laya import ScriptedLaya, action_answer, method_answer


def _rows(root):
    return [json.loads(line) for path in sorted(root.glob('*/*.jsonl'))
            for line in path.read_text().splitlines()]


def test_default_route_keeps_common_context_through_real_emitters(tmp_path, monkeypatch, capfd):
    import laya.registry
    from hooks import project_context
    from flows import first_method_context

    root = tmp_path / '.logs'
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    monkeypatch.setattr(log_utils, 'LOG_ROOT', root)
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    (tmp_path / 'sample.ts').write_text('export function calculateTotal(value: number) { return value + 1; }\n')
    fake = ScriptedLaya(action_answer, method_answer)
    monkeypatch.setattr(laya.registry, 'real_loader', fake.load)
    # Exercise actual enrichment decisions, while keeping session-store persistence
    # separate from the structured writer under test.
    monkeypatch.setattr(first_method_context, 'inspect_and_save_event',
                        lambda session, decide, **kwargs: decide([])[0])
    original_enrich = project_context.enrich_project_context

    def enrich(*args, **kwargs):
        envelope = original_enrich(*args, **kwargs)
        LogWriter(WriterConfig(root)).emit(
            LogLevel.INFO, ComponentRef(ComponentType.DISPATCHER, ComponentName.PROJECT_CONTEXT),
            LogEvent.CONTEXT_VALIDATED, 'Direct writer sees validated context.')
        return envelope

    monkeypatch.setattr(project_context, 'enrich_project_context', enrich)
    payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'production-session',
               'cwd': str(tmp_path), 'transcript_path': 'unused.jsonl',
               'prompt': 'Explain `sample.ts`: calculateTotal'}
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert main(['--hook', 'UserPromptSubmit', '--real', '--repository-root', str(tmp_path)]) == 0
    output = capfd.readouterr()
    protocol = json.loads(output.out)
    context = protocol['hookSpecificOutput']['additionalContext']
    assert 'value + 1' in context and output.out.count('\n') == 1
    assert output.err == ''
    fake.assert_complete()
    assert len(fake.calls) == 2
    records = _rows(root)
    correlation = records[0]['correlation_id']
    assert correlation and {row['correlation_id'] for row in records} == {correlation}
    assert {row['hook_event'] for row in records} == {'UserPromptSubmit'}
    assert {row['pid'] for row in records} == {os.getpid()}
    boundary = next(i for i, row in enumerate(records) if row['event'] == 'project_context_ingested')
    assert all(row['session_id'] is None for row in records[:boundary])
    assert all(row['session_id'] == 'production-session' for row in records[boundary:])
    for component, kind, event in [
        ('hook_gateway', 'gateway', 'gateway_finished'),
        ('hook_dispatcher', 'dispatcher', 'hook_dispatcher_routed'),
        ('user_prompt_submit', 'dispatcher', 'event_dispatcher_started'),
        ('project_context', 'dispatcher', 'context_validated'),
        ('first_method_context', 'flow', 'enrichment_prepared'),
        ('block_runtime', 'block', 'prepared'),
        ('laya', 'model', 'prediction_started'),
        ('extractor', 'extractor', 'request_started'),
        ('extractor', 'extractor', 'helper_returned'),
    ]:
        selected = [row for row in records if row['event'] == event and row['component'] == component]
        assert selected, (component, event)
        assert {row['component_type'] for row in selected} == {kind}
        assert {row['flow_name'] for row in selected} == {
            'first_method_context' if kind in {'block', 'model', 'extractor', 'flow'} else None}
    assert next(row for row in records if row['event'] == 'enrichment_prepared')['details']['additional_context'] == context
    assert [row['details']['response'] for row in records if row['event'] == 'response_received']
    assert next(row for row in records if row['event'] == 'prediction_started')['details']['text'] == payload['prompt']
    # These wrapper events are emitted by the parent, even though the real Node
    # helper returned the source data in their details.
    assert all(row['details']['stdout'] for row in records if row['event'] == 'helper_returned')


@pytest.mark.parametrize('number', [errno.EACCES, errno.ENOSPC], ids=['permission', 'disk-full'])
def test_ordinary_writer_failure_keeps_real_flow_outcome(tmp_path, monkeypatch, capfd, number):
    import laya.registry
    from flows import first_method_context

    fake = ScriptedLaya(action_answer, method_answer)
    monkeypatch.setattr(laya.registry, 'real_loader', fake.load)
    monkeypatch.setattr(first_method_context, 'inspect_and_save_event',
                        lambda session, decide, **kwargs: decide([])[0])
    (tmp_path / 'sample.ts').write_text('export function calculateTotal(value: number) { return value + 1; }\n')
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'io-failure-session',
               'cwd': str(tmp_path), 'transcript_path': 'unused.jsonl',
               'prompt': 'Explain `sample.ts`: calculateTotal'}

    def fail(*args, **kwargs):
        raise OSError(number, os.strerror(number))

    monkeypatch.setattr(LogWriter, 'emit', fail)
    assert log_utils.log('INFO', 'hook_gateway', 'gateway_started') is None
    with pytest.raises(OSError) as error:
        log_utils.log('INFO', 'hook_gateway', 'gateway_started', raise_on_error=True)
    assert error.value.errno == number
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
    assert main(['--hook', 'UserPromptSubmit', '--real', '--repository-root', str(tmp_path)]) == 0
    output = capfd.readouterr()
    assert 'value + 1' in json.loads(output.out)['hookSpecificOutput']['additionalContext']
    assert 'laya logging failed:' in output.err
    fake.assert_complete()


def test_unserializable_gateway_detail_cannot_contaminate_protocol(tmp_path, monkeypatch, capfd):
    from hooks.contracts import Context
    from hooks.gateway import _default_log

    class BadRepr:
        def __repr__(self):
            raise ValueError('repr unavailable')

    cycle = []
    cycle.append(cycle)
    root = tmp_path / '.logs'
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    monkeypatch.setattr(log_utils, 'LOG_ROOT', root)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"hook_event_name":"UserPromptSubmit"}'))

    def logger(level, event, **details):
        _default_log(level, event, bad=BadRepr(), cyclic=cycle, **details)

    assert main(['--hook', 'UserPromptSubmit'], logger=logger,
                dispatcher=lambda *args: Context('synthetic context')) == 0
    output = capfd.readouterr()
    assert output.out == '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"synthetic context"}}\n'
    assert output.err == ''
    records = _rows(root)
    assert records
    for record in records:
        assert record['details']['bad'] == '<unrepresentable BadRepr>'
        assert record['details']['cyclic'] == ['<cycle>']
        diagnostics = ' '.join(record['details']['_serialization_errors'])
        assert 'bad' in diagnostics and 'cyclic' in diagnostics


def test_logging_failure_during_exception_preserves_original_gateway_error(tmp_path, monkeypatch, capfd):
    from hooks.gateway import _default_log

    root = tmp_path / '.logs'
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    monkeypatch.setattr(log_utils, 'LOG_ROOT', root)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"hook_event_name":"UserPromptSubmit"}'))

    def dispatch(*args):
        raise LookupError('original application evidence')

    def logger(level, event, **details):
        if event == 'gateway_error':
            raise OSError(errno.ENOSPC, 'secondary logger failure')
        _default_log(level, event, **details)

    assert main(['--hook', 'UserPromptSubmit'], dispatcher=dispatch, logger=logger) == 0
    output = capfd.readouterr()
    assert output.out == ''
    assert 'original application evidence' in output.err
    assert 'laya logging failed: OSError' in output.err
    finished = _rows(root)[-1]
    assert finished['event'] == 'gateway_finished'
    assert finished['details']['code'] == 'gateway_error'


def test_standalone_block_emits_own_components_without_invocation(tmp_path, monkeypatch):
    from building_blocks.questions.requested_action import block
    from laya.config import RunConfig
    from soh_logging import capture_records

    root = tmp_path / '.logs'
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    monkeypatch.setattr(log_utils, 'LOG_ROOT', root)
    fake = ScriptedLaya(action_answer)
    records = []
    with capture_records(records.append):
        result = block.run({'prompt': 'Explain the calculation.'},
                           config=RunConfig(model='offline-scripted'), registry=fake.registry())
    assert result.data == {'action': 'explain'}
    fake.assert_complete()
    assert any(row['component'] == 'block_runtime' and row['component_type'] == 'block' for row in records)
    assert any(row['component'] == 'laya' and row['component_type'] == 'model' for row in records)
    assert all(row[field] is None for row in records
               for field in ['session_id', 'correlation_id', 'hook_event', 'flow_name'])
    assert not root.exists()
