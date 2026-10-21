# L3: Authenticated loopback API + single repository/parser/context owner.
# Effects: Bounded background preparations, SQLite delivery facts; never imports QQ or sends.
from __future__ import annotations
import asyncio
import hmac
import json as jsonlib
import logging
import os
import time
import uuid
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, replace

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
from .runtime_tasks import finish_thread
from .backend_sources import CODE_FILES
from ..domain.query_policy import CURRENT_POLICY

logger = logging.getLogger('ournotes_bot.backend')


@dataclass
class Work:
    request: Request
    delivery: str
    key: ContextKey
    operation: object
    lineage: object
    lease: object
    order: object
    ended: asyncio.Event = field(default_factory=asyncio.Event)
    prepared: tuple = ()
    candidate: object = None
    active: bool = True
    lock: object = None
    lock_acquired: bool = False
    task: object = None


class Backend:
    def __init__(self, repository, parser, settings, *, instance_id, app_id, ledger_path,
                 clock=time.time, plan_capacity=64, byte_capacity=128_000_000, diagnostics_enabled=True):
        require(identity(instance_id) and identity(app_id), 'configured_identity')
        self.repository, self.parser, self.settings = repository, parser, settings
        self.instance_id, self.app_id, self.clock = instance_id, app_id, clock
        self.runtime = uuid.uuid4().hex
        self.code_digest = hashlib.sha256(b''.join((Path(__file__).parent/name).read_bytes()
            for name in CODE_FILES)).hexdigest()
        self.ledger = Ledger(ledger_path, instance_id+'\0'+app_id, self.runtime, clock())
        self.diagnostics = Diagnostics(Path(ledger_path).with_name('diagnostics-v1.sqlite3'), enabled=diagnostics_enabled)
        self.admin = Admin(self)
        self.operations = Operations(Path(ledger_path).with_name('operations-v1.sqlite3'), instance_id+'\0'+app_id, clock=clock)
        self.contexts = ContextStore()
        self.gate = replies.QueryGate(settings.query_concurrency, settings.query_queue_limit, redact=True)
        self.sequencer = replies.ReplySequencer(enabled=settings.reply_order)
        self.works = {}
        self.tasks = set()
        self.plan_capacity, self.byte_capacity = plan_capacity, byte_capacity
        self.ready = False
        self.closing = False
        self.admission = asyncio.Lock()
        self.runtime_tasks = None
        self.notices = None
        self.data_owner = None

    def expire(self):
        for work in list(self.works.values()):
            if work.active and self.clock() >= work.request.timestamp_ms/1000+WINDOW:
                self.finish(work, 'expired')

    def finish(self, work, status):
        if not work.active:
            return
        # Persist termination before releasing its ownership; failures remain diagnosable.
        self.ledger.finish(work.delivery, status)
        work.active = False
        work.ended.set()
        work.prepared = ()
        work.candidate = None

    async def register(self, epoch):
        require(identity(epoch), 'gateway_epoch')
        async with self.admission:
            changed = self.ledger.register(epoch)
            if changed:
                for work in list(self.works.values()):
                    self.finish(work, 'aborted')
        return {'gateway_epoch': epoch, 'backend_epoch': self.runtime}

    async def submit(self, payload):
        started = time.perf_counter()
        request = Request.parse(payload, self.instance_id, self.app_id, self.clock())
        async with self.admission:
            self.expire()
            existing = self.ledger.request(request.request_id)
            if existing:
                require(existing['digest'] == request.content_digest, 'request_conflict', 409)
                return self.snapshot(existing, request.gateway_epoch)
            require(self.ready and not self.closing, 'not_ready', 503)
            age = self.clock()-request.timestamp_ms/1000
            require(0 <= age < WINDOW, 'message_expired', 409)
            require(len(self.works) < self.plan_capacity, 'plan_capacity', 503)
            require(self.retained_bytes() < self.byte_capacity, 'byte_capacity', 503)
            commands = split_commands(request.body)
            require(bool(commands), 'empty_commands')
            delivery = uuid.uuid4().hex
            self.ledger.admit(request, self.runtime, delivery, self.clock())
            admission_ms = (time.perf_counter()-started)*1000
            observed_commands = [{'kind': replies.command_kind(c), 'outcome': None}
                                 for c in commands[:self.settings.multi_command_limit]]
            observed_commands[0]['total_commands'] = len(commands)
            if len(commands) > self.settings.multi_command_limit:
                observed_commands.append({'kind':'overflow','outcome':'invalid_arguments'})
            self.diagnostics.admitted(request, delivery, self.runtime, self.clock(), observed_commands)
            self.diagnostics.span(uuid.uuid4().hex,request.request_id,'admission',admission_ms,self.clock(),outcome='ok')
            key = ContextKey(request.platform, request.kind, request.conversation_id, request.user_id)
            operation = parse_operation(commands[0]) if len(commands) == 1 else None
            lineage = self.contexts.lineage(key) if operation else None
            lease = self.contexts.begin(key) if operation is None else None
            order = await self.sequencer.issue()
            work = Work(request, delivery, key, operation, lineage, lease, order)
            self.works[delivery] = work
            work.task = asyncio.create_task(self.prepare(work, commands))
            self.tasks.add(work.task)
            work.task.add_done_callback(self.tasks.discard)
            return self.snapshot(self.ledger.request(request.request_id), request.gateway_epoch)

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
        policy_token = CURRENT_POLICY.set(work.request.policy)
        try:
            self.expire()
            if not work.active: return
            if work.operation:
                work.lock = self.contexts.lock(work.key)
                # A bounded lease covers lock wait + preparation + remote delivery.
                await asyncio.wait_for(work.lock.acquire(), max(.001, work.request.timestamp_ms/1000+WINDOW-self.clock()))
                work.lock_acquired = True
                self.expire()
                if not work.active: return
                current = (self.contexts.get(work.key) if work.lineage is not None
                           and self.contexts.lineage(work.key) == work.lineage else None)
                work.lease = self.contexts.begin(work.key, clear=False) if current is not None else None
                if work.request.policy.natural_query == 'off' and replies.is_ai_request(commands[0]):
                    result = replies.PreparedReply('本群已关闭 /问；请使用直接查询指令。')
                else:
                    result = (await self.prepare_call(work,self.gate.prepare_followup(current, work.operation, self.repository))
                              if current is not None else replies.PreparedReply(MISSING))
                parts = replies.expand_replies([result])
                parts = [replace(part,command_index=0,processing_route='continuation') for part in parts]
                statuses = {part.business_status or 'unknown' for part in parts}
                self.diagnostics.outcomes(work.request.request_id,
                    [next(iter(statuses)) if len(statuses) == 1 else 'mixed' if statuses else 'unknown'])
            else:
                parts, overflow = await self.prepare_call(work,replies.prepare_commands(commands, self.gate, self.repository,
                    self.parser, self.settings.multi_command_limit,
                    observe=lambda outcomes: self.diagnostics.outcomes(work.request.request_id, outcomes)))
                if overflow:
                    parts.append(replies.PreparedReply(tr(locale_for(commands[0]), 'too_many_commands',
                                                          limit=self.settings.multi_command_limit, total=len(commands))))
            self.diagnostics.prepared(work.request.request_id, parts)
            started = time.perf_counter()
            await self.sequencer.wait_turn(work.order)
            self.diagnostics.span(uuid.uuid4().hex, work.request.request_id, 'reply_order_wait',
                                  (time.perf_counter()-started)*1000, self.clock(), outcome='ok')
            self.expire()
            if not work.active:
                return
            work.prepared = tuple(parts)
            require(self.retained_bytes() <= self.byte_capacity, 'byte_capacity', 503)
            self.ledger.prepared(work.delivery, len(parts))
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
            # Only release a lock this task actually acquired (see acquired flag below).
            if work.lock is not None and work.lock_acquired:
                work.lock.release()
            await self.sequencer.release(work.order)
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
            elif isinstance(value, replies.DeliveryCursor): visit(value.remaining)
        for work in self.works.values(): visit(work.prepared)
        for context in self.contexts.retained_contexts(): visit(context)
        return total

    def live(self, row, epoch):
        work = self.works.get(row['delivery'])
        return bool(work and work.active and row['status'] == 'prepared' and row['epoch'] == epoch
                    and row['runtime'] == self.runtime and self.ledger.active(epoch)
                    and self.clock() < row['expires'])

    def snapshot(self, row, epoch):
        require(row is not None, 'unknown_request', 404)
        live = self.live(row, epoch)
        work = self.works.get(row['delivery'])
        parts = self.ledger.parts(row['delivery'])
        result = {'protocol': PROTOCOL, 'request_id': row['id'], 'delivery_id': row['delivery'],
                  'plan_version': 1, 'status': row['status'], 'send_allowed': live,
                  'expires_at': row['expires'], 'parts': []}
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
                            if part['mode'] == 'fallback' and len(reply.text) > 1800 else reply.text)
                    view.update(kind='text', text=text)
            result['parts'].append(view)
        return result

    def lookup(self, request_id, epoch):
        self.expire()
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
            if changed and live and payload.get('allow_context') is False:
                self.finish(work, 'aborted')
                live = False
            if changed and live:
                part = next(p for p in parts if p['id'] == part_id)
                reply = work.prepared[part['position']]
                candidate = reply.context
                long_fallback = part['mode'] == 'fallback' and len(reply.text) > 1800
                if payload['result'] == 'success':
                    if long_fallback: candidate = replies.fallback_cursor(reply)
                    if candidate is not None and (work.operation or len(split_commands(work.request.body)) == 1
                                                  or isinstance(candidate, replies.DeliveryCursor)):
                        work.candidate = candidate
                    if work.operation:
                        self.contexts.commit(work.lease, candidate, confirmed=True)
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
        self.closing, self.ready = True, False
        for work in list(self.works.values()): self.finish(work, 'aborted')
        # Cancellation retains QueryGate's thread slot until its actual work ends.
        tasks = list(self.tasks)
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.runtime_tasks: await self.runtime_tasks.close()
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
        return json({'ready': backend.ready, 'protocol': PROTOCOL, 'backend_epoch': backend.runtime, 'pid': os.getpid(),
                     'capability': CAPABILITY, 'backend_code_digest': backend.code_digest,
                     'background_tasks': bool(backend.runtime_tasks and backend.runtime_tasks.tasks and all(not task.done() for task in backend.runtime_tasks.tasks)),
                     'notices_enabled': backend.notices is not None,
                     'diagnostics_enabled': backend.diagnostics.db is not None,
                     'management_capabilities': ['admin_read_v1', 'telemetry_v1', 'group_policies_v1', 'ops_v1'],
                     'features': ['classify', 'gateway_retire', 'ack_context_guard', 'notice_gateway_v1']}, 200 if backend.ready else 503)
    async def classify(request):
        value = await read_json(request)
        require(isinstance(value, dict) and set(value) == {'body'} and isinstance(value['body'], str), 'classify_shape')
        require(len(value['body'].encode()) <= 16384, 'body_size')
        return json({'recognized': replies.recognizes_message(value['body'])})
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
                    web.get('/v1/requests/{rid}', lookup),
                    web.get('/v1/deliveries/{did}/parts/{pid}/image', image),
                    web.post('/v1/deliveries/{did}/events', events),
                    web.post('/v1/groups/events', group_event), web.post('/v1/notices/claims', notice_claims),
                    web.post('/v1/notices/{nid}/events', notice_event),
                    web.get('/v1/admin/overview', admin_overview), web.get('/v1/admin/requests', admin_requests),
                    web.get('/v1/admin/requests/{rid}', admin_detail), web.get('/v1/admin/groups', admin_groups),
                    web.get('/v1/admin/sources', admin_sources), web.get('/v1/admin/notices', admin_notices),
                    web.get('/v1/admin/query-metrics', admin_metrics), web.post('/v1/admin/telemetry', admin_telemetry),
                    web.get('/v1/admin/ops/jobs', ops_jobs), web.post('/v1/admin/ops/jobs', ops_submit),
                    web.get('/v1/admin/ops/jobs/{jid}', ops_detail),
                    web.get('/v1/admin/ops/notice-targets/{gid}', ops_notice_target),
                    web.get('/v1/admin/ops/export', ops_export), web.get('/v1/admin/ops/export/{rid}', ops_export)])
    async def lifetime(app):
        async def load():
            try:
                if initialize is not None: await asyncio.to_thread(initialize)
                if not backend.closing:
                    if backend.runtime_tasks: backend.runtime_tasks.start()
                    backend.ready = True
            except Exception as exc:
                logger.error('Initialization failed type=%s', type(exc).__name__)
        task = asyncio.create_task(load())
        async def reaper():
            while True:
                await asyncio.sleep(1)
                backend.expire()
        async def flush_observation():
            ticks=0
            while True:
                await asyncio.sleep(1)
                await finish_thread(backend.operations.flush_notices)
                ticks+=1
                if ticks%60==0:await finish_thread(backend.operations.housekeeping)
        expiry = asyncio.create_task(reaper())
        observation = asyncio.create_task(flush_observation())
        yield
        backend.closing = True
        expiry.cancel()
        observation.cancel()
        await asyncio.gather(expiry, observation, return_exceptions=True)
        await task  # initialization threads cannot be killed by canceling HTTP.
        await backend.close()
    app.cleanup_ctx.append(lifetime)
    return app
