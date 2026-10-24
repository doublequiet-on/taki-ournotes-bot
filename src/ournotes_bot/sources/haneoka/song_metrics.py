# L3: Fresh identity, bounded immutable-release resources, validated Meta records.
# Effects: Scoped public HTTP; same-release Catalog reuse, per-server cache, no stale fallback.
from __future__ import annotations

import json
import math
import re
import time
from dataclasses import replace
from threading import Lock
from urllib.parse import urlencode

from ...domain.meta_query import MetaError, MetaSnapshot, MetaSong, MetaSource, DIFFICULTIES
from ..json_validation import decode_json
from ..errors import ChartDataError
from .identity import release_identity, check_headers
from .transport import ORIGIN, public_get, public_http_scope
from .catalog_assets import asset_url


def number(value, *, integer=False, zero=False):
    return value if (type(value) in ((int,) if integer else (int, float)) and math.isfinite(value)
                     and (value >= 0 if zero else value > 0)) else None


def text_value(value):
    if not isinstance(value, list):
        return ''
    # Same Japanese-first fallback as the catalog's source display names.
    return next((v[:300] for v in value if isinstance(v, str) and v.strip()), '')


def normalize_song(key, row, meta, bands, server, release):
    if not isinstance(row, dict) or row.get('musicId') != int(key):
        raise MetaError('source_incomplete', '歌曲详情身份不一致。')
    title = text_value(row.get('musicTitle'))
    if not title:
        raise MetaError('source_incomplete', '歌曲标题缺失。')
    ids = row.get('bandIds')
    ids = tuple(ids) if (isinstance(ids, list) and ids and all(
        type(v) is int and str(v) in bands for v in ids)) else None
    band = ' / '.join(text_value(bands[str(v)].get('bandName')) for v in ids) if ids else '乐队未知'
    jacket = row.get('jacketThumbUrl') or row.get('jacketUrl') or ''
    prefix = f'/assets/{server}/'
    try:
        jacket = asset_url(jacket, release) if isinstance(jacket, str) and jacket.startswith(prefix) else ''
    except ValueError:
        jacket = ''
    expected = row.get('difficulty')
    if not isinstance(expected, list) or not expected or any(not isinstance(d, dict) for d in expected):
        raise MetaError('source_incomplete', '歌曲难度目录不完整。')
    difficulties = [d.get('difficulty') for d in expected]
    if any(type(d) is not int or d not in range(4) for d in difficulties) or len(set(difficulties)) != len(difficulties):
        raise MetaError('source_incomplete', '歌曲难度目录包含未核定或重复难度。')
    meta = meta if isinstance(meta, dict) else {}
    charts, times, warnings = [], [], set()
    for i, _ in enumerate(DIFFICULTIES):
        item = meta.get(str(i), {})
        ch = item.get('chart', {}) if isinstance(item, dict) else {}
        ch = ch if isinstance(ch, dict) else {}
        available = i in difficulties and ch.get('metaStatus') == 'available'
        reference = ch.get('reference', {})
        reference = reference if isinstance(reference, dict) else {}
        duration = number(ch.get('time')) if (available and reference.get('durationBasis') ==
            'maximum-last-judged-note-time-across-song-difficulties') else None
        if i in difficulties:
            times.append(duration)
        for warning in ch.get('metaWarnings', []) if isinstance(ch.get('metaWarnings'), list) else []:
            if isinstance(warning, str):
                warnings.add(warning[:200])
        quality = ch.get('metadataQuality')
        if isinstance(quality, str):
            warnings.add(quality[:200])
        level = number(ch.get('r')) if available else None
        displayed = number(ch.get('displayLevel')) or number(ch.get('playLevel'))
        if level != displayed:
            level = None
        values = {'level': level, 'notes': number(ch.get('n'), integer=True) if available else None,
                  'nps': number(ch.get('nps'), zero=True) if available else None}
        for field, raw in (('bpm_first', 'firstBpm'), ('bpm_min', 'minBpm'), ('bpm_max', 'maxBpm')):
            values[field] = number(ch.get(raw)) if available else None
        if all(values[k] is not None for k in ('bpm_min', 'bpm_first', 'bpm_max')) and not (
                values['bpm_min'] <= values['bpm_first'] <= values['bpm_max']):
            raise MetaError('source_incomplete', 'BPM 范围不一致。')
        charts.append(tuple(values.items()))
    known = {t for t in times if t is not None}
    if len(known) > 1:
        raise MetaError('source_incomplete', '跨难度的歌曲时长不一致，暂不能排名。')
    duration = times[0] if times and all(t is not None for t in times) else None
    published = row.get('publishedAt', [])
    published = tuple(v if type(v) in (int, float) and math.isfinite(v) else None for v in published[:10]) if isinstance(published, list) else ()
    sound = row.get('musicSound')
    audio_duration = number(sound.get('durationMs')) if isinstance(sound, dict) else None
    return MetaSong(key, title, ids, band, jacket, duration, tuple(charts), published, tuple(sorted(warnings))[:30], audio_duration)


class SongMetricsRepository:
    def __init__(self, *, fetch=public_get, clock=time.time, monotonic=time.monotonic, documents=None):
        self.fetch, self.clock, self.monotonic = fetch, clock, monotonic
        self.documents = documents
        self._locks = {s: Lock() for s in ('jp', 'intl')}
        self._cache = {}
        self._observations = {}

    def status(self):
        """Published observations only; opening Console never retrieves Meta."""
        return {'origin':ORIGIN,'servers':{server:dict(self._observations.get(server,
            {'code':'not_observed','release_id':None,'observed_at':None})) for server in ('jp','intl')}}

    @public_http_scope()
    def current(self, server):
        if server not in self._locks:
            raise MetaError('invalid_argument', '歌曲指标区服只支持 jp / intl。')
        deadline = self.monotonic() + 18
        if not self._locks[server].acquire(timeout=18):
            raise MetaError('source_unavailable', '歌曲指标查询繁忙，请稍后重试。')
        try:
            snapshot=self._collect(server, deadline)
            coverage={'duration_seconds':sum(s.duration_seconds is not None for s in snapshot.songs)}
            for field in ('level','notes','nps','bpm_first','bpm_min','bpm_max'):
                coverage[field]=sum(dict(chart).get(field) is not None for song in snapshot.songs for chart in song.charts)
            self._observations[server]={'code':'ready','release_id':snapshot.source.release_id,
                'source_id':snapshot.source.source_id,'observed_at':snapshot.source.checked_at,
                'songs':len(snapshot.songs),'charts':4*len(snapshot.songs),'field_coverage':coverage}
            return snapshot
        except MetaError as exc:
            self._observations[server]={**self._observations.get(server,{}),'code':exc.status,'failed_at':self.clock()}
            raise
        finally:
            self._locks[server].release()

    def _collect(self, server, deadline):
        total = 0
        documents = {}
        def get(resource, identity=None, ids=()):
            nonlocal total
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise MetaError('source_unavailable', '歌曲指标来源读取超时。')
            query = [('release', identity['releaseId'])] if identity else [('projection', 'identity')]
            query += [('id', key) for key in ids]
            limit = 512_000 if identity is None else 8_000_000
            reused = identity is not None and resource in documents
            try:
                if reused:
                    value = documents[resource]
                    if resource == 'songs':
                        if ids:
                            value = {'items': {k: value[k] for k in ids if k in value},
                                     'missing': [k for k in ids if k not in value]}
                        else:
                            value = {k: {'musicId': row['musicId']} for k, row in value.items()}
                    # Retain decoded size/aggregate budgets and isolate the consumer
                    # from the published Catalog objects. No fabricated HTTP headers.
                    raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
                    headers = None
                else:
                    raw, headers = self.fetch(f'{ORIGIN}/api/v1/servers/{server}/{resource}?{urlencode(query)}', limit, remaining)
            except (OSError, TimeoutError, ChartDataError) as exc:
                raise MetaError('source_unavailable', '歌曲指标来源暂不可用，请稍后重试。') from exc
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                raise MetaError('source_incomplete', '复用的歌曲资料结构不完整。') from exc
            total += len(raw)
            if len(raw) > limit or total > 32_000_000:
                raise MetaError('source_incomplete', '歌曲指标响应超出读取预算。')
            try:
                value = decode_json(raw)
                if not isinstance(value, dict):
                    raise ValueError('object required')
                if not reused:
                    check_headers(headers, identity or release_identity(value, server))
            except (ValueError, TypeError, KeyError, ChartDataError) as exc:
                raise MetaError('source_incomplete', '歌曲指标来源版本或响应结构不一致。') from exc
            return value

        # Every new query checks identity; cached content alone cannot establish currentness.
        identity = release_identity(get('release'), server)
        source = MetaSource(server, identity['releaseId'], identity['sourceId'], self.clock())
        cached = self._cache.get(server)
        if cached and (cached.source.release_id, cached.source.source_id) == (source.release_id, source.source_id):
            return replace(cached, source=source)
        if self.documents is not None:
            documents = self.documents(server, identity) or {}
        catalog = get('catalog', identity)
        if catalog.get('server') != server or catalog.get('sourceId') != source.source_id:
            raise MetaError('source_incomplete', '目录来源版本不一致。')
        resources = catalog.get('resources')
        if not isinstance(resources, dict):
            raise MetaError('source_incomplete', '来源目录缺失。')
        def collection(name):
            info = resources.get(name, {})
            count = info.get('count') if isinstance(info, dict) else None
            if type(count) is not int or not 1 <= count <= 5000:
                raise MetaError('source_incomplete', '来源目录数量无效。')
            rows = get(name, identity)
            if len(rows) != count or any(not re.fullmatch(r'[1-9][0-9]{0,15}', k) for k in rows):
                raise MetaError('source_incomplete', '来源目录数量或 ID 不完整。')
            return rows
        songs, meta, bands = collection('songs'), collection('song-meta'), collection('bands')
        if set(meta) != set(songs) or any(not isinstance(v, dict) or v.get('bandId') != int(k) for k, v in bands.items()):
            raise MetaError('source_incomplete', '歌曲指标或乐队目录不完整。')
        if any(not isinstance(v, dict) or v.get('musicId') != int(k) for k, v in songs.items()):
            raise MetaError('source_incomplete', '歌曲索引身份不一致。')
        records = []
        keys = sorted(songs, key=int)
        for start in range(0, len(keys), 100):
            requested = keys[start:start + 100]
            batch = get('songs', identity, requested)
            items = batch.get('items')
            if batch.get('missing') != [] or not isinstance(items, dict) or set(items) != set(requested):
                raise MetaError('source_incomplete', '歌曲详情批次不完整。')
            records.extend(normalize_song(k, items[k], meta[k], bands, server, source.release_id) for k in requested)
        snapshot = MetaSnapshot(source, tuple(records))
        if self.monotonic() > deadline:
            raise MetaError('source_unavailable', '歌曲指标来源读取超时。')
        self._cache[server] = snapshot
        return snapshot
