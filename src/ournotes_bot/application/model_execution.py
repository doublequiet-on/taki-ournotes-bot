# L3: Bounded model scheduling and authenticated one-use execution challenges.
# Effects: In-memory deadlines/futures only; no provider, persistence or platform IO.
from __future__ import annotations
import asyncio
import time
import uuid
from collections import Counter
from dataclasses import asdict

from .backend_protocol import ProtocolError, require, identity


class ModelScheduler:
    def __init__(self, *, running=5, waiting=20, user_waiting=3, wait_seconds=60, execution_seconds=30):
        self.limit, self.capacity, self.user_capacity = running, waiting, user_waiting
        self.wait_seconds, self.execution_seconds = wait_seconds, execution_seconds
        self.running = Counter()
        self.queue = []
        self.tasks = set()
        self.closed = False

    def dispatch(self):
        for user, future in list(self.queue):
            if sum(self.running.values()) >= self.limit:
                break
            if not self.running[user] and not future.done():
                self.queue.remove((user, future))
                self.running[user] += 1
                future.set_result(None)

    async def run(self, user, operation):
        require(not self.closed, 'not_ready', 503)
        future = asyncio.get_running_loop().create_future()
        immediate = not self.running[user] and sum(self.running.values()) < self.limit
        if not immediate:
            require(len(self.queue) < self.capacity and sum(u == user for u, _ in self.queue) < self.user_capacity,
                    'queue_full', 503)
        item = (user, future)
        self.queue.append(item)
        self.dispatch()
        try:
            await asyncio.wait_for(asyncio.shield(future), self.wait_seconds)
        except BaseException as exc:
            if item in self.queue:
                self.queue.remove(item)
            elif future.done() and not future.cancelled():
                self.running[user] -= 1
                if not self.running[user]: del self.running[user]
            future.cancel()
            self.dispatch()
            if isinstance(exc, asyncio.TimeoutError):
                raise ProtocolError('queue_timeout', 503) from None
            raise
        deadline = time.monotonic() + self.execution_seconds

        async def execute():
            try:
                return await operation(deadline)
            finally:
                self.running[user] -= 1
                if not self.running[user]: del self.running[user]
                self.dispatch()

        task = asyncio.create_task(execute())
        self.tasks.add(task)
        def done(task):
            self.tasks.discard(task)
            if not task.cancelled(): task.exception()  # Late completion never publishes a reply.
        task.add_done_callback(done)
        # Timed-out callers stop waiting; the real worker keeps its slot until finished.
        return await asyncio.wait_for(asyncio.shield(task), self.execution_seconds)

    async def close(self):
        self.closed = True
        for _, future in self.queue:
            if not future.done(): future.cancel()
        self.queue.clear()
        await asyncio.gather(*self.tasks, return_exceptions=True)


class ModelPermissions:
    def __init__(self, backend, *, clock=time.monotonic):
        self.backend, self.clock = backend, clock
        self.pending = {}
        self.decisions = {}
        self.versions = {}

    def challenge_view(self, work):
        entry = self.pending.get(work.delivery)
        if entry is None or self.clock() >= entry['deadline']:
            return None
        return {**entry['binding'], 'remaining_ms': max(0, int((entry['deadline']-self.clock())*1000)),
                'admission_policy_version': {k: v for k, v in asdict(work.request.policy).items() if k != 'natural_query'}}

    async def acquire(self, work, execution_deadline):
        request = work.request
        require(work.active and request.policy.natural_query == 'model' and
                self.backend.ledger.active(request.gateway_epoch), 'policy_denied', 409)
        require(self.clock() < execution_deadline, 'execution_timeout', 409)
        require(work.delivery not in self.pending, 'permission_pending', 409)
        require(work.model_attempts < 2, 'model_attempt_limit', 409)
        work.model_attempts += 1
        binding = dict(request_id=request.request_id, attempt_id=uuid.uuid4().hex, nonce=uuid.uuid4().hex,
                       scope=request.scope, user_id=request.user_id, backend_epoch=self.backend.runtime,
                       gateway_epoch=request.gateway_epoch)
        future = asyncio.get_running_loop().create_future()
        entry = dict(binding=binding, deadline=min(self.clock()+3, execution_deadline), future=future)
        self.pending[work.delivery] = entry
        try:
            decision = await asyncio.wait_for(future, max(.001, entry['deadline']-self.clock()))
            require(decision['decision'] == 'allow', 'policy_denied', 409)
            deadline = min(entry['deadline'], decision['received'] + decision['ttl_ms']/1000, execution_deadline)
            require(work.active and self.clock() < deadline, 'permission_expired', 409)
            return deadline
        finally:
            self.pending.pop(work.delivery, None)

    def decide(self, request_id, attempt_id, payload, epoch):
        row = self.backend.ledger.request(request_id)
        require(row is not None, 'unknown_request', 404)
        work = self.backend.works.get(row['delivery'])
        require(work is not None and work.active, 'inactive_request', 409)
        previous = self.decisions.get(attempt_id)
        if previous is not None:
            require(previous == payload and previous['request_id'] == request_id,
                    'permission_conflict', 409)
            require(epoch == previous['gateway_epoch'] and self.backend.ledger.active(epoch), 'owner_epoch', 409)
            return {'recorded': True, 'duplicate': True}
        entry = self.pending.get(work.delivery)
        require(entry is not None, 'permission_expired', 409)
        require(isinstance(payload, dict) and set(payload) == set(entry['binding']) | {
            'decision', 'current_policy_version', 'reason', 'ttl_ms'}, 'permission_shape')
        binding = entry['binding']
        require(epoch == binding['gateway_epoch'] and self.backend.ledger.active(epoch), 'owner_epoch', 409)
        require(attempt_id == binding['attempt_id'] and all(payload[k] == v for k, v in binding.items()),
                'permission_identity', 409)
        require(payload['decision'] in ('allow', 'deny') and identity(payload['reason']), 'permission_decision')
        require(type(payload['ttl_ms']) is int and 0 < payload['ttl_ms'] <= 1000, 'permission_ttl')
        version = payload['current_policy_version']
        require(isinstance(version, dict) and set(version) == {'epoch','global_revision','group_revision'}
                and identity(version['epoch']) and all(type(version[k]) is int and 0 <= version[k] < 2**53
                for k in ('global_revision','group_revision')), 'policy_version')
        require(version['epoch'] == work.request.policy.epoch, 'policy_epoch', 409)
        scope = (version['epoch'], *binding['scope'].values())
        admission = (work.request.policy.global_revision, work.request.policy.group_revision)
        prior = tuple(max(a,b) for a,b in zip(admission, self.versions.get(scope, admission)))
        current = (version['global_revision'], version['group_revision'])
        require(all(a >= b for a,b in zip(current, prior)), 'policy_revision', 409)
        require(self.clock() < entry['deadline'], 'permission_expired', 409)
        self.versions[scope] = current
        if len(self.versions) > 1024:
            del self.versions[next(iter(self.versions))]
        self.decisions[attempt_id] = dict(payload)
        if len(self.decisions) > 128:
            del self.decisions[next(iter(self.decisions))]
        entry['future'].set_result({**payload, 'received': self.clock()})
        return {'recorded': True, 'duplicate': False}

    def abort(self, work):
        entry = self.pending.get(work.delivery)
        if entry and not entry['future'].done():
            entry['future'].set_exception(ProtocolError('inactive_request', 409))
