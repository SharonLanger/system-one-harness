"""Reject tracked generated output and machine-local state; inspect names only."""
from pathlib import PurePosixPath
import subprocess
import sys

def prohibited(path):
    p = PurePosixPath(path)
    parts = p.parts
    if '.idea' in parts or '.logs' in parts or p.suffix == '.iml' or p.name == '.DS_Store':
        return True
    if parts[0] in {'logs', 'results', 'backup', 'build', 'dist', 'coverage', 'htmlcov'}:
        return True
    if any(x in parts for x in ('node_modules', '__pycache__', '.pytest_cache', '.venv')):
        return True
    if path.startswith(('docs/local/', 'src/building_blocks/questions/method_selection_v2/research/evidence/')):
        return True
    if parts[0] == 'experiments':
        return ('results' in parts or path.startswith(('experiments/exports/', 'experiments/reports/', 'experiments/inputs/captured_'))
                or ('checks' in parts and 'output' in parts))
    return path.startswith(('tests/.output/', 'typescript-helper/test/.output/', '.coverage'))

def main():
    # --tree is used by pre-push to inspect the outgoing tip rather than the working index.
    command = ['git', 'ls-tree', '-r', '--name-only', '-z', sys.argv[2]] if len(sys.argv) == 3 and sys.argv[1] == '--tree' else ['git', 'ls-files', '-z']
    paths = subprocess.check_output(command).decode().split('\0')
    bad = [p for p in paths if p and prohibited(p)]
    if bad:
        print('Remove these paths from Git tracking (keep local copies):\n' + '\n'.join(bad), file=sys.stderr)
        return 1
    print('Repository hygiene: no prohibited tracked paths.')
    return 0
if __name__ == '__main__':
    raise SystemExit(main())
