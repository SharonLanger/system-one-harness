"""Production vocabulary mapping and repository/local-output migration safeguards."""
import ast
import io
import json
from pathlib import Path
import subprocess
import sys

import log_utils
from soh_logging import ComponentName, ComponentType, LogEvent, LogLevel

ROOT = Path(__file__).resolve().parents[2]


def _literal_choices(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.IfExp):
        return _literal_choices(node.body) | _literal_choices(node.orelse)
    raise AssertionError(f'Unreviewed dynamic logging vocabulary: {ast.unparse(node)}')


def test_production_logging_calls_use_catalog_and_reviewed_forwarding():
    # The only dynamic event arguments belong to these three adapter functions;
    # their callers supply the closed vocabulary checked below.
    forwarding = {('hooks/gateway.py', '_default_log', 'log'),
                  ('hooks/gateway.py', 'safe_log', 'log'),
                  ('flows/first_method_context.py', '_log', 'logger')}
    seen_forwarding, events, components = set(), set(), set()

    class Audit(ast.NodeVisitor):
        def __init__(self, relative):
            self.relative, self.function = relative, None

        def visit_FunctionDef(self, node):
            previous, self.function = self.function, node.name
            self.generic_visit(node)
            self.function = previous

        def visit_Call(self, node):
            if not isinstance(node.func, ast.Name) or node.func.id not in {'log', 'logger', '_log', 'safe_log'}:
                return self.generic_visit(node)
            name = node.func.id
            direct = name == 'log' and (self.relative != 'hooks/gateway.py' or self.function == '_default_log')
            index = 2 if direct else 1
            identity = (self.relative, self.function, name)
            if identity in forwarding:
                assert isinstance(node.args[index], ast.Name) and node.args[index].id == 'event'
                seen_forwarding.add(identity)
            else:
                for event in _literal_choices(node.args[index]):
                    assert LogEvent(event).value == event, (self.relative, node.lineno, event)
                    message = log_utils._message('hook_gateway', event)
                    assert message.strip() and '\n' not in message and '_' not in message
                    events.add(event)
                if direct:
                    for component in _literal_choices(node.args[1]):
                        ComponentName(component)
                        ComponentType(log_utils._KINDS[component])
                        components.add(component)
                if name != '_log':
                    for level in _literal_choices(node.args[0]):
                        LogLevel('WARNING' if level == 'WARN' else level)
            self.generic_visit(node)

    for path in (ROOT / 'src').rglob('*.py'):
        Audit(path.relative_to(ROOT / 'src').as_posix()).visit(ast.parse(path.read_text()))
    assert seen_forwarding == forwarding
    assert {'gateway_error', 'enrichment_prepared', 'prediction_started', 'helper_returned'} <= events
    assert {'block_runtime', 'laya', 'extractor', 'answers'} <= components
    # This migration must keep one record writer. Emitting through the adapter
    # preserves the checked/best-effort policy used by the existing callers.
    emitters = {path.relative_to(ROOT / 'src').as_posix()
                for path in (ROOT / 'src').rglob('*.py')
                if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr == 'emit' for node in ast.walk(ast.parse(path.read_text())))}
    assert emitters == {'log_utils.py'}


def test_gateway_file_records_isolate_repeated_sessions_and_rejected_input(tmp_path, monkeypatch, capfd):
    from hooks.gateway import main

    root = tmp_path / '.logs'
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    monkeypatch.setattr(log_utils, 'LOG_ROOT', root)
    monkeypatch.setenv('CLAUDE_PROJECT_DIR', str(tmp_path))
    runs = []
    previous_count = 0
    for session in ['one', 'one', 'two', None]:
        payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': session,
                   'cwd': str(tmp_path), 'transcript_path': 'unused.jsonl', 'prompt': ''}
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(payload)))
        assert main(['--hook', 'UserPromptSubmit', '--real', '--repository-root', str(tmp_path)]) == 0
        assert capfd.readouterr().out == ''
        all_rows = [json.loads(line) for path in sorted(root.glob('*/*.jsonl'))
                    for line in path.read_text().splitlines()]
        runs.append(all_rows[previous_count:])
        previous_count = len(all_rows)
    correlations = [run[0]['correlation_id'] for run in runs]
    assert len(set(correlations)) == 4 and all(correlations)
    for run, expected in zip(runs, ['one', 'one', 'two', None]):
        assert run[0]['session_id'] is None
        assert run[0]['flow_name'] is None and run[-1]['flow_name'] is None
        assert run[-1]['session_id'] == expected
        assert {row['correlation_id'] for row in run} == {run[0]['correlation_id']}
        assert {row['session_id'] for row in run} <= {None, expected}
    assert {row['session_id'] for row in runs[-1]} == {None}


def test_doctor_reports_new_root_without_creating_it(tmp_path, monkeypatch, capsys):
    from cli.doctor import run

    root = tmp_path / '.logs'
    monkeypatch.setattr(log_utils, 'LOG_PATH', None)
    monkeypatch.setattr(log_utils, 'LOG_ROOT', root)
    assert run() == 0
    assert f'Log location: {root}' in capsys.readouterr().out
    assert not root.exists()


def test_git_ignore_index_and_outgoing_tree_guard_generated_output(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()

    def git(*args):
        return subprocess.run(['git', *args], cwd=repo, text=True, capture_output=True, check=True)

    git('init', '-q')
    (repo / '.gitignore').write_text((ROOT / '.gitignore').read_text())
    fixture = repo / 'tests' / 'fixtures' / 'authored.jsonl'
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"authored":true}\n')
    git('add', '.gitignore', str(fixture.relative_to(repo)))
    checker = ROOT / 'scripts' / 'check_repository_hygiene.py'

    def check(*args):
        return subprocess.run([sys.executable, str(checker), *args], cwd=repo,
                              text=True, capture_output=True, timeout=10)

    assert check().returncode == 0
    ignored = ['.logs/2024-01-01/00-00.jsonl', 'nested/.logs/.write.lock', 'nested/.logs/private.txt']
    assert git('check-ignore', *ignored).stdout.splitlines() == ignored
    prohibited = [*ignored, 'logs/debug.jsonl', 'results/generated.txt', 'tests/.output/raw.jsonl']
    for relative in prohibited:
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('SYNTHETIC_CONTENT_MUST_NOT_BE_REPORTED')
        git('add', '-f', relative)
    indexed = check()
    assert indexed.returncode == 1
    assert all(relative in indexed.stderr for relative in prohibited)
    assert 'SYNTHETIC_CONTENT_MUST_NOT_BE_REPORTED' not in indexed.stderr
    assert 'authored.jsonl' not in indexed.stderr
    git('-c', 'user.name=SOH test', '-c', 'user.email=soh@example.invalid', 'commit', '-qm', 'synthetic guards')
    outgoing = check('--tree', 'HEAD')
    assert outgoing.returncode == 1
    assert all(relative in outgoing.stderr for relative in prohibited)
    assert 'SYNTHETIC_CONTENT_MUST_NOT_BE_REPORTED' not in outgoing.stderr
