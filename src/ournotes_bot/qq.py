from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections import deque
from dataclasses import dataclass

from botpy.http import Route
from botpy.message import GroupMessage

# qq-botpy 1.2.1 still ships the retired hostname; QQ API v2 now uses this host.
Route.DOMAIN = "api.bot.qq.com"
Route.SANDBOX_DOMAIN = "api.bot.qq.com"

from .ai_query import AIQueryParser, is_ai_request
from .chart_data import ChartDataError, load_chart_score
from .commands import (ALIASES, CommandResult, handle_command, locale_for, page_notice,
                       page_slice, resolve_command, split_commands)
from .config import QQ_PASSIVE_REPLY_LIMIT, Settings
from .data import SongRepository
from .i18n import tr
from .structured_query import QueryResult
from .visuals import render_card, render_card_list, render_chart, render_song_list


logger = logging.getLogger(__name__)


def _is_group_query(content: object) -> bool:
    if not isinstance(content, str):
        return False
    cleaned = re.sub(r"<@!?\w+>", "", content).strip()
    if not cleaned:
        return False
    if cleaned.startswith(("/", "／")) or is_ai_request(cleaned):
        return True
    first = cleaned.split(None, 1)[0].casefold()
    return first in ALIASES or first in {"帮助", "help", "ヘルプ", "数据状态", "状态", "status", "状態"}


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


def register_group_message_parser(parser: dict, api, dispatch, app_id: str) -> None:
    """Dispatch only self-mentioned GROUP_MESSAGE_CREATE events from the newer gateway."""
    def parse(payload):
        data = payload.get("d", {})
        author = data.get("author") if isinstance(data, dict) else None
        if (
            isinstance(data, dict) and data.get("group_openid")
            and not (isinstance(author, dict) and author.get("bot"))
            and _mentions_bot(data, app_id) and _is_group_query(data.get("content"))
        ):
            dispatch("group_at_message_create", GroupMessage(api, payload.get("id"), data))

    parser["group_message_create"] = parse


def install_group_parser(connection, dispatch, app_id: str) -> None:
    register_group_message_parser(connection.parser, connection.state.api, dispatch, app_id)


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
        async with self._lock:
            if self._pending >= self._capacity:
                return PreparedReply("当前查询较多，请稍后重试。")
            self._pending += 1
        try:
            async with self._workers:
                work = asyncio.create_task(asyncio.to_thread(_prepare_reply, content, repository, ai_parser))
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
    results = await asyncio.gather(
        *(gate.prepare(command, repository, ai_parser) for command in selected),
        return_exceptions=True,
    )
    replies: list[PreparedReply] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.error("查询结果准备失败；错误类型=%s", type(result).__name__)
            continue
        if result:
            replies.append(result)
    return replies, max(0, len(commands) - limit)


def _image_from_result(result: QueryResult | CommandResult | None,
                       repository: SongRepository, locale: str) -> bytes | None:
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
            cards = result.cards
            if not cards:
                return None
            if spec.subject and spec.subject.kind == "card":
                return render_card(repository.card_with_detail(cards[0]), locale)
            visible = page_slice(cards, spec.page)
            return render_card_list(visible, spec.query_label(), locale,
                                    page_notice("cards", spec.query_label(), spec.page, len(cards), locale)) if visible else None
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
    try:
        if is_ai_request(content):
            reply, result = ai_parser.answer_with_plan(content, repository)
        else:
            result = resolve_command(content, repository)
            reply = handle_command(content, repository, resolved=result)
    except Exception as exc:
        logger.error("查询结果生成失败；错误类型=%s", type(exc).__name__)
        return PreparedReply("查询暂时失败，请稍后重试。")
    if not reply:
        return None
    try:
        image = _image_from_result(result, repository, locale_for(content))
    except Exception as exc:
        logger.warning("图片生成失败，改用文字；错误类型=%s", type(exc).__name__)
        image = None
    return PreparedReply(reply, image)


async def _upload_image(api, target_id: str, image: bytes, group: bool):
    path = "/v2/groups/{target_id}/files" if group else "/v2/users/{target_id}/files"
    result = await api._http.request(
        Route("POST", path, target_id=target_id),
        json={"file_type": 1, "file_data": base64.b64encode(image).decode("ascii"), "srv_send_msg": False},
    )
    if not isinstance(result, dict) or not result.get("file_info"):
        raise RuntimeError("QQ 图片上传未返回 file_info")
    return {"file_info": result["file_info"]}


async def _deliver_reply(message, target_id: str, group: bool, reply: PreparedReply,
                         msg_seq: int = 1) -> None:
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
                await send(**target, msg_type=7, media=media)
            except Exception as exc:
                logger.error("%s图片发送失败，结果不确定，不自动重发；错误类型=%s", channel, type(exc).__name__)
            return
    try:
        await send(**target, msg_type=0, content=reply.text)
    except Exception as exc:
        logger.error("%s文字发送失败，结果不确定，不自动重发；错误类型=%s", channel, type(exc).__name__)


def run_bot(app_id: str, app_secret: str, repository: SongRepository, settings: Settings) -> None:
    try:
        import botpy
        from botpy.message import C2CMessage, GroupMessage
    except ImportError as exc:
        raise RuntimeError("缺少 qq-botpy，请先运行 pip install -r requirements.txt") from exc

    ai_parser = AIQueryParser(settings)
    query_gate = QueryGate(settings.query_concurrency, settings.query_queue_limit)

    class OurNotesClient(botpy.Client):
        async def bot_connect(self, session) -> None:
            install_group_parser(self._connection, self.ws_dispatch, app_id)
            await super().bot_connect(session)

        async def on_ready(self) -> None:
            logger.info("机器人 %s 已上线", self.robot.name)

        async def _reply_commands(self, message, target_id: str, group: bool,
                                  commands: list[str]) -> None:
            """Prepare one message's commands, then reply in the order asked.

            Every send carries its own msg_seq, because QQ discards a passive
            reply that repeats an earlier (msg_id, msg_seq) pair. The total is
            kept within QQ_PASSIVE_REPLY_LIMIT, leaving room for the notice.
            """
            limit = settings.multi_command_limit
            replies, overflow = await prepare_commands(
                commands, query_gate, repository, ai_parser, limit)
            for seq, reply in enumerate(replies, start=1):
                await _deliver_reply(message, target_id, group, reply, msg_seq=seq)
            if overflow and len(replies) < QQ_PASSIVE_REPLY_LIMIT:
                logger.info("单条消息指令数超过上限：收到 %d 条，执行 %d 条",
                            len(commands), len(replies))
                notice = PreparedReply(tr(locale_for(commands[0]), "too_many_commands",
                                          limit=limit, total=len(commands)))
                await _deliver_reply(message, target_id, group, notice,
                                     msg_seq=len(replies) + 1)

        async def _reply_group(self, message: GroupMessage) -> None:
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

        async def on_c2c_message_create(self, message: C2CMessage) -> None:
            commands = split_commands(message.content)
            if commands:
                await self._reply_commands(message, message.author.user_openid, False, commands)

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

    intents = botpy.Intents(public_messages=True)
    ClientWithRefresh(intents=intents).run(appid=app_id, secret=app_secret)
