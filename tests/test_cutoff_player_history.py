"""Public identities belong to one complete observation, including after restart and cache recovery."""
from contextlib import closing
from dataclasses import replace
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from ournotes_bot.sources.cutoff_history import CutoffHistory, OpenCutoffHistory, HaneokaCutoffHistory, SourceHistory
from test_cutoff_history import event, board, NOW


class PlayerHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = CutoffHistory(self.root / 'history.sqlite3', min_free_mb=0)
        self.event = event()
        self.board = board(self.event, scores=(100, None, 0), player_ids=('12345678901234567890', '2', '3'),
                           player_names=('合成🌸名字', '缺分不展示', '零分玩家'))

    def test_restart_preserves_time_rank_score_and_public_identity_only(self):
        self.store.record(self.event, self.board)
        restored = CutoffHistory(self.store.path).latest(self.event, self.event.songs[0])
        self.assertEqual(restored.fetched_ms, NOW)
        self.assertEqual(restored.scores[:3], (100, None, 0))
        self.assertEqual(tuple(restored.player_id(r) for r in (1, 2, 3)), ('12345678901234567890', None, '3'))
        self.assertEqual(restored.player_name(1), '合成🌸名字')
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(len(db.execute('PRAGMA table_info(observations)').fetchall()), 12)
            players = json.loads(db.execute('SELECT players FROM observation_players').fetchone()[0])
            self.assertEqual(players[1], [None, None])

    def test_later_same_score_does_not_replace_earlier_owner_or_fill_rank_holes(self):
        self.store.record(self.event, self.board)
        later = replace(self.board, fetched_ms=NOW+1000, server_ms=NOW+1000,
                        player_ids=('9', None, None), player_names=('后来玩家', None, None))
        self.store.record(self.event, later)
        current = self.store.latest(self.event, self.event.songs[0])
        self.assertEqual(current.player_id(1), '9')
        self.assertIsNone(current.player_id(3))
        previous = self.store.latest(self.event, self.event.songs[0], before_ms=NOW+1000)
        self.assertEqual(previous.player_id(1), '12345678901234567890')

    def test_duplicate_can_fill_missing_identity_but_conflicting_identity_is_explicit(self):
        self.store.record(self.event, replace(self.board, player_ids=(), player_names=()))
        self.store.record(self.event, self.board)
        self.assertEqual(self.store.latest(self.event, self.event.songs[0]).player_name(1), '合成🌸名字')
        self.store.record(self.event, replace(self.board, player_ids=('9', None, None)))
        latest = self.store.latest(self.event, self.event.songs[0])
        self.assertEqual(latest.score(1), 100)
        self.assertIsNone(latest.player_id(1))
        self.assertIn('身份冲突', ' '.join(latest.notes))

    def test_backfill_requires_exact_source_time_vector_and_identity(self):
        self.store.record(self.event, replace(self.board, player_ids=(), player_names=()))
        for changed in (replace(self.board, fetched_ms=NOW+1), replace(self.board, scores=(101, None, 0)),
                        replace(self.board, song=replace(self.board.song, music_id='999'))):
            self.assertEqual(self.store.backfill_players(self.event, changed, apply=True), 'no_exact_observation')
        self.assertEqual(self.store.backfill_players(self.event, self.board), 'matched')
        self.assertFalse(self.store.latest(self.event, self.event.songs[0]).player_ids)
        self.assertEqual(self.store.backfill_players(self.event, self.board, apply=True), 'saved')
        self.assertEqual(self.store.latest(self.event, self.event.songs[0]).player_name(1), '合成🌸名字')
        with closing(sqlite3.connect(self.store.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM observations').fetchone()[0], 1)

    def test_three_source_versions_keep_players_with_calibrated_time(self):
        for cls, source, kind, contract, version in (
                (OpenCutoffHistory, 'open', 'upstream_fetched', 'moenotes:challenge-ranking/1', 2),
                (HaneokaCutoffHistory, 'haneoka', 'tracker_observed_via_haneoka', 'haneoka:challenge-ranking/1', 3)):
            with self.subTest(source=source):
                store = cls(self.root / (source+'.sqlite3'), min_free_mb=0)
                b = replace(self.board, source=source, time_kind=kind, contract_version=contract)
                if source == 'haneoka':
                    b = replace(b, server_ms=NOW+130000, fetched_ms=NOW+129000, http_date_ms=NOW)
                self.assertEqual(store.record(self.event, b), 'valid')
                latest = store.latest(self.event, self.event.songs[0])
                self.assertEqual(latest.player_name(1), '合成🌸名字')
                self.assertEqual(latest.fetched_ms, NOW-1000 if source == 'haneoka' else NOW)
                self.assertEqual(store.backfill_players(self.event, b), 'matched')
                with closing(sqlite3.connect(store.path)) as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], version)

    def test_corrupt_players_do_not_hide_valid_scores(self):
        self.store.record(self.event, self.board)
        with closing(sqlite3.connect(self.store.path)) as db, db:
            db.execute("UPDATE observation_players SET players='{}'")
        latest = self.store.latest(self.event, self.event.songs[0])
        self.assertEqual(latest.score(1), 100)
        self.assertFalse(latest.player_ids)

    def test_cross_source_player_conflict_keeps_scores_without_choosing_an_owner(self):
        formal = OpenCutoffHistory(self.root/'open.sqlite3', min_free_mb=0)
        self.store.record(self.event, self.board)
        formal.record(self.event, replace(self.board, source='open', time_kind='upstream_fetched',
                                         contract_version='moenotes:challenge-ranking/1', player_ids=('9', None, '3')))
        latest=SourceHistory(self.store, formal).latest(self.event, self.event.songs[0])
        self.assertEqual(latest.score(1), 100)
        self.assertFalse(latest.player_ids)
        self.assertIn('多来源玩家身份冲突', ' '.join(latest.notes))

    def test_misaligned_arrays_and_invalid_fields_do_not_shift_or_store_private_details(self):
        b=replace(self.board, player_ids=('1',), player_names=('保留名字', None, '零分玩家'),
                  player_details=({'private':'secret'},))
        self.store.record(self.event, b)
        latest=self.store.latest(self.event, self.event.songs[0])
        self.assertIsNone(latest.player_id(1))
        self.assertEqual(latest.player_name(1),'保留名字')
        with closing(sqlite3.connect(self.store.path)) as db:
            encoded=db.execute('SELECT players FROM observation_players').fetchone()[0]
        self.assertNotIn('private',encoded)
        self.assertNotIn('secret',encoded)

    def test_recovery_tool_backs_up_and_never_modifies_observations(self):
        self.store.record(self.event, replace(self.board, player_ids=(), player_names=()))
        spec = importlib.util.spec_from_file_location('backfill_players', Path(__file__).parents[1]/'scripts/backfill_cutoff_players.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        key = 'board:jp:1:1'
        cache = self.root/'cache'; cache.mkdir()
        data = {'schema': 1, 'key': key, 'received': NOW/1000,
                'headers': {'x-position-source': 'responseOrder', 'x-fetched-at': str(NOW), 'x-server-time': str(NOW)},
                'payload': {'scores': list(self.board.scores), 'player_ids': list(self.board.player_ids), 'player_names': list(self.board.player_names)}}
        (cache/(hashlib.sha256(key.encode()).hexdigest()+'.json')).write_text(json.dumps(data), encoding='utf-8')
        result = module.run(cache, self.store.path, 'tracker', apply=True, backup_dir=self.root/'backups')
        self.assertEqual(result['counts'], {'saved': 1})
        with closing(sqlite3.connect(result['backup'])) as backup, closing(sqlite3.connect(self.store.path)) as current:
            self.assertEqual(backup.execute('SELECT * FROM observations').fetchall(), current.execute('SELECT * FROM observations').fetchall())
            self.assertIsNone(backup.execute("SELECT 1 FROM sqlite_master WHERE name='observation_players'").fetchone())
        self.assertEqual(module.run(cache, self.store.path, 'tracker')['counts'], {'matched': 1})

    def test_haneoka_recovery_accepts_old_and_avatar_v2_cache_keys(self):
        spec = importlib.util.spec_from_file_location('backfill_haneoka_players', Path(__file__).parents[1]/'scripts/backfill_cutoff_players.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for suffix in ('', ':avatar-v2'):
            with self.subTest(suffix=suffix):
                root = self.root / ('old' if not suffix else 'avatar-v2')
                root.mkdir()
                store = HaneokaCutoffHistory(root/'history.sqlite3', min_free_mb=0)
                b = replace(self.board, source='haneoka', time_kind='tracker_observed_via_haneoka',
                            contract_version='haneoka:challenge-ranking/1')
                self.assertEqual(store.record(self.event, replace(b, player_ids=(), player_names=())), 'valid')
                key = 'board:jp:1:1:' + 'a'*64 + suffix
                cache = root/'cache'; cache.mkdir()
                data = {'schema': 1, 'key': key, 'received': NOW/1000,
                        'payload': {'scores': list(b.scores), 'player_ids': list(b.player_ids),
                                    'player_names': list(b.player_names), 'unsafe': False,
                                    'fetched': NOW, 'server': NOW}}
                (cache/(hashlib.sha256(key.encode()).hexdigest()+'.json')).write_text(json.dumps(data), encoding='utf-8')
                self.assertEqual(module.run(cache, store.path, 'haneoka')['counts'], {'matched': 1})
                self.assertFalse(store.latest(self.event, self.event.songs[0]).player_ids)
                result = module.run(cache, store.path, 'haneoka', apply=True, backup_dir=root/'backups')
                self.assertEqual(result['counts'], {'saved': 1})
                self.assertEqual(store.latest(self.event, self.event.songs[0]).player_name(1), '合成🌸名字')
                with closing(sqlite3.connect(result['backup'])) as backup, closing(sqlite3.connect(store.path)) as current:
                    self.assertEqual(backup.execute('SELECT * FROM observations').fetchall(),
                                     current.execute('SELECT * FROM observations').fetchall())
                self.assertEqual(module.run(cache, store.path, 'haneoka')['counts'], {'matched': 1})
                for invalid in (key+':unknown', key.replace('a'*64, 'a'*63)):
                    with self.subTest(invalid=invalid):
                        invalid_cache = root/'invalid'; invalid_cache.mkdir(exist_ok=True)
                        data['key'] = invalid
                        path = invalid_cache/(hashlib.sha256(invalid.encode()).hexdigest()+'.json')
                        path.write_text(json.dumps(data), encoding='utf-8')
                        with self.assertRaisesRegex(ValueError, 'invalid board cache identity'):
                            module.run(invalid_cache, store.path, 'haneoka')
                        path.unlink()
