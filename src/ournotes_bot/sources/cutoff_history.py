# L3
# Input: Captured challenge boards, explicit source/configuration and independent v1/v2/v3 paths.
# Output: Score-only observations, source-separated history views and clock-gap/storage health.
# Pos: Data / Sources history and source composition; see L2-2.md and docs/META_OPEN.md.
# Effects: Lazy SQLite, source/minute-state composition without requests; no credentials-file, worker or platform imports.
"""Append-only observed scores. Never reconstruct unobserved game history."""
from __future__ import annotations

import bisect
import hashlib
import json
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS boards (
 server TEXT, event TEXT, challenge TEXT, music TEXT NOT NULL,
 PRIMARY KEY(server,event,challenge));
CREATE TABLE IF NOT EXISTS observations (
 id INTEGER PRIMARY KEY, server TEXT, event TEXT, challenge TEXT, music TEXT,
 source_ms INTEGER NOT NULL, server_ms INTEGER, received_ms INTEGER NOT NULL,
 scores TEXT NOT NULL, digest TEXT NOT NULL, quality TEXT NOT NULL, collect_status TEXT,
 UNIQUE(server,event,challenge,source_ms,digest));
CREATE INDEX IF NOT EXISTS observation_time ON observations(server,event,challenge,source_ms);
CREATE TABLE IF NOT EXISTS gaps (
 server TEXT, event TEXT, challenge TEXT, received_ms INTEGER, code TEXT,
 UNIQUE(server,event,challenge,received_ms,code));
CREATE INDEX IF NOT EXISTS gap_time ON gaps(server,event,challenge,received_ms);
CREATE TABLE IF NOT EXISTS health (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@dataclass(frozen=True)
class HistoryPoint:
    time_ms: int
    scores: tuple[int | None, ...]
    received_ms: int
    break_before: bool = False
    quality: str = "valid"
    source: str = "tracker"
    time_kind: str = "tracker_observed"


@dataclass(frozen=True)
class HistoryView:
    points: tuple[HistoryPoint, ...] = ()
    warning: str = ""
    total: int = 0


class CutoffHistory:
    source = "tracker"

    def __init__(self, path: Path, *, enabled=True, min_free_mb=512,
                 disk_usage=shutil.disk_usage, clock=time.time):
        self.path = Path(path).resolve()
        self.enabled = enabled
        self.min_free_bytes = max(0, int(min_free_mb)) * 1024 * 1024
        self.disk_usage, self.clock = disk_usage, clock
        self.last_error = ""
        self._lock = threading.RLock()

    def writable(self):
        if not self.enabled:
            return False
        ancestor = self.path.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        try:
            if self.disk_usage(ancestor).free < self.min_free_bytes:
                self.last_error = "low_disk"
                return False
        except OSError:
            self.last_error = "disk_unavailable"
            return False
        if self.last_error in {"low_disk", "disk_unavailable"}:
            self.last_error = ""
        return True

    def _connect(self, *, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=0.15)
        else:
            connection = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.15)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 1}:
                raise sqlite3.DatabaseError("unsupported history version")
            if write:
                connection.executescript(SCHEMA)
                connection.execute("PRAGMA user_version=1")
            return connection
        except BaseException:
            connection.close()
            raise

    @staticmethod
    def _key(event, song):
        return (event.server, str(event.event_id), str(song.challenge_id))

    @staticmethod
    def _ms(value):
        return value if type(value) is int and 0 < value < 253402300799000 else None

    def record(self, event, board):
        if getattr(board, "source", "tracker") != self.source:
            return "wrong_source"
        if not self.writable():
            return "disabled" if not self.enabled else self.last_error
        if not self._lock.acquire(timeout=0.15):
            self.last_error = "busy"
            return "busy"
        try:
            key = self._key(event, board.song)
            if key[0] not in {"jp", "tw", "kr", "en"}:
                return "invalid_identity"
            raw = getattr(board, "observed_scores", None)
            raw = board.scores if raw is None else raw
            scores = tuple(n if type(n) is int and n >= 0 else None for n in raw[:100])
            scores += (None,) * (100 - len(scores))
            encoded = json.dumps(scores, separators=(",", ":"))
            digest = hashlib.sha256(encoded.encode()).hexdigest()
            source = self._ms(board.fetched_ms)
            server = self._ms(board.server_ms)
            received = self._ms(board.received_ms) or int(self.clock() * 1000)
            quality = getattr(board, "quality", "valid")
            if not source:
                quality = "unknown_time"
            elif not server or source > server + 60000:
                quality = "clock_unknown"
            elif ((board.song.effective_start_ms and source < board.song.effective_start_ms)
                  or (board.song.effective_end_ms and source > board.song.effective_end_ms)):
                quality = "outside_period"
            with closing(self._connect(write=True)) as db, db:
                identity = db.execute("SELECT music FROM boards WHERE server=? AND event=? AND challenge=?", key).fetchone()
                if identity and identity[0] != str(board.song.music_id):
                    quality = "identity_conflict"
                db.execute("INSERT OR IGNORE INTO boards VALUES(?,?,?,?)", (*key, str(board.song.music_id)))
                previous = db.execute("SELECT digest FROM observations WHERE server=? AND event=? AND challenge=? AND source_ms=?",
                                      (*key, source or -1)).fetchall()
                if any(row[0] != digest for row in previous) and source:
                    quality = "conflict"
                    db.execute("UPDATE observations SET quality='conflict' WHERE server=? AND event=? AND challenge=? AND source_ms=?",
                               (*key, source))
                duplicate = any(row[0] == digest for row in previous)
                db.execute("INSERT OR IGNORE INTO observations(server,event,challenge,music,source_ms,server_ms,received_ms,scores,digest,quality,collect_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (*key, str(board.song.music_id), source or -1, server, received, encoded, digest, quality, board.song.collect_status))
                if quality != "valid":
                    db.execute("INSERT OR IGNORE INTO gaps VALUES(?,?,?,?,?)", (*key, received, quality))
                db.execute("INSERT OR REPLACE INTO health VALUES(?,?)", ("last_success" if quality == "valid" else "last_failure", str(received)))
                db.execute("INSERT OR REPLACE INTO health VALUES('last_quality',?)", (quality,))
            self.last_error = ""
            return quality if quality == "conflict" else "duplicate" if duplicate else quality
        except (sqlite3.Error, OSError, ValueError, OverflowError):
            self.last_error = "storage_error"
            return "storage_error"
        finally:
            self._lock.release()

    def failure(self, server, event="", challenge="", code="source_error"):
        """Record a missing collection attempt, without fabricating a score/time point."""
        if not self.writable():
            return
        if not self._lock.acquire(timeout=0.15):
            self.last_error = "busy"
            return
        try:
            with closing(self._connect(write=True)) as db, db:
                now = int(self.clock() * 1000)
                db.execute("INSERT OR IGNORE INTO gaps VALUES(?,?,?,?,?)", (server, str(event), str(challenge), now, code))
                db.execute("INSERT OR REPLACE INTO health VALUES('last_failure',?)", (str(now),))
                db.execute("INSERT OR REPLACE INTO health VALUES('last_quality',?)", (code,))
        except (sqlite3.Error, OSError):
            self.last_error = "storage_error"
        finally:
            self._lock.release()

    def read(self, event, song, ranks, *, max_points=20000, gap_ms=900000):
        if not self.enabled:
            return HistoryView(warning="历史记录未启用。")
        if not self.path.exists():
            return HistoryView(warning="暂无本地历史；从启用采集后逐步积累。")
        try:
            with closing(self._connect()) as db:
                key = self._key(event, song)
                total = db.execute("SELECT count(*) FROM observations WHERE server=? AND event=? AND challenge=?", key).fetchone()[0]
                if not total:
                    return HistoryView(warning="暂无本曲历史；从启用采集后逐步积累。")
                if total > max_points:
                    return HistoryView(warning="历史点超出本次绘图预算，仅展示当前值；原始历史完整保留。", total=total)
                failures = [row[0] for row in db.execute("SELECT received_ms FROM gaps WHERE server=? AND (event=? OR event='') AND (challenge=? OR challenge='') ORDER BY received_ms LIMIT ?", (*key, max_points + 1))]
                if len(failures) > max_points:
                    return HistoryView(warning="缺口记录超出本次绘图预算；原始历史完整保留。", total=total)
                points = []
                for stamp, received, encoded, quality, music in db.execute(
                    "SELECT source_ms,received_ms,scores,quality,music FROM observations WHERE server=? AND event=? AND challenge=? AND source_ms>0 ORDER BY source_ms,id", key):
                    if music != str(song.music_id):
                        continue
                    if points and points[-1].time_ms == stamp:
                        continue  # Conflicting rows were all marked unusable during insertion.
                    raw = json.loads(encoded)
                    scores = tuple(raw[r - 1] if quality == "valid" and r <= len(raw) else None for r in ranks)
                    previous = points[-1] if points else None
                    cut = bool(previous and (stamp - previous.time_ms > gap_ms or
                        bisect.bisect_right(failures, received) > bisect.bisect_right(failures, previous.received_ms)))
                    points.append(HistoryPoint(stamp, scores, received, cut, quality))
                warning = "" if points else "暂无可用历史时间点；当前值与历史分开显示。"
                if any(p.quality != "valid" for p in points):
                    warning = "历史含冲突或不可核实记录，已断线；未选择其中较大分数。"
                return HistoryView(tuple(points), warning, total)
        except (sqlite3.Error, OSError, ValueError, TypeError, IndexError):
            self.last_error = "storage_error"
            return HistoryView(warning="历史库暂不可用；当前榜线仍可查询。")

    def status(self):
        result = {"enabled": self.enabled, "error": self.last_error, "bytes": 0, "points": 0,
                  "first_ms": None, "last_ms": None, "last_success": None, "last_failure": None}
        if not self.path.exists():
            return result
        try:
            result["bytes"] = self.path.stat().st_size
            with closing(self._connect()) as db:
                count, first, last = db.execute("SELECT count(*), min(nullif(source_ms,-1)), max(nullif(source_ms,-1)) FROM observations").fetchone()
                result.update(points=count, first_ms=first, last_ms=last)
                result.update(dict(db.execute("SELECT key,value FROM health")))
        except (sqlite3.Error, OSError):
            result["error"] = "storage_error"
        return result

    def status_text(self):
        from .moenotes_events import display_time
        state = self.status()
        label = {"low_disk": "磁盘不足，暂停历史写入与周期采样", "disk_unavailable": "磁盘状态不可用，已暂停采样",
                 "storage_error": "历史库不可用，当前查询仍可使用", "busy": "历史库繁忙"}.get(state["error"], "正常")
        return (f"榜线历史：{'启用' if self.enabled else '关闭'} · {label} · {state['bytes'] / 1048576:.2f} MiB · {state['points']} 条观测\n"
                f"覆盖：{display_time(state['first_ms'], 'tw')} ～ {display_time(state['last_ms'], 'tw')} UTC+08:00\n"
                f"最近成功：{display_time(int(state['last_success']) if state['last_success'] else None, 'tw')}；"
                f"最近失败：{display_time(int(state['last_failure']) if state['last_failure'] else None, 'tw')} UTC+08:00")


class OpenCutoffHistory(CutoffHistory):
    """Independent schema2 file; old code continues to use its untouched v1 file."""
    source = "open"
    time_kind = "upstream_fetched"
    contract_version = "moenotes:challenge-ranking/1"
    schema_version = 2

    def _connect(self, *, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=.15)
        else:
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=.15)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in ({0, self.schema_version} if write else {self.schema_version}):
                raise sqlite3.DatabaseError("unsupported formal history version")
            if write:
                if version == 0:
                    # This happens only in the independent file. A v1 file is
                    # rejected before DDL and is never migrated in place.
                    db.executescript("BEGIN IMMEDIATE;\n" + SCHEMA)
                    if db.execute("PRAGMA user_version").fetchone()[0] == 0:
                        if db.execute("SELECT count(*) FROM observations").fetchone()[0]:
                            raise sqlite3.DatabaseError("unidentified existing observations")
                        columns = {r[1] for r in db.execute("PRAGMA table_info(observations)")}
                        for name, default in (("source", self.source), ("time_kind", self.time_kind),
                                              ("contract_version", self.contract_version)):
                            if name not in columns:
                                db.execute(f"ALTER TABLE observations ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
                    db.execute(f"PRAGMA user_version={self.schema_version}")
                    db.commit()
                columns = {r[1] for r in db.execute("PRAGMA table_info(observations)")}
                if not {"source", "time_kind", "contract_version"} <= columns:
                    raise sqlite3.DatabaseError("missing formal provenance")
            return db
        except BaseException:
            db.close()
            raise

    def record(self, event, board):
        if (getattr(board, "source", "") != self.source or getattr(board, "time_kind", "") != self.time_kind
                or getattr(board, "contract_version", "") != self.contract_version):
            return "wrong_source"
        if not self._ms(board.fetched_ms):
            self.failure(event.server, event.event_id, board.song.challenge_id, "unknown_time")
            return "unknown_time"
        return super().record(event, board)

    def read(self, event, song, ranks, **kwargs):
        view = super().read(event, song, ranks, **kwargs)
        return replace(view, points=tuple(replace(p, source=self.source, time_kind=self.time_kind) for p in view.points))


class HaneokaCutoffHistory(OpenCutoffHistory):
    """Independent schema3 file; clock-offset observations leave only a gap."""
    source = "haneoka"
    time_kind = "tracker_observed_via_haneoka"
    contract_version = "haneoka:challenge-ranking/1"
    schema_version = 3

    def record(self, event, board):
        if (getattr(board, "source", "") != self.source or getattr(board, "time_kind", "") != self.time_kind
                or getattr(board, "contract_version", "") != self.contract_version):
            return "wrong_source"
        server, received = self._ms(board.server_ms), self._ms(board.received_ms)
        if server and received and abs(server - received) > 60000:
            # Queries may show these scores, but raw source times would shift the
            # trend. Record a local gap only, including late/fallback samples.
            self.failure(event.server, event.event_id, board.song.challenge_id, "clock_skew")
            return "clock_skew"
        return super().record(event, board)


class SourceHistory:
    """Route new writes by source, read bounded segments across independent files."""
    def __init__(self, legacy, formal, *, active="tracker", haneoka=None):
        self.stores = {"tracker": legacy, "open": formal}
        if haneoka is not None:
            self.stores["haneoka"] = haneoka
        if len({h.path.resolve() for h in self.stores.values()}) != len(self.stores) or active not in self.stores:
            raise ValueError("history source paths must be independent")
        self.legacy, self.formal, self.active = legacy, formal, active

    @property
    def writer(self):
        return self.stores[self.active]

    @property
    def enabled(self):
        return self.writer.enabled

    @property
    def last_error(self):
        return self.writer.last_error

    def writable(self):
        return self.writer.writable()

    def record(self, event, board):
        source = getattr(board, "source", "tracker")
        if source != self.active:
            return "wrong_source"
        return self.writer.record(event, board)

    def failure(self, *args, **kwargs):
        return self.writer.failure(*args, **kwargs)

    def read(self, event, song, ranks, *, max_points=20000, **kwargs):
        views = [h.read(event, song, ranks, max_points=max_points, **kwargs) for h in self.stores.values()]
        total = sum(view.total for view in views)
        if total > max_points:
            return HistoryView(warning="多来源历史点超出本次绘图预算，仅展示当前值；原始历史完整保留。", total=total)
        ordered = sorted((p for view in views for p in view.points), key=lambda p: (p.time_ms, p.source))
        points, switched = [], False
        for p in ordered:
            change = bool(points and points[-1].source != p.source)
            switched |= change
            points.append(replace(p, break_before=p.break_before or change))
        warnings = [v.warning for v in views if v.warning and (v.points or not ordered)]
        if switched:
            warnings.append("历史包含不同来源段（" + "、".join(sorted({p.source for p in points})) + "），切换处断线，时间口径不可等同。")
        return HistoryView(tuple(points), " ".join(dict.fromkeys(warnings)), total)

    def status(self):
        return self.writer.status()

    def status_text(self):
        return f"当前历史来源：{self.active}；旧库保留，新旧来源查询时断线。\n" + self.writer.status_text()


def configure_sources(repository, settings):
    """Composition only. No secret-file access, source requests or sampler start."""
    from .moenotes_open import OpenClient, OpenEventCutoffRepository
    from .haneoka.event_cutoffs import HaneokaEventCutoffRepository
    source = repository.event_cutoffs
    if settings.cutoff_source == "haneoka":
        if not isinstance(source, HaneokaEventCutoffRepository):
            from .haneoka.event_metadata import EventMetadataRepository
            metadata = EventMetadataRepository(settings.cache_file.parent / "haneoka-event-metadata-v1")
            source = repository.event_cutoffs = HaneokaEventCutoffRepository(
                settings.cache_file.parent / "haneoka-cutoff-v1", metadata=metadata)
    elif settings.cutoff_source == "open" and not isinstance(source, OpenEventCutoffRepository):
        source = OpenEventCutoffRepository(settings.cache_file.parent / "moenotes-open-cutoff-v1",
                                          client=OpenClient(settings.moenotes_open_secret,
                                                            state_file=settings.cache_file.with_name("moenotes-open-admission-v1.json")))
        repository.event_cutoffs = source
    elif settings.cutoff_source == "open":
        source.client.configure(settings.moenotes_open_secret)
    elif isinstance(source, (OpenEventCutoffRepository, HaneokaEventCutoffRepository)):
        from .moenotes_events import EventCutoffRepository
        source = repository.event_cutoffs = EventCutoffRepository(settings.cache_file.parent / "moenotes-cutoff-v1")
    legacy = CutoffHistory(settings.cutoff_history_file or settings.cache_file.with_name("moenotes-history-v1.sqlite3"),
                          enabled=settings.cutoff_history_enabled, min_free_mb=settings.cutoff_history_min_free_mb)
    formal = OpenCutoffHistory(settings.moenotes_open_history_file or settings.cache_file.with_name("moenotes-open-history-v2.sqlite3"),
                              enabled=settings.cutoff_history_enabled, min_free_mb=settings.cutoff_history_min_free_mb)
    haneoka = HaneokaCutoffHistory(settings.cache_file.with_name("haneoka-cutoff-history-v3.sqlite3"),
                                  enabled=settings.cutoff_history_enabled, min_free_mb=settings.cutoff_history_min_free_mb)
    source.history = SourceHistory(legacy, formal, active=settings.cutoff_source, haneoka=haneoka)
