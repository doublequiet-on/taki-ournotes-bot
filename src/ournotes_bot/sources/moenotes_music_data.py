# L3
# Input: 同生态公开 music-data 快照、独立缓存路径及可注入匿名 transport/clock。
# Output: 验证后的 MusicSnapshot；当前事实与最后兼容计算快照独立保留。
# Pos: Data / Sources 的分数表事实与有限计算统计；见 L2-2.md。
# Effects/Dependencies: 匿名 HTTPS、有界解压、原子独立缓存；运行时显式启动300秒刷新，不读取密钥或 QQ。
"""Public TW music facts, used as an explicitly labelled shared ON reference.

Only the finite model inputs are retained. This module contains no frontend or
player implementation; model provenance and licences are in THIRD_PARTY.md.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
import threading
import time
import zlib
from dataclasses import dataclass, replace
from collections import OrderedDict
from functools import cached_property
from pathlib import Path

from .moenotes_events import _NETWORK_SLOTS, SourceError

URL = "https://storage.bdon.moe/moenotes/music-data/music-data.json"
FORMAT = "nnnotes.music-data/1"
MODEL_FORMAT = "ournotes-deck.chart-stats/2"
MODEL_COMMIT = "e27d289d549aff74955977659e1bee5c72d6a4f5"
# Verified against the 0.0.3 simulator source manifest and chart-stats contract.
# Release/commit changes with identical simulator sources need no code update.
MODEL_SOURCE_SHA256 = "6f8353c73e9bfe33349d5d65a1c8a9c9ad1eadeb8f80e462a18e894b66b1f7c3"
SUPPORTED_MODEL_SOURCES = frozenset({MODEL_SOURCE_SHA256})
WIRE_LIMIT, BODY_LIMIT = 8 * 1024**2, 32 * 1024**2
TTL, MAX_STALE = 300, 86400
DIFFICULTIES = ("EASY", "NORMAL", "HARD", "EXPERT")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def decode_json(raw):
    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("nonfinite JSON")
        return result
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_float=finite_float,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))


def number(value, *, minimum=0):
    try:
        return value if type(value) in (int, float) and math.isfinite(value) and value >= minimum else None
    except OverflowError:
        return None


def text_values(value):
    if isinstance(value, str):
        return (value,) if value else ()
    return tuple(v for v in value.values() if isinstance(v, str) and v) if isinstance(value, dict) else ()


def _fields(value, names):
    return {k: value[k] for k in names if k in value} if isinstance(value, dict) else {}


def project(payload):
    """Validate identities first; missing statistics never invalidate a catalogue."""
    if not isinstance(payload, dict) or payload.get("format") != FORMAT:
        raise ValueError("unsupported music format")
    provenance = payload.get("provenance", {})
    if not isinstance(provenance, dict) or provenance.get("region") != "tw":
        raise ValueError("unverified region")
    source_songs, bands = payload.get("songs"), payload.get("bands")
    if not isinstance(source_songs, list) or not 1 <= len(source_songs) <= 3000 or not isinstance(bands, list):
        raise ValueError("invalid music catalogue")
    band_ids = [b.get("id") for b in bands if isinstance(b, dict)]
    if len(band_ids) != len(bands) or any(type(i) is not int or i <= 0 for i in band_ids) or len(set(band_ids)) != len(band_ids):
        raise ValueError("invalid band catalogue")
    song_ids, score_ids, songs = set(), set(), []
    for row in source_songs:
        if not isinstance(row, dict) or type(row.get("id")) is not int or row["id"] <= 0 or row["id"] in song_ids:
            raise ValueError("invalid or duplicate musicId")
        if not text_values(row.get("title")) or not isinstance(row.get("charts"), list):
            raise ValueError("invalid song facts")
        song_ids.add(row["id"])
        if not isinstance(row.get("bandIds", []), list) or any(type(i) is not int or i <= 0 for i in row.get("bandIds", [])):
            raise ValueError("invalid band identity")
        song = _fields(row, ("id", "title", "ruby", "phonetic", "bandIds", "bandName", "lyricist",
                             "composer", "arranger", "musicType", "gekisouMissions", "jacket", "startAt", "scoreRanks"))
        bgm = row.get("bgm")
        song["bgm"] = {"length": _fields(bgm.get("length") if isinstance(bgm, dict) else None, ("durationMs", "lengthMs"))}
        charts, difficulties = [], set()
        for chart in row["charts"]:
            if not isinstance(chart, dict):
                raise ValueError("invalid chart")
            difficulty, score_id = str(chart.get("difficulty", "")).upper(), chart.get("scoreId")
            if (difficulty not in DIFFICULTIES or difficulty in difficulties or type(score_id) is not int
                    or score_id <= 0 or score_id in score_ids):
                raise ValueError("invalid or duplicate chart identity")
            difficulties.add(difficulty)
            score_ids.add(score_id)
            c = _fields(chart, ("scoreId", "level", "displayLevel", "firstNoteMs", "lastJudgedNoteMs", "musicLengthMs"))
            c["difficulty"] = difficulty
            c["notes"] = _fields(chart.get("notes"), ("judged",))
            c["bpm"] = _fields(chart.get("bpm"), ("main", "max"))
            deck = chart.get("deck")
            if isinstance(deck, dict):
                c["deck"] = _fields(deck, ("skip", "unplayable", "ranges", "positions"))
                for group in ("seeds", "offSeeds"):
                    seeds = deck.get(group)
                    if isinstance(seeds, list) and len(seeds) <= 65536:
                        c["deck"][group] = [_fields(seed, ("score", "scorePerfect", "weights", "ranges", "rangeWeights"))
                                             for seed in seeds]
            charts.append(c)
        song["charts"] = charts
        songs.append(song)
    deck = payload.get("deck") or {}
    return {"format": FORMAT, "provenance": _fields(provenance, ("region", "client", "master", "deck")),
            "bands": [_fields(b, ("id", "name")) for b in bands],
            "deck": _fields(deck, ("model", "kinds")), "songs": songs}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class MusicSnapshot:
    data: dict
    body_sha: str
    fetched_at: float
    checked_at: float
    last_modified: str = ""
    etag: str = ""
    stale: bool = False
    unsaved: bool = False
    model_warning: str = ""

    @property
    def version(self):
        return self.body_sha

    @property
    def model_supported(self):
        model = self.data["provenance"].get("deck", {})
        if not isinstance(model, dict) or model.get("format") != MODEL_FORMAT:
            return False
        source = model.get("sourceSha256")
        if source is not None:
            return isinstance(source, str) and source in SUPPORTED_MODEL_SOURCES
        return model.get("commit") == MODEL_COMMIT

    def songs(self):
        return self.records

    @cached_property
    def records(self):
        """Project renderer/domain records entirely from this captured source."""
        from ..data import Song, Chart
        from .haneoka.song_traits import SongTraits, MISSIONS
        def localized(v):
            if not isinstance(v, dict):
                return {}
            clean = {k: val for k, val in v.items() if isinstance(val, str)}
            return {"zh": clean.get("zh-Hans") or clean.get("zh-Hant", ""), "ja": clean.get("ja", ""), "en": clean.get("en", "")}
        def primary(v):
            if isinstance(v, dict):
                for lang in ("ja", "zh-Hans"):
                    if isinstance(v.get(lang), str) and v[lang]:
                        return v[lang]
            return next(iter(text_values(v)), "")
        bands = {b.get("id"): b.get("name") for b in self.data["bands"]}
        result = []
        for s in self.data["songs"]:
            band_names = [bands[k] for k in s.get("bandIds", []) if k in bands]
            band = primary(s.get("bandName")) or " / ".join(primary(b) for b in band_names)
            local = {k: localized(s.get(k)) for k in ("title", "composer", "lyricist", "arranger")}
            local["band"] = {lang: " / ".join(localized(b).get(lang, primary(b)) for b in band_names) for lang in ("zh", "ja", "en")}
            missions = s.get("gekisouMissions")
            seq = tuple(MISSIONS[x] for x in missions) if isinstance(missions, list) and missions and all(type(x) is int and x in MISSIONS for x in missions) else None
            color = s.get("musicType")
            color = color if type(color) is int and 1 <= color <= 5 else None
            levels = [(c, number(c.get("displayLevel")), number(c.get("level"))) for c in s["charts"]]
            charts = tuple(Chart(c["difficulty"], int(lv or dl or 0), dl if dl is not None else lv or 0,
                                 (c.get("notes") or {}).get("judged") if type((c.get("notes") or {}).get("judged")) is int else None, "")
                           for c, dl, lv in levels)
            jacket = s.get("jacket", "")
            url = f"https://assets.bdon.moe/ja/Image/Jacket/{jacket}/{jacket}.webp" if re.fullmatch(r"[A-Za-z0-9_-]+", str(jacket)) else ""
            result.append(Song(s["id"], primary(s["title"]), text_values(s["title"]), band,
                               primary(s.get("composer")), primary(s.get("lyricist")), primary(s.get("arranger")),
                               str(s.get("startAt", "")), url, charts, local,
                               SongTraits(color, seq, self.stale, self.version, MODEL_FORMAT)))
        return tuple(result)


def fetch_snapshot(headers, timeout):
    """Separate anonymous session. Bounds apply to compressed and decoded bytes."""
    async def get():
        import aiohttp
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout), trust_env=False,
                                         auto_decompress=False) as session:
            async with session.get(URL, headers={"Accept-Encoding": "gzip", **headers}, allow_redirects=False) as response:
                h = {k.lower(): v for k, v in response.headers.items()}
                if response.status == 304:
                    return 304, b"", h
                if response.status != 200 or "application/json" not in h.get("content-type", ""):
                    raise SourceError("music_response")
                encoding = h.get("content-encoding", "identity").lower()
                if encoding not in ("gzip", "identity"):
                    raise SourceError("music_encoding")
                decoder = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding == "gzip" else None
                chunks, wire, size = [], 0, 0
                async for chunk in response.content.iter_chunked(65536):
                    wire += len(chunk)
                    if wire > WIRE_LIMIT:
                        raise SourceError("music_wire_limit")
                    decoded = decoder.decompress(chunk, BODY_LIMIT - size + 1) if decoder else chunk
                    size += len(decoded)
                    if size > BODY_LIMIT or decoder and decoder.unconsumed_tail:
                        raise SourceError("music_body_limit")
                    chunks.append(decoded)
                if decoder and (not decoder.eof or decoder.unused_data):
                    raise SourceError("music_gzip")
                return 200, b"".join(chunks), h
    deadline = time.monotonic() + timeout
    if not _NETWORK_SLOTS.acquire(timeout=timeout):
        raise SourceError("busy")
    try:
        timeout = deadline - time.monotonic()
        if timeout <= 0:
            raise SourceError("busy")
        return asyncio.run(get())
    except (SourceError, ValueError):
        raise
    except Exception:
        raise SourceError("music_network") from None
    finally:
        _NETWORK_SLOTS.release()


class MusicDataRepository:
    def __init__(self, path: Path, *, fetch=fetch_snapshot, clock=time.time):
        self.path, self.fetch, self.clock = Path(path), fetch, clock
        self._snapshot, self._loaded = None, False
        self._lock, self._retry_at, self._failures = threading.Lock(), 0, 0
        self._results, self._results_lock = OrderedDict(), threading.Lock()
        self.compatible_path = self.path.with_name(self.path.stem + "-compatible" + self.path.suffix)
        self._compatible = None

    def _save(self, snapshot, *, path=None):
        path = self.path if path is None else path
        envelope = {"schema": 1, "source": URL, "data": snapshot.data, "projection_sha": digest(snapshot.data),
                    **{k: getattr(snapshot, k) for k in ("body_sha", "fetched_at", "checked_at", "last_modified", "etag")}}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name, suffix=".tmp", delete=False) as f:
                temporary = f.name
                json.dump(envelope, f, ensure_ascii=False, allow_nan=False)
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self, path, now):
        try:
            if path.stat().st_size > BODY_LIMIT:
                raise ValueError("cache limit")
            saved = decode_json(path.read_bytes())
            if saved.get("schema") != 1 or saved.get("source") != URL or digest(saved["data"]) != saved["projection_sha"]:
                raise ValueError("cache identity")
            data = project(saved["data"])
            if not re.fullmatch(r"[a-f0-9]{64}", saved["body_sha"]):
                raise ValueError("cache digest")
            if not all(number(saved[k]) is not None and saved[k] <= now for k in ("fetched_at", "checked_at")):
                raise ValueError("cache time")
            return MusicSnapshot(data, saved["body_sha"], saved["fetched_at"], saved["checked_at"],
                                 saved.get("last_modified", ""), saved.get("etag", ""))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def for_calculation(self, snapshot):
        """Choose a whole compatible snapshot; never mix old statistics with new facts."""
        if snapshot.model_supported:
            return snapshot
        with self._lock:
            old = self._compatible
        if old and old.model_supported and 0 <= self.clock() - old.checked_at <= MAX_STALE:
            return replace(old, stale=True, model_warning="上游计算模型已变化，暂用上次兼容快照；新数据验证后自动恢复。")
        return snapshot

    def get(self):
        if not self._lock.acquire(timeout=12):
            return self.peek(stale=True)
        try:
            now = self.clock()
            if not self._loaded:
                self._loaded = True
                self._snapshot = self._read(self.path, now)
                self._compatible = self._read(self.compatible_path, now)
                if (self._snapshot and self._snapshot.model_supported
                        and (not self._compatible or not self._compatible.model_supported
                             or self._snapshot.checked_at >= self._compatible.checked_at)):
                    self._compatible = self._snapshot
            if self._snapshot and 0 <= now - self._snapshot.checked_at < TTL and not self._snapshot.stale:
                return self._snapshot
            if now < self._retry_at:
                return self.peek(stale=True)
            old = self._snapshot
            headers = {}
            if old and old.etag:
                headers["If-None-Match"] = old.etag
            elif old and old.last_modified:
                headers["If-Modified-Since"] = old.last_modified
            try:
                status, raw, h = self.fetch(headers, 12)
                stamp = self.clock()
                if status == 304 and old:
                    new = replace(old, checked_at=stamp, stale=False)
                elif status == 200 and len(raw) <= BODY_LIMIT:
                    new = MusicSnapshot(project(decode_json(raw)), hashlib.sha256(raw).hexdigest(), stamp, stamp,
                                        h.get("last-modified", ""), h.get("etag", ""))
                else:
                    raise ValueError("music status")
                if (not new.model_supported and old and old.model_supported
                        and (not self._compatible or old.checked_at >= self._compatible.checked_at)):
                    self._compatible = old
                    try:
                        self._save(old, path=self.compatible_path)
                    except OSError:
                        self._compatible = replace(old, unsaved=True)
                if new.model_supported:
                    try:
                        self._save(new, path=self.compatible_path)
                    except OSError:
                        new = replace(new, unsaved=True)
                    self._compatible = new
                try:
                    self._save(new)
                except OSError:
                    new = replace(new, unsaved=True)
                self._snapshot, self._failures, self._retry_at = new, 0, 0
                return new
            except (OSError, SourceError, ValueError, KeyError, TypeError, AttributeError):
                self._failures += 1
                import random
                delay = min(300, 30 * 2**min(self._failures - 1, 4))
                self._retry_at = self.clock() + min(300, delay + random.uniform(0, min(5, delay * .1)))
                return self.peek(stale=True)
        finally:
            self._lock.release()

    def peek(self, *, stale=False):
        s = self._snapshot
        if s is None or not 0 <= self.clock() - s.checked_at <= MAX_STALE:
            return None
        return replace(s, stale=stale or s.stale or self.clock() - s.checked_at >= TTL)


class MusicDataRefresher:
    """One runtime-owned worker; construction and reconnects do not duplicate it."""
    def __init__(self, source):
        self.source, self.task = source, None
        self.stopping = threading.Event()

    async def run(self):
        import logging
        logger = logging.getLogger("ournotes_bot.music_data")
        last_state = None
        try:
            while not self.stopping.is_set():
                work = asyncio.create_task(asyncio.to_thread(self.source.get))
                try:
                    snapshot = await asyncio.shield(work)
                except asyncio.CancelledError:
                    self.stopping.set()
                    await work
                    raise
                except Exception as exc:
                    logger.warning("分数表后台刷新异常：%s；等待下一周期", type(exc).__name__)
                else:
                    state = "不可用" if snapshot is None else "模型待验证" if not getattr(snapshot, "model_supported", True) else "旧缓存" if snapshot.stale else "正常"
                    if state != last_state:
                        logger.info("分数表自动刷新：%s", state)
                        last_state = state
                await asyncio.sleep(TTL)
        finally:
            self.stopping.set()

    def start(self):
        if self.task is None or self.task.done():
            self.stopping.clear()
            self.task = asyncio.create_task(self.run())
        return self.task

    def stop(self):
        self.stopping.set()
        if self.task and not self.task.done() and not self.task.get_loop().is_closed():
            self.task.cancel()
