"""Detached in-memory adapter with the same metadata/relevance contract."""
from copy import deepcopy
from hooks.session_state import _event, _name, _remaining, _session, _timestamp


class MemorySessionStore:
    def __init__(self):
        self.events = {}

    def save_event(self, session_id, event, *, deadline, database_path=None):
        _remaining(deadline)
        _session(session_id)
        _event(event)
        saved = deepcopy(event)
        saved['timestamp'] = _timestamp()
        self.events.setdefault(session_id, []).append(saved)

    def inspect_and_save_event(self, session_id, decide, *, deadline, hook_name, flow_name, database_path=None):
        _remaining(deadline)
        _session(session_id)
        _name(hook_name)
        _name(flow_name)
        history = [event for event in self.events.get(session_id, [])
                   if event['source'] == {'kind': 'flow', 'name': flow_name} or
                   (event['source'] == {'kind': 'hook', 'name': 'PostCompact'} and
                    event['hook_name'] == event['type'] == 'PostCompact')]
        result = decide(deepcopy(history))
        _remaining(deadline)
        if not isinstance(result, tuple) or len(result) != 2:
            raise ValueError('invalid_session_state_decision')
        decision, event = result
        allow = getattr(decision, 'allow', None)
        if type(allow) is not bool or (allow and not isinstance(event, dict)) or (not allow and event is not None):
            raise ValueError('invalid_session_state_decision')
        if allow:
            if _event(event)[:2] != (hook_name, flow_name):
                raise ValueError('conflicting_session_state_origin')
            self.save_event(session_id, event, deadline=deadline)
        return decision
