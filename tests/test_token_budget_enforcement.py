"""Shared boundary refuses before the first request inference, with full evidence."""
from copy import deepcopy
from types import SimpleNamespace
import importlib
import json
import pytest
from laya.budget import InputTokenBudgetExceeded, InspectionUnavailable, InspectionFailed, make_report
from laya.budget_adapters import inspect_request
from laya.config import RunConfig
from laya.registry import AgentRegistry
from token_budget_support import tokenizer
from test_predict import REQUEST, RAW
from tests.support.plumbing_inspection import plumbing_provider
api=importlib.import_module('laya.predict')

@pytest.fixture
def calls(monkeypatch):
    events=[]; forwards=[]
    monkeypatch.setattr(api,'log',lambda level,component,event,**details:events.append((level,event,details)))
    return events,forwards

def boundary(forwards,provider,action=None):
    class Agent:
        def predict(self,state,questions,**kwargs):
            forwards.append((deepcopy(state),deepcopy(questions),kwargs))
            if action: action(state,questions)
            return deepcopy(RAW)
    return AgentRegistry(loader=lambda *a,**k:Agent(),inspection_provider=provider)

@pytest.fixture
def mock_provider():
    tok=tokenizer()
    return lambda agent,config:SimpleNamespace(inspect=lambda state,questions,**kw:inspect_request(tok,state,questions,model=config.model,max_len=1024,head_max_len=256,**kw))

@pytest.mark.parametrize('kind',['option','instruction','state','multi'])
def test_all_losses_refuse_without_any_forward(kind,calls,mock_provider):
    events,forwards=calls;questions=deepcopy(REQUEST['questions']);state='hello'
    first=next(iter(questions))
    if kind=='option':questions[first]['criteria']={'a':'Description '*80,'b':'Short.'}
    elif kind=='instruction':questions[first]['instructions']='word '*500
    elif kind=='state':state='hello '*2000
    else:
        questions['bad']={'type':'noul','instructions':'word '*500}
    with pytest.raises(InputTokenBudgetExceeded) as caught:
        api.predict_strict(state,questions,registry=boundary(forwards,mock_provider))
    report=caught.value.report
    assert forwards==[] and report['request_inference_started'] is False
    assert [q['question_id'] for q in report['questions']]==list(questions)
    errors=[e for e in events if e[0]=='ERROR']
    assert len(errors)==1 and errors[0][2]['report']==report
    assert report['request_id'] and report['violations'] and 'docs/input-budgets.md' in report['hint']
    assert json.loads(json.dumps(report,allow_nan=False))==report

@pytest.mark.parametrize('provider,error',[ (None,InspectionUnavailable), (lambda *a:(_ for _ in ()).throw(RuntimeError('private failure')),InspectionFailed), (lambda *a:SimpleNamespace(inspect=lambda *a,**k:None),InspectionFailed)])
def test_missing_or_broken_capability_never_predicts(provider,error,calls):
    events,forwards=calls
    with pytest.raises(error) as caught:api.predict_strict('hello',REQUEST['questions'],registry=boundary(forwards,provider))
    assert forwards==[]
    assert len([e for e in events if e[0]=='ERROR'])==1
    assert 'private failure' not in json.dumps(caught.value.report)

@pytest.mark.parametrize('where',['provider','inspect','log'])
def test_deadline_is_not_wrapped_or_bypassed(where,calls,monkeypatch):
    events,forwards=calls; deadline=TimeoutError('deadline')
    def fail(*a,**k):raise deadline
    provider=fail if where=='provider' else lambda *a:SimpleNamespace(inspect=fail)
    if where=='log':monkeypatch.setattr(api,'log',fail)
    with pytest.raises(TimeoutError) as caught:api.predict_strict('hello',REQUEST['questions'],registry=boundary(forwards,provider))
    assert caught.value is deadline and forwards==[]


def test_logging_failure_keeps_typed_rejection(calls,monkeypatch,mock_provider):
    _,forwards=calls
    def broken(level,*a,**kw):
        if level=='ERROR':raise OSError('disk')
    monkeypatch.setattr(api,'log',broken)
    with pytest.raises(InputTokenBudgetExceeded):api.predict_strict('hello '*2000,REQUEST['questions'],registry=boundary(forwards,mock_provider))
    assert not forwards

@pytest.mark.parametrize('state',['hello',{'content':'hello'},[{'role':'user','content':'hello'}]])
def test_snapshot_survives_loader_and_agent_mutation(state,calls):
    _,forwards=calls;questions=deepcopy(REQUEST['questions']);original=deepcopy((state,questions));inspected=[]
    def provider(agent,config):
        base=plumbing_provider(agent,config)
        def inspect(s,q,**kw):inspected.append(deepcopy((s,q)));return base.inspect(s,q,**kw)
        return SimpleNamespace(inspect=inspect)
    registry=boundary(forwards,provider,lambda s,q:q.clear())
    original_loader=registry._loader
    def loader(*a,**kw):
        questions.clear()
        if isinstance(state,dict):state.clear()
        if isinstance(state,list):state.clear()
        return original_loader(*a,**kw)
    registry._loader=loader
    assert api.predict_strict(state,questions,registry=registry)==RAW
    assert inspected==[original] and forwards[0][:2]==original


def test_loader_error_keeps_identity(calls):
    _,forwards=calls;error=RuntimeError('loader')
    def loader(*a,**kw):raise error
    with pytest.raises(RuntimeError) as caught:api.predict_strict('hello',REQUEST['questions'],loader=loader,inspection_provider=plumbing_provider)
    assert caught.value is error and not forwards


def test_existing_question_validation_precedes_load(calls):
    _,forwards=calls
    with pytest.raises(ValueError):api.predict_strict('hello',{},loader=lambda *a:pytest.fail('loaded'))
    assert not forwards


def test_provider_error_uses_current_request_correlation(calls,monkeypatch):
    events,forwards=calls
    source=InspectionUnavailable(make_report('fixture',request_id='old-request'),'unsupported')
    monkeypatch.setattr('laya.budget.uuid4',lambda:SimpleNamespace(hex='current-request'))
    def provider(*a):raise source
    with pytest.raises(InspectionUnavailable) as caught:api.predict_strict('hello',REQUEST['questions'],registry=boundary(forwards,provider))
    assert caught.value.report['request_id']=='current-request'
    assert events[-1][2]['report']==caught.value.report and not forwards


def test_verified_validator_preserves_malformed_input(calls):
    _,forwards=calls;error=ValueError('SDK question malformed')
    def validate(*a):raise error
    provider=lambda *a:SimpleNamespace(validate_request=validate,inspect=lambda *a,**k:pytest.fail('inspected'))
    with pytest.raises(ValueError) as caught:api.predict_strict('hello',REQUEST['questions'],registry=boundary(forwards,provider))
    assert caught.value is error and not forwards
