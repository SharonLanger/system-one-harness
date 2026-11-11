"""Pre-inference diagnostics. Reports contain measurements, never source text."""
import json
from uuid import uuid4

HINT = ('See docs/input-budgets.md. Inspect the named question, option and boundary; '
        'shortening state cannot repair oversized instructions/options. For unavailable '
        'inspection verify the installed runtime, profile and resolved checkpoint.')


def make_report(model, *, request_id=None):
    """Unknown measurements are null; zero is reserved for a measured empty input."""
    return {'schema_version': 1, 'code': None, 'message': None,
            'request_id': request_id or uuid4().hex, 'phase': 'pre_inference',
            'request_inference_started': False, 'model': {'requested_id': model},
            'tokenizer': None, 'limits': None, 'inspection_status': 'unavailable',
            'questions': None, 'violations': [], 'hint': HINT}


class InputBudgetError(ValueError):
    """An input could not be accepted safely. Access returns independent JSON data."""
    code = 'TOKEN_BUDGET_INSPECTION_FAILED'
    status = 'failed'

    def __init__(self, report, message):
        snapshot = dict(report, code=self.code, message=message, inspection_status=self.status)
        self._report_json = json.dumps(snapshot, allow_nan=False, ensure_ascii=True)
        super().__init__(f'{self.code}: {message} (request {snapshot["request_id"]})')

    @property
    def report(self):
        return json.loads(self._report_json)


class InputTokenBudgetExceeded(InputBudgetError):
    code = 'INPUT_TOKEN_BUDGET_EXCEEDED'
    status = 'complete'


class InspectionUnavailable(InputBudgetError):
    code = 'TOKEN_BUDGET_INSPECTION_UNAVAILABLE'
    status = 'unavailable'


class InspectionFailed(InputBudgetError):
    code = 'TOKEN_BUDGET_INSPECTION_FAILED'
    status = 'failed'
