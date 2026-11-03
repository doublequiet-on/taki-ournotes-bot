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
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from .transport import public_response, response_failure
from .identity import matches_game_region
from ..json_validation import decode_json as strict_json
from ..moenotes_events import (
    BoardSnapshot, EventCutoffRepository, EventSnapshot, EventSong, SERVERS,
    SourceError, _Entry, _ms, _player_id, _player_name,
)

API = "https://haneoka.org/api/v1/game/records"
CONTRACT = "haneoka:challenge-ranking/1"
NORMALIZATION = "haneoka:challenge-ranking/2"
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
    return (event.server, event.event_id, event.start_ms, event.end_ms, event.provider,
            tuple((s.challenge_id, s.music_id, s.enabled, s.collect_status,
                   s.effective_start_ms, s.effective_end_ms) for s in event.songs))


def decode_current(data, region):
    if not matches_game_region(data.get("region"), region) or type(data.get("stale")) is not bool:
        raise SourceError("invalid_event")
    timestamp(data.get("fetchedAtMs"))
    row = data.get("event")
    if 'event' not in data:
        raise SourceError('invalid_event')
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
    total = row.get('totalRankingEnabled')
    if total is not None and type(total) is not bool:
        raise SourceError('invalid_event')
    return EventSnapshot(region, ident, f"活动 {ident}", status, start, end, tuple(songs),
                         source="haneoka", challenge_order_verified=False, metadata_version="",
                         total_ranking_enabled=total, provider=provider(data))


def provider(data):
    value = data.get('provider')
    if value is not None and (not isinstance(value, str) or not value or len(value) > 64):
        raise SourceError('invalid_provider')
    return value


def player_detail(row):
    """Optional public roster fields; never expose opaque IDs or arbitrary URLs."""
    def value(v):
        return v if type(v) is int and 0 <= v <= MAX_SCORE else None
    fields = ('rankExp', 'favoriteMemberCardId', 'totalPower')
    detail = {k: value(row.get(k)) for k in fields}
    if detail['favoriteMemberCardId']==0:detail['favoriteMemberCardId']=None
    for key in ('favoriteMemberExp', 'favoriteMemberAwakeCount', 'favoriteMemberRank'):
        if key in row:
            number = value(row[key])
            if key != 'favoriteMemberExp' and number is not None and number > 5:
                number = None
            detail[key] = number
    cards = row.get('cards')
    detail['cards'] = []
    if isinstance(cards, list) and len(cards) <= 5:
        seen = set()
        for card in cards:
            if (not isinstance(card, dict) or type(card.get('slot')) is not int
                    or not 0 <= card['slot'] < 5 or card['slot'] in seen):
                detail['cards'] = []
                break
            seen.add(card['slot'])
            detail['cards'].append({k: value(card.get(k)) for k in
                ('slot', 'memberCardId', 'memberExp', 'memberAwakeCount', 'memberRank',
                 'supportCardId', 'supportExp', 'supportRank')})
        for card in detail['cards']:
            for key in ('memberAwakeCount','memberRank','supportRank'):
                if card[key] is not None and card[key]>5:card[key]=None
            for key in ('memberCardId','supportCardId'):
                if card[key]==0:card[key]=None
        detail['cards'].sort(key=lambda c: c['slot'])
    return detail


def decode_board(data, event, song):
    if (not matches_game_region(data.get("region"), event.server) or data.get("eventId") != event.event_id
            or data.get("challengeId") != song.challenge_id or type(data.get("stale")) is not bool):
        raise SourceError("invalid_identity")
    fetched, server = timestamp(data.get("fetchedAtMs")), timestamp(data.get("serverTimeMs"))
    rows = data.get("rows")
    if not isinstance(rows, list) or len(rows) > 100:
        raise SourceError("invalid_board")
    scores, ids, names, details, ranks, states = [], [], [], [], [], []
    source = provider(data)
    sid = data.get('snapshotId')
    if sid is not None and (not isinstance(sid, str) or len(sid) > 1024):
        raise SourceError('invalid_identity')
    previous = 0
    unsafe = False
    for position, row in enumerate(rows, 1):
        # Null/non-object rows occupy their original slot. Never compact or sort.
        if row is not None and not isinstance(row, dict):
            raise SourceError('invalid_board')
        row = row or {}
        rank = row.get('rank')
        if rank is not None:
            if type(rank) is not int or not previous <= rank <= position or rank < 1:
                raise SourceError('invalid_rank')
            previous = rank
        ranks.append(rank)
        score = row.get("score")
        valid = type(score) is int and 0 <= score <= MAX_SCORE
        unsafe |= score is not None and not valid
        scores.append(score if valid else None)
        states.append('source_zero' if valid and score == 0 else 'value' if valid else
                      'null' if 'score' in row and score is None else 'invalid' if 'score' in row else 'missing')
        public_id = row.get("profileId")  # Never substitute opaque playerId.
        ids.append(_player_id(public_id) if valid and isinstance(public_id, str) else None)
        names.append(_player_name(row.get("name")) if valid else None)
        details.append(player_detail(row) if valid else None)
    return {"scores": scores, "player_ids": ids, "player_names": names, "player_details": details,
            "fetched": fetched, "server": server, "stale": data["stale"], "unsafe": unsafe,
            "display_ranks": ranks, "value_states": states, "provider": source,
            "snapshot_id": data.get('snapshotId'), "observed_ms": timestamp(data.get('observedAtMs')),
            "normalization_version": NORMALIZATION}


class HaneokaEventCutoffRepository(EventCutoffRepository):
    source = "haneoka"

    def __init__(self, cache_dir, *, transport=None, metadata=None, **kwargs):
        super().__init__(cache_dir, transport=transport or self._transport, **kwargs)
        self._event_identities = {}
        self.metadata = metadata

    @staticmethod
    def _transport(url, timeout):
        try:
            status, body, headers = public_response(url, MAX_BYTES, timeout, error_body=True)
            if status != 200:
                code, retry = response_failure(status, body, headers)
                raise SourceError(code, retry if retry is not None else 30)
            return body, headers
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
                raise SourceError(kind if isinstance(kind, str) and kind in {"not_found", "pending", "rate_limited"} else "upstream")
            return data, headers
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise SourceError("invalid_json") from exc

    def _valid_payload(self, key, payload):
        try:
            if key.startswith("current:"):
                decode_current(payload, key.split(":")[1])
                return True
            if key.startswith("board:"):
                return (super()._valid_payload(key, payload)
                        and self._valid_quality(payload)
                        and all(n is None or type(n) is int and 0 <= n <= MAX_SCORE for n in payload["scores"])
                        and ("player_details" not in payload or isinstance(payload["player_details"],list)
                             and len(payload["player_details"])==len(payload["scores"])
                             and all(v is None or isinstance(v,dict) and player_detail(v)==v for v in payload["player_details"]))
                        and type(payload.get("stale")) is bool and type(payload.get("unsafe")) is bool
                        and timestamp(payload.get("fetched")) == payload.get("fetched")
                        and timestamp(payload.get("server")) == payload.get("server"))
        except (SourceError, KeyError, TypeError):
            pass
        return False

    @staticmethod
    def _valid_quality(payload):
        provider(payload)
        timestamp(payload.get('observed_ms'))
        sid = payload.get('snapshot_id')
        if sid is not None and (not isinstance(sid, str) or len(sid) > 1024):
            return False
        if payload.get('normalization_version', 'legacy/1') not in {'legacy/1', NORMALIZATION}:
            return False
        ranks = payload.get('display_ranks')
        states = payload.get('value_states')
        length = len(payload.get('scores', []))
        if ranks is not None:
            if not isinstance(ranks, list) or len(ranks) != length:
                return False
            previous = 0
            for position, rank in enumerate(ranks, 1):
                if rank is not None:
                    if type(rank) is not int or not max(1, previous) <= rank <= position:
                        return False
                    previous = rank
        return (states is None or isinstance(states, list) and len(states) == length
                and all(s in {'value', 'source_zero', 'missing', 'null', 'invalid', 'legacy_unknown'} for s in states))

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
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
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
        notes = ["挑战原序号尚未核实；请使用歌曲ID，歌曲N暂不可用。"]
        if entry.fallback or entry.payload["stale"]:
            notes.append("当前活动使用同源旧快照，活动状态可能变化。")
        with self._guard:
            self._event_identities[server] = event_identity(event)
        event = replace(event, notes=tuple(notes))
        if self.metadata is not None:
            event = self.metadata.enrich(event, deadline)
        if not event.metadata_version:
            event = replace(event, notes=event.notes + ("静态活动身份尚未匹配，名称与素材暂不可用。",))
        return event

    def board(self, event, song, deadline):
        empty = BoardSnapshot(song, source=self.source, time_kind=TIME_KIND, contract_version=CONTRACT, game_region=event.server)
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
        # Old readers validate exact player-detail keys. Keep their cache intact.
        key = f"board:{event.server}:{event.event_id}:{song.challenge_id}:{digest}:quality-v2"
        # Read pre-C4 cache without rewriting it or inventing lost provider/ranks.
        if event.provider is None and self._load(key) is None:
            old_identity = expected[:4] + expected[5:]
            old_digest = hashlib.sha256(json.dumps(old_identity).encode()).hexdigest()
            old = self._load(f'board:{event.server}:{event.event_id}:{song.challenge_id}:{old_digest}:avatar-v2')
            if old is not None:
                self._entries[key] = old
        def load():
            data, headers = self._json(f"{API}/{event.server}/events/{event.event_id}/challenges/{song.challenge_id}/ranking", deadline)
            payload = decode_board(data, event, song)
            try:
                date = parsedate_to_datetime(headers.get("date", ""))
                if date.tzinfo is not None:
                    payload["http_date_ms"] = int(date.timestamp() * 1000)
            except (ValueError, TypeError, OverflowError):
                pass  # Missing or malformed Date cannot authorize clock calibration.
            if not matches():
                raise SourceError("identity_changed")
            return payload, self._time_headers(payload)
        try:
            entry = self._cached(key, 60, 600, load, deadline)
            if not matches():
                raise SourceError("identity_changed")
        except SourceError as exc:
            if self.history and event.provider is None:
                self.history.failure(event.server, event.event_id, song.challenge_id, exc.code)
            labels = {'pending': '榜单尚未就绪，请稍后重试', 'rate_limited': '来源限流，请稍后重试',
                      'not_found': '来源已确认榜单不存在'}
            return replace(empty, status=labels.get(exc.code, "Haneoka 榜线暂不可用，请稍后重试"), error_code=exc.code)
        board = self._snapshot(event, song, entry)
        if self.history and board.provider is not None and not getattr(self.history, 'supports_provider_history', False):
            self._note_retrieval('history_result', 'provider_schema_pending')
            board = replace(board, notes=board.notes + ('当前观测未写入旧历史库：等待来源隔离合同迁移。',))
        elif self.history:
            if entry.late:
                self.history.record(event, self._snapshot(event, song, entry.late))
            if entry.fallback and board.provider is None:
                self.history.failure(event.server, event.event_id, song.challenge_id, "source_fallback")
            stored = self.history.record(event, board)
            self._note_retrieval('history_result',stored)
            if stored == "conflict":
                board = replace(board, scores=(), quality="conflict", status="同一源时刻分数冲突，暂无可信当前值")
            elif stored == "clock_skew":
                board = replace(board, notes=board.notes + ("历史采集告警：来源时钟异常，本次原始观测已隔离保留，待核验后才能用于曲线。",))
            elif stored in {"storage_error", "busy", "low_disk", "disk_unavailable"}:
                board = replace(board, notes=board.notes + ("历史保存暂停或失败；当前分数仍可查询。",))
        return board

    def _snapshot(self, event, song, entry):
        board = super()._snapshot(event, song, entry)
        fetched, server = board.fetched_ms, board.server_ms
        date = entry.payload.get("http_date_ms")
        date = date if type(date) is int and board.received_ms and abs(date - board.received_ms) <= 5000 else None
        period_time = fetched + date - server if fetched and server and date and abs(server - board.received_ms) > 60000 else fetched
        # An absolute clock offset does not invalidate source-relative freshness.
        # Keep raw timestamps; history calibrates only with a verified HTTP Date.
        notes = [note for note in board.notes if note != "来源时钟与本机有偏差"]
        notes.append('Tn按响应位置取值；Top100最多100行，边界并列可能未收齐。')
        notes.append('实际提供者：' + (entry.payload.get('provider') or '旧数据未声明'))
        time_kind = TIME_KIND
        freshness = 'stale' if entry.payload['stale'] else board.freshness
        if entry.payload.get('provider') == 'official':
            time_kind = 'official_observed_via_haneoka'
        elif entry.payload.get('provider') == 'moenotes':
            time_kind = 'bridge_fetched_via_haneoka'
            notes.append('MoeNotes桥接仅提供获取时间，原始观测时间未知。')
            if freshness == 'fresh':
                freshness = 'unknown'
        quality = board.quality
        scores, status = board.scores, board.status
        if not fetched or not server:
            scores, status, quality = (), "来源时间或参考时钟未知，暂无可信当前值", "clock_unknown"
        elif fetched > server + 1000:
            scores, status, quality = (), "来源时钟异常，暂无可信当前值", "clock_unknown"
        elif ((song.effective_start_ms and period_time < song.effective_start_ms)
              or (song.effective_end_ms and period_time > song.effective_end_ms
                  and song.collect_status not in {"finalizing", "archived"})):
            scores, status, quality = (), "快照不在挑战期内，暂无可信当前值", "outside_period"
        elif board.age_seconds is not None and board.age_seconds > 600 and song.collect_status != "archived":
            scores, status, quality = (), "快照过期，暂无当前分数", "stale"
        if entry.payload["unsafe"]:
            notes.append("部分分数格式或安全整数精度未通过校验，保留原位空槽。")
        if entry.payload["stale"]:
            if quality in {'valid', 'fallback'}:
                quality = "stale"
        ranks = tuple(entry.payload.get('display_ranks', [None] * len(board.observed_scores or ())))
        details = entry.payload.get('player_details', [])
        details = tuple(player_detail(d) if isinstance(d, dict) else None for d in details) if (
            isinstance(details, list) and len(details) == len(board.observed_scores or ())) else ()
        return replace(board, scores=scores, status=status, quality=quality, notes=tuple(notes), player_details=details,
                       source=self.source, time_kind=time_kind,
                       contract_version=NORMALIZATION if entry.payload.get('provider') is not None else CONTRACT, http_date_ms=date,
                       display_ranks=ranks, raw_ranks=(None,) * len(ranks),
                       value_states=tuple(entry.payload.get('value_states', ['legacy_unknown'] * len(ranks))),
                       provider=entry.payload.get('provider'), snapshot_id=entry.payload.get('snapshot_id'),
                       normalization_version=entry.payload.get('normalization_version', 'legacy/1'),
                       freshness=freshness, game_region=event.server, observed_ms=entry.payload.get('observed_ms'))
