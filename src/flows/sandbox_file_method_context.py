"""Retired sandbox for the original explanation/dependency-context experiments."""
from pathlib import Path
import re
from log_utils import log
from soh_logging import FlowName, flow_scope

from building_blocks.file_reference_utils import detect_file_references, detect_configured_references
from building_blocks.resolve_file import resolve_file
from building_blocks.typescript import DEFAULT_HELPER, TypeScriptError, outline, collect_project_context
from building_blocks.questions.method_selection.block import run as select_method

# V1 recognizes complete .ts paths; callers may still supply their own pattern.
DEFAULT_REFERENCE_PATTERN = re.compile(r'''(?<![\w./-])(?P<quote>["'`])?(?P<path>(?(quote)[^"'`\r\n]+|[^\s"'`<>:,;!?()]+)\.ts)(?(quote)(?P=quote))(?::\d+(?::\d+)?)?(?=$|[\s"'`<>:,;!?()]|\.(?=$|\s))''')
_OMITTED_REFERENCE_PATTERN = object()
ELIGIBLE = re.compile(r'^\s*Explain\s+`(?P<target>[^`\r\n]+)`\s*:\s*(?P<question>\S[\s\S]*)$', re.IGNORECASE)


def _noop(reason):
    log("INFO", "sandbox_file_method_context", "finished", status="noop", reason=reason)
    return {'status': 'noop', 'reason': reason, 'context': None}


def _confined(path, root):
    canonical = path.resolve(strict=True)
    return canonical == path and canonical.is_relative_to(root)


@flow_scope(FlowName.SANDBOX_FILE_METHOD_CONTEXT)
def run(prompt, *, cwd, repository_root, config, registry,
        reference_pattern=_OMITTED_REFERENCE_PATTERN, node='node', helper=DEFAULT_HELPER,
        timeout=5.0, max_candidates=64, max_input_chars=16000,
        fallback_config=None, fallback_max_input_chars=16000,
        max_dependency_depth=2, max_dependency_chars=12000, resolve_from_project_root=False):
    """Only `Explain `backtick path`: question` qualifies for this first flow.

    Exactly one lexical file mention must equal that target. This is an explicit
    trigger, not a general intent classifier. All source work is read-only; only
    SDK load/predict is injected for offline use. Dependency context is partial.
    """
    log("INFO", "sandbox_file_method_context", "started", prompt=prompt,
        cwd=cwd, repository_root=repository_root)
    if type(resolve_from_project_root) is not bool:
        return _noop('invalid_resolution_mode')
    if (type(max_dependency_depth) is not int or not 0 <= max_dependency_depth <= 20
            or type(max_dependency_chars) is not int or not 1 <= max_dependency_chars <= 1000000):
        return _noop('invalid_dependency_budget')
    if (type(fallback_max_input_chars) is not int or fallback_max_input_chars <= 0):
        return _noop('invalid_fallback_budget')
    if not isinstance(prompt, str) or not re.match(r'^\s*Explain\b', prompt, re.IGNORECASE):
        return _noop('ineligible_request')
    try:
        references = (detect_configured_references(prompt,
            {'include_patterns': [r'\.ts$'], 'exclude_patterns': []}, node=node, helper=helper, timeout=timeout)
            if reference_pattern is _OMITTED_REFERENCE_PATTERN
            else detect_file_references(prompt, reference_pattern))
    except TypeScriptError as exc:
        log("ERROR", "extractor", "reference_transport_failed", error=exc)
        return _noop('extractor_error')
    except (ValueError, TypeError) as exc:
        log("ERROR", "sandbox_file_method_context", "reference_failed", error=exc)
        return _noop('invalid_reference')
    log("DEBUG", "sandbox_file_method_context", "references_detected", references=references)
    if not references:
        return _noop('no_file')
    if len(references) != 1:
        return _noop('multiple_files')
    eligible = ELIGIBLE.fullmatch(prompt)
    reference = references[0]
    if not eligible or reference.path != eligible.group('target'):
        return _noop('unsupported_request')
    if reference.kind != 'production_code' or not reference.path.endswith('.ts'):
        return _noop('excluded')
    try:
        resolution = resolve_file(reference, cwd=cwd, repository_root=repository_root,
                                  node=node, helper=helper, timeout=timeout,
            **({'resolve_from_project_root': True} if resolve_from_project_root else {}))
        log("DEBUG", "sandbox_file_method_context", "file_resolved", resolution=resolution)
        if resolution.status != 'resolved':
            return _noop(resolution.status)
        path = resolution.selected_path
        # A generic resolver can return a differently named symlink target;
        # eligibility remains this flow's production-TypeScript policy.
        if not path.name.endswith('.ts') or path.name.endswith(('.spec.ts', '.test.ts', '.d.ts')):
            return _noop('excluded')
        root = Path(repository_root).resolve(strict=True)
        if not _confined(path, root):
            return _noop('source_changed')
        source_outline = outline(path, node=node, helper=helper, timeout=timeout)
    except (OSError, ValueError, TypeError, TypeScriptError) as exc:
        log("ERROR", "extractor", "resolve_or_outline_failed", error=exc, reference=reference.path)
        return _noop('extractor_error')
    try:
        selection = select_method(prompt, source_outline, config=config, registry=registry,
                                  max_candidates=max_candidates, max_input_chars=max_input_chars)
        multiple = selection['decisions'].get('multiple_relevant', {})
        if (fallback_config is not None and selection['reason'] == 'method_undecided'
                and multiple.get('decided') is True and multiple.get('answer') == 'no'):
            # Fixed, opt-in representation experiment; never retry an explicit
            # negative/multi signal, failure, or a second-round rejection.
            second_text = prompt + '\n\nFallback candidate signatures (same source snapshot):\n' + '\n'.join(
                f"{candidate['signature']}" for candidate in source_outline['callables'])
            selection = select_method(second_text, source_outline, config=fallback_config, registry=registry,
                                      max_candidates=max_candidates, max_input_chars=fallback_max_input_chars)
    except TimeoutError as exc:
        log("ERROR", "sandbox_file_method_context", "selection_timeout", error=exc)
        return _noop('deadline_exceeded')
    except Exception as exc:
        log("ERROR", "sandbox_file_method_context", "selection_failed", error=exc)
        return _noop('model_error')
    log("DEBUG", "sandbox_file_method_context", "method_selected", selection=selection)
    if selection['selected_id'] is None:
        return _noop(selection['reason'])
    try:
        if not _confined(path, root):
            return _noop('source_changed')
        context = collect_project_context(path, selection['selected_id'], node=node, helper=helper, timeout=timeout,
                                        repository_root=root, max_depth=max_dependency_depth, max_dependency_chars=max_dependency_chars)
        mapped = selection['candidate_map'][selection['selected_id']]
        if (context['path'] != mapped['path'] or context['source_hash'] != mapped['source_hash']
                or context['callable'] != {k: v for k, v in mapped.items() if k not in {'path', 'source_hash'}}
                or not isinstance(context['code'], str) or not context['code']):
            log("ERROR", "extractor", "context_snapshot_mismatch", expected=mapped, context=context)
            return _noop('extractor_error')
        context['coverage'] = 'partial'
        log("INFO", "sandbox_file_method_context", "finished", status="context",
            path=path, callable=context['callable'])
        return {'status': 'context', 'reason': None, 'context': context}
    except (OSError, ValueError, TypeError, KeyError, TypeScriptError) as exc:
        log("ERROR", "extractor", "context_collection_failed", error=exc,
            path=path, selected_id=selection['selected_id'])
        return _noop('extractor_error')
