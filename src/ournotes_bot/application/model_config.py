# L3: Protected backend-only configuration snapshots and explicitly evidenced identity bindings.
# Effects: Reads and atomically writes one protected administrator file; never probes or logs secrets.
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re

from ..domain.model_calls import ModelError, ModelProfile, Price, require
from ..sources.json_validation import decode_json


def group_subject(instance_id, app_id, conversation_id, user_id):
    """Stable identity for an explicitly admitted group's verified platform actor."""
    raw=json.dumps([instance_id,app_id,conversation_id,user_id],ensure_ascii=True,separators=(',',':'))
    return 'group/'+hashlib.sha256(raw.encode()).hexdigest()


@dataclass(frozen=True)
class ConfigSnapshot:
    profile: ModelProfile
    bindings: tuple
    fingerprint: str = ''
    groups: tuple = ()
    group_admissions: tuple = ()
    identity_namespace: tuple = ()

    def for_group(self, group_id):
        overrides=dict(self.groups).get(group_id)
        return replace(self,profile=replace(self.profile,**overrides)) if overrides else self

    def subject(self, request):
        key=(request.kind,request.conversation_id,request.user_id)
        matches=[subject for kind,conversation,user,subject in self.bindings if (kind,conversation,user)==key]
        if len(matches)==1:return matches[0]
        require(not matches and request.kind=='group' and request.conversation_id in self.group_admissions
                and len(self.identity_namespace)==2,'identity_unverified')
        return group_subject(*self.identity_namespace,request.conversation_id,request.user_id)


class ModelConfiguration:
    def __init__(self, path, api_key, instance_id, app_id):
        self.path=Path(path) if path else None
        self.api_key=api_key
        self.namespace=(instance_id,app_id)
        self.versions={}
        self.current=None
        self.code='model_not_configured'
        self.prices={}
        self.defaults={}

    def read(self):
        require(self.path is not None,'model_not_configured')
        if not self.path.exists(): return None, 'absent'
        info=self.path.lstat()
        require(self.path.is_file() and not self.path.is_symlink() and info.st_size<=131072,'model_configuration')
        with self.path.open('rb') as stream: raw=stream.read(131073)
        require(len(raw)<=131072,'model_configuration')
        return decode_json(raw), hashlib.sha256(raw).hexdigest()

    def parse(self, data, digest, *, api_key=None):
        require(isinstance(data,dict) and data.get('schema') in ('taki-model-config/1','taki-model-config/2','taki-model-config/3'),'model_configuration')
        require((data.get('instance_id'),data.get('app_id'))==self.namespace,'model_configuration')
        fields={'schema','instance_id','app_id','profile','bindings'}
        if data['schema'] in ('taki-model-config/2','taki-model-config/3'): fields|={'secret','groups'}
        if data['schema']=='taki-model-config/3':fields.add('group_admissions')
        require(set(data)==fields and isinstance(data['profile'],dict),'model_configuration')
        value=dict(data['profile'])
        require('api_key' not in value and isinstance(value.get('price'),dict),'model_configuration')
        value['price']=Price(**value['price'])
        require(value['price'].source=='registered-default' and value['price'].estimated,'pricing_configuration')
        profile=ModelProfile(**value,api_key=data.get('secret',self.api_key) if api_key is None else api_key)
        require(isinstance(data['bindings'],list) and len(data['bindings'])<=1000,'identity_configuration')
        bindings=[];seen=set()
        for binding in data['bindings']:
            require(isinstance(binding,dict) and set(binding)=={'kind','conversation_id','user_id','subject','evidence_ref'},'identity_configuration')
            require(binding['kind'] in ('group','c2c'),'identity_configuration')
            require(all(isinstance(binding[k],str) and 1<=len(binding[k])<=160 and not any(ord(c)<32 for c in binding[k])
                        for k in ('conversation_id','user_id','subject','evidence_ref')),'identity_configuration')
            require(re.fullmatch(r'[A-Za-z0-9_.:/-]+',binding['evidence_ref']),'identity_configuration')
            key=(binding['kind'],binding['conversation_id'],binding['user_id'])
            require(key not in seen,'identity_configuration');seen.add(key)
            bindings.append((*key,binding['subject']))
        groups=data.get('groups',{})
        require(isinstance(groups,dict) and len(groups)<=128,'model_group_configuration')
        for key,limits in groups.items():
            require(isinstance(key,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',key) and isinstance(limits,dict)
                    and set(limits)=={'group_daily_limit','group_rate_requests'},'model_group_configuration')
            replace(profile,**limits)  # Reuse the runtime's quota and rate contract.
        admissions=data.get('group_admissions',[])
        require(isinstance(admissions,list) and len(admissions)<=128,'group_admission_configuration')
        admitted=[]
        for row in admissions:
            require(isinstance(row,dict) and set(row)=={'conversation_id','evidence_ref'},'group_admission_configuration')
            group=row['conversation_id'];evidence=row['evidence_ref']
            require(isinstance(group,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',group)
                    and group not in admitted and isinstance(evidence,str)
                    and re.fullmatch(r'[A-Za-z0-9_.:/-]{1,160}',evidence),'group_admission_configuration')
            admitted.append(group)
        return ConfigSnapshot(profile,tuple(bindings),digest,tuple((k,dict(v)) for k,v in groups.items()),
                              tuple(admitted),self.namespace)

    def snapshot(self):
        if self.path is None: raise ModelError('model_not_configured')
        try:
            data,digest=self.read()
            snapshot=self.parse(data,digest);profile=snapshot.profile
            prior=self.versions.get(profile.config_version)
            require(prior is None or prior==digest,'config_version_reused')
            require(len(self.versions)<128 or prior is not None,'config_version_capacity')
            self.versions[profile.config_version]=digest
            self.defaults[profile.config_version]=profile.price
            price=self.prices.get(profile.config_version)
            if price is not None: profile=replace(profile,price=price)
            self.current=replace(snapshot,profile=profile);self.code='ready'
            return self.current
        except ModelError as exc:
            self.current=None;self.code=str(exc);raise
        except (OSError,ValueError,TypeError,KeyError,RecursionError):
            self.current=None;self.code='model_configuration'
            raise ModelError(self.code) from None

    def writable(self):
        if self.path is None:return False
        parent=self.path.parent
        try:
            return parent.is_dir() and not parent.is_symlink() and os.access(parent,os.W_OK) and (os.name=='nt' or parent.stat().st_mode & 0o022 == 0)
        except OSError:return False

    def write(self, data, expected):
        """Single backend writer; compare current bytes, then replace one private file."""
        from .usage_ledger import encode
        require(self.path is not None,'model_not_configured')
        _,actual=self.read()
        require(actual==expected,'model_config_conflict')
        raw=(encode(data)+'\n').encode()
        require(len(raw)<=131072,'model_configuration')
        self.parse(data,hashlib.sha256(raw).hexdigest())
        version=data['profile']['config_version'];prior=self.versions.get(version)
        require(prior is None or prior==hashlib.sha256(raw).hexdigest(),'config_version_reused')
        require(len(self.versions)<128 or prior is not None,'config_version_capacity')
        # Never follow an administrator-path symlink or silently create new directories.
        require(self.path.parent.is_dir() and not self.path.parent.is_symlink(),'model_configuration')
        require(self.writable(),'model_config_permissions')
        temporary=self.path.with_name(self.path.name+'.'+data['profile']['config_version']+'.tmp')
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        try:
            with os.fdopen(fd,'wb') as stream: stream.write(raw);stream.flush();os.fsync(stream.fileno())
            require(self.read()[1]==expected,'model_config_conflict')
            os.replace(temporary,self.path)
            if os.name!='nt':
                directory=os.open(self.path.parent,os.O_RDONLY)
                try: os.fsync(directory)
                finally: os.close(directory)
            require(self.read()[1]==hashlib.sha256(raw).hexdigest(),'model_config_save_unconfirmed')
        finally:
            temporary.unlink(missing_ok=True)
        return self.snapshot()

    async def refresh_price(self, client):
        try: snapshot=self.snapshot()
        except ModelError: return
        price=await client.price(replace(snapshot.profile,price=self.defaults[snapshot.profile.config_version]))
        self.prices[snapshot.profile.config_version]=price
        if self.current and self.current.profile.config_version==snapshot.profile.config_version:
            self.current=replace(self.current,profile=replace(self.current.profile,price=price))
        if len(self.prices)>128: del self.prices[next(iter(self.prices))]

    def status(self):
        profile=self.current.profile if self.current else None
        return {'code':self.code,'configured':bool(profile),'config_version':profile.config_version if profile else None,
                'provider':profile.provider if profile else None,'model':profile.model if profile else None,
                'reasoning_effort':profile.reasoning_effort if profile else None,
                'identity_bindings':len(self.current.bindings) if self.current else 0,
                'admitted_groups':len(self.current.group_admissions) if self.current else 0,
                'price_estimated':profile.price.estimated if profile else None,
                'pricing_version':profile.price.pricing_version if profile else None,
                'price_source':profile.price.source if profile else None,
                'price_observed_at':profile.price.fetched_at if profile else None,
                'currency':profile.price.currency if profile else None,
                'reference_unit':profile.price.reference_unit if profile else None}
