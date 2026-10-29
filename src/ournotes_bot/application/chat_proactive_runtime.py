# L3: Host integration for ephemeral participation and approved group-service billing.
# Effects: Bounded memory and metadata-only usage reads; receipts alone persist dialogue.
import json
import math
import sqlite3
from collections import Counter
from dataclasses import asdict
from fractions import Fraction

from ..domain.chat import ChatMessage
from ..domain.chat_images import ImageError
from ..domain.model_calls import ModelError,require
from .chat_jobs import ServiceError,maximum_cost_micro,service_request_id
from .proactive_chat import ProactiveController,ProactivePolicy
from . import replies


class ProactiveChat:
    def __init__(self,agent):
        self.agent=agent
        self.controller=ProactiveController()
        self.addressed=Counter()

    @staticmethod
    def scope(request):return tuple(request.scope.values())

    def policy(self,snapshot,request):
        policy=self.agent.services.policy(snapshot,request.conversation_id)
        if policy is None or not policy.proactive.enabled:return None
        if not self.agent.runtime.capabilities.proactive(snapshot,request.conversation_id):return None
        if max(policy.proactive.max_calls_per_hour,policy.proactive.max_replies_per_hour)>120:return None
        return policy

    def configured(self,request,snapshot):
        scope=self.scope(request);policy=self.policy(snapshot,request)
        if policy is None and scope not in self.controller.groups:return None
        limits=policy.proactive if policy else None
        self.controller.configure(scope,ProactivePolicy(bool(policy),policy.service_subject_id if policy else '',
            policy.approval_ref if policy else '',limits.max_calls_per_hour if limits else 6,
            limits.max_replies_per_hour if limits else 3,limits.max_cost_micro_per_hour if limits else 0))
        if self.controller.status(scope)['addressed_active']!=bool(self.addressed[scope]):
            self.controller.set_addressed(scope,bool(self.addressed[scope]))
        return policy

    def observe(self,request):
        if request.kind!='group' or request.policy.natural_query!='model':return False
        try:
            if not self.agent.store.source_valid(request.scope,request.message_id,request.timestamp_ms):return False
            snapshot=self.agent.runtime.config.snapshot().for_group(request.conversation_id)
            if self.configured(request,snapshot) is None:return False
            if replies.recognizes_message(request.body) and not replies.is_ai_request(request.body):
                self.controller.cancel(self.scope(request));return False
            if request.attachments:
                self.agent.images.remember(self.agent.image_scope(request),request.message_id,request.user_id,request.timestamp_ms,request.attachments)
            self.controller.observe(self.scope(request),ChatMessage(request.message_id,request.user_id,
                request.body,request.timestamp_ms,display_name=request.sender_name))
            return True
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def candidate(self,request):
        if self.agent.audit_failures:return None
        snapshot=self.agent.runtime.config.snapshot().for_group(request.conversation_id)
        policy=self.configured(request,snapshot)
        if policy is None:return None
        cost=maximum_cost_micro(snapshot.profile)*self.agent.budget.model_calls
        web=self.agent.runtime.capabilities.web(snapshot)
        if web:cost+=self.agent.budget.model_calls*web.max_calls*web.unit_cost_micro
        candidate=self.controller.candidate(self.scope(request),reserve_cost_micro=math.ceil(cost))
        if candidate is None:return None
        last=candidate.messages[-1]
        if (last.message_id,last.speaker_id,last.text,last.timestamp_ms)!=(request.message_id,request.user_id,request.body,request.timestamp_ms):
            self.controller.cancel(self.scope(request));return None
        job=service_request_id('proactive',request.instance_id,request.app_id,request.conversation_id,candidate.candidate_id)
        try:self.agent.service_budget.check(policy,'proactive',snapshot.profile,job,cost,
            reply_times=self.agent.store.proactive_replies(request.scope))
        except ServiceError:
            self.controller.decide(candidate,'skip',actual_cost_micro=0);return None
        return candidate,policy,snapshot,job

    def start_addressed(self,work):
        if work.request.kind!='group' or work.proactive is not None:return
        scope=self.scope(work.request);self.addressed[scope]+=1;work.addressed_registered=True
        if scope in self.controller.groups:self.controller.set_addressed(scope,True)

    def finish(self,work):
        try:
            if work.proactive is not None:
                self.controller.settle(work.proactive,actual_cost_micro=self.cost(work))
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
            policy=self.policy(work.model_snapshot,work.request)
            return (policy==work.service_policy and all(self.agent.store.source_valid(work.request.scope,row.message_id,row.timestamp_ms)
                    for row in work.proactive.messages)
                    and (self.controller.can_send(work.proactive) if sending else self.controller.valid(work.proactive)))
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def history(self,work):
        # Only frozen, bounded observation material; never enters persistent context before ACK.
        rows=list(work.proactive.messages[-48:])
        while rows and sum(len(row.text.encode()) for row in rows)>24576:rows.pop(0)
        messages=[]
        for row in rows:
            value=asdict(row)
            try:ref=self.agent.images.get(self.agent.image_scope(work.request),row.message_id)
            except ImageError:ref=None
            if ref:value['text']+='\n'+''.join('[图片附件 '+item.id+' '+item.content_type+'；图像仅在本轮或有效引用中可见]' for item in ref.attachments)
            messages.append({'role':'user','content':json.dumps(value,ensure_ascii=False)})
        return messages

    def cost(self,work):
        rows=self.agent.runtime.usage.request_view(work.service_request_id)
        if any(row['billing_incomplete'] or row['state'] not in ('settled','released') for row in rows):return None
        return math.ceil(sum((Fraction(row['reference_cost_micro']) for row in rows),Fraction()))

    def decision(self,work,text):
        require(self.valid(work),'proactive_stale')
        try:value=json.loads(text)
        except (ValueError,TypeError):raise ModelError('model_protocol_error') from None
        require(isinstance(value,dict) and value.get('participation') in ('reply','skip','defer'),'model_protocol_error')
        mode=value['participation']
        require(set(value)==({'participation','text'} if mode=='reply' else {'participation','delay_seconds'} if mode=='defer' else {'participation'}),'model_protocol_error')
        if mode=='reply':require(isinstance(value['text'],str) and 0<len(value['text'].strip())<=4000,'model_protocol_error')
        if mode=='defer':require(type(value['delay_seconds']) is int and 2<=value['delay_seconds']<=5,'model_protocol_error')
        self.controller.decide(work.proactive,mode,actual_cost_micro=self.cost(work),defer_seconds=value.get('delay_seconds',2))
        work.participation=mode
        return value['text'].strip() if mode=='reply' else None

    def delivered(self,work):
        self.controller.delivered(work.proactive)

    def clear(self,scope):
        self.controller.clear(tuple(scope.values()))
