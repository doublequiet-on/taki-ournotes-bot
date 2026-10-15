# L3
# Input: Song／Chart 身份、Haneoka JP release／歌曲实体／原始谱面，以及可选缓存目录。
# Output: ChartData 保留完整 score、release/source 身份和 fresh/cached/stale/unsaved 状态。
# Pos: Data / Sources 的日服完整谱面适配；见 ../L2-2.md。
# Effects/Dependencies: 公共 transport/identity/JSON、谱面专属路径权限、固定版本及原子独立缓存；失败不访问其他来源，不调用 QQ。

"""Read release-pinned chart bytes without changing the renderer's score format."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import threading
import time
import uuid
import weakref
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from ...config import runtime_data_dir
from ...data import Chart, Song, normalize
from ..chart_data import CHART_CACHE_TTL, MAX_CHART_BYTES, ChartData, _parse_score
from ..errors import ChartDataError
from ..json_validation import decode_json
from .transport import ORIGIN, public_get
from .identity import release_identity, check_headers
from .chart_paths import ASSET_PREFIX, chart_asset_url


API = ORIGIN + "/api/v1/servers/jp/"
CACHE_NAME = "haneoka-chart-jp-v1"
MAX_CATALOG_BYTES = 512_000
MAX_CACHE_BYTES = 3_000_000
TIMEOUT = 12
DIFFICULTIES = ("EASY", "NORMAL", "HARD", "EXPERT")
_LOCKS_GUARD = threading.Lock()
_LOCKS = weakref.WeakValueDictionary()


def _cache_lock(path):
    key = os.path.normcase(str(path.resolve()))
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        # Owners and waiters keep this lock alive; idle paths leave no registry entry.
        return lock


def _select(row, song, chart):
    if not isinstance(row, dict) or type(row.get("musicId")) is not int or row["musicId"] != song.id:
        raise ChartDataError("Haneoka song ID mismatch")
    titles = row.get("musicTitle")
    if (not isinstance(titles, list) or not 1 <= len(titles) <= 10
            or any(not isinstance(title, str) or len(title) > 512 for title in titles)):
        raise ChartDataError("Invalid Haneoka song titles")
    local_titles = {normalize(title) for title in (*song.titles, song.title) if title}
    if not local_titles.intersection(normalize(title) for title in titles if title):
        raise ChartDataError("Haneoka song title mismatch")
    jacket = row.get("jacketUrl")
    if not isinstance(jacket, str) or len(jacket) > 1024:
        raise ChartDataError("Invalid Haneoka jacket identity")
    if song.jacket_url and Path(urlsplit(song.jacket_url).path).stem != Path(urlsplit(jacket).path).stem:
        raise ChartDataError("Haneoka song jacket mismatch")
    difficulties = row.get("difficulty")
    if not isinstance(difficulties, list) or not 1 <= len(difficulties) <= 16:
        raise ChartDataError("Invalid Haneoka difficulties")
    selected = [entry for entry in difficulties if isinstance(entry, dict)
                and type(entry.get("difficulty")) is int
                and entry["difficulty"] == DIFFICULTIES.index(chart.difficulty)]
    if len(selected) != 1:
        raise ChartDataError("Missing or duplicate Haneoka difficulty")
    entry = selected[0]
    if (entry.get("difficultyName") != chart.difficulty.lower()
            or type(entry.get("scoreId")) is not int or not 0 < entry["scoreId"] <= 2**53 - 1
            or type(entry.get("playLevel")) is not int or entry["playLevel"] != chart.level):
        raise ChartDataError("Haneoka chart identity mismatch")
    notes = entry.get("noteCount")
    if notes is not None and (type(notes) is not int or not 0 <= notes <= 1_000_000):
        raise ChartDataError("Invalid Haneoka note count")
    if chart.notes is not None and notes is not None and chart.notes != notes:
        raise ChartDataError("Haneoka chart note count mismatch")
    return entry


def _snapshot(saved, song, chart, now):
    if not isinstance(saved, dict) or saved.get("schema") != 1 or saved.get("source") != "haneoka":
        raise ChartDataError("Invalid Haneoka chart cache")
    identity = release_identity(saved.get("identity"), "jp")
    fetched_at = saved.get("fetched_at")
    if type(fetched_at) not in (float, int) or not 0 < fetched_at <= now or not math.isfinite(fetched_at):
        raise ChartDataError("Invalid Haneoka chart cache time")
    entry = _select(saved.get("song"), song, chart)
    chart_asset_url(entry.get("file"), identity["releaseId"])
    encoded = saved.get("raw_chart")
    if not isinstance(encoded, str) or len(encoded) > (MAX_CHART_BYTES + 2) // 3 * 4:
        raise ChartDataError("Invalid Haneoka chart cache body")
    raw = base64.b64decode(encoded, validate=True)
    if hashlib.sha256(raw).hexdigest() != saved.get("sha256"):
        raise ChartDataError("Haneoka chart cache checksum mismatch")
    decode_json(raw)
    score = _parse_score(raw)
    return ChartData(score, "haneoka", identity["releaseId"], identity["sourceId"], "cached")


def _read(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_CACHE_BYTES + 1)
    if len(raw) > MAX_CACHE_BYTES:
        raise ChartDataError("Haneoka chart cache is too large")
    return decode_json(raw)


def _save(path, saved):
    raw = json.dumps(saved, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > MAX_CACHE_BYTES:
        raise OSError("Haneoka chart cache is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(raw)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_chart(song: Song, chart: Chart, cache_dir: Path | None = None) -> ChartData:
    if (type(song.id) is not int or not 0 < song.id <= 2**53 - 1
            or chart.difficulty not in DIFFICULTIES or chart not in song.charts):
        raise ChartDataError("Invalid requested chart identity")
    path = (cache_dir or runtime_data_dir()) / CACHE_NAME / f"{song.id}_{chart.difficulty}.json"
    captured = song.catalog.get('chart_source')
    if captured is not None:
        identity = release_identity(captured['identity'], 'jp')
        row = captured['song']
        entry = _select(row, song, chart)
        chart_asset_url(entry.get('file'), identity['releaseId'])
    with _cache_lock(path):
        now = time.time()
        cached = None
        try:
            saved = _read(path)
            cached = _snapshot(saved, song, chart, now)
            if (now - saved["fetched_at"] < CHART_CACHE_TTL
                    and (captured is None or cached.release_id == identity['releaseId'])):
                return cached
        except (OSError, ValueError, TypeError, RecursionError, ChartDataError):
            pass
        deadline = time.monotonic() + TIMEOUT

        def get(url, limit):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ChartDataError("Haneoka chart request timed out")
            raw, headers = public_get(url, limit, remaining)
            if len(raw) > limit:
                raise ChartDataError("Haneoka response is too large")
            return raw, headers

        try:
            # Catalog queries already captured this exact release and chart path.
            # Older/Yume songs still use the independently verified discovery flow.
            if captured is None:
                raw, headers = get(API + "release?projection=identity", 4096)
                identity = release_identity(decode_json(raw), "jp")
                check_headers(headers, identity)
                raw, headers = get(API + f"songs/{song.id}?release={identity['releaseId']}", MAX_CATALOG_BYTES)
                check_headers(headers, identity)
                row = decode_json(raw)
                entry = _select(row, song, chart)
            raw, headers = get(chart_asset_url(entry.get("file"), identity["releaseId"]), MAX_CHART_BYTES)
            check_headers(headers, identity)
            saved = {"schema": 1, "source": "haneoka", "identity": identity, "fetched_at": now,
                     "song": {"musicId": row["musicId"], "musicTitle": row["musicTitle"],
                              "jacketUrl": row["jacketUrl"],
                              "difficulty": [{key: entry.get(key) for key in
                                              ("difficulty", "difficultyName", "scoreId", "playLevel", "noteCount", "file")}]},
                     "raw_chart": base64.b64encode(raw).decode("ascii"), "sha256": hashlib.sha256(raw).hexdigest()}
            result = replace(_snapshot(saved, song, chart, now), cache_state="fresh")
            try:
                _save(path, saved)
            except OSError:
                result = replace(result, cache_state="unsaved")
            return result
        except (OSError, ValueError, TypeError, RecursionError, ChartDataError) as exc:
            if cached is not None:
                return replace(cached, cache_state="stale")
            raise ChartDataError(f"Haneoka chart unavailable: {song.id}/{chart.difficulty}") from exc
