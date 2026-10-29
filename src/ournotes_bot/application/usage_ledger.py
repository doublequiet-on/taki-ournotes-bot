# L3: Exact weighted usage and whole-bot primary USD budget; independent of legacy call counts.
# Effects: SQLite reservations/settlements, missing-action markers and deletion trigger; no messages or credentials.
from __future__ import annotations

from dataclasses import asdict
from collections import Counter
from datetime import datetime, timedelta, timezone
from fractions import Fraction
import hashlib
import hmac
import json
import os
import re
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
    def __init__(self,path,namespace,*,clock=time.time,primary_daily_usd='2.5',exempt_subjects=()):
        require(isinstance(exempt_subjects,tuple) and len(exempt_subjects)<=100
                and all(isinstance(s,str) and 1<=len(s)<=160 and not any(ord(c)<33 for c in s) for s in exempt_subjects),'quota_exemption_configuration')
        require(isinstance(primary_daily_usd,str) and re.fullmatch(r'\d{1,6}(?:\.\d{1,6})?',primary_daily_usd),'primary_budget_configuration')
        self.primary_daily_micro=Fraction(primary_daily_usd)*1_000_000
        self.path=Path(path);self.clock=clock;self.db=None;self.observation_error=None
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
                CREATE TABLE IF NOT EXISTS attempt_observations(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, duration_ms INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS attempt_actions(
                    attempt TEXT NOT NULL, kind TEXT NOT NULL, unit_cost_micro INTEGER NOT NULL,
                    max_count INTEGER NOT NULL, count INTEGER, status TEXT NOT NULL,
                    PRIMARY KEY(attempt,kind));
            ''')
            with self.db:
                for key,value in (('schema','1'),('namespace',namespace)):
                    old=self.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
                    require(old is None or old[0]==value,'usage_ledger_identity')
                    self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',(key,value))
                self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('salt',secrets.token_hex(32)))
                self.salt=bytes.fromhex(self.db.execute("SELECT value FROM meta WHERE key='salt'").fetchone()[0])
                self.exempt_users=frozenset(self.key('user',s) for s in exempt_subjects)
                self.db.execute('CREATE TABLE IF NOT EXISTS unbounded_actions(attempt TEXT PRIMARY KEY,kind TEXT NOT NULL,count INTEGER)')
                self.db.execute('CREATE TABLE IF NOT EXISTS expected_actions(attempt TEXT PRIMARY KEY,kind TEXT NOT NULL)')
                self.db.execute('''CREATE TRIGGER IF NOT EXISTS detect_missing_action AFTER DELETE ON attempt_actions
                    WHEN EXISTS (SELECT 1 FROM attempts WHERE id=OLD.attempt AND started IS NOT NULL)
                    BEGIN
                        INSERT OR IGNORE INTO unbounded_actions VALUES(OLD.attempt,OLD.kind,NULL);
                        UPDATE attempts SET billing_incomplete=1 WHERE id=OLD.attempt;
                    END''')
                # A reservation before the durable started transition cannot have called HTTP.
                self.db.execute("UPDATE attempts SET state='released', reserved='0', error_code='restart_not_started' WHERE state='reserved'")
                self.db.execute('INSERT OR IGNORE INTO expected_actions SELECT attempt,kind FROM attempt_actions')
            # Add access paths only after the persisted identity has been checked.
            # Pair the group time-window index with day/group: without statistics,
            # otherwise SQLite may scan a group's entire history for daily quota.
            self.db.executescript('''
                CREATE INDEX IF NOT EXISTS attempts_started_pending ON attempts(started) WHERE state='started';
                CREATE INDEX IF NOT EXISTS attempts_user_recent ON attempts(user_key,created,request_id);
                CREATE INDEX IF NOT EXISTS attempts_group_recent ON attempts(group_key,created,request_id);
                CREATE INDEX IF NOT EXISTS attempts_request_created ON attempts(request_id,created);
                CREATE INDEX IF NOT EXISTS attempts_day_group ON attempts(day,group_key);
            ''')
            with self.db:
                self.db.execute('''INSERT OR IGNORE INTO unbounded_actions
                    SELECT a.id,e.kind,NULL FROM attempts a JOIN expected_actions e ON e.attempt=a.id
                    WHERE a.started IS NOT NULL AND NOT EXISTS (SELECT 1 FROM attempt_actions x
                        WHERE x.attempt=e.attempt AND x.kind=e.kind)''')
                self.db.execute('UPDATE attempts SET billing_incomplete=1 WHERE id IN (SELECT attempt FROM unbounded_actions WHERE count IS NULL OR count<>0)')
            self.reconcile()
            self.restore_unknown_actions()
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
        rows=self.db.execute(f'SELECT id,user_key,charged,reserved FROM attempts WHERE day=? AND {column}=?',(date,key))
        return sum((Fraction(r['charged'])+Fraction(r['reserved']) for r in rows
                    if r['id']!=exclude and not (group and r['user_key'] in self.exempt_users)),Fraction())

    def primary_spent(self,date,exclude=None,*,include_exempt=False):
        total=Fraction()
        for row in self.db.execute("SELECT id,user_key,price,cost,reserved FROM attempts WHERE day=? AND provider<>'deepseek' AND state<>'released'",(date,)):
            if row['id']==exclude or (not include_exempt and row['user_key'] in self.exempt_users):continue
            price=Price(**json.loads(row['price']))
            require(price.currency=='USD','primary_budget_currency')
            total+=Fraction(row['cost'])+Fraction(row['reserved'])*price.reference_rate/1_000_000
        return total

    def check_primary(self,date,profile,amount,exclude=None,*,user=None):
        if user in self.exempt_users:return
        if profile.provider=='deepseek':return
        require(profile.price.currency=='USD','primary_budget_currency')
        require(not any(r[0] not in self.exempt_users for r in self.db.execute('''SELECT a.user_key FROM attempts a JOIN unbounded_actions u ON u.attempt=a.id
            WHERE a.provider<>'deepseek' AND (u.count IS NULL OR u.count<>0)''')),'usage_unavailable')
        micro=amount*profile.price.reference_rate/1_000_000
        require(self.primary_spent(date,exclude)+micro<=self.primary_daily_micro,'primary_budget_exceeded')

    def check(self,date,user,group,amount,profile,exclude=None):
        require(not any(r[0]==user or (user not in self.exempt_users and r[0] not in self.exempt_users) for r in self.db.execute('''SELECT a.user_key FROM attempts a JOIN unbounded_actions u ON u.attempt=a.id
            WHERE (u.count IS NULL OR u.count<>0) AND (a.user_key=? OR (? IS NOT NULL AND a.group_key=?))''',(user,group,group))),'usage_unavailable')
        self.check_primary(date,profile,amount,exclude,user=user)
        if user in self.exempt_users:return
        require(self.spent(date,user,exclude=exclude)+amount<=profile.user_daily_limit,'quota_exceeded')
        if group is not None:
            require(self.spent(date,group,group=True,exclude=exclude)+amount<=profile.group_daily_limit,'quota_exceeded')

    def reserve(self,request,attempt,user,group,profile,input_bound,*,action_budget=()):
        self.reconcile()
        now=self.clock();date=day(now);amount=profile.price.reserve(input_bound,profile.max_output_tokens)
        for kind,count,rate in action_budget:
            require(kind=='web_search' and type(count) is int and 1<=count<=8 and type(rate) is int and rate>=0,'provider_action_budget')
            amount+=Fraction(count*rate*1_000_000,profile.price.reference_rate)
        with self.db:
            prior=self.db.execute('SELECT id FROM attempts WHERE id=?',(attempt,)).fetchone()
            require(prior is None,'attempt_reused')
            unit=self.db.execute("SELECT value FROM meta WHERE key='reference_unit'").fetchone()
            require(unit is None or unit[0]==profile.price.reference_unit,'reference_unit_mismatch')
            self.db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('reference_unit',profile.price.reference_unit))
            # Count business requests, so the allowed second generation is not a retry.
            recent=self.db.execute('SELECT COUNT(DISTINCT request_id) FROM attempts WHERE user_key=? AND created>? AND request_id!=?',
                                   (user,now-profile.rate_window_seconds,request)).fetchone()[0]
            require(recent<profile.user_rate_requests,'model_rate_limited')
            if group is not None:
                recent_group=self.db.execute('SELECT COUNT(DISTINCT request_id) FROM attempts WHERE group_key=? AND created>? AND request_id!=?',
                    (group,now-profile.rate_window_seconds,request)).fetchone()[0]
                require(recent_group<profile.group_rate_requests,'model_rate_limited')
            self.check(date,user,group,amount,profile)
            self.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (attempt,request,user,group,profile.provider,profile.model,profile.config_version,encode(asdict(profile.price)),
                 date,now,None,None,'reserved',str(amount),'0','0',encode(asdict(Usage())),None,0))
            self.db.executemany('INSERT INTO attempt_actions VALUES(?,?,?,?,NULL,?)',
                                [(attempt,kind,rate,count,'reserved') for kind,count,rate in action_budget])
            self.db.executemany('INSERT INTO expected_actions VALUES(?,?)',[(attempt,kind) for kind,_,_ in action_budget])

    def start(self,attempt,profile):
        row=self.get(attempt);now=self.clock();date=day(now)
        require(row['state']=='reserved','attempt_already_started')
        with self.db:
            # A permit crossing midnight is charged to the actual start day.
            self.check(date,row['user_key'],row['group_key'],Fraction(row['reserved']),profile,exclude=attempt)
            self.db.execute("UPDATE attempts SET state='started',started=?,day=? WHERE id=?",(now,date,attempt))

    def release(self,attempt,code):
        with self.db:
            changed=self.db.execute("UPDATE attempts SET state='released',reserved='0',settled=?,error_code=? WHERE id=? AND state='reserved'",
                            (self.clock(),code,attempt)).rowcount
            if changed:self.db.execute("UPDATE attempt_actions SET status='released' WHERE attempt=?",(attempt,))

    def settle(self,attempt,usage,code=None,*,actions=()):
        row=self.get(attempt)
        require(row['started'] is not None,'attempt_not_started')
        require(isinstance(usage,Usage),'usage_shape')
        old=Usage(**json.loads(row['usage']))
        new=encode(asdict(usage))
        budgets=self.db.execute('SELECT * FROM attempt_actions WHERE attempt=?',(attempt,)).fetchall()
        counts={action.kind:action.count for action in actions}
        for expected in self.db.execute('SELECT kind FROM expected_actions WHERE attempt=?',(attempt,)):
            if expected['kind'] not in {b['kind'] for b in budgets}:counts.setdefault(expected['kind'],None)
        missing=set(counts)-{b['kind'] for b in budgets}
        if row['state']=='settled' and row['usage']==new and not missing and all(
                counts.get(b['kind']) is None or counts[b['kind']]==b['count'] for b in budgets):return False
        # Late partial data may only fill gaps, never silently change known facts.
        for field in ('input_total','input_cached','output_total','output_reasoning','total'):
            a,b=getattr(old,field),getattr(usage,field)
            require(a is None or b==a,'usage_conflict')
        if usage.status=='unknown' and old.status!='unknown': return False
        price=Price(**json.loads(row['price']));charged,cost=price.amount(usage)
        incomplete=usage.status!='known' or any(counts[k]!=0 for k in missing)
        actual=[];unknown_hold=self.token_hold(price,usage)
        for budget in budgets:
            count=counts.get(budget['kind'],budget['count'])
            if count is None:count=budget['count']
            require(budget['count'] is None or count==budget['count'],'usage_conflict')
            if count is None:
                incomplete=True
                # An unknown billable action is not zero: keep its bounded
                # reservation in the ordinary user/group quota until resolved.
                unknown_hold+=Fraction(budget['max_count']*budget['unit_cost_micro']*1_000_000,price.reference_rate)
            else:
                require(type(count) is int and 0<=count<=100,'provider_action_shape')
                amount=count*budget['unit_cost_micro']
                cost+=amount;charged+=Fraction(amount*1_000_000,price.reference_rate)
            actual.append((count,'unknown' if count is None else 'known',attempt,budget['kind']))
        with self.db:
            for kind in missing:
                count=counts[kind]
                prior=self.db.execute('SELECT kind,count FROM unbounded_actions WHERE attempt=?',(attempt,)).fetchone()
                require(prior is None or prior['kind']==kind and (prior['count'] is None or count is None or prior['count']==count),'usage_conflict')
                expected=self.db.execute('SELECT 1 FROM expected_actions WHERE attempt=? AND kind=?',(attempt,kind)).fetchone()
                if count!=0 or prior is not None or expected:
                    self.db.execute('INSERT OR REPLACE INTO unbounded_actions VALUES(?,?,?)',
                        (attempt,kind,count if count is not None else prior['count'] if prior else None))
            incomplete=incomplete or bool(self.db.execute('SELECT 1 FROM unbounded_actions WHERE attempt=? AND (count IS NULL OR count<>0)',(attempt,)).fetchone())
            self.db.execute("UPDATE attempts SET state='settled',reserved=?,charged=?,cost=?,usage=?,settled=?,error_code=?,billing_incomplete=? WHERE id=?",
                (str(unknown_hold),str(charged),str(cost),new,self.clock(),code,int(incomplete),attempt))
            self.db.executemany('UPDATE attempt_actions SET count=?,status=? WHERE attempt=? AND kind=?',actual)
        return True

    def reconcile(self):
        # Never replay model HTTP. Keep crash uncertainty for a finite five minutes.
        with self.db:
            now=self.clock()
            rows=self.db.execute("SELECT id,price FROM attempts WHERE state='started' AND started<=?",(now-300,)).fetchall()
            for row in rows:
                price=Price(**json.loads(row['price']))
                hold=self.token_hold(price,Usage())+sum((Fraction(a['max_count']*a['unit_cost_micro']*1_000_000,price.reference_rate)
                          for a in self.db.execute('SELECT max_count,unit_cost_micro FROM attempt_actions WHERE attempt=?',(row['id'],))),Fraction())
                self.db.execute("UPDATE attempts SET state='settled',reserved=?,settled=?,error_code='usage_unknown_after_restart',billing_incomplete=1 WHERE id=? AND state='started'",
                                (str(hold),now,row['id']))
                self.db.execute("UPDATE attempt_actions SET status='unknown' WHERE attempt=? AND status='reserved'",(row['id'],))
    def restore_unknown_actions(self):
        # Earlier versions could settle an unknown web action with zero
        # reserved quota. Reconstruct its upper bound on opening the ledger,
        # without changing known charges or action counts.
        with self.db:
            rows=self.db.execute("SELECT id,price,reserved,usage FROM attempts WHERE state='settled' AND billing_incomplete=1").fetchall()
            for row in rows:
                price=Price(**json.loads(row['price']))
                hold=self.token_hold(price,Usage(**json.loads(row['usage'])))+sum((Fraction(a['max_count']*a['unit_cost_micro']*1_000_000,price.reference_rate)
                          for a in self.db.execute("SELECT max_count,unit_cost_micro FROM attempt_actions WHERE attempt=? AND count IS NULL AND status IN ('reserved','unknown')",(row['id'],))),Fraction())
                if hold>Fraction(row['reserved']):
                    self.db.execute('UPDATE attempts SET reserved=? WHERE id=?',(str(hold),row['id']))

    @staticmethod
    def token_hold(price,usage):
        # Historical rows did not retain input ceilings. Use the supported
        # profile maxima, in original weighted units; known dimensions remain charged.
        amount=Fraction()
        if usage.input_total is None:
            amount+=Fraction(65536*max(price.input_rate,price.cached_rate),price.reference_rate)
        elif usage.input_cached is None and price.input_rate!=price.cached_rate:
            amount+=Fraction(usage.input_total*max(price.input_rate,price.cached_rate),price.reference_rate)
        if usage.output_total is None:amount+=Fraction(8192*price.output_rate,price.reference_rate)
        return amount

    def summary(self):
        rows=self.db.execute('SELECT usage,price,charged,cost,state,billing_incomplete,error_code FROM attempts WHERE day=?',(day(self.clock()),)).fetchall()
        currencies={}
        for row in rows:
            currency=json.loads(row['price'])['currency']
            currencies[currency]=currencies.get(currency,Fraction())+Fraction(row['cost'])
        return {'schema':'taki-usage/1','day':day(self.clock()),'attempts':len(rows),
                'observation_error':self.observation_error,
                'timezone':'Asia/Shanghai','error_counts':dict(Counter(r['error_code'] for r in rows if r['error_code'])),
                'started_pending':sum(r['state']=='started' for r in rows),
                'usage_unknown':sum(r['state']=='settled' and json.loads(r['usage'])['status']=='unknown' for r in rows),
                'usage_partial':sum(json.loads(r['usage'])['status']=='partial' for r in rows),
                'billing_incomplete':sum(r['billing_incomplete'] for r in rows),
                'price_estimated':sum(json.loads(r['price'])['estimated'] for r in rows),
                'weighted_tokens':str(sum((Fraction(r['charged']) for r in rows),Fraction())),
                'reference_cost_micro':{k:str(v) for k,v in currencies.items()}}

    def costs(self):
        """Exact currency amounts; holds are never presented as settled spend."""
        date=day(self.clock())
        groups={}
        for row in self.db.execute("SELECT provider,model,state,price,cost,reserved,billing_incomplete FROM attempts WHERE day=?",(date,)):
            price=Price(**json.loads(row['price']))
            key=(row['provider'],row['model'],price.currency)
            item=groups.setdefault(key,dict(provider=key[0],model=key[1],currency=key[2],cost=Fraction(),pending=Fraction(),unknown=Fraction(),attempts=0,incomplete=0))
            item['attempts']+=1
            if row['state']=='released':continue
            item['cost']+=Fraction(row['cost'])
            hold=Fraction(row['reserved'])*price.reference_rate/1_000_000
            item['pending' if row['state'] in ('reserved','started') else 'unknown']+=hold
            item['incomplete']+=row['billing_incomplete']
        items=[{k:str(v) if isinstance(v,Fraction) else v for k,v in item.items()} for item in groups.values()]
        try:
            spent=self.primary_spent(date)
            exempt_spent=self.primary_spent(date,include_exempt=True)-spent
            remaining=str(max(Fraction(),self.primary_daily_micro-spent))
            error=None
        except ModelError as exc:
            spent=None;exempt_spent=None;remaining=None;error=str(exc)
        return dict(day=date,timezone='Asia/Shanghai',limit_micro=str(self.primary_daily_micro),
                    primary_spent_and_reserved_micro=str(spent) if spent is not None else None,
                    exempt_primary_spent_and_reserved_micro=str(exempt_spent) if exempt_spent is not None else None,
                    remaining_micro=remaining,error=error,items=items)

    def observe(self, attempt, kind, duration_ms):
        require(isinstance(kind,str) and len(kind)<=64 and kind.replace('_','').isalnum(),'usage_observation')
        require(type(duration_ms) is int and 0<=duration_ms<=3600000,'usage_observation')
        try:
            with self.db:
                self.db.execute('INSERT OR REPLACE INTO attempt_observations VALUES(?,?,?)',(attempt,kind,duration_ms))
            return True
        except sqlite3.Error:
            self.observation_error='usage_observation_unavailable'
            return False  # Optional diagnostics cannot suppress a settled business reply.

    def request_view(self, request):
        rows=self.db.execute('''SELECT a.*,o.kind,o.duration_ms FROM attempts a
            LEFT JOIN attempt_observations o ON a.id=o.id WHERE request_id=? ORDER BY created LIMIT 8''',(request,))
        return [dict(attempt_id=r['id'],provider=r['provider'],model=r['model'],config_version=r['config_version'],
            day=r['day'],state=r['state'],started_at=r['started'],settled_at=r['settled'],
            tool_kind=r['kind'],duration_ms=r['duration_ms'],usage=json.loads(r['usage']),
            price=json.loads(r['price']),weighted_tokens=r['charged'],reserved_tokens=r['reserved'],
            reference_cost_micro=r['cost'],error_code=r['error_code'],billing_incomplete=bool(r['billing_incomplete']),
            provider_actions=[dict(action) for action in self.db.execute('SELECT kind,max_count,count,status,unit_cost_micro FROM attempt_actions WHERE attempt=?',(r['id'],))]) for r in rows]

    def group_view(self, group, profile):
        key=self.key('group',group)
        return {'day':day(self.clock()),'timezone':'Asia/Shanghai',
                'spent_and_reserved':str(self.spent(day(self.clock()),key,group=True)),
                'daily_limit':profile.group_daily_limit,'rate_requests':profile.group_rate_requests,
                'rate_window_seconds':profile.rate_window_seconds}

    def close(self):
        if self.db is not None: self.db.close();self.db=None
        self.lock.close()
