"""Read-only diagnostics for the CLI, without importing model runtimes."""
import os
from pathlib import Path
import signal
import sys
import sysconfig


def run():
    import log_utils

    log_path = log_utils.LOG_PATH or log_utils.LOG_ROOT
    launcher = Path(sysconfig.get_path('scripts')) / 'system-one-harness'
    print(f'Python interpreter: {sys.executable}')
    print(f'Python version: {sys.version.split()[0]}')
    print(f'Package directory: {Path(log_utils.__file__).resolve().parent}')
    print(f'CLI install location (standard environment): {launcher}')
    print(f'Log location: {log_path}')
    python_ok = sys.version_info >= (3, 11)
    platform_ok = hasattr(signal, 'SIGALRM') and hasattr(signal, 'setitimer')
    print(f'Python >= 3.11: {"OK" if python_ok else "FAIL"}')
    print(f'Gateway timer support: {"OK" if platform_ok else "FAIL"}')
    try:
        probe = log_path
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        if log_path.exists():
            writable = os.access(log_path, os.W_OK | os.X_OK) if log_path.is_dir() else os.access(log_path, os.W_OK)
        else:
            writable = probe.is_dir() and os.access(probe, os.W_OK | os.X_OK)
        print(f'Log write access (estimate; no write attempted): {"OK" if writable else "FAIL"}')
    except OSError as exc:
        writable = False
        print(f'Log path check: FAIL ({exc})')
    print('Laya/MLX, model weights, Node/helper and Claude plugin loading: not checked.')
    print('Use hook --hook UserPromptSubmit --observe-only for a real logging check.')
    return 0 if python_ok and platform_ok and writable else 1
