"""CLI and installed-wheel tests. No frontend build/runtime dependency."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import urllib.parse
import venv
import pytest
from log_viewer import cli as viewer_cli
from .helpers import line,write

ROOT=Path(__file__).parents[2]

@pytest.fixture(scope='module')
def wheel_install(tmp_path_factory):
    work=tmp_path_factory.mktemp('installed-viewer');dist=work/'dist';dist.mkdir()
    subprocess.run([sys.executable,'-m','pip','wheel','--no-deps','--no-build-isolation','--no-index','-w',str(dist),str(ROOT)],check=True,capture_output=True,text=True)
    target=work/'env';venv.EnvBuilder(with_pip=True).create(target)
    wheel=next(dist.glob('*.whl'))
    subprocess.run([str(target/'bin/python'),'-m','pip','install','--no-index','--no-deps',str(wheel)],check=True,capture_output=True,text=True)
    return work,target

@pytest.mark.parametrize('case',['I01-wheel-outside-checkout','I02-default-text','I02-last','I02-ui-last','I02-no-open-alone','I02-negative-last','I03-interrupt-active','U12-browser-fallback'])
def test_packaging_scenario(case,wheel_install,tmp_path,monkeypatch,capsys):
    work,target=wheel_install;root=tmp_path/'logs';path=write(root,line('first')+line('last'))
    env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')};env.update(SOH_LOG_ROOT=str(root),HTTP_PROXY='http://127.0.0.1:1',HTTPS_PROXY='http://127.0.0.1:1',NO_PROXY='127.0.0.1',ALL_PROXY='http://127.0.0.1:1')
    command=[str(target/'bin/system-one-harness'),'logs']
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    if case.startswith('I02'):
        flags={'I02-default-text':[],'I02-last':['--last','1'],'I02-ui-last':['--ui','--last','5'],'I02-no-open-alone':['--no-open'],'I02-negative-last':['--last','0']}[case]
        result=subprocess.run(command+flags,cwd=work,env=env,capture_output=True,text=True,timeout=10)
        if case in ('I02-default-text','I02-last'):
            assert result.returncode==0;assert 'last' in result.stdout
            assert [json.loads(row)['message'] for row in result.stdout.splitlines()]==(['first','last'] if case=='I02-default-text' else ['last'])
        else:assert result.returncode==2;assert result.stderr
        return
    if case=='U12-browser-fallback':
        import log_utils
        monkeypatch.setattr(log_utils,'LOG_ROOT',root)
        monkeypatch.setattr(viewer_cli.webbrowser,'open',lambda _:False)
        # Keep actual server construction/close; terminate serve loop deterministically.
        monkeypatch.setattr(viewer_cli.ViewerServer,'serve_forever',lambda *a,**k:(_ for _ in ()).throw(KeyboardInterrupt()))
        assert viewer_cli.run()==0;output=capsys.readouterr();assert '#token=' in output.out;assert 'Browser did not open' in output.err
        assert hashlib.sha256(path.read_bytes()).hexdigest()==before;return
    process=subprocess.Popen(command+['--ui','--no-open'],cwd=work,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert select.select([process.stdout],[],[],5)[0],'CLI did not print URL'
        url=process.stdout.readline().strip();parts=urllib.parse.urlsplit(url);assert parts.hostname=='127.0.0.1';token=urllib.parse.parse_qs(parts.fragment)['token'][0]
        def req(path,method='GET',body=None,auth=True):
            c=http.client.HTTPConnection(parts.hostname,parts.port,timeout=5);headers={'Authorization':'Bearer '+token} if auth else {}
            if body is not None:headers['Content-Type']='application/json';body=json.dumps(body)
            c.request(method,path,body,headers);r=c.getresponse();raw=r.read();code=r.status;c.close();return code,raw
        assert req('/',auth=False)[0]==200;assert b'LogViewerController' in req('/app.js',auth=False)[1];assert b'--' in req('/style.css',auth=False)[1]
        code,raw=req('/api/searches','POST',{'time':{'mode':'all'}});assert code==201;c=json.loads(raw)
        if case=='I01-wheel-outside-checkout':
            code,raw=req('/api/searches/'+c['query_id']+'/advance','POST',{'cursor':c['cursor']});assert code==200;r=json.loads(raw);assert [m['message'] for m in r['matches']]==['last','first']
        else:
            # Active query exists at interruption; shutdown closes its snapshot state.
            assert process.poll() is None
        process.send_signal(signal.SIGINT);out,err=process.communicate(timeout=8);assert process.returncode==0;assert token not in err;assert 'Traceback' not in err
    finally:
        if process.poll() is None:process.kill();process.communicate()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
