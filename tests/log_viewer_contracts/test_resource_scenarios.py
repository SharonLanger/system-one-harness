"""Deterministic resource bounds, separate from non-gating measurements."""
from dataclasses import replace
import json
import tracemalloc
import pytest
from log_viewer.query import compact
from .helpers import line,write
from .test_http_scenarios import server,request,advance

@pytest.mark.parametrize('case',['R01-metadata-bytes','R01-entry-cap','R02-large-sparse','R02-large-zero','R03-max-line-detail','R03-over-line-preview'])
def test_resource_scenario(case,server,service,history):
    if case in ('R01-metadata-bytes','R01-entry-cap'):
        service.reader.limits=replace(service.reader.limits,inventory_bytes=100 if case.endswith('bytes') else 32*1024*1024,inventory_entries=1 if case.endswith('cap') else 100000)
        c,pages=advance(server);assert pages[-1]['state']=='failed';assert pages[-1]['error']['code']=='inventory_limit_narrow_range';assert pages[-1]['coverage']=='partial';return
    if case.startswith('R02'):
        p=write(history,line('rare')+line('unmatched')*20000)
        service.reader.limits=replace(service.reader.limits,advance_bytes=4096,block_bytes=4096)
        c,pages=advance(server,{'time':{'mode':'all'},'text':{'value':'rare' if case.endswith('sparse') else 'NO_MATCH'}})
        matches=[m for p in pages for m in p['matches']]
        assert [m['message'] for m in matches]==(['rare'] if case.endswith('sparse') else [])
        assert len(pages)>100;assert any(not p['matches'] and p['cursor'] for p in pages)
        assert all(b['progress']['bytes_scanned']-a['progress']['bytes_scanned']<=4096 for a,b in zip(pages,pages[1:]));return
    limit=service.limits.line_bytes
    baseline=line('large',details={'payload':''})
    raw=line('large',details={'payload':'x'*(limit-len(baseline)+1)})
    assert len(raw)-1==limit
    if case.endswith('over-line-preview'):raw=raw[:-1]+b' '+b'\n'
    p=write(history,line('before')+raw+line('after'))
    c,pages=advance(server);matches=[m for p in pages for m in p['matches']]
    if case.endswith('max-line-detail'):
        m=next(m for m in matches if m['message']=='large');status,data,_=request(server,f"/api/searches/{c['query_id']}/records/{m['ref']}");assert status==200;assert len(data['original']['details']['payload'])==limit-len(baseline)+1;assert len(compact(data).encode())<=service.limits.detail_bytes
    else:
        assert not any(m['message']=='large' for m in matches);diagnostic=next(d for d in pages[-1]['diagnostics'] if d['code']=='oversized_line')
        status,data,_=request(server,f"/api/searches/{c['query_id']}/diagnostics/{diagnostic['ref']}");assert status==200;assert data['truncated'];assert len(data['preview'].encode())<=service.limits.diagnostic_preview_bytes
