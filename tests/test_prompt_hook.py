"""Shared gateway guarantees against the production flow, with fixed block answers."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import pytest

from building_blocks.typescript import outline
from tests.integration.flows.first_flow.test_hook_integration import BOOTSTRAP

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / 'hooks/user_prompt_submit.py'


def hook_case(tmp_path, text='function greet() { return "😀"; }'):
    repo = tmp_path / 'repo'; repo.mkdir()
    cwd = repo / 'nested'; cwd.mkdir()
    path = cwd / 'a.ts'; path.write_bytes(text.encode())
    (repo / 'tsconfig.json').write_text('{"include":["**/*.ts"]}')
    data = outline(path)
    event = {'session_id': 'synthetic-session', 'transcript_path': 'unused.jsonl',
             'permission_mode': 'default', 'hook_event_name': 'UserPromptSubmit',
             'cwd': str(cwd), 'prompt': 'Explain `a.ts`: how does greet work?'}
    config = repo / 'config.json'
    config.write_text('{"model":"offline-fixed-blocks","temperature":0.2,"lang":"fr"}')
    shim = tmp_path / 'shim'; shim.mkdir()
    calls = tmp_path / 'calls.jsonl'; trace = tmp_path / 'trace.jsonl'
    spec = tmp_path / 'spec.json'
    spec.write_text(json.dumps({'selected_id': data['callables'][0]['id']}))
    bootstrap = BOOTSTRAP.split('\nfrom hooks.gateway import main')[0]
    bootstrap = bootstrap.replace("json.loads(os.environ['FIRST_FLOW_SPEC'])",
                                  "json.load(open(os.environ['HOOK_TEST_SPEC']))")
    (shim / 'sitecustomize.py').write_text(bootstrap)
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(shim), str(ROOT / 'src')]),
           'HOOK_TEST_SPEC': str(spec), 'FIRST_FLOW_CALLS': str(calls), 'FIRST_FLOW_LOG': str(trace),
           'FIRST_FLOW_STATE_ROOT': str(tmp_path / 'state')}
    args = ['--repository-root', str(repo), '--config', str(config), '--real']
    return {'repo': repo, 'path': path, 'event': event, 'data': data, 'env': env,
            'args': args, 'log': calls, 'spec': spec, 'code': text}


def invoke(case, payload=None, args=None):
    return subprocess.run([sys.executable, str(HOOK), *(case['args'] if args is None else args)],
        input=json.dumps(case['event']) if payload is None else payload,
        text=True, capture_output=True, env=case['env'], cwd=ROOT, timeout=15)


def assert_noop(result, diagnostic=True):
    assert result.returncode == 0 and result.stdout == ''
    if diagnostic:
        assert result.stderr


def expected_context(case):
    selected = case['data']['callables'][0]
    code = case['code']
    first = selected['location']['line']
    last = first + len(re.findall(r'\r\n|[\r\n\u2028\u2029]', code))
    path = case['path'].relative_to(Path(case['args'][1])).as_posix()
    return ('Enriched Prompt for System 1\n'
            f'File: {path} | Method: {selected["name"]} | Lines: {first}–{last}\n'
            + code + '\n\nContext scope: selected method only.')


def hook_trace(case):
    return Path(case['env']['FIRST_FLOW_LOG'])


@pytest.mark.parametrize('payload', ['', '{', '[]', 'null', '{}'])
def test_invalid_input_json_no_model(tmp_path,payload):
    case=hook_case(tmp_path); assert_noop(invoke(case,payload)); assert not case['log'].exists()


@pytest.mark.parametrize('key,value', [('cwd',None),('cwd','relative'),('cwd',13),('prompt',None),('prompt',[]),('hook_event_name',None)])
def test_invalid_required_fields(tmp_path,key,value):
    case=hook_case(tmp_path); case['event'][key]=value
    assert_noop(invoke(case)); assert not case['log'].exists()


@pytest.mark.parametrize('args', [[],['--unknown'],['--repository-root','relative'],['--repository-root','/does-not-exist'],['--deadline','0'],['--max-context-chars','0']])
def test_invalid_cli_never_exits_two(tmp_path,args):
    case=hook_case(tmp_path); assert_noop(invoke(case,args=args)); assert not case['log'].exists()


def test_bounded_deadline_while_waiting_for_stdin(tmp_path):
    case=hook_case(tmp_path)
    process=subprocess.Popen([sys.executable,str(HOOK),*case['args'],'--deadline','1'],
                             stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,text=True,env=case['env'],cwd=ROOT)
    try:
        process.stdin.write('{')
        process.stdin.flush()
        process.wait(timeout=4)
        process.stdin.close()
        stdout,stderr=process.stdout.read(),process.stderr.read()
        assert process.returncode==0 and stdout==''
        assert 'deadline exceeded' in stderr and not case['log'].exists()
    finally:
        if process.poll() is None:
            process.kill(); process.communicate()


@pytest.mark.parametrize('status', ['missing','ambiguous'])
def test_missing_or_ambiguous_source_is_protocol_noop(tmp_path,status):
    case=hook_case(tmp_path)
    case['event']['cwd']=str(case['repo'])
    if status=='missing':
        case['path'].unlink()
    else:
        other=case['repo']/'other'; other.mkdir()
        (other/'a.ts').write_text('function greet() {}')
    result=invoke(case)
    assert_noop(result); assert status in result.stderr
    assert not case['log'].exists()


@pytest.mark.parametrize('extra', [0,1])
def test_input_character_ceiling(tmp_path,extra):
    case=hook_case(tmp_path); case['event']['prompt']='hello'
    payload=json.dumps(case['event']); payload+=' '*(65536+extra-len(payload))
    result=invoke(case,payload); assert_noop(result); assert not case['log'].exists()
    assert ('input_budget_exceeded' in result.stderr)==bool(extra)


@pytest.mark.parametrize('key', ['cwd','prompt','hook_event_name'])
def test_missing_required_field(tmp_path,key):
    case=hook_case(tmp_path); del case['event'][key]
    assert_noop(invoke(case)); assert not case['log'].exists()


def test_duplicate_json_fails_closed(tmp_path):
    case=hook_case(tmp_path)
    payload=json.dumps(case['event'])[:-1]+',"prompt":"overwritten"}'
    assert_noop(invoke(case,payload)); assert not case['log'].exists()


def test_without_sdk_mock_no_real_flag_never_imports_sdk(tmp_path):
    case=hook_case(tmp_path); case['env']['PYTHONPATH']=str(ROOT/'src')
    case['args'].remove('--real')
    result=invoke(case); assert_noop(result); assert 'explicit --real required' in result.stderr
    assert not case['log'].exists()


@pytest.mark.parametrize('deadline', ['21','300'])
def test_explicit_longer_deadline_accepts_fast_mock_request(tmp_path,deadline):
    case=hook_case(tmp_path)
    result=invoke(case,args=case['args']+['--deadline',deadline])
    assert result.returncode==0 and json.loads(result.stdout)['hookSpecificOutput']['additionalContext']==expected_context(case)


@pytest.mark.parametrize('deadline', ['0','-1','nan','inf','-inf','300.001'])
def test_deadline_invalid_values_before_sdk(tmp_path,deadline):
    case=hook_case(tmp_path)
    assert_noop(invoke(case,args=case['args']+['--deadline',deadline])); assert not case['log'].exists()


@pytest.mark.parametrize('budget', ['0','-1','nan'])
def test_context_invalid_budget_before_sdk(tmp_path,budget):
    case=hook_case(tmp_path)
    assert_noop(invoke(case,args=case['args']+['--max-context-chars',budget])); assert not case['log'].exists()


@pytest.mark.parametrize('budget', ['8001','16000'])
def test_positive_context_thresholds_are_valid(tmp_path,budget):
    case=hook_case(tmp_path)
    result=invoke(case,args=case['args']+['--max-context-chars',budget])
    assert json.loads(result.stdout)['hookSpecificOutput']['additionalContext']==expected_context(case)


def test_failed_stdout_write_never_logs_successful_emission(tmp_path):
    case=hook_case(tmp_path); trace=hook_trace(case)
    process=subprocess.Popen([sys.executable,str(HOOK),*case['args']],stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=case['env'],cwd=ROOT)
    process.stdout.close()
    process.stdin.write(json.dumps(case['event'])); process.stdin.close()
    process.wait(timeout=15)
    stderr=process.stderr.read()
    assert 'BrokenPipeError' in stderr
    records=[json.loads(line) for line in trace.read_text().splitlines()]
    assert any(record['event']=='response_prepared' for record in records)
    assert not any(record['event']=='response_emitted' for record in records)


def test_noop_logs_reason_without_prepared_or_emitted_payload(tmp_path):
    case=hook_case(tmp_path); trace=hook_trace(case)
    case['event']['prompt']='Explain the algorithm generally'
    result=invoke(case)
    assert_noop(result)
    records=[json.loads(line) for line in trace.read_text().splitlines()]
    hook=[record for record in records if record['component']=='hook_gateway']
    noop=[record for record in hook if record['event']=='gateway_finished']
    assert len(noop)==1 and noop[0]['details']['reason']=='no_file'
    assert noop[0]['details']['response_present'] is False and noop[0]['details']['stdout_bytes_written']==0
    assert not any(record['event'] in {'response_prepared','response_emitted'} for record in hook)


@pytest.mark.parametrize('args', [
    ['--max-dependency-depth', '-1'], ['--max-dependency-depth', '21'],
    ['--max-dependency-depth', '1.5'], ['--max-dependency-chars', '0'],
    ['--max-dependency-chars', '1000001'],
])
def test_invalid_source_limit_arguments_fail_before_blocks(tmp_path, args):
    case = hook_case(tmp_path)
    result = invoke(case, args=case['args'] + args)
    assert_noop(result)
    assert 'invalid_budget' in result.stderr or 'invalid_arguments' in result.stderr
    assert not case['log'].exists()
