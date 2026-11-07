"""Normalize event-specific values without losing valid sibling fields."""
from dataclasses import asdict
from enum import Enum
import json
from pathlib import Path


def _safe_repr(value):
    try:
        return repr(value)
    except Exception:
        return f'<unrepresentable {type(value).__name__}>'


def normalize_details(details):
    errors = []
    active = set()

    def walk(value, path):
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            if value != value or value in (float('inf'), float('-inf')):
                errors.append(path)
                return _safe_repr(value)
            return value
        if isinstance(value, Enum):
            return walk(value.value, path)
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, BaseException):
            import traceback
            return {'type': type(value).__name__, 'message': str(value),
                    'traceback': ''.join(traceback.format_exception(value))}
        if id(value) in active:
            errors.append(path)
            return '<cycle>'
        if isinstance(value, (dict, list, tuple)):
            active.add(id(value))
            try:
                if isinstance(value, dict):
                    result = {}
                    for key, item in value.items():
                        name = key if isinstance(key, str) else _safe_repr(key)
                        result[name] = walk(item, f'{path}.{name}')
                    return result
                return [walk(item, f'{path}[{i}]') for i, item in enumerate(value)]
            finally:
                active.remove(id(value))
        errors.append(path)
        return _safe_repr(value)

    result = walk(details, 'details')
    if errors:
        result['_serialization_errors'] = errors
    return result


def encode(record):
    return (json.dumps(asdict(record), ensure_ascii=True, allow_nan=False,
                       separators=(',', ':')) + '\n').encode('utf-8')
