# L3
# Input: C3/C4 boards and an explicitly configured legacy Haneoka history.
# Output: Exact provider/normalization/time partitions; unknown legacy stays separate.
# Pos: Sources / cutoff history; see docs/HISTORY_DATA_CONTRACT.md.
# Effects: Lazy independent schema4 SQLite files; no in-place migration or network.
"""Provider partitions deliberately cannot be opened by schema3 writers."""
import hashlib
import json
import sqlite3
from dataclasses import asdict, replace

from .cutoff_history import HaneokaCutoffHistory, HistoryView
from .moenotes_events import SourceError

CONTRACT = 'haneoka:challenge-ranking/2'
TIMES = {'official': 'official_observed_via_haneoka', 'moenotes': 'bridge_fetched_via_haneoka'}
PROVENANCE_FIELDS = ('display_ranks', 'raw_ranks', 'value_states', 'snapshot_id', 'observed_ms',
                     'provider', 'normalization_version', 'time_kind', 'game_region', 'contract_version')


def identity(board):
    value = (board.provider, board.normalization_version, board.time_kind)
    if (board.source != 'haneoka' or board.contract_version != CONTRACT
            or board.provider not in TIMES or board.time_kind != TIMES[board.provider]
            or board.normalization_version != CONTRACT):
        raise ValueError('unsupported provider history contract')
    return value


def partition_name(key):
    return hashlib.sha256(json.dumps(key, separators=(',', ':')).encode()).hexdigest() + '.sqlite3'


class PartitionStore(HaneokaCutoffHistory):
    schema_version = 4
    contract_version = CONTRACT

    def __init__(self, path, key, **kwargs):
        super().__init__(path, **kwargs)
        self.key = tuple(key)
        self.provider, self.normalization_version, self.time_kind = self.key

    def _connect(self, *, write=False):
        existed = self.path.exists()
        db = super()._connect(write=write)
        try:
            if write and not existed:
                db.execute('CREATE TABLE IF NOT EXISTS partition_identity (singleton INTEGER PRIMARY KEY CHECK(singleton=1), identity TEXT NOT NULL)')
                db.execute('INSERT OR IGNORE INTO partition_identity VALUES(1,?)', (json.dumps(self.key),))
                db.execute('CREATE TABLE IF NOT EXISTS observation_provenance (observation_id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            row = db.execute('SELECT identity FROM partition_identity WHERE singleton=1').fetchone()
            if row is None or tuple(json.loads(row[0])) != self.key:
                raise sqlite3.DatabaseError('history partition identity mismatch')
            return db
        except BaseException:
            db.close()
            raise

    def record(self, event, board):
        try:
            if identity(board) != self.key or board.game_region != event.server:
                return 'wrong_source'
            raw = board.observed_scores if board.observed_scores is not None else board.scores
            size = len(raw)
            if (not 0 < size <= 100 or any(len(getattr(board, f)) != size for f in
                    ('display_ranks', 'raw_ranks', 'value_states'))
                    or any(v not in {'value', 'source_zero', 'missing', 'null', 'invalid', 'legacy_unknown'} for v in board.value_states)
                    or any(v is not None and (type(v) is not int or v < 0) for v in raw)
                    or any(v is not None and (type(v) is not int or v <= 0) for v in board.display_ranks)
                    or any(v is not None for v in board.raw_ranks)):
                return 'invalid_snapshot'
            if not any(v is not None for v in raw) and board.quality == 'valid':
                board = replace(board, quality='unusable')
            return super().record(event, board)
        except ValueError:
            return 'wrong_source'

    def _save_provenance(self, db, ident, board):
        payload = {name: getattr(board, name) for name in PROVENANCE_FIELDS}
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        previous = db.execute('SELECT payload FROM observation_provenance WHERE observation_id=?', (ident,)).fetchone()
        if previous and previous[0] != encoded:
            # Never overwrite a prior interpretation of the same score observation.
            raise ValueError('conflicting observation provenance')
        db.execute('INSERT OR IGNORE INTO observation_provenance VALUES(?,?)', (ident, encoded))

    def backfill_players(self, event, board, *, apply=False):
        try:
            if identity(board) != self.key or board.game_region != event.server:
                return 'wrong_source_or_missing'
        except ValueError:
            return 'wrong_source_or_missing'
        return super().backfill_players(event, board, apply=apply)

    def _save_quarantine_provenance(self, db, event, board):
        db.execute('CREATE TABLE IF NOT EXISTS quarantine_provenance (digest TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        payload = json.dumps({'event': asdict(event), 'board': asdict(board)}, sort_keys=True, ensure_ascii=False)
        db.execute('INSERT OR IGNORE INTO quarantine_provenance VALUES(?,?)',
                   (hashlib.sha256(payload.encode()).hexdigest(), payload))

    def _restore_provenance(self, db, ident, board):
        row = db.execute('SELECT payload FROM observation_provenance WHERE observation_id=?', (ident,)).fetchone()
        if row is None:
            raise ValueError('missing observation provenance')
        payload = json.loads(row[0])
        if not isinstance(payload, dict) or set(payload) != set(PROVENANCE_FIELDS):
            raise ValueError('invalid observation provenance fields')
        if (payload.get('provider'), payload.get('normalization_version'), payload.get('time_kind')) != self.key:
            raise ValueError('foreign observation provenance')
        size = len(payload['value_states']) if isinstance(payload['value_states'], list) else 0
        if not 0 < size <= 100 or any(not isinstance(payload[f], list) or len(payload[f]) != size
                                    for f in ('display_ranks', 'raw_ranks')):
            raise ValueError('invalid observation provenance positions')
        for name in ('display_ranks', 'raw_ranks', 'value_states'):
            payload[name] = tuple(payload[name])
        return replace(board, **payload, scores=board.scores[:size],
                       player_ids=board.player_ids[:size], player_names=board.player_names[:size],
                       freshness='unknown', notes=(*board.notes, '仅显示同一provider、归一化版本与时间口径的历史；非实时值。'))

    def read(self, event, song, ranks, **kwargs):
        view = super().read(event, song, ranks, **kwargs)
        return replace(view, points=tuple(replace(p, provider=self.provider,
                       normalization_version=self.normalization_version) for p in view.points),
                       warning='；'.join(filter(None, (view.warning,
                           f'历史来源 {self.provider}；时间口径 {self.time_kind}；不拼接其他分区。'))))


class ProviderHistory:
    """Legacy /1 remains unknown; /2 is independently writable and discoverable."""
    supports_provider_history = True
    supports_clock_calibration = True

    def __init__(self, legacy):
        self.legacy = legacy
        self.path = legacy.path
        self.directory = self.path.with_name(self.path.stem + '-partitions-v4')
        self._last_error = ''

    @property
    def enabled(self):
        return self.legacy.enabled

    @property
    def last_error(self):
        return self._last_error or self.legacy.last_error

    def writable(self):
        return self.legacy.writable()

    def store(self, key):
        return PartitionStore(self.directory / partition_name(key), key, enabled=self.enabled,
                              min_free_mb=self.legacy.min_free_bytes // (1024 * 1024),
                              disk_usage=self.legacy.disk_usage, clock=self.legacy.clock)

    def partitions(self):
        # Fixed supported identities; do not trust filenames/JSON to select code or paths.
        return tuple(self.store((provider, CONTRACT, kind)) for provider, kind in TIMES.items()
                     if (self.directory / partition_name((provider, CONTRACT, kind))).exists())

    def record(self, event, board):
        if board.provider is None:
            return self.legacy.record(event, board)
        try:
            store = self.store(identity(board))
        except ValueError:
            return 'wrong_source'
        result = store.record(event, board)
        self._last_error = store.last_error
        return result

    def failure(self, *args, **kwargs):
        # Unknown-provider failures cannot be assigned to a known provider.
        return self.legacy.failure(*args, **kwargs)

    def read_board(self, event, board, ranks, **kwargs):
        try:
            return self.store(identity(board)).read(event, board.song, ranks, **kwargs)
        except ValueError:
            return HistoryView(warning='不支持的provider历史合同，未读取其他来源。')

    def read(self, event, song, ranks, **kwargs):
        return self.legacy.read(event, song, ranks, **kwargs)

    def events(self, server):
        events = {e.event_id: e for e in self.legacy.events(server)}
        for store in self.partitions():
            for event in store.events(server):
                previous = events.get(event.event_id)
                songs = {s.challenge_id: s for s in previous.songs} if previous else {}
                for song in event.songs:
                    if song.challenge_id in songs and songs[song.challenge_id].music_id != song.music_id:
                        raise SourceError('history_identity_conflict')
                    songs[song.challenge_id] = song
                events[event.event_id] = replace(event, songs=tuple(songs.values()))
        return tuple(events.values())

    def latest(self, event, song, *, before_ms=None):
        boards = [s.latest(event, song, before_ms=before_ms) for s in self.partitions()]
        usable = [b for b in boards if b.has_values]
        if usable:
            return max(usable, key=lambda b: b.received_ms or -1)
        return self.legacy.latest(event, song, before_ms=before_ms)

    def status(self):
        result = self.legacy.status()
        result['partitions'] = {s.provider: s.status() for s in self.partitions()}
        result['error'] = self.last_error
        return result

    def status_text(self):
        return self.legacy.status_text() + '\n旧记录provider未知；显式provider写入独立schema4分区。'
