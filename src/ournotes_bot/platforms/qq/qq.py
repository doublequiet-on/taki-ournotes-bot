# L3
# Input: QQ 事件／消息、Settings、SongRepository、AIQueryParser 及 CommandResult／QueryResult。
# Output: PreparedReply 同捕获图片／完整文字、预算与选择快照；DeliveryOutcome 明确全部交付回执；批内顺序固定。
# Pos: Platform / QQ 事件、交付与后台任务；回复准备复用 application.replies；见 ../../../L2-QQ.md。
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
from ...commands import ALIASES, locale_for, split_commands
from ...config import QQ_PASSIVE_REPLY_LIMIT, Settings
from ... import config
from ...data import SongRepository
from ...i18n import tr
from ...query.continuation import ContextKey, ContextStore, MISSING, parse_operation
from ...ai_client import AIClient
from ...update_notice import UpdateNotifier


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


from ...application.replies import (
    chart_image as _chart_image,
    PreparedReply,
    DeliveryCursor,
    fallback_cursor as _fallback_cursor,
    text_chunks as _text_chunks,
    expand_replies as _expand_replies,
    QueryGate,
    ReplySequencer,
    prepare_commands,
    image_from_result as _image_from_result,
    easter_egg_reply as _easter_egg_reply,
    image_reply as _image_reply,
    prepare_reply as _prepare_reply,
    prepare_followup as _prepare_followup,
    visual_asset as _visual_asset,
    complete_list_reply as _complete_list_reply,
    detail_card as _detail_card,
    complete_detail_reply as _complete_detail_reply,
    complete_chart_reply as _complete_chart_reply,
    song_detail_reply as _song_detail_reply,
    render_prepared as _render_prepared,
    render_prepared_inner as _render_prepared_inner,
    complete_fallback as _complete_fallback
)


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
    # msg_id binds the passive reply to its sender; QQ displays the native @.
    # Explicit mention markup is displayed literally by the reported client.
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
                caption={"content": " "}
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


async def _refresh_catalog(repository: SongRepository) -> None:
    """Report the published cache state, including non-raising source fallback."""
    failure = False
    try:
        await asyncio.to_thread(repository.refresh)
    except Exception:
        failure = True
        logger.exception("主资料刷新抛出异常，检查当前已发布缓存状态")
    catalog = repository.capture_catalog()
    state = catalog.cache_state
    if state == "unsaved":
        logger.warning("主资料已取得，但本地缓存保存失败，共 %d 首曲目", len(catalog.songs))
    elif state == "stale":
        logger.warning("主资料刷新失败，继续使用旧缓存，共 %d 首曲目", len(catalog.songs))
    elif failure:
        logger.error("主资料刷新失败，当前缓存状态=%s，共 %d 首曲目", state, len(catalog.songs))
    elif state == "fresh":
        logger.info("主资料刷新成功，已取得并保存，共 %d 首曲目", len(catalog.songs))
    elif state == "cached":
        logger.info("主资料使用已验证缓存，共 %d 首曲目", len(catalog.songs))
    else:
        logger.warning("主资料刷新结果未确认，当前缓存状态=%s，共 %d 首曲目", state, len(catalog.songs))


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
    notice_ai = (AIClient(settings.ai_base_url, settings.ai_api_key, settings.ai_model, settings.ai_reasoning_effort, settings.ai_openai_proxy)
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
            await _refresh_catalog(repository)

    async def expire_conversations():
        while True:
            await asyncio.sleep(1)
            contexts.sweep()
            ai_parser.sweep()

    class ClientWithRefresh(OurNotesClient):
        async def on_ready(self) -> None:
            await super().on_ready()
            if not hasattr(self, '_context_expiry_task') or self._context_expiry_task.done():
                self._context_expiry_task = asyncio.create_task(expire_conversations())
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
                async def drain_owned_loop():
                    pending = [task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task()]
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    # Keep the CLI DataOwner lock until canceled to_thread writers really finish.
                    await loop.shutdown_default_executor()
                if not loop.is_closed():
                    loop.run_until_complete(drain_owned_loop())
                    loop.close()
                asyncio.set_event_loop(None)
