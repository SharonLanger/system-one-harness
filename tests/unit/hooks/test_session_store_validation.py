"""Common envelope/JSON boundaries use the public SQLite API."""
from copy import deepcopy
import inspect
import json
import math
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
from time import monotonic
from types import SimpleNamespace
import pytest
from hooks import session_state as state
from tests.support.sqlite_state import COMPACT, event, history, rows, save, transact
from tests.support.state_assertions import assert_no_payload_leaks,capture_state_error


@pytest.fixture
def db_path(tmp_path):
    return tmp_path.resolve()/'private/state.sqlite3'


def test_initialization_metadata_permissions_and_cleanup(db_path):
    assert save(db_path,event(payload={'value':'שלום 😀'})) is None
    row=rows(db_path)[0]
    assert row[1:5]==('s','UserPromptSubmit','first_method_context','context_enriched')
    assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z',row[5])
    assert json.loads(row[6])=={'value':'שלום 😀'}
    assert stat.S_IMODE(db_path.parent.stat().st_mode)==0o700
    assert stat.S_IMODE(db_path.stat().st_mode)==0o600
    with sqlite3.connect(db_path) as db:
        assert db.execute('PRAGMA index_info(session_events_session_order)').fetchall()[0][2]=='session_id'
        assert [r[1] for r in db.execute('PRAGMA table_info(session_events)')]==[
            'event_id','session_id','hook_name','flow_name','event_type','recorded_at','payload_json']
    save(db_path)
    assert len(history(db_path))==2
    assert rows(db_path)[1][2:5]==('PostCompact',None,'PostCompact')


def test_no_global_umask_change(db_path,monkeypatch):
    monkeypatch.setattr(state.os,'umask',lambda *a:pytest.fail('must not change process umask'))
    save(db_path)


@pytest.mark.parametrize('session',['',' ','../escape','a/b','é',None,1,True])
def test_invalid_sessions(db_path,session):
    with pytest.raises(ValueError,match='invalid_session_id'):
        save(db_path,session=session)
    assert not db_path.exists()


@pytest.mark.parametrize('field,value',[(field,value) for field in ('hook_name','type')
    for value in ('',' ',None,3,[])])
def test_invalid_metadata(db_path,field,value):
    e=event();e[field]=value
    with pytest.raises(ValueError):save(db_path,e)
    assert not db_path.exists()


@pytest.mark.parametrize('source',[None,{}, {'kind':'other','name':'x'}, {'kind':'flow','name':' '},
    {'kind':'flow','name':3},{'kind':'hook','name':'Other'}, {'kind':'flow','name':'x','extra':1}])
def test_invalid_sources(db_path,source):
    e=event();e['source']=source
    with pytest.raises(ValueError):save(db_path,e)
    assert not db_path.exists()


def test_json_roundtrip_detached_and_future_types(db_path):
    payload={'object':{},'nested':[None,True,False,12,-1,1.25,{'text':'"\\\nשלום 😀'}]}
    original=deepcopy(payload)
    save(db_path,event(flow='future',hook='FutureHook',kind='future_kind',payload=payload))
    payload['nested'].append('changed')
    result=history(db_path,flow='future');assert result[0]['payload']==original
    result[0]['payload']['nested'].clear()
    assert history(db_path,flow='future')[0]['payload']==original
    assert history(db_path)==[]


def cycle():
    value={};value['cycle']=value;return value


@pytest.mark.parametrize('payload',[[],None,'text',42,{'x':math.nan},{'x':math.inf},{'x':-math.inf},
    {'x':set()}, {'x':object()}, {1:'x'}, {'nested':[{'bad':{False:1}}]},cycle(), {'x':('tuple',)}])
def test_invalid_json_never_coerced(db_path,payload,capsys):
    with pytest.raises(ValueError,match='payload'):save(db_path,event(payload=payload) if payload is not None else {**event(),'payload':None})
    assert not db_path.exists() and capsys.readouterr().out==''


@pytest.mark.parametrize('size',[65536,65537])
@pytest.mark.parametrize('prefix',['','😀','"\\\n'])
def test_exact_utf8_boundary(db_path,size,prefix,capfd):
    marker='SOH_REJECTED_PAYLOAD_PRIVATE'
    prefix=marker+prefix
    error_text='';error_type=None
    base={'text':prefix};overhead=len(json.dumps(base,ensure_ascii=False,separators=(',',':')).encode())
    payload={'text':prefix+'x'*(size-overhead)}
    encoded=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
    byte_count=len(encoded)
    assert byte_count==size
    if size==65536:
        save(db_path,event(payload=payload))
        if history(db_path)[0]['payload']!=payload:
            pytest.fail('accepted payload did not round-trip',pytrace=False)
    else:
        error_type,error_text=capture_state_error(lambda:save(db_path,event(payload=payload)))
        assert not db_path.exists()
    captured=capfd.readouterr()
    assert_no_payload_leaks({'stdout':captured.out,'stderr':captured.err,'exception':error_text},[marker])
    assert captured.out==captured.err==''
    if size==65537:
        assert error_type is ValueError and 'too_large' in error_text


def test_oversize_callback_rollback_and_sql_constraint(db_path,capfd):
    marker='SOH_OVERSIZED_CALLBACK_PRIVATE'
    save(db_path);before=rows(db_path)
    callback_type,callback_text=capture_state_error(lambda:
        transact(db_path,callback=lambda h:(SimpleNamespace(allow=True),event(payload={'x':marker*10000}))))
    assert rows(db_path)==before
    with sqlite3.connect(db_path) as db:
        with pytest.raises(sqlite3.IntegrityError) as sql_error:
            db.execute('INSERT INTO session_events(session_id,hook_name,event_type,recorded_at,payload_json) VALUES (?,?,?,?,?)',
                       ('s','h','e','t','x'*65537))
    assert rows(db_path)==before
    captured=capfd.readouterr()
    assert_no_payload_leaks({'stdout':captured.out,'stderr':captured.err,
        'exception':callback_text,'sql_exception':repr(sql_error.value)},[marker])
    assert captured.out==captured.err=='' and callback_type is ValueError


def test_sql_injection_strings_remain_data(db_path):
    marker="'); DROP TABLE session_events; --"
    save(db_path,event(flow=marker,hook=marker,kind=marker,payload={'x':marker}))
    assert history(db_path,flow=marker)[0]['payload']=={'x':marker}
    assert rows(db_path)[0][2:5]==(marker,marker,marker)


@pytest.mark.parametrize('other',[event(flow='other'),event(hook='other'),COMPACT])
def test_callback_origin_conflict(db_path,other):
    with pytest.raises(ValueError,match='origin'):
        transact(db_path,callback=lambda h:(SimpleNamespace(allow=True),other))
    assert rows(db_path)==[]


def test_no_node_dependency_and_new_api_only(db_path,monkeypatch):
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('no subprocess in persistence'))
    assert save(db_path) is None
    assert transact(db_path).allow
    assert not transact(db_path).allow
    assert set(inspect.signature(state.save_event).parameters)=={'session_id','event','deadline','database_path'}
    assert set(inspect.signature(state.inspect_and_save_event).parameters)=={
        'session_id','decide','deadline','hook_name','flow_name','database_path'}


def test_default_root_independent_of_cwd_and_old_jsonl(tmp_path,monkeypatch):
    root=tmp_path.resolve()/'installation';root.mkdir();project=tmp_path/'project';project.mkdir()
    old=root/'logs/session-state/s.jsonl';old.parent.mkdir(parents=True);old.write_text('OLD_SYNTHETIC_SENTINEL')
    monkeypatch.setattr(state,'ROOT',root);monkeypatch.chdir(project)
    assert state.inspect_and_save_event('s',lambda h:(SimpleNamespace(allow=False),None),
        hook_name='UserPromptSubmit',flow_name='first_method_context',deadline=monotonic()+10).allow is False
    assert rows(root/'.soh/session-state.sqlite3')==[]
    assert old.read_text()=='OLD_SYNTHETIC_SENTINEL' and not (project/'.soh').exists()
    state.save_event('s',COMPACT,deadline=monotonic()+10)
    assert len(rows(root/'.soh/session-state.sqlite3'))==1


def test_relative_override_no_fallback(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError,match='absolute'):save(Path('state.sqlite3'))
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('variant',['text','empty_sqlite','bad_columns','wrong_type','missing_index','bad_index','symlink','parent_symlink','directory'])
def test_existing_database_validation(tmp_path,variant):
    path=tmp_path.resolve()/'state.sqlite3'
    if variant=='text':path.write_bytes(b'not SQLite SENTINEL')
    elif variant=='directory':path.mkdir()
    elif variant=='symlink':
        target=tmp_path/'target';target.write_bytes(b'SENTINEL');path.symlink_to(target)
    elif variant=='parent_symlink':
        target=tmp_path/'target';target.mkdir();link=tmp_path/'link';link.symlink_to(target,target_is_directory=True);path=link/'state.sqlite3'
    else:
        with sqlite3.connect(path) as db:
            if variant=='bad_columns':db.execute('CREATE TABLE session_events(x TEXT)')
            elif variant=='wrong_type':db.execute(state._TABLE.replace('session_id TEXT','session_id INTEGER'))
            elif variant!='empty_sqlite':
                db.execute(state._TABLE)
                if variant=='bad_index':db.execute('CREATE INDEX session_events_session_order ON session_events(hook_name)')
    before=path.read_bytes() if path.is_file() else None
    if variant in ('empty_sqlite','missing_index'):
        save(path);assert len(rows(path))==1
    else:
        with pytest.raises((ValueError,RuntimeError)):save(path)
        if before is not None:assert path.read_bytes()==before
        if variant=='parent_symlink':assert list(target.iterdir())==[]


@pytest.mark.parametrize("encoded", ["{SENTINEL", "[]", "null", '{"x":NaN}'])
def test_corrupt_payload_rejected_without_content(db_path,encoded):
    save(db_path,event())
    with sqlite3.connect(db_path) as db:db.execute("UPDATE session_events SET payload_json=?",(encoded,))
    with pytest.raises(ValueError,match='invalid_session_state_history') as error:history(db_path)
    assert 'SENTINEL' not in str(error.value)
    assert len(rows(db_path))==1


def test_filter_order_scope_and_no_retention(db_path,monkeypatch):
    assert history(db_path)==[]
    stamps=iter(['2099-01-01T00:00:00.000Z','1900-01-01T00:00:00.000Z']+['1900-01-01T00:00:00.000Z']*510)
    monkeypatch.setattr(state,'_timestamp',lambda:next(stamps))
    save(db_path,event('A'));save(db_path,event('B'));save(db_path)
    save(db_path,event('A'));save(db_path,event('A'),session='other')
    save(db_path,{**COMPACT,'type':'future'})
    save(db_path,{**COMPACT,'hook_name':'Other','source':{'kind':'hook','name':'Other'}})
    save(db_path,event('B',hook='PostCompact',kind='PostCompact'))
    with sqlite3.connect(db_path) as db:db.execute('UPDATE session_events SET event_id=event_id+100')
    for _ in range(500):save(db_path,event('B'))
    a=history(db_path,flow='A');b=history(db_path,flow='B');c=history(db_path,flow='C')
    assert [e['type'] for e in a]==['context_enriched','PostCompact','context_enriched']
    assert len(c)==1 and c[0]['type']=='PostCompact'
    assert len(b)==503 and len(history(db_path,session='other',flow='A'))==1
    assert sum(row[2]=='PostCompact' and row[3] is None and row[4]=='PostCompact' for row in rows(db_path))==1
    assert [e['event_id'] for e in a]==sorted(e['event_id'] for e in a)
    assert a[0]['timestamp']>a[-1]['timestamp']
    assert len(rows(db_path))==508


def test_read_only_database_directory_and_restore(tmp_path):
    # Standard test runners use an ordinary account and real OS permissions.
    import os
    import sys
    root=tmp_path.resolve();path=root/'readonly.sqlite3';save(path);path.chmod(0o400)
    command='''from pathlib import Path
from time import monotonic
from hooks.session_state import save_event
try:
 save_event('s', {'source':{'kind':'hook','name':'PostCompact'},'hook_name':'PostCompact','type':'PostCompact','payload':{}}, deadline=monotonic()+5,database_path=Path(__import__('sys').argv[1]))
except RuntimeError:
 raise SystemExit(0)
raise SystemExit(2)
'''
    assert subprocess.run([sys.executable,'-c',command,str(path)],env={**os.environ,'PYTHONPATH':str(state.ROOT/'src')},capture_output=True,timeout=10).returncode==0
    path.chmod(0o600);save(path);assert len(rows(path))==2
    directory=root/'readonly';directory.mkdir();directory.chmod(0o500)
    try:
        assert subprocess.run([sys.executable,'-c',command,str(directory/'state.sqlite3')],env={**os.environ,'PYTHONPATH':str(state.ROOT/'src')},capture_output=True,timeout=10).returncode==0
    finally:directory.chmod(0o700)


@pytest.mark.parametrize('phase',['save','allow','deny','callback','validation','sql','commit'])
def test_real_connections_close_once_on_success_and_failure(db_path,monkeypatch,phase):
    save(db_path)
    if phase=='deny':assert transact(db_path).allow
    before_count=len(rows(db_path));created=[];real_factory=state.sqlite3.connect
    class RecordedConnection(sqlite3.Connection):
        close_count=0
        def close(self):
            self.close_count+=1
            return super().close()
        def execute(self,sql,parameters=()):
            if (phase=='sql' and sql.startswith('INSERT')) or (phase=='commit' and sql=='COMMIT'):
                # A real SQL failure at the desired phase, keeping SQLite's
                # actual transaction and rollback/close behavior active.
                return super().execute('SELECT absent_column FROM session_events')
            return super().execute(sql,parameters)
    def connect(*args,**kwargs):
        connection=real_factory(*args,factory=RecordedConnection,**kwargs)
        created.append(connection)
        return connection
    monkeypatch.setattr(state.sqlite3,'connect',connect)
    def fail(history):raise ValueError('synthetic_callback_failure')
    def invalid(history):return SimpleNamespace(allow=True),event(payload={'unsupported':set()})
    if phase in ('callback','validation','sql','commit'):
        with pytest.raises((ValueError,RuntimeError)):
            transact(db_path,callback=fail if phase=='callback' else invalid if phase=='validation' else None)
    elif phase=='save':save(db_path)
    else:assert transact(db_path).allow==(phase=='allow')
    assert len(created)==1 and created[0].close_count==1
    with pytest.raises(sqlite3.ProgrammingError,match='closed'):
        created[0].execute('SELECT 1')
    monkeypatch.setattr(state.sqlite3,'connect',real_factory)
    expected=before_count+int(phase in ('save','allow'))
    assert len(rows(db_path))==expected
    save(db_path)
    assert len(rows(db_path))==expected+1


def test_shared_references_roundtrip_but_nested_cycles_fail(db_path):
    shared={'values':[1,None,{'text':'Unicode שלום'}]}
    payload={'left':shared,'right':shared}
    assert payload['left'] is payload['right']
    save(db_path,event(payload=payload))
    loaded=history(db_path)[0]['payload']
    assert loaded==payload and loaded['left'] is not loaded['right']
    loaded['left']['values'].clear()
    assert history(db_path)[0]['payload']==payload
    nested={'branch':{'children':[]}}
    nested['branch']['children'].append(nested['branch'])
    with pytest.raises(ValueError,match='payload'):save(db_path,event(payload=nested))
    assert len(rows(db_path))==1


def test_append_does_not_scan_corrupt_history_under_fresh_contract(db_path):
    save(db_path,event())
    with sqlite3.connect(db_path) as db:
        db.execute('UPDATE session_events SET payload_json=?',('{synthetic_corruption',))
    # Unlike the obsolete JSONL adapter, a plain append need not scan history.
    save(db_path)
    assert len(rows(db_path))==2
    with pytest.raises(ValueError,match='invalid_session_state_history'):history(db_path)
