"""Small shared fixtures for standalone examples; never imported by SOH runtime."""
from contextlib import contextmanager
from functools import partial
import io
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

SOURCE = 'export function calculateTotal(value: number) { return value + 1; }\n'
PROMPT = 'Explain `sample.ts`: calculateTotal'


def entrypoint(main):
    try:
        return main()
    except Exception as exc:
        print(f'FAIL: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1


@contextmanager
def workspace():
    """Keep synthetic logs in memory and project/SQLite state in a temporary folder."""
    from soh_logging import capture_records
    with TemporaryDirectory(prefix='soh-example-') as folder:
        root = Path(folder).resolve()
        project = root / 'project'
        project.mkdir()
        (project / 'sample.ts').write_text(SOURCE)
        records = []
        with capture_records(records.append):
            yield project, root / 'state/session-state.sqlite3', records


class ScriptedModel:
    """Two fixed answers for the single-function example, never a live model."""
    def __init__(self):
        self.calls = 0

    def load(self, model, **options):
        return self

    def predict(self, text, questions, **options):
        self.calls += 1
        if set(questions) == {'requested_action'}:
            name, selected = 'requested_action', 'explain'
        elif set(questions) == {'method', 'multiple_relevant'}:
            name = 'method'
            candidates = [key for key in questions[name]['criteria'] if key != 'none_or_unclear']
            if len(candidates) != 1:
                raise ValueError('The sanity fixture must have exactly one callable')
            selected = candidates[0]
        else:
            raise ValueError('Unexpected question contract')
        criteria = questions[name]['criteria']
        answers = {name: {'type': 'choice', 'choice': selected,
                   'probabilities': {key: 0.9 if key == selected else 0.1 / (len(criteria) - 1)
                                     for key in criteria}, 'answer_confidence': 0.95}}
        if name == 'method':
            answers['multiple_relevant'] = {'type': 'noul', 'noul': 0.1, 'answer_confidence': 0.95}
        return {'answers': answers}


def replay_hook(project, database, registry, *, model, deadline):
    """Run the production gateway with injected model ownership and isolated SQLite."""
    from flows.first_method_context import handle_prompt
    from hooks import gateway, session_state
    from hooks.dispatcher import dispatch_hook
    from hooks.project_context import enrich_project_context
    from hooks.user_prompt_submit.dispatcher import dispatch_user_prompt_submit

    store = SimpleNamespace(inspect_and_save_event=partial(
        session_state.inspect_and_save_event, database_path=database))

    def handler(request, logger):
        return handle_prompt(request, logger, registry=registry, session_store=store)

    def dispatcher(payload, args, invocation_id, logger):
        return dispatch_hook(payload, args, invocation_id, logger,
            dispatchers={'UserPromptSubmit': partial(dispatch_user_prompt_submit, handler=handler)},
            enricher=lambda envelope: enrich_project_context(envelope, str(project), logger))

    config = project / 'model.json'
    config.write_text(json.dumps({'model': model}))
    payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'synthetic-example',
               'cwd': str(project), 'transcript_path': str(project / 'unused.jsonl'), 'prompt': PROMPT}
    previous = sys.stdin
    try:
        sys.stdin = io.StringIO(json.dumps(payload))
        return gateway.main(['--hook', 'UserPromptSubmit', '--repository-root', str(project),
                             '--config', str(config), '--real', '--deadline', str(deadline)],
                            dispatcher=dispatcher)
    finally:
        sys.stdin = previous
