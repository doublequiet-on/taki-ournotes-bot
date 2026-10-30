# L3: Host integration for ephemeral participation and approved group-service billing.
# Effects: Bounded memory and metadata-only usage reads; receipts alone persist dialogue.
import json
import math
import sqlite3
from collections import Counter
from dataclasses import asdict,replace
from fractions import Fraction
from types import SimpleNamespace

from ..domain.chat import ChatMessage
from ..domain.chat_images import ImageError
from ..domain.model_calls import ModelError,require
from .chat_jobs import ChatServices,ServiceError,maximum_cost_micro,service_request_id
from .proactive_chat import ProactiveController,ProactivePolicy
from .chat_followup import Followups
from . import replies


class ProactiveChat:
    def __init__(self,agent):
        self.agent=agent
        self.controller=ProactiveController()
        self.followups=Followups(clock=lambda:self.controller.clock())
        self.addressed=Counter()
        self.services=ChatServices(agent.runtime.usage.path.parent,agent.runtime.backend.instance_id,
            agent.runtime.backend.app_id,filename='chat-proactive-services-v1.json')

    def owners(self):
        runtime=self.agent.runtime
        return ((self.services,runtime.proactive_capabilities) if runtime.dedicated_proactive()
                else (self.agent.services,runtime.capabilities))

    def sweep_followups(self):
        self.followups.sweep()
        for scope in tuple(self.followups.questions):
            request=SimpleNamespace(conversation_id=scope[-1])
            try:
                snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
                if self.policy(snapshot,request) is None:self.followups.clear(scope)
            except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):self.followups.clear(scope)

    @staticmethod
    def scope(request):return tuple(request.scope.values())

    def policy(self,snapshot,request):
        services,capabilities=self.owners()
        policy=services.policy(snapshot,request.conversation_id)
        if policy is None or not policy.proactive.enabled:return None
        if not capabilities.proactive(snapshot,request.conversation_id):return None
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
            snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
            if self.configured(request,snapshot) is None:
                self.followups.clear(self.scope(request));return False
            if replies.recognizes_message(request.body) and not replies.is_ai_request(request.body):
                self.followups.changed(self.scope(request))
                self.controller.cancel(self.scope(request));return False
            if request.attachments:
                self.agent.images.remember(self.agent.image_scope(request),request.message_id,request.user_id,request.timestamp_ms,request.attachments)
            accepted=self.controller.observe(self.scope(request),ChatMessage(request.message_id,request.user_id,
                request.body,request.timestamp_ms,display_name=request.sender_name))
            if accepted:self.followups.observe(self.scope(request),request.user_id,request.message_id,request.timestamp_ms,request.ingress_seq,request.gateway_epoch)
            return accepted
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def candidate(self,request):
        if self.agent.audit_failures:return None
        snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
        policy=self.configured(request,snapshot)
        if policy is None:return None
        cost=maximum_cost_micro(snapshot.profile)*self.agent.budget.model_calls
        web=self.owners()[1].web(snapshot)
        if web:cost+=self.agent.budget.model_calls*web.max_calls*web.unit_cost_micro
        candidate=self.controller.candidate(self.scope(request),reserve_cost_micro=math.ceil(cost))
        if candidate is None:return None
        last=candidate.messages[-1]
        if (last.message_id,last.speaker_id,last.text,last.timestamp_ms)!=(request.message_id,request.user_id,request.body,request.timestamp_ms):
            self.controller.cancel(self.scope(request));return None
        followup=self.followups.match(self.scope(request),request.message_id)
        if followup is not None:candidate=replace(candidate,followup=followup)
        job=service_request_id('proactive',request.instance_id,request.app_id,request.conversation_id,candidate.candidate_id)
        try:self.agent.service_budget.check(policy,'proactive',snapshot.profile,job,cost,
            reply_times=self.agent.store.proactive_replies(request.scope))
        except ServiceError:
            self.controller.decide(candidate,'skip',actual_cost_micro=0);return None
        if snapshot.fingerprint:self.agent.runtime.usage.configuration(snapshot.profile.config_version,snapshot.fingerprint)
        return candidate,policy,snapshot,job

    def observation_delay(self,request):
        """Wait within the question window without bypassing either cooldown."""
        scope=self.scope(request)
        followup=self.followups.match(scope,request.message_id)
        if followup is None:return 1500
        snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
        policy=self.policy(snapshot,request)
        if policy is None:return None
        group=self.controller.groups.get(scope)
        if group is None:return None
        wait=max(1.5,group.last_delivery_at+self.controller.cooldown_seconds-self.controller.clock())
        replies=self.agent.store.proactive_replies(request.scope)
        if replies:wait=max(wait,max(replies)+policy.proactive.cooldown_seconds-self.agent.service_budget.clock())
        if wait+5>=followup.at+self.followups.window-self.controller.clock():return None
        return math.ceil(wait*1000)

    def start_addressed(self,work):
        if work.request.kind!='group' or work.proactive is not None:return
        scope=self.scope(work.request);work.followup_generation=self.followups.changed(scope)
        self.addressed[scope]+=1;work.addressed_registered=True
        if scope in self.controller.groups:self.controller.set_addressed(scope,True)

    def remember_question(self,work,message_id,text):
        if not work.chat or work.proactive is not None or work.request.kind!='group':return
        try:
            snapshot=self.agent.runtime.proactive_snapshot(work.request.conversation_id)
            if self.policy(snapshot,work.request) is None:return
            originals=self.agent.store.read(work.request.scope,[work.request.message_id])['messages']
            if len(originals)!=1 or originals[0]['role']!='user' or originals[0]['speaker_id']!=work.request.user_id:return
            original=originals[0]
            self.followups.delivered(self.scope(work.request),getattr(work,'followup_generation',None),
                work.request.user_id,work.request.message_id,original['text'],original['reply_to'],original['display_name'],original['timestamp_ms'],work.request.ingress_seq,work.request.gateway_epoch,message_id,text,
                int(self.agent.runtime.backend.clock()*1000))
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):pass

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
            current=self.agent.runtime.proactive_snapshot(work.request.conversation_id)
            if current!=work.model_snapshot:return False
            policy=self.policy(current,work.request)
            followup=work.proactive.followup
            if followup is not None and (self.followups.match(self.scope(work.request),work.request.message_id)!=followup
                    or not self.agent.store.source_valid(work.request.scope,followup.source_id,followup.source_time)
                    or not self.agent.store.source_valid(work.request.scope,followup.message_id,followup.delivered_ms)):
                return False
            return (policy==work.service_policy and all(self.agent.store.source_valid(work.request.scope,row.message_id,row.timestamp_ms)
                    for row in work.proactive.messages)
                    and (self.controller.can_send(work.proactive) if sending else self.controller.valid(work.proactive)))
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def history(self,work):
        # Only frozen, bounded observation material; never enters persistent context before ACK.
        followup=work.proactive.followup
        rows=list(work.proactive.messages[-1:] if followup else work.proactive.messages[-48:])
        while rows and sum(len(row.text.encode()) for row in rows)>24576:rows.pop(0)
        messages=[]
        if followup:
            messages.append({'role':'user','content':json.dumps(asdict(ChatMessage(followup.source_id,followup.user_id,followup.source_text,
                followup.source_time,reply_to=followup.source_reply_to,display_name=followup.source_display_name)),ensure_ascii=False)})
            messages.append({'role':'assistant','content':json.dumps(asdict(ChatMessage(followup.message_id,'Taki',followup.text,
                followup.delivered_ms,'assistant',followup.source_id)),ensure_ascii=False)})
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
        self.followups.clear(tuple(scope.values()))
