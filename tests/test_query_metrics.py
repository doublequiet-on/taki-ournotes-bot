"""Anonymous v2 outcome counts and an actual baseline-reader rollback round trip."""
import importlib.util
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.config import Settings
from ournotes_bot.data import SongRepository
from ournotes_bot.natural_query.ai_quota import QuotaUnavailable
from ournotes_bot.natural_query.query_metrics import QueryMetrics
from ournotes_bot.natural_query.query_validation import OutcomeCode
from ournotes_bot.sources.haneoka.song_traits import SongTraits
from ournotes_bot.sources.yatta import BASE, build_data
from test_query import CHARACTERS, CARDS, SONGS, META


class QueryMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = SongRepository(BASE, self.root / 'cache.json')
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, META)
        self.repo.songs = [replace(self.repo.songs[0], traits=SongTraits(2, ('JUST',)))]
        self.repo.metadata = {'cached_at': 'metrics-fixture', 'schema': self.repo.CACHE_SCHEMA,
                              'member_skill_index_complete': True}
        self.settings = Settings('', '', BASE, self.repo.cache_file, 6, 'synthetic-key',
            ai_metrics_file=self.root / 'metrics.json', ai_quota_file=self.root / 'quota.json')
        self.parser = AIQueryParser(self.settings)

    def bucket(self, parser=None):
        record = json.loads((parser or self.parser)._metrics.path.read_text(encoding='utf-8'))
        self.assertEqual(record['schema'], 2)
        return next(iter(record['days'].values()))

    def assert_one_outcome(self, parser, expected, route):
        bucket = self.bucket(parser)
        self.assertEqual(bucket['ask_total'], 1)
        self.assertEqual(bucket['outcome_' + expected], 1)
        self.assertEqual(sum(v for k, v in bucket.items() if k.startswith('outcome_')), 1)
        self.assertEqual(bucket['route_' + route], 1)
        self.assertEqual(sum(v for k, v in bucket.items() if k.startswith('route_')), 1)
        return bucket

    def test_local_branches_record_handling_parsing_and_final_state_separately(self):
        cases = (
            ('/问 MyGO的歌有哪些', 'success', True),
            ('/问 蓝色 lv>40的歌曲', 'empty', True),
            ('/问 查曲 红色', 'empty', True),
            ('/问 查曲 颜色=黑', 'invalid_arguments', False),
            ('/问 查曲 不存在歌曲 蓝色', 'unknown_entity', True),
            ('/问 100001 EX Note', 'success', True),
            ('/问 不存在歌曲 EX Note', 'unknown_entity', True),
            ('/问 预测活动积分', 'unsupported', False),
            ('/问', 'invalid_arguments', False),
            ('/问 继续', 'invalid_arguments', False),
            ('/问 颜色=黑 成员卡有多少张', 'invalid_arguments', False),
            ('/问 查榜线 hk id1 0', 'invalid_arguments', False),
        )
        for index, (question, code, parsed) in enumerate(cases):
            with self.subTest(question=question):
                parser = AIQueryParser(replace(self.settings, ai_metrics_file=self.root / f'm{index}.json'))
                with patch.object(parser, '_request', side_effect=AssertionError('model called')), \
                     patch.object(parser._quota, 'reserve', side_effect=AssertionError('quota used')):
                    outcome = parser.answer_with_outcome(question, self.repo)
                self.assertEqual(outcome.code.value, code)
                bucket = self.assert_one_outcome(parser, code, 'local')
                self.assertEqual(bucket['local_handled'], 1)
                self.assertEqual(bucket.get('local_parsed', 0), int(parsed))
                self.assertEqual(bucket.get('local_success', 0), int(code == 'success'))
                if code == 'empty':
                    self.assertNotIn('local_terminal_reject', bucket)

    def test_local_ambiguous_and_missing_data_are_not_success(self):
        self.repo.songs.append(replace(self.repo.songs[0], id=100002))
        outcome = self.parser.answer_with_outcome('/问 迷星叫 EX Note', self.repo)
        self.assertEqual(outcome.code, OutcomeCode.AMBIGUOUS)
        self.assert_one_outcome(self.parser, 'ambiguous', 'local')
        parser = AIQueryParser(replace(self.settings, ai_metrics_file=self.root / 'missing.json'))
        self.repo.songs = [replace(self.repo.songs[0], traits=None)]
        outcome = parser.answer_with_outcome('/问 蓝色的歌曲', self.repo)
        self.assertEqual(outcome.code, OutcomeCode.DATA_UNAVAILABLE)
        bucket = self.assert_one_outcome(parser, 'data_unavailable', 'local')
        self.assertEqual(bucket['local_parsed'], 1)
        self.assertNotIn('local_success', bucket)

    def test_cached_plans_and_commands_use_current_outcome_in_both_directions(self):
        for index, question in enumerate(('/问 蓝色的歌曲', '/问 查曲 蓝色')):
            with self.subTest(question=question):
                parser = AIQueryParser(replace(self.settings, ai_metrics_file=self.root / f'cache{index}.json'))
                base = replace(self.repo.songs[0], traits=SongTraits(2, ('JUST',)))
                self.repo.songs = [base]
                self.assertEqual(parser.answer_with_outcome(question, self.repo).code, OutcomeCode.SUCCESS)
                self.repo.songs = [replace(base, traits=SongTraits(1, ('JUST',)))]
                empty = parser.answer_with_outcome(question, self.repo)
                self.assertEqual(empty.code, OutcomeCode.EMPTY)
                self.assertEqual(empty.state.processing_route, 'cache')
                self.repo.songs = [base]
                self.assertEqual(parser.answer_with_outcome(question, self.repo).code, OutcomeCode.SUCCESS)
                bucket = self.bucket(parser)
                self.assertEqual(bucket['ask_total'], 3)
                self.assertEqual(bucket['outcome_success'], 2)
                self.assertEqual(bucket['outcome_empty'], 1)
                self.assertEqual(bucket['cache_hit_terminal'], 1)
                self.assertEqual(bucket['cache_hit_success'], 1)
                self.assertEqual(bucket['local_parsed'], 1)

    def test_terminal_cache_does_not_repeat_local_parse_counts(self):
        question = '/问 推荐最强卡'
        self.parser.answer(question, self.repo)
        self.parser.answer(question, self.repo)
        bucket = self.bucket()
        self.assertEqual(bucket['outcome_unsupported'], 2)
        self.assertEqual(bucket['local_handled'], 1)
        self.assertEqual(bucket['cache_hit_terminal'], 1)
        self.assertNotIn('local_parsed', bucket)

    def test_model_repair_counts_requests_but_only_one_final_outcome(self):
        question = '/问 展示迷星叫 EXPERT 完整谱面资料'
        def action(difficulty):
            return {'action': 'call_tool', 'capability': 'chart.get',
                    'arguments': {'query': '迷星叫', 'difficulty': difficulty}}
        with patch.object(self.parser, '_request', side_effect=[action('MASTER'), action('EXPERT')]):
            outcome = self.parser.answer_with_outcome(question, self.repo)
        self.assertEqual(outcome.code, OutcomeCode.SUCCESS)
        bucket = self.assert_one_outcome(self.parser, 'success', 'model')
        self.assertEqual(bucket['ai_parse_requested'], 1)
        self.assertEqual(bucket['ai_repair_requested'], 1)
        self.assertEqual(bucket['ai_success'], 1)
        self.assertNotIn('ai_invalid_output', bucket)
        self.assertEqual(json.loads(self.settings.ai_quota_file.read_text())['used'], 2)

    def test_provider_and_quota_failures_have_exact_final_codes(self):
        question = '/问 展示迷星叫 EXPERT 完整谱面资料'
        cases = (
            ('provider', 'temporary_failure', '_request', OSError('private data')),
            ('output', 'invalid_output', '_request', None),
            ('exhausted', 'quota_exhausted', 'reserve', False),
            ('quota', 'quota_unavailable', 'reserve', QuotaUnavailable('private data')),
        )
        for name, code, method, value in cases:
            with self.subTest(name=name):
                parser = AIQueryParser(replace(self.settings, ai_metrics_file=self.root / f'{name}.json'))
                target = parser if method == '_request' else parser._quota
                args = {'side_effect': value} if isinstance(value, Exception) else {'return_value': value}
                with patch.object(target, method, **args), \
                     patch.object(parser._agent, '_can_repair', return_value=False):
                    outcome = parser.answer_with_outcome(question, self.repo)
                self.assertEqual(outcome.code.value, code)
                bucket = self.assert_one_outcome(parser, code, 'model')
                self.assertNotIn('ai_success', bucket)
                self.assertNotIn('private data', parser._metrics.path.read_text())
        parser = AIQueryParser(replace(self.settings, ai_api_key='', ai_metrics_file=self.root / 'disabled.json'))
        self.assertEqual(parser.answer_with_outcome(question, self.repo).code, OutcomeCode.NOT_CONFIGURED)
        self.assert_one_outcome(parser, 'not_configured', 'local')

    def test_concurrency_keeps_one_final_bucket_per_request(self):
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')), \
             ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.parser.answer('/问 MyGO的歌有哪些', self.repo), range(40)))
        bucket = self.bucket()
        self.assertEqual(bucket['ask_total'], 40)
        self.assertEqual(bucket['outcome_success'], 40)
        self.assertEqual(bucket.get('route_local', 0) + bucket.get('route_cache', 0), 40)

    def test_corrupt_or_failed_v2_writes_do_not_break_queries(self):
        self.parser._metrics.path.write_text('{broken', encoding='utf-8')
        self.assertEqual(self.parser.answer_with_outcome('/问 MyGO的歌有哪些', self.repo).code, OutcomeCode.SUCCESS)
        self.assertFalse(self.parser._metrics.available)
        parser = AIQueryParser(replace(self.settings, ai_metrics_file=self.root / 'failed.json'))
        with patch.object(parser._metrics, '_write', side_effect=OSError('disk full')):
            self.assertEqual(parser.answer_with_outcome('/问 MyGO的歌有哪些', self.repo).code, OutcomeCode.SUCCESS)
        self.assertFalse(parser._metrics.available)

    def test_actual_v1_reader_writer_survives_v2_and_rollback(self):
        # Exact source from 8607541, not a reimplementation of the old reader.
        fixture = Path(__file__).parent / 'fixtures' / 'query_metrics_v1.py'
        spec = importlib.util.spec_from_file_location('ournotes_bot.natural_query._metrics_v1', fixture)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        old = module.QueryMetrics(self.settings.ai_metrics_file)
        self.assertTrue(old.increment_many({'ask_total': 5, 'local_success': 4}))
        old_bytes = old.path.read_bytes()
        self.repo._save_cache()
        cache_bytes = self.repo.cache_file.read_bytes()
        self.assertTrue(self.parser._quota.reserve(10))
        quota_bytes = self.settings.ai_quota_file.read_bytes()
        self.parser.answer('/问 MyGO的歌有哪些', self.repo)
        self.assertEqual(self.parser._metrics.path.name, 'metrics.v2.json')
        self.assertEqual(old.path.read_bytes(), old_bytes)
        self.assertEqual(self.repo.cache_file.read_bytes(), cache_bytes)
        self.assertEqual(self.settings.ai_quota_file.read_bytes(), quota_bytes)
        self.assertEqual(self.bucket()['ask_total'], 1)
        resumed = module.QueryMetrics(self.settings.ai_metrics_file)
        self.assertTrue(resumed.increment('ask_total'))
        self.assertEqual(next(iter(resumed._load()['days'].values()))['ask_total'], 6)
        restarted = AIQueryParser(self.settings)
        restarted.answer('/问 MyGO的歌有哪些', self.repo)
        self.assertEqual(self.bucket(restarted)['ask_total'], 2)

    def test_non_ask_calls_do_not_create_metrics(self):
        self.parser.answer('MyGO的歌有哪些', self.repo)
        self.assertFalse(self.parser._metrics.path.exists())
        self.assertEqual(QueryMetrics(self.root / 'no-extension').path.name, 'no-extension.v2')
