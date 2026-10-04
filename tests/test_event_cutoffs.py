# L3
# Input: synthetic public-source responses, temporary caches, fake clocks/network/model/QQ.
# Output: A01-A22 regression evidence; real snapshots and art are preview evidence separately.
# Pos: Tests / event challenge cutoffs; see L2.md.
# Effects: isolated temporary files and offline Pillow images only; no real network or QQ.
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlsplit

from PIL import Image

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import handle_command, resolve_command
from ournotes_bot.config import Settings
from ournotes_bot.data import SongRepository
from ournotes_bot.query.event_cutoff_query import (CutoffRequest, execute_cutoff, parse_cutoff,
                                                  parse_natural_cutoff, event_status)
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff, load_artwork
from ournotes_bot.rendering.image_output import MAX_IMAGE_BYTES, MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS
from ournotes_bot.sources.moenotes_events import (EventCutoffRepository, SourceError, SERVERS,
                                                BoardSnapshot, EventSong, _retry_after, public_get)


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class PublicFixture:
    def __init__(self):
        self.now = 1790937673.0
        self.calls = []
        self.errors = {}
        self.players = [{"score": 20000000 - i * 12345} for i in range(100)]
        self.current_id = "1"
        self.status = "nowOn"
        self.headers = {}
        self.table_rows = {}
        for server in SERVERS:
            self.table_rows[server] = {
                "MasterEvent": [{"id": 1, "nameTextId": "evt", "storyChapterId": 6,
                                 "startAt": "2026/09/30 18:00:00", "endAt": "2026/10/08 20:59:59"}],
                "MasterChallengeMusic": [{"id": i, "eventId": 1, "liveMusicId": 100 + i} for i in range(1, 4)],
                "MasterLiveMusic": [{"id": 100 + i, "titleTextID": f"song{i}", "jacketAssetName": f"jkt_{i}"} for i in range(1, 5)],
                "MasterText": [{"id": "evt", "japanese": f"{server}活动 AtoZ"}] +
                              [{"id": f"song{i}", "japanese": name, "simplifiedChinese": name}
                               for i, name in enumerate(("夢我夢中", "长歌名 37", "123", "非本期歌曲"), 1)],
                "MasterStoryChapter": [{"id": 6, "banner": "ui_banner_chapter_6"}],
            }

    def table(self, server, name):
        return encoded({"_allData": [{"_" + k: v for k, v in row.items()} for row in self.table_rows[server][name]]})

    def __call__(self, url, timeout):
        self.calls.append(url)
        if not 0 < timeout <= 4:
            raise AssertionError(timeout)
        for part, error in self.errors.items():
            if part in url:
                raise error
        path = urlsplit(url).path
        if path == "/index.json":
            return encoded({"regions": {SERVERS[s][2]: {"entry": {"version": f"{s}-v1"},
                       "files": {name + ".json": hashlib.sha256(self.table(s, name)).hexdigest() for name in self.table_rows[s]}}
                       for s in SERVERS}}), {}
        if path == "/versions/current_version.json":
            return encoded({"regions": {s: {"resource_version": "1", "locales": {SERVERS[s][3]: {"snapshot": s}}} for s in SERVERS}}), {}
        if "/master/" in path:
            return self.table(path.split("/")[1], Path(path).stem), {}
        if path.endswith("/events/current"):
            return encoded({"eventId": self.current_id, "eventStatus": self.status, "startAt": 1790758800000,
                            "endAt": 1791460799000, "rankingDisabled": True,
                            "pointRanking": {"enabled": False, "collectStatus": "disabled"},
                            "challengeRankings": [{"challengeMusicId": str(i), "musicId": str(100 + i),
                               "rankingEnabled": True, "collectStatus": "collecting", "positionSource": "responseOrder",
                               "effectiveStartAt": 1790758800000, "effectiveEndAt": 1791460799000} for i in range(1, 4)]}), {}
        if path.endswith("/ranking"):
            return encoded({"players": self.players}), {"x-fetched-at": str(int(self.now * 1000) - 40000),
                    "x-server-time": str(int(self.now * 1000)), "x-position-source": "responseOrder", **self.headers}
        raise AssertionError("Unexpected URL " + url)


class CutoffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.http = PublicFixture()
        self.tick = 1000.0
        self.repo = SongRepository("https://offline.invalid", self.root / "main.json")
        self.source = EventCutoffRepository(self.root / "cutoff", transport=self.http,
                    clock=lambda: self.http.now, monotonic=lambda: self.tick)
        self.repo.event_cutoffs = self.source

    def advance(self, seconds):
        self.tick += seconds
        self.http.now += seconds

    def ask(self, command="/查榜线"):
        return resolve_command(command, self.repo).cutoff

    def test_overview_single_rank_and_integer_output(self):  # A01/A04/A13
        answer = self.ask()
        self.assertEqual(answer.request.server, "jp")
        self.assertEqual(answer.ranks, (1, 2, 3, 10, 100))
        self.assertEqual(len(answer.boards), 3)
        one = self.ask("/查榜线 夢我夢中")
        self.assertEqual(len(one.boards), 1)
        self.assertEqual(one.ranks, answer.ranks)
        for value in (1, 37, 100):
            precise = self.ask(f"/查榜线 夢我夢中 T{value}")
            self.assertEqual(precise.ranks, tuple(range(max(1, value-10), min(100, value+10)+1)))
            self.assertIn(str(self.http.players[value - 1]["score"]), precise.text)
        self.assertEqual(sum(url.endswith("/ranking") for url in self.http.calls), 3)

    def test_rank_only_queries_select_all_event_songs(self):
        for server in ("", "jp ", "hk ", "kr ", "en "):
            for token, rank in (("100", 100), ("t100", 100), ("T100", 100), ("37", 37), ("T1", 1)):
                with self.subTest(server=server, token=token):
                    answer = self.ask(f"/查榜线 {server}{token}")
                    self.assertEqual(answer.status, "success")
                    self.assertEqual(answer.request.server, "tw" if server == "hk " else server.strip() or "jp")
                    self.assertEqual(answer.request.query, "")
                    window = tuple(range(max(1,rank-10),min(100,rank+10)+1))
                    self.assertEqual(answer.ranks, window)
                    self.assertEqual([b.song.music_id for b in answer.boards], ["101", "102", "103"])
                    score = self.http.players[rank - 1]["score"]
                    self.assertEqual(answer.text.count(f"T{rank}：{score}"), 3)
                    for other in {1, 2, 3, 10, 100} - set(window):
                        self.assertNotIn(f"T{other}：", answer.text)
        self.assertEqual(sum(url.endswith("/ranking") for url in self.http.calls), 12)
        explicit = execute_cutoff(CutoffRequest(rank=100), self.repo)
        self.assertEqual(explicit.text, self.ask("/查榜线 100").text)

    def test_renamed_command_matches_natural_route_and_uses_new_help_label(self):
        from ournotes_bot.structured_query import QuerySpec
        direct = self.ask("/查榜线 jp 夢我夢中 T37")
        self.assertEqual(direct.ranks, tuple(range(27,48)))
        self.assertEqual(direct.boards[0].song.music_id, "101")
        for command in ("/查榜线 jp 夢我夢中 T37", "/榜线 jp 夢我夢中 T37"):
            natural = execute_cutoff(parse_natural_cutoff(command), self.repo)
            self.assertEqual(natural.text, direct.text)
            self.assertEqual(self.ask(command).text, direct.text)
        label = QuerySpec("event_cutoff", cutoff_request=direct.request).command_label()
        self.assertEqual(label, '查榜线 jp 歌名="夢我夢中" 37')
        help_text = handle_command("/帮助", self.repo)
        self.assertIn("/查榜线 [jp/hk/kr/en]", help_text)
        self.assertNotIn("/榜线 ", help_text)

    def test_servers_aliases_and_no_fallback(self):  # A02/A03
        for alias, expected in (("hk", "tw"), ("HK", "tw"), ("国服", "tw"), ("國服", "tw"), ("TW", "tw"), ("台服", "tw"), ("KR", "kr"), ("韩服", "kr"), ("en", "en"), ("英服", "en"), ("国际服", "en"), ("JP", "jp"), ("日服", "jp")):
            answer = self.ask("/查榜线 " + alias)
            self.assertEqual(answer.event.server, expected)
            self.assertTrue(answer.event.title.startswith(expected))
            self.assertIn(f"/{expected}/", answer.event.banner)
        self.assertEqual(self.ask().event.server, "jp")
        self.advance(301)
        self.http.errors["/tw/events/current"] = SourceError("not_found")
        before = len(self.http.calls)
        self.assertIn("国服", self.ask("/查榜线 hk").text)
        self.assertFalse(any("/jp/" in u for u in self.http.calls[before:]))

    def test_public_server_names_and_codes_preserve_source_identity(self):
        from ournotes_bot.structured_query import QuerySpec
        for code, name in (("jp", "日服"), ("hk", "国服"), ("kr", "韩服"), ("en", "英服")):
            direct = self.ask(f"/查榜线 {code} 夢我夢中 T50")
            self.assertTrue(direct.text.startswith(name + " · "))
            natural = execute_cutoff(parse_natural_cutoff(name + "夢我夢中前50现在多少分"), self.repo)
            self.assertEqual(natural.text, direct.text)
            label = QuerySpec("event_cutoff", cutoff_request=direct.request).command_label()
            self.assertEqual(label, f'查榜线 {code} 歌名="夢我夢中" 50')
            self.assertEqual(self.ask("/" + label).text, direct.text)
        calls = len(self.http.calls)
        current = self.ask("/查榜线 hk 夢我夢中 T50")
        for old in ("tw", "台服", "臺服", "國服"):
            self.assertEqual(self.ask(f"/查榜线 {old} 夢我夢中 T50").text, current.text)
        self.assertEqual(len(self.http.calls), calls)
        self.assertTrue(any("/tw/events/" in url for url in self.http.calls))
        self.assertFalse(any("/hk/" in url for url in self.http.calls))
        self.assertIn("/tw/zh-Hant/", current.boards[0].song.jacket)
        for message in (self.ask("/查榜线 server=us").text, parse_natural_cutoff("美服榜线多少").error):
            self.assertIn("日服（jp）、国服（hk）、韩服（kr）、英服（en）", message)

    def test_invalid_conditions_and_names(self):  # A05/A06
        for rank in ("0", "-1", "1.5", "101", "T0", "T101"):
            answer = self.ask("/查榜线 夢我夢中 " + rank)
            self.assertEqual(answer.status, "invalid_arguments", rank)
        for server in ("cn", "美服", "server=us"):
            self.assertEqual(self.ask("/查榜线 " + server + " 夢我夢中").status, "invalid_arguments")
        self.assertEqual(self.ask("/查榜线 不存在的歌").status, "unknown_entity")
        self.assertEqual(self.ask("/查榜线 非本期歌曲").status, "empty")
        for token in ("0", "-1", "1.5", "T0", "T101", "t101"):
            self.assertEqual(self.ask("/查榜线 " + token).status, "invalid_arguments", token)
        self.assertEqual(self.ask("/查榜线 123").status, "invalid_arguments")
        self.assertIsNone(self.ask('/查榜线 歌名="123"').request.rank)
        self.assertEqual(self.ask("/查榜线 长歌名 37").boards[0].song.music_id, "102")
        self.assertIsNone(self.ask("/查榜线 长歌名 37").request.rank)
        self.assertEqual(self.ask("/查榜线 长歌名 37 T37").request.rank, 37)
        with patch("ournotes_bot.query.entity_lexicon._aliases", return_value={"song": {"别名": "夢我夢中", "歧义": [101, 102]}}):
            self.assertEqual(self.ask("/查榜线 别名").boards[0].song.music_id, "101")
            self.assertEqual(self.ask("/查榜线 歧义").status, "ambiguous")

    def test_positions_holes_ties_invalid_scores_and_partial_board(self):  # A07/A08/A09
        self.http.players = [{"score": 123}, {"score": 123}, {}, None, {"score": 0}, {"score": True}, {"score": "7"}, {"score": -1}]
        answer = self.ask()
        board = answer.boards[0]
        self.assertEqual(board.scores, (123, 123, None, None, 0, None, None, None))
        self.assertEqual(board.score(5), 0)
        self.assertIsNone(board.score(100))
        self.assertIn("T100：暂无数据", answer.text)
        self.assertTrue(board.song.enabled)
        self.assertNotIn("player", "".join(p.read_text(encoding="utf-8") for p in (self.root / "cutoff").glob("*.json")))

    def test_current_song_matching_excludes_inactive_partial_and_alias_targets(self):  # A06
        inactive = self.http.table_rows["jp"]["MasterText"][-1]
        inactive.update(japanese="夢我夢中 外传 37", simplifiedChinese="夢我夢中 外传 37")
        self.assertEqual(self.ask("/查榜线 夢我").boards[0].song.music_id, "101")
        with patch("ournotes_bot.query.entity_lexicon._aliases", return_value={"song": {"共用别名": [101, 104]}}):
            self.assertEqual(self.ask("/查榜线 共用别名").boards[0].song.music_id, "101")
        outside = self.ask("/查榜线 夢我夢中 外传 37")
        self.assertEqual(outside.status, "empty")
        self.assertIsNone(outside.request.rank)
        self.assertIn("不是本期", outside.text)
        with patch("ournotes_bot.query.entity_lexicon._aliases", return_value={"song": {"真正歧义": [101, 102, 104]}}):
            ambiguous = self.ask("/查榜线 真正歧义")
        self.assertEqual(ambiguous.status, "ambiguous")
        self.assertIn("长歌名 37", ambiguous.text)
        self.assertNotIn("外传", ambiguous.text)

    def test_ascii_titles_and_aliases_resolve_before_unknown_server(self):  # A05/A06/A19
        active = self.http.table_rows["jp"]["MasterText"][1]
        active.update(japanese="GO WAY GO", simplifiedChinese="GO WAY GO")
        with patch("ournotes_bot.query.entity_lexicon._aliases", return_value={"song": {"my song": 101}}):
            for title in ("GO WAY GO", "my song"):
                for server in ("", "jp "):
                    answer = self.ask(f"/查榜线 {server}{title} T37")
                    self.assertEqual(answer.boards[0].song.music_id, "101")
                    self.assertEqual(answer.ranks, tuple(range(27,48)))
                request = parse_natural_cutoff(title + "100线多少")
                answer = execute_cutoff(request, self.repo)
                self.assertEqual(answer.boards[0].song.music_id, "101")
                self.assertEqual(answer.ranks, tuple(range(90,101)))
            before = len(self.http.calls)
            unknown = execute_cutoff(parse_natural_cutoff("cn GO WAY GO100线多少"), self.repo)
            self.assertEqual(unknown.status, "invalid_arguments")
            self.assertIn("未知服务器", unknown.text)
            self.assertEqual(len(self.http.calls), before)
            self.assertEqual(self.ask("/查榜线 jp my missing song").status, "unknown_entity")

    def test_duplicate_activity_response_preserves_valid_cache_then_recovers(self):  # A16
        first = self.ask()
        cache_path = self.source._path("current:jp")
        valid_cache = cache_path.read_bytes()
        broken = True
        def transport(url, timeout):
            raw, headers = self.http(url, timeout)
            if broken and url.endswith("/events/current"):
                body = json.loads(raw)
                body["challengeRankings"][1]["challengeMusicId"] = "1"
                raw = encoded(body)
            return raw, headers
        self.source.transport = transport
        self.advance(301)
        fallback = self.ask()
        self.assertEqual(fallback.event.songs, first.event.songs)
        self.assertIn("短期旧快照", fallback.text)
        self.assertEqual(cache_path.read_bytes(), valid_cache)
        before = len(self.http.calls)
        self.ask()
        self.assertEqual(len(self.http.calls), before)
        broken = False
        self.advance(31)
        recovered = self.ask()
        self.assertEqual(len(recovered.boards), 3)
        self.assertNotIn("短期旧快照", recovered.text)
        self.assertEqual(sum(u.endswith("/events/current") for u in self.http.calls), 3)

    def test_duplicate_activity_disk_cache_is_refetched_after_restart(self):  # A16
        self.ask()
        path = self.source._path("current:jp")
        cache = json.loads(path.read_text(encoding="utf-8"))
        cache["payload"]["challengeRankings"][1]["challengeMusicId"] = "1"
        path.write_text(json.dumps(cache), encoding="utf-8")
        self.repo.event_cutoffs = EventCutoffRepository(self.source.cache_dir, transport=self.http,
                    clock=lambda: self.http.now, monotonic=lambda: self.tick)
        answer = self.ask()
        self.assertEqual(len(answer.boards), 3)
        self.assertEqual(len({s.challenge_id for s in answer.event.songs}), 3)
        self.assertEqual(sum(u.endswith("/events/current") for u in self.http.calls), 2)

    def test_distinct_song_period_is_captured_in_all_views_and_text_fallback(self):  # A10/A12
        def transport(url, timeout):
            raw, headers = self.http(url, timeout)
            if url.endswith("/events/current"):
                body = json.loads(raw)
                body["challengeRankings"][0].update(effectiveStartAt=1790845200000, effectiveEndAt=1791287999000)
                raw = encoded(body)
            return raw, headers
        self.source.transport = transport
        answers = (self.ask(), self.ask("/查榜线 夢我夢中"), self.ask("/查榜线 夢我夢中 T37"))
        for answer in answers:
            self.assertEqual(answer.event.start_ms, 1790758800000)
            self.assertEqual(answer.event.end_ms, 1791460799000)
            self.assertEqual(answer.boards[0].song.effective_start_ms, 1790845200000)
            self.assertEqual(answer.boards[0].song.effective_end_ms, 1791287999000)
            self.assertIn("本曲榜单有效开始：2026-10-01 18:00:00 UTC+09:00", answer.text)
            self.assertIn("本曲榜单有效结束：2026-10-06 20:59:59 UTC+09:00", answer.text)
        before = len(self.http.calls)
        for answer in answers:
            pages = render_cutoff(answer)
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0].text, answer.text)
        self.assertEqual(len(self.http.calls), before)

    def test_missing_invalid_and_conflicted_song_periods_remain_unknown(self):  # A10/A12
        def transport(url, timeout):
            raw, headers = self.http(url, timeout)
            if url.endswith("/jp/events/current"):
                body = json.loads(raw)
                rows = body["challengeRankings"]
                rows[0].pop("effectiveStartAt")
                rows[0].pop("effectiveEndAt")
                rows[1].update(effectiveStartAt=True, effectiveEndAt=-1)
                rows[2].update(effectiveStartAt=1791460799000, effectiveEndAt=1790758800000)
                raw = encoded(body)
            return raw, headers
        self.source.transport = transport
        answer = self.ask()
        for board in answer.boards:
            self.assertIsNone(board.song.effective_start_ms)
            self.assertIsNone(board.song.effective_end_ms)
        self.assertEqual(answer.text.count("本曲榜单有效开始：未知"), 3)
        self.assertEqual(answer.text.count("本曲榜单有效结束：未知"), 3)
        conflicted = self.ask("/查榜线 tw")
        self.assertIsNone(conflicted.event.start_ms)
        self.assertIsNone(conflicted.event.end_ms)
        for song in conflicted.event.songs:
            self.assertIsNone(song.effective_start_ms)
            self.assertIsNone(song.effective_end_ms)

    def test_lifecycle_time_conflicts_and_event_switch(self):  # A10/A11/A12
        first = self.ask()
        self.assertEqual(first.event.start_ms, 1790758800000)
        tw = self.ask("/查榜线 tw")
        self.assertIsNone(tw.event.start_ms)
        self.assertIn("待核实", tw.text)
        self.http.current_id = "2"
        self.advance(301)
        second = self.ask()
        self.assertEqual(second.event.event_id, "2")
        self.assertEqual(first.event.event_id, "1")
        self.assertTrue(any("/events/2/challenges/" in url for url in self.http.calls))
        for state, expected in (("feature", "未开始"), ("aggregation", "结算中"), ("result", "已结束（结果公示）"), ("end", "已结束")):
            self.assertEqual(event_status(replace(first.event, status=state)), expected)
        self.http.headers["x-final-quality"] = "lastSeen"
        self.advance(61)
        self.assertIn("非确认终榜", self.ask().boards[0].status)

    def test_snapshot_freezes_identity_even_if_current_changes_mid_query(self):
        event = self.source.event("jp", self.source.deadline())
        self.http.current_id = "2"
        self.source.boards(event, event.songs, self.source.deadline())
        self.assertTrue(all("/events/1/" in u for u in self.http.calls if u.endswith("/ranking")))

    def test_freshness_local_clock_skew_fallback_expiry_and_partial_failures(self):  # A14/A15/A16
        self.http.headers = {"x-fetched-at": str(int(self.http.now * 1000) + 80000), "x-server-time": str(int(self.http.now * 1000) + 120000)}
        first = self.ask()
        self.assertEqual(first.boards[0].age_seconds, 40)
        self.assertIn("偏差", first.boards[0].notes[0])
        self.advance(70)
        self.http.errors["/challenges/2/"] = SourceError("rate_limited", 240)
        second = self.ask()
        self.assertEqual(second.boards[1].fetched_ms, first.boards[1].fetched_ms)
        self.assertEqual(second.boards[1].age_seconds, 110)
        self.assertIn("旧快照", second.boards[1].status)
        self.assertEqual(len(second.boards), 3)
        before = len(self.http.calls)
        self.advance(61)
        self.ask()
        self.assertFalse(any("/challenges/2/" in u for u in self.http.calls[before:]))
        self.advance(600)
        self.assertEqual(self.ask().boards[1].scores, ())

    def test_corrupt_cache_and_failures_do_not_affect_main_catalog(self):  # A16/A21
        self.source.cache_dir.mkdir()
        self.source._path("current:jp").write_text("not json", encoding="utf-8")
        self.assertEqual(len(self.ask().boards), 3)
        self.advance(301)
        self.http.errors["/events/current"] = SourceError("network")
        self.assertIn("短期旧快照", "".join(self.ask().event.notes))
        self.advance(601)
        self.assertEqual(self.ask().status, "data_unavailable")
        self.assertFalse(self.repo.cache_file.exists())

    def test_json_pending_html_and_http_backoff_contract(self):
        for body, code in ((b"<html>error</html>", "invalid_json"), (b'{"error":"pending"}', "pending"), (b"[]", "invalid_json")):
            self.source.transport = lambda *args, body=body: (body, {})
            with self.assertRaises(SourceError) as err:
                self.source._json("https://api.bdon.moe/test", self.source.deadline())
            self.assertEqual(err.exception.code, code)
        self.assertEqual(_retry_after("120", 0), 120)
        self.assertEqual(_retry_after("Thu, 01 Jan 1970 00:02:00 GMT", 0), 120)
        with self.assertRaises(SourceError):
            public_get("https://untrusted.invalid/", 1)

    def test_same_key_requests_coalesce_and_cache_is_lazy(self):  # A17
        self.assertFalse(self.source.cache_dir.exists())
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.ask(), range(6)))
        self.assertEqual(sum(u.endswith("/events/current") for u in self.http.calls), 1)
        self.assertEqual(sum(u.endswith("/ranking") for u in self.http.calls), 3)
        self.assertEqual(len(results), 6)
        self.assertFalse(tuple(self.source.cache_dir.glob("*.tmp")))
        # Reload of sanitized persistent data needs no transport within TTL.
        self.repo.event_cutoffs = EventCutoffRepository(self.source.cache_dir, transport=Mock(side_effect=AssertionError("network")), clock=lambda: self.http.now, monotonic=lambda: self.tick)
        self.assertEqual(len(self.ask().boards), 3)

    def test_real_lifecycle_states_control_collection_without_point_gate(self):
        event = self.source.event("jp", self.source.deadline())
        before = len(self.http.calls)
        for state, expected in (("disabled", "未开放"), ("pending", "等待"), ("missed", "未采集"), ("unknown", "未知")):
            board = self.source.board(event, replace(event.songs[0], collect_status=state), self.source.deadline())
            self.assertIn(expected, board.status)
            self.assertFalse(board.scores)
        self.assertEqual(len(self.http.calls), before)
        self.http.headers["x-final-quality"] = "lastSeen"
        board = self.source.board(replace(event, status="end"), event.songs[0], self.source.deadline())
        self.assertIn("非确认终榜", board.status)

    def test_unknown_position_time_zero_and_expired_source_are_not_current_scores(self):
        self.http.headers = {"x-position-source": "unknown"}
        self.assertFalse(self.ask().boards[0].scores)
        self.advance(61)
        self.http.headers = {"x-fetched-at": ""}
        unknown = self.ask().boards[0]
        self.assertIsNone(unknown.fetched_ms)
        self.assertIn("未知", unknown.status)
        self.advance(61)
        self.http.headers = {"x-fetched-at": str(int(self.http.now * 1000) - 601000)}
        expired = self.ask().boards[0]
        self.assertFalse(expired.scores)
        self.assertIn("过期", expired.status)

    def test_metadata_absence_keeps_overview_and_does_not_claim_song_missing(self):
        self.http.errors["/index.json"] = SourceError("network")
        answer = self.ask()
        self.assertEqual(len(answer.boards), 3)
        self.assertIn("元数据暂不可用", answer.text)
        self.assertEqual(self.ask("/查榜线 夢我夢中").status, "data_unavailable")
        self.assertEqual(len(self.ask("/查榜线 歌曲ID=101").boards), 1)
        self.assertEqual(self.ask('/查榜线 歌曲2').boards[0].song.music_id, '102')

    def test_bad_typed_cache_is_refetched_and_no_event_negatively_cached(self):
        self.source.cache_dir.mkdir()
        self.source._path("current:jp").write_text(json.dumps({"schema": 1, "key": "current:jp", "received": self.http.now,
                "payload": {"eventId": "1", "challengeRankings": "bad"}, "headers": {}}), encoding="utf-8")
        self.assertEqual(len(self.ask().boards), 3)
        self.http.errors["/tw/events/current"] = SourceError("not_found")
        self.assertEqual(self.ask("/查榜线 tw").status, "data_unavailable")
        count = len(self.http.calls)
        self.ask("/查榜线 tw")
        self.assertEqual(len(self.http.calls), count)

    def test_partial_failure_and_total_deadline_keep_all_song_blocks(self):
        self.http.errors["/challenges/2/"] = SourceError("pending")
        answer = self.ask()
        self.assertEqual(len(answer.boards), 3)
        self.assertTrue(answer.boards[0].scores)
        self.assertFalse(answer.boards[1].scores)
        self.assertIn("采集中", answer.boards[1].status)
        self.assertTrue(answer.boards[2].scores)
        self.advance(100)
        before = len(self.http.calls)
        boards = self.source.boards(answer.event, answer.event.songs, self.tick - 1)
        self.assertEqual(len(boards), 3)
        self.assertEqual(len(self.http.calls), before)

    def test_artwork_is_bounded_scoped_and_has_safe_placeholder(self):
        event = self.ask().event
        image = Image.new("RGB", (20, 20), "blue")
        raw = io.BytesIO(); image.save(raw, format="WEBP")
        transport = Mock(return_value=(raw.getvalue(), {"content-type": "image/webp"}))
        self.source.transport = transport
        pictures = load_artwork(self.source, event, (event.banner, event.songs[0].jacket, "https://elsewhere.invalid/image.webp"))
        self.assertIsNotNone(pictures[event.banner])
        self.assertIsNone(pictures["https://elsewhere.invalid/image.webp"])
        self.assertEqual(transport.call_count, 2)
        transport.side_effect = SourceError("network")
        self.assertIsNone(load_artwork(self.source, replace(event, asset_version="new"), (event.banner,))[event.banner])

    def test_artwork_retry_after_applies_to_same_url_across_versions(self):  # A16/A17
        event = self.ask().event
        transport = Mock(side_effect=SourceError("rate_limited", 120))
        self.source.transport = transport
        with ThreadPoolExecutor(max_workers=3) as pool:
            pictures = list(pool.map(lambda _: load_artwork(self.source, event, (event.banner,)), range(3)))
        self.assertTrue(all(p[event.banner] is None for p in pictures))
        self.assertEqual(transport.call_count, 1)
        self.advance(119)
        revised = replace(event, asset_version="new")
        self.assertIsNone(load_artwork(self.source, revised, (event.banner,))[event.banner])
        self.assertEqual(transport.call_count, 1)
        raw = io.BytesIO()
        Image.new("RGB", (20, 20), "blue").save(raw, format="WEBP")
        transport.side_effect = None
        transport.return_value = (raw.getvalue(), {"content-type": "image/webp"})
        self.advance(2)
        self.assertIsNotNone(load_artwork(self.source, revised, (event.banner,))[event.banner])
        self.assertEqual(transport.call_count, 2)
        for path in (self.source.cache_dir / "assets").glob("*.webp"):
            os.utime(path, (self.http.now, self.http.now))
        self.assertIsNotNone(load_artwork(self.source, revised, (event.banner,))[event.banner])
        self.assertEqual(transport.call_count, 2)

    def test_untrusted_model_cannot_route_to_local_only_cutoff_capability(self):
        from ournotes_bot.natural_query.query_validation import validate_route, OutcomeCode
        result = validate_route({"action": "route", "capability": "event.cutoff", "score": 123})
        self.assertIsNone(result.capability_id)
        self.assertNotEqual(result.code, OutcomeCode.SUCCESS)

    def test_page_upload_failure_uses_its_captured_text_and_sequence(self):
        from ournotes_bot.platforms.qq.qq import PreparedReply, _deliver_reply
        send = AsyncMock()
        message = SimpleNamespace(id="fake", _api=SimpleNamespace(post_group_message=send))
        with patch("ournotes_bot.platforms.qq.qq._upload_image", new=AsyncMock(side_effect=OSError("upload"))):
            asyncio.run(_deliver_reply(message, "fake", True, PreparedReply("complete captured page", b"image"), msg_seq=3))
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.call_args.kwargs["msg_seq"], 3)
        self.assertEqual(send.call_args.kwargs["content"], "complete captured page")

    def test_local_natural_no_model_quota_and_old_terminal_cache(self):  # A19/A20
        settings = Settings("", "", "https://offline.invalid", self.repo.cache_file, 6,
                            "", "", "https://offline.invalid", 0)
        parser = AIQueryParser(settings)
        from ournotes_bot.natural_query.query_agent import CachedOutcome
        from ournotes_bot.natural_query.query_validation import OutcomeCode
        parser._agent._sync_cache(self.repo)
        parser._agent._cache_put("现在榜线多少", CachedOutcome("terminal", OutcomeCode.UNSUPPORTED,
                                message="old unsupported cache", expires_at=float("inf")))
        with patch.object(parser, "_request", side_effect=AssertionError("model called")) as model:
            for question in ("现在榜线多少", "这期全部歌曲榜线", "夢我夢中100线多少", "国服夢我夢中前50现在多少分", "hk 夢我夢中前50现在多少分", "英服夢我夢中100线多少", "夢我夢中的档线是多少", "查榜线 jp 夢我夢中 T37", "查榜线 100", "查榜线 t100"):
                text = parser.answer("/问 " + question, self.repo)
                self.assertIn("源采集", text, question)
            for question in ("预测夢我夢中100线多少", "推荐配队打榜线", "账号榜线"):
                self.assertIn("不支持", parser.answer("/问 " + question, self.repo))
            self.http.current_id = "2"
            self.advance(301)
            self.assertIn("活动 2", parser.answer("/问 现在榜线多少", self.repo))
            model.assert_not_called()

    def test_capture_and_render_failure_never_refetches(self):  # A18
        from ournotes_bot.platforms.qq.qq import _prepare_reply
        for command in ("/查榜线", "/查榜线 t100"):
            with patch("ournotes_bot.rendering.event_cutoff_visuals.render_cutoff", side_effect=ValueError("render failure")):
                reply = _prepare_reply(command, self.repo, None)
            self.assertEqual(reply.text.count("T100："), 3)
            self.assertIsNone(reply.image)
        self.assertEqual(sum(u.endswith("/ranking") for u in self.http.calls), 3)

    def test_three_visuals_full_numbers_names_and_multi_page_coverage(self):  # A12/A13/A22
        overview = self.ask()
        for answer in (overview, self.ask("/查榜线 夢我夢中"), self.ask("/查榜线 夢我夢中 37"), self.ask("/查榜线 t100")):
            pages = render_cutoff(answer, preview_label="合成离线测试")
            self.assertEqual(len(pages), 1)
            self.assertEqual(pages[0].text, answer.text)
            image = Image.open(io.BytesIO(pages[0].image))
            self.assertLessEqual(len(pages[0].image), MAX_IMAGE_BYTES)
            self.assertLessEqual(max(image.size), MAX_IMAGE_EDGE)
            self.assertLessEqual(image.width * image.height, MAX_IMAGE_PIXELS)
        big = replace(overview, boards=tuple(replace(overview.boards[0], song=replace(overview.boards[0].song,
                      challenge_id=str(i+1), music_id=str(201+i), title=f"测试歌曲 {i} 中文／日本語・长名称 37 " * 3), scores=(12345678901234567890,) * 100) for i in range(8)))
        big = replace(big,event=replace(big.event,songs=tuple(b.song for b in big.boards)))
        pages = render_cutoff(big, preview_label="合成压力测试")
        self.assertGreater(len(pages), 1)
        texts = "\n".join(page.text for page in pages)
        for i in range(8):
            self.assertIn(f"测试歌曲 {i} ", texts)
        self.assertIn("12345678901234567890", texts)

    def test_reply_budget_order_and_unknown_send_not_retried(self):  # A17/A18/A22
        from ournotes_bot.platforms.qq.qq import PreparedReply, _expand_replies, _deliver_reply
        pages = tuple(PreparedReply(f"song{i}", b"image") for i in range(4))
        batch = [PreparedReply("all songs", pages=pages), PreparedReply("next")]
        expanded = _expand_replies(batch)
        self.assertEqual([p.text for p in expanded], ["song0", "song1", "song2", "song3", "next"])
        expanded = _expand_replies(batch + [PreparedReply("last")])
        self.assertEqual([p.text for p in expanded][1:], ["next", "last"])
        self.assertTrue(expanded[0].text.startswith("all songs\n"))
        self.assertIn("容量不足", expanded[0].text)
        send = AsyncMock(side_effect=TimeoutError("unknown"))
        message = SimpleNamespace(id="fake", _api=SimpleNamespace(post_group_message=send))
        with patch("ournotes_bot.platforms.qq.qq._upload_image", new=AsyncMock(return_value={"file_info": "fake"})):
            asyncio.run(_deliver_reply(message, "fake", True, pages[0]))
        self.assertEqual(send.await_count, 1)


if __name__ == "__main__":
    unittest.main()
