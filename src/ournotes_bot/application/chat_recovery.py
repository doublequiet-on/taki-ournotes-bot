# L3: Exact final-text staging and receipt-bound history projection.
# Effects: Local ChatStore/Ledger transactions; never sends or restores send rights.
import json
import time
from dataclasses import asdict
from ..domain.model_calls import ModelError
from ..domain.knowledge import KnowledgeError
from ..domain.story import StoryError

from .knowledge_service import KnowledgeService
from .chat_jobs import ServiceError


class ChatRecovery:
    def __init__(self,agent):
        self.agent=agent
        self.store=agent.store
        self.runtime=agent.runtime
        self.ledger=self.runtime.backend.ledger

    def stage(self,work):
        context=work.participating_history
        if work.proactive is None:
            row=self.store.db.execute('SELECT context FROM runs WHERE id=?',(work.delivery,)).fetchone()
            context=json.loads(row['context']) if row else ()
        sources={work.request.message_id:work.request.timestamp_ms}
        reply_to=None
        for item in context:
            source=json.loads(item['content'])
            reference=source.get('image_reference',source)
            if 'message_id' in reference:
                sources[reference['message_id']]=reference['timestamp_ms']
                if item['role']=='user' and 'message_id' in source:reply_to=source['message_id']
        for mid in work.memory_sources:
            rows=self.store.read(work.request.scope,[mid])['messages']
            if len(rows)!=1:raise ValueError('chat_memory_deleted')
            sources[mid]=rows[0]['timestamp_ms']
        for scope,mid in work.image_sources:
            sources[mid]=self.agent.images.get(scope,mid).timestamp_ms
        snapshot=work.primary_model_snapshot if work.budget_fallback else work.model_snapshot
        proof=dict(scope=work.request.scope,proactive=work.proactive is not None and not work.continuation,
            fingerprint=snapshot.fingerprint,config_version=snapshot.profile.config_version,
            service=asdict(work.service_policy) if work.service_policy is not None else None,
            proactive_authority=work.proactive_authority,
            story=list(work.story_versions),knowledge=KnowledgeService.retention_proof(
                lease for reply in work.prepared for lease in reply.evidence),
            vision=asdict(work.vision_capability) if work.vision_capability else None,
            web=asdict(work.web_capability) if work.web_capability else None)
        if work.quote_preview_reference:
            proof['quote_preview_reference']=work.quote_preview_reference
        expires=min(self.store.clock()+86400,self.store.clock()+work.content_deadline-time.monotonic())
        staged=[]
        for position,reply in enumerate(work.prepared):
            if reply.business_status!='success' or reply.image or not reply.text:continue
            staged.append(self.store.stage_final(work.delivery,str(position),work.request.scope,reply.text,proof,
                [dict(message_id=mid,timestamp_ms=stamp) for mid,stamp in sources.items()],reply_to,expires))
        return staged

    def valid(self,proof,*,projected=False):
        try:return self._valid(proof,projected=projected)
        except (OSError,ModelError,KnowledgeError,StoryError,ServiceError):return None

    def _valid(self,proof,*,projected):
        if proof.get('quote_preview_reference') and not self.store.detached_quote_valid(
                proof['scope'],proof['quote_preview_reference']):return False
        if proof['story'] and (self.agent.story is None or not all(self.agent.story.valid(*v) for v in proof['story'])):return False
        knowledge=self.runtime.backend.knowledge
        if proof['knowledge'] and (knowledge is None or not knowledge.validate_retention(proof['knowledge'])):return False
        # A plain delivered text does not lose its retention right merely because
        # a different chat model is selected later.
        if projected and not proof['proactive'] and proof['vision'] is None and proof['web'] is None:return True
        scope=proof['scope']
        snapshot=(self.runtime.proactive_snapshot(scope['conversation_id']) if proof['proactive'] else self.runtime.config.snapshot())
        if not proof['proactive'] and scope['kind']=='group':snapshot=snapshot.for_group(scope['conversation_id'])
        if snapshot.fingerprint!=proof['fingerprint'] or snapshot.profile.config_version!=proof['config_version']:return False
        capabilities=(self.runtime.proactive_capabilities if proof['proactive'] and self.runtime.dedicated_proactive() else self.runtime.capabilities)
        if proof['proactive']:
            services=self.agent.proactive.owners()[0]
            policy=services.policy(snapshot,scope['conversation_id'])
            if policy is None and services.code not in ('chat_services_absent','chat_service_unapproved',
                    'chat_service_revoked','chat_service_profile_mismatch','chat_service_currency_mismatch'):
                raise ServiceError('chat_service_retention_unavailable')
            if (policy is None or asdict(policy)!=proof['service']
                    or capabilities.generation(snapshot)!=proof['proactive_authority']
                    or not capabilities.proactive(snapshot,scope['conversation_id'])):return False
        for kind in ('vision','web'):
            if proof[kind] is not None:
                current=getattr(capabilities,kind)(snapshot)
                if current is None or asdict(current)!=proof[kind]:return False
        return True

    def recover(self,delivery=None,*,scope=None):
        # Also expire candidates staged before a crash that prevented Ledger.prepared.
        self.store.sweep()
        ready=True
        scope_key=self.store._scope(scope) if scope else None
        for stored in self.store.db.execute('SELECT * FROM chat_projection_history WHERE (? IS NULL OR scope=?)',
                                             (scope_key,scope_key)).fetchall():
            allowed=self.valid(json.loads(stored['proof']),projected=True)
            if allowed is None:ready=False
            elif not allowed:
                self.store.revoke_projection(stored)
                self.ledger.projection_done(stored['delivery'],stored['part'],'blocked')
        receipts=self.ledger.projection_rows(delivery)
        if delivery is None:
            registered={(row['delivery'],row['part']) for row in receipts}
            for row in self.store.db.execute('SELECT * FROM chat_final_candidates WHERE (? IS NULL OR scope=?)',
                                             (scope_key,scope_key)).fetchall():
                if (row['delivery'],row['part']) in registered:continue
                allowed=self.valid(json.loads(row['payload'])['proof'])
                if allowed is None:ready=False
                elif not allowed:
                    with self.store.db:self.store.drop_final(row['delivery'],row['part'])
        for receipt in receipts:
            if receipt['state'] in ('done','expired','gap'):continue
            key=(receipt['delivery'],receipt['part'])
            projected=self.store.projected(*key)
            if projected is not None:
                if projected['digest']!=receipt['digest']:raise ValueError('chat_projection_conflict')
                self.ledger.projection_done(*key,'done');continue
            row=self.store.final_candidate(*key)
            if row is not None and scope_key is not None and row['scope']!=scope_key:continue
            if receipt['state']=='blocked' or receipt['expires']<=self.store.clock():
                with self.store.db:self.store.drop_final(*key)
                self.ledger.projection_done(*key,'blocked' if receipt['state']=='blocked' else 'expired')
                continue
            if row is None:
                if receipt['state']=='pending':self.ledger.projection_done(*key,'gap')
                continue
            if row['digest']!=receipt['digest']:raise ValueError('chat_projection_conflict')
            allowed=self.valid(json.loads(row['payload'])['proof'])
            if allowed is None:
                ready=False;continue
            if not allowed:
                with self.store.db:self.store.drop_final(*key)
                self.ledger.projection_done(*key,'blocked');continue
            if receipt['state']!='pending':continue
            accepted=self.store.project_final(row,receipt['message'],receipt['acknowledged'])
            self.ledger.projection_done(*key,'done' if accepted else 'blocked')
        return ready
