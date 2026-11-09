"""Round-one review regressions at decoder, execution and lifecycle boundaries."""
from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import os
import threading
import pytest
from log_viewer.query import QueryBuilder
from log_viewer.reader import LogReader
from log_viewer.service import LogQueryService
from log_viewer.limits import Limits
from .conftest import NOW
from .helpers import line,write,collect
from .test_http_scenarios import server,request,advance
from .test_parser_scenarios import BASE

DECODE=[('invalid',b'{','invalid_json'),('duplicate',b'{"time":null,"time":null}','duplicate_key'),('nested',b'{"time":{"mode":"all","mode":"all"}}','duplicate_key'),('nan',b'{"page_size":NaN}','non_finite_number'),('infinity',b'{"page_size":Infinity}','non_finite_number'),('overflow',b'{"page_size":1e999}','non_finite_number'),('integer',b'{"page_size":'+b'9'*5000+b'}','invalid_json')]

@pytest.mark.parametrize('id,raw,code',DECODE,ids=[c[0] for c in DECODE])
def test_decoder_boundary(id,raw,code,server,service,history,monkeypatch,capsys):
    before={str(p):hashlib.sha256(p.read_bytes()).digest() for p in history.rglob('*') if p.is_file()}
    def forbidden(*args,**kwargs):pytest.fail('decoder invoked builder or reader')
    monkeypatch.setattr(QueryBuilder,'build',forbidden);monkeypatch.setattr(service.reader,'open_snapshot',forbidden)
    status,data,_=request(server,'/api/searches','POST',headers={'Content-Type':'application/json'},raw=raw)
    assert status==400;assert data=={'error':{'code':code,'path':'$'}};assert service.registry=={}
    assert before=={str(p):hashlib.sha256(p.read_bytes()).digest() for p in history.rglob('*') if p.is_file()}
    captured=capsys.readouterr();assert raw.decode() not in captured.err;assert server.token not in captured.err

@pytest.mark.parametrize('length',[65536,65537],ids=['TC05-utf8-at','TC05-utf8-over'])
def test_utf8_body_boundary(length,server,service,monkeypatch):
    # Thousands of multibyte characters in legal JSON trailing whitespace are not
    # possible, so use a known valid text value and ASCII padding independently.
    raw=json.dumps({'time':{'mode':'all'},'text':{'value':'界'*1024}},ensure_ascii=False,separators=(',',':')).encode()
    raw+=b' '*(length-len(raw));assert len(raw)==length;assert len(raw.decode())<length
    if length>65536:monkeypatch.setattr(QueryBuilder,'build',lambda *a,**k:pytest.fail('oversized body reached builder'))
    status,data,_=request(server,'/api/searches','POST',headers={'Content-Type':'application/json'},raw=raw)
    assert status==(201 if length==65536 else 413)
    if length==65537:assert data=={'error':{'code':'request_too_large'}};assert service.registry=={}

@pytest.mark.parametrize('kind',['omitted','null','list','string','number','bool'])
def test_details_execution(kind,tmp_path):
    values={'null':None,'list':['世界',3],'string':'needle世界','number':314,'bool':True}
    doc=dict(BASE,message='neutral',timestamp='2026-11-09T09:00:00Z')
    if kind=='omitted':doc.pop('details')
    else:doc['details']=values[kind]
    raw=json.dumps(doc,ensure_ascii=False,separators=(',',':')).encode()+b'\n';root=tmp_path/'logs';write(root,line('before',details={'other':'none'})+raw+line('after',details={'other':'none'}))
    needle={'omitted':'{}','null':'null','list':'["世界",3]','string':'"needle世界"','number':'314','bool':'true'}[kind]
    reader=LogReader(root);q=QueryBuilder.build({'time':{'mode':'all'},'text':{'value':needle,'scope':'message_and_details'}},NOW);s=reader.open_snapshot(q)
    try:
        rows,result,_=collect(reader,s);assert [r.fields['message'] for r in rows]==['neutral'];assert rows[0].original==doc
        assert reader.retrieve_record(s,rows[0].source).original==doc
        assert result['diagnostic_counts']=={'missing_details' if kind=='omitted' else 'non_object_details':1}
        assert result['state']=='complete';assert result['coverage']=='readable_complete'
    finally:reader.close(s)

BAD=[('schema',dict(BASE,schema_version=2),'unsupported_schema'),('schema-string',dict(BASE,schema_version='1'),'unsupported_schema'),('context',dict(BASE,session_id=[]),'invalid_field'),('pid',dict(BASE,pid=True),'invalid_field'),('message',dict(BASE,message=1),'invalid_field')]
@pytest.mark.parametrize('id,bad,code',BAD,ids=[x[0] for x in BAD])
def test_rejected_siblings(id,bad,code,tmp_path):
    root=tmp_path/'logs';first=line('good-before');raw=json.dumps(bad,separators=(',',':')).encode();path=write(root,first+raw+b'\n'+line('good-after'));before=path.read_bytes();reader=LogReader(root);s=reader.open_snapshot(QueryBuilder.build({'time':{'mode':'all'}},NOW))
    try:
        rows,r,_=collect(reader,s);assert [v.fields['message'] for v in rows]==['good-after','good-before'];assert r['state']=='complete' and r['coverage']=='partial'
        assert r['diagnostic_counts']=={code:1};d=r['diagnostics'][0];assert (d['code'],d['path'],d['offset'])==(code,'2026-11-09/09-00.jsonl',len(first))
        assert reader.retrieve_diagnostic(s,s.diagnostic_sources[d['ref']])['preview']==raw.decode();assert path.read_bytes()==before
    finally:reader.close(s)

@pytest.mark.parametrize('case',['removed','replaced','truncated','malformed','pending','concatenated','new-hour'])
def test_source_schedules(case,tmp_path):
    root=tmp_path/'logs';a=line('one');bad=b'{bad}' if case=='malformed' else line('tail').rstrip(b'\n') if case=='pending' else b'{"partial":'+line('unrecoverable').rstrip(b'\n')
    raw=a+bad+(b'' if case=='pending' else b'\n'+line('two'))
    p=write(root,raw if case in ('malformed','pending','concatenated') else line('one'))
    safe=write(root,line('safe'),'2026-11-09/08-00.jsonl');safe_hash=hashlib.sha256(safe.read_bytes()).digest();reader=LogReader(root);q=QueryBuilder.build({'time':{'mode':'all'}},NOW);s=reader.open_snapshot(q)
    try:
        if case in ('removed','replaced','truncated','new-hour'):
            while not s.inventory_done:reader.advance(s,budget=replace(reader.limits,advance_seconds=0))
            if case=='removed':p.unlink()
            elif case=='replaced':other=p.with_suffix('.tmp');other.write_bytes(line('evil'));other.replace(p)
            elif case=='truncated':p.write_bytes(b'')
            else:write(root,line('new-hour'),'2026-11-09/10-00.jsonl')
        rows,r,_=collect(reader,s)
        if case=='new-hour':
            assert [x.fields['message'] for x in rows]==['one','safe'];fresh=reader.open_snapshot(q)
            try:assert [x.fields['message'] for x in collect(reader,fresh)[0]]==['new-hour','one','safe']
            finally:reader.close(fresh)
        else:
            expected=['safe'] if case in ('removed','replaced','truncated') else ['one','safe'] if case=='pending' else ['two','one','safe']
            assert [x.fields['message'] for x in rows]==expected;code='source_unavailable' if case=='removed' else 'source_changed' if case in ('replaced','truncated') else 'pending_tail' if case=='pending' else 'invalid_json'
            assert r['state']=='complete' and r['coverage']=='partial';assert r['diagnostic_counts']=={code:1};d=r['diagnostics'][0]
            assert (d['code'],d['path'],d['offset'])==(code,'2026-11-09/09-00.jsonl',0 if case in ('removed','replaced','truncated') else len(a))
            if case not in ('removed','replaced','truncated'):
                preview=reader.retrieve_diagnostic(s,s.diagnostic_sources[d['ref']]);assert preview['preview']==bad.decode();assert not preview['truncated'];assert not preview['validated_record']
                if case=='pending':
                    with p.open('ab') as out:out.write(b'\n')
                    assert reader.advance(s)['records']==[]
        assert hashlib.sha256(safe.read_bytes()).digest()==safe_hash
    finally:reader.close(s)

@pytest.mark.parametrize('case',['old-cursor','expired-record','sentinel-raw','sentinel-encoded','inventory-cancel','diagnostic-stderr'])
def test_lifecycle_transport(case,server,service,history,tmp_path,monkeypatch,capsys):
    if case=='diagnostic-stderr':
        marker='SYNTHETIC_SECRET_<img>';write(history,(marker+'\n').encode()*100);c,pages=advance(server);assert pages[-1]['diagnostic_counts']=={'invalid_json':100};assert len(pages[-1]['diagnostics'])==50
        captured=capsys.readouterr();assert marker not in captured.err;assert server.token not in captured.err;return
    now=[0];service.monotonic=lambda:now[0];c=request(server,'/api/searches','POST',{'time':{'mode':'all'},'page_size':1})[1];id=c['query_id'];path='/api/searches/'+id
    first=request(server,path+'/advance','POST',{'cursor':c['cursor']})[1]
    if case=='old-cursor':
        second=request(server,path+'/advance','POST',{'cursor':first['cursor']})[1];progress=dict(service.registry[id].snapshot.progress)
        status,data,_=request(server,path+'/advance','POST',{'cursor':c['cursor']});assert status==409;assert data=={'error':{'code':'invalid_cursor'}};assert progress==service.registry[id].snapshot.progress;return
    if case=='expired-record':
        ref=first['matches'][0]['ref'];now[0]=901
        for suffix in ('/records/'+ref,'/advance'):
            status,data,_=request(server,path+suffix,'POST' if suffix=='/advance' else 'GET',{'cursor':first['cursor']} if suffix=='/advance' else None);assert status==410;assert data=={'error':{'code':'query_expired_restart'}}
        assert service.registry=={};return
    if case.startswith('sentinel'):
        sentinel=tmp_path/'OUTSIDE_SENTINEL';marker='NEVER_SERVE_SENTINEL_91';sentinel.write_text(marker)
        from urllib.parse import quote
        ref='../../'+str(sentinel) if case.endswith('raw') else quote(str(sentinel),safe='')
        status,data,_=request(server,path+'/records/'+ref);assert status==404;assert data=={'error':{'code':'route_not_found'}};assert marker not in json.dumps(data);assert sentinel.read_text()==marker;return
    if case=='inventory-cancel':
        service.delete(id);entered=threading.Event();release=threading.Event();actual=os.scandir
        def blocked(*args,**kwargs):
            iterator=actual(*args,**kwargs);entered.set();assert release.wait(5);return iterator
        monkeypatch.setattr(os,'scandir',blocked);c=request(server,'/api/searches','POST',{'time':{'mode':'all'}})[1];id=c['query_id'];q=service.registry[id];result=[]
        worker=threading.Thread(target=lambda:result.append(request(server,'/api/searches/'+id+'/advance','POST',{'cursor':c['cursor']})));worker.start();assert entered.wait(5)
        try:assert request(server,'/api/searches/'+id+'/cancel','POST',{})[1]['state']=='canceled'
        finally:release.set();worker.join(5)
        assert result[0][1]['state']=='canceled';assert q.snapshot.root_fd is None
