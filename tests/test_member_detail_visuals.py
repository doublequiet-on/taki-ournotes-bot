"""Member detail contracts with synthetic art and facts; no live services."""
from dataclasses import replace
from unittest.mock import patch
import unittest

from PIL import Image, ImageDraw

from ournotes_bot.data import Card, Skill, SupportCard
from ournotes_bot.card_visuals import detail
from ournotes_bot.member_detail_visuals import render, _time
from ournotes_bot.member_list_visuals import _frame
from ournotes_bot.visuals import _background


class MemberDetailVisualTests(unittest.TestCase):
    def setUp(self):
        self.card = Card(60, 60, "测试标题", "测试角色", "测试乐队", 4, 3, 1, 2, 3,
                         "2026-01-01T00:00:00+00:00", "", "full", "thumbnail",
                         skills=tuple(Skill(kind, "技能名字", "完整技能条件与数值132%和18%。末尾")
                                      for kind in ("leaderSkill", "liveSkill", "gekisouSkill")),
                         catalog={"stats_level1": [10, 20, 30], "detail_stale": True})

    def test_full_art_corners_survive_containment_and_frame(self):
        art = Image.new("RGBA", (400, 800), "red")
        d = ImageDraw.Draw(art)
        d.rectangle((200, 0, 399, 399), fill="green")
        d.rectangle((0, 400, 199, 799), fill="blue")
        d.rectangle((200, 400, 399, 799), fill="yellow")
        from ournotes_bot.card_visuals import _paste
        with patch("ournotes_bot.visuals._asset", side_effect=lambda url, size, **kw: art if url == "full" else None) as asset, \
             patch("ournotes_bot.member_detail_visuals._paste", wraps=_paste) as paste, \
             patch("ournotes_bot.visuals._bytes", side_effect=lambda image: image):
            picture = render(self.card)
        asset.assert_any_call("full", (1520, 2040), contain=True)
        self.assertFalse(any(call.args[0] == "thumbnail" for call in asset.call_args_list))
        x, y, width, height = paste.call_args.args[2]
        self.assertEqual(width / height, art.width / art.height)
        for px, py in ((10, 10), (390, 10), (10, 790), (390, 790)):
            self.assertEqual(picture.getpixel((2*x + px, 2*y + py)), art.getpixel((px, py))[:3])

    def test_complete_skills_state_and_missing_values(self):
        texts = []
        original = ImageDraw.ImageDraw.text
        def capture(draw, xy, value, **kwargs):
            texts.append(value)
            return original(draw, xy, value, **kwargs)
        with patch("ournotes_bot.visuals._asset", return_value=None), patch.object(ImageDraw.ImageDraw, "text", capture):
            render(self.card)
        joined = "".join(texts)
        self.assertEqual(joined.count("完整技能条件与数值132%和18%。末尾"), 3)
        for value in ("队长技能", "演出技能", "激奏技能", "Lv.1 / Rank 1 / 觉醒0次", "综合力  60", "上次有效缓存"):
            self.assertIn(value, joined)
        texts.clear()
        with patch("ournotes_bot.visuals._asset", return_value=None), patch.object(ImageDraw.ImageDraw, "text", capture):
            render(replace(self.card, catalog={"stats_level1": [float("nan"), 2, 3]}, skills=()))
        self.assertNotIn("综合力", "".join(texts))
        self.assertEqual("".join(texts).count("详情未获取，不代表无技能。"), 3)

    def test_resizing_shared_frame_preserves_rarity_colors_and_leaves_interior(self):
        for rarity in (2, 3, 4):
            image, draw = _background(500, 500, 2)
            center = image.getpixel((400, 400))
            _frame(image, draw, 20, 20, rarity, 400, 440)
            self.assertEqual(image.getpixel((400, 400)), center)
            upper, lower = image.getpixel((44, 150)), image.getpixel((44, 650))
            if rarity == 4:
                self.assertNotEqual(upper, lower)
            else:
                self.assertEqual(upper, lower)

    def test_snap_keeps_its_existing_renderer_and_time_requires_zone(self):
        support = SupportCard(1, "SNAP", "角色", (), 4, 1, 1, 1, 1, "", "", "", catalog={})
        with patch("ournotes_bot.visuals._asset", return_value=None), \
             patch("ournotes_bot.member_detail_visuals.render", side_effect=AssertionError("member only")):
            self.assertTrue(detail(support))
        self.assertEqual(_time("2026-01-01T00:00:00Z"), "2026-01-01 09:00（日本时间）")
        self.assertEqual(_time("2026-01-01T00:00:00"), "时间未确认")


if __name__ == "__main__":
    unittest.main()
