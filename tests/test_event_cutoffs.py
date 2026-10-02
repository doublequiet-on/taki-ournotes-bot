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
                               "rankingEnabled": True, "collectStatus": "collecting", "positionSource": "responseOrder"} for i in range(1, 4)]}), {}
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

    def ask(self, command="/榜线"):
        return resolve_command(command, self.repo).cutoff

    def test_overview_single_rank_and_integer_output(self):  # A01/A04/A13
        answer = self.ask()
        self.assertEqual(answer.request.server, "jp")
        self.assertEqual(answer.ranks, (1, 2, 3, 10, 100))
        self.assertEqual(len(answer.boards), 3)
        one = self.ask("/榜线 夢我夢中")
        self.assertEqual(len(one.boards), 1)
        self.assertEqual(one.ranks, answer.ranks)
        for value in (1, 37, 100):
            precise = self.ask(f"/榜线 夢我夢中 T{value}")
            self.assertEqual(precise.ranks, (value,))
            self.assertIn(str(self.http.players[value - 1]["score"]), precise.text)
        self.assertEqual(sum(url.endswith("/ranking") for url in self.http.calls), 3)

    def test_servers_aliases_and_no_fallback(self):  # A02/A03
        for alias, expected in (("TW", "tw"), ("台服", "tw"), ("KR", "kr"), ("韩服", "kr"), ("en", "en"), ("英服", "en"), ("国际服", "en"), ("JP", "jp"), ("日服", "jp")):
            answer = self.ask("/榜线 " + alias)
            self.assertEqual(answer.event.server, expected)
            self.assertTrue(answer.event.title.startswith(expected))
            self.assertIn(f"/{expected}/", answer.event.banner)
        self.assertEqual(self.ask().event.server, "jp")
        self.advance(301)
        self.http.errors["/tw/events/current"] = SourceError("not_found")
        before = len(self.http.calls)
        self.assertIn("台服", self.ask("/榜线 tw").text)
        self.assertFalse(any("/jp/" in u for u in self.http.calls[before:]))

    def test_invalid_conditions_and_names(self):  # A05/A06
        for rank in ("0", "-1", "1.5", "101", "T0", "T101"):
            answer = self.ask("/榜线 夢我夢中 " + rank)
            self.assertEqual(answer.status, "invalid_arguments", rank)
        for server in ("cn", "国服", "server=us"):
            self.assertEqual(self.ask("/榜线 " + server + " 夢我夢中").status, "invalid_arguments")
        self.assertEqual(self.ask("/榜线 不存在的歌").status, "unknown_entity")
        self.assertEqual(self.ask("/榜线 非本期歌曲").status, "empty")
        self.assertEqual(self.ask("/榜线 37").status, "invalid_arguments")
        self.assertIsNone(self.ask("/榜线 123").request.rank)
        self.assertEqual(self.ask("/榜线 长歌名 37").boards[0].song.music_id, "102")
        self.assertIsNone(self.ask("/榜线 长歌名 37").request.rank)
        self.assertEqual(self.ask("/榜线 长歌名 37 T37").request.rank, 37)
        with patch("ournotes_bot.query.entity_lexicon._aliases", return_value={"song": {"别名": "夢我夢中", "歧义": [101, 102]}}):
            self.assertEqual(self.ask("/榜线 别名").boards[0].song.music_id, "101")
            self.assertEqual(self.ask("/榜线 歧义").status, "ambiguous")

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

    def test_lifecycle_time_conflicts_and_event_switch(self):  # A10/A11/A12
        first = self.ask()
        self.assertEqual(first.event.start_ms, 1790758800000)
        tw = self.ask("/榜线 tw")
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
        self.assertEqual(self.ask("/榜线 夢我夢中").status, "data_unavailable")
        self.assertEqual(len(self.ask("/榜线 101").boards), 1)

    def test_bad_typed_cache_is_refetched_and_no_event_negatively_cached(self):
        self.source.cache_dir.mkdir()
        self.source._path("current:jp").write_text(json.dumps({"schema": 1, "key": "current:jp", "received": self.http.now,
                "payload": {"eventId": "1", "challengeRankings": "bad"}, "headers": {}}), encoding="utf-8")
        self.assertEqual(len(self.ask().boards), 3)
        self.http.errors["/tw/events/current"] = SourceError("not_found")
        self.assertEqual(self.ask("/榜线 tw").status, "data_unavailable")
        count = len(self.http.calls)
        self.ask("/榜线 tw")
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
            for question in ("现在榜线多少", "这期全部歌曲榜线", "夢我夢中100线多少", "台服夢我夢中前50现在多少分", "夢我夢中的档线是多少"):
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
        with patch("ournotes_bot.rendering.event_cutoff_visuals.render_cutoff", side_effect=ValueError("render failure")):
            reply = _prepare_reply("/榜线", self.repo, None)
        self.assertIn("T100", reply.text)
        self.assertIsNone(reply.image)
        self.assertEqual(sum(u.endswith("/ranking") for u in self.http.calls), 3)

    def test_three_visuals_full_numbers_names_and_multi_page_coverage(self):  # A12/A13/A22
        overview = self.ask()
        for answer in (overview, self.ask("/榜线 夢我夢中"), self.ask("/榜线 夢我夢中 37")):
            pages = render_cutoff(answer, preview_label="合成离线测试")
            self.assertEqual(len(pages), 1)
            image = Image.open(io.BytesIO(pages[0].image))
            self.assertLessEqual(len(pages[0].image), MAX_IMAGE_BYTES)
            self.assertLessEqual(max(image.size), MAX_IMAGE_EDGE)
            self.assertLessEqual(image.width * image.height, MAX_IMAGE_PIXELS)
        big = replace(overview, boards=tuple(replace(overview.boards[0], song=replace(overview.boards[0].song,
                      title=f"测试歌曲 {i} 中文／日本語・长名称 37 " * 3), scores=(12345678901234567890,) * 100) for i in range(8)))
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
        self.assertEqual([p.text for p in expanded], ["all songs", "next", "last"])
        send = AsyncMock(side_effect=TimeoutError("unknown"))
        message = SimpleNamespace(id="fake", _api=SimpleNamespace(post_group_message=send))
        with patch("ournotes_bot.platforms.qq.qq._upload_image", new=AsyncMock(return_value={"file_info": "fake"})):
            asyncio.run(_deliver_reply(message, "fake", True, pages[0]))
        self.assertEqual(send.await_count, 1)


if __name__ == "__main__":
    unittest.main()
