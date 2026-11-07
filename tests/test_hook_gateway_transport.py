"""Pure protocol expectations, independent of model/helper/harness."""
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from hooks.contracts import Context, Noop, Failure, ValidationError
from hooks.gateway import parse_args, parse_payload, serialize_response

@pytest.mark.parametrize('argv', [[], ['--hook',''], ['--hook',' '],
    ['--hook','X','--hook','Y'], ['--hook=X','--hook=X'], ['--hook','X','--bad'],
    ['--hook','X','--deadline','nan'], ['--hook','X','--deadline','0']])
def test_bad_arguments(argv):
    with pytest.raises(ValidationError): parse_args(argv)

@pytest.mark.parametrize('payload', ['', '{', '[]', 'null','1','true','{}',
    '{"hook_event_name":"X","hook_event_name":"X"}',
    '{"hook_event_name":"X"} {}', '{"hook_event_name":"X","n":NaN}', 'progress\n{}', '{"hook_event_name":"X","prompt":"first","prompt":"second"}'])
def test_bad_input(payload):
    with pytest.raises(ValidationError): parse_payload(payload, 'X')

def test_name_pair_unicode_and_limit():
    with pytest.raises(ValidationError, match='event_mismatch'):
        parse_payload('{"hook_event_name":"Stop"}', 'UserPromptSubmit')
    text=json.dumps({'hook_event_name':'X','prompt':'😀\\\n'})
    assert parse_payload(text+' '*(65536-len(text)), 'X')['prompt']=='😀\\\n'
    with pytest.raises(ValidationError,match='input_budget_exceeded'):
        parse_payload(text+' '*(65537-len(text)), 'X')

@pytest.mark.parametrize('result', [None, {}, Context(''), Context(None), Noop('')])
def test_bad_result(result):
    with pytest.raises(ValidationError):serialize_response(result,'X')

def test_literal_output():
    assert serialize_response(Context('😀 "x"\n'), 'UserPromptSubmit') == '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"😀 \\"x\\"\\n"}}\n'
    assert serialize_response(Noop('multiple_relevant'),'X')==''
    assert serialize_response(Failure('invalid_field','input','prompt'),'X')==''

ROOT=Path(__file__).resolve().parents[1]
@pytest.mark.parametrize('mode', ['context','noop','failure','invalid','throw','timeout'])
def test_process_noise_output_and_final_record(tmp_path,mode):
    script='''import json, os
from hooks.gateway import main
from hooks.contracts import Context, Noop, Failure
from hooks.dispatcher import dispatch_hook
records=[]
def route(envelope,args,logger):
    print('python noise')
    os.write(1,b'native noise\\n')
    if MODE=='throw': raise ValueError('planned')
    if MODE=='timeout': raise TimeoutError('planned deadline')
    return {'context':Context('Known 😀'), 'noop':Noop('multiple_relevant'),
            'failure':Failure('planned','feature'), 'invalid':None}.get(MODE)
def dispatch(payload,args,invocation_id,logger):
    return dispatch_hook(payload,args,invocation_id,logger,
                         dispatchers={'UserPromptSubmit':route})
def logger(l,e,**d):
    print('logger noise')
    os.write(1,b'logger native noise\\n')
    records.append({'event':e,**d})
main(['--hook','UserPromptSubmit'],dispatcher=dispatch,logger=logger)
open(LOG,'w').write(json.dumps(records))
'''.replace('MODE',repr(mode)).replace('LOG',repr(str(tmp_path/'log.json')))
    payload={'hook_event_name':'UserPromptSubmit','session_id':'validated',
             'cwd':str(tmp_path),'transcript_path':'unused.jsonl'}
    r=subprocess.run([sys.executable,'-c',script],input=json.dumps(payload),
        text=True,capture_output=True,timeout=5,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
    assert r.returncode==0
    assert 'python noise' in r.stderr and 'native noise' in r.stderr
    assert r.stdout == ('{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"Known 😀"}}\n' if mode=='context' else '')
    final=json.loads((tmp_path/'log.json').read_text())[-1]
    assert final['event']=='gateway_finished'
    assert final['session_id']=='validated'
    if mode=='timeout': assert final['code']=='deadline_exceeded'
    assert 'logger noise' in r.stderr and 'logger native noise' in r.stderr
    assert final['stdout_bytes_written']==len(r.stdout.encode())
    assert final['response_present']==(mode=='context')
    assert final['outcome']==({'context':'context','noop':'noop'}.get(mode,'error'))

def test_process_input_deadline():
    p=subprocess.Popen([sys.executable,'-m','hooks.gateway','--hook','X','--deadline','.1'],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
    try:
        p.stdin.write(b'{');p.stdin.flush()
        p.wait(timeout=5)
        assert p.returncode==0 and p.stdout.read()==b''
        assert b'deadline exceeded' in p.stderr.read()
    finally:
        p.stdin.close()
        if p.poll() is None:p.kill();p.wait()

def test_broken_pipe_never_records_success(monkeypatch):
    from contextlib import nullcontext
    import io
    import hooks.gateway as g
    class Broken(io.StringIO):
        def write(self, text):raise BrokenPipeError('planned')
    records=[]
    monkeypatch.setattr(g,'isolate_stdout',nullcontext)
    monkeypatch.setattr(sys,'stdin',io.StringIO('{"hook_event_name":"X"}'))
    monkeypatch.setattr(sys,'stdout',Broken())
    saved_fd=os.dup(1)
    try:
        assert g.main(['--hook','X'],dispatcher=lambda *a:Context('x'),
                      logger=lambda l,e,**d:records.append({'event':e,**d}))==0
    finally:
        os.dup2(saved_fd,1);os.close(saved_fd)
    assert records[-1]['outcome']=='error'
    assert records[-1]['stdout_bytes_written'] is None
    assert records[-1]['response_present'] is False

def test_import_does_not_load_sdk():
    r=subprocess.run([sys.executable,'-c',
        "import sys; import hooks.gateway; assert 'laya_mlx' not in sys.modules"],
        capture_output=True,env={**os.environ,'PYTHONPATH':str(ROOT/'src')},timeout=5)
    assert r.returncode==0 and r.stdout==b''


@pytest.mark.parametrize('result',[Failure(1,'input'),Failure('x',True),Failure('x','input',field=4),
    Failure('x','input',message=None), Context('text',feature_id=None),Noop('reason',feature_id=1)])
def test_malformed_outcome_fields(result):
    with pytest.raises(ValidationError):serialize_response(result,'X')

def test_mismatch_keeps_both_event_names():
    with pytest.raises(ValidationError) as exc:
        parse_payload('{"hook_event_name":"Stop"}','UserPromptSubmit')
    assert exc.value.evidence=={'command_hook':'UserPromptSubmit','payload_hook':'Stop'}

@pytest.mark.parametrize('mode',['context','noop','error'])
def test_throwing_logger_never_changes_protocol(mode):
    script="""from hooks.gateway import main
from hooks.contracts import Context,Noop,Failure
def logger(*a,**k):raise OSError('planned')
main(['--hook','X'],dispatcher=lambda *a:RESULT,logger=logger)
""".replace('RESULT',{'context':"Context('known')",'noop':"Noop('no_match')",'error':"Failure('planned','feature')"}[mode])
    r=subprocess.run([sys.executable,'-c',script],input='{"hook_event_name":"X"}',text=True,
        capture_output=True,timeout=5,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
    assert r.returncode==0 and 'logging failed' in r.stderr
    assert r.stdout==('{"hookSpecificOutput":{"hookEventName":"X","additionalContext":"known"}}\n' if mode=='context' else '')

def test_concurrent_invocations_have_distinct_correlated_ids(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    script='''import json
from hooks.gateway import main
records=[]
main(['--hook','UserPromptSubmit','--repository-root',ROOT],logger=lambda l,e,**d:records.append({'event':e,**d}))
open(LOG,'w').write(json.dumps(records))
'''
    def run(index):
        log=tmp_path/f'{index}.json'
        code=script.replace('ROOT',repr(str(tmp_path))).replace('LOG',repr(str(log)))
        p={'hook_event_name':'UserPromptSubmit','session_id':'same-session','transcript_path':'unused.jsonl','cwd':str(tmp_path),'prompt':'x'}
        r=subprocess.run([sys.executable,'-c',code],input=json.dumps(p),text=True,capture_output=True,
            timeout=5,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
        assert r.returncode==0 and r.stdout==''
        records=json.loads(log.read_text())
        assert len({x['invocation_id'] for x in records})==1
        assert records[-1]['session_id']=='same-session'
        return records[-1]['invocation_id']
    with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(run,[0,1]))
    assert ids[0]!=ids[1]

def test_closed_pipe_records_error_and_exits_zero(tmp_path):
    script='''import json
from hooks.gateway import main
from hooks.contracts import Context
records=[]
main(['--hook','X'],dispatcher=lambda *a:Context('known'),logger=lambda l,e,**d:records.append({'event':e,**d}))
open(LOG,'w').write(json.dumps(records))
'''.replace('LOG',repr(str(tmp_path/'records.json')))
    p=subprocess.Popen([sys.executable,'-c',script],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        text=True,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
    p.stdout.close();p.stdin.write('{"hook_event_name":"X"}');p.stdin.close()
    p.wait(timeout=5)
    assert p.returncode==0
    assert 'BrokenPipeError' in p.stderr.read()
    records=json.loads((tmp_path/'records.json').read_text())
    final=records[-1]
    assert final['outcome']=='error' and final['code']=='stdout_write_failed'
    assert final['stdout_bytes_written'] is None and final['response_present'] is False
    assert not any(r['event']=='response_emitted' for r in records)


def test_duplicate_prompt_rejected_without_dispatch(tmp_path):
    script="""from hooks.gateway import main
calls=[]
def dispatch(*a):calls.append(1);raise AssertionError('dispatch ran')
main(['--hook','X'],dispatcher=dispatch)
assert calls==[]
"""
    r=subprocess.run([sys.executable,'-c',script],
        input='{"hook_event_name":"X","prompt":"first","prompt":"second"}',text=True,capture_output=True,
        timeout=5,env={**os.environ,'PYTHONPATH':str(ROOT/'src')})
    assert r.returncode==0 and r.stdout=='' and 'invalid_json' in r.stderr
