"""Synthetic repositories exercise index/tree/history and local hook enforcement."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import pytest

ROOT=Path(__file__).resolve().parents[2]
CHECKER=ROOT/'scripts/check_repository_hygiene.py'
spec=importlib.util.spec_from_file_location('hygiene',CHECKER)
hygiene=importlib.util.module_from_spec(spec);spec.loader.exec_module(hygiene)


def git(repo,*args,input=None,check=True):
    return subprocess.run(['git',*args],cwd=repo,input=input,capture_output=True,check=check)


@pytest.fixture
def repo(tmp_path):
    path=tmp_path.resolve()/'repo';path.mkdir()
    git(path,'init','-b','main');git(path,'config','user.name','Synthetic Test');git(path,'config','user.email','synthetic@example.invalid')
    (path/'safe.txt').write_text('safe');git(path,'add','safe.txt');git(path,'commit','-m','base')
    return path


def check(repo,*args,stdin=None):
    return subprocess.run([sys.executable,str(CHECKER),*args],cwd=repo,input=stdin,capture_output=True,timeout=20)


def staged(repo,name,content=b'',database=False):
    path=repo/name;path.parent.mkdir(parents=True,exist_ok=True)
    if database:
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE synthetic(value TEXT)');db.execute('INSERT INTO synthetic VALUES (?)',('SECRET_SENTINEL',))
    else:path.write_bytes(content)
    git(repo,'add','-f','--',name)
    return path


@pytest.mark.parametrize('name',['.soh/data','nested/.soh/file','x.db','x.sqlite','x.sqlite3']+
    [f'nested/x.{extension}-{suffix}' for extension in ('db','sqlite','sqlite3') for suffix in ('journal','wal','shm')])
def test_ignore_and_force_added_path(repo,name):
    shutil.copyfile(ROOT/'.gitignore',repo/'.gitignore')
    assert git(repo,'check-ignore','--',name,check=False).returncode==0
    staged(repo,name)
    result=check(repo)
    assert result.returncode==1 and b'prohibited_path' in result.stderr


def test_renamed_sqlite_header_staged_not_worktree(repo):
    path=staged(repo,'harmless',database=True)
    path.write_bytes(b'safe worktree')
    result=check(repo)
    assert result.returncode==1 and b'sqlite_header' in result.stderr
    assert b'SECRET_SENTINEL' not in result.stdout+result.stderr


def test_staged_safe_content_wins_over_unstaged_database(repo):
    path=staged(repo,'harmless',b'safe staged text')
    path.unlink()
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE x(a)')
    assert check(repo).returncode==0


def test_tree_and_deleted_history_and_rename(repo):
    base=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    staged(repo,'tests/fixtures/harmless',database=True);git(repo,'commit','-m','synthetic database')
    bad=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    git(repo,'mv','tests/fixtures/harmless','moved');git(repo,'commit','-m','move synthetic')
    git(repo,'rm','moved');git(repo,'commit','-m','remove synthetic')
    assert check(repo,'--tree','HEAD').returncode==0
    assert check(repo,'--tree',bad).returncode==1
    result=check(repo,'--range',base,'HEAD')
    assert result.returncode==1 and b'sqlite_header' in result.stderr
    assert b'SECRET_SENTINEL' not in result.stderr


@pytest.mark.parametrize('name',['space name','שלום','line\nbreak','--leading','$(touch SHOULD_NOT_EXIST);`touch X`'])
def test_safe_escaped_names_and_no_blob_output(repo,name):
    staged(repo,name,b'SQLite format 3\0SECRET_SENTINEL')
    result=check(repo)
    assert result.returncode==1
    assert json.dumps(name,ensure_ascii=True).encode() in result.stderr
    assert b'SECRET_SENTINEL' not in result.stderr+result.stdout
    assert not (repo/'SHOULD_NOT_EXIST').exists() and not (repo/'X').exists()
    assert len(result.stderr.splitlines())==1


@pytest.mark.parametrize('args',[('--tree','missing'),('--range','missing','HEAD')])
def test_git_inspection_errors_fail_closed(repo,args):
    result=check(repo,*args)
    assert result.returncode==1 and b'inspection_failed' in result.stderr
    assert b'no prohibited' not in result.stdout


def test_index_object_failure_is_not_success(repo):
    staged(repo,'missing-blob',b'hello')
    oid=git(repo,'rev-parse',':missing-blob').stdout.decode().strip()
    (repo/'.git/objects'/oid[:2]/oid[2:]).unlink()
    result=check(repo)
    assert result.returncode==1 and b'inspection_failed' in result.stderr


def test_bounded_header_and_deduplicated_blob_reads(repo,monkeypatch):
    # The production reader reads exactly 16 bytes, even for a large blob.
    staged(repo,'large.txt',b'x'*(2*1024*1024))
    monkeypatch.chdir(repo)
    oid=git(repo,'rev-parse',':large.txt').stdout.decode().strip()
    calls=[];real_popen=hygiene.subprocess.Popen
    class Output:
        def __init__(self,stream):self.stream=stream
        def read(self,size):calls.append(size);return self.stream.read(size)
    class Process:
        def __init__(self,*args,**kwargs):self.real=real_popen(*args,**kwargs);self.stdout=Output(self.real.stdout)
        def __enter__(self):return self
        def __exit__(self,*args):return self.real.__exit__(*args)
        def terminate(self):return self.real.terminate()
        def wait(self):return self.real.wait()
    # Patch only the prefix stream, while Git metadata calls retain normal Popen.
    def popen(*args,**kwargs):
        return Process(*args,**kwargs) if args[0][:3]==['git','cat-file','blob'] else real_popen(*args,**kwargs)
    monkeypatch.setattr(hygiene.subprocess,'Popen',popen)
    assert hygiene.header(oid)==b'x'*16 and calls==[16]
    counts=[]
    monkeypatch.setattr(hygiene,'header',lambda value:(counts.append(value) or b'SQLite format 3\0'))
    monkeypatch.setattr(hygiene,'introduced',lambda *a:iter([('one',oid),('two',oid),('one',oid)]))
    assert hygiene.main(['--range','base','head'])==1 and counts==[oid]


def test_no_fixture_exemption_and_no_text_false_positive(repo):
    staged(repo,'source.py',b'HEADER = b"SQLite format 3\\0"\n')
    assert check(repo).returncode==0
    staged(repo,'tests/fixtures/empty.db')
    assert check(repo).returncode==1


def install_hooks(repo):
    (repo/'scripts').mkdir(exist_ok=True)
    shutil.copyfile(CHECKER,repo/'scripts/check_repository_hygiene.py')
    for name in ('pre-commit','pre-push'):
        shutil.copyfile(ROOT/'.githooks'/name,repo/name);(repo/name).chmod(0o755)


def test_pre_commit_and_pre_push_propagate_history_failure(repo):
    install_hooks(repo);base=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    staged(repo,'secret.data',database=True)
    result=subprocess.run([str(repo/'pre-commit')],cwd=repo,capture_output=True,timeout=20)
    assert result.returncode==1
    git(repo,'commit','-m','synthetic');git(repo,'rm','secret.data');git(repo,'commit','-m','delete')
    head=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    stdin=f'refs/heads/main {head} refs/heads/main {base}\n'.encode()
    assert subprocess.run([str(repo/'pre-push'),'origin'],cwd=repo,input=stdin,capture_output=True,timeout=20).returncode==1


def test_new_branch_multi_ref_and_missing_base(repo):
    install_hooks(repo);base=git(repo,'rev-parse','HEAD').stdout.decode().strip();zero='0'*40
    git(repo,'update-ref','refs/remotes/origin/main',base)
    staged(repo,'synthetic.db');git(repo,'commit','-m','synthetic');git(repo,'rm','synthetic.db');git(repo,'commit','-m','delete')
    head=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    line=f'refs/heads/new {head} refs/heads/new {zero}\n'
    assert check(repo,'--push','origin',stdin=line.encode()).returncode==1
    # A safe first ref cannot hide a later bad ref.
    lines=f'refs/heads/safe {base} refs/heads/safe {base}\n'+line
    assert check(repo,'--push','origin',stdin=lines.encode()).returncode==1
    assert check(repo,'--push','missing',stdin=line.encode()).returncode==1
    assert check(repo,'--push','origin',stdin=f'refs/heads/x {head} refs/heads/x {"1"*40}\n'.encode()).returncode==1
    assert check(repo,'--push','origin',stdin=b'invalid\n').returncode==1
    assert check(repo,'--push','origin',stdin=f'refs/heads/deleted {zero} refs/heads/deleted {head}\n'.encode()).returncode==0


def test_shallow_history_fails_explicitly(repo,tmp_path):
    clone=tmp_path/'shallow'
    git(tmp_path,'clone','--depth=1',repo.as_uri(),str(clone))
    assert check(clone,'--range','HEAD','HEAD').returncode==1


def test_runtime_sidecars_are_covered(repo):
    path=repo/'.soh/state.sqlite3';path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE x(a)');db.commit()
        names=[p.relative_to(repo).as_posix() for p in path.parent.iterdir()]
        assert any(n.endswith('-wal') for n in names) and any(n.endswith('-shm') for n in names)
        assert all(hygiene.prohibited(n) for n in names)
    db.close()
    with sqlite3.connect(path,isolation_level=None) as db:
        db.execute('PRAGMA journal_mode=DELETE');db.execute('BEGIN IMMEDIATE');db.execute('INSERT INTO x VALUES (1)')
        assert (path.parent/'state.sqlite3-journal').exists()
        assert hygiene.prohibited('.soh/state.sqlite3-journal');db.rollback()


def test_ci_scan_artifacts_and_collection_gates():
    workflow=(ROOT/'.github/workflows/repository-hygiene.yml').read_text()
    assert 'fetch-depth: 0' in workflow and 'tracked-files:' in workflow
    assert '--tree "$CANDIDATE"' in workflow and '--range "$PR_BASE" "$PR_HEAD"' in workflow
    assert '--range "$PUSH_BASE" "$CANDIDATE"' in workflow
    assert 'continue-on-error' not in workflow and '|| true' not in workflow
    offline=(ROOT/'.github/workflows/offline-tests.yml').read_text()
    assert 'python -m pytest -q' in offline and 'npm ci' in offline and 'node-version:' in offline
    assert 'path: test-results/*.xml' in offline and 'if-no-files-found: error' in offline
    missing=subprocess.run([sys.executable,str(ROOT/'scripts/summarize_tests.py'),'/nonexistent/synthetic.xml'],capture_output=True)
    assert missing.returncode==1


def test_zero_collected_report_fails(tmp_path):
    report=tmp_path/'empty.xml';report.write_text('<testsuites><testsuite tests="0" /></testsuites>')
    result=subprocess.run([sys.executable,str(ROOT/'scripts/summarize_tests.py'),str(report)],capture_output=True,timeout=10)
    assert result.returncode==1


def test_new_branch_clean_range_accepted(repo):
    base=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    git(repo,'update-ref','refs/remotes/origin/main',base)
    staged(repo,'normal.py',b'print("synthetic")\n');git(repo,'commit','-m','normal source')
    head=git(repo,'rev-parse','HEAD').stdout.decode().strip()
    stdin=f'refs/heads/feature {head} refs/heads/feature {"0"*40}\n'.encode()
    assert check(repo,'--push','origin',stdin=stdin).returncode==0
    assert check(repo,'--range',base,'HEAD').returncode==0
