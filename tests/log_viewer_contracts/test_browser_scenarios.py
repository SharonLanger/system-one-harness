"""Real packaged page -> real loopback HTTP -> real file reader."""
from dataclasses import replace
import json
import os
import shutil
import threading
import time
from urllib.parse import urlparse
import pytest
from playwright.sync_api import sync_playwright, expect
from .test_http_scenarios import server,request
from .helpers import line,write

@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as p:
        browser=p.chromium.launch(executable_path=os.environ.get('SOH_TEST_CHROMIUM') or shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        yield browser
        browser.close()

CASES=['U01-startup','U02-draft-only','U03-click','U03-double-submit','U03-enter','U04-dirty','U05-stale-response','U06-shortcuts-back','U07-load-more-dirty','U08-empty','U08-no-match','U08-warning','U09-inspect-copy-keyboard','U10-html-unicode','U11-token-reload','U12-disconnected','U13-level-or','U13-null','U14-eviction','U03-all-predicates','U03-invalid-date','U03-blank-id','U09-large-json']

@pytest.mark.parametrize('case',CASES)
def test_browser_scenario(case,browser,server,service,history,monkeypatch,tmp_path):
    context=browser.new_context(permissions=['clipboard-read','clipboard-write']);page=context.new_page();requests=[];outbound=[]
    page.on('request',lambda r:(requests.append((r.method,r.url,r.post_data)),outbound.append(r.url) if not r.url.startswith(server.origin) else None))
    if case=='U01-startup':
        write(history,line('bulk')*10000)
        monkeypatch.setattr(service.reader,'_inventory',lambda *a:pytest.fail('startup inventoried logs'))
    if case=='U08-empty':
        shutil.rmtree(history);history.mkdir()
    if case=='U08-warning':write(history,b'bad\n')
    if case=='U10-html-unicode':write(history,line('<img src="https://outside.invalid/x" onerror="window.pwned=1"> 世界'))
    if case=='U14-eviction':write(history,line('many')*18)
    if case=='U09-large-json':write(history,line('large-json',details={'payload':'x'*(4*1024*1024-1024)}))
    released=threading.Event();entered=threading.Event()
    try:
        page.goto(server.url);expect(page.locator('#status')).to_contain_text('No search run')
        def submits():return [r for r in requests if r[0]=='POST' and r[1].endswith('/api/searches')]
        def run():page.locator('#run').click();page.wait_for_function('() => window.viewer && !window.viewer.busy && !window.viewer.submitting')
        def all_history():page.locator('#timeMode').select_option('all')
        if case=='U01-startup':
            assert service.registry=={};assert submits()==[];assert page.locator('#timeMode').input_value()=='relative';assert page.locator('#level').input_value()=='';assert page.locator('#rows tr').count()==0;return
        if case=='U02-draft-only':
            for field in ('session_id','correlation_id','flow_name','component','component_type','level','event','hook_event'):page.locator('#'+field).fill('x')
            all_history();page.locator('#textValue').fill('selection');page.locator('#clear').click();assert submits()==[];assert service.registry=={};return
        if case=='U05-stale-response':
            actual=service.reader.advance;first=[True]
            def delayed(*args,**kw):
                if first[0]:first[0]=False;entered.set();assert released.wait(10)
                return actual(*args,**kw)
            monkeypatch.setattr(service.reader,'advance',delayed)
            all_history();page.locator('#run').click();assert entered.wait(5);oldid=page.evaluate('viewer.queryId');oldquery=service.registry[oldid]
            page.locator('#session_id').fill('never_seen');run();expect(page.locator('#status')).to_contain_text('No matches')
            released.set();page.wait_for_timeout(200);assert page.locator('#rows tr').count()==0;expect(page.locator('#status')).to_contain_text('No matches');assert len(submits())==2;assert oldid not in service.registry;assert oldquery.snapshot.root_fd is None;return
        if case=='U11-token-reload':
            assert page.url==server.origin+'/';assert page.evaluate('localStorage.length+sessionStorage.length')==0;page.reload();expect(page.locator('#status')).to_contain_text('authorized URL');assert submits()==[];return
        if case=='U12-disconnected':
            # Stop listening; existing loaded page detects real connection failure.
            server.shutdown();server.socket.close();run();expect(page.locator('#status')).to_contain_text('Disconnected');return
        all_history()
        if case in ('U03-click','U03-double-submit','U03-enter','U04-dirty','U06-shortcuts-back','U09-inspect-copy-keyboard'):page.locator('#session_id').fill('session-A')
        if case=='U03-all-predicates':
            values={'session_id':'session-A','correlation_id':'inv-G','flow_name':'first_method_context','component':'block_runtime','component_type':'block','level':'ERROR','event':'failed','hook_event':'UserPromptSubmit'}
            for k,v in values.items():page.locator('#'+k).fill(v)
            page.locator('#textValue').fill('selection');page.locator('#timeMode').select_option('relative')
        if case=='U03-invalid-date':
            page.locator('#timeMode').select_option('absolute');run();assert submits()==[];expect(page.locator('#error')).to_contain_text('at least one');return
        if case=='U03-blank-id':
            page.locator('#session_id').fill('   ');run();assert submits()==[];expect(page.locator('#error')).to_contain_text('visible text');return
        if case in ('U07-load-more-dirty','U14-eviction'):page.locator('#pageSize').select_option('2')
        if case=='U08-no-match':page.locator('#event').fill('never_seen')
        if case=='U13-level-or':page.locator('#level').fill('ERROR\nWARNING')
        if case=='U13-null':page.locator('#null-session_id').check()
        if case=='U03-double-submit':
            page.evaluate("() => { document.querySelector('#filters').requestSubmit(); document.querySelector('#filters').requestSubmit(); }");page.wait_for_function('() => viewer.queryId && !viewer.busy')
        elif case=='U03-enter':
            # Enter on the single-line text input triggers form submission.
            page.locator('#textValue').press('Enter');page.wait_for_function('() => window.viewer.queryId && !window.viewer.busy')
        else:run()
        assert len(submits())==1
        if case.startswith('U03-'):
            assert page.locator('#rows tr').count()==(1 if case=='U03-all-predicates' else 8)
            payload=json.loads(submits()[0][2]);assert payload['filters']['session_id']['values']==['session-A'];return
        if case=='U04-dirty':
            old=page.locator('#rows').inner_text();page.locator('#session_id').fill('session-B');expect(page.locator('#dirty')).to_contain_text('Unsent');assert page.locator('#refresh').is_disabled();assert page.locator('#rows').inner_text()==old;assert len(submits())==1;return
        if case=='U06-shortcuts-back':
            page.locator('#rows button').first.click();expect(page.locator('#copy')).to_be_enabled();page.locator('#correlationHistory').click();assert page.locator('#timeMode').input_value()=='all';assert page.locator('#correlation_id').input_value()=='inv-G';assert page.locator('#session_id').input_value()=='';assert len(submits())==1;page.locator('#back').click();assert page.locator('#session_id').input_value()=='session-A';page.locator('#sessionHistory').click();assert page.locator('#session_id').input_value()=='session-A';assert len(submits())==1;return
        if case=='U07-load-more-dirty':
            qid=page.evaluate('viewer.queryId');page.locator('#session_id').fill('never_seen');page.locator('#more').click();page.wait_for_function('() => !viewer.busy');assert page.locator('#rows tr').count()==4;assert page.evaluate('viewer.queryId')==qid;assert len(submits())==1;return
        if case.startswith('U08'):
            expected='Empty scope' if case.endswith('empty') else 'No matches' if case.endswith('match') else 'with warnings';expect(page.locator('#status')).to_contain_text(expected)
            if case.endswith('warning'):page.locator('#diagnostics summary').click();expect(page.locator('#diagnosticList')).to_contain_text('invalid_json')
            return
        if case=='U09-large-json':
            page.get_by_role('button',name='large-json',exact=True).click();expect(page.locator('#copy')).to_be_enabled();assert page.locator('#inspectFields pre').inner_text()==''
            page.locator('#jsonDetails summary').click();expect(page.locator('#json')).to_contain_text('payload');assert len(page.evaluate('viewer.selectedRecord.original.details.payload'))==4*1024*1024-1024
            page.locator('#clear').click();assert len(submits())==1;return
        if case=='U09-inspect-copy-keyboard':
            page.locator('#rows button').first.focus();page.keyboard.press('Enter');expect(page.locator('#copy')).to_be_enabled();expect(page.locator('#inspectFields')).to_contain_text('session-A');page.locator('#jsonDetails summary').click();expect(page.locator('#json')).to_contain_text('schema_version');page.locator('#copy').click();text=page.evaluate('navigator.clipboard.readText()');assert json.loads(text)['session_id']=='session-A';return
        if case=='U10-html-unicode':
            expect(page.locator('#rows')).to_contain_text('<img');assert page.locator('#rows img').count()==0;assert page.evaluate('window.pwned') is None;page.get_by_role('button',name='<img',exact=False).click();expect(page.locator('#inspectFields')).to_contain_text('世界');assert outbound==[];return
        if case=='U13-level-or':
            rows=page.locator('#rows tr');assert rows.count()==6
            assert all(x in ('ERROR','WARNING') for x in page.locator('#rows .badge').all_text_contents());return
        if case=='U13-null':assert page.locator('#rows tr').count()==3;return
        if case=='U14-eviction':
            for _ in range(6):page.locator('#more').click();page.wait_for_function('() => !viewer.busy')
            assert page.locator('#rows tr').count()==10;expect(page.locator('#evicted')).to_contain_text('evicted');assert page.evaluate('viewer.pages.length')==5;return
    finally:
        released.set();context.close()
