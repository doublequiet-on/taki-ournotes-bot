# L3: Host integration for ephemeral participation and approved group-service billing.
# Effects: Bounded observations, scoped receipt history and usage reads; receipts alone persist dialogue.
import asyncio
import time
import json
import math
import re
import sqlite3
from collections import Counter,OrderedDict
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
    def response_format():
        return {'type':'json_schema','name':'proactive_participation','strict':True,'schema':{
            'type':'object','properties':{
                'participation':{'type':'string','enum':['reply','skip','defer']},
                'text':{'type':'string','enum':['']},'delay_seconds':{'type':['integer','null']}},
            'required':['participation','text','delay_seconds'],'additionalProperties':False}}

    def __init__(self,agent):
        self.agent=agent
        self.controller=ProactiveController()
        self.followups=Followups(clock=lambda:self.controller.clock(),on_expire=self.window_expired)
        self.addressed=Counter()
        self.control_times=OrderedDict()
        self.services=ChatServices(agent.runtime.usage.path.parent,agent.runtime.backend.instance_id,
            agent.runtime.backend.app_id,filename='chat-proactive-services-v1.json',authority=agent.runtime.admin.db,require_authority=True)

    def control_event(self,request,status):
        backend=self.agent.runtime.backend
        now=backend.clock()
        if status in ('excluded_user','followup_disabled','platform_unapproved','service_disabled','budget_exhausted'):
            key=(request.conversation_id,status)
            if key in self.control_times and now-self.control_times[key]<10:return
            self.control_times[key]=now;self.control_times.move_to_end(key)
            while len(self.control_times)>1024:self.control_times.popitem(last=False)
        backend.diagnostics.conversation_event(request.request_id,request.conversation_id,'control',now,status=status)

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
        group=self.controller.groups.get(scope)
        if group:
            group.rows[:]=[row for row in group.rows if row.message.speaker_id not in excluded]
        for work in tuple(self.agent.runtime.backend.works.values()):
            if work.request.conversation_id!=group_id or work.proactive is None:continue
            if ((work.continuation and not after['followup_enabled']) or (not work.continuation
                    and any(row.speaker_id in excluded for row in work.proactive.messages))):
                self.agent.runtime.backend.finish(work,'aborted')
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

    def configured(self,request,snapshot,*,continuation=False):
        scope=self.scope(request)
        if continuation:
            # The regular user/group ledger enforces paid calls for this lane.
            policy=ProactivePolicy(True,'continuation','explicit-dialogue',120,120,10**15,cooldown_seconds=0)
            self.controller.configure(scope,policy)
            self.controller.groups[scope].coalesce_seconds=self.settings(request.conversation_id)['coalesce_seconds']
            return None
        policy=self.policy(snapshot,request)
        if policy is None and scope not in self.controller.groups:return None
        limits=policy.proactive if policy else None
        self.controller.configure(scope,ProactivePolicy(bool(policy),policy.service_subject_id if policy else '',
            policy.approval_ref if policy else '',limits.max_calls_per_hour if limits else 6,
            limits.max_replies_per_hour if limits else 3,limits.max_cost_micro_per_hour if limits else 0,
            cooldown_seconds=limits.cooldown_seconds if limits else 60))
        self.controller.groups[scope].coalesce_seconds=None
        if self.controller.status(scope)['addressed_active']!=bool(self.addressed[scope]):
            self.controller.set_addressed(scope,bool(self.addressed[scope]))
        return policy

    def observe(self,request):
        if request.kind!='group' or request.policy.natural_query!='model':return False
        try:
            scope=self.scope(request);settings=self.settings(request.conversation_id)
            if not self.agent.store.source_valid(request.scope,request.message_id,request.timestamp_ms):return False
            if replies.recognizes_message(request.body) and not replies.is_ai_request(request.body):
                self.followups.clear(scope,request.user_id);self.controller.cancel(scope);return False
            active=next((work for work in reversed(tuple(self.agent.runtime.backend.works.values()))
                if work.active and work.chat and work.proactive is None and work.request.scope==request.scope
                and work.request.user_id==request.user_id and work.request.gateway_epoch==request.gateway_epoch
                and work.request.ingress_seq<request.ingress_seq),None)
            if active and self.platform_allowed(request):
                if re.fullmatch(r'(?:不用了|先这样|先不查了|停止|结束|算了)[。！!~～\s]*',request.body.strip()):
                    self.agent.runtime.backend.finish(active,'aborted')
                    self.followups.clear(scope,request.user_id)
                    if hasattr(self.agent,'tasks'):self.agent.tasks.clear(request.scope,request.user_id)
                    self.control_event(request,'conversation_ended');return False
                if active.chat_coalesce_until and time.monotonic()<active.chat_coalesce_deadline:
                    if (request.message_id not in {row.message_id for row in active.chat_additions}
                            and len(active.chat_additions)<12
                            and sum(len(row.text.encode()) for row in active.chat_additions)+len(request.body.encode())<=8192):
                        active.chat_additions+= (ChatMessage(request.message_id,request.user_id,request.body,request.timestamp_ms,display_name=request.sender_name),)
                        active.chat_coalesce_until=min(time.monotonic()+settings['coalesce_seconds'],active.chat_coalesce_deadline)
                        self.control_event(request,'supplement_merged')
                    return False
            self.followups.observe(scope,request.user_id,request.message_id,request.timestamp_ms,request.ingress_seq,request.gateway_epoch)
            followup=self.followups.match(scope,request.message_id) if settings['followup_enabled'] else None
            if followup is not None and re.fullmatch(r'(?:不用了|先这样|先不查了|停止|结束|算了)[。！!~～\s]*',request.body.strip()):
                self.followups.clear(scope,request.user_id);self.controller.cancel(scope)
                if hasattr(self.agent,'tasks'):self.agent.tasks.clear(request.scope,request.user_id)
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
                if request.user_id in {row['user_id'] for row in settings['excluded_users']}:
                    self.control_event(request,'excluded_user');return False
                snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
                if self.configured(request,snapshot) is None:
                    status='service_disabled' if self.owners()[1].proactive(snapshot,request.conversation_id) else 'platform_unapproved'
                    self.control_event(request,status);return False
            if request.attachments:
                self.agent.images.remember(self.agent.image_scope(request),request.message_id,request.user_id,request.timestamp_ms,request.attachments)
            return self.controller.observe(scope,ChatMessage(request.message_id,request.user_id,
                request.body,request.timestamp_ms,display_name=request.sender_name))
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def candidate(self,request):
        if self.agent.audit_failures:return None
        settings=self.settings(request.conversation_id)
        followup=self.followups.match(self.scope(request),request.message_id) if settings['followup_enabled'] else None
        continuation=followup is not None
        if continuation:
            if not self.platform_allowed(request):return None
            snapshot=self.agent.runtime.config.snapshot().for_group(request.conversation_id)
            policy=self.configured(request,snapshot,continuation=True)
        else:
            if request.user_id in {row['user_id'] for row in settings['excluded_users']}:return None
            snapshot=self.agent.runtime.proactive_snapshot(request.conversation_id)
            policy=self.configured(request,snapshot)
            if policy is None:return None
        model_cost=maximum_cost_micro(snapshot.profile)*self.agent.budget.model_calls
        remaining=self.controller.remaining_cost(self.scope(request))
        if remaining is not None:model_cost=min(model_cost,remaining)
        if model_cost<=0:
            self.control_event(request,'budget_exhausted');return None
        cost=model_cost
        web=(self.agent.runtime.capabilities if continuation else self.owners()[1]).web(snapshot)
        if web:
            cost+=self.agent.budget.model_calls*web.max_calls*web.unit_cost_micro
            # Reserve the model loop and only the search budget this group can fund.
            # Each actual HTTP still checks persistent costs before it can start.
        if not continuation and web:
            preflight=service_request_id('proactive',request.instance_id,request.app_id,
                request.conversation_id,'budget/'+request.request_id)
            try:
                permit=self.agent.service_budget.check(policy,'proactive',snapshot.profile,
                    preflight,math.ceil(model_cost),reply_times=self.agent.store.proactive_replies(request.scope))
            except ServiceError:
                self.control_event(request,'budget_exhausted');return None
            spent=max(permit.spent_cost_micro,self.controller.status(self.scope(request))['hourly_cost_micro'])
            cost=min(math.ceil(cost),math.floor(policy.proactive.max_cost_micro_per_hour-spent))
            if cost<math.ceil(model_cost):
                self.control_event(request,'budget_exhausted');return None
        candidate=self.controller.candidate(self.scope(request),reserve_cost_micro=math.ceil(cost))
        if candidate is None:return None
        last=candidate.messages[-1]
        if (last.message_id,last.speaker_id,last.text,last.timestamp_ms)!=(request.message_id,request.user_id,request.body,request.timestamp_ms):
            self.controller.cancel(self.scope(request));return None
        followup=self.followups.match(self.scope(request),request.message_id)
        if followup is not None:candidate=replace(candidate,followup=followup,continuation=True)
        if continuation:return candidate,None,snapshot,'continuation/'+candidate.candidate_id
        job=service_request_id('proactive',request.instance_id,request.app_id,request.conversation_id,candidate.candidate_id)
        try:self.agent.service_budget.check(policy,'proactive',snapshot.profile,job,candidate.reserved_cost_micro,
            reply_times=self.agent.store.proactive_replies(request.scope))
        except ServiceError:
            self.control_event(request,'budget_exhausted')
            self.controller.decide(candidate,'skip',actual_cost_micro=0);return None
        if snapshot.fingerprint:self.agent.runtime.usage.configuration(snapshot.profile.config_version,snapshot.fingerprint)
        return candidate,policy,snapshot,job

    def observation_delay(self,request):
        scope=self.scope(request);settings=self.settings(request.conversation_id)
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

    async def coalesce_addressed(self,work,content):
        while work.active and work.chat_coalesce_until>time.monotonic():
            await asyncio.sleep(work.chat_coalesce_until-time.monotonic())
        work.chat_coalesce_until=0
        if work.chat_additions:
            content+='\n\n同一用户随后补充：\n'+'\n'.join(row.text for row in work.chat_additions)
        return content

    def remember_question(self,work,message_id,text):
        if not work.chat or (work.proactive is not None and not work.continuation) or work.request.kind!='group':return
        try:
            settings=self.settings(work.request.conversation_id)
            if not settings['followup_enabled'] or not self.platform_allowed(work.request):return
            generation=(self.followups.changed(self.scope(work.request),work.request.user_id) if work.continuation
                        else getattr(work,'followup_generation',None))
            originals=self.agent.store.read(work.request.scope,[work.request.message_id])['messages']
            if len(originals)!=1 or originals[0]['role']!='user' or originals[0]['speaker_id']!=work.request.user_id:return
            original=originals[0]
            self.followups.delivered(self.scope(work.request),generation,
                work.request.user_id,work.request.message_id,original['text'],original['reply_to'],original['display_name'],original['timestamp_ms'],work.request.ingress_seq,work.request.gateway_epoch,message_id,text,
                int(self.agent.runtime.backend.clock()*1000),window=settings['followup_window_seconds'],request_id=work.request.request_id)
            group=self.controller.groups.get(self.scope(work.request))
            if group and group.opportunity is not None:
                group.opportunity.knowledge_leases.clear();group.opportunity=None
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):pass

    def finish(self,work):
        self.trace(work,'finished',status=work.terminal_status,delivered=work.proactive_delivered,
                   model_attempts=work.model_attempts)
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
            settings=self.settings(work.request.conversation_id)
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
            if followup is not None and (self.followups.match(self.scope(work.request),work.request.message_id)!=followup
                    or not self.agent.store.source_valid(work.request.scope,followup.source_id,followup.source_time)
                    or not self.agent.store.source_valid(work.request.scope,followup.message_id,followup.delivered_ms)):
                return False
            return (policy==work.service_policy and all(self.agent.store.source_valid(work.request.scope,row.message_id,row.timestamp_ms)
                    for row in work.proactive.messages)
                    and (self.controller.can_send(work.proactive) if sending else self.controller.valid(work.proactive)))
        except (ModelError,ServiceError,ValueError,OSError,sqlite3.Error):return False

    def history(self,work):
        # Merge scoped participating history with frozen observations; only a new ACK persists observations.
        followup=work.proactive.followup
        rows=list(work.proactive.messages[-12:] if followup else work.proactive.messages[-48:])
        if followup:rows=[row for row in rows if row.message_id in followup.observed_ids]
        saved={}
        if not followup:
            saved={row['message_id']:ChatMessage(**row) for row in self.agent.store.recent(work.request.scope,
                since_ms=int((self.agent.runtime.backend.clock()-900)*1000))}
            merged=dict(saved)
            for row in rows:merged.setdefault(row.message_id,row)
            current=merged.pop(work.request.message_id)
            rows=sorted(merged.values(),key=lambda row:row.timestamp_ms)
            # The current observation stays last even when QQ and ACK clocks differ.
            rows.append(current)
        protected={work.request.message_id}
        recent_reply=next((row for row in reversed(rows) if row.role=='assistant'),None)
        if recent_reply:protected.update((recent_reply.message_id,recent_reply.reply_to))
        while rows and (len(rows)>48 or sum(len(json.dumps(asdict(row),ensure_ascii=False).encode()) for row in rows)>24576):
            index=next((i for i,row in enumerate(rows) if row.message_id not in protected),0)
            rows.pop(index)
        self.agent.bind_memory(work,[asdict(row) for row in rows if row.message_id in saved])
        messages=[]
        if followup:
            messages.append({'role':'user','content':json.dumps(asdict(ChatMessage(followup.source_id,followup.user_id,followup.source_text,
                followup.source_time,reply_to=followup.source_reply_to,display_name=followup.source_display_name)),ensure_ascii=False)})
            messages.append({'role':'assistant','content':json.dumps(asdict(ChatMessage(followup.message_id,'Taki',followup.text,
                followup.delivered_ms,'assistant',followup.source_id)),ensure_ascii=False)})
        for row in rows:
            value=asdict(row)
            if row.message_id==work.request.message_id and work.request.quoted_text:
                value['quoted_text']=work.request.quoted_text
                value['quote_status']='用户提供的引用预览，原作者和原消息身份未经核验'
            try:ref=self.agent.images.get(self.agent.image_scope(work.request),row.message_id)
            except ImageError:ref=None
            if ref:value['image_attachments']=''.join('[图片附件 '+item.id+' '+item.content_type+'；图像仅在本轮或有效引用中可见]' for item in ref.attachments)
            messages.append({'role':row.role,'content':json.dumps(value,ensure_ascii=False)})
        return messages

    def cost(self,work):
        rows=self.agent.runtime.usage.request_view(work.service_request_id)
        if any(row['billing_incomplete'] or row['state'] not in ('settled','released') for row in rows):return None
        return math.ceil(sum((Fraction(row['reference_cost_micro']) for row in rows),Fraction()))

    def opportunity_budget(self,work):
        candidates,cost=self.controller.opportunity_budget(work.proactive)
        request=work.request
        return dict(opportunity_requests=tuple(('continuation/'+candidate if work.continuation else service_request_id('proactive',request.instance_id,
            request.app_id,request.conversation_id,candidate)) for candidate in candidates),
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
        if mode=='reply':require(isinstance(value['text'],str) and 0<len(value['text'].strip())<=4000,'model_protocol_error')
        if mode=='defer':require(type(value['delay_seconds']) is int and 2<=value['delay_seconds']<=5,'model_protocol_error')
        self.controller.decide(work.proactive,mode,actual_cost_micro=self.cost(work),defer_seconds=value['delay_seconds'] if mode=='defer' else 2)
        self.trace(work,'decision',decision=mode,model_attempts=work.model_attempts)
        work.participation=mode
        return value['text'].strip() if mode=='reply' else None

    def delivered(self,work):
        self.controller.delivered(work.proactive)

    def clear(self,scope):
        self.controller.clear(tuple(scope.values()))
        self.followups.clear(tuple(scope.values()))
