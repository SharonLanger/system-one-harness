from dataclasses import replace
from datetime import timedelta
import os
from pathlib import Path
import threading
import pytest
from log_viewer.query import QueryBuilder
from log_viewer.reader import LogReader, ReaderError
from log_viewer.service import LogQueryService, ServiceError
from log_viewer.limits import Limits
from .conftest import NOW
from .helpers import line,write,collect

CASES=['F01-missing-root','F02-empty-root','F03-hour-gap','F04-day-gap','F05-gap-only-hour','F05-gap-only-day','F06-leading-trailing-gaps','F07-zero-byte','F08-empty-day','F09-unsupported-paths','F10-file-eacces','F10-root-eacces','F11-malformed-middle','F12-pending-tail','F13-combined-tail','F14-append-snapshot','F15-new-day','F16-old-hour-append','F17-removed','F17-replaced','F17-truncated','F18-rewrite','F19-symlink-root','F19-symlink-day','F19-symlink-file','F19-fifo','F19-device-race','F20-below','F20-at','F20-above','F20-huge-tail','F21-block-crlf','F22-physical-duplicates','F23-empty-budget','F24-page-1','F24-page-2','F24-page-200','F25-relative-refresh','F26-cancel-inventory','F26-cancel-scan','F27-inventory-cap','F27-query-cap','F28-misfiled']

@pytest.mark.parametrize('case',CASES)
def test_reader_lifecycle(case,tmp_path,monkeypatch):
    root=tmp_path/'logs';limits=Limits();query=QueryBuilder.build({'time':{'mode':'all'}},NOW)
    reader=LogReader(root,limits);snapshot=None
    def open():return reader.open_snapshot(query)
    def messages(rows):return [r.fields['message'] for r in rows]
    def inventory(s):
        # One checkpoint at a time; no fake inventory or record source.
        for _ in range(10000):
            result=reader.advance(s,budget=replace(limits,advance_seconds=0))
            if s.inventory_done:return result
        pytest.fail('inventory did not finish')
    try:
        if case=='F01-missing-root':
            snapshot=open();rows,r,_=collect(reader,snapshot);assert not root.exists();assert r['empty_root'] and rows==[];return
        root.mkdir()
        if case=='F02-empty-root':
            snapshot=open();rows,r,_=collect(reader,snapshot);assert rows==[] and r['coverage']=='readable_complete';return
        if case.startswith(('F03','F04','F05','F06','F07','F08','F09')):
            write(root,line('old',timestamp='2026-11-07T00:30:00Z'),'2026-11-07/00-00.jsonl')
            write(root,line('new',timestamp='2026-11-07T02:30:00Z'),'2026-11-07/02-00.jsonl')
            if case.startswith('F04'):write(root,line('newest'),'2026-11-09/09-00.jsonl')
            if case.startswith('F05'):
                start,end=('2026-11-07T01:00:00Z','2026-11-07T02:00:00Z') if case.endswith('hour') else ('2026-11-08T00:00:00Z','2026-11-09T00:00:00Z')
                query=QueryBuilder.build({'time':{'mode':'absolute','start':start,'end':end}},NOW)
                opened=[];actual=reader._open
                def track(*args):opened.append(args);return actual(*args)
                monkeypatch.setattr(reader,'_open',track)
            if case.startswith('F06'):(root/'2026-11-07/00-00.jsonl').unlink()
            if case.startswith('F07'):write(root,b'','2026-11-07/01-00.jsonl')
            if case.startswith('F08'):(root/'2026-11-08').mkdir()
            if case.startswith('F09'):
                for p in ('.write.lock','legacy.jsonl','2026-02-30/00-00.jsonl','2026-11-07/24-00.jsonl','2026-11-07/00-01.jsonl','2026-11-07/nested/01-00.jsonl'):write(root,b'not json',p)
            snapshot=open();rows,r,_=collect(reader,snapshot)
            expected=[] if case.startswith('F05') else ['new'] if case.startswith('F06') else ['newest','new','old'] if case.startswith('F04') else ['new','old']
            assert messages(rows)==expected;assert r['diagnostics']==[];assert r['coverage']=='readable_complete'
            if case.startswith('F05'):assert opened==[]
            if case.startswith('F07'):assert r['progress']['files_examined']==3
            return
        p=write(root,line('one')+line('two'))
        if case.startswith('F10'):
            actual=os.open
            def denied(path,flags,*args,**kw):
                if (case.endswith('root-eacces') and str(path)==str(root)) or (case.endswith('file-eacces') and str(path)=='09-00.jsonl'):raise PermissionError('injected EACCES')
                return actual(path,flags,*args,**kw)
            monkeypatch.setattr(os,'open',denied)
            snapshot=open()
            if case.endswith('root-eacces'):
                with pytest.raises(ReaderError,match='root_unreadable'):collect(reader,snapshot)
            else:
                write(root,line('safe'),'2026-11-09/08-00.jsonl');rows,r,_=collect(reader,snapshot);assert messages(rows)==['safe'];assert r['diagnostic_counts']=={'source_unavailable':1};assert r['coverage']=='partial'
            return
        if case.startswith(('F11','F12','F13')):
            if case.startswith('F11'):p.write_bytes(line('one')+b'{bad}\n'+line('two'))
            if case.startswith('F12'):p.write_bytes(line('one')+line('tail').rstrip(b'\n'))
            if case.startswith('F13'):p.write_bytes(line('one')+b'{"partial":'+line('unrecoverable')+line('two'))
            snapshot=open();rows,r,_=collect(reader,snapshot)
            assert messages(rows)==(['one'] if case.startswith('F12') else ['two','one']);assert r['diagnostic_counts']==({'pending_tail':1} if case.startswith('F12') else {'invalid_json':1});assert r['coverage']=='partial'
            if case.startswith('F12'):
                with p.open('ab') as out:out.write(b'\n')
                fresh=open()
                try:assert messages(collect(reader,fresh)[0])==['tail','one']
                finally:reader.close(fresh)
            return
        if case.startswith(('F14','F15','F16','F17','F18')):
            if case.startswith('F16'):old=write(root,line('old'),'2026-11-08/23-00.jsonl')
            snapshot=open();inventory(snapshot)
            if case.startswith('F14'):
                with p.open('ab') as out:out.write(line('three'))
            elif case.startswith('F15'):write(root,line('three'),'2026-11-10/00-00.jsonl')
            elif case.startswith('F16'):
                with old.open('ab') as out:out.write(line('three'))
            elif case=='F17-removed':p.unlink()
            elif case=='F17-replaced':
                other=p.with_suffix('.tmp');other.write_bytes(line('evil'));other.replace(p)
            elif case=='F17-truncated':p.write_bytes(b'')
            elif case=='F18-rewrite':
                rows,r,_=collect(reader,snapshot);source=rows[0].source;p.write_bytes(p.read_bytes().replace(b'two',b'bad'))
                with pytest.raises(ReaderError,match='source_changed'):reader.retrieve_record(snapshot,source)
                return
            rows,r,_=collect(reader,snapshot)
            if case.startswith('F17'):assert rows==[];assert r['progress']['skipped_count']==1
            else:
                assert messages(rows)==(['two','one','old'] if case.startswith('F16') else ['two','one']);fresh=open()
                try:assert 'three' in messages(collect(reader,fresh)[0])
                finally:reader.close(fresh)
            return
        if case.startswith('F19'):
            sentinel=tmp_path/'outside';sentinel.mkdir();write(sentinel,line('NEVER'))
            p.unlink()
            if case.endswith('root'):
                (root/'2026-11-09').rmdir();root.rmdir();root.symlink_to(sentinel,target_is_directory=True)
            elif case.endswith('day'):
                (root/'2026-11-09').rmdir();(root/'2026-11-09').symlink_to(sentinel/'2026-11-09',target_is_directory=True)
            elif case.endswith('file'):p.symlink_to(sentinel/'2026-11-09/09-00.jsonl')
            elif case.endswith('device-race'):
                p.write_bytes(line('ordinary'))
                actual=os.open
                def device(path,flags,*args,**kwargs):
                    if str(path)=='09-00.jsonl':return actual('/dev/null',flags)
                    return actual(path,flags,*args,**kwargs)
                monkeypatch.setattr(os,'open',device)
            else:os.mkfifo(p)
            snapshot=open()
            if case.endswith('root'):
                with pytest.raises(ReaderError,match='unsafe'):collect(reader,snapshot)
            else:
                rows,r,_=collect(reader,snapshot);assert rows==[];assert r['progress']['skipped_count']==1
            return
        if case.startswith('F20'):
            size=4096;reader.limits=replace(limits,line_bytes=size,block_bytes=127)
            n=size+(-1 if case.endswith('below') else 0 if case.endswith('at') else 1)
            raw=line('x');raw=raw[:-2]+b' '*max(0,n-len(raw)+1)+raw[-2:]
            assert len(raw.rstrip(b'\n'))==n
            if case.endswith('huge-tail'):p.write_bytes(line('one')+b'x'*size*20)
            else:p.write_bytes(line('one')+raw+line('two'))
            snapshot=open();rows,r,_=collect(reader,snapshot)
            assert messages(rows)==(['one'] if case.endswith('huge-tail') else ['two','one'] if case.endswith('above') else ['two','x','one'])
            assert r['diagnostic_counts']==({'pending_tail':1} if case.endswith('huge-tail') else {'oversized_line':1} if case.endswith('above') else {})
            return
        if case=='F21-block-crlf':
            reader.limits=replace(limits,block_bytes=7);p.write_bytes(b'\n\r\n'+line('世界').replace(b'\n',b'\r\n')+b'\n'+line('ß'))
            snapshot=open();rows,r,_=collect(reader,snapshot);assert messages(rows)==['ß','世界'];assert r['diagnostics']==[];assert rows[1].source.offset==3;return
        if case=='F22-physical-duplicates':
            p.write_bytes(line('same')*3);snapshot=open();rows,r,_=collect(reader,snapshot);assert messages(rows)==['same']*3;assert len({row.source.offset for row in rows})==3;return
        if case=='F23-empty-budget':
            p.write_bytes(line('match')+line('no')*200);reader.limits=replace(limits,block_bytes=100,advance_bytes=100,advance_seconds=10)
            query=QueryBuilder.build({'time':{'mode':'all'},'text':{'value':'match'}},NOW);snapshot=open();rows,r,batches=collect(reader,snapshot);assert messages(rows)==['match'];assert any(not b['records'] and b['state']=='budget_yield' for b in batches);assert r['progress']['bytes_scanned']==p.stat().st_size;return
        if case.startswith('F24'):
            query=QueryBuilder.build({'time':{'mode':'all'},'page_size':int(case.rsplit('-',1)[1])},NOW);snapshot=open();rows,r,batches=collect(reader,snapshot);assert messages(rows)==['two','one'];assert r['progress']['bytes_scanned']==p.stat().st_size;assert r['progress']['files_discovered']==1;return
        if case=='F25-relative-refresh':
            q=QueryBuilder.build({},NOW);later=QueryBuilder.build({},NOW+timedelta(hours=1));assert q.end==NOW and later.start==NOW
            snapshot=reader.open_snapshot(q);rows,r,_=collect(reader,snapshot);assert len(rows)==2
            fresh=reader.open_snapshot(later)
            try:assert collect(reader,fresh)[0]==[]
            finally:reader.close(fresh)
            return
        if case.startswith('F26'):
            snapshot=open()
            if case.endswith('scan'):inventory(snapshot)
            reader.advance(snapshot,budget=replace(limits,advance_seconds=0))
            snapshot.cancel.set();r=reader.advance(snapshot);assert r['state']=='canceled';assert r['coverage']!='readable_complete';reader.close(snapshot);assert snapshot.root_fd is None;return
        if case=='F27-inventory-cap':
            reader.limits=replace(limits,inventory_entries=1);snapshot=open()
            with pytest.raises(ReaderError,match='inventory_limit'):collect(reader,snapshot)
            assert not snapshot.complete;return
        if case=='F27-query-cap':
            service=LogQueryService(reader,limits=replace(limits,active_queries=1),clock=lambda:NOW)
            try:
                service.create({})
                with pytest.raises(ServiceError,match='capacity'):service.create({})
            finally:service.close()
            return
        if case=='F28-misfiled':
            p.write_bytes(line('wrong',timestamp='2025-01-01T00:00:00Z'));query=QueryBuilder.build({'time':{'mode':'absolute','start':'2025-01-01T00:00:00Z','end':'2025-01-02T00:00:00Z'}},NOW);snapshot=open();rows,r,_=collect(reader,snapshot);assert rows==[];assert r['progress']['files_discovered']==0
    finally:
        if snapshot:reader.close(snapshot)
