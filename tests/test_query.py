from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser, UNSUPPORTED
from ournotes_bot.commands import handle_command, parse_query, song_matches
from ournotes_bot.config import Settings
from ournotes_bot.data import DataError, SongRepository
from ournotes_bot.yatta import BASE, build_data, fetch_json
from ournotes_bot.qq import _image_reply


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


class QueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SongRepository(BASE, Path(self.temp.name) / "cache.json")
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, META)
        self.repo.metadata = {"source": BASE, "schema": 2, "song_count": 1, "card_count": 1}

    def tearDown(self):
        self.temp.cleanup()

    def test_song_chart_card_queries(self):
        self.assertIn("迷星叫", handle_command("/查曲 100001", self.repo))
        self.assertIn("迷星叫", handle_command("/查曲 迷星叫", self.repo))
        self.assertIn("768 Notes", handle_command("/查谱面 1 EXPERT", self.repo))
        self.assertIn("高松灯", handle_command("/查卡 1", self.repo))
        self.assertIn("高松灯", handle_command("/查卡 我们现在就在这里", self.repo))
        self.assertEqual(parse_query("/查谱面 1 EXPERT"), ("chart", "100001", "EXPERT"))

    def test_chart_image_loads_notes_for_direct_and_natural_queries(self):
        score = {"notes": [{"t": 0, "pos": 6, "size": 6}]}
        with patch("ournotes_bot.qq.load_chart_score", return_value=score) as load, \
             patch("ournotes_bot.qq.render_chart", return_value=b"full chart") as render:
            self.assertEqual(_image_reply("/查谱面 1 EXPERT", self.repo), b"full chart")
            load.assert_called_with(self.repo.songs[0], self.repo.songs[0].charts[-1])
            self.assertIs(render.call_args.args[3], score)
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 4)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", return_value={"intent": "chart", "query": "迷星叫", "difficulty": "EXPERT"}), \
             patch("ournotes_bot.qq.load_chart_score", return_value=score) as load, \
             patch("ournotes_bot.qq.render_chart", return_value=b"full chart"):
            parser.answer("/问 迷星叫EX物量", self.repo)
            self.assertEqual(_image_reply("/问 迷星叫EX物量", self.repo, parser), b"full chart")
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
        detail = {"statsMax": [10156, 7442, 7179], "skills": [{"type": "liveSkill", "name": ["スコアUP", "Score Up", "分数UP", "得分提升"]}]}
        with patch("ournotes_bot.yatta.card_detail", return_value=detail) as fetch:
            card = self.repo.card_with_detail(self.repo.cards[0])
            self.assertEqual(self.repo.card_with_detail(self.repo.cards[0]), card)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(card.performance + card.technic + card.visual, 24777)
        self.assertEqual(card.skill_name, "得分提升")

    def test_model_is_query_parser_only_and_budgeted(self):
        settings = Settings("", "", BASE, self.repo.cache_file, 6, "test-key", "test-model", "https://ai.example", 1)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", return_value={"intent": "chart", "query": "迷星叫", "difficulty": "EXPERT"}) as request:
            self.assertIn("768 Notes", parser.answer("/问 迷星叫的ex物量", self.repo))
            self.assertIn("768 Notes", parser.answer("/问 迷星叫的ex物量", self.repo))
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
        with patch("ournotes_bot.qq.render_song_list", return_value=b"song image"):
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
        with patch.object(parser, "_request", return_value={"intent": "card", "query": "1", "difficulty": ""}):
            self.assertIn("高松灯", parser.answer("/问 1号卡面", self.repo))
        with patch.object(self.repo, "card_with_detail", return_value=self.repo.cards[0]), \
             patch("ournotes_bot.qq.render_card", return_value=b"image"):
            self.assertEqual(_image_reply("/问 1号卡面", self.repo, parser), b"image")


if __name__ == "__main__":
    unittest.main()
