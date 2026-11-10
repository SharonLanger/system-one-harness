"""Outline and extract one synthetic TypeScript function, without a model."""
import argparse
import json
from _support import SOURCE, entrypoint, workspace


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    from building_blocks.typescript import outline, extract_callable
    with workspace() as (project, _, records):
        source = outline(project / 'sample.ts')
        candidate, = source['callables']
        result = extract_callable(project / 'sample.ts', candidate['id'])
        if result['code'] != SOURCE.rstrip('\n') or candidate['location']['line'] != 1:
            raise RuntimeError('Unexpected extracted source or location')
        print(json.dumps({'mode': 'no_model', 'passed': True, 'name': candidate['name'],
                          'location': candidate['location'], 'code': result['code']}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(entrypoint(main))
