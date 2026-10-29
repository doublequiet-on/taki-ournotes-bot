# L3: Administrator-owned evidence for optional provider capabilities; no probes.
# Effects: Reads one bounded local metadata file, never credentials or model HTTP.
import json
from dataclasses import dataclass
from pathlib import Path

from ..domain.model_calls import ModelError, require


@dataclass(frozen=True)
class WebCapability:
    max_calls: int
    unit_cost_micro: int
    evidence_ref: str


@dataclass(frozen=True)
class VisionCapability:
    max_image_tokens: int
    detail: str
    evidence_ref: str


class ChatCapabilities:
    def __init__(self,path):
        self.path=Path(path)

    def read(self,snapshot):
        if not self.path.exists():return None
        require(self.path.is_file() and not self.path.is_symlink() and self.path.stat().st_size<=16384,'chat_capability_configuration')
        try: value=json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError,UnicodeError):raise ModelError('chat_capability_configuration') from None
        require(isinstance(value,dict) and value.get('schema') in ('chat-capabilities/1','chat-capabilities/2','chat-capabilities/3'),'chat_capability_configuration')
        fields={'schema','config_version','fingerprint','web_search'}
        if value['schema'] in ('chat-capabilities/2','chat-capabilities/3'):fields.add('vision')
        if value['schema']=='chat-capabilities/3':fields.add('proactive')
        require(set(value)==fields,'chat_capability_configuration')
        if value['config_version']!=snapshot.profile.config_version or value['fingerprint']!=snapshot.fingerprint:return None
        return value

    def web(self,snapshot):
        value=self.read(snapshot)
        if value is None:return None
        web=value['web_search']
        if web is None:return None
        require(isinstance(web,dict) and set(web)=={'max_calls','unit_cost_micro','currency','evidence_ref'},'chat_capability_configuration')
        require(snapshot.profile.wire_profile=='openai-responses-v1' and web['currency']==snapshot.profile.price.currency,
                'chat_capability_unverified')
        require(type(web['max_calls']) is int and 1<=web['max_calls']<=2
                and type(web['unit_cost_micro']) is int and 0<=web['unit_cost_micro']<=10**9
                and isinstance(web['evidence_ref'],str) and 1<=len(web['evidence_ref'])<=160,'chat_capability_configuration')
        return WebCapability(web['max_calls'],web['unit_cost_micro'],web['evidence_ref'])

    def vision(self,snapshot):
        value=self.read(snapshot)
        if value is None or value.get('vision') is None:return None
        vision=value['vision']
        require(isinstance(vision,dict) and set(vision)=={'max_image_tokens','detail','evidence_ref'},'chat_capability_configuration')
        require(snapshot.profile.wire_profile=='openai-responses-v1','chat_capability_unverified')
        require(type(vision['max_image_tokens']) is int and 1<=vision['max_image_tokens']<=131072
                and vision['detail'] in ('low','high') and isinstance(vision['evidence_ref'],str)
                and 1<=len(vision['evidence_ref'])<=160,'chat_capability_configuration')
        return VisionCapability(**vision)

    def proactive(self,snapshot,group_id):
        value=self.read(snapshot)
        if value is None or value.get('proactive') is None:return False
        proof=value['proactive']
        require(isinstance(proof,dict) and set(proof)=={'groups','receive_evidence_ref','send_evidence_ref'},'chat_capability_configuration')
        require(isinstance(proof['groups'],list) and len(proof['groups'])<=128 and all(isinstance(gid,str) and 1<=len(gid)<=128 for gid in proof['groups'])
                and all(isinstance(proof[key],str) and 1<=len(proof[key])<=160 for key in ('receive_evidence_ref','send_evidence_ref')),'chat_capability_configuration')
        return group_id in proof['groups']
