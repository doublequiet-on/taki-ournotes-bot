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


class ChatCapabilities:
    def __init__(self,path):
        self.path=Path(path)

    def web(self,snapshot):
        if not self.path.exists():return None
        require(self.path.is_file() and not self.path.is_symlink() and self.path.stat().st_size<=16384,'chat_capability_configuration')
        try: value=json.loads(self.path.read_text(encoding='utf-8'))
        except (ValueError,UnicodeError):raise ModelError('chat_capability_configuration') from None
        require(isinstance(value,dict) and set(value)=={'schema','config_version','fingerprint','web_search'},'chat_capability_configuration')
        require(value['schema']=='chat-capabilities/1','chat_capability_configuration')
        if value['config_version']!=snapshot.profile.config_version or value['fingerprint']!=snapshot.fingerprint:return None
        web=value['web_search']
        if web is None:return None
        require(isinstance(web,dict) and set(web)=={'max_calls','unit_cost_micro','currency','evidence_ref'},'chat_capability_configuration')
        require(snapshot.profile.wire_profile=='openai-responses-v1' and web['currency']==snapshot.profile.price.currency,
                'chat_capability_unverified')
        require(type(web['max_calls']) is int and 1<=web['max_calls']<=2
                and type(web['unit_cost_micro']) is int and 0<=web['unit_cost_micro']<=10**9
                and isinstance(web['evidence_ref'],str) and 1<=len(web['evidence_ref'])<=160,'chat_capability_configuration')
        return WebCapability(web['max_calls'],web['unit_cost_micro'],web['evidence_ref'])
