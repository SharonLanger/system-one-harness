import json
from pathlib import Path
from .test_parser_scenarios import BASE

def line(message='one',**changes):
    data=dict(BASE,message=message,timestamp='2026-11-09T09:30:00Z');data.update(changes)
    return json.dumps(data,ensure_ascii=False,separators=(',',':')).encode()+b'\n'

def write(root,raw,path='2026-11-09/09-00.jsonl'):
    p=root/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);return p

def collect(reader,snapshot):
    rows=[];batches=[]
    for _ in range(20000):
        r=reader.advance(snapshot);rows.extend(r['records']);batches.append(r)
        if r['state'] in ('complete','failed','canceled'):break
    else:raise AssertionError('continuation loop')
    return rows,r,batches
