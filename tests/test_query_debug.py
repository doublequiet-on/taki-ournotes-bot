"""Tests for the process-local /调试数据 counters."""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import handle_command
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Song, SongRepository, SupportCard
from ournotes_bot.query_debug import QUERY_DEBUG_COUNTERS, QueryDebugCounters


class QueryDebugTests(unittest.TestCase):
    def setUp(self) -> None:
        QUERY_DEBUG_COUNTERS.reset()
        self.addCleanup(QUERY_DEBUG_COUNTERS.reset)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        folder = Path(self.temp.name)
        self.repo = SongRepository("https://bdon.yatta.moe", folder / "cache.json")
        self.repo.songs = [Song(
            id=100001, title="迷星叫", titles=("迷星叫",), band="MyGO!!!!!",
            composer="", lyricist="", arranger="", start_at="", jacket_url="",
            charts=(Chart("EXPERT", 25, 25.0, 768, ""),), localized={},
        )]
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯",
            band="MyGO!!!!!", rarity=4, card_type=0, performance=0, technic=0,
            visual=0, start_at="", skill_name="", full_url="", thumbnail_url="",
            localized={},
        )]
        self.repo.support_cards = [SupportCard(
            id=1, title="并肩前行", character="高松灯", characters=("高松灯",),
            rarity=3, card_type=0, performance=0, technic=0, visual=0,
            start_at="", full_url="", thumbnail_url="", localized={},
        )]
        self.repo.metadata = {"cached_at": "debug", "schema": 4}
        self.settings = Settings(
            "", "", self.repo.data_base, self.repo.cache_file, 6,
            "test-key", "test-model", "https://ai.example", 20,
            ai_quota_file=folder / "quota.json",
            ai_metrics_file=folder / "metrics.json",
        )

    def test_router_and_parser_calls_count_once_as_a_useful_query(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 迷星叫具体有多少个音符"
        responses = [
            {"action": "route", "capability": "chart.get"},
            {
                "action": "call_tool", "capability": "chart.get",
                "arguments": {"query": "迷星叫", "difficulty": "EXPERT"},
            },
        ]
        with patch.object(parser, "_request", side_effect=responses) as request:
            self.assertIn("768 Notes", parser.answer(question, self.repo))
            self.assertIn("768 Notes", parser.answer(question, self.repo))
        self.assertEqual(request.call_count, 2)
        snapshot = QUERY_DEBUG_COUNTERS.snapshot()
        self.assertEqual(snapshot.successful_api_calls, 2)
        self.assertEqual(snapshot.useful_ai_queries, 1)
        report = handle_command("/调试数据", self.repo)
        self.assertIn("成功 API 调用：2 次", report)
        self.assertIn("产生实际检索内容的 AI 查询：1 次", report)

    def test_empty_rejected_and_failed_requests_are_not_useful(self) -> None:
        parser = AIQueryParser(self.settings)
        rejected = "/问 帮我分析高松灯相关的成员卡内容"
        with patch.object(parser, "_request", return_value={
            "action": "reject", "reason": "unsupported",
        }):
            self.assertIn("目前只能查询", parser.answer(rejected, self.repo))

        empty = "/问 MyGO专家谱面中级数至少要到26的歌曲"
        with patch.object(parser, "_request", return_value={
            "action": "call_tool", "capability": "song.search",
            "arguments": {
                "query": "MyGO", "difficulty": "EXPERT",
                "level_operator": "gte", "level": 26,
            },
        }):
            self.assertIn("没有找到", parser.answer(empty, self.repo))

        failed = "/问 迷星叫EX物量"
        with patch.object(parser, "_request", side_effect=TimeoutError("offline")):
            self.assertIn("暂不可用", parser.answer(failed, self.repo))

        snapshot = QUERY_DEBUG_COUNTERS.snapshot()
        self.assertEqual(snapshot.successful_api_calls, 2)
        self.assertEqual(snapshot.useful_ai_queries, 0)

    def test_counter_updates_are_thread_safe(self) -> None:
        counters = QueryDebugCounters()

        def update(_: int) -> None:
            counters.record_api_success()
            counters.record_useful_query()

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(update, range(200)))
        snapshot = counters.snapshot()
        self.assertEqual(snapshot.successful_api_calls, 200)
        self.assertEqual(snapshot.useful_ai_queries, 200)


if __name__ == "__main__":
    unittest.main()
