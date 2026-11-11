"""Shared tracked-source and documented command scenarios with public block fakes."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from building_blocks.typescript import outline
from test_prompt_hook import ROOT, HOOK, hook_case, invoke, assert_noop, expected_context

TRACKED = ROOT / 'tests/fixtures/typescript/basic.ts'


def test_tracked_source_to_hook_context_and_invocation_isolation(tmp_path):
    before = TRACKED.read_bytes()
    tracked = subprocess.run(['git', 'ls-files', '--error-unmatch', str(TRACKED.relative_to(ROOT))],
                              cwd=ROOT, capture_output=True)
    assert tracked.returncode == 0
    codes = ['export function greet(name: string): string {\n  return `Hello ${name}`;\n}',
             'function twice(value: number): number { return value * 2; }']
    data = outline(TRACKED)
    outputs = []
    for index, code in enumerate(codes):
        folder = tmp_path / str(index); folder.mkdir()
        case = hook_case(folder)
        case.update(path=TRACKED, code=code, data={**data, 'callables': [data['callables'][index]]}, partial=True)
        case['args'][1] = str(ROOT)
        case['event'].update(cwd=str(ROOT), prompt=f'Explain `{TRACKED}`: selected callable')
        case['spec'].write_text(json.dumps({'selected_id': data['callables'][index]['id']}))
        spec_before = case['spec'].read_bytes()
        result = invoke(case)
        assert result.returncode == 0
        value = json.loads(result.stdout)
        assert value == {'hookSpecificOutput': {'hookEventName': 'UserPromptSubmit',
                                                'additionalContext': expected_context(case)}}
        outputs.append(value['hookSpecificOutput']['additionalContext'])
        calls = [json.loads(line) for line in case['log'].read_text().splitlines()]
        assert [call['kind'] for call in calls] == ['action', 'selection']
        assert calls[0]['data'] == {'prompt': case['event']['prompt']}
        assert case['spec'].read_bytes() == spec_before and TRACKED.read_bytes() == before
    assert outputs[0] != outputs[1] and 'function greet' in outputs[0] and 'function twice' in outputs[1]


def test_documented_enable_disable_recipe_with_fixed_block_answers(tmp_path):
    text = (ROOT / 'docs/hook-usage.md').read_text()
    examples = re.findall(r'```json\n(.*?)\n```', text, re.DOTALL)
    settings, hook_input, hook_output = map(json.loads, examples[:3])
    assert hook_input['hook_event_name'] == 'UserPromptSubmit'
    assert hook_output['hookSpecificOutput']['hookEventName'] == 'UserPromptSubmit'
    handler = settings['hooks']['UserPromptSubmit'][0]['hooks'][0]
    assert handler['type'] == 'command' and handler['timeout'] > 10
    assert 'remove this UserPromptSubmit entry' in text
    case = hook_case(tmp_path)
    command = (handler['command']
        .replace('/absolute/system-one-harness/.venv/bin/python', sys.executable)
        .replace('/absolute/run-config.json', case['args'][3]))
    env = {**case['env'], 'CLAUDE_PROJECT_DIR': str(case['repo'])}
    before = {p: p.read_bytes() for p in case['repo'].rglob('*') if p.is_file()}
    enabled = subprocess.run(['bash', '-c', command], input=json.dumps(case['event']), text=True,
                              capture_output=True, env=env, timeout=15)
    assert json.loads(enabled.stdout)['hookSpecificOutput']['additionalContext'] == expected_context(case)
    disabled = subprocess.run(['bash', '-c', command.replace(' --real', '')],
        input=json.dumps(case['event']), text=True, capture_output=True, env=env, timeout=15)
    assert_noop(disabled)
    assert len(case['log'].read_text().splitlines()) == 2
    assert {p: p.read_bytes() for p in case['repo'].rglob('*') if p.is_file()} == before
    assert not (case['repo'] / '.claude').exists()
