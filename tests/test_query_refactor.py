"""Focused offline tests for the staged natural-query refactor."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser, UNKNOWN_ENTITY
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Song, SongRepository, SupportCard
from ournotes_bot.query_capabilities import CAPABILITIES, local_route, route_prompt
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
        with patch.object(parser, "_request", side_effect=[
            {"action": "route", "capability": "song.search"},
            {
                "action": "call_tool", "capability": "song.search",
                "arguments": {"query": "不存在", "difficulty": "",
                              "level_operator": "", "level": None},
            },
        ]):
            self.assertEqual(parser.answer(f"/问 {secret}", self.repo), UNKNOWN_ENTITY)
        parser.answer("/问 MyGO的歌有哪些", self.repo)
        payload = self.metrics_path.read_text(encoding="utf-8")
        self.assertNotIn(secret, payload)
        record = json.loads(payload)
        bucket = next(iter(record["days"].values()))
        self.assertEqual(bucket["ask_total"], 2)
        self.assertEqual(bucket["ai_route_requested"], 1)
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

    def test_capability_catalog_generates_router_and_scoped_prompts(self) -> None:
        router = route_prompt()
        for capability_id in CAPABILITIES:
            self.assertIn(capability_id, router)
        self.assertNotIn("skill_query", router)
        self.assertNotIn("level_operator", router)

        song_prompt = CAPABILITIES["song.search"].prompt()
        self.assertIn("song.search", song_prompt)
        self.assertIn("level_operator", song_prompt)
        self.assertNotIn("skill_query", song_prompt)
        self.assertNotIn("rarity", song_prompt)
        self.assertNotIn("support_card.search", song_prompt)

        member_prompt = CAPABILITIES["member_card.search"].prompt()
        self.assertIn("skill_query", member_prompt)
        self.assertIn("rarity", member_prompt)
        self.assertNotIn("level_operator", member_prompt)
        self.assertNotIn("difficulty", member_prompt)

    def test_explicit_capability_uses_one_scoped_model_call(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 MyGO专家谱面中级数不大于25的歌曲"
        self.assertEqual(local_route(question), "song.search")
        action = {
            "action": "call_tool", "capability": "song.search",
            "arguments": {
                "query": "MyGO", "difficulty": "EXPERT",
                "level_operator": "lte", "level": 25,
            },
        }
        with patch.object(parser, "_request", return_value=action) as request:
            self.assertIn("迷星叫", parser.answer(question, self.repo))
        self.assertEqual(request.call_count, 1)
        prompt = request.call_args.args[1]
        self.assertIn("song.search", prompt)
        self.assertNotIn("skill_query", prompt)
        self.assertNotIn("support_card.search", prompt)

    def test_unknown_capability_routes_then_parses(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 迷星叫具体有多少个音符"
        self.assertIsNone(local_route(question))
        responses = [
            {"action": "route", "capability": "chart.get"},
            {
                "action": "call_tool", "capability": "chart.get",
                "arguments": {"query": "迷星叫", "difficulty": ""},
            },
        ]
        with patch.object(parser, "_request", side_effect=responses) as request:
            self.assertIn("768 Notes", parser.answer(question, self.repo))
        self.assertEqual(request.call_count, 2)
        self.assertIn("只判断能力", request.call_args_list[0].args[1])
        self.assertIn("chart.get", request.call_args_list[1].args[1])
        self.assertNotIn("skill_query", request.call_args_list[1].args[1])
        quota = json.loads(self.settings.ai_quota_file.read_text(encoding="utf-8"))
        self.assertEqual(quota["used"], 2)

    def test_invalid_structured_arguments_are_repaired_once(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 迷星叫EX物量"
        responses = [
            {
                "action": "call_tool", "capability": "chart.get",
                "arguments": {"query": "迷星叫", "difficulty": "MASTER"},
            },
            {
                "action": "call_tool", "capability": "chart.get",
                "arguments": {"query": "迷星叫", "difficulty": "EXPERT"},
            },
        ]
        with patch.object(parser, "_request", side_effect=responses) as request:
            self.assertIn("768 Notes", parser.answer(question, self.repo))
        state = parser.state_for(question)
        self.assertEqual(request.call_count, 2)
        self.assertEqual((state.model_calls, state.repairs, state.tool_executions), (2, 1, 1))
        repair_prompt = request.call_args_list[1].args[1]
        self.assertIn('"code":"INVALID_ARGUMENTS"', repair_prompt)
        self.assertIn('"invalid_fields":["difficulty"]', repair_prompt)
        self.assertNotIn("Traceback", repair_prompt)

    def test_router_parse_and_repair_obey_three_call_hard_limit(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 迷星叫具体有多少个音符"
        responses = [
            {"action": "route", "capability": "chart.get"},
            {
                "action": "call_tool", "capability": "chart.get",
                "arguments": {"query": "迷星叫", "difficulty": "MASTER"},
            },
            {
                "action": "call_tool", "capability": "chart.get",
                "arguments": {"query": "迷星叫", "difficulty": "EXPERT"},
            },
            AssertionError("fourth model call"),
        ]
        with patch.object(parser, "_request", side_effect=responses) as request:
            self.assertIn("768 Notes", parser.answer(question, self.repo))
        state = parser.state_for(question)
        self.assertEqual(request.call_count, 3)
        self.assertEqual((state.model_calls, state.repairs, state.tool_executions), (3, 1, 1))

    def test_total_time_budget_prevents_a_late_repair(self) -> None:
        parser = AIQueryParser(self.settings)
        now = [0.0]
        parser._agent.clock = lambda: now[0]
        invalid = {
            "action": "call_tool", "capability": "chart.get",
            "arguments": {"query": "迷星叫", "difficulty": "MASTER"},
        }

        def late_response(*_args, **_kwargs):
            now[0] = 19.0
            return invalid

        question = "/问 迷星叫EX物量"
        with patch.object(parser, "_request", side_effect=late_response) as request:
            self.assertIn("目前只能查询", parser.answer(question, self.repo))
        state = parser.state_for(question)
        self.assertEqual(request.call_count, 1)
        self.assertEqual((state.model_calls, state.repairs, state.tool_executions), (1, 0, 0))

    def test_second_invalid_output_stops_without_another_repair(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 迷星叫EX物量"
        invalid = {
            "action": "call_tool", "capability": "chart.get",
            "arguments": {"query": "迷星叫", "difficulty": "MASTER"},
        }
        with patch.object(parser, "_request", side_effect=[invalid, invalid]) as request:
            self.assertIn("目前只能查询", parser.answer(question, self.repo))
        state = parser.state_for(question)
        self.assertEqual(request.call_count, 2)
        self.assertEqual((state.model_calls, state.repairs, state.tool_executions), (2, 1, 0))
        self.assertEqual(state.final_code, OutcomeCode.INVALID_ARGUMENTS)

    def test_empty_result_is_terminal_and_cached_without_relaxing_filters(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 MyGO专家谱面中级数至少要到26的歌曲"
        action = {
            "action": "call_tool", "capability": "song.search",
            "arguments": {
                "query": "MyGO", "difficulty": "EXPERT",
                "level_operator": "gte", "level": 26,
            },
        }
        with patch.object(parser, "_request", return_value=action) as request:
            first = parser.answer(question, self.repo)
            second = parser.answer(question, self.repo)
        self.assertIn("没有找到", first)
        self.assertEqual(first, second)
        self.assertEqual(request.call_count, 1)
        state = parser.state_for(question)
        self.assertEqual(state.final_code, OutcomeCode.EMPTY)
        self.assertEqual(state.repairs, 0)
        self.assertEqual(state.model_calls, 0)  # The most recent run was a cache hit.
        self.assertEqual(state.tool_executions, 1)

    def test_deterministic_failure_is_cached_but_provider_failure_is_not(self) -> None:
        parser = AIQueryParser(self.settings)
        rejected = "/问 帮我分析高松灯相关的成员卡内容"
        with patch.object(parser, "_request", return_value={
            "action": "reject", "reason": "unsupported",
        }) as request:
            self.assertIn("目前只能查询", parser.answer(rejected, self.repo))
            self.assertIn("目前只能查询", parser.answer(rejected, self.repo))
        self.assertEqual(request.call_count, 1)
        self.assertIsNone(parser.command_for(rejected))

        transient_parser = AIQueryParser(replace(
            self.settings,
            ai_quota_file=Path(self.temp.name) / "transient-quota.json",
            ai_metrics_file=Path(self.temp.name) / "transient-metrics.json",
        ))
        with patch.object(transient_parser, "_request", side_effect=TimeoutError("secret")) as request:
            self.assertIn("暂不可用", transient_parser.answer("/问 迷星叫EX物量", self.repo))
            self.assertIn("暂不可用", transient_parser.answer("/问 迷星叫EX物量", self.repo))
        self.assertEqual(request.call_count, 2)

    def test_source_change_invalidates_terminal_cache(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 帮我分析高松灯相关的成员卡内容"
        with patch.object(parser, "_request", return_value={
            "action": "reject", "reason": "unsupported",
        }) as request:
            parser.answer(question, self.repo)
            parser.answer(question, self.repo)
            self.repo.metadata["cached_at"] = "refactor-2"
            parser.answer(question, self.repo)
        self.assertEqual(request.call_count, 2)

    def test_free_text_extra_fields_and_unknown_tool_are_terminal(self) -> None:
        cases = (
            "这里是自然语言答案",
            {
                "action": "call_tool", "capability": "chart.get",
                "arguments": {"query": "迷星叫", "difficulty": "EXPERT"},
                "extra": True,
            },
            {
                "action": "call_tool", "capability": "unknown.tool",
                "arguments": {"query": "迷星叫"},
            },
        )
        for index, response in enumerate(cases):
            with self.subTest(index=index):
                parser = AIQueryParser(replace(
                    self.settings,
                    ai_quota_file=Path(self.temp.name) / f"strict-quota-{index}.json",
                    ai_metrics_file=Path(self.temp.name) / f"strict-metrics-{index}.json",
                ))
                question = f"/问 迷星叫EX物量{'呀' * index}"
                with patch.object(parser, "_request", return_value=response) as request:
                    self.assertIn("目前只能查询", parser.answer(question, self.repo))
                state = parser.state_for(question)
                self.assertEqual(request.call_count, 1)
                self.assertEqual(state.model_calls, 1)
                self.assertEqual(state.repairs, 0)
                self.assertEqual(state.tool_executions, 0)
                self.assertEqual(state.final_code, OutcomeCode.INVALID_OUTPUT)

    def test_second_tool_execution_only_follows_explicit_repair(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 迷星叫EX物量"
        original = CAPABILITIES["chart.get"]
        attempts = 0

        def flaky_executor(spec, repository):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ValueError("private detail")
            return original.executor(spec, repository)

        action = {
            "action": "call_tool", "capability": "chart.get",
            "arguments": {"query": "迷星叫", "difficulty": "EXPERT"},
        }
        with patch.dict(CAPABILITIES, {"chart.get": replace(original, executor=flaky_executor)}), \
             patch.object(parser, "_request", side_effect=[action, action]) as request:
            self.assertIn("768 Notes", parser.answer(question, self.repo))
        state = parser.state_for(question)
        self.assertEqual(request.call_count, 2)
        self.assertEqual((state.model_calls, state.repairs, state.tool_executions), (2, 1, 2))
        self.assertEqual(attempts, 2)

    def test_concurrent_cache_and_metrics_updates_remain_valid(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 MyGO的歌有哪些"
        with patch.object(parser, "_request", side_effect=AssertionError("model called")), \
             ThreadPoolExecutor(max_workers=8) as executor:
            replies = list(executor.map(
                lambda _: parser.answer(question, self.repo), range(32),
            ))
        self.assertTrue(all("迷星叫" in reply for reply in replies))
        self.assertLessEqual(len(parser._agent._cache), 128)
        record = json.loads(self.metrics_path.read_text(encoding="utf-8"))
        bucket = next(iter(record["days"].values()))
        self.assertEqual(bucket["ask_total"], 32)
        self.assertEqual(
            bucket.get("local_success", 0) + bucket.get("cache_hit_success", 0), 32,
        )

    def test_metrics_path_can_be_configured_independently(self) -> None:
        metrics = Path(self.temp.name) / "custom" / "metrics.json"
        with patch("ournotes_bot.config.load_dotenv"), patch.dict(os.environ, {
            "OURNOTES_CACHE_FILE": str(Path(self.temp.name) / "cache-2.json"),
            "OURNOTES_AI_METRICS_FILE": str(metrics),
        }, clear=True):
            settings = Settings.from_env()
        self.assertEqual(settings.ai_metrics_file, metrics)
        self.assertNotEqual(settings.ai_metrics_file, settings.ai_quota_file)


if __name__ == "__main__":
    unittest.main()
