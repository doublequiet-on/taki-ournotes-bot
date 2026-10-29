# L3
# Input: 普通平台／会话／用户键、已捕获查询结果和有限续查操作；不接受 SDK 消息。
# Output: 可见 ID／难度、完整规范条件；至多30个选择，仅成功发送后提交上下文。
# Pos: Query / Deterministic 的有界连续查询；见 L2-2.md。
# Effects/Dependencies: 最多 1024 个内存上下文，10 分钟 TTL；执行复用查询器，可按 ID 获取详情；无模型、发送或持久化。
"""Small, receipt-committed conversations over actually displayed query results."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
import unicodedata
import weakref
from collections import OrderedDict
from dataclasses import dataclass, replace

from ..domain.meta_query import MetaResult
from ..commands import CommandResult, PAGE_SIZE, page_slice, resolve_command
from ..structured_query import QueryResult, QuerySpec, resolve_query
from .entity_lexicon import EntityRef, alias_version
from .song_identity import QueryProblem
from .song_query import DIFFS

MISSING = "没有可继续的查询，或上次结果已过期。请重新发送完整查询。"
CHANGED = "资料或昵称版本已更新，请重新发送完整查询后再选择或翻页。"


@dataclass(frozen=True)
class ContextKey:
    platform: str
    kind: str
    conversation: str
    user: str

    def __post_init__(self):
        for value in (self.platform, self.kind, self.conversation, self.user):
            if (not isinstance(value, str) or not value.strip() or len(value) > 256
                    or any(ord(c) < 32 for c in value)):
                raise ValueError("invalid context identity")


@dataclass(frozen=True)
class Operation:
    kind: str
    value: int | str = ""


def _number(text):
    if text.isascii() and text.isdigit():
        return int(text) if len(text) <= 6 else 0
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if text in digits:
        return digits[text]
    if text.count("十") == 1:
        a, b = text.split("十")
        if (not a or a in digits) and (not b or b in digits):
            return (digits.get(a, 1) * 10) + digits.get(b, 0)
    return 0


def parse_operation(content: str) -> Operation | None:
    text = unicodedata.normalize("NFKC", content).strip().lstrip("/")
    if not re.match(r"^(?:问|ask)\s+", text, re.I) and re.match(r"^(?:下一页|上一页|选|难度)", text):
        return None
    text = re.sub(r"^(?:问|ask)\s+", "", text, flags=re.I).strip(" ?？。!")
    if text in {"下一页", "上一页"}:
        return Operation("page", 1 if text == "下一页" else -1)
    if text == "继续":
        return Operation("continue")
    if text in {"详情", "看详情"}:
        return Operation("detail")
    match = re.fullmatch(r"(?:选\s*([\d零一二两三四五六七八九十]+)|第\s*([\d零一二两三四五六七八九十]+)\s*(?:个|首|张|条))", text)
    if match:
        return Operation("select", _number(match[1] or match[2]))
    match = re.fullmatch(r"(?:难度\s*|换成\s*|换难度\s*|换成?\s*(?:指定)?难度\s*)(.+)", text, re.I)
    if match:
        value = match[1].strip().upper()
        value = {"简单": "EASY", "普通": "NORMAL", "困难": "HARD", "专家": "EXPERT"}.get(value, value)
        return Operation("difficulty", DIFFS.get(value, ""))
    if re.match(r"^(?:选|难度)(?:\s|$)", text):
        return Operation("invalid")
    return None


@dataclass(frozen=True)
class Choice:
    kind: str
    entity_id: int
    difficulty: str = ""


@dataclass(frozen=True)
class QueryContext:
    query: QuerySpec | str
    visible: tuple[Choice, ...]
    page: int
    pages: int
    version: tuple[str, str]
    selected: Choice | None = None
    complete: bool = False
    origin: str = 'unknown'
    visible_indices: tuple[int, ...] = ()
    meta: MetaResult | None = None


def catalog_version(repository) -> str:
    # No refresh/get() here: version checks must not cause source I/O. Hash content
    # as well as release labels, since replaced records may keep a release label.
    capture = getattr(repository, "capture_catalog", None)
    catalog = capture() if capture is not None else repository
    digest = hashlib.sha256()
    records_version = getattr(catalog, "records_version", None)
    if records_version is None:
        values = (getattr(catalog, "metadata", {}), getattr(catalog, "songs", ()),
                  getattr(catalog, "cards", ()), getattr(catalog, "support_cards", ()))
    else:
        values = ("haneoka-records/1", records_version, getattr(catalog, "metadata", {}))
    for value in (*values, getattr(repository, "chart_source", "moenotes"),
                  getattr(repository, "data_source", "yume")):
        digest.update(repr(value).encode("utf-8"))
    digest.update(str(alias_version()).encode("ascii"))
    return digest.hexdigest()


def dependency_version(repository, query) -> tuple[str, str]:
    return catalog_version(repository), ""


def capture_context(result, repository, *, catalog_before=None, complete=False,
                    choices=None) -> QueryContext | None:
    """Copy IDs in exactly the text/image page order; never rerun the selection."""
    if result is None or result.status != "success" or result.cutoff is not None:
        return None
    if isinstance(result, QueryResult) and result.meta is not None:
        answer = result.meta
        if not answer.rows:
            return None
        visible = tuple(Choice('meta_song', int(row.song.song_id), answer.spec.difficulty or '') for row in answer.rows)
        return QueryContext(result.spec, visible, 1, 1, (answer.source.release_id, answer.source.source_id),
                            complete=True, meta=answer)
    spec = result.spec if isinstance(result, QueryResult) else result.query_spec
    if spec and spec.field:
        return None
    selected = None
    if result.catalog is not None:
        answer = result.catalog
        req = answer.request
        if req.mode == "art" or answer.error:
            return None
        kind = "support_card" if req.support else "card"
        spec = QuerySpec(kind, card_request=req, page=req.page)
        page, pages = req.page, max(1, (len(answer.cards) + PAGE_SIZE - 1) // PAGE_SIZE)
        visible = tuple(Choice(kind, c.id) for c in (answer.cards if complete else answer.visible))
        if req.mode == "detail" and len(visible) == 1:
            selected = visible[0]
    elif result.song_selection is not None:
        answer = result.song_selection
        spec = QuerySpec("song", song_query=answer.request.query, page=answer.page)
        page, pages = answer.page, max(1, (len(answer.songs) + PAGE_SIZE - 1) // PAGE_SIZE)
        visible = tuple(Choice("song", s.id, answer.request.difficulty) for s in
                        (answer.songs if complete else page_slice(answer.songs, page)))
    elif isinstance(result, QueryResult):
        page = spec.page
        if spec.intent == "chart":
            if result.chart is None:
                return None
            visible = (Choice("song", result.chart[0].id, spec.difficulty),)
            selected, pages = visible[0], 1
        else:
            records = result.songs or result.cards or result.support_cards
            pages = max(1, (len(records) + PAGE_SIZE - 1) // PAGE_SIZE)
            visible = tuple(Choice(spec.intent, r.id, spec.difficulty) for r in
                            (records if complete else page_slice(records, page)))
    elif result.parsed[0] in {"songs", "chart"}:
        kind, query, value = result.parsed
        if kind == "songs":
            page = int(value)
            # Preserve the established bare-integer level grammar for legacy lists.
            spec = f"/查曲 {query}"
            pages = max(1, (len(result.songs) + PAGE_SIZE - 1) // PAGE_SIZE)
            visible = tuple(Choice("song", s.id) for s in
                            (result.songs if complete else page_slice(result.songs, page)))
        else:
            page, pages = 1, 1
            visible = tuple(Choice("song", s.id, value or "") for s in result.songs)
            if len(visible) != 1:
                return None
            selected = visible[0]
            spec = QuerySpec("chart", EntityRef("song", selected.entity_id), selected.difficulty,
                             display_name=str(selected.entity_id))
    else:
        return None
    if choices is not None:
        if not complete or sorted((v.kind, v.entity_id, v.difficulty) for v in choices) != sorted(
                (v.kind, v.entity_id, v.difficulty) for v in visible):
            raise ValueError("display choices do not match the captured result")
        visible = tuple(choices)
    if not visible or (not complete and len(visible) > 30):
        return None
    version = dependency_version(repository, spec)
    if catalog_before is not None and version[0] != catalog_before:
        return None
    return QueryContext(spec, visible, 1 if complete else page, 1 if complete else pages,
                        version, selected, complete)


def execute_followup(context: QueryContext, operation: Operation, repository):
    """Return (captured result, next context) or a local terminal problem."""
    if context.meta is not None:
        if operation.kind not in {'select', 'detail'}:
            return QueryProblem('此结果支持固定分页和序号详情；更换条件或难度请发送完整查询。', 'invalid_arguments')
        indices = context.visible_indices or tuple(range(1, len(context.visible)+1))
        selected = context.selected
        if operation.kind == 'select':
            if operation.value not in indices:
                return QueryProblem('请选择本条消息中已展示的序号：' + '、'.join(map(str,indices)) + '。', 'invalid_arguments')
            selected = context.visible[indices.index(operation.value)]
        if selected is None:
            if len(context.visible) != 1:
                return QueryProblem('请先选择本条消息中的序号。', 'ambiguous')
            selected = context.visible[0]
        rows = tuple(r for r in context.meta.rows if r.song.song_id == str(selected.entity_id))
        answer = replace(context.meta, rows=rows)
        return QueryResult(context.query, meta=answer), replace(context, visible=(selected,), selected=selected,
                                                                visible_indices=(), meta=answer)
    if context.version != dependency_version(repository, context.query):
        return QueryProblem(CHANGED, "invalid_arguments")
    if operation.kind == "page":
        if context.complete:
            return QueryProblem("结果已按完整查询展示；尚有未发送图片时请用 /继续。", "invalid_arguments")
        page = context.page + operation.value
        if not 1 <= page <= context.pages:
            return QueryProblem(f"页码超出范围，当前第 {context.page}/{context.pages} 页。", "invalid_arguments")
        query = context.query
        result = (resolve_query(replace(query, page=page), repository) if isinstance(query, QuerySpec)
                  else resolve_command(f"{query} 页{page}", repository))
        candidate = capture_context(result, repository, catalog_before=context.version[0])
    elif operation.kind in {"select", "detail", "difficulty"}:
        selected = context.selected
        if operation.kind == "select":
            indices = context.visible_indices or tuple(range(1, len(context.visible)+1))
            if operation.value not in indices:
                return QueryProblem("请选择本条消息中已展示的序号：" + "、".join(map(str, indices)) + "。", "invalid_arguments")
            selected = context.visible[indices.index(operation.value)]
        elif selected is None:
            if len(context.visible) != 1:
                return QueryProblem("已展示的结果有多个，请按列表中的 ID 重新查询详情。", "ambiguous")
            selected = context.visible[0]
        if operation.kind == "difficulty":
            if selected.kind != "song":
                return QueryProblem("只有歌曲结果可以切换难度。", "invalid_arguments")
            if not operation.value:
                return QueryProblem("难度支持 EASY / NORMAL / HARD / EXPERT（EZ / NM / HD / EX）。", "invalid_arguments")
            selected = replace(selected, difficulty=operation.value)
        if selected.kind == "song":
            spec = QuerySpec("chart", EntityRef("song", selected.entity_id),
                             selected.difficulty if selected.difficulty not in {"", "ALL"} else "EXPERT",
                             display_name=str(selected.entity_id))
        else:
            from ..domain.card_query import CardRequest
            spec = QuerySpec(selected.kind, card_request=CardRequest(
                support=selected.kind == "support_card", mode="detail",
                card_id=selected.entity_id, query=str(selected.entity_id)))
        result = resolve_query(spec, repository)
        candidate = replace(context, selected=selected) if result.status == "success" else None
    else:
        return QueryProblem("用法：/继续、/详情；也可按 ID 重新查询。", "invalid_arguments")
    if context.version != dependency_version(repository, context.query):
        return QueryProblem(CHANGED, "invalid_arguments")
    return result, candidate


@dataclass(frozen=True)
class Ticket:
    key: ContextKey
    generation: int


@dataclass(frozen=True)
class _Entry:
    generation: int
    lineage: int
    context: QueryContext | None
    at: float


class ContextStore:
    def __init__(self, *, capacity=1024, ttl=600, clock=time.monotonic):
        if capacity < 1 or ttl <= 0:
            raise ValueError("invalid context bounds")
        self.capacity, self.ttl, self.clock = capacity, ttl, clock
        self._entries = OrderedDict()
        self._generation = 0
        self._locks = weakref.WeakValueDictionary()

    def lock(self, key):
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    def retained_contexts(self):
        """Live contexts for application-wide retained-resource accounting."""
        return tuple(context for key in tuple(self._entries) if (context := self.get(key)) is not None)

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self.clock() - entry.at >= self.ttl:
            self._entries.pop(key, None)
            return None
        return entry.context

    def sweep(self):
        for key in tuple(self._entries):
            self.get(key)

    def clear(self):
        self._entries.clear()

    def discard(self,key):
        self._entries.pop(key,None)

    def deadline(self, key):
        entry = self._entries.get(key)
        return entry.at + self.ttl if entry else 0

    def active(self, ticket):
        entry = self._entries.get(ticket.key) if ticket is not None else None
        return bool(entry is not None and entry.generation == ticket.generation and self.clock() < entry.at+self.ttl)

    def lineage(self, key):
        if self.get(key) is None:
            return None
        return self._entries[key].lineage

    def begin(self, key, *, clear=True):
        context = None if clear else self.get(key)
        previous = self._entries.get(key)
        self._generation += 1
        at = self.clock() if clear or previous is None else previous.at
        lineage = self._generation if clear or previous is None else previous.lineage
        self._entries[key] = _Entry(self._generation, lineage, context, at)
        self._entries.move_to_end(key)
        while len(self._entries) > self.capacity:
            self._entries.popitem(last=False)
        return Ticket(key, self._generation)

    def commit(self, ticket, context, *, confirmed):
        if not confirmed or ticket is None or context is None:
            return False
        entry = self._entries.get(ticket.key)
        if entry is None or entry.generation != ticket.generation or self.clock() >= entry.at + self.ttl:
            if entry is not None and self.clock() >= entry.at + self.ttl:
                self._entries.pop(ticket.key, None)
            return False
        self._entries[ticket.key] = _Entry(ticket.generation, entry.lineage, context, entry.at)
        self._entries.move_to_end(ticket.key)
        # Encoded continuation images are bounded separately from the entry count.
        while len(self._entries) > 1 and sum(getattr(e.context, 'retained_bytes', 0) for e in self._entries.values()) > 128_000_000:
            self._entries.popitem(last=False)
        return ticket.key in self._entries
