from dataclasses import replace
import http.client
import json
import os
import threading
import pytest
from log_viewer.http import ViewerServer
from log_viewer.reader import LogReader
from log_viewer.service import LogQueryService, ServiceError
from log_viewer.limits import Limits
from log_viewer.query import compact
from .conftest import NOW
from .helpers import line,write

@pytest.fixture
def server(service):
    server=ViewerServer(service);thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start()
    yield server
    server.shutdown();thread.join();server.server_close()

def request(server,path='/api/config',method='GET',body=None,headers=None,raw=None):
    c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
    h={'Authorization':'Bearer '+server.token}
    if body is not None:h['Content-Type']='application/json';raw=compact(body).encode()
    if headers is not None:h.update(headers)
    c.request(method,path,body=raw,headers=h);r=c.getresponse();data=r.read();status=r.status;rh=dict(r.getheaders());c.close()
    try:data=json.loads(data)
    except (ValueError,UnicodeError):pass
    return status,data,rh

def advance(server,request_body=None):
    status,c,_=request(server,'/api/searches','POST',request_body or {'time':{'mode':'all'}});assert status==201
    pages=[]
    while c['cursor']:
        status,r,_=request(server,f"/api/searches/{c['query_id']}/advance",'POST',{'cursor':c['cursor']});assert status==200
        pages.append(r);c['cursor']=r['cursor']
    return c,pages

CASES=['A01-create-no-io','A02-retry','A03-foreign-cursor','A03-wrong-cursor','A04-expiry','A05-cancel-twice','A05-cancel-complete','A06-response-bounds','A07-diagnostic-flood','A08-route','A08-method','A08-content-type','A08-invalid-json','A08-nested-duplicate','A08-top-duplicate','A08-nan','A08-infinity','A08-overflow','A08-body-at-limit','A08-body-over-limit','A09-host','A09-origin','A09-no-token','A09-wrong-token','A09-preflight','A10-traversal','A10-absolute','A11-changed-handle','A11-eviction','A11-advance-overlap','A11-cancel-during-read','A06-normalization-overlay','A11-close-during-detail']

@pytest.mark.parametrize('case',CASES)
def test_http_scenario(case,server,service,history,monkeypatch):
    if case=='A01-create-no-io':
        def forbidden(*args,**kwargs):pytest.fail('startup/create performed inventory')
        monkeypatch.setattr(os,'scandir',forbidden)
        assert request(server)[0]==200
        status,c,_=request(server,'/api/searches','POST',{'time':{'mode':'all'}});assert status==201;assert c['state']=='created';assert c['query']['start'] is None
        return
    if case.startswith('A08'):
        path='/api/searches';method='POST';body=None;raw=None;headers={};code=400
        if case.endswith('route'):path='/api/unknown';method='GET';code=404
        elif case.endswith('method'):path='/api/searches/x/advance';method='GET';code=405
        elif case.endswith('content-type'):raw=b'{}';headers={'Content-Type':'text/plain'};code=415
        elif case.endswith('invalid-json'):raw=b'{'
        elif case.endswith('nested-duplicate'):raw=b'{"time":{"mode":"all","mode":"all"}}'
        elif case.endswith('top-duplicate'):raw=b'{"time":null,"time":null}'
        elif case.endswith('nan'):raw=b'{"x":NaN}'
        elif case.endswith('infinity'):raw=b'{"x":Infinity}'
        elif case.endswith('overflow'):raw=b'{"x":1e999}'
        elif case.endswith('body-at-limit'):raw=b'{}'+b' '*65534;code=201
        elif case.endswith('body-over-limit'):raw=b'{}'+b' '*65535;code=413
        headers.setdefault('Content-Type','application/json')
        status,result,rh=request(server,path,method,body,headers,raw)
        assert status==code;assert rh['Cache-Control']=='no-store'
        if code not in (201,):assert 'error' in result
        return
    if case.startswith('A09'):
        headers={};method='GET';code=403
        if case.endswith('host'):headers={'Host':'outside.invalid'}
        elif case.endswith('origin'):headers={'Origin':'https://outside.invalid'}
        elif case.endswith('no-token'):headers={'Authorization':''};code=401
        elif case.endswith('wrong-token'):headers={'Authorization':'Bearer wrong'};code=401
        else:method='OPTIONS';headers={'Origin':'https://outside.invalid'}
        status,result,rh=request(server,method=method,headers=headers);assert status==code;assert 'Access-Control-Allow-Origin' not in rh;return
    if case=='A04-expiry':
        now=[0];service.monotonic=lambda:now[0]
        status,c,_=request(server,'/api/searches','POST',{});assert status==201;now[0]=901
        assert request(server,f"/api/searches/{c['query_id']}/advance",'POST',{'cursor':c['cursor']})[0]==410
        assert service.registry=={};return
    if case=='A06-response-bounds':
        write(history,line('世界'*8000,session_id='界'*1024)*220)
        c,pages=advance(server);assert all(len(compact(p).encode())<=service.limits.response_bytes for p in pages)
        matches=[m for p in pages for m in p['matches']];assert len(matches)==232
        m=next(m for m in matches if m['message_truncated']);assert m['message_truncated'];status,d,_=request(server,f"/api/searches/{c['query_id']}/records/{m['ref']}");assert status==200;assert d['original']['message']=='世界'*8000
        assert all(len(p['matches'])<=200 for p in pages);return
    if case=='A07-diagnostic-flood':
        write(history,b'bad\n'*500+line('good'));c,pages=advance(server);r=pages[-1];assert r['diagnostic_counts']['invalid_json']==500;assert len(r['diagnostics'])==50;assert r['coverage']=='partial'
        ref=r['diagnostics'][0]['ref'];status,d,_=request(server,f"/api/searches/{c['query_id']}/diagnostics/{ref}");assert status==200;assert not d['validated_record'];assert d['preview']=='bad';return
    if case=='A06-normalization-overlay':
        import shutil
        shutil.rmtree(history);history.mkdir()
        from .test_parser_scenarios import BASE
        original=dict(BASE,details=['世界',{'nested':True}],unknown={'keep':[1,None]});original.pop('session_id')
        write(history,json.dumps(original,ensure_ascii=False).encode()+b'\n')
        c,pages=advance(server);m=pages[-1]['matches'][0]
        status,d,_=request(server,f"/api/searches/{c['query_id']}/records/{m['ref']}")
        assert status==200;assert d['original']==original;assert d['fields']=={'session_id':None};assert {**d['original'],**d['fields']}['details']==['世界',{'nested':True}];return
    status,c,_=request(server,'/api/searches','POST',{'time':{'mode':'all'},'page_size':1});assert status==201
    id=c['query_id'];path=f'/api/searches/{id}';cursor=c['cursor']
    if case in ('A11-advance-overlap','A11-cancel-during-read'):
        entered=threading.Event();release=threading.Event();actual=os.pread
        def blocked(*args):entered.set();assert release.wait(5);return actual(*args)
        monkeypatch.setattr(os,'pread',blocked)
        result=[];thread=threading.Thread(target=lambda:result.append(request(server,path+'/advance','POST',{'cursor':cursor})));thread.start();assert entered.wait(5)
        try:
            if case.endswith('overlap'):assert request(server,path+'/advance','POST',{'cursor':cursor})[0]==409
            else:assert request(server,path+'/cancel','POST',{})[1]['state']=='canceled'
        finally:release.set();thread.join(5)
        assert not thread.is_alive()
        if case.endswith('read'):assert result[0][1]['state']=='canceled';assert service.registry[id].snapshot.root_fd is None
        return
    if case=='A03-foreign-cursor':
        other=request(server,'/api/searches','POST',{})[1];assert request(server,path+'/advance','POST',{'cursor':other['cursor']})[0]==409;return
    if case=='A03-wrong-cursor':assert request(server,path+'/advance','POST',{'cursor':'future'})[0]==409;return
    if case=='A05-cancel-twice':
        assert request(server,path+'/cancel','POST',{})[1]['state']=='canceled';assert request(server,path+'/cancel','POST',{})[1]['state']=='canceled';return
    if case=='A05-cancel-complete':
        c,pages=advance(server);assert request(server,f"/api/searches/{c['query_id']}/cancel",'POST',{})[1]['state']=='complete';return
    status,r,_=request(server,path+'/advance','POST',{'cursor':cursor});assert status==200
    if case=='A02-retry':assert request(server,path+'/advance','POST',{'cursor':cursor})[1]==r;return
    if case.startswith('A10'):
        ref='../../../../etc/passwd' if case.endswith('traversal') else '/etc/passwd';status,data,_=request(server,path+'/records/'+ref);assert status in (404,405,410);return
    ref=r['matches'][0]['ref']
    if case=='A11-close-during-detail':
        entered=threading.Event();release=threading.Event();actual=os.pread
        def blocked(*args):entered.set();assert release.wait(5);return actual(*args)
        monkeypatch.setattr(os,'pread',blocked);result=[]
        q=service.registry[id];worker=threading.Thread(target=lambda:result.append(request(server,path+'/records/'+ref)));worker.start();assert entered.wait(5)
        try:assert request(server,path,'DELETE')[0]==200
        finally:release.set();worker.join(5)
        assert not worker.is_alive();assert q.snapshot.root_fd is None;return
    if case=='A11-changed-handle':
        source=r['matches'][0]['source'];p=history/source['path'];raw=p.read_bytes();p.write_bytes(raw.replace(b'TRACE',b'XXXXX'))
        assert request(server,path+'/records/'+ref)[0]==409
    if case=='A11-eviction':
        service.limits=replace(service.limits,handles=1)
        assert request(server,path+'/advance','POST',{'cursor':r['cursor']})[0]==200
        assert request(server,path+'/records/'+ref)[0]==410
