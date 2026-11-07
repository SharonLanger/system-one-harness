"""Reject generated/local paths and SQLite blobs in Git's index, trees and history.

--range BASE HEAD scans HEAD and every introduced commit after the merge base.
--push REMOTE reads pre-push stdin. Existing branches use the supplied remote
SHA; new branches scan commits not reachable from any local remote-tracking ref
for REMOTE, plus the tip. Missing bases and shallow repositories fail closed.
Only synthetic databases belong in tests; no fixture exemptions exist.
"""
import argparse
import json
from pathlib import PurePosixPath
import re
import subprocess
import sys

_SQLITE = b'SQLite format 3\0'
_ZERO = '0' * 40


def prohibited(path):
    p = PurePosixPath(path)
    parts = p.parts
    if not parts:
        return False
    if '.soh' in parts or re.search(r'\.(db|sqlite|sqlite3)(-(journal|wal|shm))?$', p.name, re.I):
        return True
    if '.idea' in parts or p.suffix == '.iml' or p.name == '.DS_Store':
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


def git(*args):
    try:
        return subprocess.check_output(['git', *args], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        raise ValueError('git_inspection_failed') from None


def tree_entries(ref):
    for entry in git('ls-tree', '-r', '-z', ref).split(b'\0'):
        if entry:
            metadata, path = entry.split(b'\t', 1)
            mode, kind, oid = metadata.split()
            if kind not in (b'blob', b'commit'):
                raise ValueError('git_object_type_failed')
            yield path.decode('utf-8', 'surrogateescape'), oid.decode('ascii'), mode


def index_entries():
    for entry in git('ls-files', '--stage', '-z').split(b'\0'):
        if entry:
            metadata, path = entry.split(b'\t', 1)
            mode, oid, stage = metadata.split()
            if stage != b'0':
                raise ValueError('unmerged_index')
            yield path.decode('utf-8', 'surrogateescape'), oid.decode('ascii'), mode


def header(oid):
    # Verify the object before the bounded stream read. Never load an entire
    # database/large blob into Python, and never substitute worktree contents.
    if git('cat-file', '-t', oid).strip() != b'blob':
        raise ValueError('git_object_type_failed')
    size = int(git('cat-file', '-s', oid))
    try:
        with subprocess.Popen(['git', 'cat-file', 'blob', oid], stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL) as process:
            prefix = process.stdout.read(min(16, size))
            if len(prefix) != min(16, size):
                raise ValueError('git_blob_read_failed')
            if size <= 16:
                if process.wait() != 0:
                    raise ValueError('git_blob_read_failed')
            else:
                process.terminate()
                process.wait()
            return prefix
    except OSError:
        raise ValueError('git_blob_read_failed') from None


def full_history():
    if git('rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ValueError('shallow_history')


def revision(ref):
    return git('rev-parse', '--verify', ref + '^{commit}').decode().strip()


def introduced(base, head):
    full_history()
    base, head = revision(base), revision(head)
    common = git('merge-base', base, head).decode().strip()
    commits = git('rev-list', head, '^' + common).decode().splitlines()
    yield from tree_entries(head)
    for commit in commits:
        yield from tree_entries(commit)


def outgoing(remote, lines):
    full_history()
    for line in lines:
        fields = line.split()
        if len(fields) != 4:
            raise ValueError('invalid_push_input')
        local_ref, local_sha, remote_ref, remote_sha = fields
        if local_sha == _ZERO:
            continue
        head = revision(local_sha)
        if remote_sha != _ZERO:
            yield from introduced(remote_sha, head)
        else:
            bases = git('for-each-ref', '--format=%(objectname)', 'refs/remotes/' + remote + '/').decode().splitlines()
            if not bases:
                raise ValueError('missing_remote_base')
            for base in bases:
                revision(base)
            commits = git('rev-list', head, '--not', *bases).decode().splitlines()
            yield from tree_entries(head)
            for commit in commits:
                yield from tree_entries(commit)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--tree', metavar='REF')
    modes.add_argument('--range', nargs=2, metavar=('BASE', 'HEAD'))
    modes.add_argument('--push', metavar='REMOTE')
    args = parser.parse_args(argv)
    try:
        entries = (tree_entries(args.tree) if args.tree else introduced(*args.range) if args.range
                   else outgoing(args.push, sys.stdin) if args.push else index_entries())
        inspected, reported, bad = {}, set(), False
        for path, oid, mode in entries:
            rule = 'prohibited_path' if prohibited(path) else None
            # Gitlinks still obey path rules. Their submodule commits need not
            # be local objects, and their contents are outside this repository.
            if mode != b'160000' and oid not in inspected:
                inspected[oid] = header(oid) == _SQLITE
            if mode != b'160000' and inspected[oid]:
                rule = 'sqlite_header' if rule is None else rule + '+sqlite_header'
            if rule and (path, rule) not in reported:
                print('Repository hygiene: ' + rule + ' ' + json.dumps(path, ensure_ascii=True), file=sys.stderr)
                reported.add((path, rule))
                bad = True
        if bad:
            return 1
    except (ValueError, TypeError, UnicodeError, OSError):
        print('Repository hygiene: inspection_failed (Git objects/history must be readable and complete).', file=sys.stderr)
        return 1
    print('Repository hygiene: no prohibited tracked paths or SQLite blobs.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
