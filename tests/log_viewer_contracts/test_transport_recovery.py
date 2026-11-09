"""CR-4: actual lost browser requests/responses with a partial display page."""
from dataclasses import replace
import copy
import hashlib
import json
import shutil
import pytest
from playwright.sync_api import expect
from .conftest import NOW
from .helpers import line,write
from .test_browser_scenarios import browser
from .test_http_scenarios import server

CASES=['CR4-before-service','CR4-after-commit','CR4-cancel-held','CR4-supersede-held']

@pytest.mark.parametrize('case',CASES)
def test_transport_recovery(case,browser,server,service,history,monkeypatch):
    shutil.rmtree(history);history.mkdir()
    limits=replace(service.limits,block_bytes=256,advance_bytes=256,advance_seconds=1,client_pages=2,handles=6)
    service.limits=service.reader.limits=limits
    raw=b''.join(line('match-'+str(i),details={'payload':'x'*700})+line('skip',details={'neutral':1})*3 for i in range(7))
    path=write(history,raw);initial_hash=hashlib.sha256(raw).digest();clock_calls=[]
    def clock():clock_calls.append(NOW);return NOW
    service.clock=clock
    real_advance=service.advance;calls=[]
    def observed(id,cursor):
        q=service.registry[id];before=(dict(q.snapshot.progress),q.snapshot.page_rows)
        result=real_advance(id,cursor)
        calls.append({'id':id,'input':cursor,'result':copy.deepcopy(result),'before':before,'after':(dict(q.snapshot.progress),q.snapshot.page_rows),'snapshot':id_of(q.snapshot)})
        return result
    id_of=id
    monkeypatch.setattr(service,'advance',observed)
    context=browser.new_context();page=context.new_page();partials={};delivered=[];requests=[];fault={};held=[];done=set()
    def intercepted(route):
        request=route.request;id=request.url.split('/searches/')[1].split('/')[0];cursor=json.loads(request.post_data)['cursor'];requests.append((id,cursor))
        eligible=id in partials and id not in done
        if case=='CR4-before-service' and eligible:
            done.add(id);q=service.registry[id]
            fault.update(id=id,cursor=cursor,calls=len(calls),progress=dict(q.snapshot.progress),page_rows=q.snapshot.page_rows)
            route.abort('failed');return
        response=route.fetch();data=response.json()
        if eligible and data.get('matches'):
            done.add(id)
            if case=='CR4-after-commit':
                fault.update(id=id,cursor=cursor,response=copy.deepcopy(data),call=copy.deepcopy(calls[-1]))
                route.abort('failed');return
            if case in ('CR4-cancel-held','CR4-supersede-held'):
                held.append((id,route,response))
                request.frame.evaluate('(n) => {window.transportHeld=n;}',len(held))
                return
        if data.get('state')=='budget_yield' and data.get('matches'):partials.setdefault(id,copy.deepcopy(data))
        delivered.append(copy.deepcopy(data));route.fulfill(response=response)
    page.route('**/advance',intercepted)
    def idle():page.wait_for_function('() => viewer.queryId && !viewer.busy && !viewer.submitting')
    def rows():return page.evaluate('() => viewer.pages.flat()')
    def retained(qid):
        assert all(len(p)<=2 for p in page.evaluate('viewer.pages'))
        assert page.evaluate('viewer.pages.length')<=2
        assert len(rows())+page.evaluate('viewer.partialPage?.matches.length || 0')<=6
        for row in rows()+page.evaluate('viewer.partialPage?.matches || []'):
            assert service.detail(qid,row['ref'])['original']['message']==row['message']
    try:
        page.goto(server.url);page.locator('#textValue').fill('match-');page.locator('#pageSize').select_option('2');page.locator('#run').click()
        if case in ('CR4-before-service','CR4-after-commit'):
            idle();qid=page.evaluate('viewer.queryId');q=service.registry[qid];snapshot=q.snapshot;canonical=q.query.canonical();cursor=page.evaluate('viewer.cursor')
            assert fault['id']==qid and cursor==fault['cursor'];assert page.evaluate('viewer.partialPage.queryId')==qid
            assert [r['message'] for r in page.evaluate('viewer.partialPage.matches')]==['match-6'];assert rows()==[]
            expect(page.locator('#status')).to_contain_text('1 matches buffered');expect(page.locator('#more')).to_have_text('Retry current page');assert page.locator('#more').is_enabled();retained(qid)
            if case=='CR4-before-service':
                assert len(calls)==fault['calls'];assert q.cursor==cursor;assert q.snapshot.progress==fault['progress'];assert q.snapshot.page_rows==fault['page_rows']
            else:
                assert calls[-1]['input']==cursor;assert calls[-1]['result']==fault['response'];assert q.last_cursor==cursor
            # A retry must keep the original snapshot, even if a writer appends
            # another in-range record after the request/response is lost.
            with path.open('ab') as out:out.write(line('match-new',timestamp='2026-11-09T09:45:00Z'))
            call_index=len(calls);request_index=len(requests);page.locator('#more').click();idle()
            assert requests[request_index]==(qid,cursor);assert calls[call_index]['input']==cursor
            if case=='CR4-after-commit':
                retry=calls[call_index];assert retry['result']==fault['response'];assert retry['before']==retry['after'];assert retry['after']==fault['call']['after']
            assert q.snapshot is snapshot;assert q.query.canonical()==canonical;assert len(clock_calls)==1
            assert [r['message'] for r in rows()]==['match-6','match-5'];assert page.evaluate('viewer.partialPage') is None;assert page.evaluate('viewer.cursor') is not None
            seen=[r['message'] for r in rows()];retained(qid)
            while page.evaluate('viewer.cursor') is not None:
                page.locator('#more').click();idle();new=page.evaluate('viewer.pages[viewer.pages.length-1] || []')
                if new and new[-1]['message'] not in seen:seen.extend(r['message'] for r in new)
                retained(qid)
            assert seen==['match-'+str(i) for i in reversed(range(7))];assert len(set(seen))==7
            assert any(d['state']=='budget_yield' and not d['matches'] for d in delivered)
            assert all(c['snapshot']==id_of(snapshot) and c['id']==qid for c in calls);assert q.query.canonical()==canonical;assert len(clock_calls)==1
            assert q.snapshot.progress['bytes_scanned']==len(raw);assert hashlib.sha256(path.read_bytes()[:len(raw)]).digest()==initial_hash
            expect(page.locator('#status')).to_contain_text('Complete');return
        page.wait_for_function('() => window.transportHeld===1');oldid=page.evaluate('viewer.queryId');old=service.registry[oldid];oldpartial=page.evaluate('viewer.partialPage');assert len(oldpartial['matches'])==1
        if case=='CR4-cancel-held':
            page.locator('#cancel').click();expect(page.locator('#status')).to_contain_text('Canceled · incomplete');assert page.evaluate('viewer.partialPage') is None;assert page.evaluate('viewer.cursor') is None
            _,route,response=held[0];route.fulfill(response=response)
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(()=>requestAnimationFrame(resolve)))');page.wait_for_function('() => !viewer.busy');assert rows()==[];assert page.evaluate('viewer.partialPage') is None;assert old.snapshot.cancel.is_set();assert old.snapshot.root_fd is None;assert page.locator('#more').is_disabled()
            expect(page.locator('#status')).to_contain_text('Canceled · incomplete')
        else:
            page.locator('#textValue').fill('match');page.locator('#run').click();page.wait_for_function('() => window.transportHeld===2')
            newid=page.evaluate('viewer.queryId');assert newid!=oldid;active=page.evaluate('viewer.partialPage');assert active['queryId']==newid;assert len(active['matches'])==1
            assert active['matches'][0]['ref']!=oldpartial['matches'][0]['ref'];_,route,response=held[0];route.fulfill(response=response)
            # Drain the obsolete callback explicitly while the new query still
            # owns a different partial page and has a real response withheld.
            page.evaluate('() => new Promise(resolve => requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            assert page.evaluate('viewer.partialPage')==active;assert page.evaluate('viewer.busy');assert rows()==[]
            _,route,response=held[1];route.fulfill(response=response);idle();assert page.evaluate('viewer.queryId')==newid;assert [r['message'] for r in rows()]==['match-6','match-5'];retained(newid)
            assert oldid not in service.registry;assert old.snapshot.root_fd is None
        assert path.read_bytes()==raw
    finally:
        context.close()
