# L3
# Input: QQ 事件／消息、Settings、SongRepository、AIQueryParser 及 CommandResult／QueryResult。
# Output: PreparedReply 文字与可选图片；外部为被动回复，批内按命令顺序交付，批间排序可配置且超时可提前发送。
# Pos: Platform / QQ 的事件接入、回复准备／交付与后台任务接线；见 ../../../L2-QQ.md。
# Effects/Dependencies: botpy 连接、上传／发送、线程与 asyncio 任务、去重／队列状态；经业务模块联网及读写缓存；固定 logger ournotes_bot.qq 供更新器识别就绪。

from __future__ import annotations

import asyncio
import base64
import copy
import logging
import re
import time
from collections import deque
from dataclasses import dataclass
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
from ...sources.chart_data import ChartDataError, load_chart_score
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


def _chart_image(song, charts, locale: str) -> bytes:
    preview = next((chart for chart in charts if chart.difficulty == "EXPERT"), charts[-1] if charts else None)
    score = None
    if preview:
        try:
            score = load_chart_score(song, preview)
        except ChartDataError:
            logger.warning("音符谱面暂不可用：歌曲 %s，难度 %s", song.id, preview.difficulty)
    return render_chart(song, charts, locale, score, preview.difficulty if preview else None)


@dataclass(frozen=True)
class PreparedReply:
    text: str
    image: bytes | None = None
    pages: tuple[PreparedReply, ...] = ()
    complete_text: bool = False
    context: QueryContext | None = None


def _expand_replies(replies: list[PreparedReply], reserve: int = 0) -> list[PreparedReply]:
    """Budget all pages before any send. Text fallback uses the captured complete result."""
    expanded = []
    for index, reply in enumerate(replies):
        available = QQ_PASSIVE_REPLY_LIMIT - reserve - len(expanded) - (len(replies) - index - 1)
        if reply.pages and len(reply.pages) <= available:
            expanded.extend(reply.pages)
        elif reply.pages or reply.complete_text:
            # Conservative local text budget, not a claim about QQ's platform hard limit.
            chunks, current = [], ""
            fallback = reply.text + ("\n本次容量不足，未附曲线，请单独查询歌曲。" if reply.pages else "")
            for line in fallback.splitlines(keepends=True):
                if current and len(current) + len(line) > 1800:
                    chunks.append(current)
                    current = ""
                while len(line) > 1800:
                    chunks.append(line[:1800])
                    line = line[1800:]
                current += line
            if current:
                chunks.append(current)
            if len(chunks) <= available:
                expanded.extend(PreparedReply(chunk) for chunk in chunks)
            else:
                expanded.append(PreparedReply("本次完整榜线结果超过回复预算，无法完整发送；请将 /查榜线 单独发送，或指定一首歌曲。"))
        else:
            expanded.append(reply)
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
    if result is not None and result.meta is not None:
        from ...visuals import render_meta
        return render_meta(result.meta) if result.meta.rows else None
    if isinstance(result, QueryResult):
        spec = result.spec
        if spec.intent == "song":
            songs = result.songs
            visible = page_slice(songs, spec.page)
            return render_song_list(visible, spec.query_label(), locale,
                                    page_notice("songs", spec.query_label(), spec.page, len(songs), locale)) if visible else None
        if spec.intent == "chart":
            chart = result.chart
            return _chart_image(chart[0], chart[1], locale) if chart else None
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
        return _chart_image(songs[0], charts, locale)
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


def _image_reply(content: str, repository: SongRepository, ai_parser: AIQueryParser | None = None) -> bytes | None:
    """Compatibility helper for local previews; live replies use _prepare_reply."""
    if ai_parser and is_ai_request(content):
        _, result = ai_parser.answer_with_plan(content, repository)
    else:
        result = resolve_command(content, repository)
    return _image_from_result(result, repository, locale_for(content))


def _prepare_reply(content: str, repository: SongRepository,
                   ai_parser: AIQueryParser) -> PreparedReply | None:
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
    candidate = capture_context(result, repository, catalog_before=before)
    prepared = _render_prepared(reply, result, repository, locale_for(content), candidate)
    logger.info("查询完成；内容=%s 文本=%d字 图片=%s 耗时=%.0fms",
                describe(content), len(reply),
                f"{len(prepared.image):,}B" if prepared.image else "无", elapsed())
    return prepared


def _prepare_followup(context, operation, repository):
    from ...structured_query import answer_for
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


def _render_prepared(reply, result, repository, locale, candidate=None):
    try:
        if result is not None and result.cutoff is not None:
            from ...rendering.event_cutoff_visuals import render_cutoff, load_artwork
            pages = render_cutoff(result.cutoff, asset_loader=lambda event, urls: load_artwork(repository.event_cutoffs, event, urls))
            if len(pages) == 1:
                return PreparedReply(reply, pages[0].image)
            if pages:
                return PreparedReply(reply, pages=tuple(PreparedReply(page.text, page.image) for page in pages))
            return PreparedReply(reply, complete_text=True)
        image = _image_from_result(result, repository, locale)
    except Exception as exc:
        logger.warning("图片生成失败，改用文字；错误类型=%s", type(exc).__name__)
        image = None
    if (image is None and result is not None and result.catalog is not None
            and result.catalog.request.mode == "art" and not result.catalog.error):
        reply += "\n卡面图片暂不可用，请稍后重试。"
    return PreparedReply(reply, image, complete_text=result is not None and result.cutoff is not None,
                         context=candidate)


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
        else:
            try:
                outcome = _receipt(await send(**target, msg_type=7, media=media))
            except Exception as exc:
                logger.error("%s图片发送失败，结果不确定，不自动重发；错误类型=%s", channel, type(exc).__name__)
                return DeliveryOutcome("uncertain")
            else:
                logger.info("%s图片发送回执=%s 大小=%s msg_seq=%d", channel, outcome.status, f"{len(reply.image):,}B", msg_seq)
            return outcome
    try:
        outcome = _receipt(await send(**target, msg_type=0, content=reply.text))
    except Exception as exc:
        logger.error("%s文字发送失败，结果不确定，不自动重发；错误类型=%s", channel, type(exc).__name__)
        return DeliveryOutcome("uncertain")
    else:
        logger.info("%s文字发送回执=%s 字数=%d msg_seq=%d", channel, outcome.status, len(reply.text or ""), msg_seq)
        return outcome


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
                outcome = await _deliver_reply(message, target_id, group, reply)
                contexts.commit(followup_lease, reply.context, confirmed=outcome.confirmed)
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
        for seq, reply in enumerate(replies, start=1):
            outcome = await _deliver_reply(message, target_id, group, reply, msg_seq=seq)
            if len(candidates) == 1 and outcome.confirmed and reply.context is not None:
                confirmed_candidate = reply.context
        if overflow and len(replies) < QQ_PASSIVE_REPLY_LIMIT:
            notice = PreparedReply(tr(locale_for(commands[0]), "too_many_commands", limit=limit, total=len(commands)))
            await _deliver_reply(message, target_id, group, notice, msg_seq=len(replies) + 1)
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
    query_gate = QueryGate(settings.query_concurrency, settings.query_queue_limit)
    from ...sources.cutoff_history import CutoffHistory
    from ...sources.cutoff_sampler import HistorySampler
    if repository.event_cutoffs.history is None:
        repository.event_cutoffs.history = CutoffHistory(
            settings.cutoff_history_file or settings.cache_file.with_name("moenotes-history-v1.sqlite3"),
            enabled=settings.cutoff_history_enabled, min_free_mb=settings.cutoff_history_min_free_mb)
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
            await asyncio.sleep(6 * 3600)
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
    try:
        ClientWithRefresh(intents=intents).run(appid=app_id, secret=app_secret)
    finally:
        if sampler:
            sampler.stop()
