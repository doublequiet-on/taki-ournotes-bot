# L3
# Input: Anonymous release-pinned Haneoka JP songs and song-meta, independent cache and clock.
# Output: Both ordinary and gekisou site reference results; missing metrics stay unknown.
# Pos: Data / Sources Haneoka site meta; see ../L2-2.md.
# Effects: Bounded Haneoka-only requests, version checks and atomic cache; no local scoring or QQ.
"""Read the two reference scenarios published by the Haneoka song-meta page."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from .chart_data import API, _headers, _identity, _json, public_get
from .song_meta import DIFFICULTIES, number

TTL = 300
MAX_STALE = 86400
SOURCE = "https://haneoka.org/jp/zh-CN/song-meta/"
SCENES = {"normal": ("chart", "chart-relative-factor"), "gekisou": ("gekisou", "gekisou-relative")}


@dataclass(frozen=True)
class SiteMetaRow:
    song_id: int
    titles: tuple[str, ...]
    difficulty: str
    level: float | None
    eff: float | None
    score: float | None
    seconds: float | None
    skill_ratio: float | None
    scene: str
    reference_id: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class SiteMetaSnapshot:
    rows: tuple[SiteMetaRow, ...]
    release: str
    source_version: str
    fetched_at: str
    stale: bool = False
    unsaved: bool = False


def parse(identity, songs, meta, fetched_at):
    _identity(identity)
    stamp = datetime.fromisoformat(fetched_at)
    if stamp.tzinfo is None or not isinstance(songs, dict) or not songs or set(songs) != set(meta):
        raise ValueError("incomplete site meta snapshot")
    rows = []
    for key, song in songs.items():
        if not key.isdecimal() or type(song.get("musicId")) is not int or song["musicId"] != int(key):
            raise ValueError("song identity mismatch")
        titles = song.get("musicTitle")
        if not isinstance(titles, list) or not any(isinstance(t, str) and t for t in titles):
            raise ValueError("missing song title")
        seen = set()
        for difficulty in song["difficulty"]:
            index = difficulty.get("difficulty")
            if type(index) is not int or index not in range(4) or index in seen:
                raise ValueError("invalid difficulty")
            seen.add(index)
            if difficulty.get("difficultyName") != DIFFICULTIES[index].lower():
                raise ValueError("difficulty identity mismatch")
            item = meta[key].get(str(index))
            if item is None:
                continue
            if not isinstance(item, dict):
                raise ValueError("invalid meta row")
            chart = item.get("chart", {})
            for scene, (field, score_kind) in SCENES.items():
                metrics = item.get(field)
                if metrics is None:
                    continue
                if not isinstance(metrics, dict):
                    raise ValueError("invalid scenario")
                ref = metrics.get("reference", {})
                ref_id = metrics.get("referenceId", "")
                valid = (metrics.get("mode") == scene and metrics.get("metaStatus") == "available"
                         and metrics.get("scoreKind") == score_kind and isinstance(ref, dict)
                         and isinstance(ref_id, str) and bool(ref_id))
                # Display source reference coefficients, never native scores or inferred game results.
                eff, score = number(metrics.get("eff")), number(metrics.get("score"))
                seconds = number(ref.get("durationSeconds", chart.get("time"))) if isinstance(ref, dict) else None
                warnings = metrics.get("metaWarnings", metrics.get("qualityWarnings", []))
                if not isinstance(warnings, list) or not all(isinstance(w, str) for w in warnings):
                    raise ValueError("invalid source warnings")
                level = next((difficulty[k] for k in ("displayLevel", "playLevel", "sortLevel") if difficulty.get(k) is not None), None)
                rows.append(SiteMetaRow(int(key), tuple(t for t in titles if isinstance(t, str) and t),
                    DIFFICULTIES[index], number(level), eff if valid else None, score if valid else None,
                    seconds, number(chart.get("sr"), ratio=True) if scene == "normal" else None,
                    scene, ref_id if isinstance(ref_id, str) else "", tuple(warnings)))
    if not rows:
        raise ValueError("empty site meta")
    return SiteMetaSnapshot(tuple(rows), identity["releaseId"], identity["sourceId"], fetched_at)


class SiteMetaRepository:
    def __init__(self, path, *, fetch=public_get, clock=time.time):
        self.path, self.fetch, self.clock = Path(path), fetch, clock
        self._lock = threading.Lock()
        self._snapshot = None
        self._loaded = False
        self._checked_at = self._retry_at = 0.0

    def _document(self, resource, identity=None):
        query = "release=" + identity["releaseId"] if identity else "projection=identity"
        raw, headers = self.fetch(API + resource + "?" + query, 8_000_000, 12)
        value = _json(raw)
        _headers(headers, identity or _identity(value))
        return value

    def peek(self, *, stale=False):
        snapshot = self._snapshot
        if snapshot is None or not 0 <= self.clock() - self._checked_at <= MAX_STALE:
            return None
        return replace(snapshot, stale=stale or snapshot.stale)

    def get(self):
        with self._lock:
            if not self._loaded:
                self._loaded = True
                try:
                    if self.path.stat().st_size > 16_100_000:
                        raise ValueError("site meta cache size limit")
                    saved = _json(self.path.read_bytes())
                    if saved["schema"] != 1 or saved["source"] != SOURCE:
                        raise ValueError("foreign site meta cache")
                    candidate = parse(saved["identity"], saved["songs"], saved["meta"], saved["fetched_at"])
                    checked = datetime.fromisoformat(saved["fetched_at"]).timestamp()
                    if not 0 < checked <= self.clock():
                        raise ValueError("invalid site meta cache time")
                    self._snapshot, self._checked_at = candidate, checked
                except (OSError, ValueError, KeyError, TypeError, AttributeError):
                    pass
            now = self.clock()
            if self._snapshot and 0 <= now - self._checked_at < TTL and not self._snapshot.stale:
                return self._snapshot
            if now < self._retry_at:
                return self.peek(stale=True)
            try:
                identity = self._document("release")
                if (self._snapshot and identity["releaseId"] == self._snapshot.release
                        and identity["sourceId"] == self._snapshot.source_version):
                    self._checked_at = now
                    self._snapshot = replace(self._snapshot, stale=False)
                    return self._snapshot
                songs = self._document("songs", identity)
                meta = self._document("song-meta", identity)
                fetched_at = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
                snapshot = parse(identity, songs, meta, fetched_at)
                saved = dict(schema=1, source=SOURCE, identity=identity, songs=songs, meta=meta, fetched_at=fetched_at)
                temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    temporary.write_text(json.dumps(saved, ensure_ascii=False, allow_nan=False), encoding="utf-8")
                    temporary.replace(self.path)
                except OSError:
                    snapshot = replace(snapshot, unsaved=True)
                finally:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
                self._snapshot, self._checked_at, self._retry_at = snapshot, self.clock(), 0
                return snapshot
            except (OSError, ValueError, KeyError, TypeError, RuntimeError, AttributeError):
                self._retry_at = self.clock() + 60
                if self._snapshot:
                    self._snapshot = replace(self._snapshot, stale=True)
                return self.peek(stale=True)
