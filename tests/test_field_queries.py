"""Local projections do not spend model quota or draw unrelated full views."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.config import Settings
from ournotes_bot.commands import resolve_command, handle_command
from ournotes_bot.data import SongRepository, Skill
from ournotes_bot.platforms.qq.qq import _prepare_reply
from ournotes_bot.sources.yatta import BASE, build_data, build_support_cards
from ournotes_bot.sources.haneoka.song_traits import SongTraits
from test_query import CHARACTERS, CARDS, SONGS, META, SUPPORTS


class FieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SongRepository(BASE, Path(self.tmp.name) / "cache.json")
        self.repo.songs, self.repo.cards = build_data(CHARACTERS, CARDS, SONGS, META)
        self.repo.support_cards = build_support_cards(CHARACTERS, SUPPORTS)
        self.repo.songs = [replace(self.repo.songs[0], traits=SongTraits(2, ("JUST", "COMBO")))]
        self.parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6, ""))
        for target in ("ournotes_bot.platforms.qq.qq._chart_image", "ournotes_bot.platforms.qq.qq.render_song_list",
                       "ournotes_bot.platforms.qq.qq.render_card", "ournotes_bot.platforms.qq.qq.render_support_card"):
            blocked = patch(target, side_effect=AssertionError("unrelated rendering"))
            blocked.start()
            self.addCleanup(blocked.stop)
        model = patch.object(self.parser, "_request", side_effect=AssertionError("model called"))
        model.start()
        self.addCleanup(model.stop)

    def ask(self, question):
        reply = _prepare_reply("/问 " + question, self.repo, self.parser)
        self.assertIsNone(reply.image)
        return reply.text

    def test_fields_are_local_and_do_not_draw(self):
        for question, expected in (("迷星叫EX物量", "768 Notes"), ("迷星叫 EX 几级", "Lv.25"),
                                   ("迷星叫的颜色是什么", "蓝色"), ("迷星叫激奏顺序是什么", "JUST → COMBO"),
                                   ("迷星叫有哪些难度", "EASY / NORMAL / HARD / EXPERT")):
            with self.subTest(question=question):
                self.assertIn(expected, self.ask(question))

    def test_missing_note_zero_and_unknown_entity(self):
        song = self.repo.songs[0]
        self.repo.songs = [replace(song, charts=(replace(song.charts[-1], notes=None),))]
        self.assertIn("暂无资料", self.ask("迷星叫 EX Note"))
        self.repo.songs = [replace(song, charts=(replace(song.charts[-1], notes=0),))]
        self.assertIn("0 Notes", self.ask("迷星叫 EX Note"))
        self.assertIn("未找到", self.ask("不存在歌曲 EX Note"))

    def test_documented_question_word_order_stays_local(self):
        for question, expected in (("迷星叫 EX 多少 Note", "768 Notes"),
                                   ("迷星叫EX有多少个音符", "768 Notes"),
                                   ("迷星叫是什么颜色", "蓝色"),
                                   ("迷星叫是什么属性", "蓝色")):
            with self.subTest(question=question):
                self.assertIn(expected, self.ask(question))

    def test_short_id_and_unknown_conditions_do_not_change_entity(self):
        self.assertIn("ID 100001", self.ask("1 EX Note"))
        self.assertIn("无法识别", self.ask("迷星叫 foo=1 EX Note"))

    def test_ambiguous_short_field_never_selects_first(self):
        self.repo.songs.append(replace(self.repo.songs[0], id=100002))
        text = self.ask("迷星叫 EX Note")
        self.assertIn("100001", text)
        self.assertIn("100002", text)
        self.assertNotIn("768 Notes", text)

    def test_count_complete_set_not_page_and_unknown_coverage(self):
        song = self.repo.songs[0]
        self.repo.songs = [replace(song, id=100000 + i) for i in range(1, 36)]
        self.assertIn("35 首", self.ask("MyGO 有多少首歌"))
        self.repo.songs[-1] = replace(self.repo.songs[-1], traits=None)
        text = self.ask("MyGO 颜色=蓝 有多少首歌")
        self.assertIn("已确认匹配的歌曲共 34 首", text)
        self.assertIn("1 首歌曲缺少", text)
        self.assertIn("0 首", self.ask("颜色=红 有多少首歌"))

    def test_typed_skills_preserve_conditions_and_untyped_ids_clarify(self):
        self.assertIn("明确卡牌类型", self.ask("卡 1 技能"))
        skill = Skill("liveSkill", "合成技能", "仅 MyGO 成员；持续 5 秒；最多触发 1 次。")
        card = replace(self.repo.cards[0], skills=(skill,))
        with patch.object(self.repo, "card_with_detail", return_value=card) as fetch:
            text = self.ask("成员卡 1 技能")
        self.assertIn("Lv.5", text)
        self.assertIn("持续 5 秒；最多触发 1 次", text)
        fetch.assert_called_once()
        self.assertIn("不适用", self.ask("SNAP 1 队长技能"))

    def test_numbering_is_visible_and_distinct_from_id(self):
        text = handle_command("/查曲 MyGO", self.repo)
        self.assertIn("01. ID 100001", text)
        text = handle_command("/查卡 MyGO", self.repo)
        self.assertIn("01. ID 1", text)


if __name__ == "__main__":
    unittest.main()
