"""Byte fixtures and extraction expectations independent of parser implementation."""
from datetime import datetime,timezone
import copy
import json
import pytest
from log_viewer.parser import LogRecordParser, ParsedRecord, RecordDiagnostic
from log_viewer.query import QueryBuilder
from .case_loader import HERE, NULLABLE
from .conftest import NOW

BASE=json.loads((HERE/'fixtures/history/2026-11-06/22-00.jsonl').read_text().splitlines()[1])

def variants():
    cases=[]
    def add(id,data=BASE,code=None,field=None,warnings=(),expected=None):
        raw=data if isinstance(data,bytes) else json.dumps(data,ensure_ascii=False).encode()
        cases.append((id,raw,code,field,warnings,copy.deepcopy(expected if expected is not None else data)))
    add('P01-all-fields')
    d=copy.deepcopy(BASE);d.update({k:None for k in NULLABLE});add('P02-explicit-nulls',d)
    for k in NULLABLE:
        d=copy.deepcopy(BASE);d.pop(k);add('P03-missing-'+k,d,warnings=[('missing_context_field',k)])
        for name,v in [('string','custom'),('number',1),('list',[]),('object',{}),('bool',True)]:
            d=copy.deepcopy(BASE);d[k]=v;add(f'P04-{k}-{name}',d,code=None if name=='string' else 'invalid_field',field=None if name=='string' else k)
    for name,v in [('omitted','OMIT'),('null',None),('list',['世界']),('string','x'),('number',7),('bool',False)]:
        d=copy.deepcopy(BASE)
        if name=='omitted':d.pop('details')
        else:d['details']=v
        add('P05-details-'+name,d,warnings=[('missing_details',None)] if name=='omitted' else [('non_object_details',None)])
    d=copy.deepcopy(BASE);d['details']={'session_id':'forged','component':'forged','timestamp':'forged'};add('P06-context-collision',d)
    d=copy.deepcopy(BASE);d['future']={'nested':[1,None,'世界']};add('P07-unknown-fields',d)
    d=copy.deepcopy(BASE);d.update(level='FUTURE',event='future',component_type='future');add('P08-open-vocabulary',d)
    for name,v in [('future',2),('missing','OMIT'),('string','1'),('bool',True)]:
        d=copy.deepcopy(BASE)
        if name=='missing':d.pop('schema_version')
        else:d['schema_version']=v
        add('P09-schema-'+name,d,code='unsupported_schema')
    for key in ('timestamp','level','event','pid','message'):
        for name,v in [('missing','OMIT'),('null',None),('wrong',[]),('bool',True),('blank','')]:
            d=copy.deepcopy(BASE)
            if name=='missing':d.pop(key)
            else:d[key]=v
            good=key=='message' and name=='blank'
            code=None if good else ('timezone_required' if key=='timestamp' and name=='blank' else 'invalid_type' if key=='timestamp' else 'invalid_field')
            add(f'P10-{key}-{name}',d,code=code,field=None if good else key)
    for name,raw,code in [('utf16','{"schema_version":1}'.encode('utf-16'),'invalid_json'),('json',b'{','invalid_json'),('utf8',b'{"x":"\xff"}','invalid_json'),('duplicate',b'{"schema_version":1,"schema_version":1}','duplicate_key'),('nested-duplicate',b'{"details":{"x":1,"x":2}}','duplicate_key'),('nan',b'{"x":NaN}','non_finite_number'),('infinity',b'{"x":Infinity}','non_finite_number'),('overflow',b'{"x":1e999}','non_finite_number')]: add('P11-'+name,raw,code=code,field='$')
    d=copy.deepcopy(BASE);d['message']='<img src="https://outside.invalid/x" onerror="window.pwned=1"> 世界\n"hi"';d['details']={'z':['ß','世界'],'a':'x\ny'};add('P12-html-unicode',d)
    d=copy.deepcopy(BASE);d['details']={'z':[2,1],'a':'世界'};add('P13-details-canonical',d)
    for stamp in ('2026-11-06T23:59:59.123456+01:00','2026-11-06T22:59:59.000001Z'):
        d=copy.deepcopy(BASE);d['timestamp']=stamp;add('P14-'+stamp,d)
    for name,value in [('empty',''),('null',None),('literal','null')]:
        d=copy.deepcopy(BASE);d['session_id']=value;add('P15-'+name,d)
    return cases

@pytest.mark.parametrize('id,raw,code,field,warnings,expected',variants(),ids=[x[0] for x in variants()])
def test_parser_scenario(id,raw,code,field,warnings,expected):
    result=LogRecordParser().parse(raw)
    if code:
        assert isinstance(result,RecordDiagnostic)
        assert result.code==code
        if field is not None: assert result.field==field
        return
    assert isinstance(result,ParsedRecord)
    assert result.timestamp==datetime.fromisoformat(expected['timestamp'].replace('Z','+00:00')).astimezone(timezone.utc)
    assert result.timestamp.tzinfo==timezone.utc
    assert result.timestamp.microsecond==datetime.fromisoformat(expected['timestamp'].replace('Z','+00:00')).microsecond
    assert result.original==expected
    assert result.fields['details']==expected.get('details',{})
    for key in ('level','event','message','pid','schema_version','timestamp')+tuple(NULLABLE): assert result.fields[key]==expected.get(key)
    assert [(d.code,d.field) for d in result.diagnostics]==list(warnings)
    if id.startswith('P06'): assert result.fields['session_id']=='session-A'
    if id.startswith('P13'):
        assert QueryBuilder.build({'time':{'mode':'all'},'text':{'value':'"a":"世界","z":[2,1]','scope':'message_and_details'}},NOW).matches(result)
        assert not QueryBuilder.build({'time':{'mode':'all'},'text':{'value':expected['message']+'{','scope':'message_and_details'}},NOW).matches(result)
