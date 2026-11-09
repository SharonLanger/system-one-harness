"""Real browser regression schedules consolidated from review round one."""
from dataclasses import replace
from datetime import timedelta
import copy
import hashlib
import json
import os
import shutil
import threading
import pytest
from playwright.sync_api import expect
from .case_loader import HERE
from .conftest import NOW
from .helpers import line,write
from .test_browser_scenarios import browser
from .test_http_scenarios import server,request
from .test_query_scenarios import LOCATIONS

DATES=[('both','2026-11-09T09:00','2026-11-09T10:00',['r12','r16','r11','r10']),('before','','2026-11-09T09:30',['r10','r09','r08','r07','r06','r05','r04','r03','r02','r01']),('after','2026-11-09T09:45','',['r15','r14','r13','r12','r16']),('seconds','2026-11-09T09:00:01','2026-11-09T09:30:01',['r11']),('fraction','2026-11-09T09:59:59.998','2026-11-09T10:00:00.001',['r13','r12'])]
CASES=['CR1-tiny-batches','CR1-default-large-details','CR3-startup','TC06-draft-actions','TC06-running-draft','TC06-shortcuts','TC06-continuation','TC07-refresh','TC09-cancel','TC09-expiry','TC09-unreadable','TC09-partial','TC09-missing-root','TC10-list-inspector','TC10-scalar-inspector','TC11-fidelity','STALE-detail-error','STALE-diagnostic-error','CR2-invalid-nonempty']+['CR2-'+d[0] for d in DATES]

@pytest.mark.parametrize('case',CASES)
def test_remediation_browser(case,browser,server,service,history,monkeypatch):
    context=browser.new_context(permissions=['clipboard-read','clipboard-write']);page=context.new_page();creates=[];advances=[];outbound=[];release=threading.Event();entered=threading.Event()
    page.on('request',lambda r:creates.append(json.loads(r.post_data)) if r.method=='POST' and r.url==server.origin+'/api/searches' else None)
    page.on('request',lambda r:outbound.append(r.url) if not r.url.startswith(server.origin) else None)
    def clear():shutil.rmtree(history);history.mkdir()
    if case=='CR1-tiny-batches':
        clear();limits=replace(service.limits,block_bytes=256,advance_bytes=256,advance_seconds=1,handles=6,client_pages=2);service.limits=service.reader.limits=limits
        write(history,b''.join(line('match-'+str(i),details={'payload':'x'*700})+line('skip',details={})*3 for i in range(25)))
    if case=='CR1-default-large-details':clear();write(history,b''.join(line('large-'+str(i),details={'payload':'x'*(128*1024)}) for i in range(250)))
    if case in ('TC10-list-inspector','TC10-scalar-inspector','TC11-fidelity'):
        clear();details=['世界\nline','<img src="https://outside.invalid" onerror="window.pwned=1">'] if case=='TC10-list-inspector' else 314 if case=='TC10-scalar-inspector' else {'nested':{'unicode':'世界\nhello','html':'<script>window.pwned=1</script>'},'array':[1,None,True]}
        original={'schema_version':1,'timestamp':'2026-11-09T09:30:00.000001Z','level':'INFO','message':'complete message\n世界 '+('x'*600),'event':'inspect','pid':8,'component':'unit','component_type':'cli','session_id':'A','correlation_id':'C','flow_name':None,'hook_event':None,'details':details,'unknown':{'retained':['é',False]}}
        raw=json.dumps(original,ensure_ascii=False,separators=(',',':')).encode();path=write(history,line('before')+raw+b'\n'+line('after'));expected_source={'path':'2026-11-09/09-00.jsonl','offset':len(line('before')),'length':len(raw)}
    if case=='TC09-missing-root':shutil.rmtree(history)
    if case=='TC09-unreadable':
        actual=os.open
        def denied(path,flags,*args,**kw):
            if str(path).endswith('.jsonl'):raise PermissionError('controlled EACCES')
            return actual(path,flags,*args,**kw)
        monkeypatch.setattr(os,'open',denied)
    if case=='TC09-partial':
        service.reader.limits=replace(service.limits,advance_seconds=0)
    before_hash={str(p):hashlib.sha256(p.read_bytes()).digest() for p in history.rglob('*') if p.is_file()} if history.exists() else {}
    if case.startswith('CR1'):
        actual=service.advance
        def retried(id,cursor):
            result=actual(id,cursor);progress=dict(service.registry[id].snapshot.progress);assert actual(id,cursor)==result;assert progress==service.registry[id].snapshot.progress;advances.append(copy.deepcopy(result));return result
        monkeypatch.setattr(service,'advance',retried)
    try:
        page.goto(server.url)
        page.wait_for_function('() => viewer.configReady !== undefined')
        page.evaluate('() => viewer.configReady')
        def idle():page.wait_for_function('() => !viewer.busy && !viewer.submitting')
        def run():page.locator('#run').click();idle()
        def alltime():page.locator('#timeMode').select_option('all')
        def rows():return page.evaluate('() => viewer.pages.flat()')
        def ids():return [LOCATIONS[(r['source']['path'],r['source']['offset'])] for r in rows()]
        def state():return page.evaluate('() => ({submitted:viewer.submitted,query:viewer.queryId,selected:viewer.selectedRecord,pages:viewer.pages})')
        if case=='CR3-startup':
            assert page.locator('#filters').is_visible();assert page.locator('#relativeSeconds').input_value()=='3600';assert page.locator('#level').input_value()=='';assert page.locator('#results').is_hidden();assert page.locator('#inspector').is_hidden();assert service.registry=={}
            # Forbid both inventory and content access, including direct fallback.
            monkeypatch.setattr(service.reader,'_inventory',lambda *a:pytest.fail('startup inventory'));monkeypatch.setattr(os,'pread',lambda *a:pytest.fail('startup content'))
            alltime();page.locator('#session_id').fill('session-A');page.locator('#clear').click();page.locator('#relativeSeconds').select_option('86400');assert not creates;assert page.locator('#results').is_hidden();assert page.locator('#inspector').is_hidden()
            monkeypatch.undo();run();assert page.locator('#results').is_visible();assert page.locator('#inspector').is_visible();return
        if case.startswith('CR1'):
            alltime();size=2 if case.endswith('tiny-batches') else 200;page.locator('#pageSize').select_option(str(size))
            if case.endswith('tiny-batches'):page.locator('#textValue').fill('match-')
            run();assert len(rows())==size;assert page.evaluate('viewer.cursor') is not None;assert page.evaluate('viewer.pages.map(p=>p.length)')==[size]
            if case.endswith('tiny-batches'):
                assert any(x['state']=='budget_yield' and x['matches'] for x in advances);assert any(x['state']=='budget_yield' and not x['matches'] for x in advances)
                assert advances[-1]['progress']['records_examined']<100
            seen=[r['message'] for r in rows()];qid=page.evaluate('viewer.queryId');evicted_ref=rows()[0]['ref']
            while page.evaluate('viewer.cursor') is not None:
                page.locator('#more').click();idle();lengths=page.evaluate('viewer.pages.map(p=>p.length)');assert all(n<=size for n in lengths);assert len(lengths)<=service.limits.client_pages;assert len(rows())<=min(1000,service.limits.handles)
                if rows() and rows()[-1]['message'] not in seen:
                    seen.extend(r['message'] for r in page.evaluate('viewer.pages[viewer.pages.length-1]'))
                for r in rows():assert service.detail(qid,r['ref'])['original']['message']==r['message']
            expected=[('match-' if size==2 else 'large-')+str(i) for i in reversed(range(25 if size==2 else 250))];assert seen==expected;assert len(set(seen))==len(seen);assert len(creates)==1
            if size==2:expect(page.locator('#evicted')).to_contain_text('evicted');assert request(server,'/api/searches/'+qid+'/records/'+evicted_ref)[1]=={'error':{'code':'record_evicted_restart'}}
            assert before_hash=={str(p):hashlib.sha256(p.read_bytes()).digest() for p in history.rglob('*') if p.is_file()};return
        if case.startswith('CR2-'):
            page.locator('#timeMode').select_option('absolute')
            if case.endswith('invalid-nonempty'):
                page.locator('#start').evaluate("e=>{e.type='text';e.value='2026-02-30T09:00';e.dispatchEvent(new Event('input',{bubbles:true}));}");run();assert creates==[];expect(page.locator('#error')).to_contain_text('valid UTC start');return
            _,start,end,expected=next(d for d in DATES if case=='CR2-'+d[0]);page.locator('#start').fill(start);page.locator('#end').fill(end);actual_start=page.locator('#start').input_value();actual_end=page.locator('#end').input_value();run();assert ids()==expected
            payload=creates[-1]['time'];canonical=service.registry[page.evaluate('viewer.queryId')].query.canonical()
            for key,value in [('start',actual_start),('end',actual_end)]:
                if not value:assert key not in payload;assert canonical[key] is None;continue
                expected_value=value+(':00' if len(value)==16 else '')+'Z';assert payload[key]==expected_value
                from datetime import datetime,timezone
                expected_instant=datetime.fromisoformat(expected_value.replace('Z','+00:00')).astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z');assert canonical[key]==expected_instant
            return
        if case=='TC09-partial':
            actual=service.reader.advance;count=[0]
            def slow(*a,**k):
                count[0]+=1
                if count[0]==3:entered.set();assert release.wait(10)
                return actual(*a,**k)
            monkeypatch.setattr(service.reader,'advance',slow);alltime();page.locator('#run').click();assert entered.wait(5);expect(page.locator('#status')).to_contain_text('Discovering files');assert 'No matches' not in page.locator('#status').inner_text();assert page.evaluate('viewer.busy');release.set();idle();return
        if case=='TC09-cancel':
            actual=os.pread
            def blocked(*a):entered.set();assert release.wait(10);return actual(*a)
            monkeypatch.setattr(os,'pread',blocked);alltime();page.locator('#run').click();assert entered.wait(5);qid=page.evaluate('viewer.queryId');q=service.registry[qid];page.locator('#cancel').click();expect(page.locator('#status')).to_contain_text('Canceled · incomplete');assert q.snapshot.cancel.is_set();release.set();page.wait_for_function('() => !viewer.busy')
            for _ in range(100):
                if q.snapshot.root_fd is None:break
                page.wait_for_timeout(10)
            assert q.snapshot.root_fd is None;assert page.locator('#more').is_disabled();return
        if case=='TC07-refresh':
            clear();write(history,line('old',timestamp='2026-11-09T09:30:00Z')+line('edge',timestamp='2026-11-09T09:59:59Z'));now=[NOW];calls=[]
            def clock():calls.append(now[0]);return now[0]
            service.clock=clock;page.locator('#pageSize').select_option('1');run();oldid=page.evaluate('viewer.queryId');oldquery=copy.deepcopy(service.registry[oldid].query.canonical());assert [r['message'] for r in rows()]==['edge']
            now[0]+=timedelta(hours=1);write(history,line('new-boundary',timestamp='2026-11-09T10:00:00Z'),'2026-11-09/10-00.jsonl')
            while page.evaluate('viewer.cursor'):page.locator('#more').click();idle()
            assert [r['message'] for r in rows()]==['edge','old'];assert service.registry[oldid].query.canonical()==oldquery;assert len(calls)==1
            page.locator('#refresh').click();idle();assert [r['message'] for r in rows()]==['new-boundary'];newid=page.evaluate('viewer.queryId');assert newid!=oldid;assert service.registry[newid].query.canonical()['start']=='2026-11-09T10:00:00.000000Z';assert len(calls)==2;return
        alltime()
        if case in ('TC06-continuation','TC06-running-draft','TC09-expiry'):page.locator('#pageSize').select_option('2')
        run()
        if case in ('TC09-unreadable','TC09-missing-root'):
            expect(page.locator('#status')).to_contain_text('No readable matches; some data skipped' if case.endswith('unreadable') else 'Log root does not exist');return
        if case in ('TC10-list-inspector','TC10-scalar-inspector','TC11-fidelity'):
            target=page.locator('#rows button').filter(has_text='complete message');target.focus();page.keyboard.press('Enter');expect(page.locator('#copy')).to_be_enabled();assert page.evaluate('viewer.selectedRecord.original')==original;assert page.evaluate('viewer.selectedRecord.source')==expected_source;expect(page.locator('#inspectionStatus')).to_contain_text(str(expected_source['offset']));assert original['message'] in page.locator('#inspectFields').inner_text()
            page.get_by_text('Expand details',exact=True).click();expect(page.locator('#inspectFields pre')).to_contain_text('世界' if isinstance(details,list) or isinstance(details,dict) else '314');page.locator('#jsonDetails summary').click();page.locator('#copy').click();assert json.loads(page.evaluate('navigator.clipboard.readText()'))==original
            assert page.locator('#inspectFields img,#json img,#inspectFields script,#json script').count()==0;assert page.evaluate('window.pwned') is None;assert not outbound;assert page.evaluate('localStorage.length+sessionStorage.length')==0
            buttons=page.locator('#rows button');buttons.first.focus();page.keyboard.press('ArrowDown');expect(buttons.nth(1)).to_be_focused();page.keyboard.press('End');expect(buttons.last).to_be_focused();page.keyboard.press('Home');expect(buttons.first).to_be_focused();page.keyboard.press('ArrowDown');page.keyboard.press('ArrowUp');expect(buttons.first).to_be_focused();return
        page.locator('#rows button').first.click();expect(page.locator('#copy')).to_be_enabled();initial=state()
        if case=='TC06-running-draft':
            actual=service.reader.advance
            def blocked(*a,**k):entered.set();assert release.wait(10);return actual(*a,**k)
            monkeypatch.setattr(service.reader,'advance',blocked);page.locator('#more').click();assert entered.wait(5);page.locator('#textValue').fill('unsent during scan');expect(page.locator('#dirty')).to_contain_text('Unsent');assert page.locator('#refresh').is_disabled();assert state()==initial;assert len(creates)==1;release.set();idle();assert ids()==['r15','r14','r13','r12'];return
        if case=='TC06-draft-actions':
            for field in ('session_id','correlation_id','flow_name','component','component_type','level','event','hook_event'):
                page.locator('#'+field).fill('draft-'+field);assert state()==initial
            for field in ('session_id','correlation_id','flow_name','component','component_type','hook_event'):page.locator('#null-'+field).check();assert state()==initial
            page.locator('#textValue').fill('draft text');page.locator('#includeDetails').check();page.locator('#timeMode').select_option('absolute');page.locator('#start').fill('2026-11-09T09:00');page.locator('#end').fill('2026-11-09T10:00');page.locator('#pageSize').select_option('1');assert state()==initial;assert page.locator('#refresh').is_disabled()
            page.locator('#clear').click();assert state()==initial
            for seconds in ('86400','604800','3600'):page.locator('#relativeSeconds').select_option(seconds);assert state()==initial
            assert len(creates)==1;return
        if case=='TC06-shortcuts':
            for field in ('session_id','correlation_id','flow_name','component','component_type','level','event','hook_event'):page.locator('#'+field).fill('restrict-'+field)
            for field in ('session_id','correlation_id','flow_name','component','component_type','hook_event'):page.locator('#null-'+field).check()
            page.locator('#includeDetails').check();page.locator('#textValue').fill('x');page.locator('#timeMode').select_option('absolute');page.locator('#start').fill('2026-11-09T09:00');page.locator('#end').fill('2026-11-09T10:00');page.locator('#pageSize').select_option('2');draft=page.evaluate('viewer.draft')
            for field,button,value in [('correlation_id','correlationHistory',initial['selected']['fields']['correlation_id']),('session_id','sessionHistory',initial['selected']['fields']['session_id'])]:
                page.locator('#'+button).click();expected={'timeMode':'all','relativeSeconds':3600,'start':'','end':'','fieldValues':{field:[value]},'nullFields':[],'textValue':'','includeDetails':False,'pageSize':200};assert page.evaluate('viewer.draft')==expected;assert state()==initial;page.locator('#back').click();assert page.evaluate('viewer.draft')==draft
            assert len(creates)==1;return
        if case=='TC06-continuation':
            refs=[r['ref'] for r in rows()];expected_ids=['r15','r14'];assert ids()==expected_ids;selected=page.evaluate('viewer.selectedRecord');page.locator('#session_id').fill('unsent');page.locator('.tablewrap').evaluate("e=>{e.style.maxHeight='70px';e.scrollTop=20;}");scroll=page.locator('.tablewrap').evaluate('e=>e.scrollTop');assert scroll>0;cursor=page.evaluate('viewer.cursor');page.locator('#more').focus();page.keyboard.press('Enter');idle();assert ids()==['r15','r14','r13','r12'];assert [r['ref'] for r in rows()[:2]]==refs;assert len(set(r['ref'] for r in rows()))==4;assert page.evaluate('viewer.selectedRecord')==selected;assert page.locator('.tablewrap').evaluate('e=>e.scrollTop')==scroll;assert page.locator('#rows button').first.evaluate('e=>e===document.activeElement');assert len(creates)==1;return
        if case=='TC09-expiry':
            now=[0];service.monotonic=lambda:now[0];q=service.registry[page.evaluate('viewer.queryId')];q.touched=0;ref=rows()[0]['ref'];now[0]=901;page.locator('#more').click();idle();expect(page.locator('#status')).to_contain_text('query_expired_restart');assert q.snapshot.root_fd is None;assert request(server,'/api/searches/'+q.id+'/records/'+ref)[1]=={'error':{'code':'query_expired_restart'}};return
        if case in ('STALE-detail-error','STALE-diagnostic-error'):
            # Delay failure at the real retrieval boundary after its old request
            # starts, then select/search anew; the obsolete failure must vanish.
            actual=service.reader.retrieve_record if case=='STALE-detail-error' else service.reader.retrieve_diagnostic
            first=[True]
            from log_viewer.reader import ReaderError
            def failing(*a,**k):
                if first[0]:first[0]=False;entered.set();assert release.wait(10);raise ReaderError('STALE_FAILURE')
                return actual(*a,**k)
            if case=='STALE-detail-error':
                monkeypatch.setattr(service.reader,'retrieve_record',failing);page.locator('#rows button').nth(1).click();assert entered.wait(5)
                # A new Run creates independent query state while old detail owns
                # its guard; release may race but cannot overwrite the new panel.
                page.locator('#session_id').fill('never_seen');page.locator('#run').click();idle();expected=page.locator('#inspectionStatus').inner_text();release.set();page.wait_for_timeout(100);assert page.locator('#inspectionStatus').inner_text()==expected
            else:
                write(history,b'bad\n');page.locator('#refresh').click();idle();page.locator('#diagnostics summary').click();monkeypatch.setattr(service.reader,'retrieve_diagnostic',failing);page.get_by_role('button',name='Inspect raw preview').click();assert entered.wait(5);page.locator('#session_id').fill('never_seen');page.locator('#run').click();idle();release.set();page.wait_for_timeout(100);assert 'STALE_FAILURE' not in page.locator('#rawDiagnostic').inner_text()
            return
    finally:
        release.set();context.close()
