"""Synthetic SQLite test helpers; never read installation state."""
import json
import sqlite3
from time import monotonic
from types import SimpleNamespace
from hooks import session_state as state
from flows.first_method_context_utils import SelectedMethod, build_enrichment_event, decide_enrichment

METHOD = SelectedMethod('/project/orders.ts', 'calculate', 'calculate(): number', 20, 45)
COMPACT = {'source': {'kind': 'hook', 'name': 'PostCompact'}, 'hook_name': 'PostCompact',
           'type': 'PostCompact', 'payload': {}}


def event(flow='first_method_context', payload=None, hook='UserPromptSubmit', kind='context_enriched'):
    return dict(source={'kind': 'flow', 'name': flow}, hook_name=hook, type=kind,
                payload={} if payload is None else payload)


def save(path, value=COMPACT, session='s', **kwargs):
    return state.save_event(session, value, database_path=path, deadline=monotonic()+20, **kwargs)


def transact(path, session='s', callback=None, method=METHOD, flow='first_method_context', **kwargs):
    def decide(history):
        decision = decide_enrichment(history, method)
        return decision, build_enrichment_event(method) if decision.allow else None
    return state.inspect_and_save_event(session, callback or decide, database_path=path,
        deadline=kwargs.pop('deadline', monotonic()+20), hook_name='UserPromptSubmit', flow_name=flow, **kwargs)


def history(path, session='s', flow='first_method_context'):
    result = []
    def capture(events):
        result.extend(events)
        return SimpleNamespace(allow=False), None
    transact(path, session, callback=capture, flow=flow)
    return result


def rows(path):
    if not path.exists():
        return []
    with sqlite3.connect(path) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='session_events'").fetchone():
            return []
        return db.execute('SELECT * FROM session_events ORDER BY event_id').fetchall()
