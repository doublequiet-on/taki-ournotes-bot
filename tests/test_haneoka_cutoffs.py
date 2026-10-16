# L3
# Input: Synthetic four-region DTOs, fake time/network and independent temporary histories.
# Output: Ordering, precision, failure, identity and source-switch regression evidence.
# Pos: Tests / Haneoka challenge cutoffs; see docs/HANEOKA_CUTOFFS.md.
# Effects: Offline temporary files and Pillow rendering only; no real players or QQ.
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ournotes_bot.query.event_cutoff_query import execute_cutoff, parse_cutoff
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff
from ournotes_bot.sources.cutoff_history import (
    CutoffHistory, OpenCutoffHistory, HaneokaCutoffHistory, SourceHistory, configure_sources,
)
from ournotes_bot.sources.haneoka.event_cutoffs import (
    HaneokaEventCutoffRepository, CONTRACT, TIME_KIND, MAX_SCORE, SourceError,
)


class Fixture:
    now = 1791388500.0

    def __init__(self):
        self.calls = []
        self.failure = False
        self.event_id = "1"
        self.music = "101"
        self.fetched = int(self.now * 1000)
        self.stale = False
        self.clock_offset = 0
        self.rows = [{"rank": 1, "score": 100, "profileId": "1234567890123456789",
                      "playerId": "opaque-private-id", "name": "synthetic user"},
                     None, {"rank": 3, "score": 0, "profileId": "3", "name": "zero"}]
        self.change = None
        self.wrong_region = False

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.failure:
            raise SourceError("upstream")
        region = url.split("/")[7]
        if url.endswith("/current"):
            body = {"region": region, "fetchedAtMs": None, "stale": False,
                    "event": {"id": self.event_id, "status": "nowOn", "startAtMs": 1791300000000,
                              "endAtMs": 1791600000000,
                              "challenges": [{"id": "11", "musicId": self.music, "enabled": True,
                                              "status": "collecting", "startAtMs": 1791300000000,
                                              "endAtMs": 1791600000000}]}}
        else:
            body = {"region": "bad" if self.wrong_region else region, "eventId": self.event_id,
                    "challengeId": "11", "fetchedAtMs": self.fetched,
                    "serverTimeMs": int(self.now * 1000) + self.clock_offset, "stale": self.stale, "rows": self.rows}
            if self.change:
                self.change()
        return json.dumps(body).encode(), {}


class HaneokaCutoffTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name)
        self.fixture = Fixture()
        self.source = self.repository()

    def repository(self):
        return HaneokaEventCutoffRepository(self.path / "cache", transport=self.fixture,
                                            clock=lambda: self.fixture.now, monotonic=lambda: self.fixture.now)

    def board(self, source=None, region="jp"):
        source = source or self.source
        event = source.event(region, source.deadline())
        return event, source.board(event, event.songs[0], source.deadline())

    def test_four_regions_same_rows_holes_zero_and_public_decimal_id(self):
        for region in ("jp", "tw", "kr", "en"):
            event, board = self.board(region=region)
            self.assertEqual(board.scores, (100, None, 0))
            self.assertEqual(board.player_id(1), "1234567890123456789")
            self.assertEqual(board.player_name(3), "zero")
            self.assertIsNone(board.player_id(2))
            self.assertEqual((board.source, board.time_kind, board.contract_version), ("haneoka", TIME_KIND, CONTRACT))
            self.assertFalse(event.challenge_order_verified)
            self.assertFalse(event.banner)
        self.assertTrue(all(url.startswith("https://haneoka.org/api/v1/game/records/") for url in self.fixture.calls))
        self.assertEqual(len(self.fixture.calls), 8)

    def test_precision_boundaries_keep_original_positions(self):
        values = [MAX_SCORE, MAX_SCORE + 1, 0, True, -1, "100", 1.5, None]
        self.fixture.rows = [{"rank": i, "score": score, "profileId": "99"} for i, score in enumerate(values, 1)]
        _, board = self.board()
        self.assertEqual(board.scores, (MAX_SCORE, None, 0, None, None, None, None, None))
        self.assertTrue(board.notes)
        self.assertIsNone(board.player_id(2))

    def test_wrong_rank_or_region_never_sort_or_publish(self):
        self.fixture.rows[0]["rank"] = 2
        self.assertFalse(self.board()[1].scores)
        self.fixture.now += 61
        self.fixture.rows[0]["rank"] = 1
        self.fixture.wrong_region = True
        self.assertFalse(self.board()[1].scores)

    def test_restart_verified_cache_fallback_expiry_and_no_old_network(self):
        self.board()
        self.fixture.now += 61
        self.fixture.failure = True
        restored = self.repository()
        _, board = self.board(restored)
        self.assertEqual(board.scores, (100, None, 0))
        self.assertEqual(board.quality, "fallback")
        self.fixture.now += 601
        with self.assertRaises(SourceError):
            restored.event("jp", restored.deadline())
        self.assertTrue(all("haneoka.org" in url for url in self.fixture.calls))

    def test_corrupted_cache_rejected(self):
        self.board()
        for path in (self.path / "cache").glob("*.json"):
            path.write_text('{"schema":1,"schema":1}', encoding="utf-8")
        self.fixture.failure = True
        with self.assertRaises(SourceError):
            self.repository().event("jp", self.source.deadline())

    def test_atomic_write_failure_keeps_memory(self):
        with patch("ournotes_bot.sources.moenotes_events.os.replace", side_effect=OSError):
            self.assertEqual(self.board()[1].score(1), 100)
        self.fixture.failure = True
        self.assertEqual(self.board()[1].score(1), 100)

    def test_deep_json_response_falls_back_and_bad_disk_cache_is_ignored(self):
        # Python 3.12's C JSON decoder has a separate recursion budget.
        depth = 10000
        raw = b'{"nested":' + b'[' * depth + b'0' + b']' * depth + b'}'
        event, first = self.board()
        self.fixture.now += 61
        self.source.transport = lambda *args: (raw, {})
        board = self.source.board(event, event.songs[0], self.source.deadline())
        self.assertEqual(board.scores, first.scores)
        self.assertEqual(board.quality, "fallback")
        current = self.source.event("jp", self.source.deadline())
        self.assertEqual(current.event_id, event.event_id)
        for path in (self.path / "cache").glob("*.json"):
            path.write_bytes(raw)
        self.assertEqual(self.board(self.repository())[1].scores, first.scores)

    def test_late_response_does_not_replace_newer_snapshot(self):
        first = self.board()[1]
        self.fixture.now += 61
        self.fixture.fetched -= 1000
        self.fixture.rows[0]["score"] = 200
        self.assertEqual(self.board()[1].fetched_ms, first.fetched_ms)
        self.assertEqual(self.board()[1].score(1), 100)

    def test_event_change_while_board_in_flight_rejected(self):
        event = self.source.event("jp", self.source.deadline())
        self.fixture.change = lambda: self.source._event_identities.update(jp=("changed",))
        self.assertFalse(self.source.board(event, event.songs[0], self.source.deadline()).scores)

    def test_same_challenge_different_music_never_reuses_cache(self):
        self.board()
        self.fixture.now += 31
        self.fixture.music = "102"
        self.fixture.rows[0]["score"] = 200
        self.assertEqual(self.board()[1].score(1), 200)

    def test_missing_future_old_and_stale_source_time(self):
        for stamp, stale in ((None, False), (int(self.fixture.now * 1000) + 2000, False),
                             (int(self.fixture.now * 1000) - 601000, False)):
            self.source._entries.clear()
            for path in (self.path / "cache").glob("*.json"):
                path.unlink()
            self.fixture.fetched, self.fixture.stale = stamp, stale
            self.assertFalse(self.board()[1].scores)
        self.source._entries.clear()
        for path in (self.path / "cache").glob("*.json"):
            path.unlink()
        self.fixture.fetched, self.fixture.stale = int(self.fixture.now * 1000), True
        self.assertEqual(self.board()[1].quality, "stale")

    def test_clock_offset_displays_four_regions_without_warning_and_keeps_raw_time(self):
        for offset in (124000, -124000):
            self.fixture.now += 300
            self.fixture.clock_offset = offset
            self.fixture.fetched = int(self.fixture.now * 1000) + offset - 2000
            for region in ("jp", "tw", "kr", "en"):
                with self.subTest(offset=offset, region=region):
                    _, board = self.board(region=region)
                    self.assertEqual(board.scores, (100, None, 0))
                    self.assertEqual(board.player_id(1), "1234567890123456789")
                    self.assertEqual(board.fetched_ms, self.fixture.fetched)
                    self.assertEqual(board.age_seconds, 2)
                    self.assertEqual(board.status, "源榜单快照")
                    self.assertFalse(any("时钟" in note for note in board.notes))
            answer = execute_cutoff(parse_cutoff("日服 歌曲ID=101 T1"),
                                    SimpleNamespace(event_cutoffs=self.source, aliases={}))
            self.assertEqual(answer.status, "success")
            self.assertNotIn("时钟", answer.text)
            self.assertTrue(render_cutoff(answer)[0].image)

    def test_clock_offset_skips_history_in_sampler_and_breaks_on_recovery(self):
        from ournotes_bot.sources.cutoff_sampler import HistorySampler
        history = HaneokaCutoffHistory(self.path / "history.db", min_free_mb=0,
                                      clock=lambda: self.fixture.now)
        self.source.history = history
        event, first = self.board()
        self.assertEqual(history.status()["points"], 1)
        self.fixture.now += 61
        self.fixture.clock_offset = 124000
        self.fixture.fetched = int(self.fixture.now * 1000) + 122000
        sampler = HistorySampler(self.source, clock=lambda: self.fixture.now)
        with self.assertLogs('ournotes_bot.sources.cutoff_history', level='WARNING') as logs:
            sampler.sample_once()
        self.assertEqual(len(logs.output), 4)
        self.assertIn('采样告警', sampler.last_state)
        self.assertEqual(history.status()["points"], 1)
        with closing(sqlite3.connect(history.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM gaps WHERE code='clock_skew'").fetchone()[0], 4)
            rows = db.execute('SELECT source_ms,server_ms,received_ms,scores,source,time_kind,contract_version FROM clock_quarantine').fetchall()
            self.assertEqual(len(rows), 4)
            self.assertTrue(all(json.loads(row[3]) == [100, None, 0] for row in rows))
            self.assertTrue(all(row[0] == self.fixture.fetched and row[1] - row[2] == 124000 for row in rows))
        restarted = HaneokaCutoffHistory(history.path, min_free_mb=0)
        self.assertEqual(len(restarted.status()['clock_pending']), 4)
        self.assertIn('告警', restarted.status_text())
        self.assertIn('隔离保留', restarted.status_text())
        self.assertIn('隔离保留', ' '.join(self.board()[1].notes))
        self.assertEqual(restarted.status()['quarantined'], 4)
        # Recovery advances beyond the old skewed sample; source time is never rewritten.
        self.fixture.now += 300
        self.fixture.clock_offset = 0
        self.fixture.fetched = int(self.fixture.now * 1000) - 2000
        self.board()
        points = history.read(event, first.song, (1,)).points
        self.assertEqual([point.time_ms for point in points], [first.fetched_ms, self.fixture.fetched])
        self.assertEqual([point.break_before for point in points], [False, True])
        # Success for JP must not hide the other three regions' outstanding failures.
        self.assertEqual(len(restarted.status()['clock_pending']), 3)
        sampler.sample_once()
        self.assertFalse(restarted.status()['clock_pending'])
        self.assertNotIn('告警', restarted.status_text())
        self.assertEqual(restarted.status()['quarantined'], 4)

    def test_clock_quarantine_rollback_storage_failure_and_disabled(self):
        event, board = self.board()
        skewed = replace(board, server_ms=board.received_ms + 130000,
                         fetched_ms=board.received_ms + 128000, http_date_ms=None)
        history = HaneokaCutoffHistory(self.path / 'quarantine.db', min_free_mb=0)
        self.assertEqual(history.record(event, skewed), 'clock_skew')
        self.assertEqual(history.record(event, skewed), 'clock_skew')
        self.assertEqual(history.status()['quarantined'], 1)
        self.assertFalse(history.read(event, board.song, (1,)).points)
        # An old schema3 writer still reads/writes its tables and preserves isolation.
        old = OpenCutoffHistory(history.path, min_free_mb=0)
        old.source, old.time_kind, old.contract_version, old.schema_version = history.source, history.time_kind, history.contract_version, 3
        self.assertFalse(old.read(event, board.song, (1,)).points)
        self.assertEqual(old.record(event, board), 'valid')
        self.assertEqual(history.status()['quarantined'], 1)
        self.assertEqual(history.read(event, board.song, (1,)).points[0].scores, (100,))
        with patch.object(history, '_connect', side_effect=sqlite3.OperationalError('locked')):
            self.assertEqual(history.record(event, skewed), 'storage_error')
        history.enabled = False
        self.assertEqual(history.record(event, skewed), 'disabled')
        history.enabled = True
        history.disk_usage = lambda _: SimpleNamespace(free=0)
        history.min_free_bytes = 1
        self.assertEqual(history.record(event, skewed), 'low_disk')

    def test_late_valid_observation_does_not_clear_newer_clock_alarm(self):
        event, board = self.board()
        history = HaneokaCutoffHistory(self.path / 'late.db', min_free_mb=0)
        skewed = replace(board, received_ms=board.received_ms + 300000,
                         server_ms=board.received_ms + 430000, fetched_ms=None, http_date_ms=None)
        history.record(event, skewed)
        history.record(event, skewed)
        self.assertEqual(history.status()['quarantined'], 1)
        history.record(event, board)
        self.assertEqual(len(history.status()['clock_pending']), 1)

    def test_quarantine_and_gap_are_atomic_on_write_failure(self):
        event, board = self.board()
        history = HaneokaCutoffHistory(self.path / 'atomic.db', min_free_mb=0)
        history.record(event, board)
        with closing(sqlite3.connect(history.path)) as db, db:
            db.execute("CREATE TRIGGER reject_gap BEFORE INSERT ON gaps BEGIN SELECT RAISE(ABORT, 'test write failure'); END")
        skewed = replace(board, server_ms=board.received_ms + 130000,
                         fetched_ms=board.received_ms + 128000, http_date_ms=None)
        self.assertEqual(history.record(event, skewed), 'storage_error')
        state = history.status()
        self.assertEqual(state['quarantined'], 0)
        self.assertFalse(state['clock_pending'])
        self.assertIn('历史库不可用', history.status_text())

    def test_http_date_calibrates_four_regions_and_preserves_audit_and_rollback(self):
        from email.utils import formatdate
        from ournotes_bot.sources.cutoff_sampler import HistorySampler
        original = self.fixture.__call__
        def transport(url, timeout):
            raw, headers = original(url, timeout)
            return raw, {"Date": formatdate(self.fixture.now, usegmt=True)}
        self.source.transport = transport
        history = HaneokaCutoffHistory(self.path / "history.db", min_free_mb=0,
                                      clock=lambda: self.fixture.now)
        self.source.history = history
        for offset in (130000, -130000, 0):
            self.fixture.now += 300
            self.fixture.clock_offset = offset
            self.fixture.fetched = int(self.fixture.now * 1000) + offset - 30000
            HistorySampler(self.source, clock=lambda: self.fixture.now).sample_once()
        self.assertEqual(history.status()["points"], 12)
        with closing(sqlite3.connect(history.path)) as db:
            rows = db.execute("SELECT source_ms,received_ms,quality FROM observations").fetchall()
            self.assertTrue(all(received - stamp == 30000 and quality == "valid" for stamp, received, quality in rows))
            audit = db.execute("SELECT raw_source_ms,raw_server_ms,http_date_ms,source_ms FROM clock_calibrations").fetchall()
            self.assertEqual(len(audit), 8)
            self.assertTrue(all(raw_server - raw_source == date - stamp == 30000 for raw_source, raw_server, date, stamp in audit))
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 3)
        event, board = self.board()
        # Existing schema3 readers can still read/write, without dropping audit.
        old = OpenCutoffHistory(history.path, min_free_mb=0)
        old.source, old.time_kind, old.contract_version, old.schema_version = history.source, history.time_kind, history.contract_version, 3
        self.assertEqual(len(old.read(event, board.song, (1,)).points), 3)
        self.assertEqual(old.record(event, board), "duplicate")
        self.assertEqual(len(history.read(event, board.song, (1,)).points), 3)

    def test_http_date_must_be_current_and_keeps_raw_cache_times(self):
        from email.utils import formatdate
        original = self.fixture.__call__
        self.fixture.clock_offset = 130000
        self.fixture.fetched = int(self.fixture.now * 1000) + 100000
        for header in ("bad", formatdate(self.fixture.now + 130, usegmt=True),
                       formatdate(self.fixture.now - 30, usegmt=True)):
            source = self.repository()
            source.transport = lambda url, timeout: (original(url, timeout)[0], {"Date": header})
            history = HaneokaCutoffHistory(self.path / "history.db", min_free_mb=0)
            source.history = history
            self.assertEqual(self.board(source)[1].http_date_ms, None)
            self.assertEqual(history.status()["points"], 0)
            source._entries.clear()
            for path in (self.path / "cache").glob("*.json"):
                path.unlink()
        source = self.repository()
        source.transport = lambda url, timeout: (original(url, timeout)[0], {"Date": formatdate(self.fixture.now, usegmt=True)})
        event, board = self.board(source)
        restored = self.repository()
        cached = self.board(restored)[1]
        self.assertEqual((cached.fetched_ms, cached.server_ms, cached.http_date_ms),
                         (board.fetched_ms, board.server_ms, int(self.fixture.now * 1000)))
        self.assertEqual(cached.fetched_ms, self.fixture.fetched)

    def test_clock_offset_cache_restart_fallback_expiry_and_late_history(self):
        self.fixture.clock_offset = 124000
        self.fixture.fetched += 122000
        event, first = self.board()
        history = HaneokaCutoffHistory(self.path / "history.db", min_free_mb=0,
                                      clock=lambda: self.fixture.now)
        restored = self.repository()
        restored.history = history
        self.fixture.now += 61
        self.fixture.failure = True
        _, fallback = self.board(restored)
        self.assertEqual(fallback.scores, first.scores)
        self.assertEqual(fallback.quality, "fallback")
        self.assertEqual(fallback.age_seconds, 63)
        self.assertEqual(history.status()["points"], 0)
        self.assertTrue(any("历史采集告警" in note for note in fallback.notes))
        self.fixture.now += 61
        self.fixture.failure = False
        self.fixture.fetched -= 1000  # late and still clock-skewed
        self.assertEqual(self.board(restored)[1].fetched_ms, first.fetched_ms)
        self.assertEqual(history.status()["points"], 0)
        self.fixture.now += 601
        self.fixture.failure = True
        self.assertFalse(restored.board(event, event.songs[0], restored.deadline()).scores)

    def test_strict_json_and_size_and_route_guards(self):
        for raw in (b'{"region":"jp","region":"jp"}', b'{"x":NaN}', b' ' * 524289):
            self.source.transport = lambda *args: (raw, {})
            with self.assertRaises(SourceError):
                self.source._json("https://haneoka.org/api/v1/game/records/jp/events/current", self.source.deadline())
        for url in ("https://api.bdon.moe/jp/events/current", "https://haneoka.org/api/v1/game/records/jp/songs/1/ranking"):
            with self.assertRaises(SourceError):
                self.source._get(url, self.source.deadline())

    def test_query_unknown_ordinal_id_and_rendering(self):
        repo = SimpleNamespace(event_cutoffs=self.source, aliases={})
        ordinal = execute_cutoff(parse_cutoff("日服 歌曲1"), repo)
        self.assertEqual(ordinal.status, "data_unavailable")
        self.assertIn("原序号", ordinal.text)
        answer = execute_cutoff(parse_cutoff("日服 歌曲ID=101 T1"), repo)
        self.assertEqual(answer.status, "success")
        self.assertIn("挑战ID 11", answer.text)
        self.assertIn("Haneoka / MoeNotes", answer.text)
        pages = render_cutoff(answer)
        self.assertTrue(pages[0].image)

    def test_three_histories_identity_free_and_rollback_preserves_old_files(self):
        event, board = self.board()
        old = CutoffHistory(self.path / "old.db", min_free_mb=0)
        formal = OpenCutoffHistory(self.path / "open.db", min_free_mb=0)
        new = HaneokaCutoffHistory(self.path / "new.db", min_free_mb=0)
        old.record(event, replace(board, source="tracker", fetched_ms=board.fetched_ms - 2000))
        formal.record(event, replace(board, source="open", time_kind="upstream_fetched",
                                     contract_version="moenotes:challenge-ranking/1", fetched_ms=board.fetched_ms - 1000))
        before = (old.path.read_bytes(), formal.path.read_bytes())
        history = SourceHistory(old, formal, haneoka=new, active="haneoka")
        self.assertEqual(history.record(event, board), "valid")
        self.assertEqual(before, (old.path.read_bytes(), formal.path.read_bytes()))
        points = history.read(event, event.songs[0], (1, 2, 3)).points
        self.assertEqual([p.source for p in points], ["tracker", "open", "haneoka"])
        self.assertEqual([p.break_before for p in points], [False, True, True])
        with closing(sqlite3.connect(new.path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(observations)")}
            self.assertFalse(columns & {"player_ids", "player_names", "name", "deck"})
            self.assertEqual(db.execute("SELECT source,time_kind,contract_version FROM observations").fetchone(),
                             ("haneoka", TIME_KIND, CONTRACT))
        self.assertEqual(old.read(event, event.songs[0], (1,)).points[0].scores, (100,))
        self.assertEqual(HaneokaCutoffHistory(old.path, min_free_mb=0).record(event, board), "storage_error")
        with self.assertRaises(ValueError):
            SourceHistory(old, formal, haneoka=HaneokaCutoffHistory(old.path), active="haneoka")

    def test_source_composition_switches_without_network_or_file_creation(self):
        settings = SimpleNamespace(cutoff_source="haneoka", cache_file=self.path / "main.json",
                                   cutoff_history_file=None, moenotes_open_history_file=None,
                                   cutoff_history_enabled=True, cutoff_history_min_free_mb=0)
        repo = SimpleNamespace(event_cutoffs=self.source)
        configure_sources(repo, settings)
        self.assertIs(repo.event_cutoffs, self.source)
        self.assertEqual(repo.event_cutoffs.history.active, "haneoka")
        settings.cutoff_source = "tracker"
        configure_sources(repo, settings)
        self.assertNotIsInstance(repo.event_cutoffs, HaneokaEventCutoffRepository)
        self.assertFalse(list(self.path.rglob("*.sqlite3")))
