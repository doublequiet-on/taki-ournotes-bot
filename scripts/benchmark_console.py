"""Bounded synthetic Console benchmark. No production data or network access."""
import argparse
import asyncio
import json
import platform
import statistics
import tempfile
import time
import tracemalloc
import uuid
from pathlib import Path
from types import SimpleNamespace

from ournotes_bot.application.backend import Backend
from ournotes_bot.config import Settings


def measure(function, n=10):
    times=[]
    for _ in range(n):
        started=time.perf_counter();function();times.append((time.perf_counter()-started)*1000)
    return {'median_ms':round(statistics.median(times),3),'max_ms':round(max(times),3),'samples':n}


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        root=Path(folder);now=time.time()
        b=Backend(None,None,Settings('','','fixture',root/'cache.json',6),instance_id='fixture',app_id='app',ledger_path=root/'ledger.sqlite3',clock=lambda:now)
        with b.ledger.db:
            b.ledger.db.executemany('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?)',
                ((f'{i:064x}','fixture-digest','epoch',b.runtime,'finished',now-4096+i,now+100,f'{i:032x}','hashed-context',i+1) for i in range(4096)))
            b.ledger.db.executemany("INSERT INTO parts VALUES(?,? ,0,'primary','success',NULL,NULL,NULL)",((f'{i:032x}','0') for i in range(4096)))
        with b.diagnostics.db:
            b.diagnostics.db.executemany('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',
                ((f'{i:064x}',f'{i:032x}',b.runtime,now-4096+i,'group','fixture-group','[{"kind":"help","outcome":"success"}]','[]') for i in range(4096)))
            b.diagnostics.db.executemany('INSERT INTO spans VALUES(?,?,?,?,?,?,?)',
                ((f'{i}-{p}',f'{i:064x}','send',0,None,1,now) for i in range(4096) for p in range(31)))
        write_requests=iter(range(20))
        result={'environment':{'os':platform.platform(),'python':platform.python_version(),'requests':4096,'fixture':True},
            'overview':measure(b.admin.overview),'list25':measure(lambda:b.admin.listing({})),
            'detail':measure(lambda:b.admin.detail(f'{4095:064x}')),
            'ledger_only':measure(b.ledger.admin_rows),
            'observation_write':measure(lambda:b.diagnostics.span(uuid.uuid4().hex,f'{next(write_requests):064x}','prepare',1,now),n=20)}
        result['diagnostics_bytes']=sum(p.stat().st_size for p in root.glob('diagnostics-v1.sqlite3*'))
        result['observation_health']=b.diagnostics.health()
        result['span_rows']=b.diagnostics.db.execute('SELECT count(*) FROM spans').fetchone()[0]
        enabled=b.diagnostics
        from ournotes_bot.application.diagnostics import Diagnostics
        disabled=Diagnostics(root/'disabled.sqlite3',enabled=False)
        b.diagnostics=disabled
        result['diagnostics_off']={'overview':measure(b.admin.overview),
            'observation_write':measure(lambda:disabled.span(uuid.uuid4().hex,f'{4095:064x}','prepare',1,now),n=20)}
        b.diagnostics=enabled
        tracemalloc.start();b.admin.overview();current,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
        result['read_python_allocations']={'current_bytes':current,'peak_bytes':peak,'scope':'one overview, excludes interpreter/native allocations'}
        result['environment']['spans_per_request']=31
        asyncio.run(b.close())
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2));print(json.dumps(result))

if __name__=='__main__':main()
