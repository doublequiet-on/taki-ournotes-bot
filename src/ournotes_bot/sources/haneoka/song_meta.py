# L3
# Input: Haneoka release／songs／meta 响应、缓存路径及可选 fetch／clock。
# Output: parse_payload 返回 MetaSnapshot；MetaRepository.get 返回有效／陈旧快照或 None，行记录为 MetaRow。
# Pos: Data / Sources 的 Haneoka 日服歌曲效率快照与独立仓库；见 ../L2-2.md。
# Effects/Dependencies: 直接 HTTPS、独立 JSON 缓存、锁内刷新与失败退避；来源解析不接收 SongRepository，本地歌曲映射由消费侧核对。

"""Haneoka's public, precomputed JP analysis. No scoring formula lives here."""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import threading
import time
from http.client import HTTPException
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

BASE = "https://haneoka.org/api/v1/servers/jp/"
SOURCE = "https://haneoka.org/jp/zh-CN/song-meta/"
DIFFICULTIES = ("EASY", "NORMAL", "HARD", "EXPERT")
REFERENCE = {"downtimeSeconds": 30.0, "fever": True, "intervalEndInclusive": True,
             "perfectRate": 1, "scoreUpMultiplier": 2.5, "skillDurationSeconds": 10.0}

NO_FEVER_REFERENCE = {**REFERENCE, "fever": False}
REFERENCE_LABELS = {"reference": "含 Fever 加成", "reference-no-fever": "不含 Fever 加成"}


def reference_kind(reference):
    """Only the two verified upstream contracts; reject bool/numeric aliases."""
    if not isinstance(reference, dict) or set(reference) != set(REFERENCE):
        return "unknown"
    for key, expected in REFERENCE.items():
        value = reference[key]
        if isinstance(expected, bool):
            if type(value) is not bool:
                return "unknown"
        elif type(value) not in (int, float):
            return "unknown"
    if reference == REFERENCE:
        return "reference"
    if reference == NO_FEVER_REFERENCE:
        return "reference-no-fever"
    return "unknown"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def fetch_json(path: str):
    if not re.fullmatch(r"(?:release\?projection=identity|song-meta\?release=[\w-]+|songs\?projection=4&release=[\w-]+)", path):
        raise ValueError("unexpected Haneoka resource")
    request = Request(BASE + path, headers={"Accept": "application/json", "User-Agent": "Taki-song-meta/1"})
    with urlopen(request, timeout=5) as response:
        if "application/json" not in response.headers.get("Content-Type", ""):
            raise ValueError("non-JSON response")
        body = response.read(8 * 1024 * 1024 + 1)
    if len(body) > 8 * 1024 * 1024:
        raise ValueError("oversized response")
    return json.loads(body, object_pairs_hook=unique_object)


def number(value, *, ratio=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("invalid metric")
    try:
        value = float(value)
    except OverflowError as exc:
        raise ValueError("invalid metric") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError("invalid metric")
    if ratio and value > 1:
        raise ValueError("invalid ratio")
    return float(value)


@dataclass(frozen=True)
class MetaRow:
    song_id: int
    titles: tuple[str, ...]
    difficulty: str
    level: float | None
    eff: float | None
    score: float | None
    seconds: float | None
    skill_ratio: float | None
    reference: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class MetaSnapshot:
    rows: tuple[MetaRow, ...]
    release: str
    source_version: str
    fetched_at: str
    stale: bool = False


def parse_payload(identity, songs, meta, fetched_at: str) -> MetaSnapshot:
    if (not isinstance(identity, dict) or identity.get("schema") != "haneoka-resource-release-identity-v1"
            or identity.get("server") != "jp" or not re.fullmatch(r"r-[\w-]+", str(identity.get("releaseId", "")))
            or not isinstance(identity.get("sourceId"), str)):
        raise ValueError("unexpected release identity")
    stamp = datetime.fromisoformat(fetched_at)
    if stamp.tzinfo is None:
        raise ValueError("missing fetch timezone")
    if not isinstance(songs, dict) or not isinstance(meta, dict) or not songs or set(songs) != set(meta):
        raise ValueError("empty, paginated or incomplete catalog")
    rows = []
    for key, song in songs.items():
        if not key.isdecimal() or not isinstance(song, dict) or song.get("musicId") != int(key):
            raise ValueError("invalid song identity")
        titles = song.get("musicTitle")
        charts = song.get("difficulty")
        metadata = meta[key]
        if (not isinstance(titles, list) or not any(isinstance(t, str) and t for t in titles)
                or not isinstance(charts, list) or not isinstance(metadata, dict)):
            raise ValueError("unexpected song schema")
        seen = set()
        for chart in charts:
            if not isinstance(chart, dict):
                raise ValueError("invalid difficulty")
            index = chart.get("difficulty")
            if type(index) is not int or index not in range(4) or index in seen:
                raise ValueError("duplicate or unknown difficulty")
            seen.add(index)
            if chart.get("difficultyName") != DIFFICULTIES[index].lower():
                raise ValueError("difficulty mapping changed")
            entry = metadata.get(str(index))
            if entry is None:
                continue
            if not isinstance(entry, dict) or not isinstance(entry.get("chart"), dict):
                raise ValueError("invalid metrics schema")
            metrics = entry["chart"]
            eff, score = number(metrics.get("eff")), number(metrics.get("score"))
            seconds, ratio = number(metrics.get("time")), number(metrics.get("sr"), ratio=True)
            reference = reference_kind(metrics.get("reference"))
            # Unknown models remain visible as unavailable, never enter this ranking.
            available = (metrics.get("metaStatus") == "available"
                         and metrics.get("scoreKind") == "chart-relative-factor"
                         and metrics.get("absoluteScoreAvailable") is False
                         and reference in REFERENCE_LABELS)
            warnings = metrics.get("metaWarnings", [])
            if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
                raise ValueError("invalid warnings")
            display_level = next((chart.get(field) for field in ("displayLevel", "playLevel", "sortLevel")
                                  if chart.get(field) is not None), None)
            rows.append(MetaRow(int(key), tuple(t for t in titles if isinstance(t, str) and t),
                                DIFFICULTIES[index], number(display_level),
                                eff if available else None, score if available else None,
                                seconds, ratio if available else None,
                                reference if available else "unknown", tuple(warnings)))
    if not rows:
        raise ValueError("empty analysis")
    return MetaSnapshot(tuple(rows), identity["releaseId"], identity["sourceId"], fetched_at)


class MetaRepository:
    """Independent cache; lazy IO only for meta queries, serialized refresh with backoff."""
    def __init__(self, path: Path, *, fetch=fetch_json, clock=time.time):
        self.path, self.fetch, self.clock = path, fetch, clock
        self._lock = threading.Lock()
        self._snapshot = None
        self._loaded = False
        self._retry_at = 0.0

    def get(self) -> MetaSnapshot | None:
        with self._lock:
            if not self._loaded:
                self._loaded = True
                try:
                    saved = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
                    if not isinstance(saved, dict) or saved.get("schema") != 1 or saved.get("source") != SOURCE:
                        raise ValueError("cache schema")
                    self._snapshot = parse_payload(saved["identity"], saved["songs"], saved["meta"], saved["fetched_at"])
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            now = self.clock()
            if self._snapshot:
                age = now - datetime.fromisoformat(self._snapshot.fetched_at).timestamp()
                if 0 <= age < 24 * 3600 and not self._snapshot.stale:
                    return self._snapshot
            if now < self._retry_at:
                return replace(self._snapshot, stale=True) if self._snapshot else None
            self._retry_at = now + 300
            try:
                identity = self.fetch("release?projection=identity")
                release = identity["releaseId"]
                if not re.fullmatch(r"r-[\w-]+", str(release)):
                    raise ValueError("invalid release")
                with ThreadPoolExecutor(max_workers=2) as pool:
                    song_job = pool.submit(self.fetch, f"songs?projection=4&release={release}")
                    meta_job = pool.submit(self.fetch, f"song-meta?release={release}")
                    songs, meta = song_job.result(), meta_job.result()
                fetched_at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
                snapshot = parse_payload(identity, songs, meta, fetched_at)
                saved = {"schema": 1, "source": SOURCE, "identity": identity,
                         "songs": songs, "meta": meta, "fetched_at": fetched_at}
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                                     prefix=self.path.name, suffix=".tmp", delete=False) as file:
                        temporary = file.name
                        json.dump(saved, file, ensure_ascii=False, allow_nan=False)
                    os.replace(temporary, self.path)
                finally:
                    if temporary and os.path.exists(temporary):
                        os.unlink(temporary)
                self._snapshot = snapshot
            except (OSError, HTTPException, ValueError, KeyError, TypeError):
                if self._snapshot:
                    self._snapshot = replace(self._snapshot, stale=True)
            return self._snapshot
