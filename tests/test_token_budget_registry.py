import inspect
from types import SimpleNamespace
import pytest
from laya.config import RunConfig
from laya.registry import AgentRegistry
from laya.predict import predict
from test_predict import RAW,REQUEST,Agent


def capability():
    assert 'inspection_provider' in inspect.signature(AgentRegistry).parameters, 'missing registry inspection provider'


def test_lazy_original_agent_and_capability_reuse():
    capability(); loads=[];providers=[];agent=Agent();inspector=SimpleNamespace(inspect=lambda *a,**k:None)
    def loader(*a,**k):loads.append((a,k));return agent
    def provider(a,c):providers.append((a,c));return inspector
    registry=AgentRegistry(loader=loader,inspection_provider=provider)
    assert loads==providers==[]
    config=RunConfig(temperature=0.3)
    assert registry.get_agent(config) is agent
    assert loads==[((config.model,),{'lang_temperatures':{'en':{'temperature':[0.3]*3}}})]
    assert not providers
    assert registry.get_inspector(config) is registry.get_inspector(config) is inspector
    assert providers==[(agent,config)] and len(loads)==1
    config2=RunConfig(model='second')
    registry.get_inspector(config2)
    assert len(providers)==len(loads)==2


def test_failed_capability_initialization_retries_without_marking_verified():
    capability();calls=[];agent=Agent()
    def provider(a,c):
        calls.append(c)
        if len(calls)==1:raise RuntimeError('transient capability error')
        return SimpleNamespace(inspect=lambda *a,**k:None)
    registry=AgentRegistry(loader=lambda *a,**k:agent,inspection_provider=provider)
    with pytest.raises(RuntimeError):registry.get_inspector(RunConfig())
    assert registry.get_agent(RunConfig()) is agent
    assert registry.get_inspector(RunConfig()) is registry.get_inspector(RunConfig())
    assert len(calls)==2


def test_provider_and_registry_ownership():
    capability()
    from tests.support.plumbing_inspection import plumbing_provider as provider
    registry=AgentRegistry(loader=lambda *a:Agent(),inspection_provider=provider)
    with pytest.raises(ValueError,match='registry owns'):
        predict('hello',REQUEST['questions'],registry=registry,inspection_provider=provider)
    assert predict(REQUEST['text'],REQUEST['questions'],loader=lambda *a:Agent(),inspection_provider=provider)==RAW


def test_invalid_capability_never_cached():
    capability();calls=[]
    def provider(*a):calls.append(1);return object()
    registry=AgentRegistry(loader=lambda *a:Agent(),inspection_provider=provider)
    from laya.budget import InspectionUnavailable
    for _ in range(2):
        with pytest.raises(InspectionUnavailable):registry.get_inspector(RunConfig())
    assert len(calls)==2
