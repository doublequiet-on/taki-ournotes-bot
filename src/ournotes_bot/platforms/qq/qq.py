# L3
# Input: QQ 事件／消息、Settings、SongRepository、AIQueryParser 及 CommandResult／QueryResult。
# Output: PreparedReply 同捕获图片／完整文字、预算与选择快照；DeliveryOutcome 明确全部交付回执；批内顺序固定。
# Pos: Platform / QQ 的事件接入、回复准备／交付与后台任务接线；见 ../../../L2-QQ.md。
# Effects/Dependencies: botpy 连接、上传／发送、线程与 asyncio 任务、去重／队列状态；经业务模块联网及读写缓存；彩蛋读取随包静态图片；固定 logger ournotes_bot.qq 供更新器识别就绪。

from __future__ import annotations

import asyncio
import base64
import copy
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, replace
from importlib.resources import files
from urllib.parse import urlsplit

from botpy.http import BotHttp, Route
from botpy.message import GroupMessage

# qq-botpy 1.2.1 still ships the retired hostname; QQ API v2 now uses this host.
Route.DOMAIN = "api.bot.qq.com"
Route.SANDBOX_DOMAIN = "api.bot.qq.com"

# The gateway endpoint still advertises the retired host for websocket upgrades.
# Only that host is rewritten, so a future region-specific address stays intact.
LEGACY_GATEWAY_HOSTS = frozenset({"api.sgroup.qq.com", "sandbox.api.sgroup.qq.com"})

from ...ai_query import AIQueryParser, is_ai_request
from ...sources.chart_data import ChartDataError, load_chart_data, load_chart_score
from ...commands import (ALIASES, CommandResult, handle_command, locale_for, page_notice,
                       page_slice, resolve_command, split_commands)
from ...config import QQ_PASSIVE_REPLY_LIMIT, REPLY_ORDER_TIMEOUT_SECONDS, Settings
from ... import config
from ...data import SongRepository
from ...i18n import tr
from ...structured_query import QueryResult, query_page_notice
from ...query.continuation import (ContextKey, ContextStore, QueryContext, MISSING, catalog_version,
                                  capture_context, execute_followup, parse_operation)
from ...ai_client import AIClient
from ...update_notice import UpdateNotifier
from ...visuals import (render_card, render_card_list, render_chart, render_song_list,
                      render_support_card, render_support_card_list)


logger = logging.getLogger("ournotes_bot.qq")


def normalize_gateway_url(url: str, host: str) -> str:
    """Point an advertised websocket gateway at the unified QQ host.

    Only legacy hosts are replaced, and the port (if any) is preserved.
    """
    if not host or not isinstance(url, str):
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.hostname not in LEGACY_GATEWAY_HOSTS:
        return url
    netloc = parts.netloc.replace(parts.hostname, host, 1)
    return parts._replace(netloc=netloc).geturl()


def install_gateway_host(host: str) -> bool:
    """Rewrite the websocket gateway host; returns False when already applied.

    qq-botpy exposes no hook for this, so the API method is wrapped once.
    """
    from botpy.api import BotAPI

    current = BotAPI.get_ws_url
    if getattr(current, "_ournotes_patched", False):
        return False
    original = current

    async def get_ws_url(self):
        payload = await original(self)
        if isinstance(payload, dict) and isinstance(payload.get("url"), str):
            rewritten = normalize_gateway_url(payload["url"], host)
            if rewritten != payload["url"]:
                logger.info("WebSocket 网关域名已指向 %s（服务端下发：%s）",
                            host, urlsplit(payload["url"]).hostname)
            payload["url"] = rewritten
        return payload

    get_ws_url._ournotes_patched = True
    BotAPI.get_ws_url = get_ws_url
    return True


def describe(content: str) -> str:
    """Text for the log. Direct commands are shown; /问 bodies stay out of it."""
    cleaned = (content or "").strip()
    if is_ai_request(cleaned):
        return f"/问（{len(cleaned)} 字，正文不记录）"
    return cleaned[:60]


def _is_group_query(content: object) -> bool:
    if not isinstance(content, str):
        return False
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    if not cleaned:
        return False
    if cleaned.startswith(("/", "／")) or is_ai_request(cleaned):
        return True
    first = cleaned.split(None, 1)[0].casefold()
    return (first in ALIASES or parse_operation(cleaned) is not None
            or first in {"帮助", "help", "ヘルプ", "数据状态", "状态", "status", "状態"})


def _mentions_bot(data: dict, app_id: str) -> bool:
    mentions = data.get("mentions")
    if isinstance(mentions, list) and any(
        isinstance(mention, dict) and mention.get("is_you") is True for mention in mentions
    ):
        return True
    content = data.get("content")
    return bool(
        app_id and isinstance(content, str)
        and re.search(rf"<@!?{re.escape(app_id)}>", content)
    )


def register_group_message_parser(parser: dict, api, dispatch, app_id: str, observe=None) -> None:
    """Dispatch only self-mentioned GROUP_MESSAGE_CREATE events from the newer gateway."""
    def parse(payload):
        data = payload.get("d", {})
        author = data.get("author") if isinstance(data, dict) else None
        if observe and isinstance(data, dict) and data.get("group_openid"):
            observe(data["group_openid"])
        if (
            isinstance(data, dict) and data.get("group_openid")
            and not (isinstance(author, dict) and author.get("bot"))
            and _mentions_bot(data, app_id) and _is_group_query(data.get("content"))
        ):
            dispatch("group_at_message_create", GroupMessage(api, payload.get("id"), data))

    parser["group_message_create"] = parse


def install_group_parser(connection, dispatch, app_id: str, observe=None) -> None:
    register_group_message_parser(connection.parser, connection.state.api, dispatch, app_id, observe)


def _chart_image(song, charts, locale: str, *, source="moenotes") -> bytes:
    preview = next((chart for chart in charts if chart.difficulty == "EXPERT"), charts[-1] if charts else None)
    score = None
    notice = ""
    if preview:
        try:
            if source == "moenotes":
                score = load_chart_score(song, preview)
            else:
                result = load_chart_data(song, preview, source=source)
                score, notice = result.score, result.notice(locale)
        except ChartDataError:
            logger.warning("音符谱面暂不可用：歌曲 %s，难度 %s", song.id, preview.difficulty)
    if notice:
        return render_chart(song, charts, locale, score, preview.difficulty if preview else None,
                            source_notice=notice)
    return render_chart(song, charts, locale, score, preview.difficulty if preview else None)


@dataclass(frozen=True)
class PreparedReply:
    text: str
    image: bytes | None = None
    pages: tuple[PreparedReply, ...] = ()
    complete_text: bool = False
    context: QueryContext | DeliveryCursor | None = None
    budget_label: str = "查榜线"
    fallback_texts: tuple[str, ...] = ()

    @property
    def passive_slots(self):
        return max(1, len(self.fallback_texts))


@dataclass(frozen=True)
class DeliveryCursor:
    query_context: QueryContext | None
    remaining: tuple[PreparedReply, ...]
    sent: int
    total: int
    uncertain: bool = False

    @property
    def retained_bytes(self):
        return sum(len(p.image or b'') + len(p.text.encode('utf-8')) for p in self.remaining)


def _fallback_cursor(reply):
    previous=reply.context if isinstance(reply.context,DeliveryCursor) else None
    query=previous.query_context if previous else reply.context
    chunks=_text_chunks(reply.text)
    pages=tuple(PreparedReply(t,context=query if i==len(chunks)-1 else None) for i,t in enumerate(chunks))
    pages+=previous.remaining if previous else ()
    return DeliveryCursor(None,pages,0,len(pages))


def _text_chunks(text,limit=1700):
    return tuple(text[i:i+limit] for i in range(0,len(text),limit)) or ('',)


def _expand_replies(replies: list[PreparedReply], reserve: int = 0) -> list[PreparedReply]:
    """Budget a single ordered captured batch, including multi-command overflow."""
    pages=[]
    base=replies[0].context if len(replies)==1 and isinstance(replies[0].context,DeliveryCursor) else None
    for reply in replies:
        for part in reply.pages or (reply,):
            if part.fallback_texts:
                part=replace(part,text='\n'.join(part.fallback_texts),fallback_texts=())
            if part.image is None and len(part.text)>1800:
                chunks=_text_chunks(part.text)
                pages.extend(PreparedReply(t,context=part.context if i==len(chunks)-1 else None) for i,t in enumerate(chunks))
            else:
                pages.append(part)
    available=max(1,QQ_PASSIVE_REPLY_LIMIT-reserve)
    prior=base.sent if base else 0
    total=base.total if base else len(pages)
    keep=bool(base or len(pages)>available or any(r.pages for r in replies))
    expanded=[]
    for offset,page in enumerate(pages[:available]):
        if keep:
            query=(page.context if isinstance(page.context,QueryContext) else base.query_context if base else None) if len(replies)==1 else None
            cursor=DeliveryCursor(query,tuple(pages[offset+1:]),prior+offset+1,total)
            suffix=f'\n结果 {cursor.sent}/{total}'+('；/继续 发送剩余结果。' if cursor.remaining else '')
            page=replace(page,text=page.text+suffix,context=cursor)
        expanded.append(page)
    return expanded


class QueryGate:
    """Bound in-flight preparation, including callers waiting for a worker."""

    def __init__(self, concurrency: int, queue_limit: int) -> None:
        if concurrency < 1 or queue_limit < 0:
            raise ValueError("Invalid query gate limits")
        self._workers = asyncio.Semaphore(concurrency)
        self._capacity = concurrency + queue_limit
        self._pending = 0
        self._lock = asyncio.Lock()

    async def prepare(self, content: str, repository: SongRepository,
                      ai_parser: AIQueryParser) -> PreparedReply | None:
        return await self._run(_prepare_reply, content, repository, ai_parser)

    async def prepare_followup(self, context, operation, repository):
        return await self._run(_prepare_followup, context, operation, repository)

    async def _run(self, function, *args):
        async with self._lock:
            if self._pending >= self._capacity:
                return PreparedReply("当前查询较多，请稍后重试。")
            self._pending += 1
        try:
            async with self._workers:
                work = asyncio.create_task(asyncio.to_thread(function, *args))
                try:
                    return await asyncio.shield(work)
                except asyncio.CancelledError:
                    # to_thread cannot stop a running worker; keep its slot occupied.
                    try:
                        await work
                    finally:
                        raise
        finally:
            async with self._lock:
                self._pending -= 1


class ReplySequencer:
    """Let replies leave in arrival order even though they finish out of order.

    QueryGate leaves the gate the moment a worker frees up, so a slow query
    started first can be overtaken by a faster query that arrived later. In a
    chat that reads as a mix-up: the answer to question two shows up before the
    answer to question one.

    Each inbound message takes a ticket when it arrives and waits for its turn
    before sending. Preparation is untouched, so throughput is unchanged; only
    the sending order is serialized.

    Ordering costs tail latency: a fast query that arrives after a slow one has
    to wait. Set `enabled=False` to trade order back for latency — a ticket is
    then None and every method is a no-op, so callers need no branching.

    Tickets are always released by the caller, and the wait is bounded, so a
    wedged batch degrades to unordered behaviour instead of blocking every
    later reply forever.
    """

    def __init__(self, timeout: float = REPLY_ORDER_TIMEOUT_SECONDS, enabled: bool = True) -> None:
        self._timeout = timeout
        self._enabled = enabled
        self._issued = 0
        self._serving = 0
        self._condition = asyncio.Condition()

    async def issue(self) -> int | None:
        """Take a ticket, or None when ordering is switched off."""
        if not self._enabled:
            return None
        async with self._condition:
            ticket = self._issued
            self._issued += 1
            return ticket

    async def wait_turn(self, ticket: int | None) -> None:
        if ticket is None:
            return
        async with self._condition:
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._serving >= ticket),
                    timeout=self._timeout,
                )
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("等待发送顺序超时 %ss；本次提前发送，避免后续回复被阻塞",
                               f"{self._timeout:g}")

    async def release(self, ticket: int | None) -> None:
        if ticket is None:
            return
        async with self._condition:
            self._serving = max(self._serving, ticket + 1)
            self._condition.notify_all()


async def prepare_commands(commands: list[str], gate: "QueryGate", repository: SongRepository,
                           ai_parser: AIQueryParser, limit: int) -> tuple[list["PreparedReply"], int]:
    """Prepare several commands concurrently and return replies in asked order.

    Preparation is bounded by `gate`, so a burst queues instead of swamping the
    machine, while `gather` keeps the results aligned with `commands` — the
    delivery order therefore follows what the user asked for rather than which
    query happened to finish first. One failing command cannot drop the others.

    Returns (replies, overflow) where overflow counts commands beyond `limit`.
    """
    selected = commands[:limit]
    async def prepare_one(command):
        if len(commands) > 1 and parse_operation(command) is not None:
            return PreparedReply("连续查询请单独发送；同一条消息中的新查询和续查不会按依赖链执行。")
        return await gate.prepare(command, repository, ai_parser)
    results = await asyncio.gather(
        *(prepare_one(command) for command in selected),
        return_exceptions=True,
    )
    replies: list[PreparedReply] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.error("查询结果准备失败；错误类型=%s", type(result).__name__)
            continue
        if result:
            replies.append(result)
    overflow = max(0, len(commands) - limit)
    return _expand_replies(replies, reserve=int(bool(overflow))), overflow


def _image_from_result(result: QueryResult | CommandResult | None,
                       repository: SongRepository, locale: str) -> bytes | None:
    if isinstance(result, QueryResult) and result.spec.field:
        return None
    if result is not None and result.catalog is not None:
        answer = result.catalog
        if answer.error or not answer.cards:
            return None
        req = answer.request
        if req.mode == "art":
            from ...rendering.card_visuals import art
            return art(answer.cards[0])
        if req.mode == "detail":
            render = render_support_card if req.support else render_card
            return render(answer.cards[0], locale)
        render = render_support_card_list if req.support else render_card_list
        return render(answer.visible, req.query, locale, answer.footer) if answer.visible else None
    if result is not None and result.song_selection is not None:
        answer = result.song_selection
        visible = page_slice(answer.songs, answer.page)
        return render_song_list(visible, answer.request.query, locale, answer.footer) if visible else None
    if isinstance(result, QueryResult):
        spec = result.spec
        if spec.intent == "song":
            songs = result.songs
            visible = page_slice(songs, spec.page)
            return render_song_list(visible, spec.query_label(), locale,
                                    page_notice("songs", spec.query_label(), spec.page, len(songs), locale)) if visible else None
        if spec.intent == "chart":
            chart = result.chart
            return _chart_image(chart[0], chart[1], locale, source=repository.chart_source) if chart else None
        if spec.intent == "card":
            if (spec.skill_query or spec.skill_kind) and not repository.member_skill_index_ready():
                return None
            cards = result.cards
            if not cards:
                return None
            if spec.subject and spec.subject.kind == "card":
                return render_card(repository.card_with_detail(cards[0]), locale)
            visible = page_slice(cards, spec.page)
            return render_card_list(visible, spec.query_label(), locale,
                                    query_page_notice(spec, len(cards), locale)) if visible else None
        if spec.intent == "support_card":
            cards = result.support_cards
            if not cards:
                return None
            if spec.subject and spec.subject.kind == "support_card":
                return render_support_card(repository.support_card_with_detail(cards[0]), locale)
            visible = page_slice(cards, spec.page)
            return render_support_card_list(
                visible, spec.query_label(), locale,
                query_page_notice(spec, len(cards), locale),
            ) if visible else None
        return None
    if result is None:
        return None
    kind, query, difficulty = result.parsed
    if kind == "songs":
        songs = result.songs
        visible = page_slice(songs, int(difficulty))
        return render_song_list(visible, query, locale, page_notice(kind, query, int(difficulty), len(songs), locale)) if visible else None
    if kind == "chart":
        songs = result.songs
        if not songs:
            return None
        charts = tuple(chart for chart in songs[0].charts if difficulty is None or chart.difficulty == difficulty)
        return _chart_image(songs[0], charts, locale, source=repository.chart_source)
    if kind == "cards":
        cards = result.cards
        if not cards:
            return None
        if query.isdigit() and cards[0].id == int(query):
            return render_card(repository.card_with_detail(cards[0]), locale)
        visible = page_slice(cards, int(difficulty))
        return render_card_list(visible, query, locale, page_notice(kind, query, int(difficulty), len(cards), locale)) if visible else None
    if kind == "support_cards":
        cards = result.support_cards
        if not cards:
            return None
        if query.isdigit() and cards[0].id == int(query):
            return render_support_card(repository.support_card_with_detail(cards[0]), locale)
        visible = page_slice(cards, int(difficulty))
        return render_support_card_list(
            visible, query, locale, page_notice(kind, query, int(difficulty), len(cards), locale)
        ) if visible else None
    return None


def _easter_egg_reply(content: str) -> PreparedReply | None:
    """Route only the complete card-947 command, independently of game queries."""
    commands = split_commands(content)
    if len(commands) != 1 or re.fullmatch(r"查卡\s+947", commands[0]) is None:
        return None
    try:
        image = files(__package__).joinpath("assets/card-947.jpg").read_bytes()
    except OSError as exc:
        logger.warning("彩蛋图片读取失败；错误类型=%s", type(exc).__name__)
        return PreparedReply("彩蛋图片暂时不可用，请稍后重试。")
    return PreparedReply("彩蛋图片发送失败，请稍后重试。", image)


def _image_reply(content: str, repository: SongRepository, ai_parser: AIQueryParser | None = None) -> bytes | None:
    """Compatibility helper for local previews; live replies use _prepare_reply."""
    easter_egg = _easter_egg_reply(content)
    if easter_egg is not None:
        return easter_egg.image
    if ai_parser and is_ai_request(content):
        _, result = ai_parser.answer_with_plan(content, repository)
    else:
        result = resolve_command(content, repository)
    return _image_from_result(result, repository, locale_for(content))


def _prepare_reply(content: str, repository: SongRepository,
                   ai_parser: AIQueryParser) -> PreparedReply | None:
    easter_egg = _easter_egg_reply(content)
    if easter_egg is not None:
        return easter_egg
    started = time.monotonic()

    def elapsed() -> float:
        return (time.monotonic() - started) * 1000

    try:
        before = catalog_version(repository)
        if is_ai_request(content):
            reply, result = ai_parser.answer_with_plan(content, repository)
        else:
            result = resolve_command(content, repository)
            reply = handle_command(content, repository, resolved=result)
    except Exception as exc:
        logger.error("查询结果生成失败；错误类型=%s 耗时=%.0fms", type(exc).__name__, elapsed())
        return PreparedReply("查询暂时失败，请稍后重试。")
    if not reply:
        logger.info("未识别为查询，不回复；内容=%s", describe(content))
        return None
    candidate = capture_context(result, repository, catalog_before=before, complete=True)
    prepared = _render_prepared(reply, result, repository, locale_for(content), candidate)
    logger.info("查询完成；内容=%s 文本=%d字 图片=%s 耗时=%.0fms",
                describe(content), len(reply),
                f"{len(prepared.image):,}B" if prepared.image else "无", elapsed())
    return prepared


def _prepare_followup(context, operation, repository):
    from ...structured_query import answer_for
    if isinstance(context,DeliveryCursor):
        if context.uncertain:
            return PreparedReply('上一张图片的发送回执不确定，暂不自动重发，请重新查询。')
        if operation.kind == 'continue':
            return (PreparedReply('',pages=context.remaining,context=context) if context.remaining
                    else PreparedReply('本次查询的图片已经全部发送。',context=context))
        context=context.query_context
        if context is None:
            return PreparedReply('此结果支持 /继续；选择卡牌或歌曲请先查询对应列表。')
    if operation.kind == 'continue':
        return PreparedReply('本次查询的图片已经全部发送。',context=context)
    try:
        outcome = execute_followup(context, operation, repository)
        if isinstance(outcome, str):
            return PreparedReply(outcome)
        result, candidate = outcome
        if isinstance(result, QueryResult):
            text = answer_for(result.spec, repository, result=result)
        else:
            text = handle_command("/查曲", repository, resolved=result)
        return _render_prepared(text, result, repository, "zh", candidate)
    except Exception as exc:
        logger.error("续查准备失败；错误类型=%s", type(exc).__name__)
        return PreparedReply("查询暂时失败，请稍后重试。")


def _visual_asset(url,size):
    from ...visuals import load_visual_asset
    return load_visual_asset(url,size)


def _complete_list_reply(result,repository,locale,candidate):
    if result is None or result.status != 'success':
        return None
    if isinstance(result,QueryResult) and result.spec.field:
        return None
    cards=();songs=();label=''
    if result.catalog is not None:
        answer=result.catalog
        if answer.request.mode != 'list':return None
        cards=answer.cards;label=answer.request.query
    elif result.song_selection is not None:
        songs=result.song_selection.songs;label=result.song_selection.request.query
    elif isinstance(result,QueryResult):
        if result.spec.intent == 'chart' or (result.spec.subject and result.spec.subject.kind in {'song','card','support_card'}):return None
        cards=result.cards or result.support_cards;songs=result.songs;label=result.spec.query_label()
    elif result.parsed[0] in {'songs','cards','support_cards'}:
        kind,label,_=result.parsed
        if kind!='songs' and label.isdigit():return None
        cards=result.cards or result.support_cards;songs=result.songs
    else:return None
    page=(result.catalog.request.page if result.catalog else result.song_selection.page if result.song_selection else
          result.spec.page if isinstance(result,QueryResult) else int(result.parsed[2] or 1))
    if page<1 or page>max(1,(len(cards or songs)+15)//16):return None
    if cards:
        from ...rendering.catalog_layout import render_list
        parts,records,ranges=render_list(cards,label,locale,load=_visual_asset)
    elif songs:
        from ...rendering.song_layout import render_list
        parts,ranges=render_list(songs,label,locale,load=_visual_asset);records=tuple(songs)
    else:return None
    if candidate is not None and not candidate.complete:
        candidate=capture_context(result,repository,catalog_before=candidate.version[0],complete=True)
    if candidate is not None:
        # Preserve the original difficulty/selection metadata while aligning IDs.
        by_id={v.entity_id:v for v in candidate.visible}
        candidate=replace(candidate,visible=tuple(by_id[r.id] for r in records))
    pages=[]
    from ...data import localized_text
    for part,(start,stop) in zip(parts,ranges):
        text='\n'.join(f'{i+1}. ID {r.id} '+localized_text(r,'title',locale)+' · '+
                       (localized_text(r,'character',locale)+'\n'+'\n'.join(localized_text(skill,'description',locale) for skill in r.skills)
                        if hasattr(r,'skills') else localized_text(r,'band',locale)+' · '+' / '.join(f'{ch.difficulty} {ch.display_level:g} ({ch.notes if ch.notes is not None else "?"} Note)' for ch in r.charts))
                       for i,r in enumerate(records[start:stop],start))
        text+=f'\n共 {len(records)} 条，本图 {start+1}–{stop}。'
        ctx=replace(candidate,visible=candidate.visible[:stop]) if candidate else None
        pages.append(PreparedReply(text,part.image,context=ctx))
    return pages[0] if len(pages)==1 else PreparedReply('',pages=tuple(pages),context=candidate)


def _complete_detail_reply(result,repository,locale,candidate):
    if result is None or result.status!='success':return None
    card=None
    if result.catalog is not None and result.catalog.request.mode=='detail':
        card=result.catalog.cards[0]
    elif isinstance(result,QueryResult):
        if result.spec.field or not result.spec.subject:return None
        if result.spec.intent=='card' and result.cards:card=repository.card_with_detail(result.cards[0])
        if result.spec.intent=='support_card' and result.support_cards:card=repository.support_card_with_detail(result.support_cards[0])
    elif result.parsed[0] in {'cards','support_cards'} and result.parsed[1].isdigit():
        if result.cards:card=repository.card_with_detail(result.cards[0])
        elif result.support_cards:card=repository.support_card_with_detail(result.support_cards[0])
    if card is None:return None
    from ...rendering.card_detail_layout import render
    parts=render(card,locale,load=_visual_asset)
    from ...data import localized_text
    text=f'ID {card.id} '+localized_text(card,'title',locale)+' · '+localized_text(card,'character',locale)
    text+='\n表演／技巧／形象（Lv.1）：'+str(card.catalog.get('stats_level1') or '未获取')
    for index,skill in enumerate(card.skills):
        text+='\n'+localized_text(skill,'name',locale)+'：'+localized_text(skill,'description',locale)
        levels=card.catalog.get('skill_levels',[])
        if index<len(levels):
            text+='\n'+'\n'.join(f'Lv.{i+1} '+(d.get(locale) or d.get('ja') or '未知') for i,d in enumerate(levels[index]))
    for relation in card.catalog.get('relations',[]):
        text+='\n'+relation['kind']+' ID '+str(relation['id'])+' '+(relation['name'].get(locale) or relation['name'].get('ja') or '未知')

    pages=tuple(PreparedReply(text,p.image,context=candidate) for p in parts)
    return pages[0] if len(pages)==1 else PreparedReply(text,pages=pages,context=candidate)


def _complete_chart_reply(result,repository,locale,candidate):
    if result is None:return None
    if isinstance(result,QueryResult):
        if result.spec.field or result.spec.intent!='chart' or result.chart is None:return None
        song,charts=result.chart
    else:
        if result.parsed[0]!='chart' or not result.songs:return None
        song=result.songs[0];difficulty=result.parsed[2]
        charts=tuple(c for c in song.charts if difficulty is None or c.difficulty==difficulty)
    preview=next((c for c in charts if c.difficulty=='EXPERT'),charts[-1] if charts else None)
    score=None;notice=''
    if preview:
        try:
            if repository.chart_source=='moenotes':score=load_chart_score(song,preview)
            else:
                captured=load_chart_data(song,preview,source=repository.chart_source);score=captured.score;notice=captured.notice(locale)
        except ChartDataError:
            notice='谱面暂不可用'
    from ...rendering.chart_layout import render
    pages=render(song,charts,score,preview.difficulty if preview else None,locale,load=_visual_asset,notice=notice)
    text=f'{song.title} · ID {song.id} · '+notice+'\n'+' / '.join(f'{c.difficulty} {c.display_level:g} · {c.notes} Notes' for c in charts)
    replies=tuple(PreparedReply(text,image,context=candidate) for image in pages)
    return replies[0] if len(replies)==1 else PreparedReply('',pages=replies,context=candidate)


def _song_detail_reply(result,repository,locale,candidate):
    if result is None or len(result.songs)!=1 or result.catalog or result.song_selection:return None
    from ...data import normalize
    song=result.songs[0]
    if isinstance(result,QueryResult):
        if result.spec.intent!='song' or result.spec.field or not result.spec.subject or result.spec.subject.kind!='song':return None
    elif result.parsed[0]!='songs' or (result.parsed[1]!=str(song.id) and normalize(result.parsed[1]) not in {normalize(t) for t in song.titles+(song.title,)}):return None
    score=None;selected=next((c for c in song.charts if c.difficulty=='EXPERT'),None)
    if selected:
        try:
            score=load_chart_score(song,selected) if repository.chart_source=='moenotes' else load_chart_data(song,selected,source=repository.chart_source).score
        except ChartDataError:pass
    logos=song.catalog.get('band_logos',[])
    from ...rendering.song_detail_layout import render
    from ...data import localized_text
    text=f'{localized_text(song,"title",locale)} · ID {song.id} · '+localized_text(song,'band',locale)
    text+='\n'+' / '.join(f'{ch.difficulty} {ch.display_level:g} · {ch.notes if ch.notes is not None else "?"} Note' for ch in song.charts)
    if song.traits:text+='\n激奏：'+' → '.join(song.traits.missions or ())
    text+='\n乐曲时长（毫秒）：'+str(song.catalog.get('duration_ms') or '未获取')
    if score:
        from ...rendering.song_detail_layout import seconds_at
        events=score.get('events',{});bpms=events.get('bpm',[])
        text+='\nBPM：'+', '.join(str(v['bpm']) for v in bpms)
        text+='\n技能点位（秒）：'+', '.join(str(seconds_at(t,bpms)) for t in events.get('skill',[]))
    for label,rows in [('得分奖励',song.catalog.get('score_rewards',[])),('EXPERT 连击奖励',song.catalog.get('combo_rewards',{}).get('expert',[]))]:
        text+='\n'+label+'：'+' / '.join(str(row['tier'])+' '+(row['label'].get(locale) or row['label'].get('ja') or '道具')+' ×'+str(row['count']) for row in rows)
    return PreparedReply(text,render(song,score,locale,load=_visual_asset,band_logos=logos),context=candidate)


def _render_prepared(reply, result, repository, locale, candidate=None):
    from ...rendering.haneoka_theme import game_assets
    from ...sources.haneoka.game_assets import asset_bytes
    from ...config import runtime_data_dir
    with game_assets(lambda name: asset_bytes(name, runtime_data_dir() / 'game-assets')):
        return _render_prepared_inner(reply, result, repository, locale, candidate)


def _render_prepared_inner(reply, result, repository, locale, candidate=None):
    try:
        full = None if result is not None and result.cutoff is not None else (_song_detail_reply(result, repository, locale, candidate) or
                _complete_list_reply(result, repository, locale, candidate) or
                _complete_detail_reply(result, repository, locale, candidate) or
                _complete_chart_reply(result, repository, locale, candidate))
        if full is not None:
            return full
        if result is not None and result.cutoff is not None:
            from ...rendering.event_cutoff_visuals import load_artwork
            from ...rendering.ranking_layout import render
            from ...sources.haneoka.ranking_art import cards_for, event_marks, bands_for
            answer=result.cutoff
            source=getattr(repository,'_catalog_repository',None)
            saved=source.saved if source else None
            from ...data import Card, SupportCard
            from ...sources.moenotes_events import SERVERS, display_time, zone_label
            records=cards_for(saved,answer.event.server) if answer.event and not answer.request.numeric_only else ({},{})
            catalogs=tuple({key:cls(**value) for key,value in rows.items()} for cls,rows in zip((Card,SupportCard),records))
            assets=load_artwork(repository.event_cutoffs,answer.event,(answer.event.banner,*(b.song.jacket for b in answer.boards))) if answer.event and not answer.request.numeric_only else {}
            try:
                pages=render(answer,assets=assets,load=_visual_asset,catalogs=catalogs,song_bands=bands_for(saved,answer.event.server) if answer.event else {},bonus_marks=event_marks(saved,answer.event) if answer.event else (),display_time=display_time,server_label=SERVERS[answer.event.server][0] if answer.event else "",timezone_label=zone_label(answer.event.server) if answer.event else "")
            finally:
                for asset in assets.values():
                    if asset is not None:asset.close()
            if len(pages) == 1:
                return PreparedReply(pages[0].text, pages[0].image, complete_text=True)
            if pages:
                return PreparedReply(reply, pages=tuple(PreparedReply(page.text, page.image) for page in pages))
            return PreparedReply(reply, complete_text=True)
        image = _image_from_result(result, repository, locale)
    except Exception as exc:
        logger.warning("图片生成失败，改用文字；错误类型=%s", type(exc).__name__)
        image = None
        reply, candidate = _complete_fallback(reply, result, locale, candidate)
    if (image is None and result is not None and result.catalog is not None
            and result.catalog.request.mode == "art" and not result.catalog.error):
        reply += "\n卡面图片暂不可用，请稍后重试。"
    return PreparedReply(reply, image, complete_text=result is not None and result.cutoff is not None,
                         context=candidate)


def _complete_fallback(reply, result, locale, candidate):
    """A failed renderer must not restore the former first-page truncation."""
    if result is not None and result.cutoff is not None:
        return result.cutoff.text, candidate
    if result is None or result.status != 'success':
        return reply, candidate
    if isinstance(result, QueryResult) and result.spec.field:
        return reply, candidate
    if result.cutoff is not None:
        return result.cutoff.text, candidate
    from ...data import localized_text
    cards = result.cards or result.support_cards
    songs = result.songs
    if result.catalog is not None:
        if result.catalog.request.mode == 'art':
            return reply, candidate
        cards = result.catalog.cards
    if result.song_selection is not None:
        songs = result.song_selection.songs
    if cards:
        from ...rendering.catalog_layout import ordered
        records = ordered(cards)
        rows = [f'{i}. ID {card.id} '+localized_text(card,'title',locale)+' · '+
                localized_text(card,'character',locale)+'\n'+'\n'.join(
                    localized_text(skill,'name',locale)+'：'+localized_text(skill,'description',locale)
                    for skill in card.skills) for i,card in enumerate(records,1)]
    elif songs:
        records = songs
        rows = [f'{i}. ID {song.id} '+localized_text(song,'title',locale)+' · '+
                localized_text(song,'band',locale)+'\n'+' / '.join(
                    f'{chart.difficulty} {chart.display_level:g} · {chart.notes if chart.notes is not None else "?"} Note'
                    for chart in song.charts) for i,song in enumerate(records,1)]
    else:
        return reply, candidate
    if candidate is not None:
        choices = {choice.entity_id:choice for choice in candidate.visible}
        candidate = replace(candidate,visible=tuple(choices[r.id] for r in records))
    return '\n\n'.join(rows)+f'\n共 {len(records)} 条。', candidate


async def _upload_image(api, target_id: str, image: bytes, group: bool):
    from ...rendering.image_output import MAX_IMAGE_BYTES
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the upload budget")
    path = "/v2/groups/{target_id}/files" if group else "/v2/users/{target_id}/files"
    http = api._http
    if isinstance(http, BotHttp):
        # qq-botpy 1.2.1 hardcodes self.timeout inside request; a timeout kwarg
        # would be passed twice. Reuse the initialized session/token through a
        # shallow per-upload copy, without mutating concurrent message requests.
        await http.check_session()
        http = copy.copy(http)
        http.timeout = 30
    try:
        result = await http.request(
            Route("POST", path, target_id=target_id),
            json={"file_type": 1, "file_data": base64.b64encode(image).decode("ascii"), "srv_send_msg": False},
        )
    finally:
        if http is not api._http:
            # BotHttp.__del__ closes its session. The copy never owns the
            # shared connection; detach even on cancellation or an exception.
            session = http._session
            http._session = None
            if session is not api._http._session and session is not None and not session.closed:
                await session.close()
    if not isinstance(result, dict) or not result.get("file_info"):
        # SDK timeouts return None. Never log response bodies, headers or IDs.
        raise RuntimeError("QQ 图片上传超时或未返回 file_info")
    return {"file_info": result["file_info"]}


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str

    @property
    def confirmed(self):
        return self.status == "success"


def _receipt(payload):
    if isinstance(payload, dict):
        if payload.get("code") not in (None, 0):
            return DeliveryOutcome("failure")
        if isinstance(payload.get("id"), str) and payload["id"].strip():
            return DeliveryOutcome("success")
    return DeliveryOutcome("uncertain")


async def _deliver_reply(message, target_id: str, group: bool, reply: PreparedReply,
                         msg_seq: int = 1) -> DeliveryOutcome:
    """Send one reply as a passive reply to `message`.

    QQ rejects a second passive send that reuses the same (msg_id, msg_seq)
    pair, so every reply to one inbound message must carry a distinct, and
    increasing, msg_seq. See `QQ_PASSIVE_REPLY_LIMIT` for the per-message cap.
    """
    send = message._api.post_group_message if group else message._api.post_c2c_message
    target = {"group_openid" if group else "openid": target_id,
              "msg_id": message.id, "msg_seq": msg_seq}
    channel = "群聊" if group else "单聊"
    if reply.image:
        try:
            media = await _upload_image(message._api, target_id, reply.image, group)
        except Exception as exc:
            logger.warning("%s图片上传失败，改用文字；错误类型=%s", channel, type(exc).__name__)
            if len(reply.text)>1800:
                try:
                    receipt=_receipt(await send(**target,msg_type=0,content='图片上传失败，完整文字已保留；发送 /继续 查看。'))
                except Exception as error:
                    logger.error('%s失败提示发送回执不确定；错误类型=%s',channel,type(error).__name__)
                    return DeliveryOutcome('uncertain')
                return DeliveryOutcome('fallback_pending' if receipt.confirmed else receipt.status)
        else:
            try:
                caption={}
                if isinstance(reply.context,DeliveryCursor):
                    cursor=reply.context
                    caption['content']=f'图片 {cursor.sent}/{cursor.total}'+('；/继续 发送其余图片。' if cursor.remaining else '；本次已全部发送。')
                outcome = _receipt(await send(**target, msg_type=7, media=media, **caption))
            except Exception as exc:
                logger.error("%s图片发送失败，结果不确定，不自动重发；错误类型=%s", channel, type(exc).__name__)
                return DeliveryOutcome("uncertain")
            else:
                logger.info("%s图片发送回执=%s 大小=%s msg_seq=%d", channel, outcome.status, f"{len(reply.image):,}B", msg_seq)
            return outcome
    for offset, chunk in enumerate(reply.fallback_texts or (reply.text,)):
        try:
            outcome = _receipt(await send(**{**target, "msg_seq": msg_seq + offset}, msg_type=0, content=chunk))
        except Exception as exc:
            logger.error("%s文字发送失败，结果不确定，不自动重发；错误类型=%s", channel, type(exc).__name__)
            return DeliveryOutcome("uncertain")
        logger.info("%s文字发送回执=%s 字数=%d msg_seq=%d", channel, outcome.status, len(chunk or ""), msg_seq + offset)
        if not outcome.confirmed:
            return outcome
    return DeliveryOutcome("success")


def context_key(message, target_id, group):
    author = getattr(message, "author", None)
    user = getattr(author, "member_openid" if group else "user_openid", None)
    try:
        return ContextKey("qq", "group" if group else "c2c", target_id, user)
    except ValueError:
        return None


async def reply_commands(message, target_id, group, commands, *, gate, repository,
                         ai_parser, limit, sequencer, contexts):
    """Coordinate arrivals and explicit receipts; SDK objects stay in this module."""
    key = context_key(message, target_id, group)
    operation = parse_operation(commands[0]) if len(commands) == 1 else None
    arrival_lineage = contexts.lineage(key) if key is not None and operation is not None else None
    lease = contexts.begin(key) if key is not None and operation is None else None
    ticket = await sequencer.issue()
    try:
        if operation is not None:
            async def followup():
                current = (contexts.get(key) if key is not None and arrival_lineage is not None
                           and contexts.lineage(key) == arrival_lineage else None)
                # Do not invalidate a still-pending full query when no result was displayed.
                followup_lease = contexts.begin(key, clear=False) if current is not None else None
                reply = (await gate.prepare_followup(current, operation, repository)
                         if current is not None else PreparedReply(MISSING))
                await sequencer.wait_turn(ticket)
                seq=1
                for part in _expand_replies([reply]):
                    outcome = await _deliver_reply(message, target_id, group, part, msg_seq=seq)
                    seq += part.passive_slots
                    if outcome.confirmed:
                        contexts.commit(followup_lease, part.context, confirmed=True)
                    else:
                        if outcome.status == 'fallback_pending':
                            contexts.commit(followup_lease,_fallback_cursor(part),confirmed=True)
                        if outcome.status == 'uncertain' and isinstance(part.context,DeliveryCursor):
                            contexts.commit(followup_lease, replace(part.context,uncertain=True), confirmed=True)
                        break
            if key is None:
                await followup()
            else:
                async with contexts.lock(key):
                    await followup()
            return
        replies, overflow = await prepare_commands(commands, gate, repository, ai_parser, limit)
        await sequencer.wait_turn(ticket)
        candidates = [r for r in replies if r.context is not None]
        confirmed_candidate = None
        seq = 1
        for reply in replies:
            outcome = await _deliver_reply(message, target_id, group, reply, msg_seq=seq)
            seq += reply.passive_slots
            if (len(commands) == 1 or isinstance(reply.context,DeliveryCursor)) and outcome.confirmed and reply.context is not None:
                confirmed_candidate = reply.context
            if not outcome.confirmed:
                if outcome.status == 'fallback_pending':
                    confirmed_candidate=_fallback_cursor(reply)
                if outcome.status == 'uncertain' and isinstance(reply.context,DeliveryCursor):
                    confirmed_candidate = replace(reply.context,uncertain=True)
                break
        if overflow and seq <= QQ_PASSIVE_REPLY_LIMIT:
            notice = PreparedReply(tr(locale_for(commands[0]), "too_many_commands", limit=limit, total=len(commands)))
            await _deliver_reply(message, target_id, group, notice, msg_seq=seq)
        contexts.commit(lease, confirmed_candidate, confirmed=confirmed_candidate is not None)
    finally:
        await sequencer.release(ticket)


def run_bot(app_id: str, app_secret: str, repository: SongRepository, settings: Settings) -> None:
    try:
        import botpy
        from botpy.message import C2CMessage, GroupMessage
    except ImportError as exc:
        raise RuntimeError("缺少 qq-botpy，请在仓库根目录运行 python -m pip install -e .") from exc

    ai_parser = AIQueryParser(settings)
    repository.chart_source = settings.chart_source
    query_gate = QueryGate(settings.query_concurrency, settings.query_queue_limit)
    from ...sources.cutoff_history import configure_sources
    from ...sources.cutoff_sampler import HistorySampler
    if repository.event_cutoffs.history is None:
        configure_sources(repository, settings)
    sampler = HistorySampler(repository.event_cutoffs, servers=settings.cutoff_sampling_servers,
                             interval=settings.cutoff_sampling_interval) if settings.cutoff_sampling_enabled else None
    repository.cutoff_sampler = sampler
    reply_sequencer = ReplySequencer(enabled=settings.reply_order)
    contexts = ContextStore()
    notice_ai = (AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model)
                 if settings.ai_api_key else None)
    if settings.update_notices and notice_ai is None:
        logger.warning("更新通知缺少 AI_API_KEY；不会生成或发送群公告")
    notifier = (UpdateNotifier(config.CONFIG_ROOT, settings.cache_file.with_name("update-notices.sqlite3"),
                               app_id, ai_client=notice_ai)
                if settings.update_notices else None)
    logger.info("回复顺序：%s（OURNOTES_REPLY_ORDER 可切换）",
                "按收到顺序发送" if settings.reply_order
                else "关闭排序，谁先算完谁先发（响应更快，但可能后问先答）")
    if settings.qq_gateway_host:
        logger.info("WebSocket 网关域名统一为 %s（可用 OURNOTES_QQ_GATEWAY_HOST 置空关闭）",
                    settings.qq_gateway_host)
        install_gateway_host(settings.qq_gateway_host)

    class OurNotesClient(botpy.Client):
        async def bot_connect(self, session) -> None:
            install_group_parser(self._connection, self.ws_dispatch, app_id,
                                 notifier.observe if notifier else None)
            await super().bot_connect(session)

        async def on_ready(self) -> None:
            logger.info("机器人 %s 已上线", self.robot.name)

        async def _reply_commands(self, message, target_id: str, group: bool,
                                  commands: list[str]) -> None:
            """Prepare one message's commands, then reply in the order asked.

            A ticket is taken on arrival so this batch leaves the gate in turn,
            rather than jumping ahead of an earlier message that is still
            working; that step is skipped when OURNOTES_REPLY_ORDER is off.
            Commands inside one message are always answered in the order they
            were written. Every send carries its own msg_seq, because QQ
            discards a passive reply that repeats an earlier (msg_id, msg_seq)
            pair. The total is kept within QQ_PASSIVE_REPLY_LIMIT, leaving room
            for the notice.
            """
            logger.info("%s收到 %d 条指令：%s", "群聊" if group else "单聊", len(commands),
                        " | ".join(describe(command) for command in commands))
            await reply_commands(message, target_id, group, commands, gate=query_gate,
                                 repository=repository, ai_parser=ai_parser,
                                 limit=settings.multi_command_limit,
                                 sequencer=reply_sequencer, contexts=contexts)

        async def _reply_group(self, message: GroupMessage) -> None:
            if notifier:
                notifier.observe(message.group_openid)
            if not hasattr(self, "_seen_group_ids"):
                self._seen_group_ids = deque(maxlen=256)
            if message.id and message.id in self._seen_group_ids:
                return
            if message.id:
                self._seen_group_ids.append(message.id)
            commands = split_commands(message.content)
            if commands:
                await self._reply_commands(message, message.group_openid, True, commands)

        async def on_group_at_message_create(self, message: GroupMessage) -> None:
            await self._reply_group(message)

        async def on_group_add_robot(self, event) -> None:
            if notifier:
                notifier.observe(event.group_openid, event="add", timestamp=event.timestamp)

        async def on_group_del_robot(self, event) -> None:
            if notifier:
                notifier.observe(event.group_openid, event="remove", timestamp=event.timestamp)

        async def on_group_msg_receive(self, event) -> None:
            if notifier:
                notifier.observe(event.group_openid, event="allow", timestamp=event.timestamp)

        async def on_group_msg_reject(self, event) -> None:
            if notifier:
                notifier.observe(event.group_openid, event="reject", timestamp=event.timestamp)

        async def on_c2c_message_create(self, message: C2CMessage) -> None:
            commands = split_commands(message.content)
            if commands:
                await self._reply_commands(message, message.author.user_openid, False, commands)

    async def song_traits_loop() -> None:
        while True:
            try:
                await asyncio.to_thread(repository.refresh_song_traits)
            except Exception:
                logger.exception("歌曲属性刷新失败，保留主资料和旧属性缓存")
            await asyncio.sleep(300)

    async def refresh_loop() -> None:
        while True:
            # Unix half-hours also align with Hong Kong's UTC+8 wall clock.
            # Recalculate after each refresh; skip missed slots without catch-up.
            await asyncio.sleep(1800 - time.time() % 1800)
            try:
                await asyncio.to_thread(repository.refresh)
                logger.info("Ournotes 数据刷新成功，共 %d 首曲目", len(repository.songs))
            except Exception:
                logger.exception("刷新失败，继续使用现有缓存")

    class ClientWithRefresh(OurNotesClient):
        async def on_ready(self) -> None:
            await super().on_ready()
            if not hasattr(self, "_refresh_task"):
                self._refresh_task = asyncio.create_task(refresh_loop())
            if not hasattr(self, "_song_traits_task") or self._song_traits_task.done():
                self._song_traits_task = asyncio.create_task(song_traits_loop())
            if sampler:
                sampler.start()
            if notifier and (not hasattr(self, "_notice_task") or self._notice_task.done()):
                self._notice_task = asyncio.create_task(notifier.run(self.api))

    intents = botpy.Intents(public_messages=True)
    # Synchronous source downloads use asyncio.run(), which clears the current
    # loop. botpy requires one while constructing its client, before run().
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    owns_loop = loop is None or loop.is_closed()
    if owns_loop:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    try:
        ClientWithRefresh(intents=intents).run(appid=app_id, secret=app_secret)
    finally:
        try:
            if sampler:
                sampler.stop()
        finally:
            if owns_loop:
                loop.close()
                asyncio.set_event_loop(None)
