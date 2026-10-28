# L3
# Input: Explicit normalized total-ranking snapshots from a verified adapter; full event identity.
# Output: Independent captured totals, persistent current cache and sampled history with gaps.
# Pos: Sources / Total event rankings; see L2-2.md and docs/EVENT_CUTOFFS.md.
# Effects: Lazy isolated SQLite only; no HTTP adapter, implicit network or song-history writes.
"""Internal adapter contract, deliberately independent of any unverified upstream URL."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import asdict, replace
from pathlib import Path

from ..domain.total_rankings import TotalRankingSnapshot
from .cutoff_history import HistoryPoint, HistoryView
from .moenotes_events import EventSnapshot, EventSong, SERVERS, SourceError, player_identity
from .json_validation import decode_json

CONTRACT = 'taki:total-ranking/1'
MAX_SCORE = 2**53 - 1


def event_key(event):
    if (event.server not in SERVERS or not isinstance(event.event_id, str) or not event.event_id.isdecimal()
            or any(type(t) is not int or not 0 < t < 253402300799000 for t in (event.start_ms, event.end_ms))
            or event.start_ms >= event.end_ms or len(event.songs) > 16
            or any(not isinstance(value,str) or not value.isdecimal() for s in event.songs for value in (s.challenge_id,s.music_id))
            or len({s.challenge_id for s in event.songs}) != len(event.songs)):
        raise SourceError('invalid_total_identity')
    identity = [event.server, event.event_id, event.start_ms, event.end_ms,
                sorted((s.challenge_id, s.music_id) for s in event.songs)]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def validate(event, snapshot, source):
    if (not isinstance(snapshot, TotalRankingSnapshot) or snapshot.event_key != event_key(event)
            or snapshot.source != source or event.source != source or snapshot.contract != CONTRACT
            or type(snapshot.stale) is not bool or not isinstance(snapshot.scores,tuple) or len(snapshot.scores) > 100
            or not isinstance(snapshot.player_ids,tuple) or not isinstance(snapshot.player_names,tuple)
            or type(snapshot.received_ms) is not int or not 0 <= snapshot.received_ms < 253402300799000
            or not isinstance(snapshot.quality,str) or snapshot.quality not in {'valid','conflict','stale','clock_unknown','outside_period'}
            or not isinstance(snapshot.status,str) or len(snapshot.status)>300
            or not isinstance(snapshot.notes,tuple) or any(not isinstance(n,str) or len(n)>300 for n in snapshot.notes)
            or any(s is not None and (type(s) is not int or not 0 <= s <= MAX_SCORE) for s in snapshot.scores)
            or any(type(t) is not int or not 0 < t < 253402300799000 for t in (snapshot.fetched_ms, snapshot.server_ms))
            or any(values and len(values) != len(snapshot.scores) for values in (snapshot.player_ids, snapshot.player_names))):
        raise SourceError('invalid_total_snapshot')
    for i, score in enumerate(snapshot.scores):
        ident = snapshot.player_ids[i] if snapshot.player_ids else None
        name = snapshot.player_names[i] if snapshot.player_names else None
        if player_identity(ident, name) != (ident, name) or score is None and (ident is not None or name is not None):
            raise SourceError('invalid_total_players')
    return snapshot


def quality(event, snapshot, now_ms):
    if snapshot.quality == 'conflict':
        return 'conflict'
    if (abs(snapshot.server_ms-snapshot.received_ms) > 60000 or snapshot.fetched_ms > snapshot.server_ms+1000
            or now_ms < snapshot.received_ms):
        return 'clock_unknown'
    if not event.start_ms <= snapshot.fetched_ms <= event.end_ms:
        return 'outside_period'
    if snapshot.stale or snapshot.server_ms-snapshot.fetched_ms+now_ms-snapshot.received_ms > 600000:
        return 'stale'
    return 'valid'


def decode(payload):
    if len(payload) > 65536:
        raise ValueError('total snapshot size limit')
    row = decode_json(payload)
    for key in ('scores', 'player_ids', 'player_names', 'notes'):
        row[key] = tuple(row[key])
    if row.get('observed_scores') is not None:
        row['observed_scores'] = tuple(row['observed_scores'])
    return TotalRankingSnapshot(**row)


class TotalRankingHistory:
    """Separate schema/file: rollback to old Taki never needs to read this database."""
    def __init__(self, path, source, *, enabled=True, min_free_mb=512, clock=time.time, disk_usage=shutil.disk_usage):
        if source not in {'tracker', 'open', 'haneoka'}:
            raise ValueError('invalid total-ranking source')
        self.path, self.source = Path(path).resolve(), source
        self.enabled, self.min_free_bytes = enabled, max(0, min_free_mb)*1024*1024
        self.clock, self.disk_usage, self.lock = clock, disk_usage, threading.RLock()
        self.last_error = ''

    def writable(self):
        if not self.enabled:
            return False
        parent = self.path.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        try:
            if self.disk_usage(parent).free < self.min_free_bytes:
                self.last_error = 'low_disk'
                return False
        except OSError:
            self.last_error = 'disk_unavailable'
            return False
        return True

    def _connect(self, write=False):
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path if write else self.path.as_uri()+'?mode=ro', uri=not write, timeout=1)
        try:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            app = db.execute('PRAGMA application_id').fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if (version != 1 or app != 1414485068) and (not write or version != 0 or tables or app):
                raise ValueError('foreign total-ranking database')
            if write and version == 0:
                db.executescript('''
                    CREATE TABLE total_identity (source TEXT NOT NULL, contract TEXT NOT NULL);
                    CREATE TABLE events (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    CREATE TABLE current_totals (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    CREATE TABLE samples (key TEXT, stamp INTEGER, digest TEXT, payload TEXT, quality TEXT,
                        PRIMARY KEY(key,stamp,digest));
                    CREATE TABLE gaps (key TEXT, stamp INTEGER, code TEXT);
                    CREATE INDEX gap_times ON gaps(key,stamp);
                    PRAGMA user_version=1;
                    PRAGMA application_id=1414485068;
                ''')
                db.execute('INSERT INTO total_identity VALUES (?,?)',(self.source,CONTRACT))
            identity=db.execute('SELECT source,contract FROM total_identity').fetchmany(2)
            if identity != [(self.source,CONTRACT)]:
                raise ValueError('foreign total-ranking source')
            return db
        except BaseException:
            db.close()
            raise

    def save_current(self, event, snapshot):
        return self._write(event, snapshot, current=True)

    def record(self, event, snapshot):
        return self._write(event, snapshot, current=False)

    def _write(self, event, snapshot, *, current):
        raw = replace(snapshot, scores=snapshot.observed_scores if snapshot.observed_scores is not None else snapshot.scores,
                      observed_scores=None)
        validate(event, raw, self.source)
        if type(raw.received_ms) is not int or not 0 < raw.received_ms < 253402300799000:
            raise SourceError('invalid_total_time')
        if not self.writable():
            return self.last_error or 'disabled'
        payload = json.dumps(asdict(raw), ensure_ascii=False, allow_nan=False)
        if len(payload) > 65536:
            raise SourceError('invalid_total_snapshot')
        key = event_key(event)
        try:
            with self.lock, closing(self._connect(True)) as db, db:
                db.execute('INSERT OR REPLACE INTO events VALUES (?,?)', (key,json.dumps(asdict(event),ensure_ascii=False)))
                if current:
                    db.execute('INSERT OR REPLACE INTO current_totals VALUES (?,?)', (key,payload))
                    return 'cached'
                digest = hashlib.sha256(json.dumps(raw.scores).encode()).hexdigest()
                conflict = db.execute('SELECT 1 FROM samples WHERE key=? AND stamp=? AND digest<>?',
                                      (key,raw.fetched_ms,digest)).fetchone()
                state = 'conflict' if conflict else quality(event,raw,raw.received_ms)
                added = db.execute('INSERT OR IGNORE INTO samples VALUES (?,?,?,?,?)',
                                   (key,raw.fetched_ms,digest,payload,state)).rowcount
                if state == 'conflict':
                    db.execute("UPDATE samples SET quality='conflict' WHERE key=? AND stamp=?",(key,raw.fetched_ms))
                if state != 'valid':
                    db.execute('INSERT INTO gaps VALUES (?,?,?)',(key,raw.received_ms,state))
                return state if added or conflict else 'duplicate'
        except (sqlite3.Error, OSError, ValueError):
            self.last_error = 'storage_error'
            return self.last_error

    def failure(self, event, code):
        if not self.writable():
            return
        try:
            with self.lock, closing(self._connect(True)) as db, db:
                db.execute('INSERT INTO gaps VALUES (?,?,?)',(event_key(event),int(self.clock()*1000),code))
        except (sqlite3.Error, OSError, ValueError):
            self.last_error = 'storage_error'

    def failure_for_server(self, server, code):
        """A sampler can mark a known active board when discovery is unavailable."""
        try:
            now=int(self.clock()*1000)
            for event in self.events(server):
                if event.start_ms <= now <= event.end_ms:
                    self.failure(event,code)
        except SourceError:
            self.last_error='storage_error'

    def cached(self, event):
        if not self.path.exists():
            return None
        try:
            with self.lock, closing(self._connect()) as db:
                row = db.execute('SELECT payload FROM current_totals WHERE key=?',(event_key(event),)).fetchone()
                return validate(event,decode(row[0]),self.source) if row else None
        except (sqlite3.Error, OSError, ValueError, KeyError, TypeError, SourceError):
            self.last_error = 'storage_error'
            return None

    def events(self, server):
        if not self.enabled or not self.path.exists():
            return ()
        try:
            with self.lock, closing(self._connect()) as db:
                result=[]
                for key,payload in db.execute('SELECT key,payload FROM events WHERE key IN (SELECT key FROM samples)'):
                    row=json.loads(payload)
                    if row['server'] != server:
                        continue
                    row['songs']=tuple(EventSong(**{**s,'names':tuple(s['names'])}) for s in row['songs'])
                    event=EventSnapshot(**row)
                    if event_key(event) != key:
                        raise ValueError('foreign total event')
                    result.append(event)
                return tuple(result)
        except (sqlite3.Error,OSError,ValueError,KeyError,TypeError,SourceError) as exc:
            raise SourceError('total_history_unavailable') from exc

    def _rows(self, event, max_points):
        if not self.enabled or not self.path.exists():
            return [],[]
        with self.lock, closing(self._connect()) as db:
            rows=db.execute('SELECT stamp,payload,quality FROM samples WHERE key=? ORDER BY stamp',
                            (event_key(event),)).fetchmany(max_points+1)
            gaps=[r[0] for r in db.execute('SELECT stamp FROM gaps WHERE key=? ORDER BY stamp',(event_key(event),))]
            return rows,gaps

    def latest(self, event):
        try:
            rows,_=self._rows(event,20000)
            if len(rows)>20000:
                raise ValueError('total history budget')
            for _,payload,state in reversed(rows):
                if state=='valid':
                    board=validate(event,decode(payload),self.source)
                    return replace(board,status='综合榜最后有效观测，非确认终榜')
            return None
        except (sqlite3.Error,OSError,ValueError,KeyError,TypeError,SourceError) as exc:
            raise SourceError('total_history_unavailable') from exc

    def read(self, event, ranks, *, max_points=20000):
        try:
            rows,gaps=self._rows(event,max_points)
            if len(rows)>max_points:
                return HistoryView(warning='综合榜历史超出绘图预算；原始记录保留。',total=len(rows))
            points=[]; broken=False; previous=None
            for stamp,payload,state in rows:
                board=validate(event,decode(payload),self.source)
                if state!='valid':
                    broken=True
                    continue
                broken |= previous is not None and any(previous < t <= stamp for t in gaps)
                points.append(HistoryPoint(stamp,tuple(board.score(r) for r in ranks),board.received_ms,
                                           broken,source=self.source,time_kind='total_ranking_observed'))
                previous=stamp; broken=False
            warning='综合榜异常时间或冲突观测已排除，缺口断线。' if len(points)!=len(rows) else ''
            return HistoryView(tuple(points),warning,len(rows))
        except (sqlite3.Error,OSError,ValueError,KeyError,TypeError,SourceError):
            return HistoryView(warning='综合榜历史暂不可用，未改查单曲历史。')


class TotalRankingRepository:
    """fetch(event, deadline) must return the internal normalized contract above.

    No adapter is installed by default. Queries update only current cache;
    sample() is the sole history writer, called at the existing sampler cadence.
    """
    def __init__(self, history, *, fetch=None, clock=time.time):
        if fetch is not None and not callable(fetch):
            raise ValueError('total-ranking adapter must be callable')
        self.history,self.fetch,self.clock=history,fetch,clock
        self.lock=threading.RLock(); self.current={}; self.retry={}

    @property
    def connected(self):
        return self.fetch is not None

    def capture(self, event, deadline):
        if not self.connected:
            return None
        key=event_key(event); now=int(self.clock()*1000)
        with self.lock:
            cached=self.current.get(key) or self.history.cached(event)
            if cached and 0 <= now-cached.received_ms < 60000:
                raw=cached
            elif self.retry.get(key,0)>now:
                raw=cached
            else:
                try:
                    raw=validate(event,self.fetch(event,deadline),self.history.source)
                    now=int(self.clock()*1000)
                    raw=replace(raw,received_ms=now,observed_scores=None,quality='valid')
                    if cached and raw.fetched_ms < cached.fetched_ms:
                        raise SourceError('total_time_regression')
                    if cached and raw.fetched_ms == cached.fetched_ms and (raw.scores != cached.scores or cached.quality=='conflict'):
                        raise SourceError('total_conflict')
                    self.current[key]=raw
                    saved=self.history.save_current(event,raw)
                    if saved not in {'cached','disabled'}:
                        raw=replace(raw,notes=('综合榜当前缓存保存失败；本次结果仍可查看。',))
                    self.retry.pop(key,None)
                except SourceError as exc:
                    self.retry[key]=now+max(60000,exc.retry_after*1000)
                    if exc.code=='total_conflict':
                        raw=replace(raw,quality='conflict')
                        self.current[key]=raw
                        self.history.save_current(event,raw)
                    else:
                        raw=cached
            if raw is None:
                return None
            state=quality(event,raw,now)
            return replace(raw,scores=raw.scores if state=='valid' else (),observed_scores=raw.scores,quality=state,
                           status='综合榜当前观测' if state=='valid' else '综合榜快照时间异常或过期，暂无可信当前值',
                           notes=raw.notes+(('综合榜来源暂不可用，暂用同源缓存。',) if key in self.retry else ()))

    def sample(self, event, deadline):
        if not self.connected:
            return 'not_connected'
        board=self.capture(event,deadline)
        if board is None:
            self.history.failure(event,'total_unavailable')
            return 'unavailable'
        if event_key(event) in self.retry and board.quality!='conflict':
            self.history.failure(event,'total_source_fallback')
            return 'source_fallback'
        stored=self.history.record(event,board)
        return board.quality if board.quality!='valid' and stored in {'duplicate','valid'} else stored
