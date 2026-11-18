"""Offline archive queries use recorded identities and never fetch current rankings."""
import itertools
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ournotes_bot.commands import resolve_command
from ournotes_bot.domain.info_catalog import ActivityInfo
from ournotes_bot.query.event_cutoff_query import execute_cutoff, parse_cutoff, parse_natural_cutoff
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff
from ournotes_bot.sources.cutoff_history import CutoffHistory, OpenCutoffHistory, HaneokaCutoffHistory, SourceHistory
from ournotes_bot.sources.moenotes_events import EventCutoffRepository, EventSnapshot, EventSong, BoardSnapshot, SourceError

STAMP = 1790937673000


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.history = CutoffHistory(self.root / 'history.sqlite3', min_free_mb=0)
        self.source = EventCutoffRepository(self.root / 'current', transport=self.no_network)
        self.source.history = self.history
        self.repo = SimpleNamespace(event_cutoffs=self.source)
        self.event = EventSnapshot('jp', '12', '星の 約束', 'end', STAMP - 10000, STAMP + 10000,
                                  (EventSong('7', '101', '歌曲甲'), EventSong('8', '102', '歌曲乙')))
        for song in self.event.songs:
            self.record(self.history, self.event, song, (100, None, 0))

    def no_network(self, *args, **kwargs):
        raise AssertionError('archive query must not fetch current metadata or boards')

    def record(self, store, event, song, scores, stamp=STAMP, **changes):
        board = BoardSnapshot(song, scores, stamp, stamp, stamp, source=store.source,
                              time_kind=getattr(store, 'time_kind', 'tracker_observed'),
                              contract_version=getattr(store, 'contract_version', ''), **changes)
        return store.record(event, board)

    def ask(self, text='活动ID=12'):
        return execute_cutoff(parse_cutoff('/查榜线 ' + text), self.repo)

    def test_id_and_name_read_last_observation_without_live_calls(self):
        self.record(self.history, self.event, self.event.songs[0], (90, None, 0), STAMP + 1000)
        with patch.object(self.source, 'event', side_effect=self.no_network), patch.object(self.source, 'boards', side_effect=self.no_network):
            for selector in ('活动ID=12', '活动="星の 約束"', '活动名称=約束'):
                answer = self.ask(selector)
                self.assertEqual(answer.status, 'success', answer.text)
                self.assertEqual(len(answer.boards), 2)
                self.assertEqual(answer.boards[0].scores[:3], (90, None, 0))
                self.assertIn('最近一次有效观测', answer.text)
                self.assertIn('非实时榜', answer.text)
                self.assertFalse(answer.boards[0].player_ids)
                self.assertEqual(answer.histories[0][1].total, 2)

    def test_combined_archive_verifies_windows_and_recorded_challenge_ids(self):
        from ournotes_bot.sources.total_rankings import (TotalRankingHistory, TotalRankingRepository,
                                                         TotalRankingSnapshot, event_key)
        for case in ('matching', 'shifted', 'changed_song', 'old_song_metadata'):
            with self.subTest(case=case):
                shifted = 86400000 if case == 'shifted' else 0
                when = STAMP + shifted
                songs = self.event.songs
                if case == 'changed_song':
                    songs = (replace(songs[0], music_id='999'), *songs[1:])
                event = replace(self.event, start_ms=self.event.start_ms + shifted,
                                end_ms=self.event.end_ms + shifted, songs=songs, total_ranking_enabled=True)
                store = TotalRankingHistory(self.root / f'{case}.sqlite3', 'tracker', min_free_mb=0)
                self.source.total_rankings = TotalRankingRepository(store)
                total = TotalRankingSnapshot(event_key(event), 'tracker', (9000,), when, when, received_ms=when)
                self.assertEqual(store.record(event, total), 'valid')
                if case == 'old_song_metadata':
                    with closing(sqlite3.connect(self.history.path)) as db, db:
                        db.execute('DELETE FROM event_metadata')
                combined = self.ask('id12 1-1')
                if case == 'matching':
                    self.assertEqual(combined.status, 'success')
                    self.assertEqual((combined.boards[0].score(1), combined.total.score(1)), (100, 9000))
                else:
                    self.assertEqual(combined.status, 'data_unavailable')
                    self.assertIn('/查榜线 jp id12 单曲', combined.text)
                    self.assertIn('/查榜线 jp id12 总榜', combined.text)
                self.assertEqual(self.ask('id12 单曲 1-1').boards[0].score(1), 100)
                self.assertEqual(self.ask('id12 总榜 1-1').total.score(1), 9000)

    def test_same_id_total_archives_are_matched_by_the_selected_record_before_enrichment(self):
        from ournotes_bot.sources.total_rankings import (TotalRankingHistory, TotalRankingRepository,
                                                         TotalRankingSnapshot, event_key)
        store = TotalRankingHistory(self.root / 'total.sqlite3', 'tracker', min_free_mb=0)
        self.source.total_rankings = TotalRankingRepository(store)
        self.repo.capture_catalog = lambda: SimpleNamespace(activities=(
            ActivityInfo(12, '共通名称', {'ja': '同一别名'},
                         start_at=self.event.start_ms / 1000, end_at=self.event.end_ms / 1000),), songs=())
        for shift, title, score in ((0, '原记录', 7000), (86400000, '延期记录', 9000)):
            event = replace(self.event, start_ms=self.event.start_ms + shift, end_ms=self.event.end_ms + shift,
                            title=title, total_ranking_enabled=True)
            stamp = STAMP + shift
            total = TotalRankingSnapshot(event_key(event), 'tracker', (score,), stamp, stamp, received_ms=stamp)
            self.assertEqual(store.record(event, total), 'valid')
        for term in ('id12', '活动=记录'):
            self.assertEqual(self.ask(term).status, 'ambiguous', term)
        by_catalog_name = self.ask('活动=同一别名 1-1')
        self.assertEqual((by_catalog_name.total.score(1), by_catalog_name.event.title), (7000, '共通名称'))
        matched = self.ask('活动=原记录 1-1')
        self.assertEqual(matched.status, 'success', matched.text)
        self.assertEqual((matched.total.score(1), matched.boards[0].score(1)), (7000, 100))
        other = self.ask('活动=延期记录 1-1')
        self.assertEqual(other.status, 'data_unavailable', other.text)
        self.assertIn('活动="延期记录" 总榜', other.text)
        delayed = self.ask('id12 活动="延期记录" 总榜 1-1')
        self.assertEqual((delayed.total.score(1), delayed.event.title), (9000, '延期记录'))
        self.assertEqual(self.ask('id12 单曲 1-1').boards[0].score(1), 100)
        # Matching catalog dates cannot stand in for missing dates in the song archive.
        with closing(sqlite3.connect(self.history.path)) as db, db:
            db.execute('DELETE FROM event_metadata')
        self.assertIn('缺少可核对', self.ask('活动=原记录 1-1').text)
        self.assertEqual(self.ask('活动=同一别名 单曲 1-1').boards[0].score(1), 100)

    def test_archive_metadata_uses_only_compatible_jp_catalog_dates(self):
        from ournotes_bot.sources.total_rankings import (TotalRankingHistory, TotalRankingRepository,
                                                         TotalRankingSnapshot, event_key)
        store = TotalRankingHistory(self.root / 'total.sqlite3', 'tracker', min_free_mb=0)
        self.source.total_rankings = TotalRankingRepository(store)
        later = STAMP + 86400000
        event = replace(self.event, title='延期归档', start_ms=None, end_ms=None, banner='',
                        api_start_ms=later - 10000, api_end_ms=later + 10000, total_ranking_enabled=True)
        total = TotalRankingSnapshot(event_key(event), 'tracker', (9000,), later, later, received_ms=later)
        self.assertEqual(store.record(event, total), 'valid')

        record = ActivityInfo(12, '目录活动', {'ja': '目录别名'}, image_url='fixture:catalog-banner')
        def use_dates(start, end):
            selected = replace(record, start_at=start / 1000 if start else None, end_at=end / 1000 if end else None)
            self.repo.capture_catalog = lambda: SimpleNamespace(activities=(selected,), songs=())

        for start, end in ((self.event.start_ms, self.event.end_ms),
                           (self.event.start_ms, None), (None, self.event.end_ms)):
            use_dates(start, end)
            for name in ('目录活动', '目录别名'):
                self.assertEqual(self.ask(f'活动={name} 总榜 1-1').status, 'empty')
            for selector in ('id12', '活动=延期归档'):
                archived = self.ask(selector + ' 总榜 1-1')
                self.assertEqual(archived.total.score(1), 9000)
                self.assertEqual((archived.event.title, archived.event.banner, archived.event.start_ms,
                                  archived.event.end_ms), ('延期归档', '', None, None))

        for start, end in ((event.api_start_ms, event.api_end_ms), (event.api_start_ms, None),
                           (None, event.api_end_ms), (None, None)):
            use_dates(start, end)
            archived = self.ask('活动=目录别名 总榜 1-1')
            self.assertEqual(archived.total.score(1), 9000)
            self.assertEqual((archived.event.title, archived.event.banner, archived.event.start_ms,
                              archived.event.end_ms), ('目录活动', record.image_url, start, end))

        # Opposite partial bounds must still form an interval before filling the gap.
        partial = replace(self.event, end_ms=None, banner='fixture:archive-banner')
        self.record(self.history, partial, partial.songs[0], (101,), STAMP + 1000)
        use_dates(None, partial.start_ms)
        self.assertEqual(self.ask('活动=目录别名 单曲 1-1').status, 'empty')
        original = self.ask('id12 单曲 1-1')
        self.assertEqual((original.event.title, original.event.banner, original.event.end_ms),
                         (partial.title, partial.banner, None))
        use_dates(None, self.event.end_ms)
        enriched = self.ask('活动=目录别名 单曲 1-1')
        self.assertEqual((enriched.event.title, enriched.event.banner, enriched.event.end_ms),
                         ('目录活动', partial.banner, self.event.end_ms))

    def test_missing_total_index_cannot_use_catalog_dates_to_join_an_unknown_song_archive(self):
        from ournotes_bot.sources.total_rankings import TotalRankingHistory, TotalRankingRepository
        store = TotalRankingHistory(self.root / 'total.sqlite3', 'tracker', min_free_mb=0)
        self.source.total_rankings = TotalRankingRepository(store)
        self.repo.capture_catalog = lambda: SimpleNamespace(activities=(
            ActivityInfo(12, '活动', {}, start_at=self.event.start_ms / 1000, end_at=self.event.end_ms / 1000),), songs=())
        with closing(sqlite3.connect(self.history.path)) as db, db:
            db.execute('DELETE FROM event_metadata')
        with patch.object(store, 'events', side_effect=SourceError('total_history_unavailable')), patch.object(
                store, 'latest', side_effect=AssertionError('total archive identity unavailable')):
            answer = self.ask('id12 1-1')
        self.assertEqual(answer.status, 'success', answer.text)
        self.assertEqual(answer.boards[0].score(1), 100)
        self.assertIsNone(answer.total)

    def test_song_source_or_provider_windows_cannot_disappear_before_total_join(self):
        from ournotes_bot.sources.history_partitions import ProviderHistory
        from ournotes_bot.sources.total_rankings import (TotalRankingHistory, TotalRankingRepository,
                                                         TotalRankingSnapshot, event_key)
        other = OpenCutoffHistory(self.root / 'open.sqlite3', min_free_mb=0)
        shifted = replace(self.event, start_ms=self.event.start_ms + 86400000, end_ms=self.event.end_ms + 86400000,
                          source='open')
        self.record(other, shifted, shifted.songs[0], (999,), STAMP + 86400000)
        store = TotalRankingHistory(self.root / 'total.sqlite3', 'tracker', min_free_mb=0)
        event = replace(self.event, total_ranking_enabled=True)
        self.assertEqual(store.record(event, TotalRankingSnapshot(event_key(event), 'tracker', (753,),
                              STAMP, STAMP, received_ms=STAMP)), 'valid')
        self.source.total_rankings = TotalRankingRepository(store)
        sources = SourceHistory(self.history, other)
        providers = ProviderHistory(self.history)
        unrelated = replace(self.event, event_id='13')
        self.record(self.history, unrelated, unrelated.songs[0], (432,))
        with patch.object(providers, 'partitions', return_value=(other,)):
            for history in (sources, providers):
                self.source.history = history
                self.assertIn('各来源保存的活动时间', self.ask('id12 1-1').text)
                self.assertEqual(self.ask('id12 单曲 1-1').status, 'data_unavailable')
                self.assertEqual(self.ask('id12 总榜 1-1').total.score(1), 753)
                self.assertEqual(self.ask('id13 单曲 1-1').boards[0].score(1), 432)
            with closing(sqlite3.connect(other.path)) as db, db:
                db.execute('DELETE FROM event_metadata')
            for history in (sources, providers):
                self.source.history = history
                self.assertIn('缺少可核对', self.ask('id12 1-1').text)
                self.assertEqual(self.ask('id12 单曲 1-1').boards[0].score(1), 999)

    def test_parameters_any_order_round_trip_and_numeric_mode(self):
        for parts in itertools.permutations(('活动ID=12', 'jp', '3-3', '仅数值')):
            request = parse_cutoff(' '.join(parts))
            self.assertEqual(parse_cutoff(request.command_label()), request)
            with patch.object(self.history, 'read', side_effect=AssertionError('numeric mode should not read trends')):
                answer = execute_cutoff(request, self.repo)
            self.assertEqual(answer.status, 'success', answer.text)
            self.assertEqual(answer.ranks, (3,))
            self.assertEqual(answer.boards[0].score(3), 0)
        answer = self.ask('活动="星の 約束" 1')
        self.assertEqual(answer.ranks, tuple(range(1, 12)))
        self.assertEqual(answer.request.event_name, '星の 約束')
        self.assertEqual(parse_natural_cutoff('/查榜线 活动ID=12').event_id, '12')

    def test_command_route_keeps_selector(self):
        result = resolve_command('/查榜线 活动ID=12 3-3', self.repo)
        self.assertEqual(result.cutoff.event.event_id, '12')
        self.assertEqual(result.cutoff.ranks, (3,))

    def test_short_activity_id_forms_and_local_natural_route(self):
        for selector in ('id12', 'ID12', 'Id 12', 'id=12', 'ID:12', 'id = 12', 'ｉｄ１２', 'id0012'):
            with self.subTest(selector=selector):
                request = parse_cutoff('/查榜线 ' + selector)
                self.assertEqual(request.event_id, '12')
                self.assertEqual(parse_natural_cutoff('/查榜线 ' + selector), request)
                answer = execute_cutoff(request, self.repo)
                self.assertEqual(answer.status, 'success', answer.text)
                self.assertEqual(answer.event.event_id, '12')
                self.assertIn('id12', answer.request.command_label())
                self.assertEqual(parse_cutoff(answer.request.command_label()), answer.request)
        self.assertEqual(parse_cutoff('/查榜线 12').event_id, '')
        self.assertEqual(parse_cutoff('/查榜线 12').target_rank, 12)
        self.assertEqual(self.ask('id999').status, 'empty')

    def test_short_id_combinations_conflicts_and_literal_event_names(self):
        for groups in itertools.permutations(('id12', 'jp', '3-3')):
            answer = self.ask(' '.join(groups))
            self.assertEqual(answer.status, 'success', answer.text)
            self.assertEqual(answer.event.event_id, '12')
            self.assertEqual(answer.ranks, (3,))
            self.assertEqual(answer.boards[0].score(3), 0)
        self.assertEqual(self.ask('id12 活动ID=12 id0012').status, 'success')
        self.assertEqual(self.ask('hk id12').status, 'empty')
        for text in ('id12 id13', 'id12 活动ID=13', 'id0', 'id-1', 'id1.5', 'id+1'):
            self.assertEqual(self.ask(text).status, 'invalid_arguments', text)
        self.assertEqual(parse_cutoff('活动="id12"').event_name, 'id12')

    def test_missing_unknown_server_disabled_and_bad_store_do_not_fall_back(self):
        self.assertEqual(self.ask('活动ID=999').status, 'empty')
        self.assertEqual(self.ask('hk 活动ID=12').status, 'empty')
        self.assertEqual(self.ask('cn 活动ID=12').status, 'invalid_arguments')
        self.history.enabled = False
        self.assertEqual(self.ask().status, 'empty')
        self.history.enabled = True
        self.history.path.write_bytes(b'broken')
        self.assertEqual(self.ask().status, 'data_unavailable')

    def test_missing_database_not_created(self):
        self.history.path.unlink()
        self.assertEqual(self.ask().status, 'empty')
        self.assertFalse(self.history.path.exists())

    def test_ambiguous_names_and_id_name_conflicts(self):
        other = replace(self.event, event_id='13', title='星の 約束 再演')
        self.record(self.history, other, other.songs[0], (10,))
        ambiguous = self.ask('活动=星の')
        self.assertEqual(ambiguous.status, 'ambiguous')
        self.assertIn('id13', ambiguous.text)
        self.assertEqual(self.ask('活动="星の 約束"').event.event_id, '12')
        self.assertEqual(self.ask('活动ID=12 活动="星の 約束 再演"').status, 'empty')
        for text in ('活动ID=12 活动ID=13', '活动=甲 活动=乙', '活动ID=-1', '活动ID=1.5', '活动=""', '活动="___"', '活动="未闭合'):
            self.assertEqual(self.ask(text).status, 'invalid_arguments', text)

    def test_pre_metadata_database_and_jp_catalog_names(self):
        with closing(sqlite3.connect(self.history.path)) as db, db:
            db.execute('DROP TABLE event_metadata')
        self.repo.capture_catalog = lambda: SimpleNamespace(
            activities=(ActivityInfo(12, '旧活动名', {'ja': 'Old Event'}),), songs=())
        self.assertEqual(self.ask('活动="Old Event"').event.title, '旧活动名')
        self.assertEqual(self.ask().boards[0].song.music_id, '101')
        # Additive write remains readable with the original version and observation columns.
        self.record(self.history, self.event, self.event.songs[0], (11,), STAMP + 1000)
        with closing(sqlite3.connect(self.history.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            raw = db.execute('SELECT scores FROM observations ORDER BY source_ms DESC').fetchone()[0]
            self.assertEqual(json.loads(raw)[0], 11)
        self.assertEqual(self.history.read(self.event, self.event.songs[0], (1,)).total, 2)

    def test_new_metadata_is_restart_safe_and_contains_no_players(self):
        self.source.history = CutoffHistory(self.history.path, min_free_mb=0)
        self.assertEqual(self.ask('活动=約束').status, 'success')
        with closing(sqlite3.connect(self.history.path)) as db:
            saved = json.loads(db.execute('SELECT payload FROM event_metadata').fetchone()[0])
        self.assertNotIn('player_ids', str(saved))
        self.assertEqual(saved['songs'][0]['music_id'], '101')

    def test_jp_catalog_enrichment_preserves_recorded_and_localized_song_names(self):
        song = replace(self.event.songs[0], title='記録された曲名', names=('Recorded English',))
        event = replace(self.event, songs=(song, self.event.songs[1]))
        self.record(self.history, event, song, (123,), STAMP + 1000)
        self.repo.capture_catalog = lambda: SimpleNamespace(activities=(), songs=(
            SimpleNamespace(id=101, title='本地中文歌名', jacket_url='',
                            titles=('本地中文歌名', 'Catalog English', 'カタログ曲名')),))
        result = self.ask('id12')
        self.assertEqual(result.boards[0].score(1), 123)
        self.assertEqual(result.boards[0].song.title, '本地中文歌名')
        self.assertIn('Recorded English', result.boards[0].song.names)
        self.assertIn('Catalog English', result.boards[0].song.names)

    def test_latest_conflict_uses_older_whole_observation_and_reports_conflict(self):
        self.record(self.history, self.event, self.event.songs[0], (200,), STAMP + 1000)
        self.record(self.history, self.event, self.event.songs[0], (300,), STAMP + 1000)
        answer = self.ask('活动ID=12')
        self.assertEqual(answer.boards[0].scores[:3], (100, None, 0))
        self.assertEqual(answer.boards[0].fetched_ms, STAMP)
        self.assertIn('同一时间分数冲突', answer.text)

    def test_three_sources_and_region_isolation(self):
        formal = OpenCutoffHistory(self.root / 'open.sqlite3', min_free_mb=0)
        haneoka = HaneokaCutoffHistory(self.root / 'haneoka.sqlite3', min_free_mb=0)
        combined = SourceHistory(self.history, formal, haneoka=haneoka, active='haneoka')
        self.source.history = combined
        for store, region, value in ((formal, 'tw', 456), (haneoka, 'kr', 789), (haneoka, 'en', 1011)):
            event = replace(self.event, server=region, source=store.source)
            self.record(store, event, event.songs[0], (value,))
            answer = self.ask(f'{"hk" if region == "tw" else region} 活动=約束 1-1')
            self.assertEqual(answer.boards[0].score(1), value)
        self.record(haneoka, self.event, self.event.songs[0], (10,), STAMP + 1000)
        self.assertEqual(self.ask().boards[0].score(1), 10)
        self.record(formal, self.event, self.event.songs[0], (20,), STAMP + 1000)
        answer = self.ask()
        self.assertEqual(answer.boards[0].score(1), 100)
        self.assertEqual(answer.boards[0].fetched_ms, STAMP)
        self.assertIn('多来源历史冲突', answer.text)

    def test_history_budget_does_not_hide_last_snapshot(self):
        with patch.object(self.history, 'read', wraps=lambda e, s, r: CutoffHistory.read(self.history, e, s, r, max_points=0)):
            answer = self.ask()
        self.assertEqual(answer.boards[0].score(1), 100)
        self.assertIn('预算', answer.text)
        self.assertNotIn('当前值', answer.text)

    def test_corrupt_stored_vector_is_not_rendered_as_a_score(self):
        with closing(sqlite3.connect(self.history.path)) as db, db:
            db.execute("UPDATE observations SET scores=?", ('[true,"100"]',))
        self.assertEqual(self.ask().status, 'data_unavailable')

    def test_cross_source_challenge_identity_conflict_is_reported(self):
        formal = OpenCutoffHistory(self.root / 'open.sqlite3', min_free_mb=0)
        changed = replace(self.event, songs=(replace(self.event.songs[0], music_id='999'),))
        self.record(formal, changed, changed.songs[0], (100,))
        self.source.history = SourceHistory(self.history, formal)
        self.assertEqual(self.ask().status, 'data_unavailable')

    def test_archive_renders_overview_single_song_and_table(self):
        from PIL import Image
        import io
        for query in ('活动ID=12', '活动ID=12', '活动ID=12 1-3'):
            answer = self.ask(query)
            pages = render_cutoff(answer)
            self.assertTrue(pages)
            for page in pages:
                Image.open(io.BytesIO(page.image)).verify()
                self.assertIn('最近一次有效观测', page.text)

    def test_expired_tail_uses_last_valid_time_scores_and_keeps_database(self):
        song = self.event.songs[0]
        self.record(self.history, self.event, song, (90, None, 0), STAMP + 1000)
        self.record(self.history, self.event, replace(song, effective_end_ms=self.event.end_ms), (999, 888, 777), STAMP + 20000)
        answer = self.ask('id12 1-3')
        self.assertEqual(answer.boards[0].scores[:3], (90, None, 0))
        self.assertEqual(answer.boards[0].fetched_ms, STAMP + 1000)
        self.assertIn('超出榜单有效期', answer.text)
        self.assertNotIn('/未获取', answer.text)
        with closing(sqlite3.connect(self.history.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM observations WHERE challenge='7'").fetchone()[0], 3)

    def test_no_valid_observation_returns_short_explanation_without_image(self):
        with closing(sqlite3.connect(self.history.path)) as db, db:
            db.execute("UPDATE observations SET quality='outside_period'")
        answer = self.ask('id12 80-100')
        self.assertEqual(answer.status, 'data_unavailable')
        self.assertIn('暂无有效历史分数', answer.message)
        self.assertIn('超出榜单有效期', answer.message)
        self.assertEqual(render_cutoff(answer), ())

    def test_empty_or_corrupt_tail_is_skipped_without_combining_ranks(self):
        song = self.event.songs[0]
        self.record(self.history, self.event, song, (90, None, 0), STAMP + 1000)
        self.record(self.history, self.event, song, (None,), STAMP + 2000)
        self.record(self.history, self.event, song, (200,), STAMP + 3000)
        with closing(sqlite3.connect(self.history.path)) as db, db:
            db.execute('UPDATE observations SET scores=? WHERE source_ms=?', ('[true,"100"]', STAMP + 3000))
        board = self.ask('id12').boards[0]
        self.assertEqual(board.scores[:3], (90, None, 0))
        self.assertEqual(board.fetched_ms, STAMP + 1000)
        self.assertIn('分数记录损坏', ' '.join(board.notes))

    def test_old_metadata_enriches_period_banner_and_jacket(self):
        with closing(sqlite3.connect(self.history.path)) as db, db:
            db.execute('DROP TABLE event_metadata')
        self.repo.capture_catalog = lambda: SimpleNamespace(
            activities=(ActivityInfo(12, '旧活动名', {'ja': 'Old Event'}, 'https://example.org/banner.png',
                                     (STAMP - 10000) / 1000, (STAMP + 10000) / 1000),),
            songs=(SimpleNamespace(id=101, title='歌曲甲', titles=('歌曲甲',), jacket_url='https://example.org/song.png'),))
        event = self.ask().event
        self.assertEqual((event.start_ms, event.end_ms), (STAMP - 10000, STAMP + 10000))
        self.assertEqual(event.banner, 'https://example.org/banner.png')
        self.assertEqual(event.songs[0].jacket, 'https://example.org/song.png')

    def test_actual_ranking_renderer_shows_scores_time_without_player_placeholders(self):
        from ournotes_bot.rendering import ranking_layout
        from ournotes_bot.rendering.haneoka_theme import Canvas
        from ournotes_bot.sources.moenotes_events import display_time, zone_label
        original = Canvas.text
        seen = []
        def capture(canvas, x, y, value, *args, **kwargs):
            seen.append(str(value))
            return original(canvas, x, y, value, *args, **kwargs)
        with patch.object(Canvas, 'text', capture):
            for query in ('id12 1-3', 'id12', 'id12 1-3'):
                answer = self.ask(query)
                pages = ranking_layout.render(answer, assets={}, load=lambda *args: None, display_time=display_time,
                                              server_label='日服', timezone_label=zone_label('jp'))
                self.assertTrue(pages)
        text = ' '.join(seen)
        self.assertIn('100', seen)
        self.assertIn(display_time(STAMP, 'jp'), text)
        for missing in ('玩家', '未获取', '卡组未获取'):
            self.assertNotIn(missing, seen)

    def test_legacy_history_uses_allowlisted_catalog_art_with_release_checks(self):
        import io
        from PIL import Image
        from ournotes_bot.rendering.event_cutoff_visuals import load_artwork
        url = 'https://haneoka.org/assets/jp/Assets/AddressableResources/Image/Jacket/test.png?release=r-' + 'a' * 20
        image = Image.new('RGB', (20, 20))
        raw = io.BytesIO()
        image.save(raw, format='PNG')
        headers = {'content-type': 'image/png', 'x-haneoka-release-id': 'r-' + 'a' * 20, 'x-haneoka-source-id': 'fixture'}
        with patch('ournotes_bot.sources.haneoka.transport.public_get', return_value=(raw.getvalue(), headers)):
            result = load_artwork(self.source, self.event, (url,))
            self.assertIsNotNone(result[url])
            result[url].close()
        url2 = url.replace('test.png', 'other.png')
        with patch('ournotes_bot.sources.haneoka.transport.public_get', return_value=(raw.getvalue(), {**headers, 'x-haneoka-release-id': 'wrong'})):
            self.assertIsNone(load_artwork(self.source, self.event, (url2,))[url2])

    def test_saved_history_players_appear_in_actual_text_and_rendered_table(self):
        from ournotes_bot.rendering import ranking_layout
        from ournotes_bot.rendering.haneoka_theme import Canvas
        from ournotes_bot.sources.moenotes_events import display_time
        for song in self.event.songs:
            self.record(self.history, self.event, song, (100, None, 0),
                        player_ids=('12345678901234567890', None, '3'), player_names=('合成玩家🌸', None, '零分玩家'))
        answer = self.ask('id12 1-3')
        self.assertIn('100/12345678901234567890/合成玩家🌸', answer.text)
        original, seen = Canvas.text, []
        def capture(canvas, x, y, value, *args, **kwargs):
            seen.append(str(value))
            return original(canvas, x, y, value, *args, **kwargs)
        with patch.object(Canvas, 'text', capture):
            pages=ranking_layout.render(answer, assets={}, load=lambda *args: None, display_time=display_time,
                                        server_label='日服', timezone_label='UTC+09:00')
        self.assertTrue(pages)
        self.assertIn('合成玩家🌸', seen)
        self.assertIn('ID 12345678901234567890', seen)
        self.assertNotIn('卡组未获取', seen)
