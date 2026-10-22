# L3: Bounded durable administrator jobs; independent from delivery/notice facts.
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path

from .backend_protocol import require, ProtocolError


class Operations:
    def __init__(self, path, identity, *, clock=time.time):
        self.clock, self.identity = clock, identity
        self.lock = threading.RLock()
        self.db, self.error, self.worker = None, None, None
        self.handlers, self.closing = {}, False
        self.notice_queue, self.notice_dropped, self.notice_error = deque(), 0, None
        try:
            self.db = sqlite3.connect(Path(path), timeout=0, check_same_thread=False)
            self.db.execute('PRAGMA busy_timeout=0')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, action TEXT, params TEXT,
                    signature TEXT, state TEXT, created REAL, started REAL, finished REAL, result TEXT);
                CREATE TABLE IF NOT EXISTS submissions(id TEXT PRIMARY KEY, signature TEXT, job TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS owner(identity TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS notice_observations(nid TEXT,kind TEXT,first REAL,last REAL,PRIMARY KEY(nid,kind));
            ''')
            owners = self.db.execute('SELECT identity FROM owner').fetchall()
            require(not owners or owners == [(identity,)], 'ops_instance_mismatch')
            with self.db:
                self.db.execute('INSERT OR IGNORE INTO owner VALUES(?)', (identity,))
                self.db.execute("UPDATE jobs SET state='interrupted',finished=?,result=? WHERE state IN ('queued','running')",
                                (clock(), json.dumps({'code': 'process_interrupted', 'automatic_replay': False})))
        except (OSError, sqlite3.Error, ValueError, ProtocolError) as exc:
            self.error = type(exc).__name__
            if self.db is not None:
                self.db.close()
            self.db = None

    def register(self, action, validate, run):
        self.handlers[action] = (validate, run)

    def health(self):
        return {'available': self.db is not None and self.error is None, 'reason_code': self.error,
                'actions': sorted(self.handlers), 'retention_days': 7, 'capacity': 200,
                'notice_observation':{'queued':len(self.notice_queue),'dropped':self.notice_dropped,'reason_code':self.notice_error}}

    def observe_notice(self, nid, kind):
        if self.db is None or len(self.notice_queue)>=256:
            self.notice_dropped+=1
            return
        self.notice_queue.append((nid,kind,self.clock()))

    def flush_notices(self):
        rows=[]
        for _ in range(min(32,len(self.notice_queue))):
            rows.append(self.notice_queue.popleft())
        if not rows or self.db is None:return
        try:
            with self.lock,self.db:
                self.db.executemany('INSERT INTO notice_observations VALUES(?,?,?,?) ON CONFLICT(nid,kind) DO UPDATE SET last=excluded.last',
                                    [(nid,kind,at,at) for nid,kind,at in rows])
                self.db.execute('DELETE FROM notice_observations WHERE last<? OR rowid IN (SELECT rowid FROM notice_observations ORDER BY last DESC LIMIT -1 OFFSET 16384)',(self.clock()-86400,))
            self.notice_error=None
        except sqlite3.Error:
            self.notice_dropped+=len(rows);self.notice_error='notice_observation_write_failed'

    def notice_times(self, nid):
        if self.db is None:return None
        if not self.lock.acquire(blocking=False):return None
        try:
            rows=self.db.execute('SELECT kind,first,last FROM notice_observations WHERE nid=?',(nid,)).fetchall()
            return {kind:{'first_observed_at':first,'last_observed_at':last} for kind,first,last in rows} or None
        except sqlite3.Error:
            return None
        finally:
            self.lock.release()

    def available(self):
        require(self.db is not None and self.error is None, 'ops_store_unavailable', 503)

    def prune(self):
        cutoff = self.clock() - 7*86400
        self.db.execute("DELETE FROM jobs WHERE state NOT IN ('queued','running') AND (created<? OR id IN "
                        "(SELECT id FROM jobs ORDER BY created DESC,id DESC LIMIT -1 OFFSET 199))", (cutoff,))
        self.db.execute('DELETE FROM submissions WHERE created<? OR job NOT IN (SELECT id FROM jobs)', (cutoff,))

    def housekeeping(self):
        if self.db is None or self.error is not None:return
        try:
            with self.lock,self.db:
                self.prune()
                self.db.execute('DELETE FROM notice_observations WHERE last<?',(self.clock()-86400,))
        except sqlite3.Error:self.error='ops_store_failed'

    async def submit(self, value):
        require(not self.closing, 'ops_closing', 503)
        require(isinstance(value, dict) and set(value) == {'operation_id', 'action', 'params'}, 'ops_shape')
        ident, action, params = value['operation_id'], value['action'], value['params']
        require(isinstance(ident, str) and re.fullmatch('[a-f0-9]{32}', ident), 'ops_identity')
        require(isinstance(action, str) and action in self.handlers, 'ops_action')
        require(isinstance(params, dict), 'ops_parameters')
        encoded = json.dumps(params, sort_keys=True, separators=(',', ':'), allow_nan=False)
        require(len(encoded.encode()) <= 4096, 'ops_parameters')
        signature = hashlib.sha256((action+'\0'+encoded).encode()).hexdigest()
        try:
            with self.lock:
                self.available()
                previous = self.db.execute('SELECT signature,job FROM submissions WHERE id=?', (ident,)).fetchone()
                if previous:
                    require(previous[0] == signature, 'ops_conflict', 409)
                    return self.detail(previous[1])
                prepared = self.handlers[action][0](params)
                execution = params if prepared is None else prepared
                execution_encoded = json.dumps(execution, sort_keys=True, separators=(',', ':'), allow_nan=False)
                execution_signature = hashlib.sha256((action+'\0'+execution_encoded).encode()).hexdigest()
                with self.db:
                    self.prune()
                    require(self.db.execute('SELECT count(*) FROM submissions').fetchone()[0] < 2000, 'ops_submission_capacity', 409)
                    existing = self.db.execute("SELECT id FROM jobs WHERE signature=? AND state IN ('queued','running')", (execution_signature,)).fetchone()
                    require(existing or self.db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] < 8,
                            'ops_queue_full', 409)
                    job = existing[0] if existing else ident
                    if not existing:
                        self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,NULL,NULL,NULL)',
                                        (job, action, execution_encoded, execution_signature, 'queued', self.clock()))
                    self.db.execute('INSERT INTO submissions VALUES(?,?,?,?)', (ident, signature, job, self.clock()))
                if self.worker is None or self.worker.done():
                    require(not self.closing, 'ops_closing', 503)
                    self.worker = asyncio.create_task(self.run())
                return self.detail(job)
        except sqlite3.Error:
            self.error = 'ops_store_failed'
            require(False, 'ops_store_unavailable', 503)

    def detail(self, ident):
        require(isinstance(ident, str) and re.fullmatch('[a-f0-9]{32}', ident), 'ops_identity')
        with self.lock:
            self.available()
            try:
                row = self.db.execute('SELECT id,action,state,created,started,finished,result FROM jobs WHERE id=?', (ident,)).fetchone()
            except sqlite3.Error:
                self.error='ops_store_failed'
                require(False,'ops_store_unavailable',503)
        require(row is not None, 'ops_unknown_job', 404)
        result = dict(zip(('id','action','state','created_at','started_at','finished_at','result'), row))
        try:
            result['result'] = json.loads(result['result']) if result['result'] else None
            if result['result'] is not None and not isinstance(result['result'],dict):raise ValueError('ops_result')
        except (ValueError,TypeError):
            self.error='ops_result_corrupt'
            require(False,'ops_store_unavailable',503)
        return result

    def listing(self):
        with self.lock:
            self.available()
            try:
                ids = [row[0] for row in self.db.execute('SELECT id FROM jobs ORDER BY created DESC,id DESC LIMIT 25')]
            except sqlite3.Error:
                self.error='ops_store_failed'
                require(False,'ops_store_unavailable',503)
            items = [self.detail(ident) for ident in ids]
            for item in items:
                result = item['result']
                if result is not None:
                    item['result'] = {k: v for k,v in result.items() if k in ('code','reused','repaired','failed','checked','persisted','published')}
            return {'items': items, 'health': self.health()}

    async def run(self):
        while not self.closing:
            try:
                with self.lock, self.db:
                    row = self.db.execute("SELECT id,action,params FROM jobs WHERE state='queued' ORDER BY created,id LIMIT 1").fetchone()
                    if row is None:
                        return
                    self.db.execute("UPDATE jobs SET state='running',started=? WHERE id=?", (self.clock(), row[0]))
                handler = self.handlers[row[1]][1]
                result = await handler(json.loads(row[2])) if inspect.iscoroutinefunction(handler) else await asyncio.to_thread(handler, json.loads(row[2]))
                require(isinstance(result, dict), 'ops_result')
                state = result.pop('_state', 'succeeded')
                require(state in ('succeeded', 'partial', 'failed'), 'ops_result')
                encoded = json.dumps(result, allow_nan=False)
                require(len(encoded.encode()) <= 65536, 'ops_result_size')
            except sqlite3.Error:
                self.error = 'ops_store_failed'
                return
            except Exception as exc:
                state, encoded = 'failed', json.dumps({'code': exc.code if isinstance(exc, ProtocolError) else 'operation_failed', 'error_type': type(exc).__name__})
            try:
                with self.lock, self.db:
                    self.db.execute('UPDATE jobs SET state=?,finished=?,result=? WHERE id=?', (state, self.clock(), encoded, row[0]))
            except sqlite3.Error:
                self.error = 'ops_result_unconfirmed'
                return

    async def close(self):
        self.closing = True
        if self.worker is not None:
            await asyncio.shield(self.worker)
        while self.notice_queue:
            await asyncio.to_thread(self.flush_notices)
        if self.db is not None:
            self.db.close()
            self.db=None
