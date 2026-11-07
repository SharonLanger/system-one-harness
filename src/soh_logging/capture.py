"""Scoped test capture."""
from contextlib import contextmanager
from contextvars import ContextVar

_sink = ContextVar('soh_record_sink', default=None)


def current_sink():
    return _sink.get()


@contextmanager
def capture_records(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
