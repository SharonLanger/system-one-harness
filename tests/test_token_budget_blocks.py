"""Both block paths preserve shared diagnostics; flows own handling policy."""
from copy import deepcopy
import importlib
import importlib.util
from types import SimpleNamespace
import pytest
from laya.budget import InputBudgetError,InputTokenBudgetExceeded,InspectionUnavailable,make_report
from laya.budget_adapters import inspect_request
from laya.config import RunConfig
from laya.registry import AgentRegistry
from blocks import QuestionBlock
from block_runtime import Block,PreparedInput
from building_blocks.questions.ticket_triage.block import BLOCK_DIR
from token_budget_support import tokenizer
from test_predict import RAW

@pytest.fixture
def instrumentation(monkeypatch):
    events=[];calls=[];tok=tokenizer()
    api=importlib.import_module('laya.predict')
    monkeypatch.setattr(api,'log',lambda level,component,event,**kw:events.append((level,event,kw)))
    class Agent:
        def predict(self,*a,**kw):calls.append((a,kw));return deepcopy(RAW)
    provider=lambda agent,config:SimpleNamespace(inspect=lambda s,q,**kw:inspect_request(tok,s,q,model=config.model,max_len=1024,head_max_len=256,**kw))
    registry=AgentRegistry(loader=lambda *a,**kw:Agent(),inspection_provider=provider)
    return registry,calls,events


def modern(reduced):
    q={'type':'noul','instructions':'Is this relevant?'}
    return Block({'name':'budget-proof','questions':{'q':{'definition':q,'rule_ref':'multiple_methods_default'}}},
        validate_input=lambda v:v,prepare_input=lambda v,qs:PreparedInput(v,{'q':dict(q,instructions='word '*500)}),
        reduce=lambda d,s:reduced.append(d),validate_output=lambda v:v)

@pytest.mark.parametrize('kind',['legacy','modern'])
def test_both_blocks_propagate_identical_error_without_downstream_work(kind,instrumentation,monkeypatch):
    registry,calls,events=instrumentation;reduced=[]
    if kind=='legacy':
        monkeypatch.setattr('blocks.interpret',lambda *a,**k:pytest.fail('interpreted'))
        block=QuestionBlock(BLOCK_DIR);state='hello '*2000
    else:
        monkeypatch.setattr('block_runtime.runtime.enrich_prediction',lambda *a,**k:pytest.fail('enriched'))
        block=modern(reduced);state='hello'
    with pytest.raises(InputTokenBudgetExceeded) as caught:block.run(state,config=RunConfig(),registry=registry,strict=True)
    errors=[e for e in events if e[0]=='ERROR']
    assert len(errors)==1 and errors[0][2]['error'] is caught.value
    assert errors[0][2]['report']==caught.value.report
    assert calls==reduced==[]
    if kind=='modern':
        assert caught.value.report['violations'][0]['question_id']=='q'
        assert caught.value.report['violations'][0]['boundary']=='instructions'


def test_minimal_consumer_receives_full_report_without_retry(instrumentation):
    assert importlib.util.find_spec('tests.support.budget_diagnostics') is not None, 'missing minimal diagnostic consumer'
    from tests.support.budget_diagnostics import run_with_diagnostics
    registry,calls,events=instrumentation
    block=QuestionBlock(BLOCK_DIR)
    result=run_with_diagnostics(block.run,'hello '*2000,config=RunConfig(),registry=registry,strict=True)
    assert result['status']=='input_rejected'
    assert result['report']==next(e[2]['report'] for e in events if e[0]=='ERROR')
    assert not calls
    sentinel=object()
    assert run_with_diagnostics(lambda:sentinel) is sentinel


def test_validator_budget_error_rebound_but_malformed_error_unchanged(monkeypatch):
    api=importlib.import_module('laya.predict');events=[]
    monkeypatch.setattr(api,'log',lambda level,component,event,**kw:events.append((level,kw)))
    monkeypatch.setattr('laya.budget.uuid4',lambda:SimpleNamespace(hex='current-validator-request'))
    stale=InspectionUnavailable(make_report('old',request_id='stale-validator-request'),'changed methods')
    def validate(*a):raise stale
    inspector=SimpleNamespace(validate_request=validate,inspect=lambda *a,**k:pytest.fail('inspected'))
    agent=SimpleNamespace(predict=lambda *a,**k:pytest.fail('predicted'))
    registry=SimpleNamespace(get_agent=lambda c:agent,get_inspector=lambda c:inspector)
    with pytest.raises(InspectionUnavailable) as caught:api.predict_strict('hello',{'q':{'type':'noul','instructions':'Is it?'}},registry=registry)
    assert caught.value.report['request_id']=='current-validator-request'
    assert [d for level,d in events if level=='ERROR'][0]['report']==caught.value.report
