# L3
# Input: 最低 rankings scope 的安全配置 Secret、地区／活动／挑战身份和公开发现快照。
# Output: 同行分数／数字ID／用户名的正式来源 BoardSnapshot，时间与错误明确标识。
# Pos: Data / Sources 的开放平台挑战榜适配；见 L2-2.md。
# Effects/Dependencies: 独立认证 HTTPS session、分钟并发与有限恢复、独立当前缓存及历史路由；不读凭据文件或 QQ。
"""Open Platform challenge rankings, with public discovery/artwork exceptions.

Only this fixed origin/path receives the Bearer secret. No profile endpoints,
permanent music ranking, event-point ranking or daily-total refusal is used.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import threading
import time
from collections import deque
from dataclasses import replace
from email.utils import parsedate_to_datetime
from pathlib import Path

from .moenotes_events import (EventCutoffRepository, BoardSnapshot, SourceError, _Entry,
                             _NETWORK_SLOTS, _ms, _id, _player_id, _player_name, _retry_after)
from .moenotes_music_data import decode_json

ORIGIN = "https://bdon.moe"
CONTRACT = "moenotes:challenge-ranking/1"
TIME_KIND = "upstream_fetched"
PATH = re.compile(r"https://bdon\.moe/api/open/v1/moenotes/(?:jp|tw|kr|en)/event/challenge-ranking\?challengeMusicId=[0-9]{1,20}")


def authenticated_get(url, secret, timeout):
    if not PATH.fullmatch(url) or not isinstance(secret, str) or not secret:
        raise SourceError("authentication")
    async def get():
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=False) as session:
            async with session.get(url, headers={"Authorization": "Bearer " + secret}, allow_redirects=False) as response:
                h = {k.lower(): str(v) for k, v in response.headers.items()}
                codes = {401: "authentication", 403: "permission", 404: "not_found", 429: "rate_limited",
                         502: "upstream", 503: "upstream", 504: "upstream"}
                if response.status != 200:
                    # Never log, return or chain gateway bodies/headers containing secrets.
                    code = codes.get(response.status, "open_response")
                    if response.status == 503:
                        try:
                            error = json.loads(await response.content.read(65536)).get("error", {})
                            kind = error.get("kind") if isinstance(error, dict) else None
                            code = {"maintenance": "maintenance", "authentication_required": "upstream_authentication",
                                    "authentication": "upstream_authentication", "device_conflict": "upstream_session",
                                    "session_changed": "upstream_session", "version": "upstream_version"}.get(kind, code)
                        except (ValueError, AttributeError, UnicodeError):
                            pass
                    raise SourceError(code, _retry_after(h.get("retry-after", "30"), time.time()))
                if "application/json" not in h.get("content-type", ""):
                    raise SourceError("invalid_json")
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 8_000_000:
                        raise SourceError("too_large")
                    chunks.append(chunk)
                # Cache only documented time/clock headers, never arbitrary headers.
                return b"".join(chunks), {k: v for k, v in h.items() if k in {"x-moenotes-fetched-at", "date"}}
    try:
        return asyncio.run(get())
    except SourceError:
        raise
    except Exception:
        raise SourceError("network") from None


class OpenClient:
    def __init__(self, secret="", *, transport=authenticated_get, clock=time.time, monotonic=time.monotonic, per_minute=100, state_file=None):
        self._secret, self.transport = secret, transport
        self.clock, self.monotonic = clock, monotonic
        self.per_minute = max(1, int(per_minute))
        self._lock = threading.Lock()
        self._admitted, self._cooldown, self._auth_failed = deque(), 0, False
        self.calls = 0  # Observational only. No daily reset or daily refusal.
        self.state_file = Path(state_file) if state_file else None
        self._state_loaded = False

    def _load_state(self):
        if self._state_loaded:
            return
        self._state_loaded = True
        if self.state_file is None:
            return
        try:
            if self.state_file.stat().st_size > 65536:
                return
            s = decode_json(self.state_file.read_bytes())
            if s.get("schema") != 1:
                return
            now, tick = self.clock(), self.monotonic()
            stamps = s.get("admitted", [])
            if not isinstance(stamps, list) or any(type(t) not in (int, float) or not 0 <= t <= now for t in stamps):
                return
            self._admitted = deque(tick - (now - t) for t in stamps if now - t < 60)
            cooldown = s.get("cooldown_until", 0)
            if type(cooldown) in (int, float) and math.isfinite(cooldown) and cooldown >= 0:
                self._cooldown = tick + max(0, cooldown - now)
            count = s.get("calls", 0)
            self.calls = count if type(count) is int and count >= 0 else 0
        except (OSError, ValueError, TypeError, AttributeError, OverflowError):
            pass

    def _save_state(self):
        if self.state_file is None:
            return
        import tempfile
        temporary = None
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            now, tick = self.clock(), self.monotonic()
            state = {"schema": 1, "admitted": [now - (tick - t) for t in self._admitted],
                     "cooldown_until": now + max(0, self._cooldown - tick), "calls": self.calls}
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.state_file.parent,
                                             prefix=self.state_file.name, suffix=".tmp", delete=False) as f:
                temporary = f.name
                json.dump(state, f, allow_nan=False)
            os.replace(temporary, self.state_file)
        except (OSError, ValueError):
            pass  # Disk faults do not reset the live minute/cooldown state.
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    pass

    def __repr__(self):
        return "OpenClient(configured=" + str(bool(self._secret)) + ")"

    def configure(self, secret):
        with self._lock:
            if secret != self._secret:
                self._secret, self._auth_failed = secret, False
                # Configuration changes do not reset shared minute/cooldown state.

    def _admit(self):
        with self._lock:
            self._load_state()
            now = self.monotonic()
            if not self._secret or self._auth_failed:
                raise SourceError("authentication")
            if now < self._cooldown:
                raise SourceError("rate_limited", self._cooldown - now)
            while self._admitted and now - self._admitted[0] >= 60:
                self._admitted.popleft()
            if len(self._admitted) >= self.per_minute:
                raise SourceError("minute_limit", 60 - (now - self._admitted[0]))
            self._admitted.append(now)
            self.calls += 1
            self._save_state()
            return self._secret

    def get(self, region, challenge_id, deadline):
        if region not in {"jp", "tw", "kr", "en"}:
            raise SourceError("invalid_identity")
        if not 1 <= int(_id(challenge_id)) <= 9223372036854775807:
            raise SourceError("invalid_identity")
        url = f"{ORIGIN}/api/open/v1/moenotes/{region}/event/challenge-ranking?challengeMusicId={_id(challenge_id)}"
        for attempt in range(2):
            remaining = deadline - self.monotonic()
            if remaining <= 0 or not _NETWORK_SLOTS.acquire(timeout=max(0, remaining)):
                raise SourceError("budget")
            try:
                secret = self._admit()
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise SourceError("budget")
                try:
                    raw, h = self.transport(url, secret, min(4, remaining))
                except SourceError as exc:
                    with self._lock:
                        if exc.code in {"authentication", "permission"}:
                            self._auth_failed = True
                        if exc.code == "rate_limited":
                            self._cooldown = max(self._cooldown, self.monotonic() + exc.retry_after)
                            self._save_state()
                    if exc.code in {"network", "upstream"} and not attempt and deadline - self.monotonic() >= .25:
                        continue
                    raise
                if len(raw) > 8_000_000:
                    raise SourceError("too_large")
                try:
                    body = decode_json(raw)
                except (ValueError, UnicodeError):
                    raise SourceError("invalid_json") from None
                if (not isinstance(body, dict) or "error" in body or "players" not in body and bool(body)
                        or not isinstance(body.get("players", []), list)):
                    raise SourceError("invalid_board")
                players = body.get("players", [])
                scores, ids, names = [], [], []
                for row in players[:100]:
                    # Protobuf JSON omits a default-zero score, only on an object
                    # of the contracted player shape. Holes/nulls remain unknown.
                    shaped = (isinstance(row, dict) and set(row) <= {"playerData", "score", "highScoreDeck"}
                              and ("playerData" not in row or isinstance(row["playerData"], dict)))
                    score = row.get("score", 0) if shaped else None
                    score = score if type(score) is int and score >= 0 else None
                    p = row.get("playerData", {}) if shaped else {}
                    scores.append(score)
                    ids.append(_player_id(p.get("id")) if score is not None else None)
                    names.append(_player_name(p.get("name")) if score is not None else None)
                headers = {str(k).lower(): str(v) for k, v in h.items() if str(k).lower() in {"x-moenotes-fetched-at", "date"}}
                return {"scores": scores, "player_ids": ids, "player_names": names}, headers
            finally:
                _NETWORK_SLOTS.release()


def event_identity(event):
    return (event.event_id, event.start_ms, event.end_ms,
            tuple((s.challenge_id, s.music_id, s.effective_start_ms, s.effective_end_ms) for s in event.songs))


class OpenEventCutoffRepository(EventCutoffRepository):
    source = "open"

    def __init__(self, cache_dir, *, client=None, **kwargs):
        super().__init__(cache_dir, **kwargs)
        self.client = client or OpenClient(clock=self.clock, monotonic=self.monotonic)
        self._event_identities = {}

    def event(self, server, deadline):
        event = super().event(server, deadline)
        with self._guard:
            self._event_identities[server] = event_identity(event)
        return event

    def board(self, event, song, deadline):
        if not song.enabled:
            return BoardSnapshot(song, status="本曲挑战榜未开放", source="open", time_kind=TIME_KIND, contract_version=CONTRACT)
        expected = event_identity(event)
        key = f"board:{event.server}:{event.event_id}:{song.challenge_id}:{song.music_id}:{song.effective_start_ms}:{song.effective_end_ms}:open:{CONTRACT}"
        # A source-specific cache uses the same deadline/single-flight/old-cache
        # policy, while the loader sends only the operation's challenge ID.
        def load():
            payload, headers = self.client.get(event.server, song.challenge_id, deadline)
            # Existing cache late-response comparison uses x-fetched-at. This is
            # an internal alias of the verified formal header, never a fake time.
            headers = {**headers, "x-fetched-at": headers.get("x-moenotes-fetched-at", "")}
            with self._guard:
                actual = self._event_identities.get(event.server, expected)
            if actual != expected or not any(s.challenge_id == song.challenge_id and s.music_id == song.music_id for s in event.songs):
                raise SourceError("identity_changed")
            return payload, headers
        try:
            entry = self._cached(key, 60, 600, load, deadline)
        except SourceError as exc:
            if self.history:
                self.history.failure(event.server, event.event_id, song.challenge_id, exc.code)
            labels = {"authentication": "正式来源未配置或认证失效", "permission": "正式来源权限不足",
                      "rate_limited": "正式来源限流，稍后重试", "minute_limit": "正式来源分钟窗口繁忙",
                      "not_found": "正式来源不支持或本曲暂无榜单", "identity_changed": "活动或挑战关联变化，请重新查询"}
            labels.update(maintenance="正式来源维护中", upstream_authentication="上游游戏会话认证异常",
                          upstream_session="上游游戏会话变化或冲突", upstream_version="上游版本不兼容")
            return BoardSnapshot(song, status=labels.get(exc.code, "正式榜线来源暂不可用"), source="open", time_kind=TIME_KIND, contract_version=CONTRACT)
        board = self._open_snapshot(event, song, entry)
        if self.history:
            if entry.late:
                self.history.record(event, self._open_snapshot(event, song, entry.late))
            if entry.fallback:
                self.history.failure(event.server, event.event_id, song.challenge_id, "source_fallback")
            # Unknown source times/positions never become valid history points.
            if board.quality in {"valid", "outside_period", "clock_unknown"} and board.fetched_ms:
                stored = self.history.record(event, board)
                if stored == "conflict":
                    board = replace(board, scores=(), quality="conflict", status="同一源时刻分数冲突，暂无可信当前值")
                elif stored in {"storage_error", "busy", "low_disk", "disk_unavailable"}:
                    board = replace(board, notes=board.notes + ("历史保存暂停或失败；当前分数仍可查询。",))
            else:
                self.history.failure(event.server, event.event_id, song.challenge_id, board.quality)
        return board

    def _open_snapshot(self, event, song, entry):
        fetched = _ms(entry.headers.get("x-moenotes-fetched-at"))
        received = int(entry.received * 1000)
        server = None
        try:
            reference = parsedate_to_datetime(entry.headers.get("date", ""))
            if reference.tzinfo is None:
                raise ValueError("unknown reference timezone")
            date = int(reference.timestamp() * 1000)
            if abs(date - received) <= 60000:
                server = date
        except (ValueError, TypeError, OverflowError):
            pass
        elapsed = max(0, self.monotonic() - entry.tick)
        age = max(0, (server - fetched) / 1000) + elapsed if fetched and server and fetched <= server + 1000 else None
        scores = tuple(n if type(n) is int and n >= 0 else None for n in entry.payload.get("scores", []))
        quality = "valid"
        if not fetched:
            quality = "unknown_time"
        elif age is None:
            quality = "clock_unknown"
        elif (song.effective_start_ms and fetched < song.effective_start_ms) or (song.effective_end_ms and fetched > song.effective_end_ms):
            quality = "outside_period"
        observed = scores
        status = "正式挑战榜 · 上游获取快照"
        if age is None:
            scores, status = (), "正式源时间或可信参考时钟未知，暂无当前分数"
            if fetched and server and fetched > server + 1000:
                status = f"正式源时间超前参考时钟约{(fetched - server) / 1000:.0f}秒，暂无可信当前分数"
        elif age > 600:
            scores, status = (), "正式快照过期，暂无当前分数"
        elif quality == "outside_period" and event.status not in {"result", "end"}:
            scores, status = (), "快照不在挑战期内，暂无可信当前值"
        elif event.status in {"result", "end"}:
            status = "最后获取，非确认终榜或 tracker 最后观测"
        elif age > 120:
            status = "正式数据陈旧"
        if entry.fallback and scores:
            status = "正式来源故障，暂用同源旧快照"
            quality = "fallback"
        ids = tuple(_player_id(v) for v in entry.payload.get("player_ids", []))
        names = tuple(_player_name(v) for v in entry.payload.get("player_names", []))
        return BoardSnapshot(song, scores, fetched, server, received, age, status,
                             ("来源：Moenotes开放平台；时间口径：原成功上游获取，不等同tracker观测；名次按响应位置。",),
                             observed, quality, ids, names, "open", TIME_KIND, CONTRACT)
