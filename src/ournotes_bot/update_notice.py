"""Announce committed deployments; persist group discovery and send reservations.

The live QQ connection owns this worker. Candidate readiness alone is insufficient:
the updater must commit active state and remove its rollback transaction first.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from botpy.http import Route

logger = logging.getLogger(__name__)
RELEASE_SOURCE = Path(__file__).resolve().parents[2]


class NoticeStore:
    def __init__(self, path: Path, app_id: str):
        self.path, self.app_id = path, app_id
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS groups (
                    app TEXT NOT NULL, gid TEXT NOT NULL, present INTEGER NOT NULL,
                    allowed INTEGER, event_at REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (app, gid));
                CREATE TABLE IF NOT EXISTS notices (
                    app TEXT NOT NULL, gid TEXT NOT NULL, revision TEXT NOT NULL,
                    status TEXT NOT NULL, receipt TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (app, gid, revision));
            """)

    def connect(self):
        return sqlite3.connect(self.path, timeout=2)

    def observe(self, gid: str, *, event: str = "message", timestamp=None):
        if not isinstance(gid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", gid):
            return
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO groups(app,gid,present) VALUES(?,?,1)",
                       (self.app_id, gid))
            if event == "message":
                return  # A query does not grant proactive permission or undo removal.
            if event not in {"add", "remove", "allow", "reject"}:
                return
            try:
                stamp = float(timestamp)
            except (TypeError, ValueError):
                stamp = time.time()
            if not 0 <= stamp < float("inf"):
                return
            if event == "add":
                db.execute("UPDATE groups SET present=1,allowed=NULL,event_at=? "
                           "WHERE app=? AND gid=? AND event_at<=?",
                           (stamp, self.app_id, gid, stamp))
            else:
                field = "present" if event == "remove" else "allowed"
                value = 1 if event == "allow" else 0
                db.execute(f"UPDATE groups SET {field}=?,event_at=? "
                           "WHERE app=? AND gid=? AND event_at<=?",
                           (value, stamp, self.app_id, gid, stamp))

    def pending(self, revision: str):
        with closing(self.connect()) as db:
            return db.execute(
                "SELECT gid,allowed,event_at FROM groups g WHERE app=? AND present=1 "
                "AND NOT EXISTS (SELECT 1 FROM notices n WHERE n.app=g.app "
                "AND n.gid=g.gid AND n.revision=?) ORDER BY gid",
                (self.app_id, revision)).fetchall()

    def permission(self, gid: str, allowed: bool, event_at: float):
        # A permission/leave event received during the HTTP call takes precedence.
        with closing(self.connect()) as db, db:
            db.execute("UPDATE groups SET allowed=? WHERE app=? AND gid=? AND event_at=?",
                       (int(allowed), self.app_id, gid, event_at))

    def reserve(self, gid: str, revision: str):
        # Commit BEFORE sending. A crash or ambiguous HTTP outcome must not resend.
        with closing(self.connect()) as db, db:
            row = db.execute("SELECT present,allowed FROM groups WHERE app=? AND gid=?",
                             (self.app_id, gid)).fetchone()
            if not row or not row[0]:
                return False
            status = "disabled" if row[1] == 0 else "sending"
            inserted = db.execute("INSERT OR IGNORE INTO notices(app,gid,revision,status) VALUES(?,?,?,?)",
                                  (self.app_id, gid, revision, status)).rowcount
            return bool(inserted and status == "sending")

    def finish(self, gid: str, revision: str, status: str, receipt: str = ""):
        with closing(self.connect()) as db, db:
            db.execute("UPDATE notices SET status=?,receipt=? WHERE app=? AND gid=? AND revision=?",
                       (status, receipt, self.app_id, gid, revision))


def committed_notice(project_root: Path, source: Path):
    folder = project_root / "data/updater"
    if (folder / "transaction.json").exists() or not (folder / "state.json").exists():
        return None
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    active = state.get("active", {})
    revision = active.get("sha", "")
    if (not re.fullmatch(r"[0-9a-f]{40}", revision)
            or Path(active.get("source", "")).resolve() != source.resolve()
            or not state.get("previous") or state.get("rejected")):
        return None
    path = source / "更新通知.txt"
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8").strip()
    if not content or len(content) > 500:
        raise ValueError("Update summary must contain 1-500 characters")
    return revision, content


class UpdateNotifier:
    def __init__(self, project_root: Path, data_file: Path, app_id: str,
                 source: Path = RELEASE_SOURCE):
        self.project_root, self.source = project_root, source
        self.data_file, self.app_id = data_file, app_id
        self._store = None
        self._lock = asyncio.Lock()
        self._next_request = 0.0

    @property
    def store(self):
        if self._store is None:
            self._store = NoticeStore(self.data_file, self.app_id)
        return self._store

    def observe(self, gid, **kwargs):
        try:
            self.store.observe(gid, **kwargs)
        except Exception as exc:
            logger.error("更新通知群记录失败；错误类型=%s", type(exc).__name__)

    async def _pace(self):
        # Both the state endpoint and unverified bots permit only 30 requests/min.
        await asyncio.sleep(max(0, self._next_request - time.monotonic()))
        self._next_request = time.monotonic() + 2.1

    async def deliver(self, api):
        async with self._lock:
            notice = committed_notice(self.project_root, self.source)
            if not notice:
                return
            revision, content = notice
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
