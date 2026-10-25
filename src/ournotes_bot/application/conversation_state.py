# L3: Receipt-bound scoped topics and immutable prepared display snapshots.
# Effects: Bounded volatile memory; no repository reads, rendering or platform IO.
from __future__ import annotations
import time
import json
from collections import OrderedDict
from dataclasses import dataclass, replace, asdict

from ..query.continuation import ContextKey, QueryContext
from ..domain.knowledge import KnowledgeScope


@dataclass(frozen=True)
class Anchor:
    key: ContextKey
    lineage: int
    context: object
    snapshot_id: str
    page: int
    expires: float


@dataclass(frozen=True)
class ReadOnlyContext:
    text: str
    origin: str = 'direct'
    evidence: tuple = ()
    history: tuple = ()
    facts: str = ''
    # Last grounded knowledge request; volatile, ACK-bound and never a fact source.
    topic: str = ''
    knowledge_scope: KnowledgeScope | None = None
    focus: str = ''
    chat_topic: str = ''


@dataclass(frozen=True)
class DisplaySnapshot:
    snapshot_id: str
    scope: tuple
    key: ContextKey
    lineage: int
    pages: tuple
    created: float
    expires: float
    layout_version: str = 'prepared-reply/1'

    @property
    def retained_bytes(self):
        return sum(len(p.image or b'') for p in self.pages) + self.text_bytes

    @property
    def text_bytes(self):
        return sum(len(p.text.encode()) + (len(json.dumps(asdict(p.context),ensure_ascii=False).encode())
                   if isinstance(p.context,ReadOnlyContext) else 0)
                   + len(json.dumps([asdict(lease) for lease in p.evidence],ensure_ascii=False).encode()) for p in self.pages)


class Conversations:
    def __init__(self, contexts, *, clock=time.monotonic, capacity=1024, byte_capacity=128_000_000, valid_evidence=lambda leases: not leases):
        self.contexts, self.clock = contexts, clock
        self.capacity, self.byte_capacity = capacity, byte_capacity
        self.snapshots = OrderedDict()
        self.anchors = OrderedDict()
        self.recent = OrderedDict()
        self.valid_evidence = valid_evidence

    @staticmethod
    def scope(request):
        return tuple(request.scope.values())

    def sweep(self):
        now = self.clock()
        self.contexts.sweep()
        for key, snap in list(self.snapshots.items()):
            if now >= snap.expires:
                self.snapshots.pop(key, None)
        for key, anchor in list(self.anchors.items()):
            if now >= anchor.expires or anchor.snapshot_id not in self.snapshots:
                self.anchors.pop(key, None)
        for key, anchor in list(self.recent.items()):
            if now >= anchor.expires or anchor.snapshot_id not in self.snapshots:
                self.recent.pop(key, None)

    def resolve(self, request):
        self.sweep()
        scope = self.scope(request)
        if request.reference is not None:
            return self.anchors.get((scope, request.reference['message_id']))
        # Explicit references win; never guess which of two group topics was intended.
        choices = [a for (s, _), a in self.recent.items() if s == scope]
        return choices[0] if len(choices) == 1 else None

    def capture(self, work, pages):
        self.sweep()
        if work.anchor is not None and work.operation is not None and work.operation.kind == 'page':
            return self.snapshots.get(work.anchor.snapshot_id)
        if work.lease is None:
            return None
        deadline = min([self.contexts.deadline(work.key),
                        *(p.evidence_deadline for p in pages if p.evidence_deadline is not None)])
        if deadline <= self.clock():
            return None
        snapshot = DisplaySnapshot(work.delivery, self.scope(work.request), work.key,
                                   work.lease.generation if work.lineage is None else work.lineage,
                                   tuple(pages), self.clock(), deadline)
        versions = {p.layout_version for p in pages if p.business_status!='processing_hint'}
        if len(versions) == 1:
            snapshot = replace(snapshot, layout_version=next(iter(versions)))
        if snapshot.retained_bytes > self.byte_capacity:
            return None
        self.snapshots[work.delivery] = snapshot
        scoped = [(key,s) for key,s in self.snapshots.items() if s.scope == snapshot.scope]
        # Dialogue text has a per-conversation budget. Captured business display
        # pages use the global snapshot byte budget, preserving complete lists.
        def dialogue_chars(items):
            return sum(len(p.context.text)+len(p.context.facts)+len(p.context.topic)+len(p.context.focus)+len(p.context.chat_topic)+sum(len(t) for _,t in p.context.history) for _,s in items for p in s.pages
                       if isinstance(p.context, ReadOnlyContext))
        while scoped and (len(scoped) > 20 or dialogue_chars(scoped) > 16000):
            key,_ = scoped.pop(0)
            self.snapshots.pop(key,None)
        while (len(self.snapshots) > self.capacity or
               sum(s.retained_bytes for s in self.snapshots.values()) > self.byte_capacity or
               sum(s.text_bytes for s in self.snapshots.values()) > 8*1024*1024):
            self.snapshots.popitem(last=False)
        self.sweep()
        return self.snapshots.get(work.delivery)

    def confirm(self, work, message_id, page, context, *, fallback_notice=False):
        self.sweep()
        is_page = work.anchor is not None and work.operation is not None and work.operation.kind == 'page'
        snapshot = self.snapshots.get(work.anchor.snapshot_id if is_page else work.delivery)
        if is_page:
            page = work.anchor.page + work.operation.value
        if snapshot is None or not 0 <= page < len(snapshot.pages):
            return
        if not self.valid_evidence(snapshot.pages[page].evidence):
            return
        if self.contexts.lineage(work.key) != snapshot.lineage:
            return
        indices = snapshot.pages[page].visible_indices
        query = getattr(context, 'query_context', context)
        if fallback_notice:
            # Only the short fallback notice was sent. /continue may deliver the
            # frozen text cursor; it grants neither row selection nor image paging.
            page = -1
        elif indices and isinstance(query, QueryContext):
            query = replace(query, visible=tuple(query.visible[i-1] for i in indices), visible_indices=indices)
            context = replace(context, query_context=query) if hasattr(context, 'query_context') else query
        elif isinstance(query, QueryContext) and len(query.visible) > 1:
            if query.selected is not None:
                query = replace(query, visible=(query.selected,), visible_indices=())
                context = replace(context, query_context=query) if hasattr(context, 'query_context') else query
            else:
                context = (replace(context, query_context=None) if hasattr(context, 'query_context')
                           else ReadOnlyContext(snapshot.pages[page].text, query.origin))
        anchor = Anchor(work.key, snapshot.lineage, context, snapshot.snapshot_id, page, snapshot.expires)
        existing = self.anchors.get((snapshot.scope, message_id))
        if existing is not None and existing != anchor:
            # A conflicting receipt cannot silently redirect an already visible message.
            return
        self.anchors[(snapshot.scope, message_id)] = anchor
        self.recent[(snapshot.scope, snapshot.key)] = anchor
        scoped = [key for key in self.anchors if key[0] == snapshot.scope]
        for key in scoped[:-20]: self.anchors.pop(key,None)
        while len(self.anchors) > self.capacity:
            self.anchors.popitem(last=False)
        while len(self.recent) > self.capacity:
            self.recent.popitem(last=False)

    def page(self, anchor, offset):
        self.sweep()
        if anchor.page < 0:
            return None
        snapshot = self.snapshots.get(anchor.snapshot_id)
        index = anchor.page + offset
        if snapshot is None or not 0 <= index < len(snapshot.pages):
            return None
        if snapshot.pages[index].business_status=='processing_hint':
            return None
        return snapshot.pages[index]

    def clear(self):
        self.snapshots.clear()
        self.anchors.clear()
        self.recent.clear()

    def discard_unconfirmed(self, snapshot_id):
        if not any(a.snapshot_id == snapshot_id for a in self.anchors.values()):
            self.snapshots.pop(snapshot_id, None)
