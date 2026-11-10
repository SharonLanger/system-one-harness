"""Lazy strict SDK boundary. See docs/input-budgets.md for failures."""
from copy import deepcopy
import json
import math
from log_utils import log

DEFAULT_MODEL = 'aac6fef/laya-typed-decisions-mlx'

class ResponseError(ValueError):
    """The SDK returned unusable prediction data."""


def real_loader(model, **kwargs):
    import laya_mlx
    return laya_mlx.load(model, **kwargs)


def validate_questions(questions):
    from questions.models import Question
    if not isinstance(questions, dict) or not questions:
        raise ValueError('questions must be a nonempty named mapping')
    for name, question in questions.items():
        if not isinstance(name, str) or not name or not isinstance(question, dict):
            raise ValueError('invalid question name or definition')
        Question.from_dict({**question, 'name': name})


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _probabilities(probs, keys, name):
    if not isinstance(probs, dict) or set(probs) != set(keys):
        raise ResponseError(f'invalid probability keys: {name}')
    if not all(_finite(v) and 0 <= v <= 1 for v in probs.values()):
        raise ResponseError(f'invalid probability: {name}')
    if not math.isclose(sum(probs.values()), 1, abs_tol=0.001):
        raise ResponseError(f'probabilities must sum to one: {name}')


def validate_response(raw, questions):
    if not isinstance(raw, dict) or not isinstance(raw.get('answers'), dict):
        raise ResponseError('response must contain an answers mapping')
    try:
        json.dumps(raw, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise ResponseError('response must be strict JSON') from exc
    for name, question in questions.items():
        answer = raw['answers'].get(name)
        if not isinstance(answer, dict) or answer.get('type') != question['type']:
            raise ResponseError(f'invalid answer type: {name}')
        for field in ('confidence', 'answer_confidence'):
            if field in answer and not (_finite(answer[field]) and 0 <= answer[field] <= 1):
                raise ResponseError(f'invalid {field}: {name}')
        if 'action' in answer:
            action = answer['action']
            if not isinstance(action, dict):
                raise ResponseError(f'invalid action: {name}')
            if 'act_probability' in action and not (_finite(action['act_probability']) and 0 <= action['act_probability'] <= 1):
                raise ResponseError(f'invalid action probability: {name}')
        kind = answer['type']
        if kind == 'choice':
            criteria = question['criteria']
            if not isinstance(answer.get('choice'), str) or answer['choice'] not in criteria:
                raise ResponseError(f'invalid choice: {name}')
            _probabilities(answer.get('probabilities'), criteria, name)
        elif kind == 'noul':
            value = answer.get('noul')
            if not (_finite(value) and 0 <= value <= 1):
                raise ResponseError(f'invalid noul: {name}')
        elif kind == 'score':
            if not _finite(answer.get('score')):
                raise ResponseError(f'invalid score: {name}')
            legend = {str(i): label for i, label in enumerate(question['criteria'])}
            if answer.get('legend') != legend:
                raise ResponseError(f'invalid score legend: {name}')
            _probabilities(answer.get('probabilities'), legend, name)
        else:
            raise ResponseError(f'unsupported prediction type: {name}')
    return raw


def _inspect_or_raise(registry, config, state, questions):
    """Fail closed around inspection only; loader and input errors keep their types."""
    from laya.budget import InputBudgetError, InspectionUnavailable, InspectionFailed, InputTokenBudgetExceeded, make_report
    report = make_report(config.model)
    try:
        factory = getattr(registry, 'get_inspector', None)
        if not callable(factory):
            raise InspectionUnavailable(report, 'Registry has no inspection capability')
        inspector = factory(config)
    except InputBudgetError as exc:
        evidence = dict(exc.report, request_id=report['request_id'])
        evidence['model']['requested_id'] = config.model
        raise type(exc)(evidence, exc.report['message']) from exc
    except TimeoutError:
        raise
    except Exception as exc:
        raise InspectionFailed(report, 'Inspection capability initialization failed') from exc
    # The verified SDK validator preserves existing malformed-input exceptions.
    validator = getattr(inspector, 'validate_request', None)
    if callable(validator):
        try:
            validator(state, questions)
        except InputBudgetError as exc:
            evidence = dict(exc.report, request_id=report['request_id'])
            evidence['model']['requested_id'] = config.model
            raise type(exc)(evidence, exc.report['message']) from exc
        except (ValueError, TypeError, TimeoutError):
            raise
        except Exception as exc:
            raise InspectionFailed(report, 'Verified input validation could not complete') from exc
    try:
        measured = inspector.inspect(state, questions, request_id=report['request_id'])
        if (not isinstance(measured, dict) or measured.get('inspection_status') != 'complete'
                or measured.get('request_inference_started') is not False
                or measured.get('request_id') != report['request_id']
                or measured.get('model', {}).get('requested_id') != config.model
                or not isinstance(measured.get('violations'), list)
                or not isinstance(measured.get('questions'), list)
                or [q.get('question_id') for q in measured['questions']] != list(questions)):
            raise ValueError('Incomplete inspection report')
        measured = json.loads(json.dumps(measured, allow_nan=False))
        if measured['violations']:
            first = measured['violations'][0]
            message = (f"Question {first['question_id']!r}, boundary {first['boundary']}: "
                       f"requires {first['required']} {first['unit']}, limit {first['limit']}")
            if first.get('option_key') is not None:
                message += f" (option {first['option_key']!r})"
            raise InputTokenBudgetExceeded(measured, message)
    except InputBudgetError as exc:
        evidence = dict(exc.report, request_id=report['request_id'])
        evidence['model']['requested_id'] = config.model
        raise type(exc)(evidence, exc.report['message']) from exc
    except TimeoutError:
        raise
    except Exception as exc:
        raise InspectionFailed(report, 'Exact input inspection failed; request was not sent') from exc
    return measured


def _predict(text, questions, *, model=DEFAULT_MODEL, loader=None, real=False, config=None, registry=None, inspection_provider=None, strict=False):
    """Inspect the final private request, then return copied validated raw JSON."""
    from laya.config import RunConfig
    from laya.registry import AgentRegistry
    from laya.budget import InputBudgetError
    validate_questions(questions)
    state_snapshot, question_snapshot = deepcopy(text), deepcopy(questions)
    response_questions = deepcopy(question_snapshot)
    if config is None:
        config = RunConfig(model=model)
    if registry is None:
        registry = AgentRegistry(loader=loader, real=real, inspection_provider=inspection_provider)
    elif loader is not None or real or inspection_provider is not None:
        raise ValueError('registry owns loader, mode and inspection provider')
    log("DEBUG", "laya", "prediction_started", text=state_snapshot, questions=question_snapshot,
        model=config.model, temperature=config.temperature, lang=config.lang)
    try:
        agent = registry.get_agent(config)
        if strict:
            _inspect_or_raise(registry, config, state_snapshot, question_snapshot)
        raw = agent.predict(state_snapshot, question_snapshot, **config.predict_kwargs())
        log("DEBUG", "laya", "response_received", response=raw)
        result = deepcopy(validate_response(raw, response_questions))
    except InputBudgetError as exc:
        try:
            log("ERROR", "laya", "input_budget_rejected", error=exc, code=exc.code, report=exc.report)
        except TimeoutError:
            raise
        except Exception:
            pass  # Best-effort diagnostics cannot replace refusal or permit inference.
        raise
    except Exception as exc:
        log("ERROR", "laya", "prediction_failed", error=exc)
        raise
    log("INFO", "laya", "response_validated")
    return result


def predict(text, questions, *, model=DEFAULT_MODEL, loader=None, real=False, config=None, registry=None, inspection_provider=None):
    """Backward-compatible prediction; no token inspection or fitting claim."""
    return _predict(text, questions, model=model, loader=loader, real=real, config=config,
                    registry=registry, inspection_provider=inspection_provider)


def predict_strict(text, questions, *, model=DEFAULT_MODEL, loader=None, real=False, config=None, registry=None, inspection_provider=None):
    """Inspect the exact payload and refuse before inference when unsafe."""
    return _predict(text, questions, model=model, loader=loader, real=real, config=config,
                    registry=registry, inspection_provider=inspection_provider, strict=True)
