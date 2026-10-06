"""Automatic public-data refresh, model identity, and whole-snapshot fallback."""
import asyncio
import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_moenotes_meta import payload, make_snapshot
from ournotes_bot.commands import resolve_command
from ournotes_bot.data import SongRepository
from ournotes_bot.query.efficiency_query import execute_efficiency
from ournotes_bot.query.meta_parameters import parse_meta
from ournotes_bot.sources.moenotes_music_data import (
    MODEL_SOURCE_SHA256, TTL, MAX_STALE, MusicDataRepository, MusicDataRefresher,
)


class ModelIdentityTests(unittest.TestCase):
    def test_source_fingerprint_survives_release_changes(self):
        data = payload()
        data['provenance']['deck'].update(commit='future-release', version='0.0.99', sourceSha256=MODEL_SOURCE_SHA256)
        self.assertTrue(make_snapshot(data).model_supported)

    def test_unknown_explicit_fingerprint_cannot_use_legacy_commit(self):
        for source in ('f'*64, '', [], 42):
            data = payload()
            data['provenance']['deck']['sourceSha256'] = source
            self.assertFalse(make_snapshot(data).model_supported)
        data = payload()
        data['provenance']['deck'].update(format='ournotes-deck.chart-stats/3', sourceSha256=MODEL_SOURCE_SHA256)
        self.assertFalse(make_snapshot(data).model_supported)

    def test_legacy_model_without_fingerprint_remains_supported(self):
        self.assertTrue(make_snapshot().model_supported)
        data = payload()
        data['provenance']['deck']['commit'] = 'unknown'
        self.assertFalse(make_snapshot(data).model_supported)


class AutomaticDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = [1000.]
        self.data = payload()
        self.fetch = Mock(return_value=(200, json.dumps(self.data).encode(), {'etag': '"v1"'}))
        self.bot = SongRepository('', Path(self.temp.name)/'catalog.json')
        self.source = self.bot.music_data = MusicDataRepository(Path(self.temp.name)/'music.json', fetch=self.fetch, clock=lambda: self.now[0])

    def update(self, data, etag='"v2"'):
        self.now[0] += TTL
        self.fetch.return_value = 200, json.dumps(data).encode(), {'etag': etag}
        return self.source.get()

    def unknown(self):
        data = copy.deepcopy(self.data)
        data['provenance']['deck'].update(commit='new-model', sourceSha256='f'*64)
        data['songs'][0]['title']['ja'] = '新名字'
        data['songs'][0]['charts'][0]['notes']['judged'] = 777
        return data

    def test_new_data_changes_values_and_restores_after_unknown_model(self):
        old = self.source.get()
        unknown = self.update(self.unknown())
        self.assertEqual(self.source.for_calculation(unknown).version, old.version)
        data = copy.deepcopy(self.data)
        data['provenance']['deck'].update(commit='next-release', sourceSha256=MODEL_SOURCE_SHA256)
        for chart in data['songs'][0]['charts']:
            chart['deck']['offSeeds'][0]['score'] = 900
        new = self.update(data)
        self.assertNotEqual(new.version, old.version)
        self.assertIs(self.source.for_calculation(new), new)
        result = resolve_command('/查分数表 迷星叫 单人', self.bot).meta
        self.assertGreater(result.rows[0].score, .6)
        self.assertFalse(any('暂用上次' in n for n in result.notes))

    def test_fallback_is_whole_snapshot_and_basic_facts_use_latest(self):
        old = self.source.get()
        latest = self.update(self.unknown())
        request = parse_meta('/查分数表', self.bot)
        result = execute_efficiency(request, self.bot)
        self.assertEqual(result.versions[0], old.version)
        self.assertEqual([len(p.rows) for p in result.panels], [30, 30])
        self.assertTrue(all(any('暂用上次兼容快照' in n for n in p.notes) for p in result.panels))
        self.assertEqual(request.meta_request.snapshot.songs()[0].title, '迷星叫')
        self.assertEqual(resolve_command('/查分数表 迷星叫 单人', self.bot).meta.status, 'success')
        self.assertEqual(resolve_command('/查分数表 新名字 排行=Notes', self.bot).meta.versions[0], latest.version)
        facts = resolve_command('/查分数表 排行=Notes', self.bot).meta
        self.assertEqual(facts.versions[0], latest.version)
        self.assertEqual(facts.rows[0].notes, 777)
        self.assertFalse(any('暂用上次' in n for n in facts.notes))

    def test_backup_survives_restart_and_unknown_304_does_not_renew_it(self):
        old = self.source.get()
        self.update(self.unknown())
        restarted = MusicDataRepository(self.source.path, fetch=self.fetch, clock=lambda: self.now[0])
        latest = restarted.get()
        fallback = restarted.for_calculation(latest)
        self.assertEqual((fallback.version, fallback.checked_at), (old.version, old.checked_at))
        self.now[0] += TTL
        self.fetch.return_value = 304, b'', {}
        latest = restarted.get()
        self.assertEqual(restarted.for_calculation(latest).checked_at, old.checked_at)
        self.assertGreater(latest.checked_at, old.checked_at)
        self.now[0] = old.checked_at + MAX_STALE + 1
        latest = restarted.get()
        self.assertIs(restarted.for_calculation(latest), latest)

    def test_fallback_selection_and_pages_remain_available_until_recovery(self):
        from ournotes_bot.query.continuation import capture_context, execute_followup, Operation, CHANGED
        self.source.get()
        self.update(self.unknown())
        result = resolve_command('/查分数表', self.bot)
        context = capture_context(result, self.bot)
        self.assertIsNotNone(context)
        page, _ = execute_followup(context, Operation('page', 1), self.bot)
        self.assertEqual(page.meta.status, 'success')
        selected, _ = execute_followup(context, Operation('select', 1), self.bot)
        self.assertEqual(selected.meta.rows[0].score_id, context.visible[0].score_id)
        self.assertTrue(any('暂用上次' in n for n in selected.meta.notes))
        changed = copy.deepcopy(self.data)
        changed['songs'][0]['charts'][0]['notes']['judged'] = 123
        self.update(changed)
        self.assertEqual(execute_followup(context, Operation('page', 1), self.bot), CHANGED)

    def test_unknown_first_install_reports_unavailable_instead_of_empty_rankings(self):
        self.fetch.return_value = 200, json.dumps(self.unknown()).encode(), {}
        result = resolve_command('/查分数表', self.bot)
        self.assertIn('暂无有效兼容快照', result.meta.text)
        self.assertNotIn('共 0 条', result.meta.text)
        self.assertEqual(parse_meta('/查分数表', self.bot).status, 'data_unavailable')
        self.assertEqual(resolve_command('/查分数表 排行=Notes', self.bot).meta.status, 'success')

    def test_compatible_cache_is_schema1_and_rejects_corruption(self):
        self.source.get()
        saved = json.loads(self.source.compatible_path.read_text(encoding='utf8'))
        self.assertEqual(saved['schema'], 1)
        self.update(self.unknown())
        self.source.compatible_path.write_text('{broken', encoding='utf8')
        restarted = MusicDataRepository(self.source.path, fetch=self.fetch, clock=lambda: self.now[0])
        latest = restarted.get()
        self.assertIs(restarted.for_calculation(latest), latest)

    def test_old_existing_cache_is_backed_up_before_unknown_replaces_it(self):
        old = self.source.get()
        self.source.compatible_path.unlink()
        restarted = MusicDataRepository(self.source.path, fetch=self.fetch, clock=lambda: self.now[0])
        self.now[0] += TTL
        self.fetch.return_value = 200, json.dumps(self.unknown()).encode(), {}
        latest = restarted.get()
        again = MusicDataRepository(self.source.path, fetch=self.fetch, clock=lambda: self.now[0])
        self.assertEqual(again.for_calculation(again.get()).version, old.version)
        self.assertFalse(latest.model_supported)

    def test_disk_failure_preserves_in_memory_compatible_snapshot(self):
        with patch.object(self.source, '_save', side_effect=OSError('disk')):
            old = self.source.get()
            self.assertTrue(old.unsaved)
            latest = self.update(self.unknown())
        self.assertEqual(self.source.for_calculation(latest).version, old.version)
        self.assertFalse(list(self.source.path.parent.glob('*.tmp')))


class RefreshWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_refresh_periodic_refresh_and_reconnect_do_not_duplicate(self):
        first, second = asyncio.Event(), asyncio.Event()
        async def sleep(seconds):
            self.assertEqual(seconds, TTL)
            if not first.is_set():
                first.set()
                return
            second.set()
            await asyncio.Future()
        source = Mock(get=Mock(return_value=make_snapshot()))
        worker = MusicDataRefresher(source)
        with patch('ournotes_bot.sources.moenotes_music_data.asyncio.sleep', side_effect=sleep):
            task = worker.start()
            self.assertIs(worker.start(), task)
            await asyncio.wait_for(second.wait(), 3)
            self.assertEqual(source.get.call_count, 2)
            worker.stop()
            await asyncio.gather(task, return_exceptions=True)
            count = source.get.call_count
            self.assertEqual(source.get.call_count, count)
        self.assertTrue(worker.stopping.is_set())

    async def test_stop_waits_for_inflight_refresh_without_duplicate_fetch(self):
        entered, release = threading.Event(), threading.Event()
        def fetch():
            entered.set()
            release.wait(3)
            return make_snapshot()
        source = Mock(get=Mock(side_effect=fetch))
        worker = MusicDataRefresher(source)
        task = worker.start()
        try:
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            worker.stop()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(source.get.call_count, 1)

    async def test_unexpected_failure_waits_for_next_cycle_and_recovers(self):
        waited = asyncio.Event()
        source = Mock(get=Mock(side_effect=[RuntimeError('synthetic'), make_snapshot()]))
        calls = 0
        async def sleep(seconds):
            nonlocal calls
            calls += 1
            if calls == 1:
                return
            waited.set()
            await asyncio.Future()
        worker = MusicDataRefresher(source)
        with patch('ournotes_bot.sources.moenotes_music_data.asyncio.sleep', side_effect=sleep):
            with self.assertLogs('ournotes_bot.music_data', level='WARNING'):
                task = worker.start()
                await asyncio.wait_for(waited.wait(), 3)
                worker.stop()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(source.get.call_count, 2)
