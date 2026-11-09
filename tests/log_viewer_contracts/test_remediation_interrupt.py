"""Installed process SIGINT during an actual open-file advance."""
import hashlib
import http.client
import json
import os
import select
import signal
import subprocess
import threading
from .helpers import write,line
from .test_packaging_scenarios import wheel_install

# Instrument only OS-read timing and cancel notification. Query/parser/filter,
# HTTP, installed resources and CLI lifecycle remain production implementations.
BOOTSTRAP=r'''
import os,sys,stat
from log_viewer.reader import LogReader
actual_open=LogReader.open_snapshot
actual_read=os.pread
def opened(self,query):
    snapshot=actual_open(self,query)
    actual_set=snapshot.cancel.set
    def flagged():
        actual_set()
        print('CANCEL_SET',file=sys.stderr,flush=True)
    snapshot.cancel.set=flagged
    return snapshot
LogReader.open_snapshot=opened
def held(fd,*args):
    assert stat.S_ISREG(os.fstat(fd).st_mode)
    print('ACTIVE_READ',file=sys.stderr,flush=True)
    if sys.stdin.buffer.read(1)!=b'x':raise RuntimeError('test release missing')
    return actual_read(fd,*args)
os.pread=held
from log_viewer.cli import run
raise SystemExit(run(no_open=True))
'''

def test_active_read_interrupt(wheel_install,tmp_path):
    work,target=wheel_install;root=tmp_path/'logs';p=write(root,line('unchanged')*100);before=hashlib.sha256(p.read_bytes()).digest();env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')};env['SOH_LOG_ROOT']=str(root)
    process=subprocess.Popen([str(target/'bin/python'),'-c',BOOTSTRAP],cwd=work,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    from urllib.parse import urlsplit,parse_qs
    responses=[]
    try:
        assert select.select([process.stdout],[],[],5)[0];url=process.stdout.readline().decode().strip();parts=urlsplit(url);token=parse_qs(parts.fragment)['token'][0]
        def req(path,body):
            c=http.client.HTTPConnection('127.0.0.1',parts.port,timeout=8);c.request('POST',path,json.dumps(body),{'Authorization':'Bearer '+token,'Content-Type':'application/json'});r=c.getresponse();data=json.loads(r.read());c.close();return r.status,data
        code,created=req('/api/searches',{'time':{'mode':'all'}});assert code==201
        worker=threading.Thread(target=lambda:responses.append(req('/api/searches/'+created['query_id']+'/advance',{'cursor':created['cursor']})));worker.start()
        assert select.select([process.stderr],[],[],5)[0];assert process.stderr.readline()==b'ACTIVE_READ\n'
        process.send_signal(signal.SIGINT)
        assert select.select([process.stderr],[],[],5)[0];assert process.stderr.readline()==b'CANCEL_SET\n'
        process.stdin.write(b'x');process.stdin.flush();worker.join(8);assert not worker.is_alive();out,err=process.communicate(timeout=8)
        assert process.returncode==0;assert responses[0][0]==200;assert responses[0][1]['state']=='canceled';assert b'Traceback' not in err;assert token.encode() not in err;assert hashlib.sha256(p.read_bytes()).digest()==before
        c=http.client.HTTPConnection('127.0.0.1',parts.port,timeout=1)
        try:
            try:c.connect()
            except ConnectionRefusedError:pass
            else:raise AssertionError('server still listening after SIGINT')
        finally:c.close()
    finally:
        if process.poll() is None:process.kill();process.communicate()
