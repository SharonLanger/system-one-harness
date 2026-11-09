"""Schema-aware extraction; raw documents remain intact."""
from dataclasses import dataclass
from datetime import datetime
from .query import NULLABLE, ValidationError, instant, strict_json

@dataclass(frozen=True)
class RecordDiagnostic:
    code: str
    source: object = None
    severity: str = 'warning'
    field: str | None = None

@dataclass(frozen=True)
class ParsedRecord:
    fields: dict
    original: dict
    timestamp: datetime
    source: object
    diagnostics: tuple[RecordDiagnostic,...] = ()

class LogRecordParser:
    def parse(self, raw_line, source=None):
        try:
            data = strict_json(raw_line)
            if not isinstance(data,dict): return RecordDiagnostic('invalid_record',source)
            if type(data.get('schema_version')) is not int or data.get('schema_version') != 1:
                return RecordDiagnostic('unsupported_schema',source)
            fields = {}
            for key in ('level','event','message'):
                v = data.get(key)
                if not isinstance(v,str) or (key != 'message' and not v.strip()):
                    return RecordDiagnostic('invalid_field',source,field=key)
                fields[key] = v
            if type(data.get('pid')) is not int: return RecordDiagnostic('invalid_field',source,field='pid')
            fields['pid'] = data['pid']
            timestamp = instant(data.get('timestamp'),'timestamp')
            fields['timestamp'] = data['timestamp']
            fields['schema_version'] = 1
            warnings = []
            for key in sorted(NULLABLE):
                value = data.get(key)
                if value is not None and not isinstance(value,str): return RecordDiagnostic('invalid_field',source,field=key)
                if key not in data: warnings.append(RecordDiagnostic('missing_context_field',source,field=key))
                fields[key] = value
            if 'details' not in data: warnings.append(RecordDiagnostic('missing_details',source))
            fields['details'] = data.get('details',{})
            if not isinstance(fields['details'],dict): warnings.append(RecordDiagnostic('non_object_details',source))
            # Ensure escaped invalid surrogate values cannot break JSON detail responses.
            from .query import compact
            compact(data).encode('utf-8')
            return ParsedRecord(fields,data,timestamp,source,tuple(warnings))
        except ValidationError as exc:
            return RecordDiagnostic(exc.code,source,field=exc.path)
        except (UnicodeError, ValueError, RecursionError):
            return RecordDiagnostic('invalid_record',source)
