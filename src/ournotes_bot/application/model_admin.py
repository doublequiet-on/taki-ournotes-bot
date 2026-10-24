# L3: Protected versioned model settings and explicit, billed capability probes.
# Effects: Private config replacement and metadata-only SQLite audit; never returns credentials or text.
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import hashlib
import json
import re
import sqlite3
import time

from ..domain.model_calls import ModelError, Usage, require
from ..sources.model_provider import ENDPOINTS, request_body
from ..sources.json_validation import decode_json
from .usage_ledger import encode


class ModelAdmin:
    def __init__(self, runtime, path):
        self.runtime=runtime;self.backend=runtime.backend;self.config=runtime.config;self.task=None;self.db=None;self.error=None
        try: self.open(path)
        except (sqlite3.Error,ModelError,OSError):
            if self.db is not None: self.db.close();self.db=None
            self.error='model_admin_unavailable'

    def open(self,path):
        self.db=sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS identity(value TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY,digest TEXT NOT NULL,action TEXT NOT NULL,
                created REAL NOT NULL,state TEXT NOT NULL,config_version TEXT,result TEXT);''')
        identity='\0'.join(self.config.namespace)
        row=self.db.execute('SELECT value FROM identity').fetchone()
        require(row is None or row[0]==identity,'model_admin_identity')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO identity VALUES(?)',(identity,))
            # Never replay a paid probe or a configuration mutation after a crash.
            self.db.execute("UPDATE actions SET state='interrupted' WHERE state='intent'")

    def guard(self, fingerprint):
        return {'backend_epoch':self.backend.runtime,'fingerprint':fingerprint}

    def check_guard(self, guard):
        _,fingerprint=self.config.read()
        require(guard==self.guard(fingerprint),'model_config_conflict')

    def settings(self):
        require(not self.error,'model_admin_unavailable')
        profile=None;groups={};code='model_not_configured';fingerprint=None;key=False
        try:
            data,fingerprint=self.config.read()
            if data is not None:
                snapshot=self.config.parse(data,fingerprint,api_key='configuration-preview')
                profile=asdict(snapshot.profile);profile.pop('api_key')
                secret=data.get('secret',self.config.api_key)
                key=isinstance(secret,str) and 1<=len(secret)<=512 and not any(c.isspace() for c in secret)
                groups=dict(snapshot.groups);code='ready' if key else 'model_not_configured'
        except (OSError,ValueError,TypeError,KeyError,RecursionError):
            code='model_configuration' if self.config.path else 'model_not_configured'
            fingerprint=None
        if profile is None:
            # Unsaved form defaults, not a configured/verified model or a claimed market price.
            profile={'provider':'deepseek','model':'deepseek-chat','config_version':'',
                'wire_profile':'deepseek-chat-v1','capability_evidence':'',
                'max_input_tokens':32768,'max_output_tokens':768,'user_daily_limit':100000,'group_daily_limit':1000000,
                'parallel_control':False,'cache_usage':True,'reasoning_usage':False,
                'user_rate_requests':3,'group_rate_requests':30,'rate_window_seconds':10,
                'price':{'currency':'CNY','input_rate':1000000,'cached_rate':1000000,'output_rate':1000000,
                    'reference_rate':1000000,'pricing_version':'','fetched_at':0,'estimated':True,'source':'registered-default'}}
        return {'code':code,'editable':self.config.path is not None and fingerprint is not None,
                'guard':self.guard(fingerprint),'profile':profile,'groups':groups,'key_configured':key,
                'endpoints':dict(ENDPOINTS),'api_url':ENDPOINTS[profile['provider']],
                'capability':self.latest_probe(profile['config_version']),
                'audit':self.audit(),'timezone':'Asia/Shanghai','price_status':self.config.status()}

    def latest_probe(self, version):
        row=self.db.execute("SELECT id,state,result FROM actions WHERE action='probe' AND config_version=? ORDER BY created DESC LIMIT 1",(version,)).fetchone()
        return dict(operation_id=row['id'],state=row['state'],result=json.loads(row['result']) if row['result'] else None) if row else None

    def audit(self):
        return [dict(operation_id=r['id'],action=r['action'],created_at=r['created'],state=r['state'],
                     config_version=r['config_version'],result=json.loads(r['result']) if r['result'] else None)
                for r in self.db.execute('SELECT * FROM actions ORDER BY created DESC LIMIT 25')]

    def begin(self, value, action, version):
        require(not self.error,'model_admin_unavailable')
        operation=value.get('operation_id')
        require(isinstance(operation,str) and re.fullmatch('[a-f0-9]{32}',operation),'model_admin_shape')
        digest=hashlib.sha256(encode(value).encode()).hexdigest()
        row=self.db.execute('SELECT * FROM actions WHERE id=?',(operation,)).fetchone()
        if row:
            require(row['digest']==digest and row['action']==action,'model_operation_conflict')
            return False
        require(self.db.execute("SELECT count(*) FROM actions WHERE state='intent'").fetchone()[0]<1,'model_admin_busy')
        with self.db:
            self.db.execute("DELETE FROM actions WHERE created<? AND state!='intent'",(time.time()-30*86400,))
            require(self.db.execute('SELECT count(*) FROM actions').fetchone()[0]<1000,'model_admin_capacity')
            self.db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,NULL)',
                (operation,digest,action,time.time(),'intent',version))
        return True

    def finish(self, operation, state, result):
        try:
            with self.db:
                self.db.execute('UPDATE actions SET state=?,result=? WHERE id=?',(state,encode(result),operation))
        except sqlite3.Error:
            self.error='model_admin_unavailable';raise ModelError(self.error) from None

    def save(self, value):
        require(isinstance(value,dict) and set(value)=={'operation_id','guard','profile','groups','api_key','api_url'},'model_admin_shape')
        operation=value['operation_id']
        if not self.begin(value,'save','console-'+str(operation)): return self.settings()
        try:
            self.check_guard(value['guard'])
            old,fingerprint=self.config.read()
            require(isinstance(value['profile'],dict),'model_configuration')
            profile=dict(value['profile'])
            require(value['api_url']==ENDPOINTS.get(profile.get('provider')),'model_endpoint_unsupported')
            require(isinstance(value['api_key'],str),'model_configuration')
            secret=value['api_key'] or (old.get('secret',self.config.api_key) if old else self.config.api_key)
            profile['config_version']='console-'+operation
            profile['price']={**profile['price'],'pricing_version':'default/'+operation,
                              'fetched_at':time.time(),'estimated':True,'source':'registered-default'}
            data={'schema':'taki-model-config/2','instance_id':self.config.namespace[0],'app_id':self.config.namespace[1],
                  'profile':profile,'secret':secret,'bindings':old['bindings'] if old else [],'groups':value['groups']}
            if old and old.get('schema')=='taki-model-config/3':
                data.update(schema='taki-model-config/3',group_admissions=old['group_admissions'])
            snapshot=self.config.parse(data,'')
            unit=self.runtime.usage.db.execute("SELECT value FROM meta WHERE key='reference_unit'").fetchone()
            require(unit is None or unit[0]==snapshot.profile.price.reference_unit,'reference_unit_mismatch')
            snapshot=self.config.write(data,fingerprint)
            self.runtime.usage.configuration(snapshot.profile.config_version,snapshot.fingerprint)
            self.finish(operation,'succeeded',{'code':'saved','config_version':snapshot.profile.config_version})
        except (OSError,ValueError,TypeError,KeyError,RecursionError,sqlite3.Error) as exc:
            code=str(exc) if isinstance(exc,ModelError) else 'model_save_unconfirmed'
            self.finish(operation,'failed' if isinstance(exc,ModelError) else 'unknown',{'code':code})
            raise ModelError(code) from None
        return self.settings()

    def probe(self, value):
        require(not self.error,'model_admin_unavailable')
        require(isinstance(value,dict) and set(value)=={'operation_id','guard','confirm'}
                and value['confirm']=='billable_capability_probe','model_probe_confirmation')
        # Idempotence is checked before current config/guard. An unknown result is never retried.
        existing=self.db.execute('SELECT config_version FROM actions WHERE id=?',(value['operation_id'],)).fetchone()
        if existing:
            self.begin(value,'probe',existing[0]);return self.settings()
        self.check_guard(value['guard'])
        snapshot=self.config.snapshot()
        self.begin(value,'probe',snapshot.profile.config_version)
        self.task=asyncio.create_task(self.queued_probe(value['operation_id'],snapshot))
        return self.settings()

    async def queued_probe(self, operation, snapshot):
        from .backend_protocol import ProtocolError
        try:
            await self.backend.models.run(self.runtime.usage.key('administrator_probe'),lambda deadline:self.run_probe(operation,snapshot))
        except (asyncio.CancelledError,asyncio.TimeoutError,ProtocolError,ModelError):
            # No automatic retry; a failed audit cannot change a business request's ledger.
            if not self.error: self.finish(operation,'unknown',{'code':'probe_interrupted'})

    async def run_probe(self, operation, snapshot):
        usage=self.runtime.usage;attempt=operation;started=False;reserved=False;began=time.monotonic()
        profile=replace(snapshot.profile,max_output_tokens=128)
        try:
            # A synthetic, non-business tool. The response is checked then discarded.
            tools=[{'type':'function','function':{'name':'capability_echo','description':'Return the supplied test marker.',
                    'parameters':{'type':'object','properties':{'marker':{'type':'string','enum':['taki-probe']}},
                                  'required':['marker'],'additionalProperties':False}}}]
            body=request_body(profile,[{'role':'user','content':'Call capability_echo once with marker taki-probe.'}],tools)
            request=hashlib.sha256(('admin-probe/'+operation).encode()).hexdigest()
            usage.reserve(request,attempt,usage.key('administrator_probe'),None,profile,4096+len(encode(body).encode()));reserved=True
            usage.start(attempt,profile);started=True
            result=await asyncio.wait_for(self.runtime.client.request(profile,body,timeout=15),16)
            usage.settle(attempt,result.usage,result.error_code)
            valid=result.status=='ok' and len(result.tool_calls)==1 and result.tool_calls[0].name=='capability_echo'
            if valid:
                try: valid=decode_json(result.tool_calls[0].arguments)=={'marker':'taki-probe'}
                except (ValueError,TypeError): valid=False
            passed=valid and result.usage.status=='known'
            self.finish(operation,'succeeded' if passed else 'failed',{'code':'probe_passed' if passed else 'capability_incomplete',
                'native_tool':valid,'usage_status':result.usage.status,'parallel_control_requested':profile.parallel_control,
                'provider':profile.provider,'model':profile.model,'config_version':profile.config_version,
                'attempt_id':attempt,'reference_only':True,'observed_at':time.time()})
        except (asyncio.CancelledError,asyncio.TimeoutError,ModelError,OSError) as exc:
            code=str(exc) if isinstance(exc,ModelError) else 'probe_interrupted'
            if started and usage.get(attempt)['state']=='started': usage.settle(attempt,Usage(),code)
            elif reserved: usage.release(attempt,code)
            self.finish(operation,'unknown',{'code':code,'attempt_id':attempt})
        finally:
            if reserved: usage.observe(attempt,'capability_probe',min(3600000,int((time.monotonic()-began)*1000)))

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        if self.db is not None: self.db.close();self.db=None
