"""Formal transport/identity/time/history contracts with synthetic Bearer values."""
import copy
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock, patch

from ournotes_bot.config import Settings
from ournotes_bot.data import SongRepository
from ournotes_bot.query.event_cutoff_query import execute_cutoff, parse_cutoff
from ournotes_bot.sources.cutoff_history import (CutoffHistory, OpenCutoffHistory, SourceHistory, configure_sources)
from ournotes_bot.sources.cutoff_sampler import HistorySampler
from ournotes_bot.sources.moenotes_events import SourceError, SERVERS, BoardSnapshot
from ournotes_bot.sources.moenotes_open import (OpenClient, OpenEventCutoffRepository, CONTRACT, TIME_KIND, authenticated_get)
from test_event_cutoffs import PublicFixture, encoded
from test_cutoff_history import event, board, NOW


class FormalFixture:
    def __init__(self, public):
        self.public = public
        self.players = [{"score": 1000 - i, "playerData": {"id": f"000{i + 1}", "name": f"合成玩家{i}🌸"},
                         "highScoreDeck": {"privateTest": "never-cache"}} for i in range(100)]
        self.calls, self.error = [], None
        self.headers = {}

    def __call__(self, url, secret, timeout):
        self.calls.append(url)
        if self.error:
            raise SourceError(self.error, 20)
        return encoded({"players": self.players}), {
            "X-Moenotes-Fetched-At": str(int(self.public.now * 1000) - 30000),
            "Date": format_datetime(datetime.fromtimestamp(self.public.now, timezone.utc), usegmt=True), **self.headers}


class OpenContractTests(unittest.TestCase):
    def test_open_secret_environment_alias_and_precedence(self):
        for values, expected in (({"BDON_OPENPLATFORM": " synthetic-alias "}, "synthetic-alias"),
                                 ({"BDON_OPENPLATFORM": "alias", "MOENOTES_OPEN_SECRET": "primary"}, "primary"),
                                 ({"BDON_OPENPLATFORM": "alias", "MOENOTES_OPEN_SECRET": "  "}, "alias"),
                                 ({}, "")):
            with self.subTest(values=values), patch.dict(os.environ, values, clear=True), patch("ournotes_bot.config.load_dotenv"):
                settings = Settings.from_env()
                self.assertEqual(settings.moenotes_open_secret, expected)
                self.assertNotIn("moenotes_open_secret=", repr(settings))
                self.assertEqual(settings.cutoff_source, "haneoka")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.public = PublicFixture()
        self.formal = FormalFixture(self.public)
        self.tick = 1000
        self.client = OpenClient("synthetic-bearer", transport=self.formal, clock=lambda: self.public.now,
                                 monotonic=lambda: self.tick, state_file=self.root / "admission.json")
        self.source = OpenEventCutoffRepository(self.root / "open", transport=self.public, client=self.client,
                                                clock=lambda: self.public.now, monotonic=lambda: self.tick)
        self.repo = SongRepository("https://bdon.yatta.moe", self.root / "catalog.json")
        self.repo.event_cutoffs = self.source

    def get(self, server="jp", n=0):
        e = self.source.event(server, self.source.deadline())
        return e, self.source.board(e, e.songs[n], self.source.deadline())

    def advance(self, seconds=61):
        self.public.now += seconds
        self.tick += seconds

    def test_four_regions_operation_identity_and_12_challenge_load(self):
        for server in SERVERS:
            e = self.source.event(server, self.source.deadline())
            boards = self.source.boards(e, e.songs, self.source.deadline())
            self.assertEqual(len(boards), 3)
            self.assertEqual(boards[0].source, "open")
        self.assertEqual(len(self.formal.calls), 12)
        self.assertTrue(all("/event/challenge-ranking?challengeMusicId=" in u for u in self.formal.calls))
        self.assertTrue(all("eventId=" not in u and "musicId=" not in u and "ranks=" not in u for u in self.formal.calls))
        self.assertFalse(any(u.endswith("/ranking") for u in self.public.calls))
        hk = execute_cutoff(parse_cutoff("hk 20-40 歌曲1 仅数值"), self.repo)
        self.assertEqual(hk.event.server, "tw")
        self.assertEqual(hk.ranks, tuple(range(20, 41)))

    def test_row_positions_zero_holes_ids_names_and_sensitive_projection(self):
        self.formal.players = [None, {}, {"score": 0, "playerData": {"id": "00012345678901234567", "name": "名字🌸"}},
                               {"score": None, "playerData": {"id": "999", "name": "隐去"}},
                               {"score": True}, {"score": 2**100, "playerData": {"id": 12345678901234567890}},
                               {"unexpected": 1}]
        e, b = self.get()
        self.assertEqual(b.scores, (None, 0, 0, None, None, 2**100, None))
        self.assertEqual(b.player_id(3), "00012345678901234567")
        self.assertEqual(b.player_name(3), "名字🌸")
        self.assertIsNone(b.player_id(4))
        self.assertEqual(b.player_id(6), "12345678901234567890")
        for p in self.root.rglob("*.json"):
            text = p.read_text(encoding="utf8")
            self.assertNotIn("synthetic-bearer", text)
            self.assertNotIn("Authorization", text)
            self.assertNotIn("highScoreDeck", text)
        self.assertNotIn("synthetic-bearer", repr(self.client))
        settings = Settings("", "", "", self.root / "cache.json", 6, moenotes_open_secret="synthetic-bearer")
        self.assertNotIn("synthetic-bearer", repr(settings))

    def test_source_time_required_date_not_substituted_and_expiry_identity_hidden(self):
        self.formal.headers["X-Moenotes-Fetched-At"] = ""
        e, b = self.get()
        self.assertEqual(b.quality, "unknown_time")
        self.assertIsNone(b.fetched_ms)
        self.assertEqual(b.scores, ())
        self.assertIsNone(b.player_name(1))
        self.formal.headers.clear()
        self.advance()
        e, good = self.get()
        self.assertEqual(good.time_kind, TIME_KIND)
        self.assertEqual(good.age_seconds, 30)
        self.formal.error = "network"
        self.advance(121)
        e, stale = self.get()
        self.assertEqual(stale.scores, good.scores)
        self.assertIn("旧快照", stale.status)
        self.advance(601)
        e, expired = self.get()
        self.assertEqual(expired.scores, ())
        self.assertIsNone(expired.player_id(1))

    def test_clock_cache_fetch_time_late_and_same_time_conflict(self):
        store = OpenCutoffHistory(self.root / "history-v2.sqlite3", min_free_mb=0)
        self.source.history = store
        e, first = self.get()
        self.assertEqual(store.status()["points"], 1)
        self.advance()
        self.formal.headers["X-Moenotes-Fetched-At"] = str(first.fetched_ms - 1000)
        e, late = self.get()
        self.assertEqual(late.fetched_ms, first.fetched_ms)
        self.assertEqual(store.status()["points"], 2)
        self.advance()
        self.formal.headers["X-Moenotes-Fetched-At"] = str(first.fetched_ms)
        self.formal.players[0]["score"] += 1
        e, conflict = self.get()
        self.assertEqual(conflict.quality, "conflict")
        self.assertEqual(conflict.scores, ())

    def test_future_source_clock_keeps_scores_and_identity_unavailable(self):
        store = OpenCutoffHistory(self.root / "future-clock.sqlite3", min_free_mb=0)
        self.source.history = store
        self.formal.headers["X-Moenotes-Fetched-At"] = str(int(self.public.now * 1000) + 121000)
        e, result = self.get()
        self.assertEqual(result.quality, "clock_unknown")
        self.assertIn("超前参考时钟约121秒", result.status)
        self.assertFalse(result.scores)
        self.assertIsNone(result.player_id(1))
        self.assertIsNone(result.player_name(1))
        view = store.read(e, e.songs[0], (1,))
        self.assertTrue(all(p.quality != "valid" and all(v is None for v in p.scores) for p in view.points))

    def test_identity_change_during_fetch_and_local_top100(self):
        e = self.source.event("jp", self.source.deadline())
        original = self.client.transport
        def changed(url, secret, timeout):
            self.source._event_identities["jp"] = ("different",)
            return original(url, secret, timeout)
        self.client.transport = changed
        b = self.source.board(e, e.songs[0], self.source.deadline())
        self.assertEqual(b.scores, ())
        self.assertIn("关联变化", b.status)
        self.source._event_identities.clear()
        self.advance()
        self.client.transport = original
        result = execute_cutoff(parse_cutoff("jp 歌曲1 100"), self.repo)
        self.assertEqual(result.ranks, tuple(range(90, 101)))
        self.assertEqual(result.boards[0].score(100), 901)

    def test_single_flight_foreground_sampling_cadence(self):
        e = self.source.event("jp", self.source.deadline())
        with ThreadPoolExecutor(max_workers=6) as pool:
            rows = list(pool.map(lambda _: self.source.board(e, e.songs[0], self.source.deadline()), range(6)))
        self.assertEqual(len(self.formal.calls), 1)
        self.assertEqual(len({r.fetched_ms for r in rows}), 1)
        sampler = HistorySampler(self.source, servers=("jp", "tw", "kr", "en"), interval=300)
        self.assertEqual(sampler.interval, 300)
        self.assertIsNone(sampler.task)

    def test_auth_latch_429_shared_cooldown_one_retry_not_found(self):
        for code in ("authentication", "permission"):
            client = OpenClient("test", transport=Mock(side_effect=SourceError(code)), monotonic=lambda: self.tick)
            for _ in range(2):
                with self.assertRaises(SourceError):
                    client.get("jp", "1", self.tick + 12)
            self.assertEqual(client.transport.call_count, 1)
            client.configure("changed")
            with self.assertRaises(SourceError):
                client.get("jp", "1", self.tick + 12)
            self.assertEqual(client.transport.call_count, 2)
        self.formal.error = "rate_limited"
        with self.assertRaises(SourceError):
            self.client.get("jp", "1", self.tick + 12)
        with self.assertRaises(SourceError):
            self.client.get("tw", "2", self.tick + 12)
        self.assertEqual(len(self.formal.calls), 1)
        restored = OpenClient("test", state_file=self.client.state_file, clock=lambda: self.public.now, monotonic=lambda: self.tick,
                              transport=Mock(side_effect=AssertionError("cooldown reset")))
        with self.assertRaises(SourceError):
            restored.get("jp", "1", self.tick + 12)
        self.advance(21)
        self.formal.error = "network"
        with self.assertRaises(SourceError):
            self.client.get("jp", "1", self.tick + 12)
        self.assertEqual(len(self.formal.calls), 3)
        self.formal.error = "not_found"
        with self.assertRaises(SourceError):
            self.client.get("jp", "1", self.tick + 12)
        self.assertEqual(len(self.formal.calls), 4)

    def test_no_daily_total_gate_and_minute_window(self):
        tick = [0.0]
        transport = Mock(return_value=(b'{}', {}))
        client = OpenClient("test", transport=transport, per_minute=2, monotonic=lambda: tick[0])
        for _ in range(10001):
            client.get("jp", "1", tick[0] + 12)
            tick[0] += 61
        self.assertEqual(client.calls, 10001)
        client.get("jp", "1", tick[0] + 12)
        client.get("jp", "2", tick[0] + 12)
        with self.assertRaises(SourceError) as error:
            client.get("jp", "3", tick[0] + 12)
        self.assertEqual(error.exception.code, "minute_limit")

    def test_anonymous_and_authenticated_origin_boundaries_empty_and_bad_contract(self):
        for url in ("https://assets.bdon.moe/jacket.webp", "https://bdon.moe/api/open/catalog", "http://bdon.moe/api/open/v1/moenotes/jp/event/challenge-ranking?challengeMusicId=1"):
            with self.assertRaises(SourceError):
                authenticated_get(url, "test", 1)
        for raw in (b'[]', b'{"players":null}', b'{"players": [], "error": {}}', b'{"players":NaN}', b'<html>',
                    b'{"data":{"players":[]}}', b'{"unexpected":1}'):
            client = OpenClient("test", transport=Mock(return_value=(raw, {})))
            with self.assertRaises(SourceError):
                client.get("jp", "1", client.monotonic() + 12)
        client = OpenClient("test", transport=Mock(return_value=(b'{}', {})))
        self.assertEqual(client.get("jp", "1", client.monotonic() + 12)[0]["scores"], [])

    def test_http_authentication_headers_redirect_and_safe_response_projection(self):
        calls = []
        class Stream:
            async def iter_chunked(self, size):
                yield b'{"players":[]}'
        class Response:
            status = 200
            headers = {"Content-Type": "application/json", "X-Moenotes-Fetched-At": "1791000000000",
                       "Date": "Wed, 01 Oct 2026 12:00:00 GMT", "Authorization": "synthetic-bearer"}
            content = Stream()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        class Session:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def get(self, url, **kwargs):
                calls.append((url, kwargs))
                return Response()
        with patch("aiohttp.ClientSession", return_value=Session()) as session:
            raw, headers = authenticated_get("https://bdon.moe/api/open/v1/moenotes/jp/event/challenge-ranking?challengeMusicId=1", "synthetic-bearer", 1)
        self.assertEqual(raw, b'{"players":[]}')
        self.assertEqual(calls[0][1]["headers"], {"Authorization": "Bearer synthetic-bearer"})
        self.assertFalse(calls[0][1]["allow_redirects"])
        self.assertFalse(session.call_args.kwargs["trust_env"])
        self.assertEqual(set(headers), {"x-moenotes-fetched-at", "date"})


class SourceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = CutoffHistory(self.root / "old-v1.sqlite3", min_free_mb=0)
        self.new = OpenCutoffHistory(self.root / "new-v2.sqlite3", min_free_mb=0)
        self.e = event()

    def test_two_files_lossless_source_segments_and_old_code_rollback(self):
        self.old.record(self.e, board(self.e, scores=(2**130, 0)))
        before = hashlib.sha256(self.old.path.read_bytes()).hexdigest()
        formal = board(self.e, NOW + 300000, scores=(2**131, 0), source="open", time_kind=TIME_KIND, contract_version=CONTRACT)
        self.new.record(self.e, formal)
        combined = SourceHistory(self.old, self.new, active="open")
        view = combined.read(self.e, self.e.songs[0], (1, 2))
        self.assertEqual([p.source for p in view.points], ["tracker", "open"])
        self.assertTrue(view.points[1].break_before)
        self.assertEqual(view.points[1].scores, (2**131, 0))
        self.assertIn("切换处断线", view.warning)
        self.assertEqual(before, hashlib.sha256(self.old.path.read_bytes()).hexdigest())
        self.assertEqual(len(CutoffHistory(self.old.path, min_free_mb=0).read(self.e, self.e.songs[0], (1,)).points), 1)
        self.assertEqual(self.old.record(self.e, formal), "wrong_source")
        with closing(sqlite3.connect(self.new.path)) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(db.execute("SELECT source,time_kind,contract_version FROM observations").fetchone(), ("open", TIME_KIND, CONTRACT))
            self.assertFalse({r[1] for r in db.execute("PRAGMA table_info(observations)")} & {"name", "player_id", "deck"})

    def test_paths_and_schema_fail_closed_no_time_no_valid_point(self):
        self.old.record(self.e, board(self.e))
        before = self.old.path.read_bytes()
        wrong = OpenCutoffHistory(self.old.path, min_free_mb=0)
        formal = board(self.e, source="open", time_kind=TIME_KIND, contract_version=CONTRACT)
        self.assertEqual(wrong.record(self.e, formal), "storage_error")
        self.assertEqual(self.old.path.read_bytes(), before)
        with self.assertRaises(ValueError):
            SourceHistory(self.old, wrong)
        self.assertEqual(self.new.record(self.e, replace(formal, fetched_ms=None)), "unknown_time")
        self.assertEqual(self.new.status()["points"], 0)

    def test_empty_partial_schema_creation_can_complete_atomically(self):
        from ournotes_bot.sources.cutoff_history import SCHEMA
        with closing(sqlite3.connect(self.new.path)) as db:
            db.executescript(SCHEMA)
            db.execute("ALTER TABLE observations ADD COLUMN source TEXT NOT NULL DEFAULT 'open'")
        formal = board(self.e, source="open", time_kind=TIME_KIND, contract_version=CONTRACT)
        self.assertEqual(self.new.record(self.e, formal), "valid")
        self.assertEqual(self.new.status()["points"], 1)

    def test_composition_no_credentials_default_rollback_and_sampling_unchanged(self):
        repo = SongRepository("", self.root / "catalog.json")
        settings = Settings("", "", "", repo.cache_file, 6, cutoff_history_min_free_mb=0)
        configure_sources(repo, settings)
        self.assertNotIsInstance(repo.event_cutoffs, OpenEventCutoffRepository)
        self.assertEqual(repo.event_cutoffs.history.active, "tracker")
        configure_sources(repo, replace(settings, cutoff_source="open"))
        self.assertIsInstance(repo.event_cutoffs, OpenEventCutoffRepository)
        with self.assertRaises(SourceError):
            repo.event_cutoffs.client.get("jp", "1", repo.event_cutoffs.deadline())
        configure_sources(repo, settings)
        self.assertNotIsInstance(repo.event_cutoffs, OpenEventCutoffRepository)
        self.assertEqual(settings.cutoff_sampling_interval, 300)


if __name__ == "__main__":
    unittest.main()
