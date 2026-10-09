# L3
# Input: Synthetic live/static event identities, four regions, damaged caches and a fake clock.
# Output: Verified names/art and bounded same-source failure behavior; no ordinal inference.
# Pos: Tests / Haneoka activity; see tests/L2.md.
# Effects: Temporary files only; no real network, players or QQ.
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch

from ournotes_bot.commands import handle_command, resolve_command
from ournotes_bot.sources.haneoka.event_cutoffs import HaneokaEventCutoffRepository
from ournotes_bot.sources.haneoka.event_metadata import EventMetadataRepository
from test_haneoka_cutoffs import Fixture


class StaticFixture:
    def __init__(self):
        self.calls = []
        self.failure = False
        self.start = 1791300000000
        self.music = '101'
        self.release = 'r-0123456789abcdef0123'

    def __call__(self, url, limit, timeout):
        self.calls.append(url)
        if self.failure:
            raise OSError('synthetic offline')
        server, resource = urlsplit(url).path.split('/')[-2:]
        ident = dict(schema='haneoka-resource-release-identity-v1', server=server,
                     releaseId=self.release, sourceId='synthetic')
        if resource == 'release':
            value = ident
        elif resource == 'events':
            value = {'entries': {'1': dict(id='1', kind='game-event', title=['合成活动'],
                startAt=[self.start], endAt=[1791600000000],
                image=f'/assets/{server}/Assets/AddressableResources/Story/Banner/Chapter/event_1.webp')}}
        elif resource == 'challenge':
            value = {'entries': {'11': dict(id='11', kind='challenge', title=['合成歌曲'],
                songHref='/catalog/songs?song='+self.music,
                image=f'/assets/{server}/Assets/AddressableResources/Image/Jacket/small/jacket_101.webp')}}
        else:
            raise AssertionError(url)
        return json.dumps(value).encode(), {'x-haneoka-release-id': self.release, 'x-haneoka-source-id': 'synthetic'}


class ActivityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.live, self.static = Fixture(), StaticFixture()
        self.metadata = EventMetadataRepository(self.path/'metadata', fetch=self.static,
            clock=lambda: self.live.now, monotonic=lambda: self.live.now)
        self.source = HaneokaEventCutoffRepository(self.path/'live', transport=self.live,
            metadata=self.metadata, clock=lambda: self.live.now, monotonic=lambda: self.live.now)

    def event(self, server='jp'):
        return self.source.event(server, self.source.deadline())

    def test_four_regions_use_verified_dates_and_server_specific_art(self):
        for server in ('jp', 'tw', 'kr', 'en'):
            event = self.event(server)
            self.assertEqual(event.title, '合成活动')
            self.assertEqual(event.songs[0].title, '合成歌曲')
            self.assertIn('/assets/'+('jp' if server == 'jp' else 'intl')+'/', event.banner)
            self.assertIn('release='+self.static.release, event.songs[0].jacket)
            self.assertFalse(event.challenge_order_verified)
        self.assertEqual(len(self.static.calls), 6)

    def test_wrong_dates_or_song_link_never_enrich_wrong_identity(self):
        self.static.start += 1
        event = self.event()
        self.assertFalse(event.metadata_version)
        self.assertFalse(event.banner)
        self.live.now += 301
        self.static.release = 'r-aaaaaaaaaaaaaaaaaaaa'
        self.static.start -= 1
        self.static.music = '999'
        event = self.event()
        self.assertEqual(event.title, '合成活动')
        self.assertFalse(event.songs[0].jacket)
        self.assertNotEqual(event.songs[0].title, '合成歌曲')

    def test_corrupt_cache_and_outage_keep_live_activity_available(self):
        self.metadata.path.mkdir()
        (self.metadata.path/'jp.json').write_text('{"checked_at":"wrong"}')
        self.static.failure = True
        self.assertEqual(self.event().event_id, '1')
        self.assertFalse(self.event().banner)

    def test_expired_metadata_not_served_and_unchanged_release_is_cheap(self):
        event = self.event()
        self.live.now += 301
        self.metadata.enrich(event, self.source.deadline())
        self.assertEqual(len(self.static.calls), 4)
        self.static.failure = True
        self.live.now += 86401
        bare = replace(event, title='bare', banner='', metadata_version='')
        self.assertEqual(self.metadata.enrich(bare, self.source.deadline()), bare)

    def test_activity_command_does_not_request_player_rankings(self):
        repository = SimpleNamespace(event_cutoffs=self.source)
        answer = resolve_command('/查活动 hk', repository)
        self.assertEqual(answer.status, 'success')
        self.assertIn('合成活动', answer.activity.text)
        self.assertIn('/查榜线 hk 歌曲ID=101', handle_command('/查活动 hk', repository))
        self.assertTrue(all(url.endswith('/current') for url in self.live.calls))
        self.assertEqual(resolve_command('/查活动 bogus', repository).status, 'invalid_arguments')

    def test_event_art_checks_pinned_release_and_rejects_old_host(self):
        import io
        from PIL import Image
        from ournotes_bot.rendering.event_cutoff_visuals import load_artwork
        event = self.event()
        stream = io.BytesIO()
        Image.new('RGB',(10,10),'red').save(stream,format='PNG')
        headers = {'content-type':'image/png','x-haneoka-release-id':event.metadata_version,'x-haneoka-source-id':'synthetic'}
        with patch('ournotes_bot.sources.haneoka.chart_data.public_get', return_value=(stream.getvalue(),headers)) as fetch:
            art = load_artwork(self.source,event,(event.banner, 'https://assets.bdon.moe/old.webp'))
            self.assertIsNotNone(art[event.banner])
            art[event.banner].close()
            self.assertIsNone(art['https://assets.bdon.moe/old.webp'])
            self.assertEqual(fetch.call_count,1)
            headers['x-haneoka-release-id'] = 'r-aaaaaaaaaaaaaaaaaaaa'
            self.assertIsNone(load_artwork(self.source,event,(event.songs[0].jacket,))[event.songs[0].jacket])
