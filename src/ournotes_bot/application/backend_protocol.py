# L3: Strict local wire contracts -> validated immutable request; no SDK/data access.
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, asdict, field
from ..domain.chat_images import ImageError,parse_attachments
from ..domain.query_policy import ExecutionPolicy
from .backend_sources import TRANSPORT_PROTOCOL

PROTOCOL = TRANSPORT_PROTOCOL
CAPABILITY = 'qq-query-chat-v3'
WINDOW = 298.0


class ProtocolError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def require(condition, code, status=400):
    if not condition:
        raise ProtocolError(code, status)


def identity(value):
    return (isinstance(value, str) and 0 < len(value) <= 256 and value.strip() == value
            and not any(ord(c) < 32 or ord(c) == 127 for c in value))


def require_protocol(value):
    require(isinstance(value,dict) and set(value) == {'major','minor'}
            and all(type(value[k]) is int for k in ('major','minor')) and value == PROTOCOL,
            'protocol_version',409)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':'),
                                     sort_keys=isinstance(value, dict)).encode()).hexdigest()


@dataclass(frozen=True)
class Request:
    request_id: str
    instance_id: str
    app_id: str
    platform: str
    kind: str
    conversation_id: str
    user_id: str
    message_id: str
    timestamp_ms: int
    body: str
    capability: str
    gateway_epoch: str
    ingress_seq: int
    policy: ExecutionPolicy = ExecutionPolicy()
    trigger: str = 'command'
    reference: dict | None = None
    interaction: dict | None = None
    sender_name: str = ''
    attachments: tuple = field(default=(),repr=False)

    @classmethod
    def parse(cls, value, instance_id, app_id, now, *, reference_profiles=()):
        fields = set(cls.__dataclass_fields__)
        optional={'sender_name','attachments'}
        require(isinstance(value,dict) and fields-optional | {'protocol'}<=set(value)<=fields | {'protocol'},'request_shape')
        value={**value,'sender_name':value.get('sender_name','')}
        try:value['attachments']=parse_attachments(value.get('attachments'))
        except ImageError as exc:raise ProtocolError(exc.code) from None
        require(value['sender_name']=='' or identity(value['sender_name']),'sender_name')
        require_protocol(value['protocol'])
        require(value['instance_id'] == instance_id and value['app_id'] == app_id, 'instance_mismatch', 403)
        for key in fields - {'timestamp_ms', 'body', 'ingress_seq', 'policy', 'reference', 'interaction','sender_name','attachments'}:
            require(identity(value[key]), 'invalid_identity')
        require(value['platform'] == 'qq' and value['kind'] in ('group', 'c2c'), 'conversation_kind')
        require(value['kind'] != 'c2c' or value['conversation_id'] == value['user_id'], 'c2c_identity')
        require(value['capability'] == CAPABILITY, 'capability_profile', 409)
        require(type(value['timestamp_ms']) is int and 0 <= value['timestamp_ms'] < 10**14, 'message_time')
        require(type(value['ingress_seq']) is int and 0 < value['ingress_seq'] < 2**53, 'ingress_seq')
        require(isinstance(value['body'],str) and len(value['body'].encode())<=16384 and (value['body'] or value['attachments']),'body_size')
        require(value['trigger'] in ('command','ask','quoted_reply','button'), 'trigger')
        ref, interaction = value['reference'], value['interaction']
        if value['trigger'] == 'quoted_reply':
            require(isinstance(ref, dict) and set(ref) == {'message_id','adapter_profile_version'}
                    and identity(ref['message_id']) and identity(ref['adapter_profile_version'])
                    and ref['adapter_profile_version'] in reference_profiles,
                    'reference_profile')
        else:
            require(ref is None, 'reference_shape')
        # No live button profile has been verified; do not accept arbitrary interaction data.
        require(value['trigger'] != 'button' and interaction is None, 'interaction_profile')
        policy = value['policy']
        require(isinstance(policy, dict) and set(policy) == set(ExecutionPolicy.__dataclass_fields__), 'policy_shape')
        require(policy['natural_query'] in ('off','local','model') and identity(policy['epoch']), 'policy_value')
        require(all(type(policy[k]) is int and 0 <= policy[k] < 2**53 for k in ('global_revision','group_revision')), 'policy_revision')
        expected = digest([instance_id, app_id, 'qq', value['kind'], value['conversation_id'], value['user_id'], value['message_id']])
        require(value['request_id'] == expected, 'request_identity')
        # Age is checked after dedupe by the coordinator, so retries can read expired facts.
        return cls(**{key: value[key] for key in fields - {'policy'}}, policy=ExecutionPolicy(**policy))

    @property
    def content_digest(self):
        return digest({key: value for key, value in asdict(self).items()
                       if key not in ('gateway_epoch', 'ingress_seq')})

    @property
    def context_id(self):
        return digest([self.instance_id, self.app_id, self.platform, self.kind, self.conversation_id, self.user_id])

    @property
    def scope(self):
        return {key: getattr(self, key) for key in ('instance_id','app_id','platform','kind','conversation_id')}


def event(value):
    require(isinstance(value, dict), 'event_shape')
    common = {'protocol', 'request_id', 'plan_version', 'gateway_epoch', 'event', 'part_id', 'attempt_id'}
    legacy_ack = value.get('protocol') == {'major':1,'minor':1} and value.get('event') == 'ack'
    if legacy_ack:
        require(all(type(value['protocol'][k]) is int for k in ('major','minor')), 'protocol_version', 409)
        value = {**value, 'allow_context': False}
    else:
        require_protocol(value.get('protocol'))
    kind = value.get('event')
    extra = {'result', 'message_id'} if kind == 'ack' else set()
    if kind == 'ack' and 'allow_context' in value:
        require(type(value['allow_context']) is bool, 'context_permission')
        extra.add('allow_context')
    require(kind in ('intent', 'ack', 'upload_failed', 'aborted', 'batch_finished')
            and set(value) == common | extra, 'event_shape')
    require(value['plan_version'] == 1 and type(value['plan_version']) is int, 'plan_version', 409)
    for key in ('request_id', 'gateway_epoch', 'part_id', 'attempt_id'):
        require(identity(value[key]), 'invalid_identity')
    if kind == 'ack':
        require(value['result'] in ('success', 'failure', 'uncertain', 'aborted'), 'receipt_status')
        require(identity(value['message_id']) if value['result'] == 'success' else value['message_id'] is None,
                'receipt_message')
    return value
