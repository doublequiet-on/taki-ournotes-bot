# L3
# Input: Song／Chart 身份、可选缓存目录、显式来源与完整谱面响应。
# Output: load_chart_score 返回原 score dict；load_chart_data 另带来源、版本与缓存状态；不可用时抛 ChartDataError。
# Pos: Data / Sources 的完整谱面入口与旧 MoeNotes 适配；Haneoka 实现在 haneoka/chart_data.py；见 L2-2.md。
# Effects/Dependencies: 按所选来源联网并读写独立缓存；失败仅回退该来源的有效旧缓存，不自动切源。

"""Fetch validated MoeNotes score files for songs in the Project Yume catalog."""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.request import Request, urlopen

from ..config import runtime_data_dir
from ..data import Chart, Song


CHART_BASE = "https://assets.bdon.moe/zh-Hans/Live/MusicScore"
CHART_CACHE_TTL = 6 * 3600
MAX_CHART_BYTES = 2_000_000


class ChartDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChartData:
    score: dict
    source: str
    release_id: str = ""
    source_id: str = ""
    cache_state: str = ""

    def notice(self, locale: str) -> str:
        if self.source != "haneoka":
            return ""
        suffix = {"zh": "（旧缓存）", "ja": "（旧キャッシュ）", "en": " (stale cache)"}
        return "Haneoka JP" + (suffix.get(locale, suffix["zh"]) if self.cache_state == "stale" else "")


def load_chart_data(song: Song, chart: Chart, cache_dir: Path | None = None, *, source="moenotes") -> ChartData:
    if source == "haneoka":
        from .haneoka.chart_data import load_chart
        return load_chart(song, chart, cache_dir)
    if source == "moenotes":
        return ChartData(load_chart_score(song, chart, cache_dir), source)
    raise ChartDataError("Unsupported chart source")


def score_name(song: Song, chart: Chart) -> str:
    """Map the public 100000-series song ID to the release score naming scheme."""
    music_id = song.id - 100000
    difficulties = {"EASY": 0, "NORMAL": 1, "HARD": 2, "EXPERT": 3}
    if not 1 <= music_id <= 9999 or chart.difficulty not in difficulties or chart not in song.charts:
        raise ChartDataError("No verified score mapping for this chart")
    stem = f"{music_id:04d}_{difficulties[chart.difficulty]:02d}"
    return f"{music_id:04d}/{stem}"


def chart_url(song: Song, chart: Chart) -> str:
    name = score_name(song, chart)
    return f"{CHART_BASE}/{name}/{name.rsplit('/', 1)[1]}.json"


def _parse_score(raw: bytes) -> dict:
    if len(raw) > MAX_CHART_BYTES:
        raise ChartDataError("Score asset is too large")
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("score"), dict):
            raise ValueError("Unexpected score schema")
        score = payload["score"]
        notes = score["notes"]
        meta = payload.get("meta")
        if not isinstance(meta, dict) or meta.get("version") != 100 or not isinstance(notes, list) or not notes:
            raise ValueError("Unexpected score schema")
        if len(notes) > 20_000 or not isinstance(score.get("events"), dict):
            raise ValueError("Unexpected score size or events")
        return score
    except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ChartDataError("Unrecognized score asset") from exc


def load_chart_score(song: Song, chart: Chart, cache_dir: Path | None = None, *, source="moenotes") -> dict:
    """Return validated score data; use a stale cache if the network is unavailable."""
    if source != "moenotes":
        return load_chart_data(song, chart, cache_dir, source=source).score
    url = chart_url(song, chart)
    cache = cache_dir or runtime_data_dir() / "chart-cache"
    name = score_name(song, chart).replace("/", "_") + ".json"
    path = cache / name
    cached: dict | None = None
    if path.exists():
        try:
            cached = _parse_score(path.read_bytes())
            if time.time() - path.stat().st_mtime < CHART_CACHE_TTL:
                return cached
        except (OSError, ChartDataError):
            cached = None
    try:
        with urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=12) as response:
            raw = response.read(MAX_CHART_BYTES + 1)
        score = _parse_score(raw)
        try:
            cache.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                temporary.write_bytes(raw)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        except OSError:
            # A read-only cache must not hide a chart that was fetched successfully.
            pass
        return score
    except (OSError, ValueError, ChartDataError) as exc:
        if cached is not None:
            return cached
        raise ChartDataError(f"Score asset unavailable: {name}") from exc
