# L3: Host integration for ephemeral participation and approved group-service billing.
# Effects: Bounded observations, scoped receipt history and usage reads; receipts alone persist dialogue.
# Input: Optional backend allowlist for approved lightweight proactive groups.
import asyncio
import time
import json
import math
import os
import re
import sqlite3
import unicodedata
from collections import Counter,OrderedDict
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
from hashlib import sha256
from types import SimpleNamespace

from ..domain.chat import ChatMessage
from ..domain.chat_images import ImageError
from ..domain.model_calls import ModelError,require
from .chat_jobs import (
    ChatServices, ServiceError, lightweight_minimum_cost_micro, lightweight_profile, maximum_cost_micro,
    proactive_minimum_cost_micro, service_request_id, uses_proactive_day_accounting,
)
from .proactive_chat import ProactiveController, ProactivePolicy, select_topic_context, topic_keys
from .chat_followup import Followups
from .persona import (contextual_just_evidence_route, explicit_evidence_route,
                      lightweight_image_reference, lightweight_needs_evidence, public_lookup_candidate)
from . import replies


def _echo_key(text: str) -> str:
    folded = unicodedata.normalize('NFKC', text).casefold()
    return ' '.join(folded.split()).rstrip(' .!?。…')


_INTERJECTION = re.compile(r'(?:哈{2,}|笑死|围观|路过|插个楼|冒个泡|你们继续|吃瓜|\+1|\[表情\])')
_REACTION_EMOJI = re.compile(r'(?:[😂🤣😆😹👀]\ufe0f?\s*)+')
_TURN_CLOSED = re.compile(r'(?:已经有人回答了|(?:我|他|她|我们)?(?:已经|刚刚|刚才)?'
                          r'(?:答完了|解决了|搞定了|知道了|明白了|懂了)|不用(?:再)?答了|不用了|先这样|算了)')
_TOPIC_SHIFT = re.compile(r'^(?:换个话题|说个别的|先不说这个|聊点别的|打断一下[，,:：])')
_SUPPLEMENT = re.compile(r'^(?:补充|再补|漏说|忘了说|我说的是|我指的是|更正|纠正|我的意思是)')
_ANSWER = re.compile(r'^(?:答案是|选[的是]|直接选|原因是|因为|可以用|用.+就行)')
_ANSWER_UNCERTAIN = re.compile(r'[?？]|是不是|会不会|能不能|可不可以|要不要|有没有|能否|是否|不确定'
                               r'|(?:吗|么|吧)[。.!！…]*$')
_WAITING = re.compile(r'(?:(?:等我|我马上|我一会儿|我这就|等下我)(?:再|去)?'
                      r'(?:发(?:个图|张图|截图|图片|视频|出来|一下)?'
                      r'|补(?:充)?(?:一句|一条|一段|一下)?'
                      r'|贴(?:个图|张图|截图|图片|出来|一下)?'
                      r'|说(?:一句|一下|完)?)'
                      r'|我还没说完|等我说完|先听我说完)')
_WEB_URL = re.compile(r'[a-z][a-z0-9+.-]*://|www\.', re.I)
_REPLY_KINDS = ('react', 'opinion', 'joke', 'question')


def _observation_turn(target, request, *, lightweight=False):
    """Resolve explicit conversation cues before the model evaluates a new scene."""
    if request.attachments or (target is None and not lightweight):
        return 'advance'
    text = _echo_key(request.body).strip('，,！!？?~～')
    if _TOPIC_SHIFT.match(text):
        return 'shift'
    if (lightweight and (target is None or request.user_id == target.speaker_id)
            and not request.body.rstrip().endswith(('?', '？')) and _WAITING.fullmatch(text)):
        return 'waiting'
    if lightweight and text == '已经有人回答了':
        return 'answered'
    if lightweight and target is None and _TURN_CLOSED.fullmatch(text):
        return 'closed'
    if (lightweight and _REACTION_EMOJI.fullmatch(text) and (target is None or request.user_id != target.speaker_id)
            and not (request.reference or request.quoted_text or request.quote_hint)):
        return 'interjection'
    if target is None:
        return 'advance'
    previous = _echo_key(target.text)
    same_speaker = request.user_id == target.speaker_id
    quoted = bool(request.reference and request.reference['message_id'] == target.message_id)
    if _TURN_CLOSED.fullmatch(text) and (same_speaker or quoted or text == '已经有人回答了'):
        return 'closed'
    if not same_speaker and _ANSWER.match(text) and quoted:
        return 'closed'
    if (not same_speaker and not (request.reference or request.quoted_text or request.quote_hint)
            and _INTERJECTION.fullmatch(text)):
        return 'interjection'
    if _SUPPLEMENT.match(text) and (same_speaker or quoted):
        return 'supplement'
    if lightweight:
        common = topic_keys(text) & topic_keys(previous)
        if (not same_speaker and target.text.rstrip().endswith(('?', '？'))
                and _ANSWER.match(text) and common and not _ANSWER_UNCERTAIN.search(request.body.strip())
                and (text.startswith('答案是') or any(len(key) >= 3 for key in common))):
            return 'answered'
        if same_speaker and text != previous and any(len(key) >= 3 for key in common):
            return 'supplement'
    return 'advance'


def _ack_by_source(saved, current_ms):
    return {row.reply_to: row for row in saved.values()
            if row.role == 'assistant' and row.timestamp_ms <= current_ms}


def _confirmed_addressed_source(position, saved, ack_by_source):
    source = saved.get(position.message_id)
    if (source is not None and source.role == 'user' and source.speaker_id == position.speaker_id
            and source.timestamp_ms == position.timestamp_ms and position.message_id in ack_by_source):
        return source
    return None


@dataclass(frozen=True)
class _ObservationReceipt:
    gateway_epoch: str
    observation_seq: int
    content_digest: str
    result: tuple[bool, int, bool]
    covered_seq: int
    addressed_prefix: tuple[str, ...]


class ProactiveChat:
    @staticmethod
    def parse_staged_decision(text):
        """Validate the decision-only wire contract without changing participation state."""
        try:value=json.loads(text)
        except (ValueError,TypeError):raise ModelError('model_protocol_error') from None
        require(isinstance(value,dict) and set(value)=={'participation','text','delay_seconds'},'model_protocol_error')
        require(value['participation'] in ('reply','skip','defer') and value['text']=='','model_protocol_error')
        delay=value['delay_seconds']
        require((type(delay) is int and 2<=delay<=5) if value['participation']=='defer'
                else delay is None,'model_protocol_error')
        return value

    @staticmethod
    def response_format(*, one_pass=False, lookup=False):
        properties = {
            'participation': {'type': 'string', 'enum': ['reply', 'skip', 'defer']},
            'text': {'type': 'string'},
            'delay_seconds': {'type': ['integer', 'null']},
        }
        if one_pass:
            properties.update(
                target_message_id={'type': ['string', 'null']},
                anchor_message_ids={'type': 'array', 'items': {'type': 'string'}, 'maxItems': 2},
                reply_kind={'type': ['string', 'null'], 'enum': [*_REPLY_KINDS, None]},
            )
        else:
            properties['text']['enum'] = ['']
        if lookup:
            properties['public_query']={'type':'string'}
            properties['clarification']={'type':'string'}
        return {'type': 'json_schema', 'name': 'proactive_one_pass' if one_pass else 'proactive_participation',
                'strict': True, 'schema': {'type': 'object', 'properties': properties,
                    'required': list(properties), 'additionalProperties': False}}

    @staticmethod
    def parse_one_pass(text, target_id, visible_ids):
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            raise ModelError('model_protocol_error') from None
        require(isinstance(value, dict) and set(value) == {
            'participation', 'text', 'delay_seconds', 'target_message_id', 'anchor_message_ids', 'reply_kind',
        }, 'model_protocol_error')
        mode, body, delay = value['participation'], value['text'], value['delay_seconds']
        target, anchors, kind = value['target_message_id'], value['anchor_message_ids'], value['reply_kind']
        require(mode in ('reply', 'skip', 'defer') and isinstance(body, str), 'model_protocol_error')
        if mode != 'reply':
            require(body == '' and target is None and anchors == [] and kind is None, 'model_protocol_error')
            require((type(delay) is int and 2 <= delay <= 5) if mode == 'defer' else delay is None,
                    'model_protocol_error')
            return value
        require(delay is None and target == target_id and kind in _REPLY_KINDS, 'model_protocol_error')
        require(isinstance(anchors, list) and len(anchors) <= 2 and all(
            isinstance(anchor, str) and anchor != target_id and anchor in visible_ids for anchor in anchors
        ) and len(anchors) == len(set(anchors)), 'model_protocol_error')
        body = body.strip()
        require(0 < len(body) <= 90 and body.splitlines() == [body] and '<TAKI_SKIP>' not in body
                and not any(ord(char) < 32 for char in body), 'model_protocol_error')
        question_marks = body.count('?') + body.count('？')
        require(question_marks <= 1, 'model_protocol_error')
        if kind == 'question':
            require(question_marks == 1 and body.endswith(('?', '？')), 'model_protocol_error')
        return {**value, 'text': body}

    def __init__(self,agent):
        self.agent=agent
        self.controller=ProactiveController()
        self.followups=Followups(clock=lambda:self.controller.clock(),on_expire=self.window_expired)
        self.addressed=Counter()
        self.control_times=OrderedDict()
        self.observation_responses=OrderedDict()
        self.lightweight_groups = frozenset(
            group_id.strip() for group_id in os.getenv('OURNOTES_PROACTIVE_LIGHTWEIGHT_GROUPS', '').split(',')
            if group_id.strip()
        )
        settings = agent.runtime.backend.settings
        compact_group = getattr(settings, 'hermes_pilot_group', '') if getattr(settings, 'hermes_compact_standby', False) else ''
        one_pass = os.getenv('OURNOTES_PROACTIVE_ONE_PASS_GROUPS', compact_group)
        self.one_pass_groups = self.lightweight_groups.intersection(group_id.strip() for group_id in one_pass.split(','))
        self.services=ChatServices(agent.runtime.usage.path.parent,agent.runtime.backend.instance_id,
            agent.runtime.backend.app_id,filename='chat-proactive-services-v1.json',authority=agent.runtime.admin.db,require_authority=True)

    def control_event(self,request,status,**data):
        backend=self.agent.runtime.backend
        now=backend.clock()
        if status.startswith('chat_service_') or status in ('excluded_user','followup_disabled','platform_unapproved','service_disabled','budget_exhausted','conversation_busy'):
            key=(request.conversation_id,status)
            if key in self.control_times and now-self.control_times[key]<10:return
            self.control_times[key]=now;self.control_times.move_to_end(key)
            while len(self.control_times)>1024:self.control_times.popitem(last=False)
        backend.diagnostics.conversation_event(request.request_id,request.conversation_id,'control',now,status=status,**data)

    def window_expired(self,scope,question):
        backend=self.agent.runtime.backend
        backend.diagnostics.conversation_event(question.request_id,scope[-1],'control',backend.clock(),status='window_expired')

    def trace(self,work,kind,**data):
        if work.proactive is None:return
        backend=self.agent.runtime.backend
        backend.diagnostics.chat_event(work.request.request_id,kind,backend.clock(),**data)

    def owners(self):
        runtime=self.agent.runtime
        return ((self.services,runtime.proactive_capabilities) if runtime.dedicated_proactive()
                else (self.agent.services,runtime.capabilities))

    def settings(self,group_id):
        row=self.agent.runtime.admin.db.execute('SELECT value FROM chat_settings WHERE group_id=?',(group_id,)).fetchone()
        return json.loads(row['value']) if row else dict(followup_enabled=True,followup_window_seconds=180,coalesce_seconds=1.5,excluded_users=[])

    def settings_changed(self,group_id,before,after):
        scope=(self.agent.runtime.backend.instance_id,self.agent.runtime.backend.app_id,'qq','group',group_id)
        excluded={row['user_id'] for row in after['excluded_users']}
        if not after['followup_enabled']:self.followups.clear(scope)
        elif before['followup_window_seconds']!=after['followup_window_seconds']:
            self.followups.resize(scope,after['followup_window_seconds'])
        group=self.controller.groups.get(scope)
        if group:
            group.rows[:]=[row for row in group.rows if row.message.speaker_id not in excluded
                or row.message.speaker_id==group.continuation_user_id]
        for work in tuple(self.agent.runtime.backend.works.values()):
            if work.request.conversation_id!=group_id or work.proactive is None:continue
            if ((work.continuation and not after['followup_enabled']) or (not work.continuation
                    and any(row.speaker_id in excluded for row in work.proactive.messages))):
                self.agent.runtime.backend.finish(work,'aborted',reason='chat_settings_changed')
                self.controller.cancel(scope)

    def platform_allowed(self,request):
        runtime=self.agent.runtime
        # Receive/send evidence is independent of the optional proactive service switch.
        primary=runtime.config.snapshot().for_group(request.conversation_id)
        if runtime.capabilities.proactive(primary,request.conversation_id):return True
        if runtime.dedicated_proactive():
            snapshot=runtime.proactive_snapshot(request.conversation_id)
            return bool(runtime.proactive_capabilities.proactive(snapshot,request.conversation_id))
        return False

    def sweep_followups(self):
        self.followups.sweep()
        for scope,user_id in tuple(self.followups.questions):
            request=SimpleNamespace(conversation_id=scope[-1])
            try:
                if not self.settings(scope[-1])['followup_enabled'] or not self.platform_allowed(request):
                    self.followups.clear(scope,user_id)
            except (ModelError,ValueError,OSError,sqlite3.Error):self.followups.clear(scope,user_id)

    @staticmethod
    def scope(request):return tuple(request.scope.values())

    def policy(self,snapshot,request):
        services,capabilities=self.owners()
        policy=services.policy(snapshot,request.conversation_id)
        if policy is None or not policy.proactive.enabled:return None
        if not capabilities.proactive(snapshot,request.conversation_id):return None
        if max(policy.proactive.max_calls_per_hour,policy.proactive.max_replies_per_hour)>120:return None
        return policy

    def uses_lightweight(self, group_id, profile, policy):
        return (group_id in self.lightweight_groups and policy is not None and uses_proactive_day_accounting(policy)
                and profile.wire_profile == 'openai-responses-v1' and profile.model == 'gpt-6.1-terra')

    def lightweight_reservation(self, group_id, profile):
        if group_id in self.one_pass_groups:
            return math.ceil(maximum_cost_micro(lightweight_profile(profile, True)))
        return lightweight_minimum_cost_micro(profile)

    def lightweight_status(self, group_id):
        if group_id not in self.lightweight_groups:
            return {'status': 'off'}
        try:
            snapshot = self.agent.runtime.proactive_snapshot(group_id)
        except ModelError:
            return {'status': 'blocked'}
        policy = self.policy(snapshot, SimpleNamespace(conversation_id=group_id))
        if not self.uses_lightweight(group_id, snapshot.profile, policy):
            return {'status': 'blocked'}
        return {'status': 'ready', 'reserve_micro': self.lightweight_reservation(group_id, snapshot.profile)}

    def configured(self,request,snapshot,*,continuation=False):
        scope=self.scope(request)
        if continuation:
            # The regular user/group ledger enforces paid calls for this lane.
            policy=ProactivePolicy(True,'continuation','explicit-dialogue',120,120,10**15,cooldown_seconds=0)
            self.controller.configure(scope,policy)
            self.controller.groups[scope].coalesce_seconds=self.settings(request.conversation_id)['coalesce_seconds']
            self.controller.groups[scope].continuation_user_id=request.user_id
            return None
        policy=self.policy(snapshot,request)
        if policy is None and scope not in self.controller.groups:return None
        limits=policy.proactive if policy else None
        self.controller.configure(scope,ProactivePolicy(bool(policy),policy.service_subject_id if policy else '',
            policy.approval_ref if policy else '',min(limits.max_calls_per_hour,limits.max_evaluations_per_hour or limits.max_calls_per_hour) if limits else 6,
            limits.max_replies_per_hour if limits else 3,limits.max_cost_micro_per_hour if limits else 0,
            cooldown_seconds=limits.cooldown_seconds if limits else 60))
        self.controller.groups[scope].coalesce_seconds=None
        self.controller.groups[scope].continuation_user_id=None
        if self.controller.status(scope)['addressed_active']!=bool(self.addressed[scope]):
            self.controller.set_addressed(scope,bool(self.addressed[scope]))
        return policy

    def continuation_owner(self,scope):
        group=self.controller.groups.get(scope)
        if (group is None or group.continuation_user_id is None or not group.rows
                or group.active is None and not group.dirty and group.deferred_at is None):return None
        followup=self.followups.match(scope,group.rows[-1].message.message_id)
        return group.continuation_user_id if followup else None

    def observe(self,request,*,ingress_gap=False):
        if request.kind!='group' or request.policy.natural_query!='model':return False
        try:
            scope=self.scope(request);settings=self.settings(request.conversation_id)
            reply_to = request.reference['message_id'] if request.reference else None
            if not self.agent.store.source_valid(request.scope,request.message_id,request.timestamp_ms):return False
            owner=self.continuation_owner(scope)
            self.followups.observe(scope,request.user_id,request.message_id,request.timestamp_ms,request.ingress_seq,request.gateway_epoch)
            if owner is not None and owner!=request.user_id:
                self.control_event(request,'conversation_busy');return False
            if owner==request.user_id:
                for work in tuple(self.agent.runtime.backend.works.values()):
                    if (work.active and work.continuation and work.request.scope==request.scope
                            and work.request.user_id==request.user_id and work.request.ingress_seq<request.ingress_seq
                            and work.request.message_id!=request.message_id):
                        self.agent.runtime.backend.finish(work,'aborted',reason='continuation_replaced')
            if replies.recognizes_message(request.body) and not replies.is_ai_request(request.body):
                self.followups.clear(scope,request.user_id);self.controller.cancel(scope);return False
            active=next((work for work in reversed(tuple(self.agent.runtime.backend.works.values()))
                if work.active and work.chat and work.proactive is None and work.request.scope==request.scope
                and work.request.user_id==request.user_id and work.request.gateway_epoch==request.gateway_epoch
                and work.request.ingress_seq<request.ingress_seq),None)
            if active and self.platform_allowed(request):
                if re.fullmatch(r'(?:不用了|先这样|先不查了|停止|结束|算了)[。！!~～\s]*',request.body.strip()):
                    self.agent.runtime.backend.finish(active,'aborted',reason='conversation_ended')
                    self.followups.clear(scope,request.user_id)
                    self.agent.tasks.clear(request.scope,request.user_id)
                    self.control_event(request,'conversation_ended');return False
                if settings['followup_enabled'] and active.chat_coalesce_until and time.monotonic()<active.chat_coalesce_deadline:
                    if (request.message_id not in {row.message_id for row in active.chat_additions}
                            and len(active.chat_additions)<12
                            and sum(len(row.text.encode()) for row in active.chat_additions)+len(request.body.encode())<=8192):
                        if request.attachments:
                            try:
                                self.agent.images.remember(self.agent.image_scope(request),request.message_id,
                                    request.user_id,request.timestamp_ms,request.attachments)
                                active.chat_addition_images+= (request.message_id,)
                            except ImageError as exc:
                                active.chat_addition_image_error=exc.code
                        active.chat_additions+= (ChatMessage(request.message_id,request.user_id,request.body,
                            request.timestamp_ms,reply_to=reply_to,display_name=request.sender_name),)
                        active.chat_coalesce_until=min(time.monotonic()+settings['coalesce_seconds'],active.chat_coalesce_deadline)
                        self.control_event(request,'supplement_merged')
                    return False
            followup=self.followups.match(scope,request.message_id) if settings['followup_enabled'] else None
            lightweight = False
            if followup is not None and re.fullmatch(r'(?:不用了|先这样|先不查了|停止|结束|算了)[。！!~～\s]*',request.body.strip()):
                self.followups.clear(scope,request.user_id);self.controller.cancel(scope)
                self.agent.tasks.clear(request.scope,request.user_id)
                self.control_event(request,'conversation_ended')
                return False
            if followup is not None:
                if not self.platform_allowed(request):
                    self.control_event(request,'platform_unapproved');return False
                self.control_event(request,'followup_candidate')
                snapshot=self.agent.runtime.config.snapshot().for_group(request.conversation_id)
                self.configured(request,snapshot,continuation=True)
            else:
                if not settings['followup_enabled']:self.control_event(request,'followup_disabled')
                excluded = request.user_id in {row['user_id'] for row in settings['excluded_users']}
                if excluded:
                    self.control_event(request,'excluded_user')
                    if request.conversation_id not in self.lightweight_groups:
                        return False
                snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
                policy=self.configured(request,snapshot)
                lightweight = self.uses_lightweight(request.conversation_id, snapshot.profile, policy)
                if lightweight and (excluded or (not reply_to and (request.quote_hint or request.quoted_text))
                                    or request.quote_hint == 'image'):
                    # The ingress can carry an older platform timestamp than already saved chat.
                    self.controller.mark_context_gap(scope, int(self.agent.runtime.backend.clock() * 1000))
                    if not excluded:
                        self.control_event(request, 'lightweight_context_required')
                    return False
                if excluded:
                    return False
                if policy is None:
                    status='service_disabled' if self.owners()[1].proactive(snapshot,request.conversation_id) else 'platform_unapproved'
                    self.control_event(request,status);return False
            target = None if lightweight and ingress_gap else self.controller.target(scope)
            turn = 'advance' if followup is not None else _observation_turn(target, request, lightweight=lightweight)
            if lightweight and (ingress_gap or request.attachments or turn in ('shift','closed','answered')):
                self.controller.mark_context_gap(scope, int(self.agent.runtime.backend.clock() * 1000))
                if ingress_gap:
                    self.control_event(request, 'proactive_ingress_gap')
            if request.attachments:
                self.agent.images.remember(self.agent.image_scope(request),request.message_id,request.user_id,request.timestamp_ms,request.attachments)
            observed = self.controller.observe(scope,ChatMessage(request.message_id,request.user_id,
                request.body,request.timestamp_ms,reply_to=reply_to,display_name=request.sender_name),
                turn='advance' if turn=='shift' else turn,
                image=bool(request.attachments),
                plain_text=not (request.attachments or request.reference or request.quote_hint or request.quoted_text
                                or _WEB_URL.search(unicodedata.normalize('NFKC',request.body))))
            if observed and turn != 'advance':
                self.control_event(request, 'proactive_'+turn)
            return observed
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def gateway_observation(self, request):
        """Replay the last response when an HTTP retry repeats the same ingress."""
        scope = self.scope(request)
        identity = request.content_digest
        previous = self.observation_responses.get(scope)
        if (previous is not None and previous.gateway_epoch == request.gateway_epoch
                and request.ingress_seq <= previous.covered_seq):
            if request.ingress_seq == previous.observation_seq and identity == previous.content_digest:
                return previous.result
            return False, 0, False
        target = self.controller.target(scope)
        gap = previous is not None and (previous.gateway_epoch != request.gateway_epoch
                                        or request.ingress_seq != previous.covered_seq + 1)
        observed = self.observe(request, ingress_gap=gap)
        current = self.controller.target(scope)
        if target is not None and current is not target:
            backend = self.agent.runtime.backend
            for work in tuple(backend.works.values()):
                if (work.active and work.proactive is not None and not work.continuation
                        and (work.model_started_at is None or work.hermes_chat and not work.hermes_provider_started)
                        and work.request.scope == request.scope
                        and work.request.gateway_epoch == request.gateway_epoch
                        and work.request.ingress_seq < request.ingress_seq
                        and work.request.message_id == target.message_id):
                    backend.finish(work, 'aborted', reason='observation_replaced')
        delay = self.observation_delay(request) if observed else None
        preserve = bool(observed and target is not None and current is target)
        result = (delay is not None, delay or 0, preserve)
        # A named turn may only borrow from the contiguous accepted gateway suffix.
        # An ignored observation, a sequence gap, or a topic shift ends that suffix.
        prefix = (previous.addressed_prefix[-1:] if previous is not None and not gap
                  and not _TOPIC_SHIFT.match(_echo_key(request.body).strip('，,！!？?~～')) else ())
        self.observation_responses[scope] = _ObservationReceipt(
            request.gateway_epoch, request.ingress_seq, identity, result, request.ingress_seq,
            (*prefix, request.message_id) if observed else ())
        self.observation_responses.move_to_end(scope)
        while len(self.observation_responses)>self.controller.max_scopes:
            self.observation_responses.popitem(last=False)
        return result

    def uses_public_lookup(self, group_id, route):
        return group_id in self.one_pass_groups and route in (
            'public_lookup', 'just_window', 'just_score', 'just_card', 'activity_time', 'story_fact')

    def candidate(self,request):
        if self.agent.audit_failures:return None
        owner=self.continuation_owner(self.scope(request))
        if owner is not None and owner!=request.user_id:return None
        settings=self.settings(request.conversation_id)
        followup=self.followups.match(self.scope(request),request.message_id) if settings['followup_enabled'] else None
        continuation=followup is not None
        if continuation:
            if not self.platform_allowed(request):
                self.control_event(request,'platform_unapproved');return None
            snapshot=self.agent.runtime.config.snapshot().for_group(request.conversation_id)
            policy=self.configured(request,snapshot,continuation=True)
        else:
            if request.user_id in {row['user_id'] for row in settings['excluded_users']}:return None
            snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
            policy=self.configured(request,snapshot)
            if policy is None:return None
        profile = snapshot.profile
        scope = self.scope(request)
        lightweight = not continuation and self.uses_lightweight(request.conversation_id, profile, policy)
        if lightweight and (not request.body.strip() or request.attachments or request.quote_hint == 'image'
                            or not request.reference and (request.quote_hint or request.quoted_text)
                            or replies.is_ai_request(request.body)
                            or _WEB_URL.search(unicodedata.normalize('NFKC', request.body))):
            self.control_event(request,'chat_service_lightweight_skip')
            return None
        evidence_route = explicit_evidence_route(request.body) if lightweight else ''
        evidence_context_id = ''
        addressed_evidence = None
        if lightweight and not evidence_route:
            context = self.controller.preceding_message_from_speaker(scope,request.message_id)
            if context is not None:
                previous, plain_text, addressed = context
                if addressed:
                    saved = {row['message_id']: ChatMessage(**row) for row in self.agent.store.recent(
                        request.scope, since_ms=previous.timestamp_ms - 1)}
                    ack_by_source = _ack_by_source(saved, request.timestamp_ms)
                    addressed_evidence = _confirmed_addressed_source(previous, saved, ack_by_source)
                    previous = addressed_evidence or previous
                    plain_text = addressed_evidence is not None
                evidence_route = contextual_just_evidence_route(request.body,previous.text)
                if evidence_route:
                    if (not plain_text or lightweight_image_reference(previous.text,context_source=True)
                            or not self.agent.store.source_valid(request.scope,previous.message_id,previous.timestamp_ms)):
                        self.control_event(request,'lightweight_context_required')
                        return None
                    evidence_context_id = previous.message_id
        peer_reply_id = ''
        if (lightweight and not evidence_route and settings['followup_enabled']
                and request.conversation_id in self.one_pass_groups
                and re.search(r'(?:那|那么|所以|也|同样|一样|也是)', request.body)
                and re.search(r'[?？]|吗|嗎|呢|么|麼', request.body)):
            peer = self.followups.adjacent_peer_reply(scope, request)
            if (peer and peer.user_id not in {row['user_id'] for row in settings['excluded_users']}
                    and (public_lookup_candidate(peer.source_text) or explicit_evidence_route(peer.source_text)
                         or re.search(r'区服|日服|国际服|國際服|台服|臺服|版本', request.body))):
                evidence_route = 'public_lookup'
                peer_reply_id = peer.message_id
        if (lightweight and not evidence_route and request.conversation_id in self.one_pass_groups
                and public_lookup_candidate(request.body)):
            evidence_route = 'public_lookup'
        if lightweight and not evidence_route and lightweight_needs_evidence(request.body):
            self.control_event(request,'chat_service_lightweight_evidence_required')
            return None
        evidence_calls = 1 if evidence_route in ('just_clarify','just_minimum','story_clarify') else 2
        if evidence_route and self.agent.budget.model_calls < evidence_calls:
            self.control_event(request,'chat_service_lightweight_evidence_required')
            return None
        try:self.reconcile_costs(request,policy if not continuation else None)
        except (sqlite3.Error, ModelError, ServiceError, ValueError):
            self.control_event(request,'chat_service_usage_unavailable');return None
        if evidence_route:
            minimum = model_cost = math.ceil(maximum_cost_micro(profile) * evidence_calls)
        elif lightweight:
            minimum = self.lightweight_reservation(request.conversation_id, profile)
            # Keep the staged opportunity ceiling for replacements and defers.
            model_cost = lightweight_minimum_cost_micro(profile) + maximum_cost_micro(lightweight_profile(profile, False))
        else:
            minimum = proactive_minimum_cost_micro(profile)
            model_cost = maximum_cost_micro(profile) * self.agent.budget.model_calls
        if self.uses_public_lookup(request.conversation_id, evidence_route):
            web = self.owners()[1].web(snapshot)
            if web and web.max_calls >= 1 and self.agent.budget.model_calls >= 3:
                model_cost = math.ceil(maximum_cost_micro(profile) * min(4,self.agent.budget.model_calls) + web.unit_cost_micro)
        remaining=self.controller.remaining_cost(scope,
            evidence_reserve_micro=minimum if evidence_route else 0)
        if remaining is not None:model_cost=min(model_cost,remaining)
        if model_cost<=0:
            self.control_event(request,'budget_exhausted');return None
        cost=model_cost
        web=None if lightweight else (self.agent.runtime.capabilities if continuation else self.owners()[1]).web(snapshot)
        if web:
            cost+=self.agent.budget.model_calls*web.max_calls*web.unit_cost_micro
            # Reserve the model loop and only the search budget this group can fund.
            # Each actual HTTP still checks persistent costs before it can start.
        if not continuation:
            preflight=service_request_id('proactive',request.instance_id,request.app_id,
                request.conversation_id,'budget/'+request.request_id)
            # Check this path's minimum so pacing and hard budget denials stay distinguishable.
            required = minimum if policy.proactive.max_cost_micro_per_day else 1
            evidence_budget = (dict(required_calls=evidence_calls,opportunity_calls=self.agent.budget.model_calls,
                opportunity_requests=self.opportunity_requests(request,self.controller.opportunity_candidates(scope)))
                if evidence_route else {})
            try:
                permit=self.agent.service_budget.check(policy,'proactive',snapshot.profile,
                    preflight,required,reply_times=self.agent.store.proactive_replies(request.scope),**evidence_budget)
            except ServiceError as exc:
                data=dict(exc.budget_details)
                self.control_event(request,str(exc),**data);return None
            spent=max(permit.spent_cost_micro,self.controller.status(self.scope(request))['hourly_cost_micro'])
            cost=min(math.ceil(cost),math.floor(permit.available_cost_micro),
                     math.floor(policy.proactive.max_cost_micro_per_hour-spent))
            # Fund the initial evaluation; tools and rechecks spend only what remains.
            if cost<minimum:
                details = dict(permit.budget_details)
                details.update(requested_cost_micro=minimum, available_cost_micro=max(0, cost),
                               controller_cost_micro=math.ceil(spent))
                self.control_event(request,'chat_service_reservation_limit',**details);return None
        candidate=self.controller.candidate(scope,reserve_cost_micro=math.ceil(cost),
            message_id=request.message_id,lightweight=lightweight and not evidence_route,
            evidence_required=bool(evidence_route))
        if candidate is None:
            target = self.controller.target(scope)
            if target is None or target.message_id != request.message_id:
                self.control_event(request, 'proactive_candidate_stale')
            return None
        last=candidate.messages[-1]
        reply_to = request.reference['message_id'] if request.reference else None
        if (last.message_id,last.speaker_id,last.text,last.timestamp_ms,last.reply_to) != (
                request.message_id,request.user_id,request.body,request.timestamp_ms,reply_to):
            self.controller.cancel(self.scope(request));return None
        followup=self.followups.match(self.scope(request),request.message_id)
        if followup is not None:candidate=replace(candidate,followup=followup,continuation=True)
        if continuation:return candidate,None,snapshot,'continuation/'+candidate.candidate_id
        if lightweight:
            if addressed_evidence is not None and evidence_context_id:
                candidate = replace(candidate, messages=tuple(
                    addressed_evidence if row.message_id == evidence_context_id else row
                    for row in candidate.messages))
            candidate=replace(candidate,lightweight=not evidence_route,evidence_route=evidence_route,
                              evidence_context_id=evidence_context_id,peer_reply_id=peer_reply_id)
        job=service_request_id('proactive',request.instance_id,request.app_id,request.conversation_id,candidate.candidate_id)
        try:self.agent.service_budget.check(policy,'proactive',snapshot.profile,job,candidate.reserved_cost_micro,
            reply_times=self.agent.store.proactive_replies(request.scope))
        except ServiceError as exc:
            self.control_event(request,str(exc),**dict(exc.budget_details))
            self.controller.decide(candidate,'skip',actual_cost_micro=0);return None
        if snapshot.fingerprint:self.agent.runtime.usage.configuration(snapshot.profile.config_version,snapshot.fingerprint)
        return candidate,policy,snapshot,job

    def observation_delay(self,request):
        scope=self.scope(request);settings=self.settings(request.conversation_id)
        target=self.controller.target(scope)
        if target is None or target.message_id!=request.message_id:return None
        followup=self.followups.match(scope,request.message_id)
        if followup is not None:
            return math.ceil(self.controller.start_delay(scope)*1000) if settings['followup_enabled'] else None
        return math.ceil(self.controller.start_delay(scope)*1000)

    def start_addressed(self,work):
        if work.request.kind!='group' or work.proactive is not None:return
        scope=self.scope(work.request);work.followup_generation=self.followups.changed(scope,work.request.user_id)
        self.addressed[scope]+=1;work.addressed_registered=True
        settings=self.settings(work.request.conversation_id)
        if work.chat and settings['followup_enabled'] and self.platform_allowed(work.request):
            now=time.monotonic()
            work.chat_coalesce_until=now+settings['coalesce_seconds'];work.chat_coalesce_deadline=now+8
        if scope in self.controller.groups:self.controller.set_addressed(scope,True)
        self.note_addressed_ingress(work, scope, settings)

    def note_addressed_ingress(self, work, scope, settings):
        request = work.request
        previous = self.observation_responses.get(scope)
        if (not work.chat or request.policy.natural_query != 'model'
                or request.attachments or request.reference or request.quote_hint or request.quoted_text
                or _WEB_URL.search(unicodedata.normalize('NFKC', request.body))
                or request.user_id in {row['user_id'] for row in settings['excluded_users']}
                or previous is None or previous.gateway_epoch != request.gateway_epoch
                or request.ingress_seq != previous.covered_seq + 1
                or not self.agent.store.source_valid(request.scope, request.message_id, request.timestamp_ms)):
            return
        background = self.controller.addressed_background(
            scope, request.timestamp_ms, source_ids=previous.addressed_prefix)
        if request.conversation_id not in self.lightweight_groups:
            work.addressed_background = background
            return
        # /submit and /observations share the gateway's sequence. The ring
        # gets a position only; the named turn can borrow a bounded prefix.
        position = ChatMessage(request.message_id, request.user_id, '', request.timestamp_ms)
        if self.controller.observe(scope, position, addressed=True):
            work.addressed_background = background
            self.observation_responses[scope] = replace(previous, covered_seq=request.ingress_seq, addressed_prefix=())
            self.observation_responses.move_to_end(scope)

    def addressed_context_grant(self, snapshot, group_id):
        runtime = self.agent.runtime
        if not runtime.capabilities.proactive(snapshot, group_id):
            return None
        observed_profile = runtime.proactive_snapshot(group_id)
        policy = self.policy(observed_profile, SimpleNamespace(conversation_id=group_id))
        if policy is None:
            return None
        primary_grant = runtime.capabilities.generation(snapshot)
        observed_grant = self.owners()[1].generation(observed_profile)
        if primary_grant is None or observed_grant is None:
            return None
        bindings = (primary_grant, observed_grant, observed_profile.fingerprint,
                    policy.service_subject_id, policy.approval_ref)
        return sha256(json.dumps(bindings).encode()).hexdigest()

    def capture_addressed_context(self, work):
        try:
            work.addressed_context_grant = self.addressed_context_grant(work.model_snapshot, work.request.conversation_id)
        except (ModelError, ServiceError, OSError, sqlite3.Error, ValueError):
            work.addressed_context_grant = None
        if work.addressed_context_grant is None:
            work.addressed_background = ()

    def addressed_quote_source(self, work):
        request = work.request
        if work.addressed_context_grant is None or not work.missing_anchor or work.proactive is not None:
            return None
        source = self.controller.addressed_quote(self.scope(request), request.reference['message_id'])
        if source and self.addressed_context_sources_valid(request, (source,)):
            return source
        return None

    def addressed_context_sources_valid(self, request, rows):
        excluded = {row['user_id'] for row in self.settings(request.conversation_id)['excluded_users']}
        now_ms = int(self.agent.runtime.backend.clock()*1000)
        return all(row.speaker_id not in excluded and 0 <= now_ms - row.timestamp_ms < 60_000
                   and self.agent.store.source_valid(request.scope, row.message_id, row.timestamp_ms)
                   for row in rows)

    async def coalesce_addressed(self,work,content):
        while work.active and work.chat_coalesce_until>time.monotonic():
            await asyncio.sleep(work.chat_coalesce_until-time.monotonic())
        work.chat_coalesce_until=0
        if work.chat_additions:
            content+='\n\n同一用户随后补充：\n'+'\n'.join(row.text for row in work.chat_additions)
        return content

    def remember_question(self,work,message_id,text):
        if not work.chat or work.request.kind!='group':return
        try:
            settings=self.settings(work.request.conversation_id)
            if not settings['followup_enabled'] or not self.platform_allowed(work.request):return
            generation=(self.followups.changed(self.scope(work.request),work.request.user_id) if work.proactive is not None
                        else getattr(work,'followup_generation',None))
            originals=self.agent.store.read(work.request.scope,[work.request.message_id])['messages']
            if len(originals)!=1 or originals[0]['role']!='user' or originals[0]['speaker_id']!=work.request.user_id:return
            original=originals[0]
            self.followups.delivered(self.scope(work.request),generation,
                work.request.user_id,work.request.message_id,original['text'],original['reply_to'],original['display_name'],original['timestamp_ms'],work.request.ingress_seq,work.request.gateway_epoch,message_id,text,
                int(self.agent.runtime.backend.clock()*1000),window=settings['followup_window_seconds'],request_id=work.request.request_id)
            group=self.controller.groups.get(self.scope(work.request))
            if group and group.opportunity is not None and work.proactive is None:
                group.opportunity.knowledge_leases.clear();group.opportunity=None
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):pass

    def settle_cost(self,work):
        return self.controller.settle(work.proactive,**self.cost(work))

    def finish(self,work):
        self.trace(work,'finished',status=work.terminal_status,delivered=work.proactive_delivered,
                   model_attempts=work.model_attempts,reason=work.terminal_reason or 'reason_unknown',
                   stage='delivery' if work.prepared else work.model_stage)
        try:
            if work.proactive is not None:
                self.settle_cost(work)
                if work.proactive_delivered:self.controller.delivered(work.proactive)
                elif self.controller.valid(work.proactive):self.controller.cancel(self.scope(work.request))
        except (sqlite3.Error,ModelError,ValueError):
            self.agent.audit_failures+=1
            self.controller.cancel(self.scope(work.request))
        work.participating_history=()
        if work.addressed_registered:
            scope=self.scope(work.request);self.addressed[scope]-=1;work.addressed_registered=False
            if not self.addressed[scope]:del self.addressed[scope]
            if scope in self.controller.groups:self.controller.set_addressed(scope,bool(self.addressed[scope]))

    def valid(self,work,*,sending=False):
        if work.proactive is None:return True
        try:
            settings=self.settings(work.request.conversation_id)
            if work.proactive.peer_reply_id:
                peer = self.followups.adjacent_peer_reply(self.scope(work.request), work.request)
                if (not settings['followup_enabled'] or not peer or peer.message_id != work.proactive.peer_reply_id
                        or any(row['user_id'] == peer.user_id for row in settings['excluded_users'])
                        or not self.agent.store.source_valid(work.request.scope, peer.source_id, peer.source_time)
                        or not self.agent.store.source_valid(work.request.scope, peer.message_id, peer.delivered_ms)):
                    return False
            if work.continuation:
                if not settings['followup_enabled'] or not self.platform_allowed(work.request):return False
                current=self.agent.runtime.config.snapshot().for_group(work.request.conversation_id)
                policy=None
            else:
                if any(row.speaker_id in {user['user_id'] for user in settings['excluded_users']} for row in work.proactive.messages):return False
                current=self.agent.runtime.proactive_snapshot(work.request.conversation_id)
                policy=self.policy(current,work.request)
            expected=work.primary_model_snapshot if work.continuation and work.budget_fallback else work.model_snapshot
            if current!=expected:return False
            if self.agent.runtime.capabilities_for(work).generation(current)!=work.proactive_authority:return False
            followup=work.proactive.followup
            current_followup=self.followups.match(self.scope(work.request),work.request.message_id) if followup else None
            if followup is not None and (current_followup is None
                    or replace(current_followup,observed_ids=followup.observed_ids)!=followup
                    or not self.agent.store.source_valid(work.request.scope,followup.source_id,followup.source_time)
                    or not self.agent.store.source_valid(work.request.scope,followup.message_id,followup.delivered_ms)):
                return False
            # Admission is checked again at send intent, after model generation.
            # Explicit addressed work has no proactive candidate and is unaffected.
            # A continuation may not claim to have assessed messages received later.
            if sending and followup is not None and current_followup.observed_ids!=followup.observed_ids:
                return False
            return (policy==work.service_policy and all(self.agent.store.source_valid(work.request.scope,row.message_id,row.timestamp_ms)
                    for row in work.proactive.messages)
                    and (self.controller.can_send(work.proactive) if sending else self.controller.valid(work.proactive)))
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def text_history(self, work, rows, saved):
        # Keep the gateway's observation order. Restore a saved prefix only
        # before the observable scene, or join an ACK to its visible source.
        image_id = work.proactive.image_boundary_id
        addressed = set(work.proactive.addressed_message_ids)
        gap = work.proactive.context_gap_ms is not None or image_id
        current = rows[-1]
        ack_by_source = _ack_by_source(saved, current.timestamp_ms)
        prior = []
        cutoff = int((self.agent.runtime.backend.clock() - 300) * 1000)
        for row in rows[:-1]:
            if row.timestamp_ms <= cutoff:
                work.chat_context_cuts.add('expired')
                continue
            if row.message_id in addressed:
                row = _confirmed_addressed_source(row, saved, ack_by_source)
                if row is None:
                    continue
            if row.message_id != image_id and not lightweight_image_reference(row.text):
                prior.append(row)
        source_ids = {row.message_id for row in prior}
        first_ms = min(row.timestamp_ms for row in rows)
        prefix = [row for row in saved.values()
                  if not gap and row.message_id not in source_ids and row.message_id not in addressed
                  and row.message_id != current.message_id and row.timestamp_ms < first_ms
                  and not lightweight_image_reference(row.text)]
        prefix_ids = {row.message_id for row in prefix if row.role == 'user'}
        prefix = [row for row in prefix if row.role == 'user' or row.reply_to in prefix_ids]
        scene = list(prefix)
        for row in prior:
            scene.append(row)
            ack = ack_by_source.get(row.message_id)
            if ack is not None and ack.message_id not in source_ids:
                scene.append(ack)
        scene.append(current)
        selected, anchored = select_topic_context(scene, limit=5 if image_id else 6)
        if len(selected) < len(scene):
            work.chat_context_cuts.add('selection')
        visible = {row.message_id for row in selected}
        selected = [row for row in selected if row.role == 'user' or row.reply_to in visible]
        self.trace(work, 'control', status='proactive_context_anchor' if anchored else 'proactive_context_recent',
                   messages=len(selected))
        if image_id:
            # The cache can expire or evict a picture before the candidate is
            # prepared. The observed media boundary survives either event.
            selected = [replace(rows[0], text=''), *selected]
        return selected

    def history(self,work):
        # Merge scoped participating history with frozen observations; only a new ACK persists observations.
        followup=work.proactive.followup
        text_lane = work.proactive.lightweight or work.proactive.evidence_route
        rows=list(work.proactive.messages[-12:] if followup else work.proactive.messages[-48:])
        if not text_lane and work.proactive.addressed_message_ids:
            rows = [row for row in rows if row.message_id not in work.proactive.addressed_message_ids]
        if text_lane and work.proactive.image_boundary_id and rows[0].message_id != work.proactive.image_boundary_id:
            rows.insert(0, work.proactive.messages[0])
        if followup:rows=[row for row in rows if row.message_id in followup.observed_ids]
        saved={}
        if followup:
            saved={row['message_id']:ChatMessage(**row) for row in self.agent.store.read(work.request.scope,followup.observed_ids)['messages']}
            merged={**saved,**{row.message_id:row for row in rows}}
            rows=sorted(merged.values(),key=lambda row:row.timestamp_ms)
            current=next((row for row in rows if row.message_id==work.request.message_id),None)
            if current:rows=[row for row in rows if row.message_id!=current.message_id]+[current]
        if not followup:
            since_ms = int((self.agent.runtime.backend.clock()-(300 if text_lane else 900))*1000)
            if work.proactive.context_gap_ms is not None:
                # Saved participation must not reintroduce the older scene.
                since_ms = max(since_ms, work.proactive.context_gap_ms)
            saved={row['message_id']:ChatMessage(**row) for row in self.agent.store.recent(work.request.scope,
                since_ms=since_ms)}
            if text_lane:
                excluded = {row['user_id'] for row in self.settings(work.request.conversation_id)['excluded_users']}
                saved = {mid: row for mid, row in saved.items() if row.speaker_id not in excluded}
            if work.proactive.context_gap_ms is not None:
                # A late receipt can give a reply to the older scene a newer timestamp.
                source_ids = saved.keys() | {row.message_id for row in rows}
                saved = {mid: row for mid, row in saved.items() if row.role != 'assistant' or row.reply_to in source_ids}
            if text_lane:
                rows = self.text_history(work, rows, saved)
            else:
                merged=dict(saved)
                for row in rows:merged.setdefault(row.message_id,row)
                current=merged.pop(work.request.message_id)
                rows=sorted(merged.values(),key=lambda row:row.timestamp_ms)
                # The current observation stays last even when QQ and ACK clocks differ.
                rows.append(current)
        protected={work.request.message_id}
        if work.proactive.image_boundary_id:
            protected.add(work.proactive.image_boundary_id)
        recent_reply=next((row for row in reversed(rows) if row.role=='assistant'),None)
        if recent_reply:protected.update((recent_reply.message_id,recent_reply.reply_to))
        while rows and (len(rows)>48 or sum(len(json.dumps(asdict(row),ensure_ascii=False).encode()) for row in rows)>24576):
            index=next((i for i,row in enumerate(rows) if row.message_id not in protected),0)
            rows.pop(index)
            work.chat_context_cuts.add('history_budget')
        self.agent.bind_memory(work,[asdict(row) for row in rows if row.message_id in saved])
        messages=[]
        if followup:
            messages.append({'role':'user','content':json.dumps(asdict(ChatMessage(followup.source_id,followup.user_id,followup.source_text,
                followup.source_time,reply_to=followup.source_reply_to,display_name=followup.source_display_name)),ensure_ascii=False)})
            messages.append({'role':'assistant','content':json.dumps(asdict(ChatMessage(followup.message_id,'Taki',followup.text,
                followup.delivered_ms,'assistant',followup.source_id)),ensure_ascii=False)})
        for row in rows:
            value=asdict(row)
            if row.message_id==work.request.message_id and work.request.quoted_text and not row.reply_to:
                value['quoted_text']=work.request.quoted_text
                value['quote_status']='用户提供的引用预览，原作者和原消息身份未经核验'
            try:ref=self.agent.images.get(self.agent.image_scope(work.request),row.message_id)
            except ImageError:ref=None
            if ref:value['image_attachments']=''.join('[图片附件 '+item.id+' '+item.content_type+'；图像仅在本轮或有效引用中可见]' for item in ref.attachments)
            elif row.message_id == work.proactive.image_boundary_id:
                value['image_attachments'] = '[图片附件]'
            messages.append({'role':row.role,'content':json.dumps(value,ensure_ascii=False)})
        return messages

    def cost(self,work):
        rows=self.agent.runtime.usage.db.execute('SELECT * FROM attempts WHERE request_id=?',
            (work.service_request_id,)).fetchall()
        policy=work.service_policy
        return self.row_cost(rows,remaining_only=policy is not None and uses_proactive_day_accounting(policy)
                             and not getattr(work,'continuation',False))

    def row_cost(self,rows,*,remaining_only=False):
        if any(row['state'] not in ('settled','released') for row in rows):return dict(actual_cost_micro=None)
        amounts=[self.agent.service_budget.attempt_cost(row,remaining_only=remaining_only) for row in rows]
        held=math.ceil(sum((amount for amount,_ in amounts),Fraction()))
        return dict(actual_cost_micro=None if any(uncertain for _,uncertain in amounts) else held,
                    held_cost_micro=held)

    def reconcile_costs(self,request,policy=None):
        """Converge unfinished memory holds from terminal ledger facts before admission."""
        scope=self.scope(request)
        candidates=self.controller.pending_costs(scope)
        if not candidates:return
        jobs={job:candidate for candidate in candidates for job in (
            service_request_id('proactive',request.instance_id,request.app_id,request.conversation_id,candidate),
            'continuation/'+candidate)}
        # A live worker can still reserve another call after a completed tool round.
        live={work.service_request_id for work in self.agent.runtime.backend.works.values() if work.active}
        jobs={job:candidate for job,candidate in jobs.items() if job not in live}
        if not jobs:return
        rows=self.agent.runtime.usage.db.execute(
            'SELECT * FROM attempts WHERE group_key=? AND request_id IN ('+','.join('?' for _ in jobs)+')',
            (self.agent.runtime.usage.key('group',request.conversation_id),*jobs)).fetchall()
        grouped={candidate:[] for candidate in candidates}
        for row in rows:grouped[jobs[row['request_id']]].append(row)
        before=self.controller.status(scope)['hourly_cost_micro']
        for candidate,attempts in grouped.items():
            # Missing records and live reservations cannot prove that a request is free.
            if attempts and all(row['state'] in ('settled','released') for row in attempts):
                remaining_only=policy is not None and uses_proactive_day_accounting(policy) and not attempts[0]['request_id'].startswith('continuation/')
                self.controller.reconcile_cost(scope,candidate,**self.row_cost(attempts,remaining_only=remaining_only))
        after=self.controller.status(scope)['hourly_cost_micro']
        if after!=before:
            self.control_event(request,'budget_reconciled',controller_before_micro=before,controller_after_micro=after)

    @staticmethod
    def opportunity_requests(request,candidates):
        return tuple(value for candidate in candidates for value in (
            'continuation/'+candidate, service_request_id('proactive',request.instance_id,
            request.app_id,request.conversation_id,candidate)))

    def opportunity_budget(self,work):
        candidates,cost=self.controller.opportunity_budget(work.proactive)
        return dict(opportunity_requests=self.opportunity_requests(work.request,candidates),
            opportunity_calls=self.agent.budget.model_calls,opportunity_cost_micro=cost)

    def check_continuation_budget(self,work,amount):
        budget=self.opportunity_budget(work)
        rows=[row for request_id in budget['opportunity_requests'] for row in self.agent.runtime.usage.request_view(request_id)]
        active=[row for row in rows if row['state']!='released']
        require(len(active)<budget['opportunity_calls'],'quota_exceeded')
        require(all(not row['billing_incomplete'] and row['state'] in ('reserved','settled') for row in active),'usage_unavailable')
        cost=sum((Fraction(row['reserved_tokens'])*Fraction(row['price']['reference_rate'])/1_000_000
                  if row['state'] in ('reserved','started') else Fraction(row['reference_cost_micro']) for row in active),Fraction())
        require(cost+amount<=budget['opportunity_cost_micro'],'quota_exceeded')

    def decision(self,work,text):
        require(self.valid(work),'proactive_stale')
        # Accept a whole JSON code block; never extract an object from prose.
        if isinstance(text,str):
            text=text.strip()
            fenced=re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```',text,re.S|re.I)
            if fenced:text=fenced.group(1)
        try:value=json.loads(text)
        except (ValueError,TypeError) as exc:
            shape=('fenced' if isinstance(text,str) and text.startswith('```') else
                   'object' if isinstance(text,str) and text.startswith('{') else 'other')
            self.trace(work,'decision',decision='invalid_json',name=shape,
                       status='json_syntax' if isinstance(exc,json.JSONDecodeError) else 'non_text')
            raise ModelError('model_protocol_error') from None
        require(isinstance(value,dict) and value.get('participation') in ('reply','skip','defer'),'model_protocol_error')
        mode=value['participation']
        require(({'participation','text'} if mode=='reply' else {'participation','delay_seconds'} if mode=='defer' else {'participation'})<=set(value),'model_protocol_error')
        metadata = {}
        if mode=='reply':
            require(isinstance(value['text'],str) and 0<len(value['text'].strip())<=4000,'model_protocol_error')
            if not work.continuation:
                reply_key = _echo_key(value['text'])
                if reply_key and reply_key == _echo_key(work.request.body):
                    mode = 'skip'
                    metadata['reason'] = 'echo'
        if mode=='defer':require(type(value['delay_seconds']) is int and 2<=value['delay_seconds']<=5,'model_protocol_error')
        self.controller.decide(work.proactive,mode,**self.cost(work),defer_seconds=value['delay_seconds'] if mode=='defer' else 2)
        self.trace(work,'decision',decision=mode,model_attempts=work.model_attempts,**metadata)
        work.participation=mode
        return value['text'].strip() if mode=='reply' else None

    def delivered(self,work):
        self.controller.delivered(work.proactive)

    def clear(self,scope):
        self.controller.clear(tuple(scope.values()))
        self.followups.clear(tuple(scope.values()))
