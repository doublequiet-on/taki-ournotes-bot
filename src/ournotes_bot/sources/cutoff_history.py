# L3
# Input: Captured challenge boards, explicit source/configuration and independent v1/v2/v3 paths.
# Output: Observed scores and same-position public player identities, event metadata, latest usable snapshots and bounded history views.
# Pos: Data / Sources history and source composition; see L2-2.md and docs/META_OPEN.md.
# Effects: Lazy SQLite, source/minute-state composition without requests; no credentials-file, worker or platform imports.
"""Append-only observed scores. Never reconstruct unobserved game history."""
from __future__ import annotations

import bisect
import hashlib
import json
import logging
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import history_compression as compressed

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

# Additive table, deliberately retaining schema versions and old reader compatibility.
EVENT_METADATA = "CREATE TABLE IF NOT EXISTS event_metadata (server TEXT, event TEXT, payload TEXT NOT NULL, PRIMARY KEY(server,event))"
PLAYER_SCHEMA = "CREATE TABLE IF NOT EXISTS observation_players (observation_id INTEGER PRIMARY KEY, players TEXT NOT NULL, quality TEXT NOT NULL)"
QUARANTINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS clock_quarantine (
 server TEXT, event TEXT, challenge TEXT, music TEXT, received_ms INTEGER,
 source_ms INTEGER, server_ms INTEGER, http_date_ms INTEGER, scores TEXT,
 source TEXT, time_kind TEXT, contract_version TEXT, quality TEXT, collect_status TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS clock_quarantine_identity ON clock_quarantine
 (server,event,challenge,received_ms,coalesce(source_ms,-1),coalesce(server_ms,-1),coalesce(http_date_ms,-1),scores);
CREATE TABLE IF NOT EXISTS clock_pending (
 server TEXT, event TEXT, challenge TEXT, first_ms INTEGER, last_ms INTEGER,
 PRIMARY KEY(server,event,challenge));
"""
_LOGGER = logging.getLogger(__name__)


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
            if version == compressed.VERSION:
                connection.close()
                return compressed.open_store(self.path, write=write, legacy_version=1)
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

    @staticmethod
    def _players(board, scores):
        from .moenotes_events import player_identity
        size = len(board.observed_scores if board.observed_scores is not None else board.scores)
        ids = board.player_ids if len(board.player_ids) == size else ()
        names = board.player_names if len(board.player_names) == size else ()
        return [list(player_identity(ids[i] if i < len(ids) else None,
                                     names[i] if i < len(names) else None)) if score is not None else [None, None]
                for i, score in enumerate(scores)]

    @staticmethod
    def _read_players(encoded, scores):
        from .moenotes_events import player_identity
        players = json.loads(encoded)
        if (not isinstance(players, list) or len(players) != len(scores)
                or any(not isinstance(p, list) or len(p) != 2 or
                       player_identity(*p) != tuple(p) or
                       (score is None and p != [None, None]) for p, score in zip(players, scores))):
            raise ValueError('invalid stored player positions')
        return players

    def _save_players(self, db, ident, board, scores):
        players = self._players(board, scores)
        if not any(any(p) for p in players):
            return 'no_players'
        if not isinstance(db, compressed.Connection):
            db.execute(PLAYER_SCHEMA)
        player_table = compressed.physical_table(db, 'observation_players')
        old = db.execute('SELECT players,quality FROM observation_players WHERE observation_id=?', (ident,)).fetchone()
        if old:
            previous = self._read_players(old[0], scores)
            if old[1] != 'valid' or any(a and b and a != b for p, q in zip(previous, players) for a, b in zip(p, q)):
                db.execute(f"UPDATE {player_table} SET quality='conflict' WHERE observation_id=?", (ident,))
                return 'player_conflict'
            players = [[a or b for a, b in zip(p, q)] for p, q in zip(previous, players)]
        db.execute(f'INSERT OR REPLACE INTO {player_table} VALUES(?,?,?)',
                   (ident, compressed.encode(db, 'observation_players', json.dumps(players, ensure_ascii=False, separators=(',', ':'))), 'valid'))
        return 'saved'

    def backfill_players(self, event, board, *, apply=False):
        """Attach only to an existing identical source/time/music/score vector; never insert observations."""
        if board.source != self.source or not self.path.exists() or not self.enabled:
            return 'wrong_source_or_missing'
        raw = board.observed_scores if board.observed_scores is not None else board.scores
        if (not raw or len(raw) > 100 or not self._ms(board.fetched_ms)
                or any(n is not None and (type(n) is not int or n < 0) for n in raw)):
            return 'invalid_snapshot'
        scores = tuple(raw) + (None,) * (100 - len(raw))
        digest = hashlib.sha256(json.dumps(scores, separators=(',', ':')).encode()).hexdigest()
        with self._lock, closing(self._connect(write=apply)) as db, db:
            stamp = board.fetched_ms
            if self.source == 'haneoka' and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clock_calibrations'").fetchone():
                calibrated = db.execute('SELECT DISTINCT source_ms FROM clock_calibrations WHERE server=? AND event=? AND challenge=? AND raw_source_ms=? AND raw_server_ms=? AND received_ms=?',
                                        (*self._key(event, board.song), stamp, board.server_ms, board.received_ms)).fetchall()
                if len(calibrated) == 1:
                    stamp = calibrated[0][0]
            rows = db.execute('SELECT id,music,digest FROM observations WHERE server=? AND event=? AND challenge=? AND source_ms=?',
                              (*self._key(event, board.song), stamp)).fetchall()
            if len(rows) != 1 or rows[0][1:] != (board.song.music_id, digest):
                return 'no_exact_observation'
            if not any(any(p) for p in self._players(board, scores)):
                return 'no_players'
            return self._save_players(db, rows[0][0], board, scores) if apply else 'matched'

    def record(self, event, board, *, _clock_provenance=None):
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
                  or (board.song.effective_end_ms and source > board.song.effective_end_ms
                      and board.song.collect_status not in {"finalizing", "archived"})):
                quality = "outside_period"
            with closing(self._connect(write=True)) as db, db:
                db.execute(EVENT_METADATA)
                db.execute("INSERT OR REPLACE INTO event_metadata VALUES(?,?,?)",
                           (event.server, str(event.event_id), json.dumps(asdict(event), ensure_ascii=False)))
                identity = db.execute("SELECT music FROM boards WHERE server=? AND event=? AND challenge=?", key).fetchone()
                if identity and identity[0] != str(board.song.music_id):
                    quality = "identity_conflict"
                db.execute("INSERT OR IGNORE INTO boards VALUES(?,?,?,?)", (*key, str(board.song.music_id)))
                previous = db.execute("SELECT digest FROM observations WHERE server=? AND event=? AND challenge=? AND source_ms=?",
                                      (*key, source or -1)).fetchall()
                if any(row[0] != digest for row in previous) and source:
                    quality = "conflict"
                    table = compressed.physical_table(db, "observations")
                    db.execute(f"UPDATE {table} SET quality='conflict' WHERE server=? AND event=? AND challenge=? AND source_ms=?",
                               (*key, source))
                duplicate = any(row[0] == digest for row in previous)
                table = compressed.physical_table(db, "observations")
                payload = compressed.encode(db, "observations", encoded)
                db.execute(f"INSERT OR IGNORE INTO {table}(server,event,challenge,music,source_ms,server_ms,received_ms,scores,digest,quality,collect_status) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                           (*key, str(board.song.music_id), source or -1, server, received, payload, digest, quality, board.song.collect_status))
                ident = db.execute('SELECT id FROM observations WHERE server=? AND event=? AND challenge=? AND source_ms=? AND digest=?',
                                   (*key, source or -1, digest)).fetchone()[0]
                self._save_players(db, ident, board, scores)
                if _clock_provenance is not None:
                    db.execute("CREATE TABLE IF NOT EXISTS clock_calibrations (server TEXT, event TEXT, challenge TEXT, source_ms INTEGER, received_ms INTEGER, raw_source_ms INTEGER, raw_server_ms INTEGER, http_date_ms INTEGER, PRIMARY KEY(server,event,challenge,source_ms,received_ms))")
                    db.execute("INSERT OR IGNORE INTO clock_calibrations VALUES(?,?,?,?,?,?,?,?)",
                               (*key, source, received, *_clock_provenance))
                if quality != "valid":
                    db.execute("INSERT OR IGNORE INTO gaps VALUES(?,?,?,?,?)", (*key, received, quality))
                db.execute("INSERT OR REPLACE INTO health VALUES(?,?)", ("last_success" if quality == "valid" else "last_failure", str(received)))
                db.execute("INSERT OR REPLACE INTO health VALUES('last_quality',?)", (quality,))
                if quality == "valid" and db.execute("SELECT 1 FROM sqlite_master WHERE name='clock_pending' AND type='table'").fetchone():
                    recovered = db.execute("DELETE FROM clock_pending WHERE server=? AND event=? AND challenge=? AND last_ms<=?",
                                           (*key, received)).rowcount
                else:
                    recovered = 0
            self.last_error = ""
            if recovered:
                _LOGGER.warning("History clock sampling recovered: server=%s event=%s challenge=%s; quarantined records retained", *key)
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
                    warning = "历史含不可用观测，已在相应时间断线；无效分数不入图。"
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
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='clock_pending' AND type='table'").fetchone():
                    result['clock_pending'] = db.execute("SELECT server,event,challenge,first_ms,last_ms FROM clock_pending ORDER BY server,event,challenge").fetchall()
                    result['quarantined'] = db.execute("SELECT count(*) FROM clock_quarantine").fetchone()[0]
        except (sqlite3.Error, OSError):
            result["error"] = "storage_error"
        return result

    def events(self, server):
        """Read recorded identities, including pre-metadata databases, without creating files."""
        from .moenotes_events import EventSnapshot, EventSong, SourceError
        if not self.enabled or not self.path.exists():
            return ()
        try:
            with closing(self._connect()) as db:
                has_metadata = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_metadata'").fetchone()
                result = []
                identities = db.execute("SELECT event,challenge,music FROM boards WHERE server=? ORDER BY event,challenge", (server,)).fetchall()
                grouped = {}
                for ident, challenge, music in identities:
                    grouped.setdefault(ident, []).append(EventSong(challenge, music, f'歌曲 ID {music}'))
                for ident, songs in grouped.items():
                    row = db.execute("SELECT payload FROM event_metadata WHERE server=? AND event=?", (server, ident)).fetchone() if has_metadata else None
                    event = EventSnapshot(server, ident, f'活动 {ident}', 'historical', None, None,
                                          tuple(songs), source=self.source, challenge_order_verified=False)
                    if row:
                        data = json.loads(row[0])
                        saved_songs = {s['challenge_id']: EventSong(**{**s, 'names': tuple(s['names'])}) for s in data['songs']}
                        if (data['server'] != server or data['event_id'] != ident or not isinstance(data['title'], str)
                                or any(not isinstance(s.title, str) or not isinstance(s.music_id, str)
                                       or any(not isinstance(n, str) for n in s.names) for s in saved_songs.values())):
                            raise ValueError('foreign event metadata')
                        event = replace(event, title=data['title'], start_ms=data['start_ms'], end_ms=data['end_ms'],
                                        songs=tuple(saved_songs[s.challenge_id] if s.challenge_id in saved_songs
                                                    and saved_songs[s.challenge_id].music_id == s.music_id else s for s in songs),
                                        banner=data['banner'], metadata_version=data['metadata_version'])
                    result.append(event)
                return tuple(result)
        except (sqlite3.Error, OSError, ValueError, TypeError, KeyError):
            self.last_error = 'storage_error'
            raise SourceError('history_unavailable') from None

    def latest(self, event, song, *, before_ms=None):
        """Newest usable whole observation; excluded attempts remain explicit."""
        from .moenotes_events import BoardSnapshot, SourceError, display_time, zone_label
        empty = BoardSnapshot(song, source=self.source, time_kind=getattr(self, 'time_kind', 'tracker_observed'),
                              contract_version=getattr(self, 'contract_version', ''), status='暂无本曲历史观测')
        if not self.enabled or not self.path.exists():
            return empty
        try:
            skipped = []
            reasons = {'outside_period': '超出榜单有效期', 'conflict': '同一时间分数冲突',
                       'identity_conflict': '曲目身份冲突', 'unknown_time': '采集时间未知',
                       'clock_unknown': '来源时钟不可核实', 'clock_skew': '来源时钟偏差'}
            with closing(self._connect()) as db:
                has_players = db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name='observation_players'").fetchone()
                rows = db.execute("SELECT source_ms,received_ms,quality,music,id,"
                                  "count(*) OVER (PARTITION BY source_ms) FROM observations "
                                  "WHERE server=? AND event=? AND challenge=? AND (? IS NULL OR source_ms<?) "
                                  "ORDER BY source_ms DESC,id DESC",
                                  (*self._key(event, song), before_ms, before_ms))
                for stamp, received, quality, music, ident, count in rows:
                    reason = ('同一时间分数冲突' if count != 1 else '曲目身份冲突' if music != song.music_id
                              else '采集时间未知' if stamp <= 0 else reasons.get(quality, quality) if quality != 'valid' else '')
                    scores = []
                    if not reason:
                        try:
                            encoded = db.execute('SELECT scores FROM observations WHERE id=?', (ident,)).fetchone()[0]
                            scores = json.loads(encoded)
                        except (ValueError, TypeError):
                            reason = '分数记录损坏'
                        if (not isinstance(scores, list) or len(scores) > 100
                                or any(n is not None and (type(n) is not int or n < 0) for n in scores)):
                            reason = '分数记录损坏'
                        elif not reason and not any(n is not None for n in scores):
                            reason = '没有可用分数'
                    if reason:
                        if not skipped:
                            skipped.append(f'末次采集 {display_time(stamp if stamp > 0 else None, event.server)} '
                                           f'{zone_label(event.server)} 不可用：{reason}；已跳过，原始记录保留。')
                        continue
                    saved = db.execute('SELECT players,quality FROM observation_players WHERE observation_id=?', (ident,)).fetchone() if has_players else None
                    try:
                        players = self._read_players(saved[0], scores) if saved and saved[1] == 'valid' else []
                    except (ValueError, TypeError):
                        players, saved = [], ('', 'corrupt')
                    player_note = ('该次观测的玩家身份记录损坏，未显示身份。' if saved and saved[1] == 'corrupt'
                                   else '该次观测的玩家身份冲突，未选择其中一份。' if saved and saved[1] != 'valid'
                                   else '该次观测未保存玩家身份。' if not players else '')
                    return replace(empty, scores=tuple(scores), fetched_ms=stamp, received_ms=received,
                                   player_ids=tuple(p[0] for p in players), player_names=tuple(p[1] for p in players),
                                   status='最近一次有效观测',
                                   notes=(*skipped, *((player_note,) if player_note else ()), '不是确认终榜。'))
            return replace(empty, quality='unusable', status='暂无有效历史观测', notes=tuple(skipped))
        except (sqlite3.Error, OSError, ValueError, TypeError):
            self.last_error = 'storage_error'
            raise SourceError('history_unavailable') from None

    def status_text(self):
        from .moenotes_events import display_time
        state = self.status()
        label = {"low_disk": "磁盘不足，暂停历史写入与周期采样", "disk_unavailable": "磁盘状态不可用，已暂停采样",
                 "storage_error": "历史库不可用，当前查询仍可使用", "busy": "历史库繁忙"}.get(state["error"], "正常")
        pending = state.get('clock_pending', ())
        if pending:
            label = (label + " · " if state['error'] else "") + "告警：时钟异常，历史观测隔离待核验"
        details = ''.join(f"\n时钟异常：{server} 活动 {event} 挑战 {challenge} · 自 {display_time(first, 'tw')} 至 {display_time(last, 'tw')} UTC+08:00"
                          for server, event, challenge, first, last in pending)
        if state.get('quarantined'):
            details += f"\n隔离保留：{state['quarantined']} 条原始观测，不入曲线；恢复采样不自动回填。"
        return (f"榜线历史：{'启用' if self.enabled else '关闭'} · {label} · {state['bytes'] / 1048576:.2f} MiB · {state['points']} 条观测\n"
                f"覆盖：{display_time(state['first_ms'], 'tw')} ～ {display_time(state['last_ms'], 'tw')} UTC+08:00\n"
                f"最近成功：{display_time(int(state['last_success']) if state['last_success'] else None, 'tw')}；"
                f"最近失败：{display_time(int(state['last_failure']) if state['last_failure'] else None, 'tw')} UTC+08:00" + details)


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
            if version == compressed.VERSION:
                db.close()
                return compressed.open_store(self.path, write=write, legacy_version=self.schema_version)
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

    def record(self, event, board, *, _clock_provenance=None):
        if (getattr(board, "source", "") != self.source or getattr(board, "time_kind", "") != self.time_kind
                or getattr(board, "contract_version", "") != self.contract_version):
            return "wrong_source"
        if not self._ms(board.fetched_ms):
            self.failure(event.server, event.event_id, board.song.challenge_id, "unknown_time")
            return "unknown_time"
        return super().record(event, board, _clock_provenance=_clock_provenance)

    def read(self, event, song, ranks, **kwargs):
        view = super().read(event, song, ranks, **kwargs)
        return replace(view, points=tuple(replace(p, source=self.source, time_kind=self.time_kind) for p in view.points))


class HaneokaCutoffHistory(OpenCutoffHistory):
    """Schema3 history with auditable HTTP-Date clock calibration; old readers remain compatible."""
    source = "haneoka"
    time_kind = "tracker_observed_via_haneoka"
    contract_version = "haneoka:challenge-ranking/1"
    schema_version = 3

    def _quarantine_clock(self, event, board):
        """Keep rejected observations separately; never reinterpret their timestamps."""
        if not self.writable():
            return "disabled" if not self.enabled else self.last_error
        if not self._lock.acquire(timeout=0.15):
            self.last_error = "busy"
            return "busy"
        try:
            key = self._key(event, board.song)
            if key[0] not in {"jp", "tw", "kr", "en"}:
                return "invalid_identity"
            received = self._ms(board.received_ms) or int(self.clock() * 1000)
            raw = board.observed_scores if board.observed_scores is not None else board.scores
            scores = json.dumps([n if type(n) is int and n >= 0 else None for n in raw[:100]], separators=(',', ':'))
            with closing(self._connect(write=True)) as db, db:
                db.executescript(QUARANTINE_SCHEMA)
                db.execute("INSERT OR IGNORE INTO clock_quarantine VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (*key, str(board.song.music_id), received, board.fetched_ms, board.server_ms,
                            board.http_date_ms, scores, board.source, board.time_kind, board.contract_version,
                            board.quality, board.song.collect_status))
                db.execute("INSERT INTO clock_pending VALUES(?,?,?,?,?) ON CONFLICT(server,event,challenge) DO UPDATE SET first_ms=min(first_ms,excluded.first_ms),last_ms=max(last_ms,excluded.last_ms)",
                           (*key, received, received))
                db.execute("INSERT OR IGNORE INTO gaps VALUES(?,?,?,?,?)", (*key, received, 'clock_skew'))
                db.execute("INSERT OR REPLACE INTO health VALUES('last_failure',?)", (str(received),))
                db.execute("INSERT OR REPLACE INTO health VALUES('last_quality','clock_skew')")
            self.last_error = ""
            _LOGGER.warning("History clock_skew: server=%s event=%s challenge=%s; raw observation quarantined, excluded from trends", *key)
            return "clock_skew"
        except (sqlite3.Error, OSError, ValueError, OverflowError):
            self.last_error = "storage_error"
            _LOGGER.error("History clock quarantine storage failed: server=%s event=%s challenge=%s", *key)
            return "storage_error"
        finally:
            self._lock.release()

    def record(self, event, board):
        if (getattr(board, "source", "") != self.source or getattr(board, "time_kind", "") != self.time_kind
                or getattr(board, "contract_version", "") != self.contract_version):
            return "wrong_source"
        server, received = self._ms(board.server_ms), self._ms(board.received_ms)
        if server and received and abs(server - received) > 60000:
            date, fetched = self._ms(board.http_date_ms), self._ms(board.fetched_ms)
            # Only the same HTTPS response's Date, independently close to local
            # receipt, may anchor the source-relative age. Never hard-code 130s.
            if not date or abs(date - received) > 5000 or not fetched or fetched > server + 1000:
                return self._quarantine_clock(event, board)
            adjusted = replace(board, fetched_ms=fetched + date - server, server_ms=date)
            return super().record(event, adjusted, _clock_provenance=(fetched, server, date))
        return super().record(event, board)

    def read(self, event, song, ranks, **kwargs):
        view = super().read(event, song, ranks, **kwargs)
        if not view.points:
            return view
        try:
            with closing(self._connect()) as db:
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='clock_calibrations'").fetchone()
                calibrated = exists and db.execute("SELECT 1 FROM clock_calibrations WHERE server=? AND event=? AND challenge=? LIMIT 1",
                                                  self._key(event, song)).fetchone()
            if calibrated:
                return replace(view, warning="；".join(filter(None, (view.warning,
                    "部分历史时间按 HTTP Date 与来源相对年龄校准，含秒精度及网络误差。"))))
        except (sqlite3.Error, OSError):
            return replace(view, warning="；".join(filter(None, (view.warning, "时钟校准记录暂不可读。"))))
        return view


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

    def events(self, server):
        from .moenotes_events import SourceError
        events = {}
        for store in self.stores.values():
            for event in store.events(server):
                previous = events.get(event.event_id)
                if previous:
                    songs = {s.challenge_id: s for s in previous.songs}
                    for song in event.songs:
                        if song.challenge_id in songs and songs[song.challenge_id].music_id != song.music_id:
                            raise SourceError('history_identity_conflict')
                        if song.challenge_id not in songs or song.names:
                            songs[song.challenge_id] = song
                    event = replace(event if event.metadata_version != 'unknown' else previous, songs=tuple(songs.values()))
                events[event.event_id] = event
        return tuple(events.values())

    def latest(self, event, song):
        from .moenotes_events import display_time, zone_label
        boards = {key: store.latest(event, song) for key, store in self.stores.items()}
        notes = [n for b in boards.values() for n in b.notes]
        while True:
            latest = max(boards.values(), key=lambda b: (b.fetched_ms or -1, b.received_ms or -1))
            tied = [key for key, b in boards.items() if b.fetched_ms is not None and b.fetched_ms == latest.fetched_ms]
            if not any(boards[key].scores != latest.scores for key in tied):
                if any(a and b and a != b for key in tied for field in ('player_ids', 'player_names')
                       for a, b in zip(getattr(latest, field), getattr(boards[key], field))):
                    return replace(latest, player_ids=(), player_names=(),
                                   notes=tuple(dict.fromkeys((*notes, '同一时间的多来源玩家身份冲突，分数保留，身份未选择。'))))
                return replace(latest, notes=tuple(dict.fromkeys((*notes, *latest.notes))))
            notes.append(f'{display_time(latest.fetched_ms, event.server)} {zone_label(event.server)} '
                         '同一时间的多来源历史冲突，已跳过该时间点。')
            for key in tied:
                boards[key] = self.stores[key].latest(event, song, before_ms=latest.fetched_ms)
                notes.extend(boards[key].notes)

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
    source.scheduled_history_only = settings.cutoff_source == "tracker"
    legacy = CutoffHistory(settings.cutoff_history_file or settings.cache_file.with_name("moenotes-history-v1.sqlite3"),
                          enabled=settings.cutoff_history_enabled, min_free_mb=settings.cutoff_history_min_free_mb)
    formal = OpenCutoffHistory(settings.moenotes_open_history_file or settings.cache_file.with_name("moenotes-open-history-v2.sqlite3"),
                              enabled=settings.cutoff_history_enabled, min_free_mb=settings.cutoff_history_min_free_mb)
    haneoka = HaneokaCutoffHistory(settings.cache_file.with_name("haneoka-cutoff-history-v3.sqlite3"),
                                  enabled=settings.cutoff_history_enabled, min_free_mb=settings.cutoff_history_min_free_mb)
    source.history = SourceHistory(legacy, formal, active=settings.cutoff_source, haneoka=haneoka)
    source.score_display_limit = settings.cutoff_score_display_limit
