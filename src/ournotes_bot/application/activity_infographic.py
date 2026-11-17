# L3: Background lifecycle for JP activity infographics. Reply readers only access the store.
from functools import partial
from pathlib import Path
import time

from ..config import runtime_data_dir
from ..domain.activity_infographic import source_name
from ..rendering.activity_infographic import render_infographic
from ..rendering.haneoka_theme import game_assets
from ..sources.activity_infographic_store import ActivityInfographicStore, ActivityPeriod
from ..sources.haneoka.activity_infographic import InfographicSource, check_cancelled
from ..sources.haneoka.game_assets import asset_bytes
from ..sources.haneoka.transport import public_http_scope
from ..sources.moenotes_events import SourceError
from .replies import visual_asset


class ActivityInfographic:
    def __init__(self, repository, *, directory=None, source=None, clock=time.time, render=render_infographic):
        self.repository = repository
        self.data_root = runtime_data_dir() if directory is None else Path(directory)
        self.store = ActivityInfographicStore(self.data_root / 'activity-infographic')
        self.source = source or InfographicSource(self.store.directory)
        self.clock, self.render = clock, render

    def refresh(self, *, rebuild=False):
        source = self.repository.event_cutoffs
        if source.foreground_active:
            return {'code': 'foreground_yield'}
        try:
            event = source.event('jp', source.deadline())
        except SourceError as exc:
            return {'code': 'empty' if exc.code == 'not_found' else exc.code}
        check_cancelled()
        if not event.start_ms or not event.end_ms:
            return {'code': 'pending'}
        period = ActivityPeriod(event.event_id, event.title, event.start_ms, event.end_ms)
        now = self.clock()
        if not period.active(now) and not rebuild:
            return {'code': 'empty'}
        if period.start / 1000 > now:
            return {'code': 'empty'}
        try:
            last = self.store.period()
        except (ValueError, TypeError):
            last = None  # A verified current event can replace a malformed manifest.
        if last and period.start < last.start:
            return {'code': 'stale'}
        if last and last.image_name == period.image_name and last.end == period.end and self.store.ready(last) and not rebuild:
            return {'code': 'cached', 'event_id': period.event_id}
        if last is None or last.image_name != period.image_name:
            self.store.observe(period)
        song_ids = tuple(dict.fromkeys(song.music_id for song in event.songs if song.enabled))
        with public_http_scope(), game_assets(partial(asset_bytes, cache_dir=self.data_root / 'game-assets')):
            data = self.source.capture(period, song_ids)
            if not event.metadata_version:
                title = source_name(data.event.get('title'))
                if title:
                    period = ActivityPeriod(period.event_id, title, period.start, period.end)
            image = self.render(period, data, load=visual_asset)
        check_cancelled()
        if self.store.period() != period:
            self.store.observe(period)
        self.store.publish(period, image)
        return {'code': 'ready', 'event_id': period.event_id, 'size': len(image)}
