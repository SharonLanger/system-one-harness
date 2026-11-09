"""Loopback-only HTTP adapter. API errors never include log values or tokens."""
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
import json
import re
import secrets
import socket
from .query import FIELDS, NULLABLE, ValidationError, strict_json
from .service import ServiceError

ASSETS={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript; charset=utf-8'),'/style.css':('style.css','text/css; charset=utf-8')}

class ViewerServer(ThreadingHTTPServer):
    daemon_threads=False
    block_on_close=True
    allow_reuse_address=False
    def __init__(self,service):
        self.service=service
        self.token=secrets.token_urlsafe(32)
        super().__init__(('127.0.0.1',0),Handler)
        self.origin=f'http://127.0.0.1:{self.server_port}'
        self.url=self.origin+'/#token='+self.token
    def service_actions(self):
        self.service._expire()
    def server_close(self):
        self.service.close()
        super().server_close()

class Handler(BaseHTTPRequestHandler):
    server_version='SOHLocalViewer'
    def setup(self):
        super().setup()
        self.connection.settimeout(5)
    def log_message(self,*args): pass
    def _send(self,status,value=None,asset=None):
        raw=asset[0] if asset else json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
        limit=self.server.service.limits.detail_bytes if '/records/' in self.path else self.server.service.limits.response_bytes
        if not asset and len(raw)>limit:
            status=413; raw=b'{"error":{"code":"response_limit_narrow_query"}}'
        self.send_response(status)
        self.send_header('Content-Type',asset[1] if asset else 'application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Content-Security-Policy',"default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try: self.wfile.write(raw)
        except (BrokenPipeError,ConnectionResetError): pass
    def _body(self):
        if self.headers.get_content_type()!='application/json': raise ServiceError('json_content_type_required',415)
        if self.headers.get('Transfer-Encoding'): raise ServiceError('unsupported_transfer_encoding',400)
        try: length=int(self.headers.get('Content-Length','-1'))
        except ValueError: raise ServiceError('invalid_content_length',400)
        if length<0: raise ServiceError('content_length_required',411)
        if length>self.server.service.limits.request_bytes: raise ServiceError('request_too_large',413)
        raw=self.rfile.read(length)
        if len(raw)!=length: raise ServiceError('incomplete_body',400)
        return strict_json(raw)
    def _dispatch(self):
        try:
            if self.headers.get_all('Host')!=[self.server.origin.removeprefix('http://')]: raise ServiceError('invalid_host',403)
            origins=self.headers.get_all('Origin')
            if origins is not None and origins!=[self.server.origin]: raise ServiceError('invalid_origin',403)
            path=self.path
            if self.command=='GET' and path in ASSETS:
                filename,mime=ASSETS[path]
                return self._send(200,asset=(resources.files('log_viewer').joinpath('static',filename).read_bytes(),mime))
            auth=self.headers.get_all('Authorization')
            if auth is None or len(auth)!=1 or not secrets.compare_digest(auth[0],'Bearer '+self.server.token): raise ServiceError('unauthorized_relaunch_url',401)
            service=self.server.service
            if path=='/api/config' and self.command=='GET':
                return self._send(200,{'root_label':'Configured local log root','fields':FIELDS,'nullable_fields':sorted(NULLABLE),'limits':asdict(service.limits)})
            if path=='/api/searches' and self.command=='POST': return self._send(201,service.create(self._body()))
            match=re.fullmatch(r'/api/searches/([A-Za-z0-9_-]+)(?:/(advance|cancel|records|diagnostics)(?:/([A-Za-z0-9_-]+))?)?',path)
            if match:
                id,action,ref=match.groups()
                if action=='advance' and ref is None and self.command=='POST':
                    body=self._body()
                    if not isinstance(body,dict) or set(body)!={'cursor'}: raise ServiceError('cursor_required',400)
                    return self._send(200,service.advance(id,body['cursor']))
                if action=='cancel' and ref is None and self.command=='POST':
                    if self._body()!={}: raise ServiceError('empty_body_required',400)
                    return self._send(200,service.cancel(id))
                if action in ('records','diagnostics') and ref and self.command=='GET': return self._send(200,service.detail(id,ref,action=='diagnostics'))
                if action is None and self.command=='DELETE': return self._send(200,service.delete(id))
                raise ServiceError('method_not_allowed',405)
            raise ServiceError('route_not_found',404)
        except ValidationError as exc: self._send(400,{'error':exc.as_dict()})
        except ServiceError as exc: self._send(exc.status,{'error':{'code':exc.code}})
        except (TimeoutError,socket.timeout): self._send(408,{'error':{'code':'request_timeout'}})
        except (OSError,ValueError,RecursionError): self._send(500,{'error':{'code':'local_read_error'}})
    do_GET=_dispatch
    do_POST=_dispatch
    do_DELETE=_dispatch
    do_OPTIONS=_dispatch
    do_PUT=_dispatch
    do_PATCH=_dispatch
    do_HEAD=_dispatch
    do_TRACE=_dispatch
    do_CONNECT=_dispatch
