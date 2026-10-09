# L3
# Input: Real source composition/rendering/workers with offline HTTP responses and temporary caches.
# Output: Executed request URLs, source isolation and explicitly retained legacy dependencies.
# Pos: Tests / source retirement gate; see docs/HANEOKA_RETIREMENT.md.
# Effects: Fake HTTP sessions only; external sockets fail, no production config or QQ sends.
import asyncio
import hashlib
import io
import json
import socket
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from ournotes_bot import visuals
from ournotes_bot.data import SongRepository, Chart, Song, DataError
from ournotes_bot.query.efficiency_query import parse_efficiency, execute_efficiency
from ournotes_bot.sources import moenotes_music_data as music
from ournotes_bot.sources.chart_data import load_chart_data
from ournotes_bot.sources.haneoka import catalog, chart_data
from ournotes_bot.sources.cutoff_history import configure_sources
from ournotes_bot.sources.cutoff_sampler import HistorySampler
import test_haneoka_charts as chart_fixture
import test_haneoka_cutoffs as cutoff_fixture
import test_moenotes_meta as music_fixture
import test_event_cutoffs as tracker_fixture


class Response:
    def __init__(self, raw, headers):
        self.raw, self.headers = raw, headers
        self.status = 200
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.raw if limit < 0 else self.raw[:limit]

    async def iter_chunked(self, size):
        for start in range(0, len(self.raw), size):
            yield self.raw[start:start + size]


class SourceEgressTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.calls = []
        self.transport_failed = False
        self.saved = json.loads((Path(__file__).parent / 'fixtures/haneoka_catalog.json').read_bytes())['snapshot']
        self.saved['documents']['jp']['ui-marks'] = {f'CardType-{name}.png': f'runtime/unity/Assets/AddressableResources/UI/Atlas/FixUiSpriteAtlas.spriteatlasv2/CardType-{name}--Sprite-123.png' for name in ('Red','Blue','Green','Yellow','Purple')}
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.external_attempts = []
        original_connect, original_resolve = socket.socket.connect, socket.getaddrinfo
        def check_host(host):
            # Windows asyncio creates a loopback socket pair for its own wakeups.
            if host not in {'127.0.0.1', '::1', 'localhost'}:
                self.external_attempts.append(str(host))
                raise AssertionError('external network forbidden')
        def connect(sock, address):
            check_host(address[0] if isinstance(address, tuple) else address)
            return original_connect(sock, address)
        def resolve(host, *args, **kwargs):
            check_host(host)
            return original_resolve(host, *args, **kwargs)
        self.stack.enter_context(patch('socket.socket.connect', connect))
        self.stack.enter_context(patch('socket.getaddrinfo', resolve))
        self.addCleanup(lambda: self.assertEqual(self.external_attempts, []))
        owner = self

        class Session:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            def get(self, url, **kwargs):
                return owner.request(url, 'aiohttp', kwargs)

        self.stack.enter_context(patch('aiohttp.ClientSession', Session))
        # Imported urllib functions must also be caught at their actual consumers.
        for target in ('ournotes_bot.visuals.urlopen', 'ournotes_bot.sources.yatta.urlopen',
                       'ournotes_bot.sources.chart_data.urlopen',
                       'ournotes_bot.sources.haneoka.song_meta.urlopen',
                       'ournotes_bot.sources.haneoka.song_traits.urlopen',
                       'ournotes_bot.sources.haneoka.haneoka_members.urlopen'):
            self.stack.enter_context(patch(target, side_effect=lambda req, **kw: self.request(req.full_url, 'urllib', kw)))
        self.stack.enter_context(patch.object(visuals, 'runtime_data_dir', return_value=self.root))
        image = Image.new('RGB', (16, 16), '#777777')
        buf = io.BytesIO()
        image.save(buf, format='PNG')
        image.close()
        self.png = buf.getvalue()
        self.handler = self.catalog_response

    def request(self, url, transport, options):
        self.calls.append({'url': url, 'transport': transport})
        if urlsplit(url).netloc == 'haneoka.org' and transport == 'aiohttp':
            self.assertIs(options.get('allow_redirects'), False)
        if self.transport_failed:
            raise OSError('synthetic source outage')
        raw, headers = self.handler(url)
        return Response(raw, headers)

    def catalog_response(self, url):
        parts = urlsplit(url)
        self.assertEqual(parts.netloc, 'haneoka.org')
        server, resource = parts.path.split('/')[-2:]
        ident = self.saved['identities'][server]
        query = parse_qs(parts.query)
        if resource == 'release':
            self.assertEqual(query, {'projection': ['identity']})
            value = ident
        else:
            self.assertEqual(query['release'], [ident['releaseId']])
            value = self.saved['documents'][server][resource]
            if 'id' in query:
                value = {'items': {key: value[key] for key in query['id']}, 'missing': []}
        return catalog.encoded(value), {'x-haneoka-release-id': ident['releaseId'],
                                        'x-haneoka-source-id': ident['sourceId']}

    def repository(self):
        return SongRepository('https://bdon.yatta.moe', self.root / 'old.json',
                              data_source='haneoka', chart_source='haneoka')

    def hosts(self):
        return {urlsplit(call['url']).netloc for call in self.calls}

    def test_catalog_detail_refresh_and_failure_never_fetch_old_source(self):
        old = self.root / 'old.json'
        old.write_bytes(b'old cache must remain untouched')
        repo = self.repository()
        self.assertFalse(self.calls)  # Construction is not startup networking.
        repo.load()
        card, snap = repo.cards[0], repo.support_cards[0]
        before = len(self.calls)
        self.assertIs(repo.card_with_detail(card), card)
        self.assertIs(repo.support_card_with_detail(snap), snap)
        self.assertEqual(len(self.calls), before)
        self.transport_failed = True
        repo.refresh()
        self.assertEqual(repo.cache_state, 'stale')
        self.assertEqual(self.hosts(), {'haneoka.org'})
        self.assertEqual(old.read_bytes(), b'old cache must remain untouched')
        cold = SongRepository('https://bdon.yatta.moe', self.root / 'cold' / 'old.json', data_source='haneoka')
        with self.assertRaises(DataError):
            cold.load()
        self.assertEqual(self.hosts(), {'haneoka.org'})

    def test_pinned_image_corrupt_cache_refetch_and_failure_stay_haneoka(self):
        songs, cards, snaps = catalog.validate(self.saved, 1100)[0]
        urls = (songs[0].jacket_url, cards[0].thumbnail_url, snaps[0].full_url)
        def asset(url):
            release = parse_qs(urlsplit(url).query)['release'][0]
            return self.png, {'x-haneoka-release-id': release, 'x-haneoka-source-id': 'synthetic'}
        self.handler = asset
        for url in urls:
            path = self.root / 'haneoka-asset-cache' / (hashlib.sha256(url.encode()).hexdigest() + '.png')
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'broken image cache')
            result = visuals._asset(url, (32, 32))
            self.assertIsNotNone(result)
            result.close()
            path.write_bytes(b'broken image cache')
            self.transport_failed = True
            self.assertIsNone(visuals._asset(url, (32, 32)))
            self.transport_failed = False
        self.assertEqual(self.hosts(), {'haneoka.org'})
        self.assertEqual(len(self.calls), 6)
        self.assertTrue(all(c['transport'] == 'aiohttp' for c in self.calls))

    def test_chart_failure_uses_own_cache_and_manual_legacy_cache_survives(self):
        fixture = chart_fixture.Responses()
        self.handler = lambda url: fixture(url, 2_000_000, 10)
        chart = Chart('EXPERT', 9, 9.0, 23, 'ignored')
        song = Song(100014, '测试曲', ('テスト', '测试曲'), '', '', '', '', '', '', (chart,))
        old = self.root / '0014_0014_03.json'
        old.write_bytes(chart_fixture.encoded(chart_fixture.SCORE))
        original = old.read_bytes()
        self.assertEqual(load_chart_data(song, chart, self.root, source='haneoka').source, 'haneoka')
        self.transport_failed = True
        with patch.object(chart_data, 'CHART_CACHE_TTL', 0):
            stale = load_chart_data(song, chart, self.root, source='haneoka')
        self.assertEqual(stale.cache_state, 'stale')
        before = len(self.calls)
        legacy = load_chart_data(song, chart, self.root, source='moenotes')
        self.assertEqual(legacy.score, stale.score)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(self.hosts(), {'haneoka.org'})

    def test_four_server_sampler_and_outage_never_call_tracker(self):
        fixture = cutoff_fixture.Fixture()
        from test_haneoka_event_metadata import StaticFixture
        metadata = StaticFixture()
        self.handler = lambda url: fixture(url, 4) if '/game/records/' in url else metadata(url, 512000, 4)
        repo = self.repository()
        settings = SimpleNamespace(cutoff_source='haneoka', cache_file=repo.cache_file,
                                   cutoff_history_file=None, moenotes_open_history_file=None,
                                   cutoff_history_enabled=True, cutoff_history_min_free_mb=0)
        configure_sources(repo, settings)
        source = repo.event_cutoffs
        source.clock = source.monotonic = lambda: fixture.now
        source.metadata.clock = source.metadata.monotonic = lambda: fixture.now
        sampler = HistorySampler(source)
        sampler.sample_once()
        self.assertEqual(source.history.status()['points'], 4)
        self.assertEqual({urlsplit(c['url']).path.split('/')[5] for c in self.calls if '/game/records/' in c['url']}, {'jp', 'tw', 'kr', 'en'})
        self.assertEqual(len(self.calls), 14)
        fixture.now += 61
        self.transport_failed = True
        sampler.sample_once()
        self.assertEqual(self.hosts(), {'haneoka.org'})
        self.assertEqual(source.history.status()['points'], 4)
        self.assertFalse((self.root / 'moenotes-history-v1.sqlite3').exists())

    def test_haneoka_rendering_uses_only_captured_haneoka_art(self):
        songs, cards, snaps = catalog.validate(self.saved, 1100)[0]
        def asset(url):
            parts = urlsplit(url)
            headers = {'content-type': 'image/png'}
            if parts.netloc == 'haneoka.org' and parts.query:
                headers.update({'x-haneoka-release-id': parse_qs(parts.query)['release'][0],
                                'x-haneoka-source-id': 'synthetic'})
            return self.png, headers
        self.handler = asset
        with patch('ournotes_bot.rendering.member_list_visuals.get_snapshot', side_effect=AssertionError('unexpected summary refresh')) as summary:
            for output in (visuals.render_song_list(songs[:1], ''),
                           visuals.render_card_list(cards[:1], ''), visuals.render_card(cards[0]),
                           visuals.render_support_card_list(snaps[:1], ''), visuals.render_support_card(snaps[0])):
                self.assertTrue(output)
            summary.assert_not_called()
        self.assertEqual(self.hosts(), {'haneoka.org'})
        old_urls = [c['url'] for c in self.calls if urlsplit(c['url']).netloc == 'bdon.yatta.moe']
        self.assertFalse(old_urls)

    def test_current_meta_query_and_background_refresh_retain_old_statistics(self):
        data = music_fixture.payload(2)
        self.handler = lambda url: (json.dumps(data).encode(), {'content-type': 'application/json'})
        repo = self.repository()
        request = parse_efficiency('/查分数表', repo, direct=True)
        answer = execute_efficiency(request, repo)
        self.assertTrue(request.meta_request.compare_scenes)
        self.assertTrue(answer.text)
        self.assertEqual(self.hosts(), {'storage.bdon.moe'})
        snapshot = repo.music_data.get()
        jacket = snapshot.records[0].jacket_url
        self.assertEqual(urlsplit(jacket).netloc, 'assets.bdon.moe')
        self.handler = lambda url: (self.png, {'content-type': 'image/png'})
        picture = visuals._asset(jacket, (32, 32))
        self.assertIsNotNone(picture)
        picture.close()
        self.handler = lambda url: (json.dumps(data).encode(), {'content-type': 'application/json'})
        fresh = music.MusicDataRepository(self.root / 'background.json')
        worker = music.MusicDataRefresher(fresh)
        async def one_cycle(_):
            worker.stopping.set()
        with patch('ournotes_bot.sources.moenotes_music_data.asyncio.sleep', side_effect=one_cycle):
            asyncio.run(worker.run())
        self.assertEqual(self.hosts(), {'storage.bdon.moe', 'assets.bdon.moe'})
        self.assertEqual(sum(c['url'] == music.URL for c in self.calls), 2)

    def test_cutoff_default_is_independent_of_haneoka_catalog_selection(self):
        fixture = tracker_fixture.PublicFixture()
        self.handler = lambda url: fixture(url, 4)
        source = self.repository().event_cutoffs
        source.clock = source.monotonic = lambda: fixture.now
        event = source.event('jp', source.deadline())
        board = source.board(event, event.songs[0], source.deadline())
        self.assertIsNotNone(board.score(1))
        self.assertEqual(self.hosts(), {'api.bdon.moe', 'metadata.bdon.moe', 'assets.bdon.moe'})

    def test_site_meta_query_and_background_refresh_use_only_haneoka(self):
        from test_haneoka_site_meta import documents
        from ournotes_bot.sources.haneoka.site_meta import SiteMetaRepository
        from ournotes_bot.data import Song, Chart
        ident, songs, meta = documents()
        def response(url):
            value = ident if '/release?' in url else songs if '/songs?' in url else meta
            return json.dumps(value).encode(), {'x-haneoka-release-id': ident['releaseId'],
                'x-haneoka-source-id': ident['sourceId']}
        self.handler = response
        repo = self.repository()
        repo.meta_source = 'haneoka-site'
        repo.song_meta = SiteMetaRepository(self.root/'site-meta.json')
        repo.songs = [Song(int(key), s['musicTitle'][0], tuple(s['musicTitle']), 'MyGO!!!!!', '', '', '', '', '',
            (Chart('EXPERT',25,25,100,''),)) for key,s in songs.items()]
        request = parse_efficiency('/查分数表', repo, direct=True)
        answer = execute_efficiency(request, repo)
        self.assertEqual(len(answer.panels), 2)
        self.assertTrue(all(panel.rows for panel in answer.panels))
        fresh = SiteMetaRepository(self.root/'site-background.json')
        worker = music.MusicDataRefresher(fresh)
        async def one_cycle(_):
            worker.stopping.set()
        with patch('ournotes_bot.sources.moenotes_music_data.asyncio.sleep', side_effect=one_cycle):
            asyncio.run(worker.run())
        self.assertEqual(self.hosts(), {'haneoka.org'})
        self.assertEqual(len(self.calls), 6)
