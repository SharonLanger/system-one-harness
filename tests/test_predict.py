import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest
from laya.predict import predict, DEFAULT_MODEL, ResponseError

FIXTURES = Path(__file__).parent / 'fixtures/laya'
REQUEST = json.loads((FIXTURES / 'choice-request.json').read_text())
RAW = json.loads((FIXTURES / 'choice-raw.json').read_text())

class Agent:
    def __init__(self, raw=RAW):
        self.raw, self.calls = raw, []
    def predict(self, *args, **kwargs):
        self.calls.append((copy.deepcopy(args), kwargs))
        return copy.deepcopy(self.raw)


def test_exact_default_contract_and_json():
    agent = Agent()
    loads = []
    def loader(*args, **kwargs):
        loads.append((args, kwargs))
        return agent
    result = predict(REQUEST['text'], REQUEST['questions'], loader=loader)
    assert loads == [((DEFAULT_MODEL,), {})]
    assert agent.calls == [((REQUEST['text'], REQUEST['questions']), {})]
    assert result == RAW
    assert result['answers']['department']['choice'] == 'billing'
    assert json.loads(json.dumps(result, allow_nan=False)) == RAW


def test_explicit_fixture_model_and_isolation():
    agent = Agent()
    first = predict(REQUEST['text'], REQUEST['questions'], model=REQUEST['model'], loader=lambda model: agent)
    first['answers']['department']['choice'] = 'sales'
    assert predict(REQUEST['text'], REQUEST['questions'], loader=lambda model: agent) == RAW
    assert RAW['answers']['department']['choice'] == 'billing'


@pytest.mark.parametrize('raw', [None, [], {}, {'answers':[]}, {'answers':{}}, {'answers':{'department':{}}}, {'answers':{'department':{'type':'noul'}}}])
def test_invalid_response(raw):
    with pytest.raises(ResponseError):
        predict('x', REQUEST['questions'], loader=lambda model: Agent(raw))


@pytest.mark.parametrize('field,value', [('choice','other'), ('probabilities',{}), ('probabilities',{'billing':float('nan')})])
def test_invalid_choice(field,value):
    raw = copy.deepcopy(RAW)
    raw['answers']['department'][field] = value
    with pytest.raises(ResponseError):
        predict('x', REQUEST['questions'], loader=lambda model: Agent(raw))


def test_exceptions_are_explicit():
    def fail(*args, **kwargs):
        raise RuntimeError('planned failure')
    with pytest.raises(RuntimeError, match='planned failure'):
        predict('x', REQUEST['questions'], loader=fail)
    agent = Agent()
    agent.predict = fail
    with pytest.raises(RuntimeError, match='planned failure'):
        predict('x', REQUEST['questions'], loader=lambda model: agent)


@pytest.mark.parametrize('questions',[{}, [], {'':{}}, {'q':{'type':'unknown'}}])
def test_invalid_questions(questions):
    with pytest.raises(ValueError):
        predict('x', questions, loader=lambda model: pytest.fail('must not load'))


def test_mock_mode_requires_explicit_loader():
    with pytest.raises(RuntimeError, match='mock mode requires'):
        predict('x', REQUEST['questions'])
    assert 'laya_mlx' not in sys.modules


def test_mock_prediction_subprocess_without_sdk():
    result = subprocess.run([sys.executable, '-m', 'tests.support.mock_basic_question'], env={**__import__('os').environ,'PYTHONPATH':'src'}, capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['answers']['fruit']['choice'] == 'apple'
    assert result.stderr == ''
