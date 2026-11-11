"""Check a real local tokenizer's strict budget: one valid request or an oversized refusal."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
from _support import entrypoint, workspace


class ObservedRegistry:
    """Count inference calls while retaining inspection on the original loaded agent."""
    def __init__(self, registry):
        self.registry, self.calls = registry, 0

    def get_inspector(self, config):
        return self.registry.get_inspector(config)

    def get_agent(self, config):
        agent, owner = self.registry.get_agent(config), self

        class ObservedAgent:
            def predict(self, *args, **kwargs):
                owner.calls += 1
                return agent.predict(*args, **kwargs)
        return ObservedAgent()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', required=True, help='Allow real tokenizer/model loading')
    parser.add_argument('--model-path', required=True, help='Existing compatible typed-decisions MLX checkpoint; no download')
    parser.add_argument('--case', choices=['valid', 'oversized-state'], default='valid')
    args = parser.parse_args()
    model_path = Path(args.model_path).expanduser().resolve()
    if not model_path.is_dir():
        parser.error('--model-path must be an existing local checkpoint directory')
    from building_blocks.questions.ticket_triage_v2.block import run
    from hooks.gateway import isolate_stdout
    from laya.budget import InputBudgetError, InputTokenBudgetExceeded
    from laya.config import RunConfig
    from laya.registry import AgentRegistry

    def loader(model, **options):
        import laya_mlx
        return laya_mlx.load(str(model_path), local_files_only=True, **options)

    registry = ObservedRegistry(AgentRegistry(loader=loader))
    text = 'I cannot log in to my account.' if args.case == 'valid' else 'account login problem ' * 3000
    with workspace(), isolate_stdout():
        try:
            result = run({'text': text}, config=RunConfig(), registry=registry, strict=True)
            outcome = {'status': 'predicted', 'result': asdict(result),
                       'passed': args.case == 'valid' and registry.calls == 1}
        except InputBudgetError as exc:
            boundaries = {v['boundary'] for v in exc.report.get('violations', [])}
            outcome = {'status': type(exc).__name__, 'report': exc.report,
                       'passed': args.case == 'oversized-state' and isinstance(exc, InputTokenBudgetExceeded)
                                 and {'state', 'full_request'} <= boundaries and registry.calls == 0}
    outcome.update(case=args.case, prediction_calls=registry.calls)
    print(json.dumps(outcome, indent=2, ensure_ascii=False))
    return 0 if outcome['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
