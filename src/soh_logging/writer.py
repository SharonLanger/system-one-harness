"""One local JSONL writer used by production callers."""
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import stat
import threading
import time

from .capture import current_sink
from .context import current_context
from .records import ComponentRef, LogRecord
from .serialization import encode, normalize_details
from .vocabulary import ComponentName, ComponentType, LogEvent, LogLevel


@dataclass(frozen=True)
class WriterConfig:
    root: Path
    lock_wait_seconds: float = 0.05
    legacy_path: Path | None = None


class LogWriter:
    _thread_lock = threading.RLock()

    def __init__(self, config):
        self.config = config

    def emit(self, level: LogLevel, component: ComponentRef, event: LogEvent,
             message: str, details=None):
        if not isinstance(level, LogLevel) or not isinstance(event, LogEvent):
            raise ValueError('level and event must be predefined enums')
        if (not isinstance(component, ComponentRef)
                or (component.component_type is not None and not isinstance(component.component_type, ComponentType))
                or (component.component is not None and not isinstance(component.component, ComponentName))):
            raise ValueError('component must use predefined enums')
        if not isinstance(message, str) or not message.strip():
            raise ValueError('nonempty message required')
        now = datetime.now(timezone.utc)
        context = current_context()
        record = LogRecord(1, now.isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
                           level, ' '.join(message.splitlines()), event,
                           component.component_type, component.component,
                           context.hook_event, context.session_id, context.correlation_id,
                           context.flow_name, os.getpid(), normalize_details(details or {}))
        data = encode(record)
        sink = current_sink()
        if sink is not None:
            sink(record.__dict__.copy())
            return record
        root = self.config.root
        destination = self.config.legacy_path or (root / now.strftime('%Y-%m-%d') / now.strftime('%H-00.jsonl'))
        if not self._thread_lock.acquire(timeout=self.config.lock_wait_seconds):
            raise OSError('log writer thread lock timeout')
        try:
            root.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._check_directory(root)
            lock_path = root / '.write.lock'
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            try:
                if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                    raise OSError('log lock is not a regular file')
                deadline = time.monotonic() + self.config.lock_wait_seconds
                while True:
                    try:
                        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise OSError('log writer lock timeout')
                        time.sleep(min(0.005, max(0, deadline - time.monotonic())))
                destination.parent.mkdir(mode=0o700, exist_ok=True)
                self._check_directory(destination.parent)
                # A preexisting FIFO must fail instead of blocking in open()
                # before the regular-file check can run. Regular files ignore
                # O_NONBLOCK; fstat still rejects other incompatible objects.
                fd = os.open(destination, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NONBLOCK |
                             getattr(os, 'O_NOFOLLOW', 0), 0o600)
                try:
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        raise OSError('log destination is not a regular file')
                    view = memoryview(data)
                    while view:
                        written = os.write(fd, view)
                        if written <= 0:
                            raise OSError('short log write')
                        view = view[written:]
                finally:
                    os.close(fd)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
        finally:
            self._thread_lock.release()
        return record

    @staticmethod
    def _check_directory(path):
        if path.is_symlink() or not path.is_dir():
            raise OSError(f'unsafe log directory: {path}')
