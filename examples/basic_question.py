"""Ask a real Laya model one simple fruit question; no mock fallback."""
import argparse
import json
import sys
from _support import entrypoint, workspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--real', action='store_true', required=True, help='Allow model loading/inference')
    parser.add_argument('--model', default='aac6fef/laya-typed-decisions-mlx')
    args = parser.parse_args()
    from hooks.gateway import isolate_stdout
    from laya.predict import predict
    question = {'fruit': {'type': 'choice', 'instructions': 'Which fruit is mentioned?',
                         'criteria': {'apple': 'apple', 'pear': 'pear'}}}
    with workspace(), isolate_stdout():
        result = predict('I ate an apple.', question, model=args.model, real=True)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    correct = result['answers']['fruit']['choice'] == 'apple'
    print('PASS: real model answered apple' if correct else 'FAIL: real model did not answer apple', file=sys.stderr)
    return 0 if correct else 1


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
