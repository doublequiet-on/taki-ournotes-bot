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
from ournotes_bot.sources.moenotes_events import EventCutoffRepository, EventSnapshot, EventSong, BoardSnapshot

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

    def test_parameters_any_order_round_trip_and_numeric_mode(self):
        for parts in itertools.permutations(('活动ID=12', 'jp', '歌曲ID=101', '3-3', '仅数值')):
            request = parse_cutoff(' '.join(parts))
            self.assertEqual(parse_cutoff(request.command_label()), request)
            with patch.object(self.history, 'read', side_effect=AssertionError('numeric mode should not read trends')):
                answer = execute_cutoff(request, self.repo)
            self.assertEqual(answer.status, 'success', answer.text)
            self.assertEqual(answer.ranks, (3,))
            self.assertEqual(answer.boards[0].score(3), 0)
        answer = self.ask('活动="星の 約束" 歌名=歌曲甲 1')
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

    def test_short_id_combinations_conflicts_and_literal_song_names(self):
        for groups in itertools.permutations(('id12', 'jp', '歌曲ID=101', '3-3')):
            answer = self.ask(' '.join(groups))
            self.assertEqual(answer.status, 'success', answer.text)
            self.assertEqual(answer.event.event_id, '12')
            self.assertEqual(answer.ranks, (3,))
            self.assertEqual(answer.boards[0].score(3), 0)
        self.assertEqual(self.ask('id12 活动ID=12 id0012').status, 'success')
        self.assertEqual(self.ask('hk id12').status, 'empty')
        for text in ('id12 id13', 'id12 活动ID=13', 'id0', 'id-1', 'id1.5', 'id+1'):
            self.assertEqual(self.ask(text).status, 'invalid_arguments', text)
        literal = parse_cutoff('歌名="id12" 50')
        self.assertEqual(literal.event_id, '')
        self.assertEqual(literal.query, 'id12')
        self.assertEqual(literal.target_rank, 50)
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
        for name in ('記録された曲名', 'Recorded English', '本地中文歌名', 'Catalog English', 'カタログ曲名'):
            with self.subTest(name=name):
                answer = self.ask('id12 歌名=' + json.dumps(name, ensure_ascii=False))
                self.assertEqual(answer.status, 'success', answer.text)
                self.assertEqual(answer.boards[0].score(1), 123)
                self.assertEqual(answer.boards[0].song.title, '本地中文歌名')

    def test_latest_conflict_uses_older_whole_observation_and_reports_conflict(self):
        self.record(self.history, self.event, self.event.songs[0], (200,), STAMP + 1000)
        self.record(self.history, self.event, self.event.songs[0], (300,), STAMP + 1000)
        answer = self.ask('活动ID=12 歌曲ID=101')
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
        for query in ('活动ID=12', '活动ID=12 歌曲ID=101', '活动ID=12 1-3'):
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
        answer = self.ask('id12 歌曲ID=101 1-3')
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
        board = self.ask('id12 歌曲ID=101').boards[0]
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
            for query in ('id12 1-3', 'id12', 'id12 歌曲ID=101 1-3'):
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
