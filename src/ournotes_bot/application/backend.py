# L3: Authenticated loopback API + single repository/parser/context owner.
# Effects: Bounded background preparations, SQLite delivery facts; never imports QQ or sends.
from __future__ import annotations
import asyncio
import hmac
import json as jsonlib
import logging
import os
import re
import sqlite3
import time
import uuid
import hashlib
from functools import partial
from concurrent.futures import TimeoutError as FutureTimeout, CancelledError as FutureCancelled
from pathlib import Path
from dataclasses import dataclass, field, replace, fields, is_dataclass

from aiohttp import web
from ..commands import split_commands, locale_for
from ..i18n import tr
from ..query.continuation import ContextKey, ContextStore, MISSING, parse_operation
from . import replies
from .backend_protocol import PROTOCOL, CAPABILITY, WINDOW, Request, ProtocolError, require, identity, event
from .delivery_ledger import Ledger
from .diagnostics import Diagnostics
from .admin import Admin
from .operations import Operations
from .runtime_tasks import finish_thread, drain_task
from .backend_sources import CODE_FILES
from ..domain.query_policy import (CURRENT_POLICY, CURRENT_QUERY_SCOPE, CURRENT_MODEL_START,
                                  CURRENT_EXECUTION_CHECK, CURRENT_CONTENT_DEADLINE, continuation_allowed)
from ..ai_client import AIClientError
from ..natural_query.query_agent import DeferredModel
from .model_execution import ModelScheduler, ModelPermissions
from .conversation_state import Conversations, ReadOnlyContext

logger = logging.getLogger('ournotes_bot.backend')


@dataclass
class Work:
    request: Request
    delivery: str
    key: ContextKey
    operation: object
    lineage: object
    lease: object
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    prepared: tuple = ()
    candidate: object = None
    active: bool = True
    lock: object = None
    lock_acquired: bool = False
    task: object = None
    anchor: object = None
    missing_anchor: bool = False
    model_attempts: int = 0
    model_attempt_limit: int = 2
    content_deadline: float = field(default_factory=lambda: time.monotonic()+600)
    model_snapshot: object = None
    model_subject: str | None = None
    model_error: str | None = None
    model_started_at: float | None = None
    native_claimed: bool = False
    hint: bool = False
    terminal_status: str | None = None
    terminal_pending: bool = False
    terminal_since: float | None = None
    terminal_retries: int = 0
    terminal_retry_at: float | None = None
    chat: bool = False
    story_versions: tuple = ()
    persona_version: str = 'taki-v0/legacy-derived'
    memory_sources: tuple = ()
    image_sources: tuple = ()
    proactive: object = None
    service_policy: object = None
    service_request_id: str = ''
    participating_history: tuple = ()
    participation: str = ''
    addressed_registered: bool = False
    proactive_delivered: bool = False
    private_search_texts: tuple = ()
    private_search_ids: tuple = ()
    public_question: str = ''
    entity_evidence: tuple = ()
    entity_source_version: str = ''
    web_capability: object = None
    knowledge_scope: object = None
    image_deadline: float = float('inf')
    image_bytes: int = 0


class Backend:
    def __init__(self, repository, parser, settings, *, instance_id, app_id, ledger_path,
                 clock=time.time, plan_capacity=64, byte_capacity=128_000_000, diagnostics_enabled=True,
                 reference_profiles=(), ledger_request_capacity=4096,
                 ledger_context_capacity=4096, ledger_epoch_capacity=4096):
        require(identity(instance_id) and identity(app_id), 'configured_identity')
        self.repository, self.parser, self.settings = repository, parser, settings
        self.instance_id, self.app_id, self.clock = instance_id, app_id, clock
        self.runtime = uuid.uuid4().hex
        self.code_digest = hashlib.sha256(b''.join((Path(__file__).parent/name).read_bytes()
            for name in CODE_FILES)).hexdigest()
        self.ledger = Ledger(ledger_path, instance_id+'\0'+app_id, self.runtime, clock(),
                             request_capacity=ledger_request_capacity, context_capacity=ledger_context_capacity,
                             epoch_capacity=ledger_epoch_capacity)
        self.diagnostics = Diagnostics(Path(ledger_path).with_name('diagnostics-v1.sqlite3'), enabled=diagnostics_enabled)
        self.admin = Admin(self)
        self.operations = Operations(Path(ledger_path).with_name('operations-v1.sqlite3'), instance_id+'\0'+app_id, clock=clock)
        self.contexts = ContextStore()
        self.conversations = Conversations(self.contexts, valid_evidence=self.valid_evidence)
        self.models = ModelScheduler()
        self.permissions = ModelPermissions(self)
        self.reference_profiles = frozenset(reference_profiles)
        self.gate = replies.QueryGate(settings.query_concurrency, settings.query_queue_limit, redact=True)
        self.works = {}
        self.tasks = set()
        self.plan_capacity, self.byte_capacity = plan_capacity, byte_capacity
        self.ready = False
        self.closing = False
        self.admission = asyncio.Lock()
        self.runtime_tasks = None
        self.knowledge = None
        self.model_runtime = None
        self.notices = None
        self.data_owner = None
        self._close_task = None
        self.reaper_task = None
        self.reaper_error = None

    def expire(self):
        self.conversations.sweep()
        sweep = getattr(self.parser, 'sweep', None)
        if sweep is not None: sweep()
        if self.model_runtime is not None: self.model_runtime.usage.reconcile()
        for work in list(self.works.values()):
            context_deadline = self.contexts.deadline(work.key)
            if work.active and (self.clock() >= work.request.timestamp_ms/1000+WINDOW or
                                work.lease is not None and time.monotonic() >= context_deadline or
                                work.anchor is not None and work.anchor.snapshot_id not in self.conversations.snapshots):
                self.finish(work, 'expired')

    def valid_evidence(self, leases):
        leases = tuple(leases)
        return not leases or self.knowledge is not None and self.knowledge.validate_current(leases)

    def finish(self, work, status):
        if work.terminal_status is not None or not work.active:
            return
        # Revoke permission immediately, but retain ownership until the fact is durable.
        work.terminal_status = status
        work.terminal_pending = True
        work.active = False
        if self.model_runtime and self.model_runtime.agent:self.model_runtime.agent.proactive.finish(work)
        if work.chat and self.model_runtime and self.model_runtime.agent:
            self.model_runtime.agent.audit(self.model_runtime.agent.store.finish,work.delivery,status)
        self.permissions.abort(work)
        work.ended.set()
        work.prepared = ()
        work.candidate = None
        work.anchor = None
        self.conversations.discard_unconfirmed(work.delivery)
        if status in ('expired', 'aborted') and work.task is not None and work.task is not asyncio.current_task():
            work.task.cancel()
        self.persist_termination(work)

    def persist_termination(self, work):
        try:
            self.ledger.finish(work.delivery, work.terminal_status)
        except sqlite3.Error as exc:
            if work.terminal_since is None:
                work.terminal_since = self.clock()
            delays = (1, 2, 4)
            work.terminal_retry_at = (time.monotonic()+delays[work.terminal_retries]
                                      if work.terminal_retries < len(delays) else None)
            logger.error('Delivery termination storage failed delivery=%s type=%s retries=%s',
                         work.delivery, type(exc).__name__, work.terminal_retries)
            return
        work.terminal_pending = False
        work.terminal_retry_at = None
        if work.task is None or work.task.done():
            self.works.pop(work.delivery, None)

    def retry_terminations(self):
        now = time.monotonic()
        for work in list(self.works.values()):
            if work.terminal_pending and work.terminal_retry_at is not None and now >= work.terminal_retry_at:
                work.terminal_retries += 1
                self.persist_termination(work)

    def maintenance_status(self):
        pending = [work for work in self.works.values() if work.terminal_pending]
        reaper_alive = self.reaper_task is None or not self.reaper_task.done()
        reason = ('delivery_storage_unavailable' if pending else
                  'maintenance_failed' if self.reaper_error or not reaper_alive else None)
        return {'reason': reason, 'pending_terminations': len(pending),
                'oldest_pending_seconds': max((max(0, self.clock()-work.terminal_since) for work in pending), default=0),
                'exhausted_terminations': sum(work.terminal_retry_at is None for work in pending),
                'reaper_alive': reaper_alive, 'reaper_error': self.reaper_error}

    def accepting(self):
        return self.ready and not self.closing and self.maintenance_status()['reason'] is None

    def maintain(self):
        # Termination recovery must run even when unrelated expiry maintenance fails.
        self.retry_terminations()
        try:
            self.expire()
        except sqlite3.Error as exc:
            self.reaper_error = type(exc).__name__
            logger.error('Expiry maintenance storage failed type=%s', self.reaper_error)
        else:
            self.reaper_error = None

    def preparation_done(self, work, task):
        self.tasks.discard(task)
        if task.cancelled():
            self.finish(work, 'aborted')
        else:
            error = task.exception()
            if error is not None:
                logger.error('Preparation owner failed request=%s type=%s', work.request.request_id, type(error).__name__)
                self.finish(work, 'failed')
        if not work.terminal_pending:
            self.works.pop(work.delivery, None)

    async def register(self, epoch):
        require(identity(epoch), 'gateway_epoch')
        async with self.admission:
            changed = self.ledger.register(epoch)
            if changed:
                for work in list(self.works.values()):
                    self.finish(work, 'aborted')
                self.permissions.versions.clear()
        return {'gateway_epoch': epoch, 'backend_epoch': self.runtime}

    async def submit(self, payload, *, proactive=None):
        started = time.perf_counter()
        request = Request.parse(payload, self.instance_id, self.app_id, self.clock(), reference_profiles=self.reference_profiles)
        require(request.trigger!='observation','request_trigger')
        require((request.trigger=='proactive')==(proactive is not None),'proactive_admission')
        async with self.admission:
            self.expire()
            existing = self.ledger.request(request.request_id)
            if existing:
                require(existing['digest'] == request.content_digest, 'request_conflict', 409)
                return self.snapshot(existing, request.gateway_epoch)
            require(self.accepting(), 'not_ready', 503)
            age = self.clock()-request.timestamp_ms/1000
            require(0 <= age < WINDOW, 'message_expired', 409)
            require(len(self.works) < self.plan_capacity, 'plan_capacity', 503)
            require(self.retained_bytes() < self.byte_capacity, 'byte_capacity', 503)
            commands = split_commands(request.body)
            chat=bool(getattr(self.settings,'chat_engine','legacy')=='agent' and self.model_runtime and (self.model_runtime.is_chat(request.body)
                      or not request.body.strip() and request.attachments or proactive is not None)
                      and (proactive is not None or parse_operation(request.body) is None))
            if chat: commands=[request.body or '[图片]']
            require(bool(commands), 'empty_commands')
            delivery = uuid.uuid4().hex
            key = ContextKey(request.platform, request.kind, request.conversation_id, request.request_id)
            work = Work(request, delivery, key, None, None, None)
            work.chat=chat
            if proactive is not None:
                work.proactive,work.service_policy,work.model_snapshot,work.service_request_id=proactive
            self.ledger.admit(request, self.runtime, delivery, self.clock())
            self.works[delivery] = work
            try:
                if self.model_runtime and self.model_runtime.agent:
                    self.model_runtime.agent.proactive.start_addressed(work)
                    if proactive is not None:self.model_runtime.agent.store.service_run(delivery,request.scope,work.service_request_id)
                admission_ms = (time.perf_counter()-started)*1000
                observed_commands = [{'kind': replies.command_kind(c), 'outcome': None}
                                     for c in commands[:self.settings.multi_command_limit]]
                observed_commands[0]['total_commands'] = len(commands)
                if len(commands) > self.settings.multi_command_limit:
                    observed_commands.append({'kind':'overflow','outcome':'invalid_arguments'})
                self.diagnostics.admitted(request, delivery, self.runtime, self.clock(), observed_commands)
                self.diagnostics.span(uuid.uuid4().hex,request.request_id,'admission',admission_ms,self.clock(),outcome='ok')
                work.operation = parse_operation(commands[0]) if len(commands) == 1 else None
                work.anchor = self.conversations.resolve(request) if work.operation or request.reference or (not chat and
                    self.model_runtime is not None and len(commands)==1 and replies.is_ai_request(commands[0])) else None
                work.missing_anchor = request.reference is not None and work.anchor is None
                if chat and work.missing_anchor and (self.model_runtime.agent.context.contains(request,request.reference['message_id'])
                        or self.model_runtime.agent.image_contains(request,request.reference['message_id'])):
                    work.missing_anchor=False
                if work.anchor is not None:
                    work.key, work.lineage = work.anchor.key, work.anchor.lineage
                    work.content_deadline = min(work.content_deadline, work.anchor.expires)
                if not work.operation and work.anchor is None:
                    work.lease = self.contexts.begin(work.key)
                if self.model_runtime is not None and (chat or any(replies.is_ai_request(c) for c in commands)):
                    if proactive is None:self.model_runtime.capture(work)
                work.task = asyncio.create_task(self.prepare(work, commands))
                self.tasks.add(work.task)
                work.task.add_done_callback(lambda task: self.preparation_done(work, task))
            except Exception as exc:
                logger.error('Admitted request initialization failed request=%s type=%s', request.request_id, type(exc).__name__)
                self.finish(work, 'failed')
            return self.snapshot(self.ledger.request(request.request_id), request.gateway_epoch)

    async def prepare_command(self, work, content):
        if work.chat:
            return await self.model_runtime.prepare(work,content)
        if self.model_runtime is not None and replies.is_ai_request(content) and work.request.policy.natural_query!='off':
            return await self.model_runtime.prepare(work, content)
        if not replies.is_ai_request(content) or work.request.policy.natural_query == 'off' or not hasattr(self.parser, 'prepare_local'):
            return await self.gate.prepare(content, self.repository, self.parser)
        before = replies.catalog_version(self.repository)
        outcome = await self.gate.run(self.parser.prepare_local, content, self.repository)
        if isinstance(outcome, replies.PreparedReply):
            return outcome
        if isinstance(outcome, DeferredModel):
            loop = asyncio.get_running_loop()
            async def model(deadline):
                def start():
                    pending = asyncio.run_coroutine_threadsafe(self.permissions.acquire(work, deadline), loop)
                    try:
                        allowed_until = pending.result(timeout=3.1)
                    except (ProtocolError, FutureTimeout, FutureCancelled, asyncio.CancelledError):
                        pending.cancel()
                        raise AIClientError('current_permission_unavailable') from None
                    if not work.active or time.monotonic() >= allowed_until:
                        raise AIClientError('current_permission_expired')
                    consumed = False
                    def consume():
                        nonlocal consumed
                        if consumed or not work.active or time.monotonic() >= allowed_until:
                            raise AIClientError('current_permission_expired')
                        consumed = True
                    return consume
                token = CURRENT_MODEL_START.set(start)
                active_token = CURRENT_EXECUTION_CHECK.set(lambda: work.active and time.monotonic() < deadline)
                try:
                    return await finish_thread(partial(self.parser.resume_model, outcome, self.repository))
                finally:
                    CURRENT_MODEL_START.reset(token)
                    CURRENT_EXECUTION_CHECK.reset(active_token)
            try:
                # Cross-scene identity mapping is not assumed; retain trusted ingress scope.
                user = (work.request.kind, work.request.conversation_id, work.request.user_id)
                outcome = await self.models.run(user, model)
            except (ProtocolError, asyncio.TimeoutError) as exc:
                code = exc.code if isinstance(exc, ProtocolError) else 'execution_timeout'
                return replies.PreparedReply('当前 AI 请求较多或等待超时，请稍后重试。', business_status=code)
        if not work.active:
            return None
        return await self.gate.run(partial(replies.prepare_reply, log_content=False, outcome=outcome, catalog_before=before),
                                   content, self.repository, self.parser)

    async def prepare_call(self, work, operation):
        started, outcome = time.perf_counter(), 'error'
        try:
            result = await operation
            outcome = 'ok'
            return result
        finally:
            self.diagnostics.span(uuid.uuid4().hex,work.request.request_id,'prepare',
                (time.perf_counter()-started)*1000,self.clock(),outcome=outcome)

    async def prepare(self, work, commands):
        overflow = 0
        # Reserve space for the gateway's native mention (identity <=256 chars).
        # Private replies retain their original body budget.
        text_limit = 1500 if work.request.kind == 'group' else 1800
        policy_token = CURRENT_POLICY.set(work.request.policy)
        scope_token = CURRENT_QUERY_SCOPE.set((self.instance_id, self.app_id, work.request.request_id))
        deadline_token = CURRENT_CONTENT_DEADLINE.set(min(work.content_deadline,
            self.contexts.deadline(work.key) or work.content_deadline))
        try:
            self.expire()
            if not work.active: return
            work.lock = self.contexts.lock(ContextKey(work.request.platform,work.request.kind,
                work.request.conversation_id,'chat-agent') if work.chat else work.key)
            # Only this topic's predecessor owns the lock through its final ACK.
            await asyncio.wait_for(work.lock.acquire(), max(.001, work.request.timestamp_ms/1000+WINDOW-self.clock()))
            work.lock_acquired = True
            self.expire()
            if not work.active: return
            if work.missing_anchor:
                parts = [replies.PreparedReply(MISSING)]
            elif work.chat:
                # Triggered chat keeps a whole multiline message and serializes
                # its group through ACK, independently of query topic snapshots.
                result=await self.prepare_call(work,self.prepare_command(work,commands[0]))
                parts=[] if work.proactive is not None and not result.text else replies.expand_replies([result],text_limit=text_limit)
                self.diagnostics.outcomes(work.request.request_id,[result.business_status or 'unknown'])
            elif work.operation or work.anchor is not None:
                current = (self.contexts.get(work.key) if work.lineage is not None
                           and self.contexts.lineage(work.key) == work.lineage else None)
                if work.request.reference is not None and current is not None:
                    current = work.anchor.context
                work.lease = self.contexts.begin(work.key, clear=False) if current is not None else None
                if work.request.policy.natural_query == 'off' and replies.is_ai_request(commands[0]):
                    result = replies.PreparedReply('本群已关闭 /问；请使用直接查询指令。')
                elif current is not None and not continuation_allowed(getattr(current,'origin','unknown')):
                    result = replies.PreparedReply('当前策略不允许继续此前 /问 结果，请重新发送直接查询。')
                elif current is not None and work.operation and work.operation.kind == 'page':
                    result = self.conversations.page(work.anchor, work.operation.value)
                    result = result or replies.PreparedReply('页码超出范围，或这份结果已过期。')
                elif work.operation is None:
                    result = await self.prepare_command(work, commands[0]) if current is not None else replies.PreparedReply(MISSING)
                else:
                    query_context = current.query_context if isinstance(current,replies.DeliveryCursor) else current
                    native_detail = (self.model_runtime is not None and work.operation.kind != 'continue'
                                     and getattr(current,'origin','unknown') in ('local','model')
                                     and getattr(query_context,'meta',None) is None)
                    result = (await self.prepare_call(work,self.model_runtime.followup(work,current,work.operation)
                              if native_detail else self.gate.prepare_followup(current, work.operation, self.repository))
                              if current is not None else replies.PreparedReply(MISSING))
                parts = replies.expand_replies([result],text_limit=text_limit)
                parts = [replace(part,command_index=0,processing_route='continuation') for part in parts]
                statuses = {part.business_status or 'unknown' for part in parts}
                self.diagnostics.outcomes(work.request.request_id,
                    [next(iter(statuses)) if len(statuses) == 1 else 'mixed' if statuses else 'unknown'])
            else:
                parts, overflow = await self.prepare_call(work,replies.prepare_commands(commands, self.gate, self.repository,
                    self.parser, self.settings.multi_command_limit,
                    observe=lambda outcomes: self.diagnostics.outcomes(work.request.request_id, outcomes),
                    prepare=lambda command: self.prepare_command(work, command),text_limit=text_limit))
                if overflow:
                    parts.append(replies.PreparedReply(tr(locale_for(commands[0]), 'too_many_commands',
                                                          limit=self.settings.multi_command_limit, total=len(commands))))
            self.diagnostics.span(uuid.uuid4().hex, work.request.request_id, 'reply_order_wait',
                                  0, self.clock(), outcome='ok')
            self.expire()
            if not work.active:
                return
            # An image is itself a passive reply, bound to the original msg_id
            # by the gateway. Only an actual slow-query hint consumes a slot.
            if work.hint:
                parts = [work.prepared[0], *replies.reserve_progress_slot(parts,overflow=bool(overflow),text_limit=text_limit)]
            self.diagnostics.prepared(work.request.request_id, parts)
            parts = [replace(p, context=ReadOnlyContext(p.text, p.parse_origin, p.evidence))
                     if p.context is None and p.business_status!='processing_hint' else p for p in parts]
            work.prepared = tuple(parts)
            complete = list(parts)
            if parts and isinstance(parts[-1].context, replies.DeliveryCursor):
                complete.extend(parts[-1].context.remaining)
            self.conversations.capture(work, complete)
            require(self.retained_bytes() <= self.byte_capacity, 'byte_capacity', 503)
            self.ledger.prepared(work.delivery, len(parts), start=int(work.hint))
            if not parts:
                self.finish(work, 'finished')
            else:
                await asyncio.wait_for(work.ended.wait(), max(.001, work.request.timestamp_ms/1000+WINDOW-self.clock()))
        except asyncio.TimeoutError:
            self.finish(work, 'expired')
        except asyncio.CancelledError:
            self.finish(work, 'aborted')
            raise
        except Exception as exc:
            logger.error('Preparation failed request=%s type=%s', work.request.request_id, type(exc).__name__)
            self.finish(work, 'failed')
        finally:
            CURRENT_POLICY.reset(policy_token)
            CURRENT_QUERY_SCOPE.reset(scope_token)
            CURRENT_CONTENT_DEADLINE.reset(deadline_token)
            # Only release a lock this task actually acquired (see acquired flag below).
            if work.lock is not None and work.lock_acquired:
                work.lock.release()
            if not work.terminal_pending:
                self.works.pop(work.delivery, None)

    def retained_bytes(self):
        seen, total = set(), 0
        def visit(value):
            nonlocal total
            if value is None or id(value) in seen:
                return
            seen.add(id(value))
            if isinstance(value, bytes): total += len(value)
            elif isinstance(value, str): total += len(value.encode())
            elif isinstance(value, (tuple, list)):
                for item in value: visit(item)
            elif isinstance(value, replies.PreparedReply):
                for item in (value.image, value.text, value.pages, value.context, value.fallback_texts): visit(item)
            elif isinstance(value, replies.DeliveryCursor):
                visit(value.remaining)
                visit(value.query_context)
            elif isinstance(value, dict):
                for key, item in value.items(): visit(key); visit(item)
            elif is_dataclass(value):
                for item in fields(value): visit(getattr(value,item.name))
        for work in self.works.values():
            visit(work.prepared)
            visit(work.request.body)
        for context in self.contexts.retained_contexts(): visit(context)
        for snapshot in self.conversations.snapshots.values():
            for page in snapshot.pages: visit(page)
        return total

    def live(self, row, epoch):
        work = self.works.get(row['delivery'])
        if work and work.chat and getattr(self.settings,'chat_engine','legacy')!='agent':
            return False
        if work and work.story_versions and not self.model_runtime.agent.story_valid(work.story_versions):
            return False
        if work and work.chat and not self.model_runtime.agent.memory_valid(work):
            return False
        if work and work.chat and not self.model_runtime.agent.images_valid(work):
            return False
        if work and work.chat and not self.model_runtime.agent.entity_valid(work):
            return False
        if work and work.chat and not self.model_runtime.agent.web_valid(work):
            return False
        if work and work.proactive is not None and not self.model_runtime.agent.proactive.valid(work,sending=True):
            return False
        if not (work and work.active and (row['status'] == 'prepared' or work.hint and row['status']=='preparing')
                and row['epoch'] == epoch and row['runtime'] == self.runtime and self.ledger.active(epoch)
                and self.clock() < row['expires']):
            return False
        return (self.valid_evidence(lease for reply in work.prepared for lease in reply.evidence)
                and all(reply.evidence_deadline is None or time.monotonic() < reply.evidence_deadline
                        for reply in work.prepared))

    def snapshot(self, row, epoch):
        require(row is not None, 'unknown_request', 404)
        live = self.live(row, epoch)
        work = self.works.get(row['delivery'])
        parts = self.ledger.parts(row['delivery'])
        result = {'protocol': PROTOCOL, 'request_id': row['id'], 'delivery_id': row['delivery'],
                  'plan_version': 1, 'status': row['status'], 'send_allowed': live,
                  'expires_at': row['expires'], 'parts': []}
        if work and work.hint: result['processing_hint'] = True
        if work and work.active and row['epoch'] == epoch and self.ledger.active(epoch):
            challenge = self.permissions.challenge_view(work)
            if challenge is not None: result['pending_model_permission'] = challenge
        for part in parts:
            view = {key: part[key] for key in ('id', 'position', 'mode', 'status', 'attempt', 'message')}
            if live and work:
                reply = work.prepared[part['position']]
                view['business_status'] = reply.business_status or 'message'
                if reply.image and part['mode'] == 'primary':
                    cursor = reply.context
                    caption = (f'图片 {cursor.sent}/{cursor.total}'+('；/继续 发送其余图片。' if cursor.remaining else '；本次已全部发送。')
                               if isinstance(cursor, replies.DeliveryCursor) else ' ')
                    mime = 'image/png' if reply.image.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg'
                    view.update(kind='image', text=caption, image=f"/v1/deliveries/{row['delivery']}/parts/{part['id']}/image", mime=mime)
                else:
                    text = ('图片上传失败，完整文字已保留；发送 /继续 查看。'
                            if part['mode'] == 'fallback' and len(reply.text) > (1500 if work.request.kind == 'group' else 1800) else reply.text)
                    view.update(kind='text', text=text)
            result['parts'].append(view)
        return result

    def lookup(self, request_id, epoch):
        self.expire()
        row=self.ledger.request(request_id)
        work=self.works.get(row['delivery']) if row else None
        if (work and work.active and work.proactive is None and not work.hint and row['status']=='preparing'
                and work.model_started_at is not None and time.monotonic()-work.model_started_at>=8
                and row['epoch']==epoch and self.ledger.active(epoch)):
            self.ledger.processing_hint(work.delivery)
            work.hint=True
            work.prepared=(replies.PreparedReply('还在核实，请稍等。',business_status='processing_hint'),)
        return self.snapshot(self.ledger.request(request_id), epoch)

    def image(self, delivery, part_id, epoch):
        self.expire()
        row = self.ledger.by_delivery(delivery)
        require(row is not None, 'unknown_delivery', 404)
        require(self.live(row, epoch), 'inactive_delivery', 409)
        part = next((p for p in self.ledger.parts(delivery) if p['id'] == part_id), None)
        require(part is not None and part['mode'] == 'primary', 'unknown_image', 404)
        data = self.works[delivery].prepared[part['position']].image
        require(data is not None, 'unknown_image', 404)
        return data

    def apply_event(self, delivery, payload):
        payload = event(payload)
        self.expire()
        row = self.ledger.by_delivery(delivery)
        require(row is not None and row['id'] == payload['request_id'], 'delivery_identity', 404)
        require(row['epoch'] == payload['gateway_epoch'], 'owner_epoch', 409)
        kind, part_id = payload['event'], payload['part_id']
        parts = self.ledger.parts(delivery)
        work = self.works.get(delivery)
        live = self.live(row, payload['gateway_epoch'])
        if kind == 'ack':
            changed = self.ledger.ack(delivery, part_id, payload['attempt_id'], payload['result'], payload['message_id'])
            if changed and payload['result']=='success' and self.model_runtime and self.model_runtime.agent:
                self.model_runtime.agent.audit(self.model_runtime.agent.store.service_ack,delivery)
                if work and work.proactive is not None:work.proactive_delivered=True
            if work and work.hint and part_id=='0':
                # Progress occupies one ordinary send slot, but creates no topic
                # and never completes a request whose answer is still preparing.
                if changed and (payload['result']!='success' or payload.get('allow_context') is False):
                    self.finish(work,'aborted')
                return {'recorded':True,'duplicate':not changed,**self.snapshot(self.ledger.by_delivery(delivery),payload['gateway_epoch'])}
            if changed and live and payload.get('allow_context') is False:
                self.finish(work, 'aborted')
                live = False
            if changed and live:
                part = next(p for p in parts if p['id'] == part_id)
                reply = work.prepared[part['position']]
                candidate = reply.context
                text_limit = 1500 if work.request.kind == 'group' else 1800
                long_fallback = part['mode'] == 'fallback' and len(reply.text) > text_limit
                if work.chat and self.model_runtime.agent:
                    self.model_runtime.agent.acknowledge(work,part_id,payload['message_id'],reply.text,
                        payload['result'],allow_context=payload.get('allow_context') is not False)
                if payload['result'] == 'success':
                    if long_fallback: candidate = replies.fallback_cursor(reply,text_limit=text_limit)
                    if candidate is not None and (work.operation or len(split_commands(work.request.body)) == 1
                                                  or isinstance(candidate, replies.DeliveryCursor)):
                        work.candidate = candidate
                    if work.operation:
                        self.contexts.commit(work.lease, candidate, confirmed=True)
                    elif candidate is not None:
                        self.contexts.commit(work.lease, candidate, confirmed=True)
                    self.conversations.confirm(work, payload['message_id'], part['position'], candidate,
                                               fallback_notice=long_fallback)
                elif payload['result'] == 'uncertain' and isinstance(candidate, replies.DeliveryCursor):
                    work.candidate = replace(candidate, uncertain=True)
                    if work.operation: self.contexts.commit(work.lease, work.candidate, confirmed=True)
                terminal = (payload['result'] != 'success' or long_fallback or part['position'] == len(parts)-1)
                if terminal:
                    if not work.operation:
                        self.contexts.commit(work.lease, work.candidate, confirmed=work.candidate is not None)
                    self.finish(work, 'finished' if payload['result'] == 'success' else payload['result'])
            return {'recorded': True, 'duplicate': not changed, **self.snapshot(self.ledger.by_delivery(delivery), payload['gateway_epoch'])}
        if kind in ('aborted', 'batch_finished'):
            if kind == 'batch_finished':
                require(all(p['status'] == 'success' for p in parts), 'batch_incomplete', 409)
            if work: self.finish(work, 'aborted' if kind == 'aborted' else 'finished')
            return self.snapshot(self.ledger.by_delivery(delivery), payload['gateway_epoch'])
        # No new permission after expiry/reload, including a repeat of an old intent.
        require(live, 'inactive_delivery', 409)
        part = next((p for p in parts if p['id'] == part_id), None)
        if kind == 'upload_failed' and part is None:
            part = next((p for p in parts if p['id'] == part_id+':f'), None)
            require(part is not None and part['mode'] == 'fallback', 'unknown_part', 404)
            return self.snapshot(row, payload['gateway_epoch'])
        require(part is not None, 'unknown_part', 404)
        require(all(p['status'] == 'success' for p in parts if p['position'] < part['position']), 'part_order', 409)
        if kind == 'upload_failed':
            require(part['status'] == 'pending' and part['attempt'] is None
                    and work.prepared[part['position']].image is not None, 'fallback_transition', 409)
            if part['mode'] == 'primary': self.ledger.fallback(delivery, part_id)
            return self.snapshot(row, payload['gateway_epoch'])
        granted = self.ledger.intent(delivery, part_id, payload['attempt_id'])
        return {'permission': granted, 'msg_seq': part['position']+1}

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await drain_task(self._close_task)

    async def _close(self):
        self.closing, self.ready = True, False
        for work in list(self.works.values()): self.finish(work, 'aborted')
        # Cancellation retains QueryGate's thread slot until its actual work ends.
        tasks = list(self.tasks)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.models.close()
        self.conversations.clear()
        self.contexts.clear()
        if self.runtime_tasks: await self.runtime_tasks.close()
        if self.model_runtime is not None: await self.model_runtime.close()
        if self.knowledge is not None: self.knowledge.close()
        await self.operations.close()
        self.ledger.close()
        self.diagnostics.close()
        if self.data_owner: self.data_owner.close()


def create_app(backend, token, initialize=None):
    require(isinstance(token, str) and len(token) >= 32, 'backend_token_required')

    @web.middleware
    async def boundary(request, handler):
        try:
            supplied = request.headers.get('Authorization', '')
            require(hmac.compare_digest(supplied.encode(), ('Bearer '+token).encode()), 'unauthorized', 401)
            require(request.headers.get('X-Taki-Instance') == backend.instance_id, 'instance_mismatch', 403)
            if request.path.startswith('/v1/admin/') and request.method == 'GET':
                require(len(request.query) == len(set(request.query)), 'admin_parameters')
                if request.path not in ('/v1/admin/requests','/v1/admin/notices'):
                    require(not request.query, 'admin_parameters')
            return await handler(request)
        except ProtocolError as exc:
            return web.json_response({'error': exc.code, 'protocol': PROTOCOL}, status=exc.status)

    app = web.Application(middlewares=[boundary], client_max_size=32768)
    def json(value, status=200): return web.json_response(value, status=status, headers={'Cache-Control': 'no-store'})
    async def read_json(request):
        try:
            return await request.json()
        except (jsonlib.JSONDecodeError, UnicodeError):
            raise ProtocolError('invalid_json') from None
    async def health(request): return json({'alive': True, 'protocol': PROTOCOL})
    async def ready(request):
        accepting = backend.accepting()
        return json({'ready': accepting, 'protocol': PROTOCOL, 'backend_epoch': backend.runtime, 'pid': os.getpid(),
                     'maintenance': backend.maintenance_status(),
                     'capability': CAPABILITY, 'backend_code_digest': backend.code_digest,
                     'background_tasks': bool(backend.runtime_tasks and backend.runtime_tasks.tasks and all(not task.done() for task in backend.runtime_tasks.tasks)),
                     'notices_enabled': backend.notices is not None,
                     'diagnostics_enabled': backend.diagnostics.db is not None,
                     'management_capabilities': ['admin_read_v1', 'telemetry_v1', 'group_policies_v1', 'ops_v1'],
                     'features': ['classify', 'gateway_retire', 'ack_context_guard', 'notice_gateway_v1',
                                  'quoted_context_v1', 'fixed_snapshot_v1', 'current_model_permit_v1', 'independent_delivery_v1',
                                  'chat_observation_v1','chat_images_v1'],
                     'reference_profiles': sorted(backend.reference_profiles)}, 200 if accepting else 503)
    async def classify(request):
        value = await read_json(request)
        require(isinstance(value, dict) and set(value) == {'body'} and isinstance(value['body'], str), 'classify_shape')
        require(len(value['body'].encode()) <= 16384, 'body_size')
        return json({'recognized': replies.recognizes_message(value['body']) or bool(
            getattr(backend.settings,'chat_engine','legacy')=='agent' and backend.model_runtime and backend.model_runtime.is_chat(value['body']))})
    async def retire(request):
        value = await read_json(request)
        require(isinstance(value, dict) and set(value) == {'gateway_epoch'}, 'epoch_shape')
        epoch = value['gateway_epoch']
        require(identity(epoch), 'gateway_epoch')
        async with backend.admission:
            backend.ledger.retire(epoch)
            for work in list(backend.works.values()):
                if work.request.gateway_epoch == epoch: backend.finish(work, 'aborted')
        return json({'retired': True})
    async def register(request):
        value = await read_json(request)
        require(isinstance(value, dict) and set(value) == {'gateway_epoch'}, 'epoch_shape')
        return json(await backend.register(value['gateway_epoch']))
    async def submit(request): return json(await backend.submit(await read_json(request)), 202)
    async def lookup(request): return json(backend.lookup(request.match_info['rid'], request.headers.get('X-Taki-Epoch')))
    async def model_permission(request):
        return json(backend.permissions.decide(request.match_info['rid'], request.match_info['aid'],
                                              await read_json(request), request.headers.get('X-Taki-Epoch')))
    async def image(request):
        data = backend.image(request.match_info['did'], request.match_info['pid'], request.headers.get('X-Taki-Epoch'))
        mime = 'image/png' if data.startswith(b'\x89PNG\r\n\x1a\n') else 'image/jpeg'
        return web.Response(body=data, content_type=mime, headers={'Cache-Control': 'no-store'})
    async def events(request): return json(backend.apply_event(request.match_info['did'], await read_json(request)))
    def notices():
        require(backend.notices is not None, 'notifications_disabled', 503)
        return backend.notices
    async def group_event(request): return json(notices().group(await read_json(request)))
    async def notice_claims(request): return json(await notices().claims(await read_json(request)))
    async def notice_event(request): return json(notices().event(request.match_info['nid'], await read_json(request)))
    async def admin_overview(request):
        require(not request.query, 'admin_parameters')
        return json(backend.admin.overview())
    async def admin_requests(request): return json(backend.admin.listing(dict(request.query)))
    async def admin_detail(request): return json(backend.admin.detail(request.match_info['rid']))
    async def admin_groups(request): return json(backend.admin.groups())
    async def admin_sources(request): return json(backend.admin.sources())
    async def admin_notices(request): return json(backend.admin.notices(dict(request.query)))
    async def admin_metrics(request): return json(backend.admin.metrics())
    async def model_settings(request):
        from ..domain.model_calls import ModelError
        require(backend.model_runtime is not None,'model_admin_unavailable',503)
        owner=backend.model_runtime.admin
        try:
            if request.method=='GET': return json(owner.settings())
            require(backend.ready and not backend.closing,'backend_not_ready',503)
            require(backend.ledger.active(request.headers.get('X-Taki-Epoch')),'ops_gateway_epoch',409)
            value=await read_json(request)
            return json(owner.probe(value) if request.path.endswith('/probe') else owner.save(value))
        except ModelError as exc:
            raise ProtocolError(str(exc),409) from None
        except (OSError,sqlite3.Error):
            raise ProtocolError('model_admin_unavailable',503) from None
    async def model_group(request):
        from ..domain.model_calls import ModelError
        require(backend.model_runtime is not None,'model_admin_unavailable',503)
        gid=request.match_info['gid']
        require(isinstance(gid,str) and re.fullmatch('[A-Za-z0-9_-]{1,128}',gid),'admin_group')
        try:
            runtime=backend.model_runtime;profile=runtime.config.snapshot().for_group(gid).profile
            return json(runtime.usage.group_view(gid,profile))
        except ModelError as exc: raise ProtocolError(str(exc),409) from None
    async def chat_observation(request):
        require(backend.accepting(),'not_ready',503)
        value=await read_json(request)
        parsed=Request.parse(value,backend.instance_id,backend.app_id,backend.clock())
        require(parsed.trigger=='observation' and backend.ledger.active(parsed.gateway_epoch),'observation_admission',409)
        require(0<=backend.clock()-parsed.timestamp_ms/1000<WINDOW,'message_expired',409)
        agent=backend.model_runtime.agent if backend.model_runtime else None
        observed=bool(agent and getattr(backend.settings,'chat_engine','legacy')=='agent' and agent.proactive.observe(parsed))
        return json({'protocol':PROTOCOL,'observed':observed,'delay_ms':1500 if observed else 0})
    async def chat_candidate(request):
        from ..domain.model_calls import ModelError
        require(backend.accepting(),'not_ready',503)
        value=await read_json(request)
        parsed=Request.parse(value,backend.instance_id,backend.app_id,backend.clock())
        require(parsed.trigger=='proactive' and parsed.policy.natural_query=='model'
                and backend.ledger.active(parsed.gateway_epoch),'proactive_admission',409)
        require(0<=backend.clock()-parsed.timestamp_ms/1000<WINDOW,'message_expired',409)
        agent=backend.model_runtime.agent if backend.model_runtime else None
        if agent is None or getattr(backend.settings,'chat_engine','legacy')!='agent':return json({'protocol':PROTOCOL,'candidate':False})
        try:
            existing=backend.ledger.request(parsed.request_id)
            candidate=agent.proactive.candidate(parsed) if existing is None else None
            if existing is not None:
                require(existing['digest']==parsed.content_digest,'request_conflict',409)
                return json({'protocol':PROTOCOL,'candidate':True,'plan':backend.snapshot(existing,parsed.gateway_epoch)})
            if candidate is None:return json({'protocol':PROTOCOL,'candidate':False})
            return json({'protocol':PROTOCOL,'candidate':True,'plan':await backend.submit(value,proactive=candidate)})
        except ModelError:return json({'protocol':PROTOCOL,'candidate':False})
        except (sqlite3.Error,OSError,ValueError):raise ProtocolError('chat_service_unavailable',503) from None
    async def chat_memory(request):
        require(backend.model_runtime is not None and backend.model_runtime.agent is not None,'chat_unavailable',503)
        gid=request.match_info['gid']
        require(re.fullmatch('[A-Za-z0-9_-]{1,128}',gid),'admin_group')
        scope=dict(instance_id=backend.instance_id,app_id=backend.app_id,platform='qq',kind='group',conversation_id=gid)
        agent=backend.model_runtime.agent
        try:
            if request.method=='GET':return json(agent.store.status(scope))
            require(backend.ready and not backend.closing,'backend_not_ready',503)
            require(backend.ledger.active(request.headers.get('X-Taki-Epoch')),'ops_gateway_epoch',409)
            value=await read_json(request)
            require(isinstance(value,dict) and (set(value)=={'action'} and value['action']=='clear' or
                    set(value)=={'action','message_ids'} and value['action']=='delete'),'chat_memory_action')
            return json(agent.forget(scope,value['message_ids'] if value['action']=='delete' else None))
        except ValueError as exc:raise ProtocolError(str(exc)) from None
        except (OSError,sqlite3.Error):raise ProtocolError('chat_memory_unavailable',503) from None
    async def ops_jobs(request): return json(backend.operations.listing())
    async def ops_detail(request): return json(backend.operations.detail(request.match_info['jid']))
    async def ops_submit(request):
        require(backend.ready and not backend.closing, 'backend_not_ready', 503)
        require(backend.ledger.active(request.headers.get('X-Taki-Epoch')), 'ops_gateway_epoch', 409)
        return json(await backend.operations.submit(await read_json(request)), 202)
    async def ops_export(request): return json(backend.admin.export(request.match_info.get('rid')))
    async def ops_notice_target(request): return json(backend.admin.notice_target(request.match_info['gid']))
    async def admin_telemetry(request):
        return json(backend.admin.telemetry(await read_json(request), request.headers.get('X-Taki-Epoch')))
    app.add_routes([web.get('/v1/health', health), web.get('/v1/ready', ready),
                    web.post('/v1/gateways', register), web.post('/v1/gateways/retire', retire),
                    web.post('/v1/classify', classify), web.post('/v1/requests', submit),
                    web.post('/v1/chat/observations',chat_observation),web.post('/v1/chat/candidates',chat_candidate),
                    web.get('/v1/requests/{rid}', lookup),
                    web.post('/v1/requests/{rid}/model-permissions/{aid}', model_permission),
                    web.get('/v1/deliveries/{did}/parts/{pid}/image', image),
                    web.post('/v1/deliveries/{did}/events', events),
                    web.post('/v1/groups/events', group_event), web.post('/v1/notices/claims', notice_claims),
                    web.post('/v1/notices/{nid}/events', notice_event),
                    web.get('/v1/admin/overview', admin_overview), web.get('/v1/admin/requests', admin_requests),
                    web.get('/v1/admin/requests/{rid}', admin_detail), web.get('/v1/admin/groups', admin_groups),
                    web.get('/v1/admin/sources', admin_sources), web.get('/v1/admin/notices', admin_notices),
                    web.get('/v1/admin/query-metrics', admin_metrics), web.post('/v1/admin/telemetry', admin_telemetry),
                    web.get('/v1/admin/model', model_settings),web.post('/v1/admin/model', model_settings),
                    web.post('/v1/admin/model/probe',model_settings),web.get('/v1/admin/model/groups/{gid}',model_group),
                    web.get('/v1/admin/chat/groups/{gid}/memory',chat_memory),web.post('/v1/admin/chat/groups/{gid}/memory',chat_memory),
                    web.get('/v1/admin/ops/jobs', ops_jobs), web.post('/v1/admin/ops/jobs', ops_submit),
                    web.get('/v1/admin/ops/jobs/{jid}', ops_detail),
                    web.get('/v1/admin/ops/notice-targets/{gid}', ops_notice_target),
                    web.get('/v1/admin/ops/export', ops_export), web.get('/v1/admin/ops/export/{rid}', ops_export)])
    async def lifetime(app):
        async def load():
            try:
                if initialize is not None: await finish_thread(initialize)
                if not backend.closing:
                    if backend.runtime_tasks: backend.runtime_tasks.start()
                    if backend.model_runtime: backend.model_runtime.start()
                    backend.ready = True
            except Exception as exc:
                logger.error('Initialization failed type=%s', type(exc).__name__)
        task = asyncio.create_task(load(), name='backend-initialize')
        async def reaper():
            try:
                while True:
                    await asyncio.sleep(1)
                    backend.maintain()
            except Exception as exc:
                backend.reaper_error = type(exc).__name__
                logger.error('Expiry maintenance stopped type=%s', backend.reaper_error)
                raise
        async def flush_observation():
            ticks=0
            while True:
                await asyncio.sleep(1)
                await finish_thread(backend.operations.flush_notices)
                ticks+=1
                if ticks%60==0:await finish_thread(backend.operations.housekeeping)
        expiry = asyncio.create_task(reaper())
        backend.reaper_task = expiry
        observation = asyncio.create_task(flush_observation())
        async def shutdown():
            backend.closing = True
            expiry.cancel()
            observation.cancel()
            try:
                await asyncio.gather(expiry, observation, return_exceptions=True)
            finally:
                # Even a separately cancelled initializer owns its real thread
                # until finish_thread settles. Close stores only afterwards.
                try:
                    await task
                finally:
                    await backend.close()
        try:
            yield
        finally:
            # HTTP cleanup is a waiter, not the owner of the shutdown sequence.
            # Cancellation must not skip initializer draining or store closure.
            await drain_task(asyncio.create_task(shutdown()))
    app.cleanup_ctx.append(lifetime)
    return app
