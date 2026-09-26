"""Compatibility baseline for the /问 facade before the agent refactor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser, UNSUPPORTED
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Song, SongRepository, SupportCard
from ournotes_bot.entity_lexicon import EntityRef
from ournotes_bot.structured_query import QueryResult, QuerySpec


class QueryFacadeBaselineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cache = Path(self.temp.name) / "cache.json"
        self.repo = SongRepository("https://bdon.yatta.moe", cache)
        self.repo.songs = [Song(
            id=100001, title="迷星叫", titles=("迷星叫", "Mayoiuta"), band="MyGO!!!!!",
            composer="", lyricist="", arranger="", start_at="", jacket_url="",
            charts=(Chart("EXPERT", 25, 25.0, 768, ""),), localized={},
        )]
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=4, card_type=0, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="", localized={},
        )]
        self.repo.support_cards = [SupportCard(
            id=1, title="并肩前行", character="高松灯", characters=("高松灯",),
            rarity=3, card_type=0, performance=0, technic=0, visual=0, start_at="",
            full_url="", thumbnail_url="", localized={},
        )]
        self.repo.metadata = {"cached_at": "baseline", "schema": 4}
        self.settings = Settings("", "", self.repo.data_base, cache, 6, "test-key",
                                 "test-model", "https://ai.example", 20)

    def test_answer_with_plan_and_cache_accessors_keep_contract(self) -> None:
        parser = AIQueryParser(self.settings)
        answer, result = parser.answer_with_plan("/问 MyGO的EXPERT 25级以下歌曲", self.repo)
        self.assertIsInstance(answer, str)
        self.assertIsInstance(result, QueryResult)
        self.assertEqual(result.spec, QuerySpec(
            "song", EntityRef("band", "MyGO!!!!!"), "EXPERT", "<=", 25.0,
            display_name="MyGO",
        ))
        self.assertEqual(parser.spec_for("/问 MyGO的EXPERT 25级以下歌曲"), result.spec)
        self.assertEqual(
            parser.command_for("/问 MyGO的EXPERT 25级以下歌曲"),
            "查曲 MyGO lv<=25 diff=EXPERT",
        )
        self.assertEqual(parser.command_for("普通消息"), None)
        self.assertEqual(parser.spec_for("普通消息"), None)

    def test_common_local_paths_never_call_or_charge_ai(self) -> None:
        parser = AIQueryParser(self.settings)
        samples = (
            "/问 MyGO的EXPERT 25级以下歌曲",
            "/问 MyGO的歌有哪些",
            "/问 tmr的SSR成员卡",
            "/问 tmr的SR支援卡有哪些",
            "/问 迷星叫和另一首歌哪个好",
            "/问 推荐最强阵容",
        )
        with patch.object(parser, "_request", side_effect=AssertionError("unexpected AI call")), \
             patch.object(parser._quota, "reserve", side_effect=AssertionError("unexpected AI charge")):
            replies = [parser.answer(sample, self.repo) for sample in samples]
        self.assertIn("迷星叫", replies[0])
        self.assertIn("迷星叫", replies[1])
        self.assertIn("我们现在就在这里", replies[2])
        self.assertIn("并肩前行", replies[3])
        self.assertEqual(replies[4], UNSUPPORTED)
        self.assertEqual(replies[5], UNSUPPORTED)

    def test_model_path_is_structured_and_request_owned(self) -> None:
        parser = AIQueryParser(self.settings)
        plan = {"intent": "chart", "query": "迷星叫", "difficulty": "EXPERT"}
        with patch.object(parser, "_request", return_value=plan) as request:
            answer, result = parser.answer_with_plan("/问 迷星叫EX物量", self.repo)
        self.assertEqual(request.call_count, 1)
        self.assertIsInstance(result, QueryResult)
        self.assertEqual(result.spec.intent, "chart")
        self.assertIn("768 Notes", answer)


if __name__ == "__main__":
    unittest.main()
