import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import pytest

from hooks.contracts import Failure, Noop
from hooks.dispatcher import dispatch_hook
from hooks.gateway import parse_args, parse_payload, POST_COMPACT_INPUT_CHARS
from hooks.session_state import ROOT, save_event

EVENT = {'source': {'kind': 'hook', 'name': 'PostCompact'}, 'hook_name': 'PostCompact', 'type': 'PostCompact', 'payload': {}}


def payload(tmp_path, trigger='manual'):
    return {'hook_event_name': 'PostCompact', 'session_id': 'abc123', 'cwd': str(tmp_path),
            'transcript_path': '/unused/transcript', 'trigger': trigger, 'compact_summary': 'SECRET שלום'}


@pytest.mark.parametrize('trigger', ['manual', 'auto'])
def test_dispatch_writes_exact_event(tmp_path, monkeypatch, trigger):
    calls=[]
    monkeypatch.setattr('hooks.post_compact.dispatcher.save_event',
                        lambda *a, **kw: calls.append((a,kw)))
    args=parse_args(['--hook','PostCompact','--log-state']);args.deadline_at=monotonic()+10
    assert isinstance(dispatch_hook(payload(tmp_path,trigger),args,'id',lambda *a,**kw:None),Noop)
    assert calls[0][0] == ('abc123',EVENT)


def test_routing_and_invalid_input(tmp_path, monkeypatch):
    monkeypatch.setattr('hooks.post_compact.dispatcher.save_event',
                        lambda *a,**kw: pytest.fail('must not write'))
    args=SimpleNamespace(hook='PostCompact',log_state=False)
    assert dispatch_hook(payload(tmp_path),args,'id',lambda *a,**kw:None).reason=='unsupported_action'
    args.log_state=True
    assert isinstance(dispatch_hook(payload(tmp_path,'bad'),args,'id',lambda *a,**kw:None),Failure)
    bad=payload(tmp_path);bad.pop('session_id')
    assert isinstance(dispatch_hook(bad,args,'id',lambda *a,**kw:None),Failure)
    for argv in [['--hook','UserPromptSubmit','--log-state'],['--hook','PostCompact','--log-state','--real']]:
        with pytest.raises(Exception):parse_args(argv)


def test_input_limits_and_summary(tmp_path):
    value=payload(tmp_path);value['compact_summary']='x'*70000
    assert parse_payload(json.dumps(value),'PostCompact')==value
    with pytest.raises(Exception):parse_payload('x'*(POST_COMPACT_INPUT_CHARS+1),'PostCompact')
    value['hook_event_name']='UserPromptSubmit'
    with pytest.raises(Exception):parse_payload(json.dumps(value),'UserPromptSubmit')


def test_adapter_real_sqlite_concurrent_and_sessions(tmp_path):
    from tests.support.sqlite_state import history, rows
    path=tmp_path.resolve()/'state.sqlite3'
    def write(i): save_event('abc123',EVENT,deadline=monotonic()+15,database_path=path)
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(write,range(8)))
    records=history(path,'abc123')
    assert len(records)==8
    assert all(x['type']=='PostCompact' and x['payload']=={} and x['timestamp'] for x in records)
    save_event('other',EVENT,deadline=monotonic()+10,database_path=path)
    assert len(history(path,'other'))==1 and len(rows(path))==9


def test_adapter_errors_and_lock_deadline(tmp_path):
    import sqlite3
    path=tmp_path.resolve()/'state.sqlite3'
    with pytest.raises(ValueError):save_event('../bad',EVENT,deadline=monotonic()+10,database_path=path)
    save_event('id',EVENT,deadline=monotonic()+10,database_path=path)
    with sqlite3.connect(path,isolation_level=None) as db:
        db.execute('BEGIN IMMEDIATE')
        script="""from pathlib import Path
from time import monotonic
from hooks.session_state import save_event
try:
 save_event('id',{'source':{'kind':'hook','name':'PostCompact'},'hook_name':'PostCompact','type':'PostCompact','payload':{}},deadline=monotonic()+0.05,database_path=Path(__import__('sys').argv[1]))
except TimeoutError:raise SystemExit(0)
raise SystemExit(2)
"""
        result=subprocess.run([sys.executable,'-c',script,str(path)],capture_output=True,
            env={**__import__('os').environ,'PYTHONPATH':str(ROOT/'src')},timeout=5)
        assert result.returncode==0
        db.rollback()
    save_event('id',EVENT,deadline=monotonic()+10,database_path=path)


def test_gateway_empty_stdout_no_summary_in_diagnostics(tmp_path):
    script='''import sys
from hooks.gateway import main
from hooks.contracts import Noop
raise SystemExit(main(['--hook','PostCompact','--log-state'], dispatcher=lambda *a: Noop('session_state_logged'), logger=lambda *a,**kw: print(repr(kw),file=sys.stderr)))'''
    result=subprocess.run([sys.executable,'-c',script],input=json.dumps(payload(tmp_path)),
                          text=True,capture_output=True,env={**__import__('os').environ,'PYTHONPATH':str(ROOT/'src')})
    assert result.returncode==0 and result.stdout==''
    assert 'SECRET' not in result.stderr and 'session_state_logged' in result.stderr


def test_plugin_registration():
    hooks=json.loads((ROOT/'plugins/hook-laya/hooks/hooks.json').read_text())['hooks']
    assert 'UserPromptSubmit' in hooks
    entry=hooks['PostCompact'][0]
    assert 'matcher' not in entry and len(entry['hooks'])==1
    assert '--hook PostCompact --log-state' in entry['hooks'][0]['command']


def test_gateway_real_dispatch_and_store(tmp_path):
    script='''from pathlib import Path
import sys
import hooks.session_state as state
from hooks.gateway import main
state.ROOT = Path(sys.argv[1])
# Redirect only the SQLite location.
original=state.save_event
from hooks.post_compact import dispatcher
dispatcher.save_event=lambda session,event,**kw: original(session,event,database_path=Path(sys.argv[1])/'state.sqlite3',**kw)
raise SystemExit(main(['--hook','PostCompact','--log-state'], logger=lambda *a,**kw: None))'''
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],
        input=json.dumps(payload(tmp_path)),text=True,capture_output=True,
        env={**__import__('os').environ,'PYTHONPATH':str(ROOT/'src')})
    assert result.returncode==0 and result.stdout==''
    from tests.support.sqlite_state import history
    record=history(tmp_path/'state.sqlite3','abc123')[0]
    assert record['type']=='PostCompact' and record['payload']=={}



@pytest.mark.parametrize('error',[RuntimeError('SENTINEL'),TimeoutError('deadline')])
def test_storage_failure_and_timeout(tmp_path,monkeypatch,error):
    def fail(*a,**kw):raise error
    monkeypatch.setattr('hooks.post_compact.dispatcher.save_event',fail)
    args=parse_args(['--hook','PostCompact','--log-state']);args.deadline_at=monotonic()+10
    logs=[]
    if isinstance(error,TimeoutError):
        with pytest.raises(TimeoutError):dispatch_hook(payload(tmp_path),args,'id',lambda *a,**kw:logs.append((a,kw)))
    else:
        result=dispatch_hook(payload(tmp_path),args,'id',lambda *a,**kw:logs.append((a,kw)))
        assert isinstance(result,Failure) and 'SENTINEL' not in repr((result,logs))


@pytest.mark.parametrize('trigger',['manual','auto'])
def test_compaction_repeated_shared_and_payload_exclusion(tmp_path,monkeypatch,trigger):
    from tests.support.sqlite_state import history, rows
    path=tmp_path.resolve()/'state.sqlite3'
    original=save_event
    monkeypatch.setattr('hooks.post_compact.dispatcher.save_event',lambda *a,**kw:original(*a,database_path=path,**kw))
    def forbidden(*a,**kw):pytest.fail('PostCompact cannot call model/source')
    from building_blocks import typescript
    from laya import predict,registry
    monkeypatch.setattr(predict,'real_loader',forbidden);monkeypatch.setattr(registry,'real_loader',forbidden)
    monkeypatch.setattr(typescript,'outline',forbidden)
    args=parse_args(['--hook','PostCompact','--log-state']);args.deadline_at=monotonic()+10
    for _ in range(2):assert isinstance(dispatch_hook(payload(tmp_path,trigger),args,'id',lambda *a,**kw:None),Noop)
    for flow in ('first_method_context','future'):
        assert [e['payload'] for e in history(path,'abc123',flow)]==[{},{}]
    assert len(rows(path))==2 and 'SECRET' not in repr(rows(path))
