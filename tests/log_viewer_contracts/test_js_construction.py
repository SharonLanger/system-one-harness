"""Run the packaged production JS builder in Node, with independent requests."""
import copy
import json
from pathlib import Path
import subprocess
import pytest
from .case_loader import CATALOG,FIELDS,query_cases

SCRIPT=Path(__file__).parents[2]/'src/log_viewer/static/app.js'

def construction_cases():
    cases=copy.deepcopy(CATALOG['ui_creation_cases'])
    base=copy.deepcopy(cases[0]['draft'])
    for field in FIELDS:
        d=copy.deepcopy(base);d['fieldValues']={field:['custom']}
        r=copy.deepcopy(cases[0]['expected_request']);r['filters']={field:{'values':['custom'],'include_null':False}}
        cases.append({'id':'B-FIELD-'+field,'draft':d,'expected_request':r})
    for c in query_cases():
        if not c['id'].startswith('M-PRESENCE'):continue
        r=copy.deepcopy(c['request']);d=copy.deepcopy(base);d['timeMode']=r['time']['mode'];d['fieldValues']={k:v['values'] for k,v in r.get('filters',{}).items()};d['textValue']=r.get('text',{}).get('value','')
        expected={'query_version':1,**r,'page_size':200,'order':'file_append_desc'}
        cases.append({'id':c['id'].replace('M-','B-'),'draft':d,'expected_request':expected})
    return cases
CASES=construction_cases()

@pytest.fixture(scope='module')
def built():
    code="const fs=require('fs');const {buildRequest}=require(process.argv[1]);const cases=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(cases.map(c=>buildRequest(c.draft))));"
    process=subprocess.run(['node','-e',code,str(SCRIPT)],input=json.dumps(CASES),text=True,capture_output=True,check=True)
    return json.loads(process.stdout)

@pytest.mark.parametrize('index,case',list(enumerate(CASES)),ids=[c['id'] for c in CASES])
def test_js_construction(index,case,built):
    assert built[index]==case['expected_request']
