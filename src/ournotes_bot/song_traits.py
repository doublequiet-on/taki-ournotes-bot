"""Release-pinned Haneoka JP song attributes; queries only read local snapshots.

Song-level mission order comes from MasterLiveMusic, not chart fever intervals.
No upstream implementation is copied. See THIRD_PARTY.md for field evidence.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .song_meta import BASE, unique_object

SOURCE = "https://haneoka.org/jp/zh-CN/songs/"
COLORS = {1: "红色", 2: "蓝色", 3: "绿色", 4: "黄色", 5: "紫色"}
MISSIONS = {1: "COMBO", 2: "LUCK", 3: "JUST"}
TTL = 24 * 3600


@dataclass(frozen=True)
class SongTraits:
    color: int | None = None
    missions: tuple[str, ...] | None = None
    stale: bool = False


def describe(song) -> str:
    traits = song.traits
    color = COLORS.get(traits.color, "未获取") if traits else "未获取"
    sequence = " → ".join(traits.missions) if traits and traits.missions else "未获取"
    return f"颜色：{color} · 激奏：{sequence}" + ("（旧缓存）" if traits and traits.stale else "")


def fetch_json(resource):
    if not re.fullmatch(r"release\?projection=identity|(?:songs(?:/[0-9]+)?|gekisou)\?release=r-[\w-]+", resource):
        raise ValueError("unverified song resource")
    with urlopen(Request(BASE + resource, headers={"Accept": "application/json", "User-Agent": "Taki-song-traits/1"}), timeout=8) as response:
        if "application/json" not in response.headers.get("Content-Type", ""):
            raise ValueError("non-JSON song response")
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("oversized song response")
    return json.loads(raw, object_pairs_hook=unique_object)


def validate(saved):
    if (not isinstance(saved, dict) or saved.get("schema") != 1 or saved.get("source") != SOURCE
            or saved.get("server") != "jp" or not re.fullmatch(r"r-[\w-]+", str(saved.get("release", "")))
            or not isinstance(saved.get("source_version"), str) or not saved["source_version"]):
        raise ValueError("invalid song snapshot identity")
    stamp = datetime.fromisoformat(saved["fetched_at"])
    if stamp.tzinfo is None:
        raise ValueError("missing fetch timezone")
    rows, ids = saved.get("rows"), saved.get("ids")
    if not isinstance(rows, dict) or not rows or not isinstance(ids, list) or len(ids) != len(set(ids)) or set(ids) != set(rows):
        raise ValueError("incomplete song snapshot")
    for key, row in rows.items():
        if (not isinstance(key, str) or not key.isdecimal() or not isinstance(row, dict)
                or type(row.get("id")) is not int or row["id"] != int(key)
                or not isinstance(row.get("titles"), list) or not row["titles"]
                or not all(isinstance(t, str) and t for t in row["titles"])
                or not isinstance(row.get("jacket"), str) or not row["jacket"]):
            raise ValueError("invalid song mapping")
        if row.get("color") is not None and (type(row["color"]) is not int or row["color"] not in COLORS):
            raise ValueError("unknown song color")
        seq = row.get("missions")
        if seq is not None and (not isinstance(seq, list) or not 1 <= len(seq) <= 16 or any(type(v) is not int or v not in MISSIONS for v in seq)):
            raise ValueError("unknown song mission")
    return saved


def collect(fetch, fetched_at):
    identity = fetch("release?projection=identity")
    if (not isinstance(identity, dict) or identity.get("schema") != "haneoka-resource-release-identity-v1"
            or identity.get("server") != "jp" or not re.fullmatch(r"r-[\w-]+", str(identity.get("releaseId", "")))):
        raise ValueError("unexpected release")
    release = identity["releaseId"]
    catalog = fetch(f"songs?release={release}")
    if not isinstance(catalog, dict) or not catalog or len(catalog) > 3000 or any(not k.isdecimal() for k in catalog):
        raise ValueError("empty or invalid catalog")
    enums = fetch(f"gekisou?release={release}")
    if not isinstance(enums, dict) or not isinstance(enums.get("enums"), dict) or enums["enums"].get("missionType") != {"sourceType": "App.LiveBase.GekisouMissionType", "values": {"0": "None", "1": "Combo", "2": "Luck", "3": "JustCount", "4": "All"}}:
        raise ValueError("mission enum changed")
    def detail(key):
        row = fetch(f"songs/{key}?release={release}")
        base = catalog[key]
        if (not isinstance(row, dict) or not isinstance(base, dict) or row.get("musicId") != int(key)
                or base.get("musicId") != int(key) or row.get("musicType") != base.get("musicType")
                or row.get("musicTitle") != base.get("musicTitle") or row.get("jacketUrl") != base.get("jacketUrl")):
            raise ValueError("conflicting song detail")
        if (not isinstance(row.get("musicTitle"), list)
                or not isinstance(row.get("gekisou"), (dict, type(None)))):
            raise ValueError("song detail fields changed")
        return key, {"id": row["musicId"], "titles": [t for t in row.get("musicTitle", []) if t],
                     "jacket": Path(urlsplit(row.get("jacketUrl", "")).path).stem,
                     "color": row.get("musicType"), "missions": (row.get("gekisou") or {}).get("missionTypes")}
    # One complete low-frequency refresh, never a per-query download fan-out.
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = dict(pool.map(detail, catalog))
    return validate({"schema": 1, "source": SOURCE, "server": "jp", "release": release,
                     "source_version": identity.get("sourceId"), "fetched_at": fetched_at,
                     "ids": list(catalog), "rows": rows})


class SongTraitsRepository:
    def __init__(self, path, *, fetch=fetch_json, clock=time.time):
        self.path, self.fetch, self.clock = Path(path), fetch, clock
        self.saved = None
        self.failed = False
        self.retry_at = 0
        self._lock = threading.Lock()
        try:
            self.saved = validate(json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=unique_object))
        except (OSError, ValueError, TypeError, KeyError):
            pass

    @property
    def stale(self):
        return bool(self.saved and (self.failed or self.clock() - datetime.fromisoformat(self.saved["fetched_at"]).timestamp() >= TTL))

    def refresh(self):
        with self._lock:
            if self.clock() < self.retry_at or (self.saved and not self.stale):
                return
            try:
                candidate = collect(self.fetch, datetime.fromtimestamp(self.clock(), timezone.utc).isoformat())
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
                temporary.replace(self.path)
                self.saved, self.failed = candidate, False
            except (OSError, HTTPException, ValueError, TypeError, KeyError):
                self.failed = True
                self.retry_at = self.clock() + 300

    def apply(self, songs):
        from .data import normalize
        saved = self.saved
        rows = saved["rows"] if saved else {}
        result = []
        for song in songs:
            row = rows.get(str(song.id))
            traits = None
            # ID alone is insufficient; require exact known title and jacket identity.
            if (row and {normalize(t) for t in song.titles + (song.title,)} & {normalize(t) for t in row["titles"]}
                    and Path(urlsplit(song.jacket_url).path).stem == row["jacket"]):
                seq = row.get("missions")
                traits = SongTraits(row.get("color"), tuple(MISSIONS[v] for v in seq) if seq else None, self.stale)
            result.append(replace(song, traits=traits))
        return result
