# L3: Current-view query and deterministic detail orchestration; no chat runtime.
# Input: Backend-owned work, captured context, parser and query gate.
# Output: PreparedReply; preserves expiry/evidence checks before and after source IO.
# Effects: Bounded source reads/rendering, no model client, knowledge writer or QQ.
import time
from functools import partial

from ..data import DataError
from ..domain.model_calls import ModelError, require
from .backend_protocol import ProtocolError
from . import replies


def confirmed_page(backend, work):
    snapshot = backend.conversations.snapshots.get(work.anchor.snapshot_id)
    require(snapshot is not None and 0 <= work.anchor.page < len(snapshot.pages), 'context_expired')
    return snapshot.pages[work.anchor.page]


def check_query_work(backend, work, deadline, leases=()):
    require(work.active and time.monotonic() < deadline, 'execution_timeout')
    require(time.monotonic() < work.content_deadline, 'context_expired')
    if work.anchor:
        require(time.monotonic() < work.anchor.expires and work.anchor.snapshot_id in backend.conversations.snapshots, 'context_expired')
    if work.lease:
        require(backend.contexts.active(work.lease), 'context_expired')
    require(backend.valid_evidence(leases), 'knowledge_revoked')


def query_failure(code):
    text = {'context_expired': '上次话题已过期，请重新说明问题。',
            'knowledge_revoked': '依据资料已变化，这次回答已停止。请重新提问。',
            'source_unavailable': '当前资料核实失败，请稍后重试。',
            'query_busy': '当前查询较多，请稍后重试。',
            'execution_timeout': '这次查询处理超时，请稍后重试。'}
    return replies.PreparedReply(text.get(code, '查询暂时不可用，请稍后重试。'),
                                 business_status=code, parse_origin='policy', processing_route='local')


class QueryRuntime:
    def __init__(self, backend):
        self.backend = backend

    def check(self, work, deadline, leases=()):
        check_query_work(self.backend, work, deadline, leases)

    def evidence(self, work):
        if not work.anchor:
            return ()
        confirmed_page(self.backend, work)
        return tuple(getattr(work.anchor.context, 'evidence', ()))

    async def current_repository(self):
        try:
            view = await self.backend.gate.run(self.backend.repository.current_query_view)
            require(not isinstance(view, replies.PreparedReply), 'query_busy')
            return view
        except (DataError, OSError, ValueError, KeyError, TypeError):
            raise ModelError('source_unavailable') from None

    async def followup(self, work, context, operation, *, validate=None, failure=query_failure):
        validate = validate or self.check
        try:
            leases = self.evidence(work)
            validate(work, work.content_deadline, leases)
            repository = await self.current_repository()
            validate(work, work.content_deadline, leases)
            return await self.backend.gate.prepare_followup(context, operation, repository)
        except (ModelError, ProtocolError) as exc:
            return failure(exc.code if isinstance(exc, ProtocolError) else str(exc))

    async def prepare(self, work, content):
        try:
            leases = self.evidence(work)
            self.check(work, work.content_deadline, leases)
            repository = await self.current_repository()
            self.check(work, work.content_deadline, leases)
            outcome = await self.backend.gate.run(self.backend.parser.prepare_native_local, content, repository)
            if isinstance(outcome, replies.PreparedReply):
                return outcome
            self.check(work, work.content_deadline, leases)
            return await self.backend.gate.run(partial(replies.prepare_reply, log_content=False, outcome=outcome),
                                               content, repository, self.backend.parser)
        except (ModelError, ProtocolError) as exc:
            return query_failure(exc.code if isinstance(exc, ProtocolError) else str(exc))
