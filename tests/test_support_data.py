from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser, UNKNOWN_ENTITY
from ournotes_bot.commands import handle_command, parse_query
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, SongRepository
from ournotes_bot.structured_query import QuerySpec, answer_for, query_page_notice
from ournotes_bot.yatta import BASE, build_skills, build_support_cards


CHARACTERS = {
    "1": {"name": ["高松 燈", "Tomori Takamatsu", "高松燈", "高松灯"]},
    "2": {"name": ["千早 愛音", "Anon Chihaya", "千早愛音", "千早爱音"]},
}
SUPPORTS = {"items": {
    "7": {
        "id": 7,
        "name": ["サポート", "Support", "支援", "支援"],
        "subtitle": ["並んで歩こう", "Side by Side", "並肩前行", "并肩前行"],
        "characters": [1, 2],
        "rarity": 3,
        "attribute": 5,
        "startAt": 1767225600,
    },
}}


class SupportDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SongRepository(BASE, Path(self.temp.name) / "cache.json")
        self.repo.support_cards = build_support_cards(CHARACTERS, SUPPORTS)
        self.repo.metadata = {"source": BASE, "schema": 4, "cached_at": "2026-09-26T00:00:00+00:00"}

    def tearDown(self):
        self.temp.cleanup()

    def test_support_card_search_parse_and_cache_round_trip(self):
        card = self.repo.support_cards[0]
        self.assertEqual(card.characters, ("高松灯", "千早爱音"))
        self.assertEqual(card.title, "并肩前行")
        self.assertEqual(self.repo.search_support_cards("tmr")[0].id, 7)
        self.assertEqual(parse_query("/查支援卡 tmr 页2"), ("support_cards", "tmr", 2))
        self.assertIn("并肩前行", handle_command("/查支援卡 tmr", self.repo))

        self.repo._save_cache()
        loaded = SongRepository(BASE, self.repo.cache_file)
        loaded._load_cache()
        self.assertEqual(loaded.support_cards[0], card)

    def test_skill_effects_render_at_level_five(self):
        skills = build_skills([
            {
                "type": "supportSkill",
                "name": ["支援", "Support", "支援", "支援提升"],
                "description": ["{value}%", "{value}%", "{value}%", "提升<color=#fff>{value}</color>%"],
                "effects": {"3": [{
                    "param": "value", "type": "property", "data": {"5": 2500},
                    "formula": {"divide": 100, "format": "F1"},
                }]},
            },
            {
                "type": "gekisouSupportSkill",
                "name": ["対象", "Targets", "目標", "目标"],
                "description": ["{bands}", "{bands}", "{bands}", "{bands}成员"],
                "effects": {"3": [{
                    "param": "bands", "type": "targets",
                    "data": {"5": {"bandType": ["MyGO!!!!!", "MyGO!!!!!", "MyGO!!!!!", "MyGO!!!!!"]}},
                }]},
            },
        ])
        self.assertEqual(skills[0].description, "提升25.0%")
        self.assertEqual(skills[1].description, "MyGO!!!!!成员")

    def test_member_and_support_id_queries_include_details(self):
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=5, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="", localized={},
        )]
        member_skills = [
            {"type": kind, "name": [name, name, name, name], "description": ["效果", "Effect", "效果", "效果"]}
            for kind, name in (("leaderSkill", "队长"), ("liveSkill", "Live"), ("gekisouSkill", "激奏"))
        ]
        support_skills = [
            {"type": "supportSkill", "name": ["支援", "Support", "支援", "支援"]},
            {"type": "gekisouSupportSkill", "name": ["激奏", "Gekisou", "激奏", "激奏支援"]},
        ]
        with patch("ournotes_bot.yatta.card_detail", return_value={
            "statsMax": [10156, 7442, 7179], "skills": member_skills,
        }):
            member = handle_command("/查卡 1", self.repo)
        self.assertIn("[成员卡详情]", member)
        self.assertIn("综合力：24,777", member)
        self.assertIn("队长技能：队长", member)
        self.assertIn("Live 技能：Live", member)
        self.assertIn("激奏技能：激奏", member)

        with patch("ournotes_bot.yatta.support_card_detail", return_value={
            "statsMax": [600, 500, 400], "skills": support_skills,
        }):
            support = handle_command("/查支援卡 7", self.repo)
        self.assertIn("[支援卡详情]", support)
        self.assertIn("支援加成：15%", support)
        self.assertIn("支援技能：支援", support)
        self.assertIn("激奏支援技能：激奏支援", support)

    def test_ask_handles_skill_filters_and_support_cards_locally(self):
        score_skill = build_skills([{
            "type": "liveSkill", "name": ["スコアUP", "Score Up", "分数UP", "得分提升"],
            "description": ["得分提升", "Score rises", "得分提升", "得分提升50%"],
        }])
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=5, performance=10156, technic=7442, visual=7179, start_at="",
            skill_name="得分提升", full_url="", thumbnail_url="", localized={}, skills=score_skill,
        )]
        parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6))
        with patch.object(parser, "_request", side_effect=AssertionError("model should not be called")):
            skill_answer = parser.answer("/问 得分提升技能的成员卡有哪些", self.repo)
            support_answer = parser.answer("/问 tmr的支援卡有哪些", self.repo)
            all_support = parser.answer("/问 支援卡有哪些", self.repo)
        self.assertIn("我们现在就在这里", skill_answer)
        self.assertIn("↳ 得分提升", skill_answer)
        self.assertIn("并肩前行", support_answer)
        self.assertIn("并肩前行", all_support)
        self.assertEqual(parser.spec_for("/问 tmr的支援卡有哪些").intent, "support_card")
        self.assertIn("我们现在就在这里", parser.answer("/问 Live技能的成员卡", self.repo))
        self.assertIn("我们现在就在这里", parser.answer("/问 高松灯的得分提升技能的成员卡", self.repo))
        page_query = "/问 高松灯的得分提升技能的成员卡 页2"
        self.assertIn("页码超出范围", parser.answer(page_query, self.repo))
        page_spec = parser.spec_for(page_query)
        self.assertEqual((page_spec.subject.kind, page_spec.subject.value), ("character", "灯"))
        self.assertEqual((page_spec.skill_query, page_spec.page), ("得分提升", 2))

    def test_ask_pagination_and_incomplete_skill_index_are_safe(self):
        parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6))
        answer = parser.answer("/问 支援卡有哪些 页2", self.repo)
        self.assertIn("页码超出范围", answer)
        self.assertIn("/问 支援卡有哪些", answer)
        self.assertEqual(
            query_page_notice(QuerySpec("support_card", display_name="全部支援卡"), 17, "zh"),
            "第 1/2 页 · 共 17张\n下一页：/问 支援卡有哪些 页2",
        )
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=5, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="", localized={},
        )]
        self.assertIn("技能索引", answer_for(
            QuerySpec("card", skill_query="得分提升"), self.repo,
        ))

    def test_model_skill_and_support_plans_are_grounded(self):
        score_skill = build_skills([{
            "type": "liveSkill", "name": ["スコアUP", "Score Up", "分数UP", "得分提升"],
            "description": ["得分提升", "Score rises", "得分提升", "得分提升50%"],
        }])
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=5, performance=10156, technic=7442, visual=7179, start_at="",
            skill_name="得分提升", full_url="", thumbnail_url="", localized={}, skills=score_skill,
        )]
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 10)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", return_value={
            "intent": "card", "query": "", "difficulty": "", "level_operator": "", "level": None,
            "skill_query": "得分提升", "skill_kind": "live",
        }):
            self.assertIn("我们现在就在这里", parser.answer("/问 哪些成员卡的技能效果包含得分提升并且是Live技能", self.repo))
        with patch.object(parser, "_request", return_value={
            "intent": "card", "query": "", "difficulty": "", "level_operator": "", "level": None,
            "skill_query": "得分提升", "skill_kind": "",
        }):
            self.assertEqual(parser.answer("/问 哪些成员卡有加分效果", self.repo), UNKNOWN_ENTITY)
        with patch.object(parser, "_request", return_value={
            "intent": "support_card", "query": "高松灯", "difficulty": "",
            "level_operator": "", "level": None, "skill_query": "", "skill_kind": "",
        }):
            self.assertIn("并肩前行", parser.answer("/问 帮我找高松灯相关的支援内容", self.repo))

    def test_refresh_builds_and_persists_member_skill_index(self):
        base_card = Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=5, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="", localized={},
        )
        detail = {"statsMax": [10156, 7442, 7179], "skills": [{
            "type": "liveSkill", "name": ["スコアUP", "Score Up", "分数UP", "得分提升"],
            "description": ["得分提升", "Score rises", "得分提升", "得分提升50%"],
        }]}
        with patch("ournotes_bot.yatta.fetch_json", return_value={}), \
             patch("ournotes_bot.yatta.build_data", return_value=([], [base_card])), \
             patch("ournotes_bot.yatta.build_support_cards", return_value=self.repo.support_cards), \
             patch("ournotes_bot.yatta.card_detail", return_value=detail) as fetch:
            self.repo.refresh()
        self.assertEqual(fetch.call_count, 1)
        self.assertTrue(self.repo.member_skill_index_ready())
        self.assertEqual(self.repo.metadata["member_card_detail_count"], 1)
        self.assertTrue(self.repo.metadata["member_skill_index_complete"])

        loaded = SongRepository(BASE, self.repo.cache_file)
        loaded._load_cache()
        self.assertTrue(loaded.member_skill_index_ready())
        self.assertEqual(loaded.cards[0].skills[0].name, "得分提升")


if __name__ == "__main__":
    unittest.main()
