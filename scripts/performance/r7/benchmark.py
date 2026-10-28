"""File-backed old/new usage ledger comparison. Synthetic data, no network.
Example: python -B scripts/performance/r7/benchmark.py --repo <candidate> --rounds 2 --output <new.json>
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
import types
from dataclasses import asdict
from unittest.mock import patch

BASELINE = '080c8bcdf1734193e475796b2917da6753fa9eda'
parser = argparse.ArgumentParser(__doc__)
parser.add_argument('--repo', type=Path, required=True)
parser.add_argument('--baseline-source', type=Path, default=Path(__file__).with_name('usage_ledger_baseline.py'))
parser.add_argument('--output', type=Path)
parser.add_argument('--worker-file', type=Path)
parser.add_argument('--worker-version', choices=['baseline','candidate'])
parser.add_argument('--sizes', type=int, nargs='+', default=[2000,20000,200000])
parser.add_argument('--day-shares', type=float, nargs='+', default=[.01,.5])
parser.add_argument('--statistics', choices=['none','analyzed'], nargs='+', default=['none','analyzed'])
parser.add_argument('--rounds', type=int, default=2)
parser.add_argument('--reads', type=int, default=5)
parser.add_argument('--writes', type=int, default=5)
args = parser.parse_args()
assert args.rounds >= 1 and args.reads >= 1 and args.writes >= 1
repo = args.repo.resolve()
sys.path.insert(0,str(repo/'src'))
from ournotes_bot.application import usage_ledger as current
from ournotes_bot.domain.model_calls import Price, ModelProfile, Usage
assert Path(current.__file__).resolve().is_relative_to(repo/'src')
source = args.baseline_source.read_bytes()
baseline = types.ModuleType('ournotes_bot.application._r7_baseline')
baseline.__package__ = 'ournotes_bot.application'
exec(compile(source,str(args.baseline_source),'exec'),baseline.__dict__)
Old, New = baseline.UsageLedger, current.UsageLedger
now = 1793059200.0
sha = lambda value: hashlib.sha256(value.encode()).hexdigest()
users, groups = [sha('u'+str(i)) for i in range(500)], [sha('g'+str(i)) for i in range(4)]
price = Price('USD',1000000,100000,2000000,1000000,'fixture',1,True,'registered-default')
profile = ModelProfile('deepseek','fixture','v1','deepseek-chat-v1','fixture',price,'fixture',
                      user_daily_limit=10**12,group_daily_limit=10**12,user_rate_requests=1000,group_rate_requests=1000)
price_json, usage_json = current.encode(asdict(price)), current.encode(asdict(Usage()))
queries = {
    'expired': ("UPDATE attempts SET state='settled',reserved='0',settled=?,error_code='usage_unknown_after_restart',billing_incomplete=1 WHERE state='started' AND started<=?", (now,now-300)),
    'user_recent': ('SELECT COUNT(DISTINCT request_id) FROM attempts WHERE user_key=? AND created>? AND request_id!=?', (users[0],now-60,sha('excluded'))),
    'group_recent': ('SELECT COUNT(DISTINCT request_id) FROM attempts WHERE group_key=? AND created>? AND request_id!=?', (groups[0],now-60,sha('excluded'))),
    'request': ('SELECT a.*,o.kind,o.duration_ms FROM attempts a LEFT JOIN attempt_observations o ON a.id=o.id WHERE request_id=? ORDER BY created LIMIT 8', (sha('r0'),)),
    'user_day': ('SELECT id,charged,reserved FROM attempts WHERE day=? AND user_key=?', (current.day(now),users[0])),
    'group_day': ('SELECT id,charged,reserved FROM attempts WHERE day=? AND group_key=?', (current.day(now),groups[0])),
}

def plans(db):
    return {key:[row[3] for row in db.execute('EXPLAIN QUERY PLAN '+sql,params)] for key,(sql,params) in queries.items()}

def timed(fn, count):
    values=[]
    for _ in range(count):
        started=time.perf_counter();fn();values.append((time.perf_counter()-started)*1000)
    return {'median_ms':statistics.median(values),'samples_ms':values}

def seed(path, count, day_share):
    ledger=Old(path,'fixture',clock=lambda:now)
    today_count=max(16,int(count*day_share));recent_count=min(400,today_count)
    dates=[current.day(now-86400*i) for i in range(120)]
    def rows():
        for i in range(count):
            request=i//2
            created=now-request%30 if i<recent_count else now-3600 if i<today_count else now-86400*(1+request%119)
            yield (sha('a'+str(i))[:32],sha('r'+str(request)),users[request%500],groups[request%4],
                   'deepseek','fixture','v1',price_json,dates[0] if i<today_count else dates[1+request%119],
                   created,now-1 if i<8 else created,None if i<8 else created+1,'started' if i<8 else 'settled',
                   '1' if i<8 else '0','1','0',usage_json,None,0)
    with ledger.db:
        ledger.db.executemany('INSERT INTO attempts VALUES('+','.join('?'*19)+')',rows())
        ledger.db.executemany('INSERT INTO attempt_observations VALUES(?,?,?)',((sha('a'+str(i))[:32],'fixture',1) for i in range(0,count,20)))
    ledger.close()
    return {'today_rows':today_count,'recent_rows':recent_count,'started_not_due':8}

original_connect=sqlite3.connect

def measure(kind, path, analyzed):
    create_times=[]
    class Connection(sqlite3.Connection):
        def executescript(self, sql):
            start=time.perf_counter()
            try: return super().executescript(sql)
            finally:
                if 'CREATE INDEX IF NOT EXISTS attempts_started_pending' in sql:
                    create_times.append((time.perf_counter()-start)*1000)
    def connect(*a,**kw):return original_connect(*a,**kw,factory=Connection)
    started=time.perf_counter()
    with patch.object(current.sqlite3,'connect',side_effect=connect):
        ledger=(Old if kind=='baseline' else New)(path,'fixture',clock=lambda:now)
    startup=(time.perf_counter()-started)*1000
    try:
        db=ledger.db
        assert db.execute('PRAGMA journal_mode').fetchone()[0]=='delete'
        assert db.execute('PRAGMA synchronous').fetchone()[0]==2
        before=plans(db)
        if analyzed: db.execute('ANALYZE');db.commit()
        after=plans(db)
        assert db.execute(*queries['user_recent']).fetchone()[0]>0
        assert db.execute(*queries['group_recent']).fetchone()[0]>0
        reads={key:timed(lambda sql=sql,params=params:db.execute(sql,params).fetchall(),args.reads) for key,(sql,params) in queries.items()}
        # Include zero-hit windows independently; never infer them from the positive fixture.
        for key in ['user_recent','group_recent']:
            sql,params=queries[key];miss=(sha('absent'),*params[1:])
            reads[key+'_empty']=timed(lambda sql=sql,params=miss:db.execute(sql,params).fetchall(),args.reads)
        db.commit()
        writes={key:[] for key in ['reserve','start','settle']}
        for i in range(args.writes):
            rid,aid=sha('bench-r'+str(i)),sha('bench-a'+str(i))[:32]
            for name,fn in [('reserve',lambda:ledger.reserve(rid,aid,sha('bench-u'),sha('bench-g'),profile,100)),
                            ('start',lambda:ledger.start(aid,profile)),
                            ('settle',lambda:ledger.settle(aid,Usage(100,0,20,None,120,'known')))]:
                start=time.perf_counter();fn();writes[name].append((time.perf_counter()-start)*1000)
        assert db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        return {'version':kind,'startup_ms':startup,'create_indexes_ms':sum(create_times),'bytes':path.stat().st_size,
                'plans_before_statistics':before,'plans':after,'reads':reads,
                'writes':{key:{'median_ms':statistics.median(values),'samples_ms':values} for key,values in writes.items()}}
    finally:ledger.close()

if args.worker_file is not None:
    assert args.worker_version and args.worker_file.resolve().is_relative_to(repo)
    worker_cpu=time.process_time();worker_wall=time.perf_counter()
    value=measure(args.worker_version,args.worker_file.resolve(),args.statistics[0]=='analyzed')
    value['cpu_seconds']=time.process_time()-worker_cpu
    value['wall_seconds']=time.perf_counter()-worker_wall
    try:
        import resource
        value['process_peak_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except ImportError:value['process_peak_rss_kib']=None
    print(json.dumps(value))
    raise SystemExit(0)

assert args.output is not None
output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
report={'scope':'Local file database and real old/new UsageLedger; synthetic rows; no network or production data',
        'baseline_commit':BASELINE,'baseline_source_sha256':hashlib.sha256(source).hexdigest(),
        'candidate_source_sha256':hashlib.sha256(Path(current.__file__).read_bytes()).hexdigest(),
        'python':platform.python_version(),'sqlite':sqlite3.sqlite_version,'platform':platform.platform(),
        'journal_mode':'delete','synchronous':'FULL','id_widths':{'user':64,'group':64,'request':64,'attempt':32},
        'order':'independent-process ABBA','rounds':args.rounds,'reads':args.reads,'writes':args.writes,'cases':[]}
start_cpu=time.process_time();start_wall=time.perf_counter()
with tempfile.TemporaryDirectory(prefix='benchmark-',dir=output.parent) as temp:
    root=Path(temp).resolve();assert root.is_relative_to(output.parent)
    for size in args.sizes:
        for share in args.day_shares:
            base=root/'seed.sqlite3';distribution=seed(base,size,share)
            for statistics_mode in args.statistics:
                case={'rows':size,'day_share':share,'distribution':distribution,'statistics':statistics_mode,'runs':[]}
                report['cases'].append(case)
                for round_number in range(args.rounds):
                    for position,kind in enumerate(['baseline','candidate','candidate','baseline']):
                        path=root/'active.sqlite3';shutil.copyfile(base,path)
                        command=[sys.executable,'-B',str(Path(__file__).resolve()),'--repo',str(repo),
                                 '--baseline-source',str(args.baseline_source.resolve()),'--worker-file',str(path),
                                 '--worker-version',kind,'--statistics',statistics_mode,
                                 '--reads',str(args.reads),'--writes',str(args.writes)]
                        worker=subprocess.run(command,env=dict(os.environ,QQ_APP_ID='',QQ_APP_SECRET='',AI_API_KEY=''),
                                              capture_output=True,text=True,check=True,timeout=300)
                        value=json.loads(worker.stdout)
                        value.update(round=round_number,position=position);case['runs'].append(value)
                        path.unlink();path.with_suffix('.lock').unlink()
                        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
                        print(json.dumps({'rows':size,'share':share,'statistics':statistics_mode,'round':round_number,
                                          'position':position,'kind':kind,'bytes':value['bytes'],
                                          'group_day_ms':value['reads']['group_day']['median_ms']}),flush=True)
            base.unlink();base.with_suffix('.lock').unlink()
report['cpu_seconds']=time.process_time()-start_cpu;report['wall_seconds']=time.perf_counter()-start_wall
try:
    import resource
    report['process_peak_rss_kib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
except ImportError:report['process_peak_rss_kib']=None
output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print('complete',str(output),flush=True)
