"""Retired score-table paths stay unavailable without source or model requests."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import ALIASES, HELP_TEXT, handle_command, resolve_command
from ournotes_bot.config import Settings
from ournotes_bot.data import Song, SongRepository
from ournotes_bot.natural_query.query_capabilities import CAPABILITIES
from ournotes_bot.natural_query.query_validation import OutcomeCode
from ournotes_bot.platforms.qq.menu import GROUP_PANEL_ITEMS, PANEL_ITEMS


class RemovedScoreTableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SongRepository('', Path(self.temp.name) / 'cache.json')
        self.parser = AIQueryParser(Settings('', '', '', self.repo.cache_file, 6, ''))

    def test_retired_entrypoints_and_modules_are_absent(self):
        for command in ('查分数表', '查效率'):
            self.assertNotIn(command, ALIASES)
            self.assertIsNone(resolve_command('/' + command, self.repo))
        self.assertNotIn('/查分数表', HELP_TEXT)
        self.assertNotIn('song.meta', CAPABILITIES)
        self.assertTrue(all(item['name'] != '查分数表' for item in GROUP_PANEL_ITEMS + PANEL_ITEMS))
        for name in ('query.efficiency_query', 'query.haneoka_meta_query',
                     'query.meta_parameters', 'query.meta_model',
                     'sources.haneoka.song_meta', 'sources.haneoka.site_meta',
                     'sources.moenotes_music_data'):
            self.assertIsNone(importlib.util.find_spec('ournotes_bot.' + name), name)
        for attribute in ('meta_source', 'song_meta', 'music_data'):
            self.assertFalse(hasattr(self.repo, attribute))

    def test_natural_score_requests_are_local_terminal_rejections(self):
        with patch.object(self.parser, '_request', side_effect=AssertionError('model called')):
            for question in ('查一下分数表', 'MyGO的EX效率前十有哪些',
                             '每分钟得分效率最高的歌曲', '查分数表 MyGO EX',
                             'MyGO meta', 'META', 'MyGO MeTa?', 'MyGO的meta？'):
                outcome = self.parser.answer_with_outcome('/问 ' + question, self.repo)
                self.assertEqual(outcome.code, OutcomeCode.UNSUPPORTED)
                self.assertIsNone(outcome.result)
                self.assertNotIn('/查分数表', outcome.text)

    def test_meta_requests_do_not_use_configured_ai_or_quota(self):
        parser = AIQueryParser(Settings('', '', '', self.repo.cache_file, 6, 'test-key'))
        # Keep an existing quota record unchanged, including when AI is enabled.
        self.assertTrue(parser._quota.reserve(20))
        before = parser._quota.path.read_bytes()
        with patch.object(parser, '_request', side_effect=AssertionError('model called')) as request, \
                patch.object(parser._quota, 'reserve', wraps=parser._quota.reserve) as reserve:
            for question in ('MyGO meta', 'META', 'MyGO MeTa?', 'MyGO的meta？',
                             'meta MyGO', '(meta)', 'MyGO meta EX'):
                with self.subTest(question=question):
                    outcome = parser.answer_with_outcome('/问 ' + question, self.repo)
                    self.assertEqual(outcome.code, OutcomeCode.UNSUPPORTED)
                    self.assertIsNone(outcome.result)
            request.assert_not_called()
            reserve.assert_not_called()
        self.assertEqual(parser._quota.path.read_bytes(), before)

    def test_meta_inside_an_entity_word_is_not_a_retired_request(self):
        for title in ('metadata', 'Metamorphosis', 'Xmeta', 'meta2', 'meta_song'):
            with self.subTest(title=title):
                self.repo.songs = [Song(100001, title, (title,), 'MyGO!!!!!',
                                        '', '', '', '', '', ())]
                with patch.object(self.parser, '_request', side_effect=AssertionError('model called')):
                    outcome = self.parser.answer_with_outcome('/问 查曲 ' + title, self.repo)
                self.assertEqual(outcome.code, OutcomeCode.SUCCESS)
                self.assertEqual(outcome.result.songs[0].title, title)

    def test_old_environment_setting_is_ignored(self):
        with patch('ournotes_bot.config.load_dotenv'), patch.dict('os.environ',
                {'OURNOTES_META_SOURCE': 'retired-source'}, clear=True):
            settings = Settings.from_env()
        self.assertFalse(hasattr(settings, 'meta_source'))
        self.assertEqual(settings.cutoff_source, 'haneoka')
