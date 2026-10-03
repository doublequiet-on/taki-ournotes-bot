# L3
# Input: 普通平台／会话／用户键、已捕获查询结果和有限续查操作；不接受 SDK 消息。
# Output: 不可变可见选择快照、规范查询、版本校验及有代次的上下文提交。
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

from ..commands import CommandResult, PAGE_SIZE, page_slice, resolve_command
from ..structured_query import QueryResult, QuerySpec, resolve_query
from .entity_lexicon import EntityRef, alias_version
from .song_identity import QueryProblem
from .song_query import DIFFS, serialize_filter

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
        return int(text) if len(text) < 4 else 999
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
    text = re.sub(r"^(?:问|ask)\s+", "", text, flags=re.I).strip(" ?？。!")
    if text in {"下一页", "上一页"}:
        return Operation("page", 1 if text == "下一页" else -1)
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


def catalog_version(repository) -> str:
    # No refresh/get() here: version checks must not cause source I/O. Hash content
    # as well as release labels, since replaced records may keep a release label.
    digest = hashlib.sha256()
    for value in (getattr(repository, "metadata", {}), getattr(repository, "songs", ()),
                  getattr(repository, "cards", ()), getattr(repository, "support_cards", ())):
        digest.update(repr(value).encode("utf-8"))
    digest.update(str(alias_version()).encode("ascii"))
    return digest.hexdigest()


def dependency_version(repository, query) -> tuple[str, str]:
    meta = ""
    if isinstance(query, QuerySpec) and query.intent == "efficiency":
        snapshot = getattr(getattr(repository, "song_meta", None), "_snapshot", None)
        if snapshot is not None:
            meta = hashlib.sha256(repr((snapshot.release, snapshot.source_version, snapshot.rows)).encode("utf-8")).hexdigest()
    return catalog_version(repository), meta


def capture_context(result, repository, *, catalog_before=None) -> QueryContext | None:
    """Copy IDs in exactly the text/image page order; never rerun the selection."""
    if result is None or result.status != "success" or result.cutoff is not None:
        return None
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
        spec = QuerySpec(kind, card_query=req.query, page=req.page)
        page, pages = req.page, max(1, (len(answer.cards) + PAGE_SIZE - 1) // PAGE_SIZE)
        visible = tuple(Choice(kind, c.id) for c in answer.visible)
        if req.mode == "detail" and len(visible) == 1:
            selected = visible[0]
    elif result.meta is not None:
        if spec is None:
            return None
        page, pages = spec.page, max(1, (result.meta.total + spec.limit - 1) // spec.limit)
        visible = tuple(Choice("efficiency", r.song_id, r.difficulty) for r in result.meta.rows)
        if not result.meta.columns and len(visible) == 1:
            selected = visible[0]
    elif result.song_selection is not None:
        answer = result.song_selection
        spec = QuerySpec("song", song_query=answer.request.query, page=answer.page)
        page, pages = answer.page, max(1, (len(answer.songs) + PAGE_SIZE - 1) // PAGE_SIZE)
        visible = tuple(Choice("song", s.id, answer.request.difficulty) for s in page_slice(answer.songs, page))
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
            visible = tuple(Choice(spec.intent, r.id, spec.difficulty) for r in page_slice(records, page))
    elif result.parsed[0] in {"songs", "chart"}:
        kind, query, value = result.parsed
        if kind == "songs":
            page = int(value)
            # Preserve the established bare-integer level grammar for legacy lists.
            spec = f"/查曲 {query}"
            pages = max(1, (len(result.songs) + PAGE_SIZE - 1) // PAGE_SIZE)
            visible = tuple(Choice("song", s.id) for s in page_slice(result.songs, page))
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
    if not visible or len(visible) > 30:
        return None
    version = dependency_version(repository, spec)
    if catalog_before is not None and version[0] != catalog_before:
        return None
    return QueryContext(spec, visible, page, pages, version, selected)


def execute_followup(context: QueryContext, operation: Operation, repository):
    """Return (captured result, next context) or a local terminal problem."""
    if context.version != dependency_version(repository, context.query):
        return QueryProblem(CHANGED, "invalid_arguments")
    if operation.kind == "page":
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
            if not 1 <= operation.value <= len(context.visible):
                return QueryProblem(f"请选择当前页的 1～{len(context.visible)} 号。", "invalid_arguments")
            selected = context.visible[operation.value - 1]
        elif selected is None:
            if len(context.visible) != 1:
                return QueryProblem("当前页有多个结果，请先发送 /选 编号，例如 /选 2。", "ambiguous")
            selected = context.visible[0]
        if operation.kind == "difficulty":
            if selected.kind not in {"song", "efficiency"}:
                return QueryProblem("只有歌曲或分数表结果可以切换难度。", "invalid_arguments")
            if not operation.value:
                return QueryProblem("难度支持 EASY / NORMAL / HARD / EXPERT（EZ / NM / HD / EX）。", "invalid_arguments")
            selected = replace(selected, difficulty=operation.value)
        if selected.kind == "efficiency":
            query = context.query
            shared = query.song_filter
            if shared:
                shared = replace(shared, term=str(selected.entity_id), difficulty=selected.difficulty)
                shared = replace(shared, query=serialize_filter(shared))
            spec = replace(query, subject=EntityRef("song", selected.entity_id),
                           difficulty=selected.difficulty, page=1, song_filter=shared,
                           display_name=str(selected.entity_id))
        elif selected.kind == "song":
            spec = QuerySpec("chart", EntityRef("song", selected.entity_id),
                             selected.difficulty if selected.difficulty not in {"", "ALL"} else "EXPERT",
                             display_name=str(selected.entity_id))
        else:
            spec = QuerySpec(selected.kind, card_query=str(selected.entity_id))
        result = resolve_query(spec, repository)
        candidate = replace(context, selected=selected) if result.status == "success" else None
    else:
        return QueryProblem("用法：/下一页、/上一页、/选 2、/详情、/难度 EX。", "invalid_arguments")
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

    def get(self, key):
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self.clock() - entry.at >= self.ttl:
            self._entries.pop(key, None)
            return None
        return entry.context

    def lineage(self, key):
        if self.get(key) is None:
            return None
        return self._entries[key].lineage

    def begin(self, key, *, clear=True):
        previous = self._entries.get(key)
        context = None if clear else self.get(key)
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
        if entry is None or entry.generation != ticket.generation:
            return False
        self._entries[ticket.key] = _Entry(ticket.generation, entry.lineage, context, self.clock())
        self._entries.move_to_end(ticket.key)
        return True
