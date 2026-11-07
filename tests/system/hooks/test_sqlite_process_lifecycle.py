"""Fresh interpreters exercise SQLite via the public CLI/gateway protocol."""
import json
import os
from pathlib import Path
import select
import sqlite3
import subprocess
import sys
from time import monotonic
import pytest
from tests.support.offline_scenarios import PROMPT,SOURCE
from tests.support.sqlite_state import rows,save,COMPACT

ROOT=Path(__file__).resolve().parents[3]


@pytest.fixture
def launcher(tmp_path):
    root=tmp_path.resolve();project=root/'project';project.mkdir();(project/'sample.ts').write_text(SOURCE)
    directory=root/'state';path=directory/'session-state.sqlite3'
    def launch(*,hook='UserPromptSubmit',session='one',entry='cli',env_extra=None,asynchronous=False,pass_fds=(),
               scenario='success',payload_change=None):
        event={'hook_event_name':hook,'session_id':session,'cwd':str(project),
               'transcript_path':'TRANSCRIPT_SENTINEL','prompt':PROMPT,'trigger':'manual','compact_summary':'SUMMARY_SENTINEL'}
        event.update(payload_change or {})
        command=[sys.executable,'-m','tests.support.cli_runner',entry,'empty' if hook=='PostCompact' else scenario,
            str(project),str(directory),'--hook',hook,'--repository-root',str(project),'--deadline','30',
            '--log-state' if hook=='PostCompact' else '--real']
        env={**os.environ,'PYTHONPATH':os.pathsep.join([str(ROOT/'src'),str(ROOT)]),**(env_extra or {})}
        if asynchronous:
            process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                text=True,cwd=root,env=env,pass_fds=pass_fds)
            process.stdin.write(json.dumps(event));process.stdin.close();process.stdin=None
            return process
        return subprocess.run(command,input=json.dumps(event),text=True,capture_output=True,cwd=root,env=env,timeout=40)
    return launch,path,project


@pytest.mark.parametrize('entry',['cli','gateway'])
def test_fresh_process_lifecycle_isolation_and_old_history(launcher,entry):
    launch,path,project=launcher
    # Age has no effect, and old JSONL remains untouched.
    old=path.parent/'logs/session-state/one.jsonl';old.parent.mkdir(parents=True);old.write_text('OLD_SENTINEL')
    save(path,session='old')
    with sqlite3.connect(path) as db:db.execute("UPDATE session_events SET recorded_at='1900-01-01T00:00:00.000Z'")
    a=launch(entry=entry);b=launch(entry=entry);c=launch(entry=entry,hook='PostCompact');d=launch(entry=entry)
    assert all(p.returncode==0 for p in (a,b,c,d))
    assert json.loads(a.stdout)['hookSpecificOutput']['additionalContext']==json.loads(d.stdout)['hookSpecificOutput']['additionalContext']
    assert a.stdout.count('\n')==1 and b.stdout==c.stdout==''
    first=[r for r in rows(path) if r[1]=='one']
    assert [r[4] for r in first]==['context_enriched','PostCompact','context_enriched']
    assert first[1][3] is None and first[1][6]=='{}'
    other=launch(session='two',entry=entry)
    assert other.stdout==a.stdout and [r for r in rows(path) if r[1]=='one']==first
    assert len([r for r in rows(path) if r[1]=='old'])==1 and old.read_text()=='OLD_SENTINEL'
    stored=''.join(r[6] for r in rows(path))
    assert 'TRANSCRIPT_SENTINEL' not in stored and 'SUMMARY_SENTINEL' not in stored
    assert SOURCE.strip() not in stored and PROMPT not in stored


def test_unwritable_state_then_restore_without_suspension(launcher):
    launch,path,_=launcher
    path.parent.mkdir();path.parent.chmod(0o500)
    try:
        first=launch();compact=launch(hook='PostCompact')
        assert first.returncode==0 and json.loads(first.stdout)['hookSpecificOutput']['additionalContext']
        assert compact.returncode==0 and compact.stdout=='' and 'session_state_failed' in compact.stderr
        assert not path.exists()
    finally:path.parent.chmod(0o700)
    after=launch();duplicate=launch()
    assert after.returncode==0 and after.stdout and duplicate.stdout=='' and len(rows(path))==1


def test_post_compact_process_never_calls_model_or_source(launcher):
    launch,path,_=launcher
    result=launch(hook='PostCompact')
    assert result.returncode==0 and result.stdout=='' and len(rows(path))==1
    evidence=json.loads(next(line.split(':',1)[1] for line in result.stderr.splitlines() if line.startswith('TEST_EVIDENCE:')))
    assert evidence['calls']==0


def test_killed_after_commit_before_gateway_emission(launcher):
    launch,path,_=launcher
    checkpoint_read,checkpoint_write=os.pipe();release_read,release_write=os.pipe()
    process=None
    try:
        process=launch(asynchronous=True,pass_fds=(checkpoint_write,release_read),env_extra={
            'SOH_TEST_CHECKPOINT_FD':str(checkpoint_write),'SOH_TEST_RELEASE_FD':str(release_read)})
        os.close(checkpoint_write);checkpoint_write=None;os.close(release_read);release_read=None
        assert select.select([checkpoint_read],[],[],30)[0]
        assert os.read(checkpoint_read,1)==b'C'
        process.terminate();stdout,stderr=process.communicate(timeout=5)
        assert stdout=='' and len(rows(path))==1
        duplicate=launch();assert duplicate.returncode==0 and duplicate.stdout=='' and len(rows(path))==1
    finally:
        if process is not None:
            if process.poll() is None:process.kill()
            process.communicate(timeout=5)
        for descriptor in (checkpoint_read,checkpoint_write,release_read,release_write):
            if descriptor is not None:os.close(descriptor)


def test_persisted_payload_whitelist_excludes_every_sensitive_input(launcher):
    from tests.support.offline_scenarios import MODEL_RESPONSE_SENTINEL
    from tests.support.sqlite_state import history
    from tests.support.state_assertions import assert_no_payload_leaks
    launch,path,project=launcher
    prompt_marker='SOH_PROMPT_PRIVATE_MARKER';source_marker='SOH_SOURCE_PRIVATE_MARKER'
    transcript_marker='SOH_TRANSCRIPT_PRIVATE_MARKER';summary_marker='SOH_SUMMARY_PRIVATE_MARKER'
    source=f'export function calculateTotal(value: number) {{ /* {source_marker} */ return value + 1; }}\n'
    (project/'sample.ts').write_text(source)
    result=launch(scenario='sensitive',payload_change={'prompt':PROMPT+' '+prompt_marker,
        'transcript_path':transcript_marker})
    if result.returncode!=0 or not result.stdout:
        pytest.fail('synthetic sensitive-input hook did not return context',pytrace=False)
    # Confirm the scripted response marker was actually supplied by the model
    # boundary; existing diagnostic logs intentionally contain model responses.
    if MODEL_RESPONSE_SENTINEL not in result.stderr:
        pytest.fail('scripted model response marker was not exercised',pytrace=False)
    compact=launch(hook='PostCompact',payload_change={'compact_summary':summary_marker,
        'transcript_path':transcript_marker})
    assert compact.returncode==0 and compact.stdout==''
    events=history(path,'one')
    assert_no_payload_leaks({'persisted_events':json.dumps(events,ensure_ascii=False)},
        [prompt_marker,source_marker,transcript_marker,MODEL_RESPONSE_SENTINEL,summary_marker])
    if len(events)!=2:
        pytest.fail('unexpected persisted event count',pytrace=False)
    expected_keys={'event_id','session_id','hook_name','source','type','timestamp','payload'}
    assert all(set(item)==expected_keys for item in events)
    assert events[0]['source']=={'kind':'flow','name':'first_method_context'}
    assert events[0]['hook_name']=='UserPromptSubmit' and events[0]['type']=='context_enriched'
    expected={'files':[{'filePath':str((project/'sample.ts').resolve()),'methodNames':['calculateTotal'],
        'methods':[{'name':'calculateTotal','signature':'export function calculateTotal(value: number)',
                    'startLine':1,'endLine':1}]}]}
    if events[0]['payload']!=expected:
        pytest.fail('persisted enrichment payload differs from exact metadata whitelist',pytrace=False)
    assert events[1]['hook_name']=='PostCompact' and events[1]['type']=='PostCompact'
    assert events[1]['source']=={'kind':'hook','name':'PostCompact'} and events[1]['payload']=={}
