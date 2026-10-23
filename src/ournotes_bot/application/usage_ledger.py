# L3: Single-writer, versioned exact weighted usage facts; independent of legacy call counts.
# Effects: SQLite reservations/settlements and private pseudonym salt; no messages or credentials.
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time

from ..domain.model_calls import ModelError, Price, Usage, require


def day(now):
    return datetime.fromtimestamp(now,timezone(timedelta(hours=8))).date().isoformat()


def encode(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


class UsageLedger:
    def __init__(self,path,namespace,*,clock=time.time):
        self.path=Path(path);self.clock=clock;self.db=None
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.lock=self.path.with_suffix('.lock').open('a+b')
        try:
            if os.name=='nt':
                import msvcrt
                if self.lock.tell()==0: self.lock.write(b'0');self.lock.flush()
                self.lock.seek(0);msvcrt.locking(self.lock.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.db=sqlite3.connect(self.path)
            self.db.row_factory=sqlite3.Row
            self.db.executescript('''
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS configurations(version TEXT PRIMARY KEY,digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(
                    id TEXT PRIMARY KEY, request_id TEXT NOT NULL, user_key TEXT NOT NULL, group_key TEXT,
                    provider TEXT NOT NULL, model TEXT NOT NULL, config_version TEXT NOT NULL, price TEXT NOT NULL,
                    day TEXT NOT NULL, created REAL NOT NULL, started REAL, settled REAL,
                    state TEXT NOT NULL, reserved TEXT NOT NULL, charged TEXT NOT NULL, cost TEXT NOT NULL,
                    usage TEXT NOT NULL, error_code TEXT, billing_incomplete INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS attempts_day ON attempts(day,user_key,group_key);
            ''')
            with self.db:
                for key,value in (('schema','1'),('namespace',namespace)):
                    old=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
                    require(old is None or old[0]==value,'usage_ledger_identity')
                    self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',(key,value))
                self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('salt',secrets.token_hex(32)))
                self.salt=bytes.fromhex(self.db.execute("SELECT value FROM meta WHERE key='salt'").fetchone()[0])
                # A reservation before the durable started transition cannot have called HTTP.
                self.db.execute("UPDATE attempts SET state='released', reserved='0', error_code='restart_not_started' WHERE state='reserved'")
            self.reconcile()
        except BaseException:
            self.close();raise

    def key(self,kind,*parts):
        return hmac.new(self.salt,encode([kind,*parts]).encode(),hashlib.sha256).hexdigest()

    def configuration(self,version,digest):
        with self.db:
            old=self.db.execute('SELECT digest FROM configurations WHERE version=?',(version,)).fetchone()
            require(old is None or old[0]==digest,'config_version_reused')
            self.db.execute('INSERT OR IGNORE INTO configurations VALUES(?,?)',(version,digest))

    def get(self,attempt):
        row=self.db.execute('SELECT * FROM attempts WHERE id=?',(attempt,)).fetchone()
        require(row is not None,'unknown_attempt')
        return dict(row)

    def spent(self,date,key,*,group=False,exclude=None):
        column='group_key' if group else 'user_key'
        rows=self.db.execute(f'SELECT id,charged,reserved FROM attempts WHERE day=? AND {column}=?',(date,key))
        return sum((Fraction(r['charged'])+Fraction(r['reserved']) for r in rows if r['id']!=exclude),Fraction())

    def check(self,date,user,group,amount,profile,exclude=None):
        require(self.spent(date,user,exclude=exclude)+amount<=profile.user_daily_limit,'quota_exceeded')
        if group is not None:
            require(self.spent(date,group,group=True,exclude=exclude)+amount<=profile.group_daily_limit,'quota_exceeded')

    def reserve(self,request,attempt,user,group,profile,input_bound):
        self.reconcile()
        now=self.clock();date=day(now);amount=profile.price.reserve(input_bound,profile.max_output_tokens)
        with self.db:
            prior=self.db.execute('SELECT id FROM attempts WHERE id=?',(attempt,)).fetchone()
            require(prior is None,'attempt_reused')
            unit=self.db.execute("SELECT value FROM meta WHERE key='reference_unit'").fetchone()
            require(unit is None or unit[0]==profile.price.reference_unit,'reference_unit_mismatch')
            self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('reference_unit',profile.price.reference_unit))
            # Count business requests, so the allowed second generation is not a retry.
            recent=self.db.execute('SELECT COUNT(DISTINCT request_id) FROM attempts WHERE user_key=? AND created>? AND request_id!=?',
                                   (user,now-10,request)).fetchone()[0]
            require(recent<3,'model_rate_limited')
            self.check(date,user,group,amount,profile)
            self.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (attempt,request,user,group,profile.provider,profile.model,profile.config_version,encode(asdict(profile.price)),
                 date,now,None,None,'reserved',str(amount),'0','0',encode(asdict(Usage())),None,0))

    def start(self,attempt,profile):
        row=self.get(attempt);now=self.clock();date=day(now)
        require(row['state']=='reserved','attempt_already_started')
        with self.db:
            # A permit crossing midnight is charged to the actual start day.
            self.check(date,row['user_key'],row['group_key'],Fraction(row['reserved']),profile,exclude=attempt)
            self.db.execute("UPDATE attempts SET state='started',started=?,day=? WHERE id=?",(now,date,attempt))

    def release(self,attempt,code):
        with self.db:
            self.db.execute("UPDATE attempts SET state='released',reserved='0',settled=?,error_code=? WHERE id=? AND state='reserved'",
                            (self.clock(),code,attempt))

    def settle(self,attempt,usage,code=None):
        row=self.get(attempt)
        require(row['started'] is not None,'attempt_not_started')
        require(isinstance(usage,Usage),'usage_shape')
        old=Usage(**json.loads(row['usage']))
        new=encode(asdict(usage))
        if row['state']=='settled' and row['usage']==new: return False
        # Late partial data may only fill gaps, never silently change known facts.
        for field in ('input_total','input_cached','output_total','output_reasoning','total'):
            a,b=getattr(old,field),getattr(usage,field)
            require(a is None or b==a,'usage_conflict')
        if usage.status=='unknown' and old.status!='unknown': return False
        price=Price(**json.loads(row['price']));charged,cost=price.amount(usage)
        with self.db:
            self.db.execute("UPDATE attempts SET state='settled',reserved='0',charged=?,cost=?,usage=?,settled=?,error_code=?,billing_incomplete=? WHERE id=?",
                (str(charged),str(cost),new,self.clock(),code,int(usage.status!='known'),attempt))
        return True

    def reconcile(self):
        # Never replay model HTTP. Keep crash uncertainty for a finite five minutes.
        with self.db:
            self.db.execute("UPDATE attempts SET state='settled',reserved='0',settled=?,error_code='usage_unknown_after_restart',billing_incomplete=1 WHERE state='started' AND started<=?",
                            (self.clock(),self.clock()-300))

    def summary(self):
        rows=self.db.execute('SELECT usage,price,charged,cost,state,billing_incomplete FROM attempts WHERE day=?',(day(self.clock()),)).fetchall()
        currencies={}
        for row in rows:
            currency=json.loads(row['price'])['currency']
            currencies[currency]=currencies.get(currency,Fraction())+Fraction(row['cost'])
        return {'schema':'taki-usage/1','day':day(self.clock()),'attempts':len(rows),
                'started_pending':sum(r['state']=='started' for r in rows),
                'usage_unknown':sum(r['state']=='settled' and json.loads(r['usage'])['status']=='unknown' for r in rows),
                'usage_partial':sum(json.loads(r['usage'])['status']=='partial' for r in rows),
                'billing_incomplete':sum(r['billing_incomplete'] for r in rows),
                'price_estimated':sum(json.loads(r['price'])['estimated'] for r in rows),
                'weighted_tokens':str(sum((Fraction(r['charged']) for r in rows),Fraction())),
                'reference_cost_micro':{k:str(v) for k,v in currencies.items()}}

    def close(self):
        if self.db is not None: self.db.close();self.db=None
        self.lock.close()
