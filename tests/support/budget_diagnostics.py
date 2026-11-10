"""Minimal flow consumer: expose diagnostics without automatic trimming or retries."""
from laya.budget import InputBudgetError


def run_with_diagnostics(block_call, *args, **kwargs):
    """Fitting results pass through; the caller can display a rejected report."""
    try:
        return block_call(*args, **kwargs)
    except InputBudgetError as error:
        return {'status': 'input_rejected', 'report': error.report}
