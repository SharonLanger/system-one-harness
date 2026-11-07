"""State secrecy assertions report channel names without reproducing payloads."""
import pytest


def assert_no_payload_leaks(channels, markers):
    for label, value in channels.items():
        if any(marker in str(value) for marker in markers):
            pytest.fail(f'session payload exposed through {label}',pytrace=False)


def capture_state_error(operation):
    """Let secrecy checks inspect an error before pytest can render its text."""
    try:
        operation()
    except Exception as error:
        return type(error),repr(error)
    return None,''
