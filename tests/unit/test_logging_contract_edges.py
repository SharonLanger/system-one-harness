"""Observable schema, snapshots and scope restoration beyond happy-path logging."""
import asyncio
from datetime import datetime
import json
import os

import pytest

from soh_logging import (ComponentName, ComponentRef, ComponentType, FlowName,
                         HookEvent, LogEvent, LogLevel, LogWriter, WriterConfig,
                         bind, capture_records, current_context, flow_scope,
                         invocation, set_session)


REF = ComponentRef(ComponentType.BLOCK, ComponentName.BLOCK_RUNTIME)
COMMON_TYPES = {'schema_version': int, 'timestamp': str, 'level': str,
                'message': str, 'event': str, 'component_type': str,
                'component': str, 'hook_event': type(None), 'session_id': type(None),
                'correlation_id': type(None), 'flow_name': type(None),
                'pid': int, 'details': dict}


def test_file_and_capture_share_schema_and_detached_json_values(tmp_path):
    writer = LogWriter(WriterConfig(tmp_path / '.logs'))
    details = {'text': 'שלום 😀 "quoted"\tline\nnext',
               'values': [1, 1.25, True, False, None, {'nested': ['before']}]}
    captured = []
    message = 'Prepared input.\r\nReady to predict.\nNext step.'
    with capture_records(captured.append):
        writer.emit(LogLevel.INFO, REF, LogEvent.PREPARED, message, details)
        writer.emit(LogLevel.INFO, REF, LogEvent.PREPARED, 'No details.')
    assert not writer.config.root.exists()
    writer.emit(LogLevel.INFO, REF, LogEvent.PREPARED, message, details)
    writer.emit(LogLevel.INFO, REF, LogEvent.PREPARED, 'No details.')
    details['values'][-1]['nested'].append('after')
    path, = writer.config.root.glob('*/*.jsonl')
    lines = path.read_text().splitlines()
    assert len(lines) == 2
    written = [json.loads(line) for line in lines]
    for records in (captured, written):
        record, empty = records
        assert set(record) == set(COMMON_TYPES)
        assert all(isinstance(record[key], kind) for key, kind in COMMON_TYPES.items())
        assert type(record['schema_version']) is int and type(record['pid']) is int
        assert record['schema_version'] == 1 and record['pid'] == os.getpid()
        assert datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')).utcoffset().total_seconds() == 0
        assert record['message'] == 'Prepared input. Ready to predict. Next step.'
        assert record['component_type'] == 'block' and record['component'] == 'block_runtime'
        assert record['details'] == {'text': 'שלום 😀 "quoted"\tline\nnext',
                                     'values': [1, 1.25, True, False, None, {'nested': ['before']}]}
        assert [type(value) for value in record['details']['values']] == [int, float, bool, bool, type(None), dict]
        assert empty['details'] == {}


def test_all_vocabulary_members_serialize_as_values(tmp_path):
    writer = LogWriter(WriterConfig(tmp_path / '.logs'))
    catalogs = [LogLevel, LogEvent, ComponentType, ComponentName, HookEvent, FlowName]
    # Exercise each common-field serialization path as well as values in details.
    for catalog in catalogs:
        for member in catalog:
            fields = {'level': LogLevel.INFO, 'component': REF, 'event': LogEvent.PREPARED}
            scope = {}
            if catalog is LogLevel:
                fields['level'] = member
            elif catalog is LogEvent:
                fields['event'] = member
            elif catalog is ComponentType:
                fields['component'] = ComponentRef(member, ComponentName.BLOCK_RUNTIME)
            elif catalog is ComponentName:
                fields['component'] = ComponentRef(ComponentType.BLOCK, member)
            elif catalog is HookEvent:
                scope['hook_event'] = member
            else:
                scope['flow_name'] = member
            with bind(**scope):
                writer.emit(**fields, message='Vocabulary serialization.',
                            details={'catalog': catalog.__name__, 'value': member})
    rows = [json.loads(line) for path in writer.config.root.glob('*/*.jsonl')
            for line in path.read_text().splitlines()]
    assert len(rows) == sum(len(catalog) for catalog in catalogs)
    keys = {LogLevel: 'level', LogEvent: 'event', ComponentType: 'component_type',
            ComponentName: 'component', HookEvent: 'hook_event', FlowName: 'flow_name'}
    for catalog in catalogs:
        selected = [row for row in rows if row['details']['catalog'] == catalog.__name__]
        assert [row[keys[catalog]] for row in selected] == [member.value for member in catalog]
        assert [row['details']['value'] for row in selected] == [member.value for member in catalog]


def test_chained_exception_and_large_prompt_response_preserve_evidence(tmp_path):
    writer = LogWriter(WriterConfig(tmp_path / '.logs'))
    try:
        try:
            raise KeyError('missing input')
        except KeyError as cause:
            raise RuntimeError('cannot prepare') from cause
    except RuntimeError as error:
        writer.emit(LogLevel.ERROR, REF, LogEvent.FAILED, 'Preparation failed.', {'error': error})
    prompt = 'Explain `sample.ts`: calculateTotal.\n' * 5000
    response = {'answers': [{'code': 'export function calculateTotal() { return "😀"; }',
                             'accepted': True, 'score': 0.95}] * 3000}
    writer.emit(LogLevel.INFO, REF, LogEvent.COMPLETED, 'Prediction completed.',
                {'prompt': prompt, 'response': response})
    rows = [json.loads(line) for path in writer.config.root.glob('*/*.jsonl')
            for line in path.read_text().splitlines()]
    assert rows[0]['details']['error']['type'] == 'RuntimeError'
    assert rows[0]['details']['error']['message'] == 'cannot prepare'
    trace = rows[0]['details']['error']['traceback']
    assert 'KeyError' in trace and 'missing input' in trace and 'RuntimeError: cannot prepare' in trace
    assert 'direct cause' in trace
    assert rows[1]['details'] == {'prompt': prompt, 'response': response}


def test_nested_flow_and_exceptional_capture_restore_context_and_sink(tmp_path):
    writer = LogWriter(WriterConfig(tmp_path / '.logs'))
    outer, inner = [], []

    @flow_scope(FlowName.SANDBOX_FILE_METHOD_CONTEXT)
    def nested():
        with capture_records(inner.append):
            writer.emit(LogLevel.INFO, REF, LogEvent.PREPARED, 'Nested flow.')
            raise RuntimeError('leave nested scope')

    @flow_scope(FlowName.FIRST_METHOD_CONTEXT)
    def enclosing():
        before = current_context()
        with pytest.raises(RuntimeError, match='leave nested scope'):
            nested()
        assert current_context() == before
        writer.emit(LogLevel.INFO, REF, LogEvent.COMPLETED, 'Outer flow restored.')
        return 'early return'

    with invocation('nested-invocation', HookEvent.USERPROMPTSUBMIT):
        set_session('nested-session', correlation_id='nested-invocation')
        before = current_context()
        with capture_records(outer.append):
            assert enclosing() == 'early return'
            assert current_context() == before
            writer.emit(LogLevel.INFO, REF, LogEvent.COMPLETED, 'Flow exited.')
    assert [record['flow_name'] for record in outer] == ['first_method_context', None]
    assert inner[0]['flow_name'] == 'sandbox_file_method_context'
    for record in [*outer, *inner]:
        assert set(record) == set(COMMON_TYPES)
        assert record['session_id'] == 'nested-session'
        assert record['correlation_id'] == 'nested-invocation'
        assert record['hook_event'] == 'UserPromptSubmit'
    assert not writer.config.root.exists()
    writer.emit(LogLevel.INFO, REF, LogEvent.COMPLETED, 'Capture exited.')
    assert len(list(writer.config.root.glob('*/*.jsonl'))) == 1
    assert current_context().correlation_id is None


def test_concurrent_tasks_keep_context_and_capture_isolated(tmp_path):
    writer = LogWriter(WriterConfig(tmp_path / '.logs'))

    async def exercise():
        ready = asyncio.Event()
        arrived = 0

        async def task(index, flow):
            nonlocal arrived
            records = []
            with invocation(f'inv-{index}', HookEvent.USERPROMPTSUBMIT), bind(flow_name=flow):
                set_session(f'session-{index}', correlation_id=f'inv-{index}')
                with capture_records(records.append):
                    arrived += 1
                    if arrived == 2:
                        ready.set()
                    await ready.wait()
                    for _ in range(3):
                        await asyncio.sleep(0)
                        writer.emit(LogLevel.INFO, REF, LogEvent.PREPARED, 'Concurrent task.')
                assert all(row['session_id'] == f'session-{index}' and
                           row['correlation_id'] == f'inv-{index}' and row['flow_name'] == flow
                           for row in records)
                assert len(records) == 3
        await asyncio.gather(task(0, FlowName.FIRST_METHOD_CONTEXT),
                             task(1, FlowName.SANDBOX_FILE_METHOD_CONTEXT))

    asyncio.run(exercise())
    assert current_context().session_id is None
    assert not writer.config.root.exists()
