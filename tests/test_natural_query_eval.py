"""Deterministic natural-query evaluation with synthetic in-memory data only."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from test_moenotes_meta import make_snapshot

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Skill, Song, SongRepository, SupportCard
from ournotes_bot.natural_query.local_query import parse_local_query
from ournotes_bot.natural_query.query_capabilities import CAPABILITIES, local_route
from ournotes_bot.natural_query.query_validation import (
    OutcomeCode,
    validate_capability_action,
    validate_legacy_plan,
)
from ournotes_bot.structured_query import QuerySpec


class NaturalQueryEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = SongRepository("https://invalid.example", root / "cache.json")
        snapshot = make_snapshot(now=self.repo.music_data.clock())
        self.repo.music_data._snapshot = snapshot
        self.repo.music_data.get = Mock(return_value=snapshot)
        self.repo.songs = [Song(
            id=100001, title="迷星叫", titles=("迷星叫",), band="MyGO!!!!!",
            composer="", lyricist="", arranger="", start_at="", jacket_url="",
            charts=(Chart("EXPERT", 25, 25.0, 768, ""),),
        )]
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯",
            band="MyGO!!!!!", rarity=4, card_type=1, performance=1, technic=1,
            visual=1, start_at="", skill_name="得分提升", full_url="",
            thumbnail_url="", skills=(
                Skill("liveSkill", "得分提升", "得分提升50%"),
            ), catalog={
                "bands": ["MyGO!!!!!"],
                "tags": {"1": {"zh": "MyGO!!!!!"}},
                "attribute": "red",
                "categories": {"live": ["score"], "gekisou": ["just"]},
            },
        ), Card(
            id=2, asset_id=2, title="春日影", character="高松灯",
            band="MyGO!!!!!", rarity=3, card_type=1, performance=1, technic=1,
            visual=1, start_at="", skill_name="", full_url="", thumbnail_url="",
            skills=(Skill("liveSkill", "回复", "回复生命"),),
        )]
        self.repo.support_cards = [SupportCard(
            id=7, title="并肩前行", character="高松灯", characters=("高松灯",),
            rarity=3, card_type=1, performance=1, technic=1, visual=1,
            start_at="", full_url="", thumbnail_url="",
        )]
        self.repo.metadata = {
            "schema": 4,
            "cached_at": "natural-query-eval",
            "member_skill_index_complete": True,
            "card_catalog_version": 1,
        }
        self.settings = Settings(
            "", "", self.repo.data_base, self.repo.cache_file, 6,
            "test-key", "test-model", "https://invalid.example", 20,
            ai_quota_file=root / "quota.json",
            ai_metrics_file=root / "metrics.json",
        )

    def test_l1_routes_representative_capabilities(self) -> None:
        cases = (
            ("MyGO中级数不大于25的歌曲", "song.search"),
            ("展示迷星叫 EXPERT 完整谱面资料", "chart.get"),
            ("高松灯相关的成员卡内容", "member_card.search"),
            ("tmr的支援卡有哪些", "support_card.search"),
            ("MyGO的EX效率前十有哪些", "song.meta"),
        )
        for question, expected in cases:
            with self.subTest(question=question):
                self.assertEqual(local_route(question), expected)

    def test_l2_validates_scoped_actions_and_song_meta_exactly(self) -> None:
        cases = (
            (
                "展示迷星叫 EXPERT 完整谱面资料",
                "chart.get",
                {
                    "action": "call_tool", "capability": "chart.get",
                    "arguments": {"query": "迷星叫", "difficulty": "EXPERT"},
                },
                OutcomeCode.SUCCESS,
            ),
            (
                "MyGO的EX效率前十有哪些",
                "song.meta",
                {
                    "action": "call_tool", "capability": "song.meta",
                    "arguments": {
                        "query": "MyGO!!!!!", "difficulty": "EXPERT",
                        "level_operator": "", "level": None, "page": 1,
                        "metric": "eff", "order": "desc",
                    },
                },
                OutcomeCode.SUCCESS,
            ),
            (
                "MyGO的EX效率前十有哪些",
                "song.meta",
                {
                    "action": "call_tool", "capability": "song.meta",
                    "arguments": {
                        "query": "MyGO!!!!!", "difficulty": "EXPERT",
                        "level_operator": "", "level": None, "page": 1,
                        "metric": "score", "order": "desc",
                    },
                },
                OutcomeCode.INVALID_ARGUMENTS,
            ),
        )
        for question, capability_id, action, expected in cases:
            with self.subTest(question=question, expected=expected):
                result = validate_capability_action(
                    action, CAPABILITIES[capability_id], question, self.repo,
                )
                self.assertEqual(result.code, expected)
                self.assertEqual(result.spec is not None, expected == OutcomeCode.SUCCESS)

    def test_l3_local_queries_return_fixed_records_without_ai_or_quota(self) -> None:
        cases = (
            ("MyGO的歌有哪些", "song", (100001,)),
            ("得分提升技能的成员卡有哪些", "card", (1,)),
            ("tmr的支援卡有哪些", "support_card", (7,)),
            ("MyGO的红色SSR角色卡有哪些", "card", (1,)),
        )
        parser = AIQueryParser(self.settings)
        with patch.object(
            parser, "_request", side_effect=AssertionError("model must stay offline"),
        ) as request, patch.object(
            parser._quota, "reserve", side_effect=AssertionError("quota must not be reserved"),
        ) as reserve:
            for question, intent, expected_ids in cases:
                with self.subTest(question=question):
                    outcome = parser.answer_with_outcome("/问 " + question, self.repo)
                    self.assertEqual(outcome.code, OutcomeCode.SUCCESS)
                    self.assertIsNotNone(outcome.result)
                    spec = outcome.result.spec
                    self.assertEqual(spec.intent, intent)
                    rows = {
                        "song": outcome.result.songs,
                        "card": outcome.result.cards,
                        "support_card": outcome.result.support_cards,
                    }[intent]
                    self.assertEqual(tuple(row.id for row in rows), expected_ids)
            request.assert_not_called()
            reserve.assert_not_called()

    def test_l3_fixed_model_action_executes_one_chart_query(self) -> None:
        parser = AIQueryParser(self.settings)
        action = {
            "action": "call_tool", "capability": "chart.get",
            "arguments": {"query": "迷星叫", "difficulty": "EXPERT"},
        }
        with patch.object(parser, "_request", return_value=action) as request:
            outcome = parser.answer_with_outcome("/问 展示迷星叫 EXPERT 完整谱面资料", self.repo)
        self.assertEqual(outcome.code, OutcomeCode.SUCCESS)
        self.assertEqual(request.call_count, 1)
        self.assertEqual(outcome.state.model_calls, 1)
        self.assertEqual(outcome.state.tool_executions, 1)
        self.assertEqual(outcome.result.chart[0].id, 100001)

    def test_new_queryspec_fields_and_local_parser_priority_are_preserved(self) -> None:
        efficiency = parse_local_query("MyGO的EX效率前十有哪些", self.repo)
        self.assertIsInstance(efficiency, QuerySpec)
        self.assertEqual(
            (efficiency.intent, efficiency.metric, efficiency.order, efficiency.limit),
            ("efficiency", "eff", "desc", 10),
        )
        catalog = parse_local_query("MyGO的红色SSR角色卡有哪些", self.repo)
        self.assertIsInstance(catalog, QuerySpec)
        self.assertEqual(catalog.intent, "card")
        self.assertIsNotNone(catalog.card_query)

    def test_pr2_rarity_enumerations_are_local_and_return_fixed_records(self) -> None:
        cases = (
            ("有哪些4星卡", "card", (1,)),
            ("哪些三星成员卡", "card", (2,)),
            ("有什么4星卡", "card", (1,)),
            ("有啥SR卡", "card", (2,)),
            ("有什么样的4星卡", "card", (1,)),
            ("有哪些SR支援卡", "support_card", (7,)),
        )
        parser = AIQueryParser(self.settings)
        with patch.object(
            parser, "_request", side_effect=AssertionError("model must stay offline"),
        ) as request, patch.object(
            parser._quota, "reserve", side_effect=AssertionError("quota must not be reserved"),
        ) as reserve:
            for question, intent, expected_ids in cases:
                with self.subTest(question=question):
                    outcome = parser.answer_with_outcome("/问 " + question, self.repo)
                    self.assertEqual(outcome.code, OutcomeCode.SUCCESS)
                    self.assertEqual(outcome.result.spec.intent, intent)
                    rows = (outcome.result.cards if intent == "card"
                            else outcome.result.support_cards)
                    self.assertEqual(tuple(row.id for row in rows), expected_ids)
            request.assert_not_called()
            reserve.assert_not_called()

    def test_pr2_skill_placeholders_get_local_clarification_without_ai(self) -> None:
        expected = "请补充技能名称、效果关键词，或指定队长／Live／激奏技能。"
        parser = AIQueryParser(self.settings)
        with patch.object(
            parser, "_request", side_effect=AssertionError("model must stay offline"),
        ) as request, patch.object(
            parser._quota, "reserve", side_effect=AssertionError("quota must not be reserved"),
        ) as reserve:
            for question in (
                "哪些技能卡", "有哪些技能成员卡", "什么技能卡", "有哪些4星技能卡",
            ):
                with self.subTest(question=question):
                    outcome = parser.answer_with_outcome("/问 " + question, self.repo)
                    self.assertEqual(outcome.text, expected)
                    self.assertEqual(outcome.code, OutcomeCode.INVALID_ARGUMENTS)
                    self.assertIsNone(outcome.result)
                    self.assertEqual(outcome.state.model_calls, 0)
            request.assert_not_called()
            reserve.assert_not_called()

    def test_pr2_model_skill_placeholder_is_repairable_invalid_argument(self) -> None:
        action = {
            "action": "call_tool", "capability": "member_card.search",
            "arguments": {
                "query": "", "skill_query": "哪些", "skill_kind": "",
                "rarity": None, "page": 1,
            },
        }
        result = validate_capability_action(
            action, CAPABILITIES["member_card.search"], "哪些技能卡", self.repo,
            allow_empty_subject=True,
        )
        self.assertEqual(result.code, OutcomeCode.INVALID_ARGUMENTS)
        self.assertEqual(result.invalid_fields, ("skill_query",))
        self.assertTrue(result.repairable)
        legacy = validate_legacy_plan(
            {"intent": "card", "query": "", "skill_query": "哪些"},
            "哪些技能卡", self.repo, allow_empty_subject=True,
        )
        self.assertEqual(legacy.code, OutcomeCode.INVALID_ARGUMENTS)

    def test_pr3_song_search_actions_preserve_explicit_user_conditions(self) -> None:
        cases = (
            (
                "MyGO专家谱面中级数不大于25的歌曲",
                {"query": "MyGO!!!!!", "difficulty": "EXPERT",
                 "level_operator": "lte", "level": 25, "page": 1},
                OutcomeCode.SUCCESS, (),
            ),
            (
                "MyGO专家谱面中级数不大于25的歌曲",
                {"query": "MyGO!!!!!", "difficulty": "EXPERT",
                 "level_operator": "gt", "level": 25, "page": 1},
                OutcomeCode.INVALID_ARGUMENTS, ("level_operator",),
            ),
            (
                "MyGO专家谱面中级数不大于25的歌曲",
                {"query": "MyGO!!!!!", "difficulty": "EXPERT",
                 "level_operator": "lte", "level": 20, "page": 1},
                OutcomeCode.INVALID_ARGUMENTS, ("level",),
            ),
            (
                "迷星叫25级以上的歌",
                {"query": "迷星叫", "difficulty": "",
                 "level_operator": "gte", "level": 25, "page": 1},
                OutcomeCode.SUCCESS, (),
            ),
            (
                "迷星叫25级以上的歌",
                {"query": "迷星叫", "difficulty": "",
                 "level_operator": "gte", "level": 20, "page": 1},
                OutcomeCode.INVALID_ARGUMENTS, ("level",),
            ),
            (
                "迷星叫25级以上的歌",
                {"query": "迷星叫", "difficulty": "EXPERT",
                 "level_operator": "gte", "level": 25, "page": 1},
                OutcomeCode.INVALID_ARGUMENTS, ("difficulty",),
            ),
            (
                "MyGO expert songs with level at most 25",
                {"query": "MyGO!!!!!", "difficulty": "EXPERT",
                 "level_operator": "lte", "level": 25, "page": 1},
                OutcomeCode.SUCCESS, (),
            ),
        )
        capability = CAPABILITIES["song.search"]
        for question, arguments, expected_code, invalid_fields in cases:
            action = {
                "action": "call_tool", "capability": "song.search",
                "arguments": arguments,
            }
            with self.subTest(question=question, arguments=arguments):
                result = validate_capability_action(
                    action, capability, question, self.repo,
                    allow_empty_subject=True,
                )
                self.assertEqual(result.code, expected_code)
                self.assertEqual(result.invalid_fields, invalid_fields)
                self.assertEqual(
                    result.repairable,
                    expected_code == OutcomeCode.INVALID_ARGUMENTS,
                )

    def test_pr3_tampered_song_condition_repairs_once_then_returns_expected_song(self) -> None:
        parser = AIQueryParser(self.settings)
        question = "/问 MyGO专家谱面中级数不大于25的歌曲"
        tampered = {
            "action": "call_tool", "capability": "song.search",
            "arguments": {
                "query": "MyGO!!!!!", "difficulty": "EXPERT",
                "level_operator": "gt", "level": 25, "page": 1,
            },
        }
        corrected = {
            "action": "call_tool", "capability": "song.search",
            "arguments": {
                "query": "MyGO!!!!!", "difficulty": "EXPERT",
                "level_operator": "lte", "level": 25, "page": 1,
            },
        }
        with patch.object(parser, "_request", side_effect=[tampered, corrected]) as request:
            outcome = parser.answer_with_outcome(question, self.repo)
        self.assertEqual(outcome.code, OutcomeCode.SUCCESS)
        self.assertEqual(tuple(song.id for song in outcome.result.songs), (100001,))
        self.assertEqual(request.call_count, 2)
        self.assertEqual(outcome.state.model_calls, 2)
        self.assertEqual(outcome.state.repairs, 1)
        self.assertEqual(outcome.state.tool_executions, 1)
        self.assertEqual(outcome.state.final_code, OutcomeCode.SUCCESS)


if __name__ == "__main__":
    unittest.main()
