"""Storage-independent immutable queries and strict request validation."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import re

FIELDS = ('session_id', 'correlation_id', 'flow_name', 'component', 'component_type', 'level', 'event', 'hook_event')
NULLABLE = frozenset(FIELDS) - {'level', 'event'}
UTC = timezone.utc

class ValidationError(ValueError):
    def __init__(self, code, path):
        self.code, self.path = code, path
        super().__init__(f'{code} at {path}')
    def as_dict(self):
        return {'code': self.code, 'path': self.path}

def fail(code, path):
    raise ValidationError(code, path)

def obj(value, path):
    if not isinstance(value, dict): fail('invalid_type', path)
    return value

def keys(value, allowed, path='', code='unknown_field'):
    for key in value:
        if key not in allowed: fail(code, f'{path}.{key}' if path else key)

def integer(value, path, low, high):
    if type(value) is not int: fail('invalid_type', path)
    if not low <= value <= high: fail('out_of_range', path)
    return value

def instant(value, path):
    if not isinstance(value, str): fail('invalid_type', path)
    if not re.search(r'(Z|[+-]\d{2}:\d{2})$', value): fail('timezone_required', path)
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)', value):
        fail('invalid_timestamp', path)
    try: return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone(UTC)
    except (ValueError, OverflowError): fail('invalid_timestamp', path)

def iso(value):
    return value.isoformat(timespec='microseconds').replace('+00:00', 'Z') if value else None

def strict_json(raw):
    def pairs(items):
        out = {}
        for k,v in items:
            if k in out: fail('duplicate_key', '$')
            out[k] = v
        return out
    def constant(_): fail('non_finite_number', '$')
    try:
        if isinstance(raw,bytes): raw=raw.decode("utf-8")
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
        # Overflowing exponent floats also become non-finite in Python.
        def finite(v):
            import math
            if isinstance(v, float) and not math.isfinite(v): fail('non_finite_number', '$')
            if isinstance(v, dict):
                for x in v.values(): finite(x)
            elif isinstance(v, list):
                for x in v: finite(x)
        finite(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc,ValidationError): raise
        fail('invalid_json', '$')

def compact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)

@dataclass(frozen=True)
class ValueSelector:
    values: tuple[str, ...] = ()
    include_null: bool = False

@dataclass(frozen=True)
class LogQuery:
    start: datetime | None
    end: datetime | None
    selectors: tuple[tuple[str, ValueSelector], ...] = ()
    text: str | None = None
    text_scope: str = 'message'
    page_size: int = 200
    order: str = 'file_append_desc'
    def canonical(self):
        return {'query_version': 1, 'start': iso(self.start), 'end': iso(self.end),
                'selectors': {k: {'values': list(v.values), 'include_null': v.include_null} for k,v in self.selectors},
                'text': {'value': self.text, 'scope': self.text_scope} if self.text else None,
                'page_size': self.page_size, 'order': self.order}
    def matches(self, record):
        f = record.fields
        if self.start is not None and record.timestamp < self.start: return False
        if self.end is not None and record.timestamp >= self.end: return False
        for key, selector in self.selectors:
            value = f[key]
            if not ((value is None and selector.include_null) or value in selector.values): return False
        if self.text:
            needle = self.text.casefold()
            if needle not in f['message'].casefold():
                if self.text_scope != 'message_and_details' or needle not in compact(f['details']).casefold(): return False
        return True

class QueryBuilder:
    @staticmethod
    def build(request, now):
        obj(request, '$')
        keys(request, {'query_version','time','filters','text','page_size','order'})
        version = request.get('query_version', 1)
        if type(version) is not int: fail('invalid_type', 'query_version')
        if version != 1: fail('unsupported_version', 'query_version')
        page = integer(request.get('page_size', 200), 'page_size', 1, 200)
        order = request.get('order', 'file_append_desc')
        if not isinstance(order, str): fail('invalid_type', 'order')
        if order != 'file_append_desc': fail('unsupported_value', 'order')
        t = request.get('time')
        if t is None: t = {'mode':'relative','seconds':3600}
        obj(t, 'time')
        mode = t.get('mode')
        if mode not in ('all', 'absolute', 'relative'): fail('unsupported_value', 'time.mode')
        keys(t, {'mode'} | ({'seconds'} if mode == 'relative' else {'start','end'} if mode == 'absolute' else set()), 'time', 'unexpected_field')
        start = end = None
        if mode == 'relative':
            seconds = integer(t.get('seconds'), 'time.seconds', 1, 604800)
            end = now.astimezone(UTC)
            start = end - timedelta(seconds=seconds)
        if mode == 'absolute':
            start = instant(t['start'],'time.start') if t.get('start') is not None else None
            end = instant(t['end'],'time.end') if t.get('end') is not None else None
            if start is None and end is None: fail('missing_bound', 'time')
            if start is not None and end is not None and start >= end: fail('invalid_range','time')
        filters = request.get('filters')
        filters = {} if filters is None else obj(filters, 'filters')
        keys(filters, FIELDS, 'filters')
        selectors = []
        for k, raw in filters.items():
            if raw is None: continue
            path = f'filters.{k}'
            obj(raw,path); keys(raw, {'values','include_null'}, path)
            values = raw.get('values', [])
            if not isinstance(values,list): fail('invalid_type',path+'.values')
            if len(values)>100: fail('too_many_values',path+'.values')
            for i,v in enumerate(values):
                p = f'{path}.values[{i}]'
                if not isinstance(v,str): fail('invalid_type',p)
                if not v.strip(): fail('blank_value',p)
                if len(v)>1024: fail('too_long',p)
                # Unpaired surrogates cannot be represented in UTF-8 assets/responses.
                try: v.encode('utf-8')
                except UnicodeError: fail('invalid_unicode',p)
            null = raw.get('include_null', False)
            if type(null) is not bool: fail('invalid_type',path+'.include_null')
            if null and k not in NULLABLE: fail('null_not_supported',path+'.include_null')
            if values or null: selectors.append((k, ValueSelector(tuple(sorted(set(values))),null)))
        text = request.get('text')
        value, scope = None, 'message'
        if text is not None:
            obj(text,'text'); keys(text, {'value','scope'}, 'text')
            scope = text.get('scope','message')
            if scope not in ('message','message_and_details'): fail('unsupported_value','text.scope')
            value = text.get('value','')
            if not isinstance(value,str): fail('invalid_type','text.value')
            if len(value)>1024: fail('too_long','text.value')
            try: value.encode('utf-8')
            except UnicodeError: fail('invalid_unicode','text.value')
        return LogQuery(start,end,tuple(sorted(selectors)),value or None,scope,page,order)
