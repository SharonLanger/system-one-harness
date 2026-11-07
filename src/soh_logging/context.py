"""Scoped logging context, independent of the test capture sink."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import wraps

from .records import LogContext
from .records import ComponentRef
from .vocabulary import ComponentName, ComponentType

_current = ContextVar('soh_log_context', default=LogContext())
_component = ContextVar('soh_log_component', default=None)


def current_context():
    return _current.get()


def current_component():
    return _component.get()


def component_scope(component_type, component):
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            token = _component.set(ComponentRef(component_type, component))
            try:
                return func(*args, **kwargs)
            finally:
                _component.reset(token)
        return wrapped
    return decorate


@contextmanager
def invocation(correlation_id, hook_event=None):
    token = _current.set(LogContext(correlation_id=correlation_id, hook_event=hook_event))
    component_token = _component.set(None)
    try:
        yield
    finally:
        _component.reset(component_token)
        _current.reset(token)


@contextmanager
def bind(**fields):
    token = _current.set(replace(_current.get(), **fields))
    try:
        yield
    finally:
        _current.reset(token)


def set_session(session_id, *, correlation_id):
    """Promote a validated session only into the matching invocation scope.

    Standalone dispatchers must not mutate the default context or an unrelated
    invocation. The enclosing invocation restores this update on every exit.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError('validated session_id required')
    context = _current.get()
    if not correlation_id or context.correlation_id != correlation_id:
        return False
    _current.set(replace(context, session_id=session_id))
    return True


def flow_scope(flow_name):
    def decorate(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            token = _component.set(ComponentRef(ComponentType.FLOW, ComponentName(flow_name.value)))
            try:
                with bind(flow_name=flow_name):
                    return func(*args, **kwargs)
            finally:
                _component.reset(token)
        return wrapped
    return decorate
