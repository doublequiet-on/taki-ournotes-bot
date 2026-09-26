"""Focused offline tests for the staged natural-query refactor."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser, UNKNOWN_ENTITY
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Song, SongRepository, SupportCard
from ournotes_bot.query_validation import OutcomeCode


class QueryRefactorTests(unittest.TestCase):
    def setUp(self) -> None:
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
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=4, card_type=0, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="", localized={},
        )]
        self.repo.support_cards = [SupportCard(
            id=1, title="并肩前行", character="高松灯", characters=("高松灯",),
            rarity=3, card_type=0, performance=0, technic=0, visual=0, start_at="",
            full_url="", thumbnail_url="", localized={},
        )]
        self.repo.metadata = {"cached_at": "refactor", "schema": 4}
        self.metrics_path = folder / "metrics.json"
        self.settings = Settings(
            "", "", self.repo.data_base, self.repo.cache_file, 6,
            "test-key", "test-model", "https://ai.example", 20,
            ai_quota_file=folder / "quota.json", ai_metrics_file=self.metrics_path,
        )

    def test_slow_model_request_does_not_block_local_query(self) -> None:
        parser = AIQueryParser(self.settings)
        started = threading.Event()
        release = threading.Event()

        def slow_request(*_args, **_kwargs):
            started.set()
            release.wait(timeout=5)
            return {"intent": "chart", "query": "迷星叫", "difficulty": "EXPERT"}

        with patch.object(parser, "_request", side_effect=slow_request), \
             ThreadPoolExecutor(max_workers=2) as executor:
            slow = executor.submit(parser.answer, "/问 迷星叫EX物量", self.repo)
            self.assertTrue(started.wait(timeout=1))
            local = executor.submit(parser.answer, "/问 MyGO的歌有哪些", self.repo)
            try:
                self.assertIn("迷星叫", local.result(timeout=1))
                self.assertFalse(slow.done())
            finally:
                release.set()
            self.assertIn("768 Notes", slow.result(timeout=1))

    def test_metrics_are_classified_without_query_text(self) -> None:
        parser = AIQueryParser(self.settings)
        secret = "私人内容-13800000000"
        with patch.object(parser, "_request", return_value={
            "intent": "song", "query": "不存在", "difficulty": "",
        }):
            self.assertEqual(parser.answer(f"/问 {secret}", self.repo), UNKNOWN_ENTITY)
        parser.answer("/问 MyGO的歌有哪些", self.repo)
        payload = self.metrics_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, payload)
        record = json.loads(payload)
        bucket = next(iter(record["days"].values()))
        self.assertEqual(bucket["ask_total"], 2)
        self.assertEqual(bucket["ai_parse_requested"], 1)
        self.assertEqual(bucket["ai_unknown_entity"], 1)
        self.assertEqual(bucket["local_success"], 1)
        self.assertEqual(parser.outcome_code_for(f"/问 {secret}"), OutcomeCode.UNKNOWN_ENTITY)

    def test_corrupt_metrics_do_not_break_or_repeat_model_calls(self) -> None:
        self.metrics_path.write_text("{broken", encoding="utf-8")
        parser = AIQueryParser(self.settings)
        with patch.object(parser, "_request", return_value={
            "intent": "chart", "query": "迷星叫", "difficulty": "EXPERT",
        }) as request:
            self.assertIn("768 Notes", parser.answer("/问 迷星叫EX物量", self.repo))
        self.assertEqual(request.call_count, 1)
        self.assertFalse(parser._metrics.available)


if __name__ == "__main__":
    unittest.main()
