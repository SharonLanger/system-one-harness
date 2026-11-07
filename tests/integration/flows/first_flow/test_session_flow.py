"""Final flow decision uses fixed blocks and real source utilities."""
from time import monotonic
import json
import pytest
from .conftest import PROMPT
from flows import first_method_context as flow
from flows.first_method_context_utils import EnrichmentDecision
from hooks import session_state
from hooks.contracts import Context, Noop
from hooks.gateway import serialize_response


def test_final_decision_metadata_and_skip(h, golden):
    calls=[]
    def finalize(selected): calls.append(selected); return EnrichmentDecision(False, 'already_enriched_since_compaction')
    result=h.run(finalize_enrichment=finalize)
    assert isinstance(result,Noop)
    assert serialize_response(result,'UserPromptSubmit') == ''
    assert not h.prepared()
    assert len(calls)==1
    s=calls[0]
    assert s.file_path == str(h.path.resolve())
    assert s.name == h.candidate['name'] and s.signature == h.candidate['signature']
    assert f'Lines: {s.start_line}–{s.end_line}' in golden
    assert h.run(finalize_enrichment=lambda _:EnrichmentDecision(True, 'no_history')) == Context(golden)


@pytest.mark.parametrize('kind',['no_file','action','selection','overflow','source'])
def test_early_routes_never_finalize(h,monkeypatch,kind):
    if kind=='action': h.action_result.data['action']='modify_code'
    if kind=='selection': h.selection_result.data.update(selected_id=None,reason='unclear')
    if kind=='source': monkeypatch.setattr(flow.typescript,'extract_callable',lambda *a: (_ for _ in ()).throw(ValueError()))
    calls=[]
    result=h.run(prompt='no file' if kind=='no_file' else PROMPT,
                 max_context_chars=1 if kind=='overflow' else 8000,
                 finalize_enrichment=lambda s:calls.append(s))
    assert not isinstance(result,Context) and not calls


@pytest.mark.parametrize('error',[RuntimeError('read_failed'),ValueError('invalid_session')])
def test_state_error_preserves_context_and_debug(h,monkeypatch,golden,error):
    def fail(*args,**kwargs): raise error
    monkeypatch.setattr(flow,'inspect_and_save_event',fail)
    assert flow.handle_prompt(h.request(deadline_at=monotonic()+20),h.logger,registry=h.registry)==Context(golden)
    assert any(e=='session_state_enrichment_failed' for _,e,_ in h.logs)
    assert not any(e=='session_state_saved' for _,e,_ in h.logs)


def test_adapter_uses_exact_session_deadline(h,monkeypatch):
    calls=[]; deadline=monotonic()+20
    def inspect(sid,decide,**kw):
        calls.append((sid,kw)); d,e=decide([])
        assert e['payload']['files'][0]['methods'][0]['startLine']>0
        return d
    monkeypatch.setattr(flow,'inspect_and_save_event',inspect)
    assert isinstance(flow.handle_prompt(h.request(deadline_at=deadline),h.logger,registry=h.registry),Context)
    assert calls==[('offline-session',{'deadline':deadline,'hook_name':'UserPromptSubmit','flow_name':'first_method_context'})]


def test_real_flow_session_sequence(h,monkeypatch,tmp_path):
    real=flow.inspect_and_save_event
    monkeypatch.setattr(flow,'inspect_and_save_event',lambda *a,**kw:real(*a,database_path=tmp_path / "state.sqlite3",**kw))
    def run(session='offline-session'):
        request=h.request(deadline_at=monotonic()+20)
        request.envelope.common['session_id']=session
        return flow.handle_prompt(request,h.logger,registry=h.registry)
    state_path=tmp_path / "state.sqlite3"
    assert isinstance(run(),Context)
    first=state_path.read_bytes()
    assert isinstance(run(),Noop) and state_path.read_bytes()==first
    original=h.candidate['name']
    other=next(c['name'] for c in h.outline['callables'] if c['name']!=original)
    h.choose(other)
    assert isinstance(run(),Context)
    h.choose(original)
    assert isinstance(run(),Noop)
    compact={'source':{'kind':'hook','name':'PostCompact'},'hook_name':'PostCompact','type':'PostCompact','payload':{}}
    session_state.save_event('offline-session',compact,database_path=tmp_path / "state.sqlite3",deadline=monotonic()+20)
    assert isinstance(run(),Context)
    after=state_path.read_bytes()
    assert isinstance(run(),Noop) and state_path.read_bytes()==after
    from tests.support.sqlite_state import history
    events=history(state_path, 'offline-session')
    assert [e['type'] for e in events]==['context_enriched','context_enriched','PostCompact','context_enriched']
    before_other=history(state_path, 'offline-session')
    assert isinstance(run('other-session'),Context)
    assert history(state_path, 'offline-session')==before_other
    assert any(e=='enrichment_skipped' for _,e,_ in h.logs)
    assert all(e['source']['name'] in ('first_method_context','PostCompact') for e in events)


def test_invalid_metadata_avoids_transaction(h,monkeypatch):
    def invalid(*a,**kw): raise ValueError('metadata')
    monkeypatch.setattr(flow,'SelectedMethod',invalid)
    calls=[]
    assert isinstance(h.run(finalize_enrichment=lambda s:calls.append(s)),Context)
    assert calls==[]
    assert any(e=='session_state_enrichment_failed' for _,e,_ in h.logs)


@pytest.mark.parametrize('allow',[True,False])
def test_regular_logger_failure_preserves_committed_decision(h,monkeypatch,allow):
    monkeypatch.setattr(flow,'inspect_and_save_event',lambda *a,**kw:EnrichmentDecision(allow,'no_history' if allow else 'already_enriched_since_compaction'))
    def logger(*a,**kw): raise RuntimeError('debug unavailable')
    result=flow.handle_prompt(h.request(deadline_at=monotonic()+20),logger,registry=h.registry)
    assert isinstance(result,Context if allow else Noop)


def test_finalizer_timeout_propagates(h,monkeypatch):
    def expired(*a,**kw): raise TimeoutError('deadline')
    monkeypatch.setattr(flow,'inspect_and_save_event',expired)
    with pytest.raises(TimeoutError):
        flow.handle_prompt(h.request(deadline_at=monotonic()+20),h.logger,registry=h.registry)


def test_navigation_fallback_still_finalizes(h,monkeypatch):
    def unavailable(*a,**kw): raise RuntimeError('navigation')
    monkeypatch.setattr(flow.typescript,'get_called_method_locations',unavailable)
    calls=[]
    def finalize(s): calls.append(s); return EnrichmentDecision(True,'no_history')
    result=h.run(finalize_enrichment=finalize)
    assert isinstance(result,Context) and len(calls)==1
    assert 'Methods called by' not in result.text


@pytest.mark.parametrize('changes',[
    {'file_path':'/other/orders.ts'},{'name':'other'},
    {'signature':'different(): number'},{'start_line':21},{'end_line':46}])
def test_identity_fields_with_real_store(tmp_path,changes):
    from dataclasses import replace
    from tests.support.sqlite_state import METHOD,transact,rows
    path=tmp_path.resolve()/'state.sqlite3'
    assert transact(path).allow
    assert transact(path,method=replace(METHOD,**changes)).allow
    assert not transact(path).allow and len(rows(path))==2


def test_body_edit_with_same_identity_stays_suppressed(h,monkeypatch,tmp_path):
    from tests.support.sqlite_state import rows
    path=tmp_path.resolve()/'state.sqlite3';real=flow.inspect_and_save_event
    monkeypatch.setattr(flow,'inspect_and_save_event',lambda *a,**kw:real(*a,database_path=path,**kw))
    source='export function stable(): number { return 1; }\n'
    h.path.write_text(source);h.choose('stable')
    assert isinstance(flow.handle_prompt(h.request(deadline_at=monotonic()+20),h.logger,registry=h.registry),Context)
    h.path.write_text(source.replace('return 1','return 2'));h.choose('stable')
    assert isinstance(flow.handle_prompt(h.request(deadline_at=monotonic()+20),h.logger,registry=h.registry),Noop)
    assert len(rows(path))==1


def test_real_committed_decisions_survive_logger_failure_and_do_not_leak(h,tmp_path):
    from tests.support.sqlite_state import METHOD,rows
    from types import SimpleNamespace
    path=tmp_path.resolve()/'state.sqlite3'
    store=SimpleNamespace(inspect_and_save_event=lambda *a,**kw:session_state.inspect_and_save_event(*a,database_path=path,**kw))
    def logger(*a,**kw):raise RuntimeError('diagnostic unavailable')
    request=h.request(deadline_at=monotonic()+20)
    assert flow.finalize_enrichment(request,logger,METHOD,session_store=store).allow
    assert not flow.finalize_enrichment(request,logger,METHOD,session_store=store).allow
    assert len(rows(path))==1
    captured=[]
    def failing(*a,**kw):raise RuntimeError('SECRET_PAYLOAD_SENTINEL')
    store.inspect_and_save_event=failing
    assert flow.finalize_enrichment(request,lambda *a,**kw:captured.append((a,kw)),METHOD,session_store=store).allow
    assert 'SECRET_PAYLOAD_SENTINEL' not in repr(captured)


def test_memory_store_relevance_matches_sqlite(tmp_path):
    from tests.support.memory_session_store import MemorySessionStore
    from tests.support.sqlite_state import COMPACT,event,history
    from types import SimpleNamespace
    path=tmp_path.resolve()/'state.sqlite3';memory=MemorySessionStore()
    values=[event('A'),event('B'),COMPACT,event('A'),{**COMPACT,'type':'future'},event('B',hook='PostCompact',kind='PostCompact')]
    for value in values:
        assert memory.save_event('s',value,deadline=monotonic()+20) is None
        session_state.save_event('s',value,database_path=path,deadline=monotonic()+20)
    for name in ('A','B','C'):
        loaded=[]
        memory.inspect_and_save_event('s',lambda h:(loaded.extend(h) or EnrichmentDecision(False,'test'),None),
            deadline=monotonic()+20,hook_name='UserPromptSubmit',flow_name=name)
        real=history(path,flow=name)
        strip=lambda events:[{k:e[k] for k in ('source','hook_name','type','payload')} for e in events]
        assert strip(loaded)==strip(real)


@pytest.mark.parametrize('failure',['oversized_callback','corrupt_history'])
def test_real_storage_failures_do_not_leak_across_caller_channels(h,tmp_path,capfd,failure):
    from dataclasses import replace
    from hashlib import sha256
    from types import SimpleNamespace
    import sqlite3
    from tests.support.sqlite_state import METHOD,rows,transact
    from tests.support.state_assertions import assert_no_payload_leaks,capture_state_error
    marker='SOH_STORE_FAILURE_PRIVATE_MARKER'
    path=tmp_path.resolve()/'state.sqlite3'
    assert transact(path,'offline-session').allow
    selected=replace(METHOD,signature=marker*3000)
    if failure=='corrupt_history':
        with sqlite3.connect(path) as db:
            db.execute('UPDATE session_events SET payload_json=?',('{'+marker,))
    fingerprint=lambda:sha256(json.dumps(rows(path),ensure_ascii=False).encode()).hexdigest()
    before=fingerprint();captured_logs=[]
    # Exercise the public failure and then the actual enrichment caller with
    # the same real failing database/event, without replacing the store oracle.
    error_type,error_text=capture_state_error(lambda:transact(path,'offline-session',method=selected))
    store=SimpleNamespace(inspect_and_save_event=lambda *a,**kw:
        session_state.inspect_and_save_event(*a,database_path=path,**kw))
    result=flow.finalize_enrichment(h.request(deadline_at=monotonic()+20),
        lambda *args,**details:captured_logs.append((args,details)),selected,session_store=store)
    captured=capfd.readouterr()
    assert_no_payload_leaks({'stdout':captured.out,'stderr':captured.err,
        'exception':error_text,'result':repr(result),'logger_arguments':repr(captured_logs)},[marker])
    assert captured.out==captured.err=='' and error_type is ValueError
    assert result.allow and result.reason=='state_unavailable'
    assert len(captured_logs)==1 and captured_logs[0][0][1]=='session_state_enrichment_failed'
    assert fingerprint()==before and len(rows(path))==1
