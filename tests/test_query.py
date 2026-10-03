from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser, UNKNOWN_ENTITY, UNSUPPORTED
from ournotes_bot.commands import handle_command, parse_query, resolve_command, song_matches
from ournotes_bot.config import Settings
from ournotes_bot.data import DataError, SongRepository
from ournotes_bot.sources.yatta import BASE, build_data, build_skills, build_support_cards, fetch_json
from ournotes_bot.platforms.qq.qq import _image_from_result, _image_reply


CHARACTERS = {"1": {"id": 1, "name": ["高松 燈", "Tomori Takamatsu", "高松燈", "高松灯"], "band": 1}}
BANDS = {"musicTags": {"1": ["MyGO!!!!!", "MyGO!!!!!", "MyGO!!!!!", "MyGO!!!!!"]}}
CARDS = {"refs": BANDS, "items": {"1": {"id": 1, "name": CHARACTERS["1"]["name"],
                                      "subtitle": ["今、ここにいる", "Right Here", "我們現在就在這裡", "我们现在就在这里"],
                                      "character": 1, "rarity": 2, "attribute": 5, "startAt": 1767225600}}}
SONGS = {"refs": BANDS, "items": {"100001": {"id": 100001, "title": ["迷星叫", "Mayoiuta", "迷星叫", "迷星叫"],
                                              "bands": [1], "difficulties": [9, 13, 20, 25],
                                              "jacket": "jkt_001_100001", "startAt": 1767225600}}}
META = {"100001": [[9, 0, 0, "190", 342], [13, 0, 0, "190", 408],
                   [20, 0, 0, "190", 682], [25, 0, 0, "190", 768]]}
SUPPORTS = {"items": {"1": {"id": 1, "name": ["灯的支援", "Tomori Support", "燈的支援", "灯的支援"],
                                  "subtitle": ["并肩前行", "Side by Side", "並肩前行", "并肩前行"],
                                  "characters": [1], "rarity": 3, "attribute": 5, "startAt": 1767225600}}}


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SongRepository(BASE, Path(self.temp.name) / "cache.json")
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, META)
        self.repo.support_cards = build_support_cards(CHARACTERS, SUPPORTS)
        self.repo.metadata = {"source": BASE, "schema": 4, "song_count": 1, "card_count": 1, "support_card_count": 1}

    def tearDown(self):
        self.temp.cleanup()

    def test_song_chart_card_queries(self):
        self.assertIn("迷星叫", handle_command("/查曲 100001", self.repo))
        self.assertIn("迷星叫", handle_command("/查曲 迷星叫", self.repo))
        self.assertIn("768 Notes", handle_command("/查谱面 1 EXPERT", self.repo))
        with patch("ournotes_bot.sources.yatta.card_detail", return_value={}):
            self.assertIn("高松灯", handle_command("/查卡 1", self.repo))
        self.assertIn("高松灯", handle_command("/查卡 我们现在就在这里", self.repo))
        self.assertIn("并肩前行", handle_command("/查支援卡 高松灯", self.repo))
        self.assertEqual(parse_query("/查谱面 1 EXPERT"), ("chart", "100001", "EXPERT"))
        self.assertEqual(parse_query("/查支援卡 tmr 页2"), ("support_cards", "tmr", 2))

    def test_card_rarity_filters_entities_and_skills_without_ai(self):
        skill = build_skills([{"type": "liveSkill", "name": "得分提升", "description": "5秒内提升50%"}])
        base = replace(self.repo.cards[0], localized={}, skills=skill)
        self.repo.cards = [
            replace(base, id=1, rarity=4), replace(base, id=2, rarity=3),
            replace(base, id=3, rarity=4, character="千早爱音"),
            replace(base, id=4, rarity=4, character="丰川祥子", band="Ave Mujica"),
            replace(base, id=5, rarity=0), replace(base, id=6, rarity=2),
        ]
        parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6, "test-key"))
        with patch.object(parser, "_request", side_effect=AssertionError("unexpected AI call")), \
             patch.object(parser._quota, "reserve", side_effect=AssertionError("unexpected AI charge")):
            for token in ("SSR", "ssr", "四星", "4星", "４星", "★4", "4★", "★★★★", "星级=4", "rarity=4", "4-star"):
                with self.subTest(token=token):
                    direct = resolve_command(f"/查卡 {token}", self.repo)
                    _, natural = parser.answer_with_plan(f"/问 所有{token}卡有哪些", self.repo)
                    self.assertEqual([card.id for card in direct.cards], [1, 3, 4])
                    self.assertEqual(natural.cards, direct.cards)
            for question, expected in (
                ("高松灯的三星卡有哪些", [2]), ("tmr有哪些4星卡", [1]),
                ("只看SSR的MyGO卡", [1, 3]), ("查看mygo的四星卡", [1, 3]),
                ("SSR", [1, 3, 4]), ("SR卡有哪些", [2]), ("R卡有哪些", [6]),
                ("高松灯的SSR卡有哪些", [1]), ("高松灯的四星Live技能的成员卡", [1]),
                ("SSR得分提升技能的成员卡", [1, 3, 4]),
                ("四星5秒技能的成员卡", [1, 3, 4]),
            ):
                with self.subTest(question=question):
                    _, selected = parser.answer_with_plan("/问 " + question, self.repo)
                    self.assertIsNotNone(selected)
                    self.assertEqual([card.id for card in selected.cards], expected)
            for command, expected in (("/查卡 tmr 三星", [2]), ("/查卡 ★4 mygo", [1, 3]),
                                      ("/查卡 SR", [2]), ("/查卡 R", [6]),
                                      ("/查卡 1 SSR", [1]), ("/查卡 5", [5])):
                with self.subTest(command=command):
                    self.assertEqual([card.id for card in resolve_command(command, self.repo).cards], expected)
            direct = resolve_command("/查支援卡 三星", self.repo)
            _, natural = parser.answer_with_plan("/问 tmr的SR支援卡", self.repo)
            self.assertEqual(direct.support_cards, natural.support_cards)
            self.assertEqual(len(natural.support_cards), 1)
            support = self.repo.support_cards[0]
            self.repo.support_cards = [replace(support, id=stars, rarity=stars) for stars in (2, 3, 4, 10)]
            for grade, stars in (("SSR", 4), ("SR", 3), ("R", 2)):
                with self.subTest(support_grade=grade):
                    direct = resolve_command(f"/查支援卡 {grade}", self.repo)
                    _, natural = parser.answer_with_plan(f"/问 tmr的{grade}支援卡有哪些", self.repo)
                    self.assertEqual(direct.support_cards, natural.support_cards)
                    self.assertEqual([card.id for card in natural.support_cards], [stars])

    def test_card_rarity_rejects_invalid_filters_and_keeps_empty_results(self):
        parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6, "test-key"))
        with patch.object(parser, "_request", side_effect=AssertionError("unexpected AI call")):
            for condition in ("零星", "一星", "五星", "6星", "4.5星", "-4星", "三到四星", "四星以上", "至少四星", "★★★★★★"):
                with self.subTest(condition=condition):
                    self.assertIn("请指定", handle_command("/查卡 " + condition, self.repo))
                    answer, selected = parser.answer_with_plan("/问 " + condition + "卡", self.repo)
                    self.assertIn("请指定", answer)
                    self.assertIsNone(selected)
            answer, selected = parser.answer_with_plan("/问 不存在的角色的SSR卡", self.repo)
            self.assertEqual(answer, UNKNOWN_ENTITY)
            self.assertIsNone(selected)
            answer, selected = parser.answer_with_plan("/问 tmr的四星卡", self.repo)
            self.assertEqual(selected.cards, ())
            self.assertIn("4星", answer)
            self.assertEqual(resolve_command("/查卡 tmr SSR", self.repo).cards, ())

    def test_card_rarity_pagination_preserves_filters_and_image_selection(self):
        base = self.repo.cards[0]
        self.repo.cards = [replace(base, id=i, rarity=4, title=f"卡{i}") for i in range(1, 18)]
        self.repo.cards.append(replace(base, id=99, rarity=3))
        support = self.repo.support_cards[0]
        self.repo.support_cards = [replace(support, id=i, rarity=3) for i in range(1, 18)]
        self.repo.support_cards.append(replace(support, id=99, rarity=4))
        parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6))
        for command, renderer in (
            ("/查卡 mygo SSR 页2", "render_card_list"), ("/问 mygo的四星卡第2页", "render_card_list"),
            ("/查支援卡 SR 页2", "render_support_card_list"), ("/问 SR支援卡第2页", "render_support_card_list"),
        ):
            with self.subTest(command=command):
                if command.startswith("/问"):
                    answer, selected = parser.answer_with_plan(command, self.repo)
                else:
                    selected = resolve_command(command, self.repo)
                    answer = handle_command(command, self.repo, resolved=selected)
                self.assertIn("第 2/2 页", answer)
                self.assertIn("共17张", answer.replace(" ", ""))
                with patch("ournotes_bot.platforms.qq.qq." + renderer, return_value=b"image") as render:
                    self.assertEqual(_image_from_result(selected, self.repo, "zh"), b"image")
                self.assertEqual([card.id for card in render.call_args.args[0]], [17])
        first = parser.answer("/问 mygo的SSR卡", self.repo)
        next_command = first.split("下一页：", 1)[1].splitlines()[0]
        self.assertIn("4星", next_command)
        self.assertEqual([card.id for card in resolve_command(next_command, self.repo).cards], list(range(1, 18)))
        first = parser.answer("/问 SR支援卡有哪些", self.repo)
        next_command = first.split("下一页：", 1)[1].splitlines()[0]
        self.assertIn("3星", next_command)
        answer, selected = parser.answer_with_plan(next_command, self.repo)
        self.assertIn("第 2/2 页", answer)
        self.assertEqual([card.id for card in selected.support_cards], list(range(1, 18)))

    def test_chart_image_loads_notes_for_direct_and_natural_queries(self):
        score = {"notes": [{"t": 0, "pos": 6, "size": 6}]}
        with patch("ournotes_bot.platforms.qq.qq.load_chart_score", return_value=score) as load, \
             patch("ournotes_bot.platforms.qq.qq.render_chart", return_value=b"full chart") as render:
            self.assertEqual(_image_reply("/查谱面 1 EXPERT", self.repo), b"full chart")
            load.assert_called_with(self.repo.songs[0], self.repo.songs[0].charts[-1])
            self.assertIs(render.call_args.args[3], score)
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 4)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", return_value={"intent": "chart", "query": "迷星叫", "difficulty": "EXPERT"}), \
             patch("ournotes_bot.platforms.qq.qq.load_chart_score", return_value=score) as load, \
             patch("ournotes_bot.platforms.qq.qq.render_chart", return_value=b"full chart"):
            parser.answer("/问 展示迷星叫 EXPERT 完整谱面资料", self.repo)
            self.assertEqual(_image_reply("/问 展示迷星叫 EXPERT 完整谱面资料", self.repo, parser), b"full chart")
            load.assert_called_with(self.repo.songs[0], self.repo.songs[0].charts[-1])

    def test_only_yatta_assets_and_localized_data(self):
        card = self.repo.cards[0]
        song = self.repo.songs[0]
        self.assertEqual(card.title, "我们现在就在这里")
        self.assertTrue(card.full_url.startswith(BASE + "/Resources/en/Assets/"))
        self.assertTrue(song.jacket_url.startswith(BASE + "/Resources/en/Assets/"))
        self.assertEqual(song.charts[-1].level, 25)

    def test_cache_rejects_old_source(self):
        self.repo._save_cache()
        self.repo._load_cache()
        self.repo.metadata["source"] = "https://old.example"
        self.repo._save_cache()
        with self.assertRaises(DataError):
            self.repo._load_cache()

    def test_details_are_loaded_only_on_demand(self):
        skills = [
            {"type": "leaderSkill", "name": ["Leader", "Leader", "隊長", "队长"],
             "description": ["{value}%", "{value}%", "{value}%", "提升{value}%"],
             "effects": {"3": [{"param": "value", "type": "property", "data": {"5": 1500},
                                    "formula": {"divide": 100, "format": "F1"}}]}},
            {"type": "liveSkill", "name": ["スコアUP", "Score Up", "分数UP", "得分提升"]},
            {"type": "gekisouSkill", "name": ["Gekisou", "Gekisou", "激奏", "激奏"]},
        ]
        detail = {"statsMax": [10156, 7442, 7179], "skills": skills}
        with patch("ournotes_bot.sources.yatta.card_detail", return_value=detail) as fetch:
            card = self.repo.card_with_detail(self.repo.cards[0])
            self.assertEqual(self.repo.card_with_detail(self.repo.cards[0]), card)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(card.performance + card.technic + card.visual, 24777)
        self.assertEqual(card.skill_name, "得分提升")
        self.assertEqual([skill.kind for skill in card.skills], ["leaderSkill", "liveSkill", "gekisouSkill"])
        self.assertEqual(card.skills[0].description, "提升15.0%")

    def test_support_card_details_are_loaded_only_on_demand(self):
        detail = {"statsMax": [600, 500, 400], "skills": [
            {"type": "supportSkill", "name": ["支援", "Support", "支援", "支援提升"],
             "description": ["{value}%", "{value}%", "{value}%", "提升{value}%"],
             "effects": {"3": [{"param": "value", "type": "property", "data": {"5": 2500},
                                    "formula": {"divide": 100, "format": "F1"}}]}},
            {"type": "gekisouSupportSkill", "name": ["激奏", "Gekisou", "激奏", "激奏支援"]},
        ]}
        with patch("ournotes_bot.sources.yatta.support_card_detail", return_value=detail) as fetch:
            card = self.repo.support_card_with_detail(self.repo.support_cards[0])
            self.assertEqual(self.repo.support_card_with_detail(self.repo.support_cards[0]), card)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual((card.performance, card.technic, card.visual), (600, 500, 400))
        self.assertEqual(card.skills[0].description, "提升25.0%")
        with patch.object(self.repo, "support_card_with_detail", return_value=card), \
             patch("ournotes_bot.platforms.qq.qq.render_support_card", return_value=b"support image"):
            self.assertEqual(_image_reply("/查支援卡 1", self.repo), b"support image")

    def test_ask_skill_and_support_queries_use_regular_list_renderers(self):
        skills = build_skills([{
            "type": "liveSkill", "name": ["スコアUP", "Score Up", "分数UP", "得分提升"],
            "description": ["得分提升", "Score rises", "得分提升", "得分提升50%"],
        }])
        self.repo.cards = [replace(self.repo.cards[0], skills=skills)]
        parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6))
        with patch("ournotes_bot.platforms.qq.qq.render_card_list", return_value=b"skill cards") as render:
            self.assertEqual(_image_reply("/问 得分提升技能的成员卡有哪些", self.repo, parser), b"skill cards")
        self.assertEqual([card.id for card in render.call_args.args[0]], [1])
        with patch("ournotes_bot.platforms.qq.qq.render_support_card_list", return_value=b"support cards") as render:
            self.assertEqual(_image_reply("/问 tmr的支援卡有哪些", self.repo, parser), b"support cards")
        self.assertEqual([card.id for card in render.call_args.args[0]], [1])

    def test_model_is_query_parser_only_and_budgeted(self):
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 1)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", return_value={"intent": "chart", "query": "迷星叫", "difficulty": "EXPERT"}) as request:
            self.assertIn("768 Notes", parser.answer("/问 展示迷星叫 EXPERT 完整谱面资料", self.repo))
            self.assertIn("768 Notes", parser.answer("/问 展示迷星叫 EXPERT 完整谱面资料", self.repo))
            self.assertEqual(request.call_count, 1)
            self.assertIn("额度", parser.answer("/问 另一首歌的谱面", self.repo))
        with patch.object(parser, "_request", return_value={"intent": "unsupported", "query": "", "difficulty": ""}):
            self.assertIn("额度", parser.answer("/问 那首歌的谱面", self.repo))
        self.assertEqual(UNSUPPORTED.startswith("目前只能查询"), True)
        with patch.object(parser, "_request", side_effect=AssertionError("should not call model")):
            self.assertEqual(parser.answer("/问 推荐最强阵容", self.repo), UNSUPPORTED)

    def test_natural_song_level_floor_uses_local_data_without_ai(self):
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 1)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", side_effect=AssertionError("should not call model")):
            self.assertIn("迷星叫", parser.answer("/问 mygo25级以上的歌曲", self.repo))
            for question, command, found in (
                ("/问 MyGO 25级及以上的歌", "查曲 MyGO lv>=25", True),
                ("/问 25级以上的MyGO歌曲", "查曲 MyGO lv>=25", True),
                ("/问 MyGO中至少25级的歌曲", "查曲 MyGO lv>=25", True),
                ("/问 MyGO不少于25级的歌曲", "查曲 MyGO lv>=25", True),
                ("/问 MyGO高于25级的歌", "查曲 MyGO lv>25", False),
                ("/问 MyGO不超过25级的歌", "查曲 MyGO lv<=25", True),
                ("/问 MyGO不高于25级的歌", "查曲 MyGO lv<=25", True),
                ("/问 MyGO低于25级的歌", "查曲 MyGO lv<25", False),
                ("/问 MyGO不到25级的歌", "查曲 MyGO lv<25", False),
                ("/问 EX25级以上的MyGO歌曲", "查曲 MyGO lv>=25 diff=EXPERT", True),
                ("/问 MyGO EX 25.5以上的歌", "查曲 MyGO lv>=25.5 diff=EXPERT", False),
                ("/问 25级以上的歌", "查曲 lv>=25", True),
                ("/问 MyGO 25以上第2页", "查曲 MyGO lv>=25 页2", False),
            ):
                response = parser.answer(question, self.repo)
                self.assertEqual(parser.command_for(question), command)
                self.assertEqual(response.startswith("歌曲列表"), found, question)
        self.assertEqual(parser.command_for("/问 mygo25级以上的歌曲"), "查曲 mygo lv>=25")
        self.assertEqual(len(song_matches(self.repo, "mygo lv>=25")), 1)
        self.assertEqual(song_matches(self.repo, "mygo lv>=25.5"), [])
        self.assertEqual(len(song_matches(self.repo, "mygo lv<=25")), 1)
        self.assertEqual(song_matches(self.repo, "mygo lv<25"), [])
        self.assertEqual(song_matches(self.repo, "mygo lv>=25.5 diff=EXPERT"), [])
        with patch("ournotes_bot.platforms.qq.qq.render_song_list", return_value=b"song image"):
            self.assertEqual(_image_reply("/问 mygo25级以上的歌曲", self.repo, parser), b"song image")

    def test_model_can_return_bounded_song_level_filter(self):
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 4)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", return_value={"intent": "song", "query": "MyGO!!!!!", "difficulty": "EXPERT", "level_operator": "lte", "level": 25}):
            self.assertIn("迷星叫", parser.answer("/问 MyGO专家谱面中级数不大于25的歌曲", self.repo))
        self.assertEqual(parser.command_for("/问 MyGO专家谱面中级数不大于25的歌曲"), "查曲 MyGO!!!!! lv<=25 diff=EXPERT")
        with patch.object(parser, "_request", return_value={"intent": "song", "query": "", "difficulty": "", "level_operator": "gte", "level": True}):
            self.assertEqual(parser.answer("/问 奇怪的等级条件", self.repo), UNSUPPORTED)
        with patch.object(parser, "_request", return_value={"intent": "song", "query": "MyGO", "difficulty": "EXPERT", "level_operator": "", "level": None}):
            self.assertEqual(parser.answer("/问 另一个奇怪条件", self.repo), UNSUPPORTED)

    def test_fetch_rejects_other_hosts(self):
        with self.assertRaises(ValueError):
            fetch_json("https://example.com/data.json")

    def test_ai_card_query_uses_regular_image_renderer(self):
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 2)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", return_value={"intent": "card", "query": "1", "difficulty": ""}), \
             patch("ournotes_bot.sources.yatta.card_detail", return_value={}):
            self.assertIn("高松灯", parser.answer("/问 1号卡面", self.repo))
        with patch.object(self.repo, "card_with_detail", return_value=self.repo.cards[0]), \
             patch("ournotes_bot.platforms.qq.qq.render_card", return_value=b"image"):
            self.assertEqual(_image_reply("/问 1号卡面", self.repo, parser), b"image")


if __name__ == "__main__":
    unittest.main()
