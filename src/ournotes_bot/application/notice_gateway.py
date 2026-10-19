# L3: Durable notice claims and group versions in the existing NoticeStore database.
# Effects: Uses the sole notices completion ledger; never calls a platform API.
import asyncio
import hashlib
import json
import re
import time
import uuid
from contextlib import closing
from .backend_protocol import PROTOCOL, require, identity
from .notice_reports import committed_notice


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class NoticeGateway:
    def __init__(self, backend, reports, capacity=4096):
        self.backend, self.reports, self.capacity = backend, reports, capacity
        self.store = reports.store
        self.lock = asyncio.Lock()
        with closing(self.store.connect()) as db, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS gateway_group_versions(
                    app TEXT,gid TEXT,version INTEGER,epoch TEXT,seq INTEGER,PRIMARY KEY(app,gid));
                CREATE TABLE IF NOT EXISTS gateway_notice_claims(
                    app TEXT,nid TEXT,cid TEXT UNIQUE,gid TEXT,revision TEXT,epoch TEXT,
                    version INTEGER,expires REAL,status TEXT,attempt TEXT,PRIMARY KEY(app,nid));
                CREATE TABLE IF NOT EXISTS gateway_notice_requests(
                    app TEXT,id TEXT,digest TEXT,response TEXT,PRIMARY KEY(app,id));
                CREATE TABLE IF NOT EXISTS gateway_notice_events(
                    app TEXT,id TEXT,digest TEXT,response TEXT,PRIMARY KEY(app,id));
            ''')
            db.execute("UPDATE gateway_notice_claims SET status='expired' WHERE app=? AND status='claimed'", (self.store.app_id,))
            # Existing notices.sending already suppresses retry in old and new runtimes.
            db.execute("UPDATE gateway_notice_claims SET status='uncertain' WHERE app=? AND status='sending'", (self.store.app_id,))

    def common(self, value, fields, active=True):
        require(isinstance(value, dict) and set(value) == set(fields) | {'protocol','instance_id','app_id','gateway_epoch'}, 'notice_shape')
        require(value['protocol'] == PROTOCOL, 'protocol_version', 409)
        require(value['instance_id'] == self.backend.instance_id and value['app_id'] == self.backend.app_id, 'notice_identity', 403)
        require(identity(value['gateway_epoch']), 'gateway_epoch')
        if active:
            require(self.backend.ready and not self.backend.closing and self.backend.ledger.active(value['gateway_epoch']), 'inactive_epoch', 409)

    def capacity_check(self, db, table):
        require(db.execute(f'SELECT count(*) FROM {table} WHERE app=?', (self.store.app_id,)).fetchone()[0] < self.capacity, 'notice_capacity', 503)

    def cached(self, db, table, key, value):
        row = db.execute(f'SELECT digest,response FROM {table} WHERE app=? AND id=?', (self.store.app_id,key)).fetchone()
        if row:
            require(row[0] == digest(value), 'notice_conflict', 409)
            return json.loads(row[1])
        self.capacity_check(db, table)

    def save(self, db, table, key, value, result):
        db.execute(f'INSERT INTO {table} VALUES(?,?,?,?)', (self.store.app_id,key,digest(value),json.dumps(result)))

    def group(self, value):
        self.common(value, {'event_id','group_id','kind','timestamp_ms','ingress_seq'})
        require(identity(value['event_id']) and isinstance(value['group_id'],str)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['group_id']), 'group_identity')
        require(value['kind'] in ('discover','add','remove','allow','reject'), 'group_kind')
        stamp, seq = value['timestamp_ms'], value['ingress_seq']
        require(type(stamp) in (int,float) and 0 <= stamp <= (time.time()+30)*1000
                and type(seq) is int and seq > 0, 'group_time_sequence')
        app, gid = self.store.app_id, value['group_id']
        key = 'group:'+value['event_id']
        stable = {k:v for k,v in value.items() if k not in ('gateway_epoch','ingress_seq')}
        with closing(self.store.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            cached = self.cached(db, 'gateway_notice_events', key, stable)
            if cached is not None: return cached
            row = db.execute('SELECT present,allowed,event_at FROM groups WHERE app=? AND gid=?',(app,gid)).fetchone()
            version = db.execute('SELECT version,epoch,seq FROM gateway_group_versions WHERE app=? AND gid=?',(app,gid)).fetchone()
            number = version[0] if version else 0
            applied = (not version or version[1] != value['gateway_epoch'] or seq > version[2]) and (not row or stamp/1000 >= row[2])
            if row is None:
                self.capacity_check(db, 'groups')
                db.execute('INSERT INTO groups(app,gid,present) VALUES(?,?,1)',(app,gid))
            if applied and (row is None or value['kind'] != 'discover'):
                number += 1
                kind = value['kind']
                if kind == 'add':
                    db.execute('UPDATE groups SET present=1,allowed=NULL,event_at=? WHERE app=? AND gid=?',(stamp/1000,app,gid))
                elif kind != 'discover':
                    field = 'present' if kind == 'remove' else 'allowed'
                    db.execute(f'UPDATE groups SET {field}=?,event_at=? WHERE app=? AND gid=?',(int(kind=='allow'),stamp/1000,app,gid))
            if applied:
                db.execute('INSERT OR REPLACE INTO gateway_group_versions VALUES(?,?,?,?,?)',(app,gid,number,value['gateway_epoch'],seq))
            result = {'group_version':number,'applied':bool(applied)}
            self.save(db, 'gateway_notice_events', key, stable, result)
            return result

    async def claims(self, value):
        fields = {'claim_request_id','limit'}
        if isinstance(value, dict) and 'group_allowlist' in value: fields.add('group_allowlist')
        self.common(value, fields)
        require(identity(value['claim_request_id']) and type(value['limit']) is int and 1 <= value['limit'] <= 16, 'claim_input')
        groups = value.get('group_allowlist', [])
        require(isinstance(groups, list) and len(groups) <= 128 and all(
            isinstance(gid, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', gid) for gid in groups), 'claim_groups')
        require(len(set(groups)) == len(groups), 'claim_groups')
        allowed = set(groups)
        async with self.lock:
            with closing(self.store.connect()) as db:
                cached = self.cached(db, 'gateway_notice_requests', value['claim_request_id'], value)
                if cached is not None: return cached
            notice = committed_notice(self.reports.project_root, self.reports.source)
            content = await self.reports._report(*notice) if notice else None
            self.common(value, fields)
            result = {'claims':[]}
            with closing(self.store.connect()) as db, db:
                db.execute('BEGIN IMMEDIATE')
                if content and committed_notice(self.reports.project_root, self.reports.source) == notice:
                    revision = notice[0]
                    rows = db.execute('SELECT gid FROM groups g WHERE app=? AND present=1 AND NOT EXISTS '
                        '(SELECT 1 FROM notices n WHERE n.app=g.app AND n.gid=g.gid AND n.revision=?) ORDER BY gid',
                        (self.store.app_id,revision)).fetchall()
                    for (gid,) in rows:
                        # Filter before the limit/lease: excluded groups must not starve permitted ones.
                        if allowed and gid not in allowed: continue
                        if len(result['claims']) >= value['limit']: break
                        nid = digest([self.backend.instance_id,self.store.app_id,gid,revision])
                        prior = db.execute('SELECT status,expires,epoch FROM gateway_notice_claims WHERE app=? AND nid=?',(self.store.app_id,nid)).fetchone()
                        if prior and (prior[0] in ('sending','sent','uncertain','failure') or
                                      prior[0]=='claimed' and prior[1]>time.time() and self.backend.ledger.active(prior[2])): continue
                        if not prior: self.capacity_check(db,'gateway_notice_claims')
                        row = db.execute('SELECT version FROM gateway_group_versions WHERE app=? AND gid=?',(self.store.app_id,gid)).fetchone()
                        version = row[0] if row else 0
                        cid, expires = uuid.uuid4().hex, time.time()+120
                        db.execute('INSERT OR REPLACE INTO gateway_notice_claims VALUES(?,?,?,?,?,?,?,?,?,NULL)',
                                   (self.store.app_id,nid,cid,gid,revision,value['gateway_epoch'],version,expires,'claimed'))
                        result['claims'].append(dict(notice_id=nid,claim_id=cid,expires_at=expires,group_id=gid,
                            group_version=version,deployment_revision=revision,text=content,send_type='active'))
                # Empty polls do not create unbounded metadata. Reusing their ID may observe new work.
                if result['claims']: self.save(db,'gateway_notice_requests',value['claim_request_id'],value,result)
            return result

    def event(self, nid, value):
        kind = value.get('event') if isinstance(value,dict) else None
        fields = {'claim_id','event_id','event','group_version'}
        if kind == 'permission': fields |= {'allowed','observed_at_ms'}
        if kind in ('intent','ack'): fields.add('attempt_id')
        if kind == 'ack': fields |= {'result','message_id'}
        self.common(value, fields, active=kind != 'ack')
        require(kind in ('permission','intent','ack','aborted') and identity(value['claim_id']) and identity(value['event_id']), 'notice_event')
        require(type(value['group_version']) is int and value['group_version'] >= 0, 'group_version')
        if kind in ('intent','ack'): require(identity(value['attempt_id']), 'attempt_id')
        if kind == 'permission':
            require(type(value['allowed']) is bool and type(value['observed_at_ms']) in (int,float)
                    and time.time()*1000-120000 <= value['observed_at_ms'] <= (time.time()+30)*1000, 'permission_observation')
        if kind == 'ack':
            require(value['result'] in ('success','failure','uncertain','aborted'), 'notice_result')
            require(identity(value['message_id']) if value['result']=='success' else value['message_id'] is None, 'notice_receipt')
        key = 'notice:'+value['event_id']
        with closing(self.store.connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            cached = self.cached(db,'gateway_notice_events',key,{'nid':nid,**value})
            if cached is not None:
                return {'permission':'existing'} if kind=='intent' else cached
            row = db.execute('SELECT cid,gid,revision,epoch,version,expires,status,attempt FROM gateway_notice_claims WHERE app=? AND nid=?',
                             (self.store.app_id,nid)).fetchone()
            require(row is not None and (row[0],row[3],row[4])==(value['claim_id'],value['gateway_epoch'],value['group_version']), 'claim_owner',409)
            cid,gid,revision,epoch,version,expires,status,attempt = row
            if kind == 'ack':
                require(attempt == value['attempt_id'] and status in ('sending','uncertain'), 'notice_attempt',409)
                outcome = 'sent' if value['result']=='success' else value['result']
                db.execute('UPDATE notices SET status=?,receipt=? WHERE app=? AND gid=? AND revision=?',
                           (outcome,value['message_id'] or '',self.store.app_id,gid,revision))
                db.execute('UPDATE gateway_notice_claims SET status=? WHERE app=? AND nid=?',(outcome,self.store.app_id,nid))
                result = {'recorded':True}
            else:
                require(expires > time.time() and status=='claimed', 'claim_expired_or_started',409)
                current = committed_notice(self.reports.project_root,self.reports.source)
                require(current and current[0]==revision,'deployment_changed',409)
                group = db.execute('SELECT present,allowed,event_at FROM groups WHERE app=? AND gid=?',(self.store.app_id,gid)).fetchone()
                current_version = db.execute('SELECT version FROM gateway_group_versions WHERE app=? AND gid=?',(self.store.app_id,gid)).fetchone()
                require(group and group[0] and (current_version[0] if current_version else 0)==version,'group_changed',409)
                if kind=='permission':
                    require(value['observed_at_ms']/1000 >= group[2], 'stale_permission',409)
                    db.execute('UPDATE groups SET allowed=? WHERE app=? AND gid=?',(int(value['allowed']),self.store.app_id,gid))
                    result = {'applied':True}
                elif kind=='aborted':
                    db.execute("UPDATE gateway_notice_claims SET status='aborted' WHERE app=? AND nid=?",(self.store.app_id,nid))
                    result = {'aborted':True}
                else:
                    permitted = group[1] != 0
                    inserted = db.execute('INSERT OR IGNORE INTO notices(app,gid,revision,status) VALUES(?,?,?,?)',
                        (self.store.app_id,gid,revision,'sending' if permitted else 'disabled')).rowcount
                    granted = bool(inserted and permitted)
                    db.execute('UPDATE gateway_notice_claims SET status=?,attempt=? WHERE app=? AND nid=?',
                               ('sending' if granted else 'failure',value['attempt_id'],self.store.app_id,nid))
                    result = {'permission':'granted' if granted else 'denied'}
            self.save(db,'gateway_notice_events',key,{'nid':nid,**value},result)
            return result
