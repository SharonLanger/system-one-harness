"""Real SQLite transactions and process checkpoints (TX/ERR acceptance cases)."""
from dataclasses import replace
import multiprocessing as mp
from pathlib import Path
import sqlite3
from time import monotonic
from types import SimpleNamespace
import pytest
from hooks import session_state as state
from tests.support.sqlite_state import COMPACT, METHOD, event, history, rows, save, transact


@pytest.fixture
def tmp_path(tmp_path):
    # macOS temp roots can include /var aliases. Use a canonical test override.
    return tmp_path.resolve()


def test_round_trip_and_compaction(tmp_path, capsys):
    path = tmp_path/'state.sqlite3'
    assert transact(path).allow
    first = rows(path)
    assert not transact(path).allow and rows(path) == first
    save(path)
    assert transact(path).allow and not transact(path).allow
    assert [e['type'] for e in history(path)] == ['context_enriched','PostCompact','context_enriched']
    assert transact(path, 'other').allow
    assert len(history(path, 'other')) == 1
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('result', [None, (), (None,None), (True,{}),
    (SimpleNamespace(allow=1),{}), (SimpleNamespace(allow=True),None), (SimpleNamespace(allow=False),{})])
def test_invalid_decision_no_write(tmp_path,result):
    path=tmp_path/'state.sqlite3'
    with pytest.raises(ValueError): transact(path, callback=lambda _:result)
    assert rows(path) == []
    assert transact(path).allow


def test_callback_failure_and_skip_never_write(tmp_path):
    path=tmp_path/'state.sqlite3'
    save(path);previous=rows(path)
    def fail(history): raise ValueError('callback')
    with pytest.raises(ValueError): transact(path, callback=fail)
    assert not transact(path, callback=lambda _: (SimpleNamespace(allow=False),None)).allow
    assert rows(path)==previous
    assert transact(path).allow


def worker(path, barrier, queue, session='s', name='calculate', hold=None, release=None):
    try:
        if barrier is not None: barrier.wait(timeout=10)
        method=replace(METHOD,name=name)
        def decide(events):
            from flows.first_method_context_utils import build_enrichment_event, decide_enrichment
            if hold is not None:
                hold.set()
                if not release.wait(10): raise TimeoutError('test_checkpoint')
            decision=decide_enrichment(events,method)
            return decision,build_enrichment_event(method) if decision.allow else None
        result=transact(Path(path),session,callback=decide)
        queue.put(('ok',result.allow))
    except BaseException as exc:
        queue.put(('error',type(exc).__name__))


def finish(processes, queue=None):
    for process in processes:
        if process.pid:
            process.join(timeout=5)
            if process.is_alive(): process.terminate(); process.join(timeout=5)
            assert not process.is_alive()
    if queue is not None: queue.close(); queue.join_thread()


@pytest.mark.parametrize('variant',['same','different_method','different_session','first_use'])
def test_competing_processes(tmp_path,variant):
    path=tmp_path/'new/state.sqlite3'
    ctx=mp.get_context('spawn'); count=4 if variant=='first_use' else 2
    barrier=ctx.Barrier(count); queue=ctx.Queue()
    processes=[ctx.Process(target=worker,args=(str(path),barrier,queue,
        str(i) if variant in ('different_session','first_use') else 's',
        str(i) if variant=='different_method' else 'calculate')) for i in range(count)]
    try:
        for p in processes: p.start()
        results=[queue.get(timeout=30) for _ in processes]
        assert all(status=='ok' for status,_ in results),results
        expected=1 if variant=='same' else count
        assert sum(value for _,value in results)==expected
        assert len(rows(path))==expected
        if variant=='different_session':
            assert all(len(history(path,str(i)))==1 for i in range(count))
    finally: finish(processes,queue)


def test_competitor_observes_committed_history(tmp_path):
    path=tmp_path/'state.sqlite3';ctx=mp.get_context('spawn');queue=ctx.Queue()
    held=ctx.Event();release=ctx.Event()
    a=ctx.Process(target=worker,args=(str(path),None,queue,'s','calculate',held,release))
    b=ctx.Process(target=worker,args=(str(path),None,queue))
    try:
        a.start(); assert held.wait(10)
        b.start(); release.set()
        assert sorted([queue.get(timeout=30),queue.get(timeout=30)])==[('ok',False),('ok',True)]
        assert len(rows(path))==1
    finally: release.set(); finish([a,b],queue)


def compact_worker(path, acquired, release, queue):
    try:
        with state._transaction(Path(path),monotonic()+20) as store:
            acquired.set()
            if not release.wait(10): raise TimeoutError('test_checkpoint')
            store.append('s',state._event(COMPACT))
        queue.put(('ok','compact'))
    except BaseException as exc: queue.put(('error',type(exc).__name__))


@pytest.mark.parametrize('first_mode',['compact','enrich'])
def test_compaction_and_enrichment_lock_order(tmp_path,first_mode):
    path=tmp_path/'state.sqlite3'; assert transact(path).allow
    ctx=mp.get_context('spawn'); held=ctx.Event();release=ctx.Event();queue=ctx.Queue()
    other_held=ctx.Event();other_release=ctx.Event();other_release.set()
    comp_args=(str(path),held if first_mode=='compact' else other_held,
               release if first_mode=='compact' else other_release,queue)
    enrich_args=(str(path),None,queue,'s','calculate',held if first_mode=='enrich' else other_held,
                 release if first_mode=='enrich' else other_release)
    compact=ctx.Process(target=compact_worker,args=comp_args)
    enrich=ctx.Process(target=worker,args=enrich_args)
    first,second=(compact,enrich) if first_mode=='compact' else (enrich,compact)
    try:
        first.start();assert held.wait(10)
        second.start();release.set()
        results=[queue.get(timeout=30),queue.get(timeout=30)]
        assert all(status=='ok' for status,_ in results),results
        assert ('ok',first_mode=='compact') in results
        expected=['context_enriched','PostCompact']+(['context_enriched'] if first_mode=='compact' else [])
        assert [e['type'] for e in history(path)]==expected
        assert transact(path).allow==(first_mode=='enrich')
    finally: release.set(); finish([first,second],queue)


def crash_worker(path, checkpoint, release, committed):
    with state._transaction(Path(path),monotonic()+20) as store:
        store.append('s',state._event(COMPACT))
        if not committed:
            checkpoint.set();release.wait(15)
    if committed:
        checkpoint.set();release.wait(15)


@pytest.mark.parametrize('committed',[False,True])
def test_process_exit_checkpoint(tmp_path,committed):
    path=tmp_path/'state.sqlite3';save(path)
    ctx=mp.get_context('spawn');checkpoint=ctx.Event();release=ctx.Event()
    process=ctx.Process(target=crash_worker,args=(str(path),checkpoint,release,committed))
    try:
        process.start();assert checkpoint.wait(15)
        process.terminate();process.join(timeout=5)
        assert len(history(path))==1+int(committed)
        assert transact(path).allow
    finally: finish([process])


def test_expired_deadline(tmp_path):
    path=tmp_path/'absent/state.sqlite3'
    with pytest.raises(TimeoutError):transact(path,deadline=monotonic()-1)
    assert not path.parent.exists()


def test_real_lock_deadline_and_callback_count(tmp_path):
    path=tmp_path/'state.sqlite3';save(path);calls=[]
    with sqlite3.connect(path,isolation_level=None) as db:
        db.execute('BEGIN IMMEDIATE')
        with pytest.raises(TimeoutError):
            transact(path,callback=lambda h:calls.append(h),deadline=monotonic()+0.05)
        db.rollback()
    assert calls==[] and len(rows(path))==1
    assert transact(path).allow


def test_lock_release_within_budget(tmp_path):
    path=tmp_path/'state.sqlite3';save(path)
    ctx=mp.get_context('spawn');held=ctx.Event();release=ctx.Event();q=ctx.Queue()
    p=ctx.Process(target=compact_worker,args=(str(path),held,release,q))
    try:
        p.start();assert held.wait(10)
        # Releasing a coordinated holder allows one decision, without replay.
        release.set();calls=[]
        def deny(h): calls.append(len(h));return SimpleNamespace(allow=False),None
        assert not transact(path,callback=deny).allow
        assert q.get(timeout=20)==('ok','compact') and len(calls)==1
    finally: release.set();finish([p],q)


@pytest.mark.parametrize('phase',['read','callback','insert','commit'])
def test_deadline_rollback_in_each_phase(tmp_path,monkeypatch,phase):
    path=tmp_path/'state.sqlite3';save(path);real=state.monotonic; expired=[False];calls=[]
    monkeypatch.setattr(state,'monotonic',lambda: float('inf') if expired[0] else real())
    original=state._Store.execute
    def execute(self,sql,parameters=()):
        result=original(self,sql,parameters)
        if phase=='read' and sql.startswith('SELECT event_id'): expired[0]=True
        if phase=='insert' and sql.startswith('INSERT'): expired[0]=True
        if phase=='commit' and sql=='COMMIT':
            # Simulates expiry immediately before commit, so no ambiguous outcome.
            pass
        return result
    monkeypatch.setattr(state._Store,'execute',execute)
    append=state._Store.append
    def inserting(self,*args):
        append(self,*args)
        if phase=='commit': expired[0]=True
    monkeypatch.setattr(state._Store,'append',inserting)
    def decide(h):
        calls.append(1)
        if phase=='callback': expired[0]=True
        return SimpleNamespace(allow=True),event()
    with pytest.raises(TimeoutError):transact(path,callback=decide)
    expired[0]=False
    assert len(rows(path))==1 and len(calls)<=1
    monkeypatch.setattr(state._Store,'execute',original)
    monkeypatch.setattr(state._Store,'append',append)
    assert transact(path).allow


@pytest.mark.parametrize('phase',['insert','commit','uncertain_commit'])
def test_io_failure_no_retry_and_cleanup(tmp_path,monkeypatch,phase):
    path=tmp_path/'state.sqlite3';save(path);calls=[];inserts=[]
    original=state._Store.execute
    def execute(self,sql,parameters=()):
        if sql.startswith('INSERT'): inserts.append(1)
        target=sql.startswith('INSERT') if phase=='insert' else sql=='COMMIT'
        if target:
            if phase=='uncertain_commit': original(self,sql,parameters)
            raise sqlite3.OperationalError('disk I/O error')
        return original(self,sql,parameters)
    monkeypatch.setattr(state._Store,'execute',execute)
    def decide(h): calls.append(1);return SimpleNamespace(allow=True),event()
    with pytest.raises(RuntimeError,match='storage_failed'):transact(path,callback=decide)
    assert len(calls)==len(inserts)==1
    assert len(rows(path))==(2 if phase=='uncertain_commit' else 1)
    monkeypatch.setattr(state._Store,'execute',original)
    assert transact(path).allow


def test_sql_progress_deadline_and_ordinary_error_are_distinct(tmp_path,monkeypatch):
    path=tmp_path/'state.sqlite3';save(path);original=state._Store.history
    def long_query(self,*args):
        return self.execute('''WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<100000000)
            SELECT sum(x) FROM n''').fetchall()
    monkeypatch.setattr(state._Store,'history',long_query)
    with pytest.raises(TimeoutError):transact(path,deadline=monotonic()+0.03)
    def broken(self,*args): self.execute('SELECT nonexistent_column FROM session_events')
    monkeypatch.setattr(state._Store,'history',broken)
    with pytest.raises(RuntimeError,match='storage_failed'):transact(path)
    monkeypatch.setattr(state._Store,'history',original)
    assert transact(path).allow


def mixed_worker(path,barrier,queue,flow):
    try:
        barrier.wait(timeout=10)
        allowed=0
        for i in range(8):
            def decide(events):
                previous=next((e for e in reversed(events) if e['type'] in ('PostCompact','step')),None)
                allow=previous is None or previous['type']=='PostCompact'
                return SimpleNamespace(allow=allow),event(flow,kind='step') if allow else None
            allowed+=transact(Path(path),callback=decide,flow=flow).allow
            if i%2==0:save(Path(path))
        queue.put(('ok',allowed))
    except BaseException as exc:queue.put(('error',type(exc).__name__))


def test_bounded_mixed_flow_stress(tmp_path):
    path=tmp_path/'state.sqlite3';ctx=mp.get_context('spawn');barrier=ctx.Barrier(3);q=ctx.Queue()
    ps=[ctx.Process(target=mixed_worker,args=(str(path),barrier,q,f'flow{i}')) for i in range(3)]
    try:
        for p in ps:p.start()
        results=[q.get(timeout=30) for _ in ps]
        assert all(status=='ok' for status,_ in results),results
        assert len(rows(path))==sum(n for _,n in results)+12
        for i in range(3):
            seen=False
            for e in history(path,flow=f'flow{i}'):
                if e['type']=='PostCompact':seen=False
                else:assert not seen;seen=True
    finally:finish(ps,q)
