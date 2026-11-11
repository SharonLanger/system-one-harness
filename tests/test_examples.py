"""User examples: real subprocess output, isolation and honest live-call boundaries."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIVE = ['basic_question', 'building_block', 'hook_real', 'token_budget']
OFFLINE = ['typescript_extract', 'flow_sanity', 'hook_sanity']

# Block the external SDK, even if installed on a developer's machine. Live-script
# outcome tests explicitly supply controlled loader/capability collaborators.
RUNNER = '''
import importlib, runpy, sys
from pathlib import Path
sys.modules['laya_mlx'] = None
root, script, scenario, *args = sys.argv[1:]
sys.path.insert(0, str(Path(root) / 'examples'))
if scenario != 'none':
    from tests.support.fake_laya import ScriptedLaya, action_answer, method_answer, choice_response
    if scenario in ('apple', 'pear'):
        steps = [lambda text, questions: {'answers': choice_response('fruit', scenario, questions['fruit']['criteria'])}]
    elif scenario == 'block':
        steps = [action_answer]
    elif scenario == 'rejected':
        def reject(text, questions):
            return {'answers': {'requested_action': {'type': 'choice', 'choice': 'explain',
              'probabilities': {k: 0.25 for k in questions['requested_action']['criteria']}}}}
        steps = [reject]
    elif scenario == 'hook':
        steps = [action_answer, method_answer]
    elif scenario == 'noop':
        steps = [lambda text, questions: {'answers': choice_response('requested_action', 'modify_code', questions['requested_action']['criteria'])}]
    elif scenario == 'failure':
        steps = [RuntimeError('controlled model failure')]
    else:
        raise ValueError(scenario)
    fake = ScriptedLaya(*steps)
    importlib.import_module('laya.predict').real_loader = fake.load
    importlib.import_module('laya.registry').real_loader = fake.load
sys.argv = [script, *args]
runpy.run_path(str(Path(root) / 'examples' / (script + '.py')), run_name='__main__')
'''


def invoke(tmp_path, script, *args, scenario='none', runner=RUNNER):
    temp = tmp_path / 'temporary'; temp.mkdir(exist_ok=True)
    log_root = tmp_path / 'normal-logs'
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join([str(ROOT / 'src'), str(ROOT), str(ROOT / 'tests')]),
           'TMPDIR': str(temp), 'SOH_LOG_ROOT': str(log_root)}
    result = subprocess.run([sys.executable, '-c', runner, str(ROOT), script, scenario, *args],
                            cwd=tmp_path, env=env, text=True, capture_output=True, timeout=45)
    assert not list(temp.iterdir()), 'Example left temporary project or session data behind'
    assert not log_root.exists(), 'Example polluted normal SOH logs'
    return result


@pytest.mark.parametrize('script', LIVE + OFFLINE)
def test_help_without_sdk_or_source_work(tmp_path, script):
    result = invoke(tmp_path, script, '--help')
    assert result.returncode == 0 and 'usage:' in result.stdout and result.stderr == ''


@pytest.mark.parametrize('script', LIVE)
def test_live_scripts_require_explicit_opt_in(tmp_path, script):
    result = invoke(tmp_path, script)
    assert result.returncode == 2 and '--real' in result.stderr and result.stdout == ''


@pytest.mark.parametrize('script', OFFLINE)
def test_offline_examples_exercise_real_components_without_sdk(tmp_path, script):
    result = invoke(tmp_path, script)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    source = 'export function calculateTotal(value: number) { return value + 1; }'
    if script == 'typescript_extract':
        assert output['passed'] and output['code'] == source
        assert output['location'] == {'line': 1, 'column': 1}
    elif script == 'flow_sanity':
        assert output['passed'] and output['mode'] == 'scripted_model'
        assert output['prediction_calls'] == 2 and source in output['context']
    else:
        assert result.stdout.count('\n') == 1
        assert output['hookSpecificOutput']['hookEventName'] == 'UserPromptSubmit'
        assert source in output['hookSpecificOutput']['additionalContext']
        assert 'scripted model' in result.stderr and 'PASS:' in result.stderr


@pytest.mark.parametrize('script,scenario,code', [
    ('basic_question', 'apple', 0), ('basic_question', 'pear', 1),
    ('building_block', 'block', 0), ('building_block', 'rejected', 1),
    ('hook_real', 'hook', 0), ('hook_real', 'noop', 0), ('hook_real', 'failure', 1),
    ('basic_question', 'failure', 1), ('building_block', 'failure', 1),
])
def test_live_script_reports_actual_controlled_outcome(tmp_path, script, scenario, code):
    result = invoke(tmp_path, script, '--real', scenario=scenario)
    assert result.returncode == code, result.stderr
    if scenario in ('apple', 'pear'):
        assert json.loads(result.stdout)['answers']['fruit']['choice'] == scenario
    elif scenario == 'block':
        assert json.loads(result.stdout)['data']['action'] == 'explain'
    elif scenario == 'rejected':
        assert json.loads(result.stdout)['data']['action'] == 'undecided'
        assert 'FAIL:' in result.stderr
    elif scenario == 'hook':
        assert 'calculateTotal' in json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        assert 'Completed: context' in result.stderr
    elif scenario == 'noop':
        assert result.stdout == '' and 'Completed: noop' in result.stderr
    else:
        assert result.stdout == '' and ('error' in result.stderr or 'FAIL:' in result.stderr)


def test_local_token_budget_requires_existing_checkpoint(tmp_path):
    result = invoke(tmp_path, 'token_budget', '--real', '--model-path', str(tmp_path / 'missing'))
    assert result.returncode == 2 and 'existing local checkpoint' in result.stderr


BUDGET_RUNNER = '''
import runpy, sys
from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
root, script, scenario, *args = sys.argv[1:]
sys.path.insert(0, str(Path(root) / 'examples'))
from token_budget_support import tokenizer
from laya.budget_adapters import inspect_request
import laya.budget_profiles as profiles
from tests.test_predict import RAW
def load(model, **kwargs):
    assert Path(model).is_dir() and kwargs.pop('local_files_only') is True
    def predict(*a, **kw):
        if scenario == 'oversized-state':
            raise AssertionError('Oversized input reached inference')
        return {'answers': {**deepcopy(RAW['answers']),
                            'churn_risk': {'type': 'noul', 'noul': 0.1}}}
    return SimpleNamespace(predict=predict)
sys.modules['laya_mlx'] = SimpleNamespace(load=load)
profiles.verified_provider = lambda agent,config: SimpleNamespace(inspect=lambda state,questions,**kw:
    inspect_request(tokenizer(),state,questions,model=config.model,max_len=1024,head_max_len=256,**kw))
sys.argv = [script, *args]
runpy.run_path(str(Path(root) / 'examples' / (script + '.py')), run_name='__main__')
'''


@pytest.mark.parametrize('case,calls,status', [('valid', 1, 'predicted'), ('oversized-state', 0, 'InputTokenBudgetExceeded')])
def test_budget_example_measures_refusal_and_counts_calls(tmp_path, case, calls, status):
    model = tmp_path / 'checkpoint'; model.mkdir()
    result = invoke(tmp_path, 'token_budget', '--real', '--model-path', str(model), '--case', case,
                    scenario=case, runner=BUDGET_RUNNER)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['passed'] and output['prediction_calls'] == calls and output['status'] == status
    if calls == 0:
        assert output['report']['request_inference_started'] is False
        assert {'state', 'full_request'} <= {v['boundary'] for v in output['report']['violations']}
