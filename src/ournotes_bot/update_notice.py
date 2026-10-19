# L3: Legacy QQ notification transport; shared reports/ledger remain platform independent.
from __future__ import annotations
import asyncio
import hashlib
import logging
from botpy.http import Route
from .application.notice_reports import NoticeStore, NoticeReports, committed_notice, release_facts, RELEASE_SOURCE
logger = logging.getLogger(__name__)

class UpdateNotifier(NoticeReports):
    async def deliver(self, api):
        async with self._lock:
            notice = committed_notice(self.project_root, self.source)
            if not notice:
                return
            revision, previous_sha = notice
            content = await self._report(revision, previous_sha)
            if not content or committed_notice(self.project_root, self.source) != notice:
                return
            for gid, allowed, event_at in self.store.pending(revision):
                await self._pace()
                try:
                    state = await api._http.request(Route(
                        "GET", "/v2/groups/{group_openid}/bot_state", group_openid=gid))
                    if isinstance(state, dict) and isinstance(state.get("allow_proactive_msg"), bool):
                        self.store.permission(gid, state["allow_proactive_msg"], event_at)
                except Exception:
                    # This GET is whitelist-only. The POST still enforces the QQ
                    # user's permission; never impersonate a passive reply.
                    pass
                if committed_notice(self.project_root, self.source) != notice:
                    return
                await self._pace()
                if committed_notice(self.project_root, self.source) != notice:
                    return
                if not self.store.reserve(gid, revision):
                    continue
                label = hashlib.sha256(gid.encode()).hexdigest()[:8]
                try:
                    # botpy 1.2.1 retries connection resets and discards the retry
                    # response. Starting at its last attempt prevents duplicate
                    # proactive messages after an uncertain network result.
                    result = await api._http.request(
                        Route("POST", "/v2/groups/{group_openid}/messages", group_openid=gid),
                        retry_time=2, json={"msg_type": 0, "content": content})
                except Exception as exc:
                    self.store.finish(gid, revision, "failed")
                    logger.warning("更新通知未确认送达；群=%s 版本=%s 错误类型=%s；不自动重发",
                                   label, revision[:12], type(exc).__name__)
                else:
                    receipt = result.get("id") if isinstance(result, dict) else None
                    if isinstance(receipt, str) and receipt:
                        self.store.finish(gid, revision, "sent", receipt)
                        logger.info("更新通知已发送；群=%s 版本=%s", label, revision[:12])
                    else:
                        self.store.finish(gid, revision, "uncertain")
                        logger.warning("更新通知无有效回执；群=%s 版本=%s；不自动重发", label, revision[:12])

    async def run(self, api):
        logger.info("自动更新通知已启用；仅在版本切换完成后发送，群记录持久保存")
        while True:
            try:
                await self.deliver(api)
            except Exception as exc:
                logger.error("更新通知暂不可用；错误类型=%s", type(exc).__name__)
            await asyncio.sleep(30)
