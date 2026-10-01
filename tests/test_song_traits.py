"""Offline synthetic fixtures; no production caches, QQ, or paid model calls."""
import copy
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import handle_command, resolve_command
from ournotes_bot.config import Settings
from ournotes_bot.data import Song, Chart, SongRepository
from ournotes_bot.sources.haneoka.song_traits import SongTraits, SongTraitsRepository, SOURCE, collect, validate, fetch_json
from ournotes_bot.query.song_query import parse_filter, execute
from ournotes_bot.sources.yatta import BASE


def snapshot():
    return {"schema": 1, "source": SOURCE, "server": "jp", "release": "r-synthetic", "source_version": "synthetic",
            "fetched_at": "2026-01-01T00:00:00+00:00", "ids": ["100001"], "rows": {
                "100001": {"id": 100001, "titles": ["迷星叫"], "jacket": "jkt_001_100001", "color": 2, "missions": [3, 3, 1]}}}


class SongTraitsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "traits.json"
        self.repo = SongRepository(BASE, Path(self.temp.name) / "main.json")
        base = Song(100001, "迷星叫", ("迷星叫",), "MyGO!!!!!", "", "", "", "", "https://example.test/jkt_001_100001.webp",
                    (Chart("HARD", 20, 20, 100, ""), Chart("EXPERT", 25, 25, 200, "")), traits=SongTraits(2, ("JUST", "JUST", "COMBO")))
        self.repo.songs = [base, replace(base, id=100002, traits=SongTraits(3, ("COMBO", "JUST", "LUCK"))),
                           replace(base, id=100003, traits=SongTraits(2, ("JUST", "JUST", "JUST"))),
                           replace(base, id=100004, traits=None)]

    def test_modes_preserve_order_and_repetitions(self):
        cases = {"激奏=JUST": [100001, 100002, 100003], "激奏=纯JUST": [100003], "激奏=混合": [100001, 100002],
                 "激奏=JUST/JUST/COMBO": [100001], "激奏=JUST/COMBO/JUST": [],
                 "激奏=包含全部JUST/COMBO": [100001, 100002], "颜色=蓝/绿 激奏=JUST EX lv<=25": [100001, 100002, 100003],
                 "蓝色 MyGO EX 激奏=JUST lv<25": [], "蓝色 MyGO HD 激奏=JUST lv<25": [100001, 100003]}
        for query, expected in cases.items():
            with self.subTest(query=query):
                result = resolve_command('/查曲 ' + query, self.repo)
                self.assertIsNotNone(result.song_selection)
                self.assertEqual([s.id for s in result.songs], expected)

    def test_invalid_conditions_never_broaden(self):
        for query in ('颜色=黑', '激奏=ALL', '激奏=纯JUST/COMBO', '激奏=JUST COMBO=1', '蓝色 绿色', '蓝色 EX HD', '蓝色 lv>99'):
            with self.subTest(query=query):
                result = resolve_command('/查曲 ' + query, self.repo)
                self.assertTrue(result.hint)
                self.assertFalse(result.songs)

    def test_missing_stale_unknown_entity_and_empty_are_distinct(self):
        self.assertIn('缺少', handle_command('/查曲 蓝色', self.repo))
        self.repo.songs = [replace(self.repo.songs[0], traits=None)]
        self.assertIn('暂不可用', handle_command('/查曲 蓝色', self.repo))
        self.repo.songs = [replace(self.repo.songs[0], traits=SongTraits(2, ('JUST',), True))]
        self.assertIn('旧缓存', handle_command('/查曲 蓝色', self.repo))
        self.assertNotIn('暂不可用', handle_command('/查曲 不存在的歌曲 蓝色', self.repo))
        self.assertIn('没有符合', handle_command('/查曲 红色', self.repo))

    def test_local_and_direct_identical_without_model(self):
        parser = AIQueryParser(Settings('', '', BASE, self.repo.cache_file, 6, ''))
        pairs = [('蓝色', '蓝色的歌有哪些'), ('MyGO 激奏=JUST', 'MyGO有哪些带JUST激奏的歌'),
                 ('激奏=纯COMBO', '哪些歌是纯COMBO'), ('激奏=COMBO/JUST/LUCK', '找激奏顺序是combo/just/luck的歌曲'),
                 ('蓝色 EX lv<=25', '蓝色EX 25级以下的歌曲'), ('蓝色 激奏=JUST', '蓝色 激奏=JUST的歌曲')]
        with patch.object(parser, '_request', side_effect=AssertionError('model called')) as model:
            for direct, natural in pairs:
                with self.subTest(natural=natural):
                    text, result = parser.answer_with_plan('/问 ' + natural, self.repo)
                    expected = resolve_command('/查曲 ' + direct, self.repo)
                    self.assertIsNotNone(result, text)
                    self.assertEqual(result.songs, expected.songs)
                    self.assertEqual(text, handle_command('/查曲 ' + direct, self.repo, expected))
            model.assert_not_called()

    def test_pagination_keeps_every_condition(self):
        self.repo.songs = [replace(self.repo.songs[0], id=i) for i in range(100001, 100039)]
        text = handle_command('/查曲 蓝色 MyGO EX 激奏=JUST 页2', self.repo)
        self.assertIn('页3', text)
        for part in ('颜色=蓝色', 'MyGO', 'EXPERT', '激奏=JUST'):
            self.assertIn(part, text)
        self.assertIn('页码超出', handle_command('/查曲 蓝色 页99', self.repo))

    def test_cache_validation_and_mapping_conflict(self):
        self.path.write_text(json.dumps(snapshot()), encoding='utf8')
        repo = SongTraitsRepository(self.path)
        self.assertEqual(repo.apply(self.repo.songs)[0].traits.missions, ('JUST', 'JUST', 'COMBO'))
        for song in (replace(self.repo.songs[0], titles=('other',), title='other'), replace(self.repo.songs[0], jacket_url='other.webp')):
            self.assertIsNone(repo.apply([song])[0].traits)
        for mutate in (lambda x: x['ids'].append('100002'), lambda x: x['ids'].append('100001'),
                       lambda x: x['rows']['100001'].update(color=float('nan')),
                       lambda x: x['rows']['100001'].update(missions=[3, 9]),
                       lambda x: x.update(rows={}), lambda x: x.update(server='intl')):
            broken = copy.deepcopy(snapshot()); mutate(broken)
            with self.assertRaises(ValueError): validate(broken)

    def test_stale_failure_and_complete_unavailable(self):
        self.path.write_text(json.dumps(snapshot()), encoding='utf8')
        fetch = Mock(side_effect=OSError('offline'))
        repo = SongTraitsRepository(self.path, fetch=fetch)
        old = self.path.read_bytes()
        repo.refresh(); repo.refresh()
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(repo.stale)
        self.assertEqual(repo.saved['rows'], snapshot()['rows'])
        self.assertEqual(self.path.read_bytes(), old)
        empty = SongTraitsRepository(self.path.with_name('empty.json'), fetch=fetch)
        empty.refresh()
        self.assertIsNone(empty.saved)

    def test_refresh_serializes_hits_and_updates(self):
        current = copy.deepcopy(snapshot())
        from datetime import datetime, timezone
        current['fetched_at'] = datetime.now(timezone.utc).isoformat()
        repo = SongTraitsRepository(self.path)
        with patch('ournotes_bot.sources.haneoka.song_traits.collect', return_value=current) as collect_mock:
            with ThreadPoolExecutor(4) as pool:
                list(pool.map(lambda _: repo.refresh(), range(4)))
            self.assertEqual(collect_mock.call_count, 1)
        repo2 = SongTraitsRepository(self.path, fetch=Mock(side_effect=AssertionError('cache hit')))
        repo2.refresh()
        self.assertEqual(repo2.saved, current)

    def test_main_cache_does_not_write_new_song_fields(self):
        self.repo.metadata = {'schema': 4, 'source': BASE}
        self.repo._save_cache()
        payload = json.loads(self.repo.cache_file.read_text(encoding='utf8'))
        self.assertNotIn('traits', payload['songs'][0])
        self.assertEqual(payload['metadata']['schema'], 2)

    def test_html_and_duplicate_json_rejected(self):
        response = Mock()
        response.headers = {'Content-Type': 'text/html'}
        response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        with patch('ournotes_bot.sources.haneoka.song_traits.urlopen', return_value=response):
            with self.assertRaises(ValueError): fetch_json('songs?release=r-test')
            response.headers = {'Content-Type': 'application/json'}
            response.read.return_value = b'{"a":1,"a":2}'
            with self.assertRaises(ValueError): fetch_json('songs?release=r-test')

    def test_images_and_text_share_traits_and_fit_budget(self):
        from ournotes_bot.visuals import render_song_list
        from PIL import ImageDraw
        drawn = []
        original = ImageDraw.ImageDraw.text
        def text(draw, xy, value, *args, **kwargs):
            drawn.append(str(value)); return original(draw, xy, value, *args, **kwargs)
        answer = execute(parse_filter('蓝色 激奏=JUST'), self.repo)
        with patch('ournotes_bot.visuals._asset', return_value=None), patch.object(ImageDraw.ImageDraw, 'text', text):
            raw = render_song_list(answer.songs, answer.request.query, footer=answer.footer)
        self.assertLessEqual(len(raw), 1500000)
        self.assertEqual([value for value in drawn if value in {'JUST', 'COMBO'}][:3],
                         ['JUST', 'JUST', 'COMBO'])
        self.assertTrue(any('缺少' in value for value in drawn))
        self.assertIn('JUST → JUST → COMBO', answer.text())

    def test_real_reference_rows_and_version_pinned_collection(self):
        evidence = json.loads((Path(__file__).parent / 'fixtures/haneoka-song-traits.json').read_text(encoding='utf8'))
        rows = evidence['rows']
        catalog = {k: {'musicId': r['id'], 'musicType': r['color'], 'musicTitle': r['titles'],
                       'jacketUrl': '/assets/' + r['jacket'] + '.png'} for k, r in rows.items()}
        seen = []
        def fetch(path):
            seen.append(path)
            if path == 'release?projection=identity':
                return {'schema': 'haneoka-resource-release-identity-v1', 'server': 'jp',
                        'releaseId': evidence['release'], 'sourceId': evidence['source_version']}
            self.assertTrue(path.endswith('?release=' + evidence['release']))
            if path.startswith('gekisou?'):
                return {'enums': {'missionType': {'sourceType': 'App.LiveBase.GekisouMissionType', 'values': {
                    '0': 'None', '1': 'Combo', '2': 'Luck', '3': 'JustCount', '4': 'All'}}}}
            if path.startswith('songs?'): return catalog
            key = path.split('/')[1].split('?')[0]
            return {**catalog[key], 'gekisou': {'missionTypes': rows[key]['missions']}}
        saved = collect(fetch, evidence['fetched_at'])
        self.assertEqual(saved['rows'], rows)
        repo = SongTraitsRepository(self.path); repo.saved = saved
        mapped = repo.apply([replace(self.repo.songs[0], traits=None)])
        self.assertEqual(mapped[0].traits, SongTraits(1, ('COMBO', 'COMBO', 'COMBO'), repo.stale))
        def incomplete(path):
            if path.startswith('songs/'): raise OSError('missing detail')
            return fetch(path)
        with self.assertRaises(OSError): collect(incomplete, evidence['fetched_at'])
        def conflict(path):
            value = fetch(path)
            return {**value, 'musicType': 5} if path.startswith('songs/') else value
        with self.assertRaises(ValueError): collect(conflict, evidence['fetched_at'])

    def test_result_cache_reexecutes_after_traits_update(self):
        parser = AIQueryParser(Settings('', '', BASE, self.repo.cache_file, 6, ''))
        with patch.object(parser, '_request', side_effect=AssertionError('model called')):
            before, result = parser.answer_with_plan('/问 蓝色的歌有哪些', self.repo)
            self.assertEqual(len(result.songs), 2)
            self.repo.songs = [replace(s, traits=SongTraits(3, ('LUCK',))) for s in self.repo.songs]
            self.repo.song_traits.saved = snapshot()
            after, result = parser.answer_with_plan('/问 蓝色的歌有哪些', self.repo)
            self.assertFalse(result.songs)
            self.assertNotEqual(before, after)

    def test_fractional_levels_and_original_routes(self):
        self.repo.songs = [replace(self.repo.songs[0], charts=(Chart('EXPERT', 27, 27.5, 200, ''),))]
        self.assertTrue(resolve_command('/查曲 蓝色 27', self.repo).songs)
        self.assertTrue(resolve_command('/查曲 蓝色 lv27.5', self.repo).songs)
        self.assertFalse(resolve_command('/查曲 蓝色 lv27.4', self.repo).songs)
        self.assertFalse(resolve_command('/查曲 蓝色 lv27.0', self.repo).songs)
        self.assertIn('lv=27.0', parse_filter('蓝色 lv27.0').query)
        self.assertIn('27.5', handle_command('/查谱面 1 EX', self.repo))
        self.assertIn('激奏', handle_command('/帮助', self.repo))
        self.assertIn('Taki', handle_command('/介绍', self.repo))
        self.assertIn('歌曲颜色', handle_command('/数据状态', self.repo))


if __name__ == '__main__':
    unittest.main()
