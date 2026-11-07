"""Small CLI contract checks: delegation, diagnostics, logs and installation."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest

from cli import main
import log_utils
ROOT = Path(__file__).resolve().parents[1]


def test_hook_forwards_arguments_streams_and_return_code(monkeypatch, capsys):
    from hooks import gateway
    args = ['--hook', 'UserPromptSubmit', '--repository-root', '/project with spaces',
            '--real', '--config', '/config.json', '--deadline', '5', '--future-flag']
    stdin = io.StringIO('original stdin')
    monkeypatch.setattr(sys, 'stdin', stdin)
    def delegate(received):
        assert received == args
        assert sys.stdin is stdin
        assert stdin.read() == 'original stdin'
        return 17
    monkeypatch.setattr(gateway, 'main', delegate)
    assert main(['hook', *args]) == 17
    output = capsys.readouterr()
    assert output.out == output.err == ''


@pytest.mark.parametrize('kind', ['context', 'noop', 'failure', 'malformed', 'observe'])
def test_subprocess_protocol_matches_existing_gateway(tmp_path, kind):
    script = '''
import sys
from pathlib import Path
import log_utils
log_utils.LOG_PATH = Path(sys.argv[2])
from hooks import gateway
from hooks.contracts import Context, Noop, Failure
from cli import main
kind = sys.argv[3]
result = {'context': Context('known context'), 'noop': Noop('no_match'),
          'failure': Failure('planned', 'test')}.get(kind, Noop('unused'))
gateway._default_dispatch = lambda *args: result
args = ['--hook', 'UserPromptSubmit']
if kind == 'observe': args += ['--observe-only']
raise SystemExit(main(['hook', *args]) if sys.argv[1] == 'cli' else gateway.main(args))
'''
    raw = '{broken' if kind == 'malformed' else '{"hook_event_name":"UserPromptSubmit","prompt":"hello"}'
    results = [subprocess.run([sys.executable, '-c', script, entry, str(tmp_path / (entry+'.jsonl')), kind],
               input=raw, text=True, capture_output=True, timeout=5, cwd=tmp_path,
               env={**os.environ, 'PYTHONPATH': str(ROOT / 'src')}) for entry in ['cli', 'gateway']]
    direct, original = results
    assert direct.returncode == original.returncode == 0
    assert direct.stdout == original.stdout
    if kind == 'context':
        assert json.loads(direct.stdout)['hookSpecificOutput']['additionalContext'] == 'known context'
    else:
        assert direct.stdout == ''
    if kind == 'malformed':
        assert 'invalid_json' in direct.stderr and 'invalid_json' in original.stderr
    else:
        assert direct.stderr == original.stderr
    if kind == 'observe':
        records = [json.loads(line) for line in (tmp_path / 'cli.jsonl').read_text().splitlines()]
        assert records[-1]['details']['outcome'] == 'observed'


def test_logs_tail_preserves_records_and_does_not_write(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'debug.jsonl'
    content = ''.join(json.dumps({'n': n, 'text': 'שלום'}, ensure_ascii=False)+'\n' for n in range(8))
    path.write_text(content)
    monkeypatch.setattr(log_utils, 'LOG_PATH', path)
    assert main(['logs']) == 0
    assert capsys.readouterr().out == ''.join(content.splitlines(keepends=True)[-5:])
    assert main(['logs', '--last', '2']) == 0
    assert capsys.readouterr().out == ''.join(content.splitlines(keepends=True)[-2:])
    assert path.read_text() == content


def test_missing_and_unreadable_logs(tmp_path, monkeypatch, capsys):
    missing = tmp_path / 'missing.jsonl'
    monkeypatch.setattr(log_utils, 'LOG_PATH', missing)
    assert main(['logs']) == 0
    output = capsys.readouterr()
    assert output.out == '' and str(missing) in output.err
    assert not missing.exists()
    monkeypatch.setattr(log_utils, 'LOG_PATH', tmp_path)
    assert main(['logs']) == 1
    assert 'Cannot read log file' in capsys.readouterr().err


@pytest.mark.parametrize('args', [[], ['unknown'], ['logs', '--last', '0'], ['logs', '--last', '-1'], ['logs', '--last', 'abc']])
def test_cli_usage_errors(args):
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_doctor_read_only_and_reports_failed_log_destination(tmp_path, monkeypatch, capsys):
    path = tmp_path / 'new' / 'logs.jsonl'
    monkeypatch.setattr(log_utils, 'LOG_PATH', path)
    assert main(['doctor']) == 0
    output = capsys.readouterr().out
    assert sys.executable in output and str(path) in output
    assert 'not checked' in output
    assert not path.parent.exists()
    blocker = tmp_path / 'blocker'
    blocker.write_text('file')
    monkeypatch.setattr(log_utils, 'LOG_PATH', blocker / 'log.jsonl')
    assert main(['doctor']) == 1
    assert 'FAIL' in capsys.readouterr().out


def test_installed_console_command_uses_selected_environment(tmp_path):
    wheel_dir = tmp_path / 'wheels'
    subprocess.run([sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                    '--wheel-dir', str(wheel_dir), str(ROOT)], check=True, capture_output=True)
    wheel, = wheel_dir.glob('*.whl')
    environment = tmp_path / 'custom python environment'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / 'bin/python'
    subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)],
                   check=True, capture_output=True)
    launcher = environment / 'bin/system-one-harness'
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    env['SOH_LOG_ROOT'] = str(tmp_path / '.logs')
    def run(*args, raw=None):
        return subprocess.run([str(launcher), *args], input=raw, text=True,
                              capture_output=True, timeout=10, cwd=tmp_path, env=env)
    result = run('doctor')
    assert result.returncode == 0, result.stderr
    assert str(python) in result.stdout and str(launcher) in result.stdout
    assert str(ROOT / 'src') not in result.stdout
    alias = environment / 'bin/laya-context-router'
    assert alias.is_file()
    alias_help = subprocess.run([str(alias), '--help'], cwd=tmp_path, env=env,
                                capture_output=True, text=True, check=True)
    module_help = subprocess.run([str(python), '-m', 'cli', '--help'], cwd=tmp_path,
                                 env=env, capture_output=True, text=True, check=True)
    assert 'system-one-harness' in alias_help.stdout == module_help.stdout
    installed_probe = subprocess.run([str(python), '-c', '''
from pathlib import Path
import answers, block_runtime, building_blocks, cli, defaults, flows, hooks, laya, questions
import blocks, context_format, json_utils, live_check, log_utils
from block_runtime import Catalog
from hooks import gateway
from building_blocks.questions.private_method_testing_blocker.block import BLOCK_DIR
for module in (answers, block_runtime, building_blocks, cli, defaults, flows,
               hooks, laya, questions, blocks, context_format, json_utils,
               live_check, log_utils, gateway):
    assert Path(module.__file__).is_relative_to(Path(cli.__file__).parent.parent)
assert set(Catalog().question('private_method_testing_blocker')['criteria']) == {'yes', 'no'}
assert (BLOCK_DIR / 'block.json').is_file()
print(hooks.__file__)
'''], cwd=tmp_path, env=env, capture_output=True, text=True, check=True)
    assert 'site-packages/hooks/__init__.py' in installed_probe.stdout
    result = run('hook', '--hook', 'UserPromptSubmit', '--observe-only',
                 raw='{"hook_event_name":"UserPromptSubmit","prompt":"installed test"}')
    assert result.returncode == 0 and result.stdout == result.stderr == ''
    result = run('logs', '--last', '1')
    assert result.returncode == 0
    assert json.loads(result.stdout)['details']['outcome'] == 'observed'
