"""Scripted external responses shared by flow and process-boundary tests."""
from tests.support.fake_laya import action_answer, method_answer, choice_response

SOURCE = 'export function calculateTotal(value: number) { return value + 1; }\n'
PROMPT = 'Explain `sample.ts`: calculateTotal'
MODEL_RESPONSE_SENTINEL = 'SOH_MODEL_RESPONSE_PRIVATE_MARKER'


def scenario_steps(scenario, source):
    if scenario in {'missing', 'empty', 'malformed'}:
        return []
    if scenario == 'sensitive':
        def marked(step):
            def answer(text,questions):
                result=step(text,questions)
                result['synthetic_private_response']=MODEL_RESPONSE_SENTINEL
                return result
            return answer
        return [marked(action_answer),marked(method_answer)]
    if scenario == 'unsupported':
        return [lambda text, questions: {'answers': choice_response(
            'requested_action', 'modify_code', questions['requested_action']['criteria'])}]
    if scenario == 'invalid':
        return [{'answers': {}}]
    if scenario == 'failure':
        return [RuntimeError('scripted SDK failure')]
    if scenario == 'no_candidates':
        return [action_answer]
    if scenario == 'ambiguous':
        def ambiguous(text, questions):
            return {'answers': {**choice_response('method', 'none_or_unclear', questions['method']['criteria']),
                                'multiple_relevant': {'type': 'noul', 'noul': 0.1, 'answer_confidence': 0.95}}}
        return [action_answer, ambiguous]
    if scenario == 'source_removed':
        def remove_source(text, questions):
            answer = method_answer(text, questions)
            source.unlink()
            return answer
        return [action_answer, remove_source]
    if scenario not in {'success', 'budget', 'noise'}:
        raise ValueError(scenario)
    return [action_answer, method_answer]
