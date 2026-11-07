"""Pure session-enrichment policy; no I/O or model decisions."""
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SelectedMethod:
    file_path: str
    name: str
    signature: str
    start_line: int
    end_line: int

    def __post_init__(self):
        if (not isinstance(self.file_path, str) or not Path(self.file_path).is_absolute()
                or not isinstance(self.name, str) or not self.name.strip()
                or not isinstance(self.signature, str) or not self.signature.strip()
                or type(self.start_line) is not int or type(self.end_line) is not int
                or self.start_line <= 0 or self.end_line < self.start_line):
            raise ValueError('invalid_selected_method')


@dataclass(frozen=True)
class EnrichmentDecision:
    allow: bool
    reason: str
    matched_event_index: int | None = None
    latest_compaction_index: int | None = None
    legacy_event_indices: tuple[int, ...] = ()


def build_enrichment_event(selected):
    return {'source': {'kind': 'flow', 'name': 'first_method_context'},
            'hook_name': 'UserPromptSubmit', 'type': 'context_enriched', 'payload': {'files': [{
                'filePath': selected.file_path, 'methodNames': [selected.name],
                'methods': [{'name': selected.name, 'signature': selected.signature,
                             'startLine': selected.start_line, 'endLine': selected.end_line}]}]}}


def decide_enrichment(history, selected):
    """Append order determines the current compaction interval, never timestamps."""
    boundary, matches, legacy = None, [], []
    expected = build_enrichment_event(selected)['payload']['files'][0]['methods'][0]
    for index, event in enumerate(history):
        if not isinstance(event, dict):
            continue
        source = event.get('source')
        if source == {'kind': 'hook', 'name': 'PostCompact'} and event.get('type') == 'PostCompact':
            boundary = index
        if source != {'kind': 'flow', 'name': 'first_method_context'} or event.get('type') != 'context_enriched':
            continue
        payload = event.get('payload')
        files = payload.get('files') if isinstance(payload, dict) else None
        if not isinstance(files, list):
            continue
        for file in files:
            if not isinstance(file, dict) or file.get('filePath') != selected.file_path:
                continue
            methods = file.get('methods')
            if not isinstance(methods, list):
                names = file.get('methodNames')
                if isinstance(names, list) and selected.name in names:
                    legacy.append(index)
                continue
            for method in methods:
                if (isinstance(method, dict) and all(method.get(k) == v for k, v in expected.items())
                        and type(method.get('startLine')) is int and type(method.get('endLine')) is int):
                    matches.append(index)
                    break
    latest = max(matches, default=None)
    if latest is not None and (boundary is None or latest > boundary):
        return EnrichmentDecision(False, 'already_enriched_since_compaction', latest, boundary, tuple(legacy))
    reason = ('no_history' if not history else 'compaction_after_enrichment'
              if latest is not None and boundary is not None else 'not_previously_enriched')
    return EnrichmentDecision(True, reason, latest, boundary, tuple(legacy))
