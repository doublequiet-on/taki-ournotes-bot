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
    def test_old_catalog_miss_keeps_stale_state_at_query_time(self):
        from types import SimpleNamespace
        self.repo.data_source = 'haneoka'
        self.repo.cache_state = 'fresh'
        self.repo._published_source_snapshot = {'fetched_at': 100}
        self.repo._catalog_repository = SimpleNamespace(clock=lambda: 100000)
        captured = self.repo.capture_query_view()
        self.assertEqual(captured.cache_state, 'stale')
        result = execute(parse_filter('不存在的歌曲 蓝色'), captured)
        self.assertIn('旧缓存', result.warning)
        self.assertTrue(result.unknown_entity)

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

    def test_direct_mission_spellings_select_the_same_songs(self):
        self.repo.songs.append(replace(self.repo.songs[0], id=100005, traits=SongTraits(2, ('COMBO', 'COMBO'))))
        cases = (
            ('激奏=JUST', ('just', '激奏 jUsT', '击奏 JuSt', '激奏:just', '激奏just'), [100001, 100002, 100003]),
            ('激奏=COMBO', ('combo', '激奏 CoMBo'), [100001, 100002, 100005]),
            ('激奏=LUCK', ('lUcK', '击奏 lucK'), [100002]),
            ('MyGO 激奏=纯COMBO', ('MyGO 纯combo', 'MyGO 激奏 纯combo'), [100005]),
            ('激奏=混合', ('混合', '激奏 混合'), [100001, 100002]),
            ('激奏=包含全部JUST/COMBO', ('包含全部just/combo', '激奏 包含全部JUST/combo'), [100001, 100002]),
            ('激奏=JUST/JUST/COMBO', ('just/JuSt/coMBo', '激奏 just / JuSt / coMBo'), [100001]),
            ('激奏=JUST/COMBO/JUST', ('just/combo/just',), []),
            ('乐队=卖狗 蓝色 激奏=JUST EX lv=25', ('乐队=卖狗 蓝色 jUsT EX lv=25', '蓝色 MyGO 激奏 just EX 25'),
             [100001, 100003]),
        )
        for canonical, aliases, ids in cases:
            answer = resolve_command('/查曲 ' + canonical, self.repo).song_selection
            for query in (canonical, *aliases):
                with self.subTest(query=query):
                    result = resolve_command('/查曲 ' + query, self.repo)
                    self.assertEqual([song.id for song in result.songs], ids)
                    self.assertEqual(result.song_selection.request.mode, answer.request.mode)
                    self.assertEqual(result.song_selection.request.missions, answer.request.missions)

    def test_mission_markers_are_independent_and_complete_titles_take_priority(self):
        for query, expected in (
            ('激奏', '请指定激奏条件的值'),
            ('击奏:', '请指定激奏条件的值'),
            ('激奏 unknown', '激奏条件无法识别'),
            ('just combo', '请只给出一个激奏条件'),
            ('just 激奏 combo', '请只给出一个激奏条件'),
            ('激奏 激奏 just', '请只给出一个激奏条件'),
        ):
            with self.subTest(query=query):
                result = resolve_command('/查曲 ' + query, self.repo)
                self.assertEqual(result.status, 'invalid_arguments')
                self.assertIn(expected, result.hint)
        base = self.repo.songs[0]
        self.repo.songs = [
            replace(base, id=song_id, title=title, titles=(title,))
            for song_id, title in ((100001, 'JUSTICE Ahead'), (100002, 'JUST'),
                                   (100003, 'Just/COMBO'), (100004, 'A Just/COMBO Adventure'))
        ]
        for query, song_id in (('JUSTICE', 100001), ('just', 100002),
                               ('jUsT/cOmBo', 100003), ('a just/combo Adventure', 100004),
                               ('A Just/COMBO Adventure 蓝色', 100004)):
            with self.subTest(query=query):
                result = resolve_command('/查曲 ' + query, self.repo)
                self.assertEqual([song.id for song in result.songs], [song_id])
                if result.song_selection:
                    self.assertFalse(result.song_selection.request.mode)

    def test_direct_chinese_level_conditions_match_symbol_comparisons(self):
        base = self.repo.songs[0]
        hard, expert = base.charts
        self.repo.songs = [
            base,
            replace(base, id=100002, charts=(hard, replace(expert, level=25, display_level=25.5))),
            replace(base, id=100003, charts=(hard, replace(expert, level=27, display_level=27.5))),
            replace(base, id=100004, charts=(replace(hard, level=24, display_level=24),)),
            replace(base, id=100005, charts=(replace(hard, level=29, display_level=29), expert)),
        ]
        for aliases, canonical, ids in (
            (('25以上', '25级以上'), 'lv>=25', [100001, 100002, 100003, 100005]),
            (('25以下', '25级以下'), 'lv<=25', [100001, 100005]),
            (('大于25', '大于25级'), 'lv>25', [100002, 100003]),
            (('小于25', '小于25级'), 'lv<25', []),
            (('27.5以下', '27.5级以下'), 'lv<=27.5', [100001, 100002, 100003, 100005]),
        ):
            expected = resolve_command('/查曲 MyGO EX ' + canonical + ' 激奏=JUST', self.repo)
            for condition in (*aliases, canonical):
                with self.subTest(condition=condition):
                    result = resolve_command('/查曲 MyGO EX ' + condition + ' just', self.repo)
                    self.assertEqual([song.id for song in result.songs], ids)
                    self.assertEqual(result.song_selection.request, expected.song_selection.request)
        self.assertEqual([song.id for song in resolve_command('/song MyGO 25以下', self.repo).songs],
                         [100001, 100004])
        self.assertEqual([song.id for song in resolve_command('/曲 MyGO EX 25以下', self.repo).songs],
                         [100001, 100005])
        self.assertEqual([song.id for song in resolve_command('/查曲 MyGO 25', self.repo).songs],
                         [100001, 100002, 100005])
        for condition, hint in (('25以下 lv>24', '一个等级条件'), ('大于24 27级以下', '一个等级条件'),
                                ('0以下', '等级须'), ('大于41级', '等级须'),
                                ('-1以上', '等级须'), ('27..5以下', '等级须')):
            with self.subTest(condition=condition):
                result = resolve_command('/查曲 MyGO ' + condition, self.repo)
                self.assertEqual(result.status, 'invalid_arguments')
                self.assertIn(hint, result.hint)
        title = replace(base, id=100006, title='25以下', titles=('25以下',))
        self.repo.songs.append(title)
        self.assertEqual(resolve_command('/查曲 25以下', self.repo).songs, (title,))
        filtered = resolve_command('/查曲 25以下 蓝色', self.repo)
        self.assertEqual(filtered.songs, (title,))
        self.assertIsNone(filtered.song_selection.request.level)

    def test_missing_stale_unknown_entity_and_empty_are_distinct(self):
        self.assertIn('缺少', handle_command('/查曲 蓝色', self.repo))
        self.repo.songs = [replace(self.repo.songs[0], traits=None)]
        self.assertIn('暂不可用', handle_command('/查曲 蓝色', self.repo))
        self.repo.songs = [replace(self.repo.songs[0], traits=SongTraits(2, ('JUST',), True))]
        self.assertIn('旧缓存', handle_command('/查曲 蓝色', self.repo))
        self.assertNotIn('暂不可用', handle_command('/查曲 不存在的歌曲 蓝色', self.repo))
        self.assertIn('没有符合', handle_command('/查曲 红色', self.repo))

    def test_incomplete_filter_distinguishes_confirmed_and_unknown_songs(self):
        blue = self.repo.songs[0]
        red = replace(blue, id=100002, traits=SongTraits(1, ('JUST',)))
        missing_color = replace(blue, id=100003, traits=SongTraits(None, ('JUST',)))
        missing_missions = replace(blue, id=100004, traits=SongTraits(2, None))
        command = '/查曲 蓝色 MyGO EX 激奏=JUST'
        cases = (
            ([blue, red, missing_missions], 'success', (100001,), '有 1 首歌曲缺少所需激奏资料'),
            ([red], 'empty', (), '没有符合条件的歌曲。'),
            ([red, missing_color], 'empty', (), '现有资料中未确认有歌曲符合条件。'),
            ([missing_color], 'data_unavailable', (), '缺少颜色）'),
            ([missing_missions], 'data_unavailable', (), '缺少激奏）'),
            ([missing_color, missing_missions], 'data_unavailable', (), '缺少颜色／激奏）'),
        )
        for songs, status, ids, message in cases:
            with self.subTest(ids=[song.id for song in songs]):
                self.repo.songs = songs
                result = resolve_command(command, self.repo)
                text = handle_command(command, self.repo, result)
                self.assertEqual(result.status, status)
                self.assertEqual(tuple(song.id for song in result.songs), ids)
                self.assertIn(message, text)
                if status == 'data_unavailable':
                    self.assertTrue(text.startswith('这次无法判断哪些歌曲符合条件。'))
                if songs == [red, missing_color]:
                    self.assertIn('有 1 首歌曲缺少所需颜色资料，未参与筛选。', text)

    def test_direct_song_keywords_use_one_title_variant_and_all_filters(self):
        base = replace(self.repo.songs[0], title='晴空下尚未写完的乐章', titles=(),
                       localized={'title': {'en': 'The Unfinished Sky Sonata', 'ja': '青空に残る楽章'}})
        other = replace(base, id=100005, title='晴空写给乐章', localized={}, band='Ave Mujica')
        self.repo.songs = [
            other, replace(base, id=100002, title='晴空散步', localized={'title': {'ja': '我的乐章'}}),
            replace(base, id=100003, traits=SongTraits(1, ('JUST',))),
            replace(base, id=100004, charts=(base.charts[0],)),
            base, base,
        ]
        for command, expected in (
            ('/查曲 晴空 乐章', [100001, 100003, 100004, 100005]),
            ('/查曲 晴空 乐章 EX 颜色=蓝', [100001, 100005]),
            ('/查曲 晴空 乐章 EX 颜色=蓝 乐队=卖狗 激奏=JUST lv=25', [100001]),
            ('/查曲 晴空 乐章 25', [100001, 100003, 100005]),
            ('/song Sky Unfinished', [100001, 100003, 100004]),
            ('/曲 青空 楽章', [100001, 100003, 100004]),
        ):
            with self.subTest(command=command):
                self.assertEqual([song.id for song in resolve_command(command, self.repo).songs], expected)

    def test_direct_song_preserves_exact_entities_before_reading_conditions(self):
        base = replace(self.repo.songs[0], title='晴空 EX 25', titles=())
        self.repo.songs = [base, replace(base, id=100002, title='晴空遥远的夜', charts=(base.charts[0],)),
                           replace(base, id=100003, title='Interlude Page 2', band='Ave Mujica', charts=())]
        for query in ('100001', '晴空 EX 25', 'まよいうた', '晴空 EX 25 颜色=蓝', '25'):
            with self.subTest(query=query):
                self.assertEqual([song.id for song in resolve_command('/查曲 ' + query, self.repo).songs], [100001])
        self.assertEqual([song.id for song in resolve_command('/查曲 卖狗', self.repo).songs], [100001, 100002])
        self.assertEqual([song.id for song in resolve_command('/查曲 遥远的夜', self.repo).songs], [100002])
        self.assertEqual(resolve_command('/查曲 Interlude Page 2', self.repo).parsed,
                         ('songs', 'Interlude Page 2', 1))

    def test_direct_song_typo_suggestions_require_id_and_respect_filters(self):
        base = replace(self.repo.songs[0], title='Stellar Odyssey', titles=('Stellar Odyssey',))
        blue = replace(base, id=100005, title='Stellar Odyssea', titles=('Stellar Odyssea',))
        red = replace(base, id=100003, title='Stellar Odyssez', titles=('Stellar Odyssez',),
                      traits=SongTraits(1, ('JUST',)))
        no_ex = replace(base, id=100004, title='Stellar Odyssex', titles=('Stellar Odyssex',), charts=(base.charts[0],))
        self.repo.songs = [red, no_ex, blue, base]
        command = '/查曲 Stelar Odyssey EX 颜色=蓝'
        answer = resolve_command(command, self.repo)
        self.assertFalse(answer.songs)
        self.assertEqual(answer.status, 'ambiguous')
        self.assertEqual([song.id for song in answer.song_selection.suggestions], [100001, 100005])
        text = handle_command(command, self.repo, answer)
        self.assertIn('可能是这些歌曲', text)
        self.assertIn('Stellar Odyssey · MyGO!!!!! · ID 100001', text)
        self.assertNotIn('100003', text)
        self.assertNotIn('100004', text)
        self.repo.songs = [base]
        self.assertEqual([song.id for song in resolve_command('/查曲 Stelar Odyssey', self.repo).candidates], [100001])
        self.assertEqual([song.id for song in resolve_command('/查曲 Stelar Odyssey', self.repo,
                                                             direct_song_keywords=False).songs], [100001])
        for term in ('St', 'QX', 'Od'):
            tip = resolve_command('/查曲 ' + term + 'q', self.repo)
            self.assertFalse(tip.songs or tip.candidates)
        self.assertIsNone(resolve_command('/查谱面 Stelar Odyssey', self.repo).song_selection)
        self.repo.songs = [replace(base, id=song_id) for song_id in range(100013, 100000, -1)]
        capped = resolve_command(command, self.repo)
        self.assertEqual([song.id for song in capped.candidates], list(range(100001, 100011)))
        self.repo.songs = [replace(base, traits=None)]
        self.repo.cache_state = 'stale'
        unavailable = resolve_command(command, self.repo)
        self.assertEqual(unavailable.status, 'data_unavailable')
        text = handle_command(command, self.repo, unavailable)
        self.assertIn('缺少颜色', text)
        self.assertIn('旧缓存', text)

    def test_direct_three_han_title_typo_requires_id_but_literal_matches_win(self):
        from ournotes_bot.application.replies import prepare_reply

        base = self.repo.songs[0]
        self.repo.songs = [base]
        parser = AIQueryParser(Settings('', '', BASE, self.repo.cache_file, 6, ''))
        with patch('ournotes_bot.application.replies.visual_asset', return_value=None), \
             patch('ournotes_bot.sources.haneoka.game_assets.asset_bytes', return_value=None), \
             patch('ournotes_bot.application.replies.load_chart_score', return_value=None):
            suggestion = prepare_reply('/查曲 迷星咬', self.repo, parser)
            self.assertEqual(suggestion.business_status, 'ambiguous')
            self.assertIn('迷星叫 · MyGO!!!!! · ID 100001', suggestion.text)
            self.assertIn('/查曲 ID', suggestion.text)
            self.assertIsNone(suggestion.image)
            self.assertIsNone(suggestion.context)
            exact = prepare_reply('/查曲 迷星叫', self.repo, parser)
            self.assertEqual(exact.business_status, 'success')
            self.assertTrue(exact.image)
            self.assertIn('迷星叫 · ID 100001', exact.text)
            for term in ('迷星', '迷 叫', 'まよいうた'):
                self.assertEqual(prepare_reply('/查曲 ' + term, self.repo, parser).business_status, 'success')
            for term in ('咬', '星咬', '星迷叫', '迷星A'):
                self.assertEqual(prepare_reply('/查曲 ' + term, self.repo, parser).business_status, 'empty')
        literal = replace(base, id=100002, title='迷星咬之歌', titles=('迷星咬之歌',))
        self.repo.songs = [base, literal]
        self.assertEqual(resolve_command('/查曲 迷星咬', self.repo).songs, (literal,))

    def test_direct_three_han_suggestions_count_distinct_ids_after_filters(self):
        base = self.repo.songs[0]
        other_variants = replace(base, id=100002, title='晴天下', titles=('晴天下', '迷星摇'),
                                 localized={'title': {'ja': '迷星音'}})
        third = replace(base, id=100003, title='迷星雨', titles=('迷星雨',))
        fourth = replace(base, id=100004, title='迷星梦', titles=('迷星梦',), traits=SongTraits(1, ('JUST',)))
        no_ex = replace(base, id=100005, title='迷星雪', titles=('迷星雪',), charts=base.charts[:1])
        wrong_band = replace(base, id=100006, title='迷星曲', titles=('迷星曲',), band='Ave Mujica')
        wrong_mission = replace(base, id=100007, title='迷星光', titles=('迷星光',), traits=SongTraits(2, ('LUCK',)))
        wrong_level = replace(base, id=100008, title='迷星舞', titles=('迷星舞',),
                              charts=(replace(base.charts[-1], level=26, display_level=26),))
        longer = replace(base, id=100009, title='迷星叫之歌', titles=('迷星叫之歌',))
        mixed = replace(base, id=100010, title='迷星A', titles=('迷星A',))
        self.repo.songs = [third, other_variants, mixed, longer, wrong_level, wrong_mission,
                           wrong_band, no_ex, fourth, base, base]
        for command, count in (('/查曲 迷星咬', 8), ('/查曲 迷星咬 EX 颜色=蓝', 6)):
            with self.subTest(command=command):
                result = resolve_command(command, self.repo)
                self.assertEqual(result.status, 'ambiguous')
                text = handle_command(command, self.repo, result)
                self.assertIn(f'{count} 首', text)
                self.assertIn('补充歌名', text)
                self.assertNotIn('ID ', text)
        command = '/查曲 迷星咬 EX 颜色=蓝 乐队=卖狗 激奏=JUST lv=25'
        result = resolve_command(command, self.repo)
        self.assertEqual([song.id for song in result.candidates], [100001, 100002, 100003])
        self.assertIn('/查曲 ID 查询详情', handle_command(command, self.repo, result))
        for song_id in (100001, 100002, 100003):
            self.assertIn(f'ID {song_id}', handle_command(command, self.repo, result))
        self.repo.cache_state = 'stale'
        self.assertIn('旧缓存', handle_command('/查曲 迷星咬', self.repo))
        self.repo.songs = [replace(base, traits=None)]
        result = resolve_command(command, self.repo)
        self.assertEqual(result.status, 'data_unavailable')
        self.assertIn('缺少颜色／激奏', handle_command(command, self.repo, result))
        self.repo.songs = [fourth]
        self.assertEqual(resolve_command(command, self.repo).status, 'empty')

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
        self.assertLessEqual(len(raw), 3_000_000)
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
        self.assertEqual(mapped[0].traits, SongTraits(1, ('COMBO', 'COMBO', 'COMBO'), repo.stale,
                                                     repo.saved['release'], repo.saved['source_version']))
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
