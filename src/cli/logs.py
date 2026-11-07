"""Read recent local records without modifying the log directory."""
from collections import deque
import sys


def run(last):
    import log_utils

    try:
        if log_utils.LOG_PATH is not None:
            paths = [log_utils.LOG_PATH]
        else:
            paths = sorted(log_utils.LOG_ROOT.glob('*/*.jsonl'))
        if not paths:
            print(f'No log file yet: {log_utils.LOG_ROOT}', file=sys.stderr)
            return 0
        lines = deque(maxlen=last)
        for path in paths:
            with path.open(encoding='utf-8') as stream:
                lines.extend(stream)
    except FileNotFoundError:
        print(f'No log file yet: {log_utils.LOG_PATH or log_utils.LOG_ROOT}', file=sys.stderr)
        return 0
    except (OSError, UnicodeError) as exc:
        print(f'Cannot read log file {log_utils.LOG_PATH or log_utils.LOG_ROOT}: {exc}', file=sys.stderr)
        return 1
    sys.stdout.writelines(lines)
    return 0
