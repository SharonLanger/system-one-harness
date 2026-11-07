"""One selected TypeScript implementation and its direct outgoing locations.

The hook gateway owns transport/deadlines. Building blocks own semantic decisions;
the deterministic TypeScript helpers own parsing and source identity validation.
Tokenizer work is separate and outside this flow.
"""
from copy import copy
from dataclasses import replace
from pathlib import Path
import re

from block_runtime import BlockResult
from building_blocks import typescript
from building_blocks.file_reference_utils import detect_configured_references
from building_blocks.resolve_file import resolve_file
from hooks.contracts import Context, Failure, Noop
from hooks.session_state import inspect_and_save_event
from flows.first_method_context_utils import SelectedMethod, EnrichmentDecision, build_enrichment_event, decide_enrichment

_FLOW_ID = 'first_method_context'
_CAP = 8000


def _log(logger, event, **details):
    try:
        logger('INFO', event, flow_id=_FLOW_ID, **details)
    except TimeoutError:
        raise
    except Exception:
        pass


def _root(value):
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError('explicit project root required')
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('project root must be absolute')
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise ValueError('project root must be a directory')
    return path


def _eligible(path):
    return str(path).endswith('.ts') and not str(path).endswith(('.spec.ts', '.test.ts', '.d.ts'))


def _metadata(value):
    """Escape metadata controls only; source never passes through this helper."""
    escapes = {'\n': r'\n', '\r': r'\r', '\t': r'\t'}
    return ''.join(escapes.get(c, f'\\u{ord(c):04x}')
                   if ord(c) < 32 or 127 <= ord(c) <= 159 or c in '\u2028\u2029'
                   else c for c in value)


def _units(text):
    return len(text.encode('utf-16-le')) // 2


def _method_lines(candidate, code):
    first = candidate['location']['line']
    breaks = len(re.findall(r'\r\n|[\r\n\u2028\u2029]', code))
    if code.endswith(('\r', '\n', '\u2028', '\u2029')):
        breaks -= 1
    last = first + max(0, breaks)
    return first, last


def _method_header(path, candidate, code):
    first, last = _method_lines(candidate, code)
    separator = '\n' if code.endswith(('\n', '\u2028', '\u2029')) else '\n\n'
    return ('Enriched Prompt for System 1\n'
            f'File: {_metadata(path)} | Method: {_metadata(candidate["name"])} | Lines: {first}–{last}\n'
            + code + separator)


def _render(header, rows, method_name):
    section = (f'Methods called by {_metadata(method_name)}:\n' + '\n'.join(rows) + '\n') if rows else ''
    return header + section + ('\n' if rows else '') + 'Context scope: selected method only.'


def _pack(header, rows, method_name, limit):
    method_only = _render(header, [], method_name)
    if _units(method_only) > limit:
        return None
    if rows:
        complete = _render(header, rows, method_name)
        if _units(complete) <= limit:
            return complete, len(rows)
    return method_only, 0


def _block_data(result):
    if not isinstance(result, BlockResult) or result.status not in ('completed', 'skipped'):
        raise ValueError('invalid building-block result')
    if result.status == 'skipped':
        if not isinstance(result.reason, str) or not result.reason:
            raise ValueError('invalid building-block skip')
        return Noop(result.reason)
    if result.reason is not None or not isinstance(result.data, dict):
        raise ValueError('invalid completed building-block result')
    return result.data


def handle_prompt(request, logger, *, registry=None, session_store=None):
    """Adapt existing hook setup without allowing cwd-based configuration reads."""
    try:
        root = _root(getattr(request.config, 'repository_root', None))
    except TimeoutError:
        raise
    except (OSError, ValueError, RuntimeError):
        return Failure('invalid_project_root', 'configuration', 'repository_root')
    args = copy(request.config)
    config_path = getattr(args, 'config', None)
    if config_path is not None and not Path(config_path).is_absolute():
        args.config = str(root / config_path)
    from hooks.user_prompt_submit.prompt_context_enrichment import _prepare_feature_config
    prepared = _prepare_feature_config(replace(request, config=args))
    if isinstance(prepared, Failure):
        return prepared
    if registry is None:
        from laya.registry import AgentRegistry
        registry = AgentRegistry(real=True)
    return run(request.prompt, project_root=root, config=prepared['model_config'],
               registry=registry, logger=logger,
               max_context_chars=getattr(args, 'max_context_chars', _CAP),
               finalize_enrichment=lambda selected: finalize_enrichment(request, logger, selected,
                                                                        session_store=session_store))


def finalize_enrichment(request, logger, selected, *, session_store=None):
    """Commit the final state decision; ordinary persistence errors preserve context."""
    common = getattr(getattr(request, 'envelope', None), 'common', {})
    session_id = common.get('session_id') if isinstance(common, dict) else None
    details = dict(session_id=session_id, path=selected.file_path, method=selected.name,
                   start_line=selected.start_line, end_line=selected.end_line)
    def decide(history):
        decision = decide_enrichment(history, selected)
        return decision, build_enrichment_event(selected) if decision.allow else None
    try:
        deadline = getattr(request.config, 'deadline_at', None)
        if deadline is None or session_id is None:
            raise ValueError('session_state_metadata_missing')
        transaction = (session_store.inspect_and_save_event if session_store is not None
                       else inspect_and_save_event)
        decision = transaction(session_id, decide, deadline=deadline,
                               hook_name='UserPromptSubmit', flow_name=_FLOW_ID)
    except TimeoutError:
        raise
    except Exception as exc:
        _log(logger, 'session_state_enrichment_failed', error_type=type(exc).__name__,
             message='session_state_unavailable', **details)
        return EnrichmentDecision(True, 'state_unavailable')
    for index in decision.legacy_event_indices:
        _log(logger, 'legacy_metadata_ignored', record_index=index, **details)
    _log(logger, 'enrichment_allowed' if decision.allow else 'enrichment_skipped',
         reason=decision.reason, matched_event_index=decision.matched_event_index,
         latest_compaction_index=decision.latest_compaction_index, **details)
    if decision.allow:
        _log(logger, 'session_state_saved', event_type='context_enriched', **details)
    return decision


def run(prompt, *, project_root, config, registry, logger, max_context_chars=_CAP,
        finalize_enrichment=None):
    """Return the existing hook outcome; never retry or reinterpret a block answer."""
    stage = 'configuration'
    def finish(result):
        _log(logger, 'flow_finished', outcome=type(result).__name__,
             reason=result.reason if isinstance(result, Noop) else None,
             code=result.code if isinstance(result, Failure) else None, stage=stage)
        return result
    try:
        root = _root(project_root)
        if type(max_context_chars) is not int or max_context_chars <= 0:
            return finish(Failure('invalid_output_budget', stage))
        limit = min(_CAP, max_context_chars)
        if not isinstance(prompt, str):
            return finish(Failure('invalid_prompt', 'input'))
        if not prompt.strip():
            return finish(Noop('ineligible_request'))
        stage = 'resolution'
        references = detect_configured_references(prompt, {
            'include_patterns': [r'\.ts$'], 'exclude_patterns': []})
        unique = {}
        for reference in references:
            unique.setdefault(reference.path, reference)
        if not unique:
            return finish(Noop('no_file'))
        paths, unresolved, excluded = set(), [], False
        for reference in unique.values():
            resolved = resolve_file(reference, cwd=root, repository_root=root,
                                    resolve_from_project_root=True)
            excluded = excluded or not _eligible(reference.path)
            if (resolved.status not in {'resolved', 'missing', 'ambiguous', 'inaccessible',
                                        'outside_repository', 'not_file', 'invalid_resolution_mode'}
                    or resolved.status == 'resolved' and resolved.selected_path is None):
                return finish(Failure('invalid_resolution_result', stage))
            if resolved.status != 'resolved':
                unresolved.append(resolved.status)
                continue
            path = resolved.selected_path.resolve(strict=True)
            if not path.is_relative_to(root):
                unresolved.append('outside_repository')
                continue
            paths.add(path)
            excluded = excluded or not _eligible(path)
        if len(paths) + len(unresolved) > 1:
            return finish(Noop('multiple_files'))
        if unresolved:
            reason = unresolved[0]
            return finish(Noop(reason) if reason in {'missing', 'ambiguous'} else Failure(reason, stage))
        if excluded:
            return finish(Noop('excluded'))
        if len(paths) != 1:
            return finish(Noop('no_file'))
        path = paths.pop()
        relative = path.relative_to(root).as_posix()
        stage = 'requested_action'
        from building_blocks.questions.requested_action import block as action_block
        action = _block_data(action_block.run({'prompt': prompt}, config=config, registry=registry))
        _log(logger, 'block_finished', stage=stage, token_metadata='unavailable')
        if isinstance(action, Noop):
            return finish(action)
        if set(action) != {'action'} or action['action'] not in {'explain', 'generate_tests', 'modify_code', 'undecided'}:
            return finish(Failure('invalid_block_result', stage))
        if action['action'] not in {'explain', 'generate_tests'}:
            return finish(Noop(action['action']))
        stage = 'outline'
        source = typescript.outline(path)
        if not source['callables']:
            return finish(Noop('no_candidates'))
        stage = 'method_selection'
        from building_blocks.questions.method_selection_v2 import block as selection_block
        selection = _block_data(selection_block.run({'text': prompt, 'source_outline': source},
                                                    config=config, registry=registry))
        _log(logger, 'block_finished', stage=stage, token_metadata='unavailable')
        if isinstance(selection, Noop):
            return finish(selection)
        selected_id = selection.get('selected_id')
        reason = selection.get('reason')
        if selected_id is None:
            if not isinstance(reason, str) or not reason:
                return finish(Failure('invalid_block_result', stage))
            return finish(Noop(reason))
        candidates = [c for c in source['callables'] if c['id'] == selected_id]
        if len(candidates) != 1 or reason is not None:
            return finish(Failure('invalid_block_result', stage))
        candidate = candidates[0]
        stage = 'extraction'
        extracted = typescript.extract_callable(path, selected_id)
        if (extracted['path'] != str(path) or extracted['source_hash'] != source['source_hash']
                or extracted['callable'] != candidate):
            return finish(Failure('source_changed', stage))
        stage = 'formatting'
        header = _method_header(relative, candidate, extracted['code'])
        # This is a strict lower bound, without any call-list row/status text.
        if _pack(header, [], candidate['name'], limit) is None:
            return finish(Noop('output_budget_exceeded'))
        stage = 'navigation'
        rows, seen, status = [], set(), 'unavailable'
        try:
            navigation = typescript.get_called_method_locations(relative, selected_id, project_root=root)
            if navigation['status'] != 'error':
                status = navigation['status']
                for item in navigation['data']['methods']:
                    target = (root / item['path']).resolve(strict=True)
                    if not target.is_relative_to(root):
                        status = 'partial'
                        continue
                    target_path = target.relative_to(root).as_posix()
                    identity = (target_path, item['callable_id'])
                    if identity in seen:
                        continue
                    seen.add(identity)
                    rows.append(f'{_metadata(target_path)} | {_metadata(item["signature"])} | Lines: {item["start_line"]}–{item["end_line"]}')
        except TimeoutError:
            raise
        except Exception:
            rows, status = [], 'unavailable'
        stage = 'source_check'
        if typescript.outline(path)['source_hash'] != source['source_hash']:
            return finish(Failure('source_changed', stage))
        stage = 'formatting'
        packed = _pack(header, rows, candidate['name'], limit)
        if packed is None:
            return finish(Noop('output_budget_exceeded'))
        text, included = packed
        reason = ('included' if included else 'context_budget' if rows else
                  'navigation_unavailable' if status == 'unavailable' else 'no_resolved_targets')
        _log(logger, 'call_navigation_prepared', path=relative, method=candidate['name'],
             navigation_status=status, resolved_methods=len(rows), included_methods=included,
             reason=reason)
        context = Context(text)
        if finalize_enrichment is not None:
            try:
                first, last = _method_lines(candidate, extracted['code'])
                selected = SelectedMethod(str(path), candidate['name'], candidate['signature'], first, last)
            except (ValueError, KeyError, TypeError) as exc:
                _log(logger, 'session_state_enrichment_failed', error_type=type(exc).__name__,
                     message='invalid_selected_method')
            else:
                if not finalize_enrichment(selected).allow:
                    return finish(Noop('already_enriched_since_compaction'))
        _log(logger, 'enrichment_prepared', additional_context=text, path=relative,
             method=candidate['name'], context_utf16_units=_units(text),
             included_methods=included, omitted_methods=len(rows)-included)
        return finish(context)
    except TimeoutError:
        raise
    except Exception as exc:
        code = ('invalid_project_root' if stage == 'configuration' else
                'block_error' if stage in {'requested_action', 'method_selection'} else
                'formatting_error' if stage == 'formatting' else 'source_error')
        return finish(Failure(code, stage, message=type(exc).__name__))

