"""UTC routing, hostile paths, coordinated appends and process-death limits."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import select
import subprocess
import sys

import pytest

from soh_logging import (ComponentName, ComponentRef, ComponentType, LogEvent,
                         FlowName, LogLevel, LogWriter, WriterConfig)


PRELUDE = '''
from pathlib import Path
import sys
from soh_logging import (ComponentName, ComponentRef, ComponentType, FlowName, HookEvent,
                         LogEvent, LogLevel, LogWriter, WriterConfig, bind, invocation, set_session)
ref = ComponentRef(ComponentType.BLOCK, ComponentName.BLOCK_RUNTIME)
def emit(writer, **details):
    return writer.emit(LogLevel.INFO, ref, LogEvent.PREPARED, 'Prepared input.', details)
'''
ENV = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src')}


def emit(root, **details):
    return LogWriter(WriterConfig(root, lock_wait_seconds=0.1)).emit(
        LogLevel.INFO, ComponentRef(ComponentType.BLOCK, ComponentName.BLOCK_RUNTIME),
        LogEvent.PREPARED, 'Prepared input.', details)


def rows(root):
    return [json.loads(line) for path in sorted(root.glob('*/*.jsonl'))
            for line in path.read_text().splitlines()]


def ready(process):
    assert select.select([process.stdout], [], [], 10)[0], 'child did not become ready'
    assert process.stdout.readline() == b'ready\n'


def stop(process):
    if process.poll() is None:
        process.kill()
    process.communicate(timeout=5)


@pytest.mark.parametrize('before,after', [
    ('2024-06-03T08:59:59+00:00', '2024-06-03T09:00:00+00:00'),
    ('2024-06-03T23:59:59+00:00', '2024-06-04T00:00:00+00:00'),
    ('2024-04-30T23:59:59+00:00', '2024-05-01T00:00:00+00:00'),
    ('2024-12-31T23:59:59+00:00', '2025-01-01T00:00:00+00:00'),
    ('2024-02-28T23:59:59+00:00', '2024-02-29T00:00:00+00:00'),
    ('2024-02-29T23:59:59+00:00', '2024-03-01T00:00:00+00:00'),
])
def test_consecutive_writes_cross_utc_boundaries(tmp_path, monkeypatch, before, after):
    import soh_logging.writer as module
    captured = datetime.fromisoformat(before)

    class Clock:
        @staticmethod
        def now(zone):
            assert zone is timezone.utc
            return captured

    monkeypatch.setattr(module, 'datetime', Clock)
    root = tmp_path / '.logs'
    emit(root, index=0)
    emit(root, index=1)
    first, = root.glob('*/*.jsonl')
    assert [row['details']['index'] for row in rows(root)] == [0, 1]
    captured = datetime.fromisoformat(after)
    emit(root, index=2)
    assert {str(path.relative_to(root)) for path in root.glob('*/*.jsonl')} == {
        f'{before[:10]}/{before[11:13]}-00.jsonl', f'{after[:10]}/{after[11:13]}-00.jsonl'}
    assert first.stat().st_mode & 0o777 == 0o600
    assert first.parent.stat().st_mode & 0o777 == 0o700
    assert [row['details']['index'] for row in rows(root)] == [0, 1, 2]


def test_wait_crossing_hour_uses_event_time_not_lock_release_time(tmp_path, monkeypatch):
    import soh_logging.writer as module
    captured = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    after = datetime(2025, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

    class Clock:
        @staticmethod
        def now(zone):
            return captured

    original = module.fcntl.flock
    attempts = 0

    def delayed(fd, flags):
        nonlocal attempts, captured
        if flags & fcntl.LOCK_NB:
            attempts += 1
            if attempts == 1:
                captured = after
                raise BlockingIOError('controlled contention')
        return original(fd, flags)

    monkeypatch.setattr(module, 'datetime', Clock)
    monkeypatch.setattr(module.fcntl, 'flock', delayed)
    root = tmp_path / '.logs'
    emit(root)
    assert attempts == 2
    path, = root.glob('*/*.jsonl')
    assert path.relative_to(root) == Path('2024-12-31/23-00.jsonl')
    assert rows(root)[0]['timestamp'] == '2024-12-31T23:59:59.000Z'


@pytest.mark.parametrize('stamp', ['2024-01-15T00:15:00+00:00', '2024-07-15T00:15:00+00:00'])
def test_non_utc_host_timezone_cwd_and_input_paths_do_not_change_destination(tmp_path, stamp):
    root = tmp_path / '.logs'
    legacy = tmp_path / 'logs' / 'debug.jsonl'
    legacy.parent.mkdir()
    legacy.write_bytes(b'legacy bytes\r\n{"old": true}\n')
    before = legacy.read_bytes()
    script = PRELUDE + '''
import os, time
from datetime import datetime, timezone
import soh_logging.writer as module
from log_utils import log
# POSIX TZ rule works without depending on a host timezone database.
time.tzset()
assert time.timezone != 0
class Clock:
    @staticmethod
    def now(zone):
        assert zone is timezone.utc
        return datetime.fromisoformat(sys.argv[1])
module.datetime = Clock
with invocation('../../odd-invocation', HookEvent.USERPROMPTSUBMIT):
    set_session('../../outside/session', correlation_id='../../odd-invocation')
    log('INFO', 'block_runtime', 'prepared', path='/tmp/escape', nested={'path': '../../escape'})
'''
    for cwd in [tmp_path / 'one', tmp_path / 'two']:
        cwd.mkdir()
        result = subprocess.run([sys.executable, '-c', script, stamp], cwd=cwd,
                                env={**ENV, 'SOH_LOG_ROOT': str(root), 'TZ': 'EST5EDT,M3.2.0,M11.1.0'},
                                capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr.decode()
        assert list(cwd.iterdir()) == []
    path, = root.glob('*/*.jsonl')
    assert str(path.relative_to(root)) == f'{stamp[:10]}/{stamp[11:13]}-00.jsonl'
    assert len(rows(root)) == 2
    assert {row['timestamp'] for row in rows(root)} == {stamp[:19] + '.000Z'}
    assert {row['session_id'] for row in rows(root)} == {'../../outside/session'}
    assert legacy.read_bytes() == before
    assert {path.name for path in root.iterdir()} == {stamp[:10], '.write.lock'}


def test_synchronized_threads_and_processes_append_varied_records(tmp_path):
    root = tmp_path / '.logs'
    script = PRELUDE + '''
from concurrent.futures import ThreadPoolExecutor
import threading
writer = LogWriter(WriterConfig(Path(sys.argv[1]), lock_wait_seconds=3))
barrier = threading.Barrier(3)
def thread(index):
    correlation = f'{sys.argv[2]}-{index}'
    flow = list(FlowName)[index % 2]
    with invocation(correlation, HookEvent.USERPROMPTSUBMIT), bind(flow_name=flow):
        set_session('session-' + correlation, correlation_id=correlation)
        barrier.wait(timeout=5)
        for seq, size in enumerate([0, 17, 4096, 65536, 144000]):
            emit(writer, worker=correlation, seq=seq, body='x' * size)
print('ready', flush=True)
sys.stdin.readline()
with ThreadPoolExecutor(max_workers=3) as pool:
    list(pool.map(thread, range(3)))
'''
    workers = [subprocess.Popen([sys.executable, '-c', script, str(root), str(index)], env=ENV,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
               for index in range(3)]
    try:
        for worker in workers:
            ready(worker)
        assert not root.exists()
        for worker in workers:
            worker.stdin.write(b'go\n')
            worker.stdin.flush()
        for worker in workers:
            _, stderr = worker.communicate(timeout=20)
            assert worker.returncode == 0, stderr.decode()
    finally:
        for worker in workers:
            stop(worker)
    records = rows(root)
    expected = {(f'{process}-{thread}', seq) for process in range(3)
                for thread in range(3) for seq in range(5)}
    assert len(records) == len(expected)
    assert {(row['details']['worker'], row['details']['seq']) for row in records} == expected
    for row in records:
        worker = row['details']['worker']
        assert row['correlation_id'] == worker and row['session_id'] == 'session-' + worker
        assert row['flow_name'] == list(FlowName)[int(worker[-1]) % 2]
        assert len(row['details']['body']) == [0, 17, 4096, 65536, 144000][row['details']['seq']]


@pytest.mark.parametrize('failure', ['interrupted', 'zero'])
def test_partial_write_interruption_or_zero_reports_failure_and_releases_lock(tmp_path, monkeypatch, failure):
    import soh_logging.writer as module
    original = module.os.write
    calls = 0

    def injected(fd, data):
        nonlocal calls
        calls += 1
        if calls == 1:
            return original(fd, data[:20])
        if failure == 'interrupted':
            raise InterruptedError('interrupted after partial progress')
        return 0

    root = tmp_path / '.logs'
    monkeypatch.setattr(module.os, 'write', injected)
    with pytest.raises(OSError):
        emit(root)
    path, = root.glob('*/*.jsonl')
    assert len(path.read_bytes()) == 20 and not path.read_bytes().endswith(b'\n')
    monkeypatch.setattr(module.os, 'write', original)
    # A partial final record is permitted after an unsuccessful write. Remove
    # only this test's tail before proving a subsequent writer can acquire locks.
    path.write_bytes(b'')
    emit(root, recovered=True)
    assert rows(root)[0]['details'] == {'recovered': True}


def test_cross_process_lock_timeout_and_release_on_process_death(tmp_path):
    root = tmp_path / '.logs'
    root.mkdir()
    script = '''
import fcntl, sys
with open(sys.argv[1], 'a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    print('ready', flush=True)
    sys.stdin.read()
'''
    holder = subprocess.Popen([sys.executable, '-c', script, str(root / '.write.lock')],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        ready(holder)
        attempt = subprocess.run([sys.executable, '-c', PRELUDE + '''
try:
    emit(LogWriter(WriterConfig(Path(sys.argv[1]), lock_wait_seconds=0.02)))
except OSError as error:
    assert 'lock timeout' in str(error)
else:
    raise AssertionError('contended write falsely succeeded')
''', str(root)], env=ENV, capture_output=True, timeout=5)
        assert attempt.returncode == 0, attempt.stderr.decode()
        assert not list(root.glob('*/*.jsonl'))
        holder.kill()
        holder.communicate(timeout=5)
        followup = subprocess.run([sys.executable, '-c', PRELUDE + '''
emit(LogWriter(WriterConfig(Path(sys.argv[1]))), recovered=True)
''', str(root)], env=ENV, capture_output=True, timeout=5)
        assert followup.returncode == 0, followup.stderr.decode()
        assert rows(root)[0]['details'] == {'recovered': True}
    finally:
        stop(holder)


def test_killed_mid_record_can_leave_partial_tail_and_releases_lock(tmp_path):
    root = tmp_path / '.logs'
    script = PRELUDE + '''
import os
import soh_logging.writer as module
original = os.write
def halt_mid_write(fd, data):
    count = original(fd, data[:37])
    print('ready', flush=True)
    sys.stdin.read()
    return count
module.os.write = halt_mid_write
emit(LogWriter(WriterConfig(Path(sys.argv[1]))), body='x' * 10000)
'''
    child = subprocess.Popen([sys.executable, '-c', script, str(root)], env=ENV,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        ready(child)
        child.kill()
        child.communicate(timeout=5)
        path, = root.glob('*/*.jsonl')
        tail = path.read_bytes()
        assert len(tail) == 37 and not tail.endswith(b'\n')
        with pytest.raises(json.JSONDecodeError):
            json.loads(tail)
        emit(root, subsequent=True)
        # Append coordination releases on death, but intentionally does not
        # repair a killed writer's incomplete bytes.
        assert path.read_bytes().startswith(tail)
        with pytest.raises(json.JSONDecodeError):
            json.loads(path.read_text())
    finally:
        stop(child)


@pytest.mark.parametrize('target,kind', [('lock', 'directory'), ('lock', 'fifo'),
                                         ('destination', 'directory'), ('destination', 'fifo')])
def test_nonregular_lock_or_destination_fails_without_hanging(tmp_path, target, kind):
    root = tmp_path / '.logs'
    root.mkdir()
    now = datetime.now(timezone.utc)
    if target == 'lock':
        path = root / '.write.lock'
    else:
        day = root / now.strftime('%Y-%m-%d')
        day.mkdir()
        path = day / now.strftime('%H-00.jsonl')
    if kind == 'directory':
        path.mkdir()
    else:
        os.mkfifo(path)
    result = subprocess.run([sys.executable, '-c', PRELUDE + '''
from datetime import datetime
import soh_logging.writer as module
class Clock:
    @staticmethod
    def now(zone):
        return datetime.fromisoformat(sys.argv[2])
module.datetime = Clock
try:
    emit(LogWriter(WriterConfig(Path(sys.argv[1]))))
except OSError:
    pass
else:
    raise AssertionError('nonregular object accepted')
''', str(root), now.isoformat()], env=ENV, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr.decode()
