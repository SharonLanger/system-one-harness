"""Serialization must preserve deadline exceptions through logging entrypoints."""
import pytest

import log_utils
from soh_logging import (ComponentName, ComponentRef, ComponentType, LogEvent,
                         LogLevel, LogWriter, WriterConfig, capture_records)


@pytest.mark.parametrize('entrypoint', ['writer', 'facade'])
@pytest.mark.parametrize('location', ['value', 'key'])
def test_repr_timeout_propagates_without_emitting(tmp_path, monkeypatch,
                                                entrypoint, location):
    error = TimeoutError('hook deadline exceeded during repr')

    class TimedOutRepr:
        def __repr__(self):
            raise error

    value = TimedOutRepr()
    details = {'item': value} if location == 'value' else {'item': {value: 1}}
    root = tmp_path / '.logs'
    writer = LogWriter(WriterConfig(root))
    monkeypatch.setattr(log_utils, 'LOG_ROOT', root)
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    captured = []

    with capture_records(captured.append), pytest.raises(TimeoutError) as raised:
        if entrypoint == 'writer':
            writer.emit(LogLevel.INFO,
                        ComponentRef(ComponentType.GATEWAY, ComponentName.HOOK_GATEWAY),
                        LogEvent.GATEWAY_STARTED, 'Gateway started.', details)
        else:
            log_utils.log('INFO', 'hook_gateway', 'gateway_started', **details)

    assert raised.value is error
    assert captured == []
    assert not root.exists()
