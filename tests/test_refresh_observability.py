"""Published cache facts drive both /数据状态 and background refresh logs."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.commands import handle_command
from ournotes_bot.data import SongRepository
from ournotes_bot.platforms.qq.qq import _refresh_catalog
from ournotes_bot.sources.haneoka.catalog import CatalogRepository


class RefreshObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = SongRepository('', root / 'cache.json', data_source='haneoka')
        self.source = CatalogRepository(root / 'catalog.json', clock=lambda: 1100)
        self.saved = json.loads((Path(__file__).parent / 'fixtures/haneoka_catalog.json').read_bytes())['snapshot']
        self.source.saved = copy.deepcopy(self.saved)
        self.repo._catalog_repository = self.source
        self.repo.load()

    async def test_real_source_states_match_status_command(self):
        for mode, expected, log_text, status_text in (
            ('fresh', 'fresh', '已取得并保存', '本次运行同步成功'),
            ('stale', 'stale', '继续使用旧缓存', '正在使用旧缓存'),
            ('unsaved', 'unsaved', '本地缓存保存失败', '未能保存缓存'),
        ):
            with self.subTest(mode=mode):
                collect = {'side_effect': OSError('offline')} if mode == 'stale' else {'return_value': copy.deepcopy(self.saved)}
                write = {'side_effect': OSError('disk full')} if mode == 'unsaved' else {'return_value': None}
                with patch.object(self.source, '_collect', **collect), \
                     patch.object(self.source, '_write', **write), \
                     self.assertLogs('ournotes_bot.qq', level='INFO') as logs:
                    await _refresh_catalog(self.repo)
                self.assertEqual(self.repo.capture_catalog().cache_state, expected)
                text = '\n'.join(logs.output)
                self.assertIn(log_text, text)
                self.assertIn(status_text, handle_command('/数据状态', self.repo))
                if mode != 'fresh':
                    self.assertNotIn('刷新成功', text)

    async def test_cached_unknown_and_exceptions_never_claim_refresh_success(self):
        for state, fail, expected in (
            ('cached', False, '使用已验证缓存'), ('unknown', False, '结果未确认'),
            ('unknown', True, '刷新失败'), ('fresh', True, '刷新失败'),
            ('unsaved', True, '本地缓存保存失败'),
        ):
            with self.subTest(state=state, fail=fail):
                self.repo.cache_state = state
                with patch.object(self.repo, 'refresh', side_effect=OSError('failure') if fail else None), \
                     self.assertLogs('ournotes_bot.qq', level='INFO') as logs:
                    await _refresh_catalog(self.repo)
                text = '\n'.join(logs.output)
                self.assertIn(expected, text)
                self.assertNotIn('刷新成功', text)

    async def test_cold_failure_without_verified_cache_is_not_success(self):
        self.source.saved = None
        self.repo.songs = []
        self.repo.cache_state = 'unknown'
        with patch.object(self.source, '_collect', side_effect=OSError('offline')), \
             self.assertLogs('ournotes_bot.qq', level='ERROR') as logs:
            await _refresh_catalog(self.repo)
        text = '\n'.join(logs.output)
        self.assertIn('刷新失败', text)
        self.assertNotIn('刷新成功', text)
