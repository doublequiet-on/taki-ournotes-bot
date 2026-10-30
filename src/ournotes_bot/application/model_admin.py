# L3: Protected versioned model settings and explicit, billed capability probes.
# Effects: Private config, capability and group-service binding replacement; metadata-only audit, no credentials or text.
from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
import hashlib
import json
import os
from fractions import Fraction
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
            self.db.execute('CREATE TABLE IF NOT EXISTS budget(key TEXT PRIMARY KEY,value TEXT NOT NULL,revision TEXT NOT NULL)')
            self.db.execute('INSERT OR IGNORE INTO identity VALUES(?)',(identity,))
            # Never replay a paid probe or a configuration mutation after a crash.
            self.db.execute("UPDATE actions SET state='interrupted' WHERE state='intent'")
        row=self.db.execute("SELECT value FROM budget WHERE key='primary_daily_usd'").fetchone()
        if row:self.runtime.usage.primary_daily_micro=self.budget_amount(row[0])

    @staticmethod
    def budget_amount(value):
        require(isinstance(value,str) and re.fullmatch(r'\d{1,6}(?:\.\d{1,6})?',value),'primary_budget_configuration')
        return Fraction(value)*1000000

    def budget_settings(self):
        row=self.db.execute("SELECT value,revision FROM budget WHERE key='primary_daily_usd'").fetchone()
        amount=self.runtime.usage.primary_daily_micro
        return {'daily_usd':row[0] if row else format(float(amount/1000000),'.6f').rstrip('0').rstrip('.'),
                'guard':{'backend_epoch':self.backend.runtime,'revision':row[1] if row else 'environment'}}

    def save_budget(self,value):
        require(isinstance(value,dict) and set(value)=={'operation_id','guard','daily_usd'},'model_admin_shape')
        if not self.begin(value,'save_budget','budget'):return self.settings()
        try:
            require(value['guard']==self.budget_settings()['guard'],'model_config_conflict')
            amount=self.budget_amount(value['daily_usd'])
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO budget VALUES('primary_daily_usd',?,?)",(value['daily_usd'],value['operation_id']))
                self.db.execute("UPDATE actions SET state='succeeded',result=? WHERE id=?",(encode({'code':'saved'}),value['operation_id']))
            self.runtime.usage.primary_daily_micro=amount
        except (ModelError,sqlite3.Error) as exc:
            self.finish(value['operation_id'],'failed' if isinstance(exc,ModelError) else 'unknown',{'code':str(exc) if isinstance(exc,ModelError) else 'model_save_unconfirmed'})
            raise
        return self.settings()

    def guard(self, fingerprint):
        return {'backend_epoch':self.backend.runtime,'fingerprint':fingerprint}

    def check_guard(self, guard, config=None):
        _,fingerprint=(config or self.config).read()
        require(guard==self.guard(fingerprint),'model_config_conflict')

    def selected_config(self,fallback=False,proactive=False):
        require(not (fallback and proactive),'model_admin_shape')
        return self.runtime.proactive_config if proactive else self.runtime.fallback if fallback else self.config

    def settings(self, fallback=False, *, proactive=False):
        config=self.selected_config(fallback,proactive)
        require(not self.error,'model_admin_unavailable')
        profile=None;groups={};code='model_not_configured';fingerprint=None;key=False
        try:
            data,fingerprint=config.read()
            if data is not None:
                snapshot=config.parse(data,fingerprint,api_key='configuration-preview')
                profile=asdict(snapshot.profile);profile.pop('api_key')
                secret=data.get('secret',config.api_key)
                key=isinstance(secret,str) and 1<=len(secret)<=512 and not any(c.isspace() for c in secret)
                groups=dict(snapshot.groups);code='ready' if key else 'model_not_configured'
        except (OSError,ValueError,TypeError,KeyError,RecursionError):
            code='model_configuration' if config.path else 'model_not_configured'
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
        writable=config.writable()
        return {'role':'proactive' if proactive else 'fallback' if fallback else 'primary','code':code,'editable':writable and fingerprint is not None,
                'write_reason':None if writable else 'model_config_permissions','primary_budget':self.budget_settings(),
                'guard':self.guard(fingerprint),'profile':profile,'groups':groups,'key_configured':key,
                'endpoints':dict(ENDPOINTS),'api_url':('https://api.openai.com/v1/responses'
                    if profile.get('wire_profile')=='openai-responses-v1' else ENDPOINTS[profile['provider']]),
                'capability':self.latest_probe(profile['config_version'],proactive=proactive),
                'web_search':self.web_settings(proactive=proactive) if fingerprint is not None and not fallback and code=='ready' else None,
                'audit':self.audit(),'timezone':'Asia/Shanghai','price_status':config.status()}

    @staticmethod
    def metadata_digest(path, limit=16384):
        if not path.exists():return 'absent'
        require(path.is_file() and not path.is_symlink() and path.stat().st_size<=limit,'chat_capability_configuration')
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def web_context(self, proactive=False):
        config=self.selected_config(proactive=proactive)
        snapshot=config.snapshot()
        owner=self.runtime.proactive_capabilities if proactive else self.runtime.capabilities
        # Retain approved evidence when disabled. Old releases still read web_search=null.
        archive=type(owner)(owner.path.with_name(owner.path.stem+'.web-approved.json'))
        before=(self.metadata_digest(owner.path),self.metadata_digest(archive.path))
        current=owner.read(snapshot)
        proof=current if owner.web(snapshot) else archive.read(snapshot)
        verified=owner.web(snapshot) or archive.web(snapshot)
        guard={'backend_epoch':self.backend.runtime,'model_fingerprint':snapshot.fingerprint,
               'fingerprint':self.metadata_digest(owner.path),'approval_fingerprint':self.metadata_digest(archive.path)}
        require(before==(guard['fingerprint'],guard['approval_fingerprint']),'model_config_conflict')
        return snapshot,owner,archive,current,proof,verified,guard

    def web_settings(self, *, proactive=False):
        snapshot,owner,archive,current,proof,verified,guard=self.web_context(proactive)
        enabled=owner.web(snapshot) is not None
        parent=owner.path.parent
        writable=parent.is_dir() and not parent.is_symlink() and os.access(parent,os.W_OK) and (os.name=='nt' or parent.stat().st_mode & 0o022==0)
        return {'enabled':enabled,'code':'enabled' if enabled else 'disabled' if verified else 'unverified',
                'editable':bool(verified and writable),'guard':guard,'max_calls':verified.max_calls if verified else 1,
                'unit_cost_micro':verified.unit_cost_micro if verified else None,'currency':snapshot.profile.price.currency,
                'evidence_ref':verified.evidence_ref if verified else None}

    def write_web_metadata(self, path, data, operation, *, limit=16384):
        parent=path.parent
        require(parent.is_dir() and not parent.is_symlink() and os.access(parent,os.W_OK)
                and (os.name=='nt' or parent.stat().st_mode & 0o022==0),'model_config_permissions')
        self.metadata_digest(path,limit)
        raw=(encode(data)+'\n').encode();require(len(raw)<=limit,'chat_capability_configuration')
        temporary=path.with_name(path.name+'.'+operation+'.tmp')
        try:
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,path)
            if os.name!='nt':
                directory=os.open(parent,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            require(self.metadata_digest(path,limit)==hashlib.sha256(raw).hexdigest(),'model_config_save_unconfirmed')
        finally:temporary.unlink(missing_ok=True)

    def save_web(self, value, *, proactive=False):
        require(isinstance(value,dict) and set(value)=={'operation_id','guard','enabled','max_calls'}
                and type(value['enabled']) is bool and type(value['max_calls']) is int and 1<=value['max_calls']<=2,'model_admin_shape')
        action='web_proactive' if proactive else 'web_primary'
        if not self.begin(value,action,'web-search'):return self.settings(proactive=proactive)
        try:
            snapshot,owner,archive,current,proof,verified,guard=self.web_context(proactive)
            require(guard==value['guard'],'model_config_conflict')
            require(verified is not None and current is not None,'chat_capability_unverified')
            web={**proof['web_search'],'max_calls':value['max_calls']}
            saved={'schema':'chat-capabilities/1','config_version':snapshot.profile.config_version,
                   'fingerprint':snapshot.fingerprint,'web_search':web}
            # Save recovery evidence before disabling; a crash cannot discard the approval.
            require(self.web_context(proactive)[-1]==guard,'model_config_conflict')
            self.write_web_metadata(archive.path,saved,value['operation_id'])
            expected={**guard,'approval_fingerprint':self.metadata_digest(archive.path)}
            require(self.web_context(proactive)[-1]==expected,'model_config_conflict')
            self.write_web_metadata(owner.path,{**current,'web_search':web if value['enabled'] else None},value['operation_id'])
            self.finish(value['operation_id'],'succeeded',{'code':'saved','enabled':value['enabled'],'max_calls':value['max_calls']})
        except (OSError,ValueError,TypeError,KeyError,RecursionError,sqlite3.Error) as exc:
            code=str(exc) if isinstance(exc,ModelError) else 'model_save_unconfirmed'
            self.finish(value['operation_id'],'failed' if isinstance(exc,ModelError) else 'unknown',{'code':code})
            raise ModelError(code) from None
        return self.settings(proactive=proactive)

    def proactive_limits(self, group_id):
        from .chat_jobs import ChatServices,ProactiveLimits,_limits
        require(isinstance(group_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',group_id),'admin_group')
        snapshot=self.runtime.proactive_config.snapshot().for_group(group_id)
        owner=ChatServices(self.runtime.usage.path.parent,*self.config.namespace,filename='chat-proactive-services-v1.json')
        data,policies,digest=owner.read(snapshot)
        row=(data or {}).get('groups',{}).get(group_id,{})
        limits=asdict(_limits(row.get('proactive',asdict(ProactiveLimits(False,24,2,500000,600))),'proactive'))
        approved=group_id in policies
        proof=self.runtime.proactive_capabilities.proactive(snapshot,group_id)
        return {'group_id':group_id,'currency':snapshot.profile.price.currency,'limits':limits,
                'approved':approved,'capability_approved':proof,'eligible':self.runtime.agent is not None and approved and proof and limits['enabled'] and max(limits['max_calls_per_hour'],limits['max_replies_per_hour'])<=120,
                'guard':{'backend_epoch':self.backend.runtime,'fingerprint':digest,'model_fingerprint':snapshot.fingerprint}}

    def save_proactive_limits(self, group_id, value):
        from .chat_jobs import ChatServices,ServiceError,_limits
        require(isinstance(value,dict) and set(value)=={'operation_id','guard','limits'},'model_admin_shape')
        current=self.proactive_limits(group_id)
        if not self.begin({**value,'group_id':group_id},'save_proactive_limits',self.runtime.proactive_config.snapshot().profile.config_version):
            return self.proactive_limits(group_id)
        temporary=None
        try:
            require(value['guard']==current['guard'],'model_config_conflict')
            limits=_limits(value['limits'],'proactive')
            require(max(limits.max_calls_per_hour,limits.max_replies_per_hour)<=120,'proactive_limits_unsupported')
            snapshot=self.runtime.proactive_config.snapshot().for_group(group_id)
            owner=ChatServices(self.runtime.usage.path.parent,*self.config.namespace,filename='chat-proactive-services-v1.json')
            data,_,digest=owner.read(snapshot)
            require(digest==current['guard']['fingerprint'],'model_config_conflict')
            if data is None:
                data={'schema':'taki-chat-services/1','instance_id':self.config.namespace[0],'app_id':self.config.namespace[1],
                      'profile':{'config_version':snapshot.profile.config_version,'fingerprint':snapshot.fingerprint,
                                 'currency':snapshot.profile.price.currency},'groups':{}}
            require(group_id in data['groups'] or len(data['groups'])<128,'chat_service_configuration')
            row=data['groups'].setdefault(group_id,{'approved':False})
            # Limits never mint platform evidence, a service subject or an approval.
            row['proactive']=asdict(limits)
            raw=(encode(data)+'\n').encode()
            require(len(raw)<=131072,'chat_service_configuration')
            parent=owner.path.parent
            require(parent.is_dir() and not parent.is_symlink() and os.access(parent,os.W_OK)
                    and (os.name=='nt' or parent.stat().st_mode & 0o022==0),'model_config_permissions')
            temporary=owner.path.with_name(owner.path.name+'.'+value['operation_id']+'.tmp')
            fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            # Check both model and service bytes immediately before replacement.
            require(self.proactive_limits(group_id)['guard']==value['guard'],'model_config_conflict')
            os.replace(temporary,owner.path)
            if os.name!='nt':
                directory=os.open(parent,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            require(owner.read(snapshot)[2]==hashlib.sha256(raw).hexdigest(),'model_config_save_unconfirmed')
            self.finish(value['operation_id'],'succeeded',{'code':'saved'})
        except (OSError,ValueError,TypeError,KeyError,RecursionError,sqlite3.Error) as exc:
            code=str(exc) if isinstance(exc,(ModelError,ServiceError)) else 'model_save_unconfirmed'
            self.finish(value['operation_id'],'failed' if isinstance(exc,(ModelError,ServiceError)) else 'unknown',{'code':code})
            raise ModelError(code) from None
        finally:
            if temporary:temporary.unlink(missing_ok=True)
        return self.proactive_limits(group_id)

    def latest_probe(self, version, *, proactive=False):
        row=self.db.execute("SELECT id,state,result FROM actions WHERE action=? AND config_version=? ORDER BY created DESC LIMIT 1",('probe_proactive' if proactive else 'probe',version)).fetchone()
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
            if action not in ('probe','probe_proactive'):
                require(row['state']=='succeeded',json.loads(row['result'] or '{}').get('code','model_save_unconfirmed'))
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

    def approval_rebindings(self, before, after, *, proactive=False):
        """Carry existing exact-match evidence across tuning, never a model/key change."""
        from .chat_jobs import ChatServices
        tuned=('reasoning_effort','max_input_tokens','max_output_tokens','user_daily_limit',
               'group_daily_limit','user_rate_requests','group_rate_requests','rate_window_seconds')
        price=replace(before.profile.price,pricing_version=after.profile.price.pricing_version,
                      fetched_at=after.profile.price.fetched_at)
        if replace(before.profile,config_version=after.profile.config_version,price=price,
                   **{k:getattr(after.profile,k) for k in tuned})!=after.profile:return []
        owner=self.runtime.proactive_capabilities if proactive else self.runtime.capabilities
        archive=type(owner)(owner.path.with_name(owner.path.stem+'.web-approved.json'))
        plan=[]
        for cap in (owner,archive):
            digest=self.metadata_digest(cap.path)
            data=cap.read(before)
            if data is None:continue
            cap.web(before);cap.vision(before);cap.proactive(before,'')
            plan.append((cap.path,digest,{**data,'config_version':after.profile.config_version,
                                        'fingerprint':after.fingerprint},16384))
        services=ChatServices(self.runtime.usage.path.parent,*self.config.namespace,
                              filename='chat-proactive-services-v1.json' if proactive else 'chat-services-v1.json')
        digest=self.metadata_digest(services.path,131072)
        if digest!='absent':
            data=json.loads(services.path.read_text(encoding='utf-8'))
            require(isinstance(data,dict),'chat_service_configuration')
            binding=data.get('profile',{})
            require(isinstance(binding,dict),'chat_service_configuration')
            if binding.get('config_version')==before.profile.config_version and binding.get('fingerprint')==before.fingerprint:
                data,_,parsed_digest=services.read(before)
                require(digest==parsed_digest,'model_config_conflict')
                plan.append((services.path,digest,{**data,'profile':{**data['profile'],
                    'config_version':after.profile.config_version,'fingerprint':after.fingerprint}},131072))
        return plan

    def save(self, value, fallback=False, *, proactive=False):
        config=self.selected_config(fallback,proactive)
        require(isinstance(value,dict) and set(value)=={'operation_id','guard','profile','groups','api_key','api_url'},'model_admin_shape')
        operation=value['operation_id']
        if not self.begin(value,'save_proactive' if proactive else 'save_fallback' if fallback else 'save','console-'+str(operation)): return self.settings(fallback,proactive=proactive)
        try:
            self.check_guard(value['guard'],config)
            old,fingerprint=config.read()
            require(isinstance(value['profile'],dict),'model_configuration')
            profile=dict(value['profile'])
            require(not fallback or profile.get('provider')=='deepseek','fallback_configuration')
            expected_url=('https://api.openai.com/v1/responses' if profile.get('provider')=='openai'
                          and profile.get('wire_profile')=='openai-responses-v1' else ENDPOINTS.get(profile.get('provider')))
            require(value['api_url']==expected_url,'model_endpoint_unsupported')
            require(isinstance(value['api_key'],str),'model_configuration')
            require(not old or old['profile']['provider']==profile['provider'] or bool(value['api_key']),'provider_key_required')
            secret=value['api_key'] or (old.get('secret',config.api_key) if old else config.api_key)
            profile['config_version']='console-'+operation
            profile['price']={**profile['price'],'pricing_version':'default/'+operation,
                              'fetched_at':time.time(),'estimated':True,'source':'registered-default'}
            data={'schema':'taki-model-config/2','instance_id':config.namespace[0],'app_id':config.namespace[1],
                  'profile':profile,'secret':secret,'bindings':old['bindings'] if old else [],'groups':value['groups']}
            if old and old.get('schema')=='taki-model-config/3':
                data.update(schema='taki-model-config/3',group_admissions=old['group_admissions'])
            snapshot=config.parse(data,hashlib.sha256((encode(data)+'\n').encode()).hexdigest())
            rebindings=self.approval_rebindings(config.parse(old,fingerprint),snapshot,proactive=proactive) if old and not fallback and old.get('secret',config.api_key)==secret else []
            for path,digest,_,limit in rebindings:
                require(self.metadata_digest(path,limit)==digest,'model_config_conflict')
                require(path.parent.is_dir() and not path.parent.is_symlink() and os.access(path.parent,os.W_OK)
                        and (os.name=='nt' or path.parent.stat().st_mode & 0o022==0),'model_config_permissions')
            unit=self.runtime.usage.db.execute("SELECT value FROM meta WHERE key='reference_unit'").fetchone()
            require(unit is None or unit[0]==snapshot.profile.price.reference_unit,'reference_unit_mismatch')
            snapshot=config.write(data,fingerprint)
            self.runtime.usage.configuration(snapshot.profile.config_version,snapshot.fingerprint)
            try:
                for path,digest,rebound,limit in rebindings:
                    require(self.metadata_digest(path,limit)==digest,'model_config_conflict')
                    self.write_web_metadata(path,rebound,operation,limit=limit)
            except (OSError,ValueError,TypeError,KeyError,RecursionError):
                raise ModelError('model_approval_rebind_unconfirmed') from None
            self.finish(operation,'succeeded',{'code':'saved','config_version':snapshot.profile.config_version})
        except (OSError,ValueError,TypeError,KeyError,RecursionError,sqlite3.Error) as exc:
            code=str(exc) if isinstance(exc,ModelError) else 'model_save_unconfirmed'
            self.finish(operation,'failed' if isinstance(exc,ModelError) else 'unknown',{'code':code})
            raise ModelError(code) from None
        return self.settings(fallback,proactive=proactive)

    def probe(self, value, *, proactive=False):
        require(not self.error,'model_admin_unavailable')
        require(isinstance(value,dict) and set(value)=={'operation_id','guard','confirm'}
                and value['confirm']=='billable_capability_probe','model_probe_confirmation')
        # Idempotence is checked before current config/guard. An unknown result is never retried.
        existing=self.db.execute('SELECT config_version FROM actions WHERE id=?',(value['operation_id'],)).fetchone()
        if existing:
            self.begin(value,'probe_proactive' if proactive else 'probe',existing[0]);return self.settings(proactive=proactive)
        config=self.selected_config(proactive=proactive)
        self.check_guard(value['guard'],config)
        snapshot=config.snapshot()
        self.begin(value,'probe_proactive' if proactive else 'probe',snapshot.profile.config_version)
        self.task=asyncio.create_task(self.queued_probe(value['operation_id'],snapshot))
        return self.settings(proactive=proactive)

    async def queued_probe(self, operation, snapshot):
        from .backend_protocol import ProtocolError
        try:
            await self.backend.models.run(self.runtime.usage.key('administrator_probe'),lambda deadline:self.run_probe(operation,snapshot))
        except (asyncio.CancelledError,asyncio.TimeoutError,ProtocolError,ModelError):
            # No automatic retry; a failed audit cannot change a business request's ledger.
            if not self.error: self.finish(operation,'unknown',{'code':'probe_interrupted'})

    async def run_probe(self, operation, snapshot):
        usage=self.runtime.usage;attempt=operation;started=False;reserved=False;began=time.monotonic()
        profile=replace(snapshot.profile,max_output_tokens=(2048
            if snapshot.profile.wire_profile=='openai-responses-v1' else 128))
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
