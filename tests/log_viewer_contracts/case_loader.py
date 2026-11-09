"""Expand independently authored memberships; never import production matching code."""
from datetime import datetime, timedelta, timezone
import copy
import itertools
import json
from pathlib import Path

HERE=Path(__file__).parent
CATALOG=json.loads((HERE/'query_cases.json').read_text())
FIELDS=CATALOG['families'][0]['fields']
NULLABLE=CATALOG['families'][1]['fields']

def generated(id,request,ids=None,error=None,canonical=None,**extra):
    c=dict(id=id,request=request,expected_ids=ids or [],expected_state='complete',expected_diagnostics=[],**extra)
    if error: c['expected_error']=error
    if canonical is not None: c['expected_canonical']=canonical
    return c

def canonical(filters=None,text=None,time=False,page=200):
    c=copy.deepcopy(CATALOG['canonical_all_history_defaults'])
    if time: c.update(start='2026-11-09T09:00:00.000000Z',end='2026-11-09T10:00:00.000000Z')
    c['selectors']=filters or {}; c['text']=text; c['page_size']=page
    return c

def query_cases():
    cases=copy.deepcopy(CATALOG['cases']+CATALOG['invalid_cases'])
    cases.append(generated('Q-END-WITHIN-HOUR',{'time':{'mode':'absolute','start':'2026-11-09T09:00:00Z','end':'2026-11-09T09:45:00Z'}},['r11','r10']))
    order=CATALOG['fixture_order']; m=CATALOG['matrix']
    for bits in itertools.product((0,1),repeat=10):
        request={'time':copy.deepcopy(m['active_time'] if bits[0] else m['inactive_time'])}
        selectors={}; ids=set(order)
        for bit,dim in zip(bits,m['dimensions']):
            if not bit: continue
            ids.intersection_update(m['truth_memberships'][dim])
            if dim in FIELDS: selectors[dim]={'values':[m['active_values'][dim]],'include_null':False}
            if dim=='text': request['text']=copy.deepcopy(m['active_text'])
        if selectors: request['filters']=copy.deepcopy(selectors)
        cases.append(generated('M-PRESENCE-'+''.join(map(str,bits)),request,[i for i in order if i in ids],canonical=canonical(selectors,request.get('text'),bool(bits[0]))))
    for field in FIELDS:
        for name,value in [('omitted','omit'),('null',None),('empty_object',{}),('empty_values',{'values':[]})]:
            request={'time':{'mode':'all'}}
            if value!='omit': request['filters']={field:value}
            cases.append(generated(f'M-UNSET-{field}-{name}',request,order,canonical=canonical()))
    family=CATALOG['families'][1]
    for field in family['fields']:
        for variant in family['variants']:
            selector={'values':[],'include_null':True}
            ids=set(family['null_memberships'][field])
            if variant=='value_or_null':
                selector['values']=[family['values'][field]]; ids.update(family['value_memberships'][field])
            filters={field:selector}
            if variant=='null_only_plus_unrelated_no_match': filters['event']={'values':['never_seen'],'include_null':False}; ids=set()
            cases.append(generated(f'M-NULL-{field}-{variant}',{'time':{'mode':'all'},'filters':filters},[i for i in order if i in ids],canonical=canonical(filters)))
    all_request=next(c['request'] for c in cases if c['id']=='Q034')
    for field in FIELDS+['text']:
        for rotation in range(9):
            request=copy.deepcopy(all_request)
            if field=='text': request['text']['value']='never_seen'
            else: request['filters'][field]['values']=['never_seen']
            members=list(request['filters'].items()); n=rotation%len(members)
            request['filters']=dict(members[n:]+members[:n])
            members=list(request.items()); n=rotation%len(members)
            request=dict(members[n:]+members[:n])
            cases.append(generated(f'M-ZERO-{field}-{rotation}',request,prior_request=next(c['request'] for c in cases if c['id']=='Q011'),prior_expected_ids=next(c['expected_ids'] for c in cases if c['id']=='Q011')))
    for field in FIELDS:
        for count in (100,101):
            request={'time':{'mode':'all'},'filters':{field:{'values':[f'never_seen-{i}' for i in range(count)]}}}
            cases.append(generated(f'M-BOUNDS-{field}-{count}',request,error={'code':'too_many_values','path':f'filters.{field}.values'} if count==101 else None))
    bad_selectors=[('array',[],'invalid_type',''),('number',1,'invalid_type',''),('bool',True,'invalid_type',''),('values_type',{'values':'x'},'invalid_type','.values'),('number_element',{'values':[1]},'invalid_type','.values[0]'),('bool_element',{'values':[True]},'invalid_type','.values[0]'),('null_element',{'values':[None]},'invalid_type','.values[0]'),('blank',{'values':['\t']},'blank_value','.values[0]'),('include_type',{'include_null':1},'invalid_type','.include_null'),('unknown',{'operator':'or'},'unknown_field','.operator')]
    for field in FIELDS:
        for name,value,code,suffix in bad_selectors:
            cases.append(generated(f'VT-TYPE-{field}-{name}',{'filters':{field:value}},error={'code':code,'path':f'filters.{field}{suffix}'}))
        for length in (1024,1025):
            cases.append(generated(f'VT-LIMIT-{field}-{length}',{'time':{'mode':'all'},'filters':{field:{'values':['界'*length]}}},error={'code':'too_long','path':f'filters.{field}.values[0]'} if length==1025 else None))
        if field not in NULLABLE:
            cases.append(generated(f'VT-NULL-{field}',{'filters':{field:{'include_null':True}}},error={'code':'null_not_supported','path':f'filters.{field}.include_null'}))
    for field in ('query_version','page_size','order'):
        cases.append(generated('VT-NULL-'+field,{field:None},error={'code':'invalid_type','path':field}))
    for name,field,values in [('page','page_size',[0,201,1.2,True]),('seconds','seconds',[0,604801,1.2,True])]:
        for i,v in enumerate(values):
            request={field:v} if field=='page_size' else {'time':{'mode':'relative','seconds':v}}
            path=field if field=='page_size' else 'time.seconds'
            cases.append(generated(f'VT-LIMIT-{name}-{i}',request,error={'code':'invalid_type' if type(v) is not int else 'out_of_range','path':path}))
    for value in (1,200): cases.append(generated(f'VT-LIMIT-page-valid-{value}',{'time':{'mode':'all'},'page_size':value},order,canonical=canonical(page=value)))
    for length in (1024,1025):
        cases.append(generated(f'VT-LIMIT-text-{length}',{'text':{'value':'界'*length}},error={'code':'too_long','path':'text.value'} if length==1025 else None))
    extras=[('missing-seconds',{'time':{'mode':'relative'}},'invalid_type','time.seconds'),('time-extra',{'time':{'mode':'relative','seconds':1,'end':'x'}},'unexpected_field','time.end'),('text-extra',{'text':{'operator':'regex'}},'unknown_field','text.operator'),('nonobject',[],'invalid_type','$')]
    for name,request,code,path in extras: cases.append(generated('VT-JSON-'+name,request,error={'code':code,'path':path}))
    for name,start,end in [('midnight','2026-11-08T23:59:59Z','2026-11-09T00:00:00Z'),('year','2026-12-31T23:59:59Z','2027-01-01T00:00:00Z'),('leap','2024-02-29T00:00:00Z','2024-03-01T00:00:00Z'),('offset','2026-11-09T10:00:00+01:00','2026-11-09T11:00:00+01:00'),('fraction','2026-11-09T09:00:00.000001Z','2026-11-09T09:00:00.000002Z')]:
        ids=['r12','r16','r11','r10'] if name=='offset' else []
        cases.append(generated('VT-TIME-'+name,{'time':{'mode':'absolute','start':start,'end':end}},ids))
    for name,t in [('all-seconds',{'mode':'all','seconds':1}),('absolute-seconds',{'mode':'absolute','seconds':1}),('relative-start',{'mode':'relative','seconds':1,'start':'x'})]:
        key='start' if name=='relative-start' else 'seconds'
        cases.append(generated('VT-TIME-'+name,{'time':t},error={'code':'unexpected_field','path':'time.'+key}))
    for stamp in ('2026-11-09T09:00:00.1234567Z','2026-11-09T09:00:00+24:00','2026-11-09T09:00:00+01:99'):
        cases.append(generated('VT-TIME-invalid-'+stamp,{'time':{'mode':'absolute','start':stamp}},error={'code':'invalid_timestamp','path':'time.start'}))
    for id,request,ids in [
        ('VT-TYPE-meaningful-spaces',{'time':{'mode':'all'},'filters':{'session_id':{'values':[' A ']}}},['space']),
        ('P15-null-empty-literal-query',{'time':{'mode':'all'},'filters':{'session_id':{'include_null':True}}},['null']),
        ('P14-microsecond-query',{'time':{'mode':'absolute','start':'2026-11-09T09:00:00.000001Z','end':'2026-11-09T09:00:00.000003Z'}},['empty','space'])
    ]: cases.append(generated(id,request,ids,fixture='schema_edges'))
    for value in (1,604800):
        cases.append(generated(f'VT-LIMIT-seconds-valid-{value}',{'time':{'mode':'relative','seconds':value}},['r12'] if value==1 else CATALOG['cases'][1]['expected_ids']+['r09','r08','r07','r06','r05','r04','r03','r02','r01']))
    for name,boundary in [('day','2026-11-09T00:00:00+00:00'),('month','2026-12-01T00:00:00+00:00'),('year','2027-01-01T00:00:00+00:00'),('leap','2024-03-01T00:00:00+00:00')]:
        at=datetime.fromisoformat(boundary);end=at+timedelta(microseconds=1)
        start_text=at.isoformat(timespec='microseconds').replace('+00:00','Z');end_text=end.isoformat(timespec='microseconds').replace('+00:00','Z')
        stamps=[('before',at-timedelta(microseconds=1)),('at',at),('offset',at.astimezone(timezone(timedelta(hours=2)))),('after',end)]
        expected=canonical();expected.update(start=start_text,end=end_text)
        cases.append(generated('VT-TIME-populated-'+name,{'time':{'mode':'absolute','start':start_text,'end':end_text}},['offset','at'],canonical=expected,boundary_records=[(id,stamp.isoformat(timespec='microseconds')) for id,stamp in stamps]))
    assert len({c['id'] for c in cases})==len(cases)
    assert sum(c['id'].startswith('M-PRESENCE') for c in cases)==1024
    return cases
