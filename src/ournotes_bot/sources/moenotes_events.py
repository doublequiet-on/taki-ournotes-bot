# L3
# Input: server, public MoeNotes endpoints, an isolated cache directory and injectable transport/clocks.
# Output: immutable event/song/board snapshots; scores preserve response positions.
# Pos: Data / MoeNotes challenge rankings; see L2-2.md.
# Effects: bounded anonymous HTTPS GETs and atomic writes in moenotes-cutoff-v1 only; no startup I/O or QQ.
"""On-demand challenge rankings. Event points and permanent song records are never read."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlsplit

API = "https://api.bdon.moe/api/v1"
META = "https://metadata.bdon.moe"
ASSETS = "https://assets.bdon.moe"
SERVERS = {"jp": ("日服", 9, "jp", "ja"), "tw": ("台服", 8, "hk-tw-mo", "zh-Hant"),
           "kr": ("韩服", 9, "kr", "ko"), "en": ("国际服", 0, "en", "en")}
_NETWORK_SLOTS = threading.BoundedSemaphore(3)
_TABLES = ("MasterEvent", "MasterChallengeMusic", "MasterLiveMusic", "MasterText", "MasterStoryChapter")


class SourceError(Exception):
    def __init__(self, code: str, retry_after: float = 30):
        super().__init__(code)
        self.code, self.retry_after = code, max(1, retry_after)


def _retry_after(value: str, now: float) -> float:
    try:
        seconds = float(value)
        return seconds if math.isfinite(seconds) else 30
    except ValueError:
        try:
            return max(1, parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 30


def public_get(url: str, timeout: float) -> tuple[bytes, dict[str, str]]:
    """No credentials, redirects or retries. Total timeout includes the body."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc not in {"api.bdon.moe", "metadata.bdon.moe", "assets.bdon.moe"}:
        raise SourceError("invalid_url")
    async def get():
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),
                                         trust_env=False) as session:
            async with session.get(url, allow_redirects=False) as response:
                headers = {k.lower(): v for k, v in response.headers.items()}
                if response.status != 200:
                    code = "not_found" if response.status == 404 else "rate_limited" if response.status == 429 else "upstream"
                    if response.status not in {404, 429}:
                        try:
                            error = json.loads(await response.content.read(65536)).get("error", {})
                            if isinstance(error, dict) and error.get("kind") == "pending":
                                code = "pending"
                        except (ValueError, AttributeError, UnicodeError):
                            pass
                    raise SourceError(code, _retry_after(headers.get("retry-after", "30"), time.time()))
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 8_000_000:
                        raise SourceError("too_large")
                    chunks.append(chunk)
                return b"".join(chunks), headers
    try:
        return asyncio.run(get())
    except SourceError:
        raise
    except Exception as exc:
        raise SourceError("network") from exc


def _id(value) -> str:
    result = str(value)
    if not re.fullmatch(r"[0-9]{1,20}", result):
        raise SourceError("invalid_identity")
    return result


def _ms(value) -> int | None:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if type(value) is int and 0 < value < 253402300799000 else None


def display_time(value: int | None, server: str, *, date: bool = True) -> str:
    if value is None:
        return "未知"
    zone = timezone(timedelta(hours=SERVERS[server][1]))
    return datetime.fromtimestamp(value / 1000, zone).strftime("%Y-%m-%d %H:%M:%S" if date else "%H:%M:%S")


def zone_label(server: str) -> str:
    offset = SERVERS[server][1]
    return f"UTC+{offset:02d}:00" if offset else "UTC"


@dataclass(frozen=True)
class EventSong:
    challenge_id: str
    music_id: str
    title: str
    names: tuple[str, ...] = ()
    jacket: str = ""
    enabled: bool = True
    collect_status: str = "unknown"
    position_source: str = ""


@dataclass(frozen=True)
class EventSnapshot:
    server: str
    event_id: str
    title: str
    status: str
    start_ms: int | None
    end_ms: int | None
    songs: tuple[EventSong, ...]
    banner: str = ""
    metadata_version: str = "unknown"
    asset_version: str = "unknown"
    catalog: tuple[tuple[str, tuple[str, ...]], ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoardSnapshot:
    song: EventSong
    scores: tuple[int | None, ...] = ()
    fetched_ms: int | None = None
    server_ms: int | None = None
    received_ms: int | None = None
    age_seconds: float | None = None
    status: str = "暂无数据"
    notes: tuple[str, ...] = ()

    def score(self, rank: int) -> int | None:
        return self.scores[rank - 1] if 1 <= rank <= len(self.scores) else None


@dataclass(frozen=True)
class _Entry:
    payload: dict
    headers: dict[str, str]
    received: float
    tick: float
    fallback: bool = False


def asset_url(server: str, kind: str, name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", name):
        return ""
    path = "Story/Banner/Chapter" if kind == "banner" else "Image/Jacket"
    return f"{ASSETS}/{server}/{SERVERS[server][3]}/{path}/{name}/{name}.webp"


class EventCutoffRepository:
    """Lazy, isolated, per-key coalesced cache. Never changes the main data schema."""
    def __init__(self, cache_dir: Path, *, transport: Callable = public_get,
                 clock: Callable = time.time, monotonic: Callable = time.monotonic):
        self.cache_dir = cache_dir
        self.transport, self.clock, self.monotonic = transport, clock, monotonic
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._entries: dict[str, _Entry] = {}
        self._negative: dict[str, tuple[float, SourceError]] = {}

    def deadline(self) -> float:
        return self.monotonic() + 12

    def _lock(self, key: str):
        with self._guard:
            return self._locks.setdefault(key, threading.Lock())

    def _path(self, key: str) -> Path:
        return self.cache_dir / (hashlib.sha256(key.encode()).hexdigest() + ".json")

    def _load(self, key: str) -> _Entry | None:
        if key in self._entries:
            return self._entries[key]
        try:
            path = self._path(key)
            if path.stat().st_size > 16_000_000:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            age = self.clock() - data["received"]
            if (data["schema"] != 1 or data["key"] != key or age < 0 or not math.isfinite(age)
                    or not isinstance(data["payload"], dict) or not isinstance(data["headers"], dict)
                    or any(not isinstance(v, str) for v in data["headers"].values())
                    or not self._valid_payload(key, data["payload"])):
                return None
            entry = _Entry(data["payload"], data["headers"], data["received"], self.monotonic() - age)
            self._entries[key] = entry
            return entry
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @staticmethod
    def _valid_payload(key: str, payload: dict) -> bool:
        if key.startswith("current:"):
            try:
                _id(payload.get("eventId"))
                rows = payload["challengeRankings"]
                return isinstance(rows, list) and all(isinstance(row, dict) and
                    _id(row.get("challengeMusicId")) and _id(row.get("musicId")) for row in rows)
            except (KeyError, SourceError):
                return False
        if key.startswith("board:"):
            return isinstance(payload.get("scores"), list) and len(payload["scores"]) <= 100
        if key.startswith("table:"):
            return isinstance(payload.get("rows"), list) and all(isinstance(row, dict) for row in payload["rows"])
        if key.startswith("metadata:"):
            return isinstance(payload.get("version"), str) and all(isinstance(payload.get(name), list) for name in _TABLES)
        return False

    def _save(self, key: str, entry: _Entry) -> None:
        temp = self._path(key).with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps({"schema": 1, "key": key, "received": entry.received,
                                        "payload": entry.payload, "headers": entry.headers},
                                       ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self._path(key))
        except OSError:
            pass  # Successful data remains usable in memory when disk is read-only/full.
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass

    def _cached(self, key: str, ttl: float, max_age: float, loader: Callable,
                deadline: float) -> _Entry:
        lock = self._lock(key)
        if not lock.acquire(timeout=max(0, deadline - self.monotonic())):
            raise SourceError("budget")
        try:
            old = self._load(key)
            age = self.monotonic() - old.tick if old else float("inf")
            if old and age < ttl:
                return old
            negative = self._negative.get(key)
            try:
                if negative and negative[0] > self.monotonic():
                    raise negative[1]
                if self.monotonic() >= deadline:
                    raise SourceError("budget")
                payload, headers = loader()
                entry = _Entry(payload, headers, self.clock(), self.monotonic())
                self._entries[key] = entry
                self._negative.pop(key, None)
                self._save(key, entry)
                return entry
            except SourceError as exc:
                if not negative or negative[0] <= self.monotonic():
                    self._negative[key] = (self.monotonic() + (60 if exc.code == "not_found" else exc.retry_after), exc)
                # A confirmed absence must not resurrect an old event/board.
                if old and age <= max_age and exc.code != "not_found":
                    return replace(old, fallback=True)
                raise
        finally:
            lock.release()

    def _get(self, url: str, deadline: float) -> tuple[bytes, dict]:
        if not _NETWORK_SLOTS.acquire(timeout=max(0, deadline - self.monotonic())):
            raise SourceError("budget")
        try:
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise SourceError("budget")
            raw, headers = self.transport(url, min(4, remaining))
            return raw, {k.lower(): str(v) for k, v in headers.items()}
        finally:
            _NETWORK_SLOTS.release()

    def _json(self, url: str, deadline: float) -> tuple[dict, dict]:
        raw, headers = self._get(url, deadline)
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("object expected")
            if "error" in data:
                raise SourceError("pending" if "pending" in str(data["error"]).lower() else "upstream")
            return data, headers
        except (ValueError, UnicodeError) as exc:
            raise SourceError("invalid_json") from exc

    def _metadata(self, server: str, deadline: float) -> _Entry:
        def load():
            index, _ = self._json(META + "/index.json", deadline)
            try:
                region = index["regions"][SERVERS[server][2]]
                version = region["entry"]["version"]
                hashes = region["files"]
                if not isinstance(version, str) or len(version) > 160:
                    raise ValueError()
                def table(name):
                    filename = name + ".json"
                    key = f"table:{server}:{version}:{filename}:{hashes[filename]}"
                    def download():
                        raw, headers = self._get(f"{META}/{server}/master/{filename}?v={quote(version, safe='')}", deadline)
                        if hashlib.sha256(raw).hexdigest() != hashes[filename]:
                            raise SourceError("metadata_changed")
                        data = json.loads(raw)
                        if not isinstance(data, dict) or not isinstance(data.get("_allData"), list) or any(not isinstance(row, dict) for row in data["_allData"]):
                            raise ValueError()
                        return {"rows": [{k.lstrip("_"): v for k, v in row.items()} for row in data["_allData"]]}, headers
                    return name, self._cached(key, 86400 * 365, 0, download, deadline).payload["rows"]
                with ThreadPoolExecutor(max_workers=3) as pool:
                    tables = dict(pool.map(table, _TABLES))
                tables["version"] = version
                # Asset versions are independent of Master versions; absence only loses artwork.
                try:
                    assets, _ = self._json(ASSETS + "/versions/current_version.json", deadline)
                    release = assets["regions"][server]
                    locale = release["locales"][SERVERS[server][3]]
                    tables["asset_version"] = str(release["resource_version"]) + ":" + str(locale["snapshot"])
                except (SourceError, KeyError, TypeError):
                    tables["asset_version"] = "unknown"
                return tables, {}
            except (KeyError, TypeError, ValueError, UnicodeError) as exc:
                raise SourceError("invalid_metadata") from exc
        return self._cached(f"metadata:{server}", 3600, 86400, load, deadline)

    def event(self, server: str, deadline: float) -> EventSnapshot:
        if server not in SERVERS:
            raise SourceError("invalid_server")
        key = f"current:{server}"
        old = self._load(key)
        ttl = 300
        if old:
            boundaries = [_ms(old.payload.get(k)) for k in ("startAt", "endAt")]
            if any(t and abs(t / 1000 - self.clock()) < 600 for t in boundaries):
                ttl = 30
        def current():
            body, headers = self._json(f"{API}/{server}/events/current", deadline)
            _id(body.get("eventId"))
            if not self._valid_payload(key, body):
                raise SourceError("invalid_event")
            return body, headers
        entry = self._cached(key, ttl, 600, current, deadline)
        data = entry.payload
        event_id = _id(data.get("eventId"))
        songs = []
        for row in data["challengeRankings"]:
            if not isinstance(row, dict):
                raise SourceError("invalid_event")
            challenge, music = _id(row.get("challengeMusicId")), _id(row.get("musicId"))
            songs.append(EventSong(challenge, music, f"歌曲 {music}", enabled=row.get("rankingEnabled") is True,
                                   collect_status=str(row.get("collectStatus", "unknown")),
                                   position_source=str(row.get("positionSource", ""))))
        if len({s.challenge_id for s in songs}) != len(songs):
            raise SourceError("invalid_event")
        notes = ["当前活动接口暂不可用，使用短期旧快照"] if entry.fallback else []
        if data.get("stale") is True:
            notes.append("来源将活动快照标记为陈旧")
        event = EventSnapshot(server, event_id, f"活动 {event_id}", str(data.get("eventStatus", "unknown")),
                              _ms(data.get("startAt")), _ms(data.get("endAt")), tuple(songs), notes=tuple(notes))
        try:
            metadata = self._metadata(server, deadline)
            event = self._enrich(event, metadata.payload)
            if metadata.fallback:
                event = replace(event, notes=event.notes + ("元数据暂用旧版本",))
        except (SourceError, ValueError, TypeError, KeyError):
            event = replace(event, notes=event.notes + ("元数据暂不可用；名称、素材或时间校验不完整",))
        return event

    @staticmethod
    def _enrich(event: EventSnapshot, data: dict) -> EventSnapshot:
        texts = {str(row["id"]): row for row in data["MasterText"]}
        preferred = {"jp": "japanese", "tw": "traditionalChinese", "kr": "korean", "en": "english"}[event.server]
        def names(key):
            row = texts.get(str(key), {})
            values = [row.get(k) for k in (preferred, "japanese", "simplifiedChinese", "traditionalChinese", "korean", "english")]
            return tuple(dict.fromkeys(v for v in values if isinstance(v, str) and v.strip()
                                       and not re.match(r"(?:Music_|Event_|Story_)", v)))
        music = {str(row["id"]): row for row in data["MasterLiveMusic"]}
        master = next((row for row in data["MasterEvent"] if str(row["id"]) == event.event_id), {})
        chapter = next((row for row in data["MasterStoryChapter"] if row["id"] == master.get("storyChapterId")), {})
        identities = {str(row["id"]): str(row["liveMusicId"]) for row in data["MasterChallengeMusic"]
                      if str(row["eventId"]) == event.event_id}
        notes = list(event.notes)
        if identities != {s.challenge_id: s.music_id for s in event.songs}:
            notes.append("挑战歌曲与元数据尚未同步；身份以本次活动接口为准")
        songs = []
        for song in event.songs:
            row = music.get(song.music_id, {})
            titles = names(row.get("titleTextID"))
            songs.append(replace(song, title=titles[0] if titles else song.title, names=titles,
                                 jacket=asset_url(event.server, "jacket", str(row.get("jacketAssetName", "")))))
        times = [event.start_ms, event.end_ms]
        for i, field in enumerate(("startAt", "endAt")):
            raw = master.get(field)
            if raw:
                try:
                    # Source Master offsets are +9 for JP and +8 for TW/KR/EN, unlike display zones.
                    zone = timezone(timedelta(hours=9 if event.server == "jp" else 8))
                    stamp = int(datetime.strptime(raw, "%Y/%m/%d %H:%M:%S").replace(tzinfo=zone).timestamp() * 1000)
                    if times[i] != stamp:
                        notes.append(f"{'开始' if i == 0 else '结束'}时间待核实（API {display_time(times[i], event.server)}；Master {raw}）")
                        times[i] = None
                except (ValueError, TypeError):
                    notes.append("Master 活动时间格式未知")
        title = names(master.get("nameTextId"))
        return replace(event, title=title[0] if title else event.title, songs=tuple(songs), start_ms=times[0], end_ms=times[1],
                       banner=asset_url(event.server, "banner", str(chapter.get("banner", ""))),
                       metadata_version=data["version"], asset_version=data.get("asset_version", "unknown"),
                       catalog=tuple((key, names(row.get("titleTextID"))) for key, row in music.items()), notes=tuple(notes))

    def board(self, event: EventSnapshot, song: EventSong, deadline: float) -> BoardSnapshot:
        if not song.enabled or song.collect_status == "disabled":
            return BoardSnapshot(song, status="本曲挑战榜未开放")
        if song.collect_status not in {"collecting", "finalizing", "archived"}:
            return BoardSnapshot(song, status={"pending": "等待来源采集", "missed": "来源未采集到本曲"}.get(song.collect_status, "本曲采集状态未知"))
        def load():
            body, headers = self._json(f"{API}/{event.server}/events/{event.event_id}/challenges/{song.challenge_id}/ranking", deadline)
            players = body.get("players")
            if not isinstance(players, list):
                raise SourceError("invalid_board")
            # Persist only scores, including null holes. Never persist player identities/decks.
            scores = [p.get("score") if isinstance(p, dict) else None for p in players[:100]]
            return {"scores": [n if type(n) is int and n >= 0 else None for n in scores]}, headers
        try:
            entry = self._cached(f"board:{event.server}:{event.event_id}:{song.challenge_id}", 60, 600, load, deadline)
        except SourceError as exc:
            messages = {"pending": "来源采集中", "rate_limited": "来源限流，稍后重试", "not_found": "本曲暂无榜单", "budget": "本次查询预算已用尽"}
            return BoardSnapshot(song, status=messages.get(exc.code, "本曲来源暂不可用"))
        headers = entry.headers
        fetched, server = _ms(headers.get("x-fetched-at")), _ms(headers.get("x-server-time"))
        elapsed = max(0, self.monotonic() - entry.tick)
        anchor = server if server else int(entry.received * 1000)
        age = max(0, (anchor - fetched) / 1000) + elapsed if fetched and anchor >= fetched else None
        notes = []
        if server and abs(server / 1000 - entry.received) > 60:
            notes.append("来源时钟与本机有偏差")
        position = headers.get("x-position-source", song.position_source)
        scores = tuple(n if type(n) is int and n >= 0 else None for n in entry.payload.get("scores", []))
        status = "源榜单快照"
        if headers.get("x-collect-status") == "disabled":
            scores, status = (), "本曲挑战榜未开放"
        elif position != "responseOrder":
            scores, status = (), "来源名次依据未知"
        elif (headers.get("x-final-quality", "").lower() == "lastseen"
              or event.status in {"result", "end"} or song.collect_status == "archived"):
            status = "最后观测，非确认终榜"
        elif age is None:
            status = "源采集时间未知" if fetched is None else "新鲜度未知（源时钟异常）"
            if entry.fallback:
                scores = ()
        elif age > 600:
            scores, status = (), "快照过期，暂无当前分数"
        elif age > 120 or headers.get("x-stale", "").lower() in {"true", "1"}:
            status = "数据陈旧"
        elif song.collect_status == "finalizing":
            status = "结算采集中"
        if entry.fallback:
            if age is None or age > 600:
                scores, status = (), "旧缓存超限，暂无分数"
            else:
                status = "来源故障，暂用旧快照"
        return BoardSnapshot(song, scores, fetched, server, int(entry.received * 1000), age, status, tuple(notes))

    def boards(self, event: EventSnapshot, songs: tuple[EventSong, ...], deadline: float) -> tuple[BoardSnapshot, ...]:
        with ThreadPoolExecutor(max_workers=3) as pool:
            return tuple(pool.map(lambda song: self.board(event, song, deadline), songs))
