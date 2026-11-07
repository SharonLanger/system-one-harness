"""Compatibility facade for SOH's shared structured local writer."""
import os
from pathlib import Path

from soh_logging import (ComponentName, ComponentRef, ComponentType, LogEvent,
                         LogLevel, LogWriter, WriterConfig, capture_records)

def _default_root():
    explicit = os.environ.get('SOH_LOG_ROOT')
    if explicit:
        root = Path(explicit).expanduser()
        if not root.is_absolute():
            raise ValueError('SOH_LOG_ROOT must be an absolute path')
        return root
    checkout = Path(__file__).resolve().parents[1]
    if (checkout / '.git').exists():
        return checkout / '.logs'
    state = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state'))
    return state / 'system-one-harness' / '.logs'


LOG_ROOT = _default_root()
# Legacy temporary-file override used by existing tests; production uses LOG_ROOT.
LOG_PATH = None
_KINDS = {
    'hook_gateway': 'gateway', 'hook_dispatcher': 'dispatcher',
    'user_prompt_submit': 'dispatcher', 'post_compact': 'dispatcher',
    'project_context': 'dispatcher', 'flow': 'flow',
    'first_method_context': 'flow', 'sandbox_file_method_context': 'flow',
    'block_runtime': 'block', 'extractor': 'extractor', 'typescript_extractor': 'extractor',
    'laya': 'model', 'cli': 'cli', 'test': 'cli',
    'answers': 'block', 'enrichment': 'block', 'questions': 'block', 'rules': 'block',
}
_WRITER = LogWriter(WriterConfig(LOG_ROOT))


def _message(component, event):
    if event in {'started', 'failed', 'finished', 'completed', 'prepared',
                 'loaded', 'configured', 'decision', 'default', 'captured', 'event'}:
        return f'{component.replace("_", " ").capitalize()} {event.replace("_", " ")}.'
    return event.replace('_', ' ').capitalize() + '.'


def log(level, component, event, *, error=None, raise_on_error=False, **details):
    """Emit a predefined event; ordinary logging errors remain best effort."""
    try:
        if error is not None:
            details['error'] = error
        if level == 'WARN':
            level = 'WARNING'
        kind = _KINDS.get(component)
        ref = ComponentRef(ComponentType(kind) if kind else None,
                           ComponentName(component) if component in ComponentName._value2member_map_ else None)
        root = LOG_ROOT if LOG_PATH is None else LOG_PATH.parent
        writer = _WRITER if root == _WRITER.config.root and LOG_PATH is None else LogWriter(WriterConfig(root, legacy_path=LOG_PATH))
        return writer.emit(LogLevel(level), ref, LogEvent(event),
                           _message(component, event), details)
    except TimeoutError:
        raise
    except Exception:
        if raise_on_error:
            raise
