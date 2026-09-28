"""Offline member-list presentation contracts; all card data is synthetic."""
import io
import unittest
from dataclasses import replace
from unittest.mock import patch, Mock

from PIL import Image, ImageDraw

from ournotes_bot.data import Card, Skill
from ournotes_bot.card_visuals import grid
from ournotes_bot.member_list_visuals import condition_tags, tag_layout


class MemberListVisualTests(unittest.TestCase):
    def setUp(self):
        source = patch("ournotes_bot.member_list_visuals.get_snapshot", return_value=None)
        self.source = source.start()
        self.addCleanup(source.stop)
        self.card = Card(51, 1, "合成卡", "角色", "乐队", 4, 1, 0, 0, 0,
                         "", "", "", "", catalog={"categories": {"live": ["score"], "gekisou": ["COMBO"]}})

    def test_filter_labels_preserve_names_and_wrap_without_overflow(self):
        self.assertEqual(condition_tags(""), ["条件：不限"])
        self.assertEqual(condition_tags("SSR SR 角色=长崎爽世 乐队=Ave Mujica"),
                         ["稀有度：SSR / SR", "角色：长崎爽世", "乐队：Ave Mujica"])
        tags, _ = tag_layout("角色=" + "长名称" * 100)
        self.assertGreater(len(tags), 1)
        self.assertTrue(all(x + width <= 960 for _, x, _, width in tags))
        self.assertEqual("".join(t[0] for t in tags), "角色：" + "长名称" * 100)

    def test_full_page_keeps_dimensions_ids_categories_and_navigation(self):
        drawn = []
        original = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, **kwargs):
            drawn.append(str(text))
            return original(draw, xy, text, **kwargs)
        cards = [replace(self.card, id=i, rarity=(2, 3, 4)[i % 3], card_type=i % 5 + 1,
                         catalog={"categories": {"live": [["score"], ["life"], ["judgement"], [None]][i % 4],
                                                   "gekisou": [["JUST"], ["COMBO"], ["LUCK"], [None]][i % 4]}})
                 for i in range(1, 17)]
        footer = "共 30 张 · 第 1/2 页 · 本页 1–16\n下一页：/查卡 SSR SR 页2\n部分详情使用上次有效缓存。"
        with patch("ournotes_bot.visuals._asset", return_value=None), patch.object(ImageDraw.ImageDraw, "text", capture):
            raw = grid(cards, "SSR SR", footer=footer)
        self.assertEqual(Image.open(io.BytesIO(raw)).width, 2000)
        for text in ("数据暂不可用", *map(str, range(1, 17)), *footer.splitlines()):
            self.assertIn(text, drawn)
        self.assertFalse({"SSR", "SR", "R"}.intersection(drawn))
        self.assertEqual(drawn.count("队长"), 16)
        self.assertEqual(drawn.count("演出"), 16)
        self.assertEqual(drawn.count("激奏"), 16)
        self.assertFalse(any(word in " ".join(drawn) for word in ("原生卡框未取得", "中性边框", "排序", "按稀有度")))

    def test_numeric_summaries_fit_without_losing_conditions(self):
        card = replace(self.card, skills=(
            Skill("liveSkill", "", "【生命值】5.0秒内得分提升90.0%发动时若LIFE在700及以上得分提升110.0%"),
            Skill("gekisouSkill", "", "LUCK激奏开始时有43%的概率发动抽选条累积100.0%LIFE在700及以上时，有65%的概率发动")))
        self.source.return_value = Mock(stale=False)
        self.source.return_value.for_card.return_value = (
            ("技巧值 +132%", "Ave Mujica成员\n演出【判定】成员另+18%"),
            ("得分 +90→110%", "5秒 · LIFE≥700取后值"),
            ("LUCK起始条100%", "概率43→65% · LIFE≥700"))
        drawn = []
        original = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, **kwargs):
            drawn.append(text)
            return original(draw, xy, text, **kwargs)
        with patch("ournotes_bot.visuals._asset", return_value=None), patch.object(ImageDraw.ImageDraw, "text", capture):
            grid([card], "")
        self.assertIn("技巧值 +132%", drawn)
        self.assertIn("Ave Mujica成员", drawn)
        self.assertIn("演出【判定】成员另+18%", drawn)
        self.assertIn("得分 +90→110%", drawn)
        self.assertIn("LUCK起始条100%", drawn)
        self.assertIn("概率43→65% · LIFE≥700", drawn)
        self.assertNotIn("摘要较长", drawn)

    def test_single_condition_match_stays_list_and_snap_bypasses_new_renderer(self):
        with patch("ournotes_bot.visuals._asset", return_value=None):
            self.assertTrue(grid([self.card], "SSR"))
            with patch("ournotes_bot.member_list_visuals.render", side_effect=AssertionError("member only")):
                self.assertTrue(grid([self.card], "SSR", support=True))


if __name__ == "__main__":
    unittest.main()
