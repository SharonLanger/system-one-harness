from datetime import datetime, timezone
import hashlib
import json
import shutil
import pytest
from log_viewer.query import QueryBuilder, ValidationError
from .case_loader import query_cases, CATALOG, HERE
from .conftest import NOW

MANIFEST=json.loads((HERE/'fixtures/history/manifest.json').read_text())
LOCATIONS={}
for record in MANIFEST['records']:
    lines=(HERE/'fixtures/history'/record['file']).read_bytes().splitlines(keepends=True)
    LOCATIONS[(record['file'],sum(map(len,lines[:record['line']-1])))]=record['id']

def drain(service,request,locations=LOCATIONS):
    created=service.create(request); cursor=created['cursor']; ids=[]; batches=[]
    for _ in range(2000):
        result=service.advance(created['query_id'],cursor)
        # Network retry must be lossless even for zero-match batches.
        assert service.advance(created['query_id'],cursor)==result
        ids.extend(locations[(m['source']['path'],m['source']['offset'])] for m in result['matches'])
        batches.append(result)
        cursor=result['cursor']
        if cursor is None: break
    else: pytest.fail('continuation did not terminate')
    assert len(set(ids))==len(ids)
    service.delete(created['query_id'])
    return ids,result,batches

@pytest.mark.parametrize('case',query_cases(),ids=lambda c:c['id'])
def test_query_scenario(case,service,history):
    locations=LOCATIONS
    if case.get('fixture')=='schema_edges':
        shutil.rmtree(history);shutil.copytree(HERE/'fixtures/schema_edges',history)
        manifest=json.loads((history/'manifest.json').read_text());locations={}
        for r in manifest['records']:
            lines=(history/r['file']).read_bytes().splitlines(keepends=True)
            locations[(r['file'],sum(map(len,lines[:r['line']-1])))]=r['id']
    if case.get('boundary_records'):
        from .test_parser_scenarios import BASE
        shutil.rmtree(history);history.mkdir();locations={}
        for id,stamp in case['boundary_records']:
            parsed=datetime.fromisoformat(stamp).astimezone(timezone.utc);relative=parsed.strftime('%Y-%m-%d/%H-00.jsonl');path=history/relative;path.parent.mkdir(exist_ok=True)
            raw=json.dumps(dict(BASE,message=id,timestamp=stamp),ensure_ascii=False).encode()+b'\n';offset=path.stat().st_size if path.exists() else 0
            with path.open('ab') as out:out.write(raw)
            locations[(relative,offset)]=id
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in history.rglob('*') if p.is_file()}
    if 'expected_error' in case:
        calls=[]
        service.reader.open_snapshot=lambda query:calls.append(query)
        with pytest.raises(ValidationError) as exc: service.create(case['request'])
        assert exc.value.as_dict()==case['expected_error']
        assert calls==[]
        return
    clock=datetime.fromisoformat(case.get("clock",CATALOG["default_clock"]).replace("Z","+00:00"))
    service.clock=lambda:clock
    if case.get("prior_request") and not case["id"].startswith("M-ZERO"):
        prior,_,_=drain(service,case["prior_request"])
        assert prior==case["prior_expected_ids"]
    query=QueryBuilder.build(case['request'],clock)
    if 'expected_canonical' in case: assert query.canonical()==case['expected_canonical']
    ids,result,batches=drain(service,case['request'],locations)
    assert ids==case['expected_ids']
    assert result['state']==case.get('expected_state','complete')
    assert result['diagnostics']==case.get('expected_diagnostics',[])
    assert result['coverage']=='readable_complete'
    for old,new in zip(batches,batches[1:]):
        assert all(new['progress'][k]>=v for k,v in old['progress'].items())
    if case.get('prior_request') and case['id'].startswith('M-ZERO'):
        again,_,_=drain(service,case['prior_request'])
        assert again==case['prior_expected_ids']
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in history.rglob('*') if p.is_file()}
    assert before==after
