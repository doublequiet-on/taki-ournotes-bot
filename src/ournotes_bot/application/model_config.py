# L3: Protected backend-only configuration snapshots and explicitly evidenced identity bindings.
# Effects: Reads one bounded administrator file; never writes keys, sends probes or logs content.
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from pathlib import Path
import re

from ..domain.model_calls import ModelError, ModelProfile, Price, require
from ..sources.json_validation import decode_json


@dataclass(frozen=True)
class ConfigSnapshot:
    profile: ModelProfile
    bindings: tuple
    fingerprint: str = ''

    def subject(self, request):
        key=(request.kind,request.conversation_id,request.user_id)
        matches=[subject for kind,conversation,user,subject in self.bindings if (kind,conversation,user)==key]
        require(len(matches)==1,'identity_unverified')
        return matches[0]


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

    def snapshot(self):
        if self.path is None: raise ModelError('model_not_configured')
        try:
            with self.path.open('rb') as stream: raw=stream.read(131073)
            require(len(raw)<=131072,'model_configuration')
            data=decode_json(raw)
            require(isinstance(data,dict) and data.get('schema')=='taki-model-config/1','model_configuration')
            require((data.get('instance_id'),data.get('app_id'))==self.namespace,'model_configuration')
            fields={'schema','instance_id','app_id','profile','bindings'}
            require(set(data)==fields and isinstance(data['profile'],dict),'model_configuration')
            value=dict(data['profile'])
            require('api_key' not in value and isinstance(value.get('price'),dict),'model_configuration')
            value['price']=Price(**value['price'])
            require(value['price'].source=='registered-default' and value['price'].estimated,'pricing_configuration')
            profile=ModelProfile(**value,api_key=self.api_key)
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
            digest=hashlib.sha256(raw).hexdigest()
            prior=self.versions.get(profile.config_version)
            require(prior is None or prior==digest,'config_version_reused')
            require(len(self.versions)<128 or prior is not None,'config_version_capacity')
            self.versions[profile.config_version]=digest
            self.defaults[profile.config_version]=profile.price
            price=self.prices.get(profile.config_version)
            if price is not None: profile=replace(profile,price=price)
            self.current=ConfigSnapshot(profile,tuple(bindings),digest);self.code='ready'
            return self.current
        except ModelError as exc:
            self.current=None;self.code=str(exc);raise
        except (OSError,ValueError,TypeError,KeyError,RecursionError):
            self.current=None;self.code='model_configuration'
            raise ModelError(self.code) from None

    async def refresh_price(self, client):
        try: snapshot=self.snapshot()
        except ModelError: return
        price=await client.price(replace(snapshot.profile,price=self.defaults[snapshot.profile.config_version]))
        self.prices[snapshot.profile.config_version]=price
        if len(self.prices)>128: del self.prices[next(iter(self.prices))]

    def status(self):
        profile=self.current.profile if self.current else None
        return {'code':self.code,'configured':bool(profile),'config_version':profile.config_version if profile else None,
                'provider':profile.provider if profile else None,'model':profile.model if profile else None,
                'identity_bindings':len(self.current.bindings) if self.current else 0,
                'price_estimated':profile.price.estimated if profile else None,
                'pricing_version':profile.price.pricing_version if profile else None}
