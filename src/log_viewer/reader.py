"""Descriptor-relative, resumable JSONL backend. Never acquires writer locks."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import os
import re
import stat
import threading
import time
from .limits import Limits
from .summary import summarize
from .query import compact
from .parser import LogRecordParser, ParsedRecord, RecordDiagnostic

class ReaderError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class FileIdentity:
    path: str
    device: int
    inode: int
    size: int
    mtime_ns: int

@dataclass(frozen=True)
class Source:
    file: FileIdentity
    offset: int
    length: int
    digest: str = ''
    @property
    def path(self): return self.file.path

@dataclass
class Snapshot:
    query: object
    cancel: threading.Event = field(default_factory=threading.Event)
    root_fd: int | None = None
    manifest: list = field(default_factory=list)
    inventory_done: bool = False
    empty_root: bool = False
    complete: bool = False
    closed: bool = False
    iterator: object = None
    pending_record: object = None
    page_rows: int = 0
    progress: dict = field(default_factory=lambda: dict(files_discovered=0,files_examined=0,bytes_scanned=0,records_examined=0,matches_seen=0,skipped_count=0,pending_tail_count=0,inventory_entries=0))
    diagnostics: list = field(default_factory=list)
    diagnostic_counts: dict = field(default_factory=dict)
    diagnostic_sources: dict = field(default_factory=dict)

class LogReader:
    def __init__(self, root, limits=None, parser=None):
        self.root = os.fspath(root)
        self.limits = limits or Limits()
        self.parser = parser or LogRecordParser()

    def open_snapshot(self, query):
        # Creation is deliberately free of filesystem access.
        s = Snapshot(query)
        s.iterator = self._events(s)
        return s

    def _diagnostic(self, s, code, source=None, field=None, skipped=True):
        s.diagnostic_counts[code] = s.diagnostic_counts.get(code,0) + 1
        if skipped: s.progress['skipped_count'] += 1
        if code == 'pending_tail': s.progress['pending_tail_count'] += 1
        if len(s.diagnostics) < self.limits.diagnostic_entries:
            entry = {'code':code,'severity':'warning','path':source.path if source else None,'offset':source.offset if source else None}
            if field: entry['field'] = field
            if source:
                ref = str(len(s.diagnostics))
                entry['ref'] = ref
                s.diagnostic_sources[ref] = source
            s.diagnostics.append(entry)

    def _dir(self, name, parent=None):
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)

    def _open(self, s, identity):
        day, name = identity.path.split('/')
        try:
            rootfd=s.root_fd
            temporary_root=rootfd is None
            if temporary_root: rootfd=self._dir(self.root)
            try: dfd = self._dir(day,rootfd)
            finally:
                if temporary_root: os.close(rootfd)
            try: fd = os.open(name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=dfd)
            finally: os.close(dfd)
            st = os.fstat(fd)
            if (not stat.S_ISREG(st.st_mode) or (st.st_dev,st.st_ino)!=(identity.device,identity.inode)
                or st.st_size<identity.size or (st.st_size==identity.size and st.st_mtime_ns!=identity.mtime_ns)):
                os.close(fd)
                raise ReaderError('source_changed')
            return fd
        except OSError as exc: raise ReaderError('source_unavailable') from exc

    def _inventory(self, s):
        try: s.root_fd = self._dir(self.root)
        except FileNotFoundError:
            s.empty_root = True
            return
        except OSError as exc: raise ReaderError('root_unreadable_or_unsafe') from exc
        metadata = 0
        with os.scandir(s.root_fd) as days:
            for day in days:
                if s.cancel.is_set(): return
                s.progress['inventory_entries'] += 1
                if s.progress['inventory_entries'] > self.limits.inventory_entries: raise ReaderError('inventory_limit_narrow_range')
                yield None
                if not re.fullmatch(r'\d{4}-\d\d-\d\d',day.name): continue
                try: date = datetime.strptime(day.name,'%Y-%m-%d').replace(tzinfo=timezone.utc)
                except ValueError: continue
                if s.query.start and date+timedelta(days=1)<=s.query.start: continue
                if s.query.end and date>=s.query.end: continue
                try: dfd = self._dir(day.name,s.root_fd)
                except OSError:
                    self._diagnostic(s,'unsafe_or_unreadable_directory')
                    continue
                try:
                    with os.scandir(dfd) as files:
                        for item in files:
                            if s.cancel.is_set(): return
                            s.progress['inventory_entries'] += 1
                            if s.progress['inventory_entries'] > self.limits.inventory_entries: raise ReaderError('inventory_limit_narrow_range')
                            yield None
                            if not re.fullmatch(r'(?:[01]\d|2[0-3])-00\.jsonl',item.name): continue
                            hour = date + timedelta(hours=int(item.name[:2]))
                            if s.query.start and hour+timedelta(hours=1)<=s.query.start: continue
                            if s.query.end and hour>=s.query.end: continue
                            path = day.name+'/'+item.name
                            try: st = item.stat(follow_symlinks=False)
                            except OSError:
                                self._diagnostic(s,'source_unavailable'); continue
                            identity = FileIdentity(path,st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns)
                            if not stat.S_ISREG(st.st_mode):
                                self._diagnostic(s,'unsafe_source',Source(identity,0,0)); continue
                            metadata += 256 + len(path.encode('utf-8'))
                            if metadata > self.limits.inventory_bytes: raise ReaderError('inventory_limit_narrow_range')
                            s.manifest.append(identity)
                            s.progress['files_discovered'] += 1
                finally: os.close(dfd)
        s.manifest.sort(key=lambda f:f.path,reverse=True)

    def _lines(self, s, identity):
        """Yield checkpoints and byte lines in reverse with bounded fragment memory."""
        fd = self._open(s,identity)
        try:
            pos = identity.size
            end = pos
            chunks, length, first, oversized = [], 0, True, False
            while pos:
                if s.cancel.is_set(): return
                count = min(pos,self.limits.block_bytes)
                pos -= count
                data = os.pread(fd,count,pos)
                if len(data)!=count: raise ReaderError('source_changed')
                s.progress['bytes_scanned'] += len(data)
                yield None
                right = len(data)
                while right:
                    i = data.rfind(b'\n',0,right)
                    part = data[i+1:right]
                    length += len(part)
                    if length<=self.limits.line_bytes and not oversized: chunks.append(part)
                    else: oversized=True; chunks=[]
                    if i<0: break
                    start = pos+i+1
                    if first:
                        if length:
                            self._diagnostic(s,'pending_tail',Source(identity,start,end-start))
                        first=False
                    elif oversized:
                        self._diagnostic(s,'oversized_line',Source(identity,start,end-start))
                    else:
                        raw=b''.join(reversed(chunks))
                        if raw.strip(): yield (raw,Source(identity,start,end-start,hashlib.sha256(raw).hexdigest()))
                    end=pos+i
                    chunks=[]; length=0; oversized=False
                    right=i
            if length:
                source=Source(identity,0,end)
                if first: self._diagnostic(s,'pending_tail',source)
                elif oversized: self._diagnostic(s,'oversized_line',source)
                else:
                    raw=b''.join(reversed(chunks))
                    if raw.strip(): yield raw,Source(identity,0,end,hashlib.sha256(raw).hexdigest())
        finally: os.close(fd)

    def _events(self,s):
        yield from self._inventory(s)
        if s.cancel.is_set(): return
        s.inventory_done = True
        yield None
        for identity in s.manifest:
            if s.cancel.is_set(): return
            try:
                for item in self._lines(s,identity):
                    if item is None: yield None; continue
                    if s.cancel.is_set(): return
                    raw,source = item
                    s.progress['records_examined'] += 1
                    record = self.parser.parse(raw,source)
                    if isinstance(record,RecordDiagnostic): self._diagnostic(s,record.code,source,record.field)
                    else:
                        for d in record.diagnostics: self._diagnostic(s,d.code,source,d.field,skipped=False)
                        if s.query.matches(record):
                            s.progress['matches_seen'] += 1
                            yield record
                            continue
                    yield None
            except ReaderError as exc: self._diagnostic(s,exc.code,Source(identity,0,0))
            s.progress['files_examined'] += 1
            yield None
        s.complete = True

    def advance(self, snapshot, position=None, query=None, budget=None):
        """Position belongs to this snapshot; no scan restart is possible."""
        s = snapshot
        if s.closed: raise ReaderError('snapshot_closed')
        budget = budget or self.limits
        began=time.monotonic(); before=s.progress['bytes_scanned']; rows=[]
        page_limit=s.query.page_size-s.page_rows
        response_size=48*1024
        while not s.cancel.is_set() and len(rows)<page_limit:
            try:
                if s.pending_record is not None:
                    event=s.pending_record; s.pending_record=None
                else: event=next(s.iterator)
            except StopIteration: break
            if isinstance(event,ParsedRecord):
                size=len(compact(summarize(event,self.limits,"x"*24)).encode("utf-8"))+1
                if rows and response_size+size>self.limits.response_bytes:
                    s.pending_record=event
                    break
                rows.append(event);response_size+=size
            if s.progress['bytes_scanned']-before>=budget.advance_bytes or time.monotonic()-began>=budget.advance_seconds: break
        s.page_rows+=len(rows)
        if s.cancel.is_set(): state='canceled'
        elif s.complete: state='complete'
        elif s.page_rows>=s.query.page_size:
            state='page_ready'
            s.page_rows=0
        else: state='budget_yield'
        return {'records':rows,'state':state,'progress':dict(s.progress),'inventory_complete':s.inventory_done,
                'coverage':'unknown' if not s.inventory_done else 'partial' if not s.complete or s.progress['skipped_count'] or s.progress['pending_tail_count'] else 'readable_complete',
                'diagnostics':list(s.diagnostics),'diagnostic_counts':dict(s.diagnostic_counts),'empty_root':s.empty_root}

    def retrieve_record(self,s,source):
        fd=self._open(s,source.file)
        try: raw=os.pread(fd,source.length,source.offset)
        finally: os.close(fd)
        if hashlib.sha256(raw).hexdigest()!=source.digest: raise ReaderError('source_changed')
        record=self.parser.parse(raw,source)
        if not isinstance(record,ParsedRecord): raise ReaderError('source_changed')
        return record

    def retrieve_diagnostic(self,s,source):
        fd=self._open(s,source.file)
        try: raw=os.pread(fd,min(source.length,self.limits.diagnostic_preview_bytes),source.offset)
        finally: os.close(fd)
        return {'preview':raw.decode('utf-8',errors='replace'),'truncated':source.length>len(raw),'validated_record':False,'path':source.path,'offset':source.offset}

    def close(self,s):
        if s.closed: return
        s.cancel.set()
        if s.iterator is not None: s.iterator.close()
        if s.root_fd is not None: os.close(s.root_fd); s.root_fd=None
        s.pending_record=None
        s.closed=True
