"""PostCompact actions. Logging never calls models or code extraction."""
from hooks.contracts import Noop, Failure
from hooks.session_state import save_event


def dispatch_post_compact(envelope, args, logger, *, session_store=None):
    if not getattr(args, 'log_state', False):
        return Noop('unsupported_action', feature_id='session_state')
    if envelope.raw_payload.get('trigger') not in ('manual', 'auto'):
        return Failure('invalid_trigger', 'post_compact', 'trigger')
    event = {'source': {'kind': 'hook', 'name': 'PostCompact'},
             'hook_name': 'PostCompact', 'type': 'PostCompact', 'payload': {}}
    try:
        writer = session_store.save_event if session_store is not None else save_event
        writer(envelope.common['session_id'], event, deadline=args.deadline_at)
    except TimeoutError:
        raise
    except Exception as exc:
        logger('ERROR', 'session_state_failed', error_type=type(exc).__name__, message='session_state_unavailable')
        return Failure('session_state_failed', 'post_compact', message='session_state_unavailable')
    logger('INFO', 'session_state_saved', event_type='PostCompact')
    return Noop('session_state_logged', feature_id='session_state')
