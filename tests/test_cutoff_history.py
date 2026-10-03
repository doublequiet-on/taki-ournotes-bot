"""History integrity, resource limits, restarts and runtime-only sampling."""
import asyncio
import json
import sqlite3
import tempfile
import threading
import unittest
from dataclasses import replace
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from ournotes_bot.sources.cutoff_history import CutoffHistory
from ournotes_bot.sources.cutoff_sampler import HistorySampler
from ournotes_bot.sources.moenotes_events import BoardSnapshot, EventSong, EventSnapshot, EventCutoffRepository
from test_event_cutoffs import PublicFixture

NOW = 1790937673000


def event(server="jp", ident="1", music="101"):
    return EventSnapshot(server, ident, "合成活动", "nowOn", None, None,
                         (EventSong("1", music, "合成歌曲", collect_status="collecting", position_source="responseOrder"),))


def board(evt, stamp=NOW, scores=(100, 0), received=None, **changes):
    return BoardSnapshot(evt.songs[0], scores, stamp, stamp, received or stamp, **changes)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "history.sqlite3"
        self.clock = NOW / 1000
        self.store = CutoffHistory(self.path, min_free_mb=0, clock=lambda: self.clock)
        self.evt = event()

    def test_lossless_positions_missing_zero_and_no_player_columns(self):
        values = (2**130, None, True, "99", -1, 0) + tuple(range(100))
        self.assertEqual(self.store.record(self.evt, board(self.evt, scores=values)), "valid")
        points = self.store.read(self.evt, self.evt.songs[0], (1, 2, 3, 4, 5, 6, 100)).points
        self.assertEqual(points[0].scores, (2**130, None, None, None, None, 0, 93))
        with closing(sqlite3.connect(self.path)) as db:
            stored = json.loads(db.execute("SELECT scores FROM observations").fetchone()[0])
            self.assertEqual(len(stored), 100)
            columns = {r[1] for r in db.execute("PRAGMA table_info(observations)")}
            self.assertFalse(columns & {"players", "user_id", "name", "deck"})

    def test_dedup_flat_new_time_and_restart(self):
        b = board(self.evt)
        self.store.record(self.evt, b)
        self.assertEqual(self.store.record(self.evt, b), "duplicate")
        self.store.record(self.evt, board(self.evt, NOW + 300000))
        restored = CutoffHistory(self.path, min_free_mb=0)
        self.assertEqual(restored.status()["points"], 2)
        points = restored.read(self.evt, self.evt.songs[0], (1,)).points
        self.assertEqual([p.scores for p in points], [(100,), (100,)])

    def test_identity_scopes_and_same_board_music_conflict(self):
        for e in (self.evt, event("tw"), event(ident="2")):
            self.store.record(e, board(e))
        wrong = event(music="102")
        self.assertEqual(self.store.record(wrong, board(wrong, NOW + 300000)), "identity_conflict")
        self.assertEqual(len(self.store.read(self.evt, self.evt.songs[0], (1,)).points), 1)
        self.assertEqual(len(self.store.read(event("tw"), event("tw").songs[0], (1,)).points), 1)

    def test_late_points_sorted_and_conflicts_never_chosen(self):
        self.store.record(self.evt, board(self.evt, NOW + 600000, (200,)))
        self.store.record(self.evt, board(self.evt, NOW, (100,)))
        self.assertEqual(self.store.record(self.evt, board(self.evt, NOW, (150,))), "conflict")
        self.assertEqual(self.store.record(self.evt, board(self.evt, NOW, (150,))), "conflict")
        points = self.store.read(self.evt, self.evt.songs[0], (1,)).points
        self.assertEqual([p.time_ms for p in points], [NOW, NOW + 600000])
        self.assertEqual(points[0].scores, (None,))
        self.assertEqual(points[1].scores, (200,))

    def test_source_and_local_clocks_and_periods(self):
        unknown = replace(board(self.evt), fetched_ms=None)
        self.assertEqual(self.store.record(self.evt, unknown), "unknown_time")
        future = replace(board(self.evt, NOW + 100000), server_ms=NOW)
        self.assertEqual(self.store.record(self.evt, future), "clock_unknown")
        # Local clock drift doesn't move an otherwise consistent source timestamp.
        self.store.record(self.evt, board(self.evt, NOW + 300000, received=NOW + 3600000))
        points = self.store.read(self.evt, self.evt.songs[0], (1,)).points
        self.assertEqual(points[-1].time_ms, NOW + 300000)
        self.assertEqual(points[-1].scores, (100,))
        bounded = replace(self.evt, songs=(replace(self.evt.songs[0], effective_start_ms=NOW + 500000),))
        self.assertEqual(self.store.record(bounded, board(bounded, NOW + 400000)), "outside_period")

    def test_failures_and_long_intervals_break_lines(self):
        self.store.record(self.evt, board(self.evt))
        self.clock += 60
        self.store.failure("jp", "1", "1", "network")
        self.store.record(self.evt, board(self.evt, NOW + 300000))
        self.store.record(self.evt, board(self.evt, NOW + 1500000))
        points = self.store.read(self.evt, self.evt.songs[0], (1,)).points
        self.assertEqual([p.break_before for p in points], [False, True, True])

    def test_low_disk_pauses_without_deleting_and_recovers(self):
        self.store.record(self.evt, board(self.evt))
        before = self.path.read_bytes()
        self.store.min_free_bytes = 512 * 1024**2
        self.store.disk_usage = lambda _: SimpleNamespace(free=511 * 1024**2)
        self.assertEqual(self.store.record(self.evt, board(self.evt, NOW + 300000)), "low_disk")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("磁盘不足", self.store.status_text())
        self.store.disk_usage = lambda _: SimpleNamespace(free=513 * 1024**2)
        self.assertEqual(self.store.record(self.evt, board(self.evt, NOW + 300000)), "valid")

    def test_lock_and_corruption_fail_closed_without_recreating(self):
        self.store.record(self.evt, board(self.evt))
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("BEGIN EXCLUSIVE")
            self.assertEqual(self.store.record(self.evt, board(self.evt, NOW + 300000)), "storage_error")
            db.rollback()
        self.path.write_bytes(b"damaged-database")
        self.assertEqual(self.store.record(self.evt, board(self.evt)), "storage_error")
        self.assertEqual(self.path.read_bytes(), b"damaged-database")
        self.assertIn("不可用", self.store.read(self.evt, self.evt.songs[0], (1,)).warning)

    def test_reads_are_bounded_and_keep_all_disk_observations(self):
        for i in range(4):
            self.store.record(self.evt, board(self.evt, NOW + i * 300000))
        view = self.store.read(self.evt, self.evt.songs[0], (1,), max_points=3)
        self.assertFalse(view.points)
        self.assertIn("预算", view.warning)
        self.assertEqual(self.store.status()["points"], 4)

    def test_source_records_query_snapshots_and_keeps_newer_current(self):
        fixture = PublicFixture()
        source = EventCutoffRepository(Path(self.tmp.name) / "current", transport=fixture,
                                      clock=lambda: fixture.now, monotonic=lambda: fixture.now)
        source.history = self.store
        evt = source.event("jp", source.deadline())
        first = source.board(evt, evt.songs[0], source.deadline())
        fixture.now += 301
        fixture.headers["x-fetched-at"] = str(first.fetched_ms - 300000)
        fixture.players[0]["score"] = 1
        second = source.board(evt, evt.songs[0], source.deadline())
        self.assertEqual(second.scores, first.scores)
        self.assertEqual(self.store.status()["points"], 2)


class SamplerTests(unittest.TestCase):
    def test_cancelled_worker_cannot_restart_while_thread_is_in_flight(self):
        async def run():
            sampler = HistorySampler(SimpleNamespace(history=None))
            started, release = threading.Event(), threading.Event()
            def blocked():
                started.set()
                release.wait(2)
            sampler.sample_once = blocked
            first = sampler.start()
            await asyncio.to_thread(started.wait, 1)
            sampler.stop()
            await asyncio.sleep(0)
            self.assertIs(first, sampler.start())
            release.set()
            await asyncio.gather(first, return_exceptions=True)
            self.assertTrue(first.done())
        asyncio.run(run())

    def test_four_servers_and_foreground_yield_archives_and_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            fx = PublicFixture()
            source = EventCutoffRepository(Path(folder) / "cache", transport=fx, clock=lambda: fx.now, monotonic=lambda: fx.now)
            source.history = CutoffHistory(Path(folder) / "history.sqlite3", min_free_mb=0)
            sampler = HistorySampler(source, clock=lambda: fx.now)
            self.assertIsNone(sampler.task)
            with source.foreground():
                sampler.sample_once()
            self.assertFalse(fx.calls)
            sampler.sample_once()
            self.assertEqual(source.history.status()["points"], 12)
            source.event = Mock(side_effect=lambda server, _: replace(event(server), songs=(replace(event(server).songs[0], collect_status="archived"),)))
            source.board = Mock()
            sampler.sample_once()
            self.assertEqual(source.event.call_count, 4)
            source.board.assert_not_called()
            sampler.sample_once()
            self.assertEqual(source.event.call_count, 4)
            fx.now += 901
            sampler.sample_once()
            self.assertEqual(source.event.call_count, 8)

    def test_worker_start_is_idempotent_and_stop_cancels(self):
        async def run():
            source = SimpleNamespace(history=None)
            sampler = HistorySampler(source)
            first = sampler.start()
            self.assertIs(first, sampler.start())
            await asyncio.sleep(0.01)
            sampler.stop()
            await asyncio.gather(first, return_exceptions=True)
            self.assertTrue(first.done())
            self.assertTrue(sampler.stopping.is_set())
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
