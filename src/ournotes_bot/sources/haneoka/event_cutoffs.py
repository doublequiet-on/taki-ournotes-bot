# L3
# Input: Explicit four-region Game Records event/challenge identities and bounded DTOs.
# Output: Captured same-row Top 100 observations; unknown static associations remain unknown.
# Pos: Data / Sources Haneoka challenge cutoffs; see docs/HANEOKA_CUTOFFS.md.
# Effects: Anonymous Haneoka-only requests and independent cache; no legacy network fallback.
"""Game Records adapter. Static resource mapping is deliberately not inferred."""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import replace
from urllib.parse import urlsplit

from .chart_data import public_get, _json as strict_json
from ..moenotes_events import (
    BoardSnapshot, EventCutoffRepository, EventSnapshot, EventSong, SERVERS,
    SourceError, _Entry, _ms, _player_id, _player_name,
)

API = "https://haneoka.org/api/v1/game/records"
CONTRACT = "haneoka:challenge-ranking/1"
TIME_KIND = "tracker_observed_via_haneoka"
MAX_BYTES = 524288
MAX_SCORE = 2**53 - 1


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        raise SourceError("invalid_identity")
    return value


def timestamp(value):
    if value is not None and (type(value) is not int or _ms(value) is None):
        raise SourceError("invalid_time")
    return value


def event_identity(event):
    return (event.server, event.event_id, event.start_ms, event.end_ms,
            tuple((s.challenge_id, s.music_id, s.enabled, s.collect_status,
                   s.effective_start_ms, s.effective_end_ms) for s in event.songs))


def decode_current(data, region):
    if data.get("region") != region or type(data.get("stale")) is not bool:
        raise SourceError("invalid_event")
    timestamp(data.get("fetchedAtMs"))
    row = data.get("event")
    if row is None:
        raise SourceError("not_found")
    if not isinstance(row, dict):
        raise SourceError("invalid_event")
    ident = identifier(row.get("id"))
    start, end = timestamp(row.get("startAtMs")), timestamp(row.get("endAtMs"))
    if start and end and start >= end:
        raise SourceError("invalid_event")
    challenges = row.get("challenges")
    if not isinstance(challenges, list) or len(challenges) > 16:
        raise SourceError("invalid_event")
    songs = []
    for challenge in challenges:
        if not isinstance(challenge, dict) or type(challenge.get("enabled")) is not bool:
            raise SourceError("invalid_event")
        cid, mid = identifier(challenge.get("id")), identifier(challenge.get("musicId"))
        a, b = timestamp(challenge.get("startAtMs")), timestamp(challenge.get("endAtMs"))
        status = challenge.get("status")
        if not isinstance(status, str) or len(status) > 40 or a and b and a >= b:
            raise SourceError("invalid_event")
        songs.append(EventSong(cid, mid, f"歌曲 ID {mid}", enabled=challenge["enabled"],
                               collect_status=status, position_source="responseOrder",
                               effective_start_ms=a, effective_end_ms=b))
    if len({s.challenge_id for s in songs}) != len(songs):
        raise SourceError("invalid_event")
    status = row.get("status")
    if not isinstance(status, str) or len(status) > 40:
        raise SourceError("invalid_event")
    return EventSnapshot(region, ident, f"活动 {ident}", status, start, end, tuple(songs),
                         source="haneoka", challenge_order_verified=False)


def decode_board(data, event, song):
    if (data.get("region") != event.server or data.get("eventId") != event.event_id
            or data.get("challengeId") != song.challenge_id or type(data.get("stale")) is not bool):
        raise SourceError("invalid_identity")
    fetched, server = timestamp(data.get("fetchedAtMs")), timestamp(data.get("serverTimeMs"))
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) > 100:
        raise SourceError("invalid_board")
    scores, ids, names = [], [], []
    unsafe = False
    for position, row in enumerate(rows, 1):
        # Null/non-object rows occupy their original slot. Never compact or sort.
        row = row if isinstance(row, dict) else {}
        if row and (type(row.get("rank")) is not int or row["rank"] != position):
            raise SourceError("unknown_position")
        score = row.get("score")
        valid = type(score) is int and 0 <= score <= MAX_SCORE
        unsafe |= score is not None and not valid
        scores.append(score if valid else None)
        public_id = row.get("profileId")  # Never substitute opaque playerId.
        ids.append(_player_id(public_id) if valid and isinstance(public_id, str) else None)
        names.append(_player_name(row.get("name")) if valid else None)
    return {"scores": scores, "player_ids": ids, "player_names": names,
            "fetched": fetched, "server": server, "stale": data["stale"], "unsafe": unsafe}


class HaneokaEventCutoffRepository(EventCutoffRepository):
    source = "haneoka"

    def __init__(self, cache_dir, *, transport=None, **kwargs):
        super().__init__(cache_dir, transport=transport or self._transport, **kwargs)
        self._event_identities = {}

    @staticmethod
    def _transport(url, timeout):
        try:
            return public_get(url, MAX_BYTES, timeout)
        except RuntimeError as exc:
            raise SourceError("upstream") from exc

    def _get(self, url, deadline):
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc != "haneoka.org" or parts.query or parts.fragment
                or not re.fullmatch(r"/api/v1/game/records/(jp|tw|kr|en)/events/(?:current|[1-9][0-9]{0,18}/challenges/[1-9][0-9]{0,18}/ranking)", parts.path)):
            raise SourceError("invalid_url")
        raw, headers = super()._get(url, deadline)
        if len(raw) > MAX_BYTES:
            raise SourceError("oversize")
        return raw, headers

    def _json(self, url, deadline):
        raw, headers = self._get(url, deadline)
        try:
            data = strict_json(raw)
            if not isinstance(data, dict):
                raise ValueError()
            if "error" in data:
                error = data["error"]
                kind = error.get("kind") if isinstance(error, dict) else None
                raise SourceError(kind if kind in {"not_found", "pending", "rate_limited"} else "upstream")
            return data, headers
        except (ValueError, UnicodeError) as exc:
            raise SourceError("invalid_json") from exc

    def _valid_payload(self, key, payload):
        try:
            if key.startswith("current:"):
                decode_current(payload, key.split(":")[1])
                return True
            if key.startswith("board:"):
                return (super()._valid_payload(key, payload)
                        and all(n is None or type(n) is int and 0 <= n <= MAX_SCORE for n in payload["scores"])
                        and type(payload.get("stale")) is bool and type(payload.get("unsafe")) is bool
                        and timestamp(payload.get("fetched")) == payload.get("fetched")
                        and timestamp(payload.get("server")) == payload.get("server"))
        except (SourceError, KeyError, TypeError):
            pass
        return False

    def _load(self, key):
        if key in self._entries:
            return self._entries[key]
        try:
            with self._path(key).open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                return None
            data = strict_json(raw)
            received = data["received"]
            age = self.clock() - received
            if (type(received) not in (int, float) or not math.isfinite(age) or age < 0
                    or data["schema"] != 1 or data["key"] != key
                    or not isinstance(data["payload"], dict) or not self._valid_payload(key, data["payload"])):
                return None
            entry = _Entry(data["payload"], self._time_headers(data["payload"]), received, self.monotonic() - age)
            self._entries[key] = entry
            return entry
        except (OSError, ValueError, TypeError, KeyError):
            return None

    @staticmethod
    def _time_headers(payload):
        return {"x-fetched-at": str(payload.get("fetched") or ""),
                "x-server-time": str(payload.get("server") or ""),
                "x-stale": str(payload.get("stale", False)).lower()}

    def event(self, server, deadline):
        if server not in SERVERS:
            raise SourceError("invalid_server")
        def load():
            body, _ = self._json(f"{API}/{server}/events/current", deadline)
            decode_current(body, server)
            return body, {}
        entry = self._cached(f"current:{server}", 30, 600, load, deadline)
        event = decode_current(entry.payload, server)
        notes = ["挑战原序号尚未核实；请使用歌曲ID，歌曲N暂不可用。",
                 "四服与静态资源的正式关联待核实，暂不显示歌名与素材。"]
        if entry.fallback or entry.payload["stale"]:
            notes.append("当前活动使用同源旧快照，活动状态可能变化。")
        with self._guard:
            self._event_identities[server] = event_identity(event)
        return replace(event, notes=tuple(notes))

    def board(self, event, song, deadline):
        empty = BoardSnapshot(song, source=self.source, time_kind=TIME_KIND, contract_version=CONTRACT)
        expected = event_identity(event)
        def matches():
            with self._guard:
                return (event.source == self.source and song in event.songs
                        and self._event_identities.get(event.server) == expected)
        if not matches():
            return replace(empty, status="活动或挑战关联变化，请重新查询")
        if not song.enabled or song.collect_status not in {"collecting", "finalizing", "archived"}:
            return replace(empty, status="本曲挑战榜未开放或尚未采集")
        digest = hashlib.sha256(json.dumps(expected).encode()).hexdigest()
        key = f"board:{event.server}:{event.event_id}:{song.challenge_id}:{digest}"
        def load():
            data, _ = self._json(f"{API}/{event.server}/events/{event.event_id}/challenges/{song.challenge_id}/ranking", deadline)
            payload = decode_board(data, event, song)
            if not matches():
                raise SourceError("identity_changed")
            return payload, self._time_headers(payload)
        try:
            entry = self._cached(key, 60, 600, load, deadline)
            if not matches():
                raise SourceError("identity_changed")
        except SourceError as exc:
            if self.history:
                self.history.failure(event.server, event.event_id, song.challenge_id, exc.code)
            return replace(empty, status="Haneoka 榜线暂不可用，请稍后重试")
        board = self._snapshot(event, song, entry)
        if self.history:
            if entry.late:
                self.history.record(event, self._snapshot(event, song, entry.late))
            if entry.fallback:
                self.history.failure(event.server, event.event_id, song.challenge_id, "source_fallback")
            stored = self.history.record(event, board)
            if stored == "conflict":
                board = replace(board, scores=(), quality="conflict", status="同一源时刻分数冲突，暂无可信当前值")
            elif stored in {"storage_error", "busy", "low_disk", "disk_unavailable"}:
                board = replace(board, notes=board.notes + ("历史保存暂停或失败；当前分数仍可查询。",))
        return board

    def _snapshot(self, event, song, entry):
        board = super()._snapshot(event, song, entry)
        fetched, server, received = board.fetched_ms, board.server_ms, board.received_ms
        notes = list(board.notes)
        quality = board.quality
        scores, status = board.scores, board.status
        if not fetched or not server or abs(server - received) > 60000 or fetched > server + 1000:
            scores, status, quality = (), "来源时间或参考时钟未知，暂无可信当前值", "clock_unknown"
        elif ((song.effective_start_ms and fetched < song.effective_start_ms)
              or (song.effective_end_ms and fetched > song.effective_end_ms)):
            scores, status, quality = (), "快照不在挑战期内，暂无可信当前值", "outside_period"
        elif board.age_seconds is not None and board.age_seconds > 600 and song.collect_status != "archived":
            scores, status, quality = (), "快照过期，暂无当前分数", "stale"
        if entry.payload["unsafe"]:
            notes.append("部分分数格式或安全整数精度未通过校验，保留原位空槽。")
        if entry.payload["stale"]:
            quality = "stale"
        return replace(board, scores=scores, status=status, quality=quality, notes=tuple(notes),
                       source=self.source, time_kind=TIME_KIND, contract_version=CONTRACT)
