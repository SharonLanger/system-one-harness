"""Opaque query lifecycle above a replaceable reader boundary."""
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
import secrets
import threading
import time
from .limits import Limits
from .query import QueryBuilder, iso
from .reader import ReaderError
from .summary import summarize

class ServiceError(RuntimeError):
    def __init__(self,code,status=409):
        self.code,self.status=code,status
        super().__init__(code)

@dataclass
class Search:
    id: str
    query: object
    snapshot: object
    cursor: str = field(default_factory=lambda:secrets.token_urlsafe(24))
    guard: object = field(default_factory=threading.Lock)
    last_cursor: str | None = None
    last_response: dict | None = None
    touched: float = field(default_factory=time.monotonic)
    state: str = 'created'
    handles: OrderedDict = field(default_factory=OrderedDict)
    closing: bool = False

class LogQueryService:
    def __init__(self,reader,limits=None,clock=None,monotonic=None):
        self.reader=reader
        self.limits=limits or reader.limits
        self.clock=clock or (lambda:datetime.now(timezone.utc))
        self.monotonic=monotonic or time.monotonic
        self.registry={}
        self.lock=threading.Lock()
    def _expire(self):
        now=self.monotonic()
        with self.lock:
            expired=[q for q in self.registry.values() if now-q.touched>self.limits.idle_seconds]
            for q in expired: self.registry.pop(q.id,None); q.closing=True; q.snapshot.cancel.set()
        for q in expired:
            if q.guard.acquire(False):
                try: self.reader.close(q.snapshot)
                finally: q.guard.release()
    def _get(self,id):
        self._expire()
        with self.lock:
            q=self.registry.get(id)
            if q is None: raise ServiceError('query_expired_restart',410)
            q.touched=self.monotonic()
            return q
    def create(self,request):
        query=QueryBuilder.build(request,self.clock())
        self._expire()
        with self.lock:
            if len(self.registry)>=self.limits.active_queries: raise ServiceError('query_capacity_close_or_restart',429)
            id=secrets.token_urlsafe(24)
            q=Search(id,query,self.reader.open_snapshot(query),touched=self.monotonic())
            self.registry[id]=q
        return {'query_id':id,'query':query.canonical(),'cursor':q.cursor,'state':'created'}
    def _summary(self,q,record):
        ref=secrets.token_urlsafe(18)
        q.handles[ref]=record.source
        while len(q.handles)>self.limits.handles: q.handles.popitem(last=False)
        return summarize(record,self.limits,ref)
    def advance(self,id,cursor):
        q=self._get(id)
        if not q.guard.acquire(False): raise ServiceError('advance_in_progress')
        try:
            if q.last_response is not None and cursor==q.last_cursor: return q.last_response
            if not isinstance(cursor,str) or cursor!=q.cursor: raise ServiceError('invalid_cursor')
            if q.state in ('complete','failed','canceled'): raise ServiceError('query_terminal_restart')
            try: result=self.reader.advance(q.snapshot)
            except ReaderError as exc:
                result={'state':'failed','records':[],'error':{'code':exc.code},'progress':dict(q.snapshot.progress),'coverage':'partial','diagnostics':list(q.snapshot.diagnostics),'diagnostic_counts':dict(q.snapshot.diagnostic_counts),'inventory_complete':q.snapshot.inventory_done,'empty_root':q.snapshot.empty_root}
            rows=result.pop('records')
            result['matches']=[self._summary(q,r) for r in rows]
            result['query_id']=q.id
            q.state=result['state']
            next_cursor=None if q.state in ('complete','failed','canceled') else secrets.token_urlsafe(24)
            result['cursor']=next_cursor
            q.last_cursor=cursor; q.cursor=next_cursor; q.last_response=result
            return result
        finally:
            q.touched=self.monotonic()
            if q.closing: self.reader.close(q.snapshot)
            q.guard.release()
    def cancel(self,id):
        q=self._get(id)
        if q.state not in ('complete','failed'):
            q.snapshot.cancel.set(); q.state='canceled'
        # Close suspended generators as soon as no advance owns them.
        if q.guard.acquire(False):
            try:
                if q.state=='canceled': self.reader.close(q.snapshot)
            finally: q.guard.release()
        else: q.closing=True
        return {'query_id':id,'state':q.state}
    def detail(self,id,ref,diagnostic=False):
        q=self._get(id)
        if not q.guard.acquire(False): raise ServiceError('advance_in_progress')
        try:
            source=q.snapshot.diagnostic_sources.get(ref) if diagnostic else q.handles.get(ref)
            if source is None: raise ServiceError('record_evicted_restart',410)
            try:
                if diagnostic: return self.reader.retrieve_diagnostic(q.snapshot,source)
                r=self.reader.retrieve_record(q.snapshot,source)
            except ReaderError as exc: raise ServiceError(exc.code,409) from exc
            # Only normalization defaults differ from the original document. Avoid
            # duplicating a maximum-sized details/message envelope in transport.
            normalized={k:v for k,v in r.fields.items() if k not in r.original}
            return {'original':r.original,'fields':normalized,'normalized_timestamp':iso(r.timestamp),'source':{'path':source.path,'offset':source.offset,'length':source.length}}
        finally:
            if q.closing: self.reader.close(q.snapshot)
            q.guard.release()
    def delete(self,id):
        q=self._get(id)
        with self.lock: self.registry.pop(id,None)
        q.closing=True; q.snapshot.cancel.set()
        if q.guard.acquire(False):
            try: self.reader.close(q.snapshot)
            finally: q.guard.release()
        return {'state':'closed'}
    def close(self):
        with self.lock: queries=list(self.registry.values()); self.registry.clear()
        for q in queries:
            q.snapshot.cancel.set(); q.closing=True
            with q.guard: self.reader.close(q.snapshot)
