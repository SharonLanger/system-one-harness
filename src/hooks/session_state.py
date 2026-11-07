"""Local, deadline-bounded SQLite event history; no model or subprocess work."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
_PAYLOAD_LIMIT = 65536
_TABLE = '''CREATE TABLE session_events (
    event_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL CHECK (length(trim(session_id)) > 0),
    hook_name TEXT NOT NULL CHECK (length(trim(hook_name)) > 0),
    flow_name TEXT CHECK (flow_name IS NULL OR length(trim(flow_name)) > 0),
    event_type TEXT NOT NULL CHECK (length(trim(event_type)) > 0),
    recorded_at TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (length(CAST(payload_json AS BLOB)) <= 65536)
)'''
_COLUMNS = [('event_id', 'INTEGER', 0, 1), ('session_id', 'TEXT', 1, 0),
            ('hook_name', 'TEXT', 1, 0), ('flow_name', 'TEXT', 0, 0),
            ('event_type', 'TEXT', 1, 0), ('recorded_at', 'TEXT', 1, 0),
            ('payload_json', 'TEXT', 1, 0)]


def _remaining(deadline):
    if not isinstance(deadline, (float, int)) or isinstance(deadline, bool) or not math.isfinite(deadline):
        raise ValueError('invalid_session_state_deadline')
    value = deadline - monotonic()
    if value <= 0:
        raise TimeoutError('session_state_deadline')
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('invalid_session_state_metadata')
    return value


def _session(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('invalid_session_id')


def _json_value(value, active):
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) not in (dict, list) or id(value) in active:
        raise ValueError('invalid_session_state_payload')
    active.add(id(value))
    try:
        if isinstance(value, dict):
            if not all(type(key) is str for key in value):
                raise ValueError('invalid_session_state_payload')
            values = value.values()
        else:
            values = value
        for item in values:
            _json_value(item, active)
    finally:
        active.remove(id(value))


def _encode(payload):
    try:
        if type(payload) is not dict:
            raise ValueError('invalid_session_state_payload')
        _json_value(payload, set())
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > _PAYLOAD_LIMIT:
            raise ValueError('session_state_payload_too_large')
        return encoded
    except (TypeError, OverflowError, RecursionError, UnicodeError):
        raise ValueError('invalid_session_state_payload') from None


def _event(event):
    if not isinstance(event, dict):
        raise ValueError('invalid_session_state_event')
    source = event.get('source')
    if not isinstance(source, dict) or set(source) != {'kind', 'name'}:
        raise ValueError('invalid_session_state_source')
    hook, name = _name(event.get('hook_name')), _name(source['name'])
    if source['kind'] == 'flow':
        flow = name
    elif source['kind'] == 'hook' and name == hook:
        flow = None
    else:
        raise ValueError('invalid_session_state_source')
    return hook, flow, _name(event.get('type')), _encode(event.get('payload'))


def _timestamp():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _prepare_path(database_path, deadline):
    _remaining(deadline)
    path = Path(database_path) if database_path is not None else ROOT / '.soh/session-state.sqlite3'
    if not path.is_absolute():
        raise ValueError('session_state_database_path_must_be_absolute')
    # Refuse symlinked directories as well as a symlinked database. Never resolve
    # a caller's path to a different target and then write through it.
    for parent in reversed(path.parents):
        if parent.is_symlink():
            raise ValueError('session_state_symlink')
        try:
            parent.mkdir(mode=0o700)
        except FileExistsError:
            if not parent.is_dir():
                raise ValueError('invalid_session_state_directory') from None
        else:
            if os.name == 'posix' and stat.S_IMODE(parent.stat().st_mode) != 0o700:
                raise ValueError('session_state_directory_permissions')
        _remaining(deadline)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    except FileExistsError:
        pass
    else:
        try:
            if os.name == 'posix' and stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                raise ValueError('session_state_database_permissions')
        finally:
            os.close(descriptor)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('invalid_session_state_database')
    _remaining(deadline)
    return path, (info.st_dev, info.st_ino)


class _Store:
    def __init__(self, connection, deadline):
        self.connection, self.deadline = connection, deadline

    def execute(self, sql, parameters=()):
        # Refresh before every potentially blocking SQL phase, including commit.
        budget = _remaining(self.deadline)
        self.connection.execute(f'PRAGMA busy_timeout={min(2147483647, max(0, int(budget * 1000)))}')
        try:
            result = self.connection.execute(sql, parameters)
        except sqlite3.OperationalError as exc:
            # SQLite's timeout is an integer number of milliseconds. Exhaustion
            # of the final fractional millisecond is still a deadline timeout.
            if (getattr(exc, 'sqlite_errorcode', None) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
                    and self.deadline - monotonic() < 0.002):
                raise TimeoutError('session_state_deadline') from None
            raise
        _remaining(self.deadline)
        return result

    def initialize(self):
        row = self.execute("SELECT sql FROM sqlite_master WHERE name='session_events'").fetchone()
        if row is None:
            self.execute(_TABLE)
        else:
            columns = self.execute('PRAGMA table_info(session_events)').fetchall()
            actual = [(r[1], r[2], r[3], r[5]) for r in columns]
            # Check the defining constraints too: a matching column list alone
            # would accept tables without the payload byte limit.
            normalize = lambda sql: re.sub(r'\s+', '', sql).lower().replace('ifnotexists', '')
            if actual != _COLUMNS or normalize(row[0] or '') != normalize(_TABLE):
                raise ValueError('incompatible_session_state_schema')
        self.execute('CREATE INDEX IF NOT EXISTS session_events_session_order ON session_events(session_id,event_id)')
        index = self.execute('PRAGMA index_info(session_events_session_order)').fetchall()
        if [r[2] for r in index] != ['session_id', 'event_id']:
            raise ValueError('incompatible_session_state_index')

    def history(self, session_id, flow_name):
        rows = self.execute('''SELECT event_id, session_id, hook_name, flow_name,
            event_type, recorded_at, payload_json FROM session_events
            WHERE session_id=? AND (flow_name=? OR
                (flow_name IS NULL AND hook_name='PostCompact' AND event_type='PostCompact'))
            ORDER BY event_id''', (session_id, flow_name)).fetchall()
        _remaining(self.deadline)
        history = []
        for event_id, session, hook, flow, kind, timestamp, encoded in rows:
            _remaining(self.deadline)
            try:
                payload = json.loads(encoded, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                _encode(payload)
            except (ValueError, TypeError, RecursionError, UnicodeError):
                raise ValueError('invalid_session_state_history') from None
            history.append(dict(event_id=event_id, session_id=session, hook_name=hook,
                                source={'kind': 'flow' if flow is not None else 'hook',
                                        'name': flow if flow is not None else hook},
                                type=kind, timestamp=timestamp, payload=payload))
        _remaining(self.deadline)
        return history

    def append(self, session_id, normalized):
        self.execute('''INSERT INTO session_events
            (session_id,hook_name,flow_name,event_type,recorded_at,payload_json)
            VALUES (?,?,?,?,?,?)''', (session_id, *normalized[:3], _timestamp(), normalized[3]))


@contextmanager
def _transaction(database_path, deadline):
    connection = None
    try:
        path, identity = _prepare_path(database_path, deadline)
        connection = sqlite3.connect(path, timeout=0, isolation_level=None)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != identity:
            raise ValueError('session_state_database_changed')
        connection.set_progress_handler(lambda: int(monotonic() >= deadline), 500)
        store = _Store(connection, deadline)
        store.execute('PRAGMA synchronous=FULL')
        # DELETE is the conservative rollback journal; never disable durability.
        store.execute('PRAGMA journal_mode=DELETE')
        store.execute('BEGIN IMMEDIATE')
        store.initialize()
        yield store
        store.execute('COMMIT')
    except sqlite3.Error:
        if monotonic() >= deadline:
            raise TimeoutError('session_state_deadline') from None
        raise RuntimeError('session_state_storage_failed') from None
    except TimeoutError:
        raise
    except OSError:
        raise RuntimeError('session_state_storage_failed') from None
    finally:
        if connection is not None:
            # Disable deadline interruption for cleanup. Closing also rolls back
            # if rollback itself fails (for example, injected I/O failure).
            connection.set_progress_handler(None, 0)
            try:
                if connection.in_transaction:
                    connection.rollback()
            finally:
                connection.close()


def save_event(session_id, event, *, deadline, database_path=None):
    """Append a validated envelope. Success returns None."""
    _remaining(deadline)
    _session(session_id)
    normalized = _event(event)
    _remaining(deadline)
    with _transaction(database_path, deadline) as store:
        store.append(session_id, normalized)


def inspect_and_save_event(session_id, decide, *, deadline, hook_name, flow_name, database_path=None):
    """Read, decide once, and optionally append within BEGIN IMMEDIATE."""
    _remaining(deadline)
    _session(session_id)
    _name(hook_name)
    _name(flow_name)
    if not callable(decide):
        raise ValueError('invalid_session_state_callback')
    with _transaction(database_path, deadline) as store:
        history = store.history(session_id, flow_name)
        result = decide(history)
        _remaining(deadline)
        if not isinstance(result, tuple) or len(result) != 2:
            raise ValueError('invalid_session_state_decision')
        decision, event = result
        allow = getattr(decision, 'allow', None)
        if type(allow) is not bool or (allow and not isinstance(event, dict)) or (not allow and event is not None):
            raise ValueError('invalid_session_state_decision')
        if allow:
            normalized = _event(event)
            if normalized[:2] != (hook_name, flow_name):
                raise ValueError('conflicting_session_state_origin')
            _remaining(deadline)
            store.append(session_id, normalized)
    return decision
