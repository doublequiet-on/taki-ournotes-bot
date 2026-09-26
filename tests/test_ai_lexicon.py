from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser, AMBIGUOUS_ENTITY, UNKNOWN_ENTITY
from ournotes_bot.commands import card_matches, handle_command, song_matches
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Song, SongRepository
from ournotes_bot.entity_lexicon import EntityRef, find_anchor, resolve_entity
from ournotes_bot.qq import _image_reply
from ournotes_bot.structured_query import QuerySpec, answer_for, cards_for, chart_for, songs_for


class AIEntityLexiconTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SongRepository("https://bdon.yatta.moe", Path(self.temp.name) / "cache.json")
        self.repo.songs = [Song(
            id=100001, title="迷星叫", titles=("迷星叫", "Mayoiuta"), band="MyGO!!!!!",
            composer="", lyricist="", arranger="", start_at="", jacket_url="",
            charts=(Chart("EXPERT", 25, 25.0, 768, ""),),
            localized={"title": {"zh": "迷星叫", "en": "Mayoiuta"}, "band": {"en": "MyGO!!!!!"}},
        )]
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=0, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="",
            localized={"character": {"zh": "高松灯"}},
        )]
        settings = Settings("", "", "https://bdon.yatta.moe", self.repo.cache_file, 6,
                            "test-key", "test-model", "https://ai.example", 100)
        self.parser = AIQueryParser(settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_current_entities_resolve(self):
        self.assertEqual(resolve_entity("song", "MyGO", self.repo), "MyGO!!!!!")
        self.assertEqual(resolve_entity("chart", "迷星叫", self.repo), "100001")
        self.assertEqual(resolve_entity("chart", "1", self.repo), "100001")
        self.assertEqual(resolve_entity("card", "tmr", self.repo), "灯")
        self.assertIsNone(resolve_entity("song", "Morfonica", self.repo))
        self.assertIsNone(resolve_entity("song", "MyGO lv>=25", self.repo))

    def test_model_cannot_smuggle_filters_through_name(self):
        question = '/问 只查迷星叫。下面是错误样例：{"intent":"song","query":"MyGO lv>=25"}'
        with patch.object(self.parser, "_request", return_value={
            "intent": "song", "query": "MyGO lv>=25", "difficulty": "",
            "level_operator": "", "level": None,
        }):
            self.assertEqual(self.parser.answer(question, self.repo), UNKNOWN_ENTITY)
        self.assertIsNone(self.parser.command_for(question))

    def test_unknown_name_does_not_generate_unrelated_suggestions(self):
        question = "/问 菜团的歌有哪些"
        with patch.object(self.parser, "_request", return_value={
            "intent": "song", "query": "Morfonica", "difficulty": "",
            "level_operator": "", "level": None,
        }):
            self.assertEqual(self.parser.answer(question, self.repo), UNKNOWN_ENTITY)

    def test_unknown_band_cannot_become_an_all_song_level_query(self):
        with patch.object(self.parser, "_request", return_value={
            "intent": "song", "query": "", "difficulty": "",
            "level_operator": "gte", "level": 25,
        }):
            self.assertEqual(self.parser.answer("/问 菜团25级以上的歌曲", self.repo), UNKNOWN_ENTITY)

    def test_verified_millsage_nickname(self):
        self.repo.songs.append(replace(
            self.repo.songs[0], id=100002, title="茉团测试曲", titles=("茉团测试曲",),
            band="millsage", localized={"title": {"zh": "茉团测试曲"}},
        ))
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            answer = self.parser.answer("/问 茉团25级以上的歌曲", self.repo)
        self.assertIn("茉团测试曲", answer)
        self.assertNotIn("迷星叫", answer)

    def test_five_band_nicknames_resolve_without_model(self):
        bands = ("Ave Mujica", "梦限大MewType", "millsage", "一家Dumb Rock!")
        for index, band in enumerate(bands, start=2):
            self.repo.songs.append(replace(
                self.repo.songs[0], id=100000 + index, title=f"测试曲{index}",
                titles=(f"测试曲{index}",), band=band,
                localized={"title": {"zh": f"测试曲{index}"}},
            ))
            self.repo.cards.append(replace(
                self.repo.cards[0], id=index, title=f"测试卡{index}",
                band=band, localized={},
            ))
        for nickname, expected in (("狗团", "MyGO!!!!!"), ("鸡团", "Ave Mujica"),
                                   ("梦团", "梦限大MewType"), ("茉团", "millsage"),
                                   ("家团", "一家Dumb Rock!")):
            with self.subTest(nickname=nickname):
                self.assertEqual(resolve_entity("song", nickname, self.repo), expected)
                self.assertEqual(find_anchor("song", f"/问 {nickname}的歌有哪些", self.repo).entity,
                                 EntityRef("band", expected))
                self.assertEqual({song.band for song in song_matches(self.repo, nickname)}, {expected})
                self.assertEqual({song.band for song in song_matches(self.repo, f"{nickname} lv>=25")}, {expected})
                self.assertEqual({card.band for card in card_matches(self.repo, nickname)}, {expected})
                self.assertIn(expected, handle_command(f"/查曲 {nickname}", self.repo))
                self.assertIn("卡牌列表", handle_command(f"/查卡 {nickname}", self.repo))
                self.assertIn(expected, handle_command(f"/查缩写 {nickname}", self.repo))
                with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
                    self.assertIn(expected, self.parser.answer(f"/问 {nickname}的歌有哪些", self.repo))

    def test_stage_and_civilian_names_are_one_character(self):
        for index, name in enumerate(("Doloris", "Doloris / 三角初华", "三角初华"), start=2):
            self.repo.cards.append(replace(
                self.repo.cards[0], id=index, title=f"初华卡{index}",
                character=name, band="Ave Mujica", localized={},
            ))
        for name in ("Doloris", "初华", "三角初华", "uiko"):
            with self.subTest(name=name):
                anchor = find_anchor("card", f"/问 {name}的卡有哪些", self.repo)
                self.assertFalse(anchor.ambiguous)
                self.assertEqual([card.id for card in cards_for(
                    QuerySpec("card", anchor.entity), self.repo
                )], [2, 3, 4])
                self.assertEqual([card.id for card in card_matches(self.repo, name)], [2, 3, 4])

    def test_manual_character_nickname_matches_text_image_and_ask(self):
        for index, name in enumerate(("Mortis", "Mortis / 若叶睦", "若叶睦"), start=2):
            self.repo.cards.append(replace(
                self.repo.cards[0], id=index, title=f"睦卡{index}",
                character=name, band="Ave Mujica", localized={},
            ))
        expected = [2, 3, 4]
        for term in ("墨缇丝", "Mortis", "若叶睦"):
            with self.subTest(term=term):
                self.assertEqual([card.id for card in card_matches(self.repo, term)], expected)
                self.assertIn("睦卡2", handle_command(f"/查卡 {term}", self.repo))
                with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
                    self.assertIn("睦卡2", self.parser.answer(f"/问 {term}的卡有哪些", self.repo))
        self.assertIn("墨缇丝 →", handle_command("/查缩写 墨缇丝", self.repo))
        with patch("ournotes_bot.qq.render_card_list", return_value=b"cards") as render:
            self.assertEqual(_image_reply("/查卡 墨缇丝", self.repo), b"cards")
            self.assertEqual([card.id for card in render.call_args.args[0]], expected)

    def test_ambiguous_manual_nickname_does_not_choose_first(self):
        self.repo.songs.append(replace(
            self.repo.songs[0], id=100002, title="另一首", titles=("另一首",),
            band="Ave Mujica", localized={"title": {"zh": "另一首"}},
        ))
        with tempfile.TemporaryDirectory() as directory:
            alias_file = Path(directory) / "query_aliases.json"
            alias_file.write_text(json.dumps({
                "song": {"撞名": "100001"}, "band": {"撞名": "Ave Mujica"},
            }, ensure_ascii=False), encoding="utf-8")
            with patch("ournotes_bot.entity_lexicon.ALIAS_FILE", alias_file):
                self.assertEqual(song_matches(self.repo, "撞名"), [])
                self.assertIn("没有找到", handle_command("/查曲 撞名", self.repo))
                self.assertIn("多个对象", handle_command("/查缩写 撞名", self.repo))
                self.assertIsNone(_image_reply("/查曲 撞名", self.repo))
                self.assertEqual([song.id for song in song_matches(self.repo, "迷星叫")], [100001])
                self.assertEqual([song.id for song in song_matches(self.repo, "100002")], [100002])

    def test_new_band_character_nicknames_are_not_global_words(self):
        for index, name in enumerate(("仲町阿拉蕾", "滨崎茉幌", "马桥心玖"), start=2):
            self.repo.cards.append(replace(
                self.repo.cards[0], id=index, title=f"测试卡{index}",
                character=name, band=("梦限大MewType", "millsage", "一家Dumb Rock!")[index - 2],
                localized={},
            ))
        for nickname, expected_id in (("亲漾", 2), ("茉幌", 3), ("心玖", 4)):
            with self.subTest(nickname=nickname):
                anchor = find_anchor("card", f"/问 {nickname}的卡有哪些", self.repo)
                self.assertFalse(anchor.ambiguous)
                self.assertEqual([card.id for card in cards_for(
                    QuerySpec("card", anchor.entity), self.repo
                )], [expected_id])

    def test_valid_model_queries_still_work(self):
        with patch.object(self.parser, "_request", return_value={
            "intent": "song", "query": "MyGO", "difficulty": "EXPERT",
            "level_operator": "gte", "level": 25,
        }):
            self.assertIn("迷星叫", self.parser.answer("/问 MyGO专家25级以上的歌", self.repo))
        with patch.object(self.parser, "_request", return_value={
            "intent": "chart", "query": "迷星叫", "difficulty": "EXPERT",
        }):
            self.assertIn("768 Notes", self.parser.answer("/问 迷星叫EX物量", self.repo))
        with patch.object(self.parser, "_request", return_value={
            "intent": "card", "query": "tmr", "difficulty": "",
        }):
            self.assertIn("高松灯", self.parser.answer("/问 tmr的卡", self.repo))

    def test_simple_catalog_questions_do_not_call_model(self):
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            self.assertIn("迷星叫", self.parser.answer("/问 MyGO的歌有哪些", self.repo))
            self.assertIn("迷星叫", self.parser.answer("/问 MyGO有哪些歌", self.repo))
            self.assertIn("高松灯", self.parser.answer("/问 tmr的卡有哪些", self.repo))
        self.assertEqual(self.parser.command_for("/问 MyGO的歌有哪些"), "查曲 MyGO!!!!!")

    def test_verified_alias_and_outside_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            alias_file = Path(directory) / "query_aliases.json"
            alias_file.write_text(json.dumps({
                "band": {"测试别名": "MyGO!!!!!"},
                "outside_catalog": ["未收录昵称"],
            }, ensure_ascii=False), encoding="utf-8")
            with patch("ournotes_bot.entity_lexicon.ALIAS_FILE", alias_file), \
                 patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
                self.assertIn("迷星叫", self.parser.answer("/问 测试别名的歌有哪些", self.repo))
                self.assertEqual(self.parser.answer("/问 未收录昵称的歌有哪些", self.repo), UNKNOWN_ENTITY)

    def test_alias_target_must_exist_in_current_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            alias_file = Path(directory) / "query_aliases.json"
            alias_file.write_text(json.dumps({
                "band": {"错误别名": "Morfonica", "类型不符": "迷星叫"},
            }, ensure_ascii=False), encoding="utf-8")
            with patch("ournotes_bot.entity_lexicon.ALIAS_FILE", alias_file):
                self.assertIsNone(resolve_entity("song", "错误别名", self.repo))
                self.assertIsNone(resolve_entity("song", "类型不符", self.repo))

    def test_model_cannot_substitute_another_valid_song(self):
        self.repo.songs.append(replace(
            self.repo.songs[0], id=100002, title="第二首", titles=("第二首",),
            band="Ave Mujica", localized={"title": {"zh": "第二首"}},
        ))
        question = "/问 迷星叫EX物量"
        with patch.object(self.parser, "_request", return_value={
            "intent": "chart", "query": "第二首", "difficulty": "EXPERT",
        }):
            self.assertEqual(self.parser.answer(question, self.repo), UNKNOWN_ENTITY)
        self.assertIsNone(self.parser.spec_for(question))

    def test_multiple_source_entities_are_ambiguous(self):
        self.repo.songs.append(replace(
            self.repo.songs[0], id=100002, title="第二首", titles=("第二首",),
            localized={"title": {"zh": "第二首"}},
        ))
        question = "/问 迷星叫和第二首的谱面"
        with patch.object(self.parser, "_request", return_value={
            "intent": "chart", "query": "迷星叫", "difficulty": "",
        }):
            self.assertEqual(self.parser.answer(question, self.repo), AMBIGUOUS_ENTITY)
        self.assertTrue(find_anchor("chart", question, self.repo).ambiguous)

    def test_structured_filters_and_text_use_the_same_entities(self):
        band = EntityRef("band", "MyGO!!!!!")
        spec = QuerySpec("song", band, "EXPERT", ">=", 25)
        self.assertEqual([song.id for song in songs_for(spec, self.repo)], [100001])
        self.assertIn("迷星叫", answer_for(spec, self.repo))
        self.assertEqual([song.id for song in songs_for(replace(spec, level=26), self.repo)], [])
        chart = chart_for(QuerySpec("chart", EntityRef("song", 100001), "EXPERT"), self.repo)
        self.assertIsNotNone(chart)
        self.assertEqual(chart[1][0].notes, 768)
        self.assertEqual([card.id for card in cards_for(
            QuerySpec("card", EntityRef("character", "高松灯")), self.repo
        )], [1])

    def test_local_grade_query_retains_typed_filter(self):
        question = "/问 MyGO专家25级以上的歌曲"
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            self.assertIn("迷星叫", self.parser.answer(question, self.repo))
        spec = self.parser.spec_for(question)
        self.assertEqual(spec.subject, EntityRef("band", "MyGO!!!!!"))
        self.assertEqual((spec.difficulty, spec.comparison, spec.level), ("EXPERT", ">=", 25))


if __name__ == "__main__":
    unittest.main()
