# L3
# Input: Captured live event identities and anonymous fixed-release static resources.
# Output: Names/art only after event dates and challenge/music links match; no guessed order.
# Pos: Sources / Haneoka activity enrichment; see ../L2-2.md.
# Effects: Haneoka-only bounded reads and independent cache; never changes live event times.
from __future__ import annotations

import json
import math
import threading
import time
from dataclasses import replace
from pathlib import Path

from .catalog import identity
from .catalog_assets import asset_url
from .chart_data import ORIGIN, _headers, _json, public_get


def validate(saved, server, now):
    if saved['schema'] != 1 or saved['server'] != server:
        raise ValueError('foreign metadata')
    identity(saved['identity'], server)
    checked = saved['checked_at']
    if type(checked) not in (int, float) or not math.isfinite(checked) or not 0 < checked <= now:
        raise ValueError('invalid metadata time')
    for key in ('events', 'challenges'):
        if not isinstance(saved[key]['entries'], dict):
            raise ValueError('invalid metadata entries')
    return saved


class EventMetadataRepository:
    def __init__(self, path, *, fetch=public_get, clock=time.time, monotonic=time.monotonic):
        self.path, self.fetch, self.clock, self.monotonic = Path(path), fetch, clock, monotonic
        self.saved = {}
        self.retry = {}
        self.lock = threading.Lock()

    def enrich(self, event, deadline):
        # Same preferred static server as Haneoka's event-tracker page; no cross-server fallback.
        server = "jp" if event.server == "jp" else "intl"
        with self.lock:
            now = self.clock()
            saved = self.saved.get(server)
            if saved is None:
                try:
                    target = self.path / (server + '.json')
                    if target.stat().st_size > 1_100_000:
                        raise ValueError('metadata size limit')
                    saved = validate(_json(target.read_bytes()), server, now)
                    self.saved[server] = saved
                except (OSError, ValueError, KeyError, TypeError):
                    saved = None
            if ((not saved or not 0 <= now - saved['checked_at'] < 300)
                    and now >= self.retry.get(server, 0)):
                self.retry[server] = now + 60
                try:
                    def get(resource, ident=None):
                        remaining = deadline - self.monotonic()
                        if remaining <= 0:
                            raise ValueError('metadata deadline')
                        query = 'release=' + ident['releaseId'] if ident else 'projection=identity'
                        raw, headers = self.fetch(f'{ORIGIN}/api/v1/servers/{server}/{resource}?{query}',
                                                   512_000, min(4, remaining))
                        value = _json(raw)
                        _headers(headers, ident or identity(value, server))
                        return value
                    ident = identity(get('release'), server)
                    if saved and saved['identity'] == ident:
                        saved = {**saved, 'checked_at': now}
                    else:
                        candidate = dict(schema=1, server=server, identity=ident, checked_at=now,
                                         events=get('events', ident), challenges=get('challenge', ident))
                        saved = validate(candidate, server, now)
                    self.saved[server] = saved
                    try:
                        self.path.mkdir(parents=True, exist_ok=True)
                        target = self.path/(server + '.json')
                        temporary = target.with_suffix('.tmp')
                        temporary.write_text(json.dumps(saved, ensure_ascii=False), encoding='utf8')
                        temporary.replace(target)
                    except OSError:
                        pass
                except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                    pass
            if not saved or not 0 <= now - saved['checked_at'] <= 86400:
                return event
            try:
                row = saved['events']['entries'][event.event_id]
                if (row.get('id') != event.event_id or row.get('kind') != 'game-event'
                        or not event.start_ms or not event.end_ms
                        or event.start_ms not in row.get('startAt', []) or event.end_ms not in row.get('endAt', [])):
                    return event
                release = saved['identity']['releaseId']
                def names(values):
                    return tuple(dict.fromkeys(values[i] for i in (3, 0, 1, 2, 4)
                        if i < len(values) and isinstance(values[i], str) and values[i].strip()))
                def image(path):
                    try:
                        return asset_url(path, release)
                    except ValueError:
                        return ''
                title = names(row.get('title', []))
                songs, catalog = [], []
                for song in event.songs:
                    item = saved['challenges']['entries'].get(song.challenge_id, {})
                    if (item.get('id') != song.challenge_id or item.get('kind') != 'challenge'
                            or item.get('songHref') != '/catalog/songs?song=' + song.music_id):
                        songs.append(song)
                        continue
                    titles = names(item.get('title', []))
                    songs.append(replace(song, title=titles[0] if titles else song.title,
                                         names=titles, jacket=image(item.get('image'))))
                    catalog.append((song.music_id, titles))
                notes = event.notes
                if now - saved['checked_at'] >= 300:
                    notes += ('活动名称与素材暂用已核对的旧静态版本。',)
                return replace(event, title=title[0] if title else event.title, songs=tuple(songs),
                    banner=image(row.get('image')), catalog=tuple(catalog), metadata_version=release, notes=notes)
            except (KeyError, ValueError, TypeError, IndexError, AttributeError):
                return event
