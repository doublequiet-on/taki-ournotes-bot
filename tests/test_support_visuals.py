"""Offline SNAP rendering contracts; synthetic facts and art only."""
from dataclasses import replace
from unittest.mock import patch
import unittest
from PIL import Image, ImageDraw

from ournotes_bot.data import SupportCard, Skill
from ournotes_bot.card_visuals import grid, detail
from ournotes_bot.support_visuals import render_detail, render_list
from ournotes_bot.support_summary import summarize, entries


class SupportVisualTests(unittest.TestCase):
    def test_summary_keeps_base_units_trigger_and_cap(self):
        # Small factual text examples from cached Project Yume support cards 1/5;
        # values changed here deliberately to test extraction, not ID lookup.
        duration = Skill("supportSkill", "延长", "装配此技能的成员的演出技能发动时间延长2.50秒\n若为「测试乐队」成员，\n则演出技能发动时间延长5.00秒")
        combo = Skill("gekisouSupportSkill", "COMBO", "在COMBO激奏中，每达成10次激奏COMBO\n得分提升4.00%（最高30.00%）\n若为「测试乐队」成员\n则得分提升8.00%")
        self.assertEqual(summarize(duration), ("延长 +2.5秒", ""))
        self.assertEqual(summarize(combo), ("COMBO得分 +4%", "每10连击 · 上限30%"))
        for bad in (replace(duration, description=duration.description + "未知额外条件"),
                    replace(duration, kind="gekisouSupportSkill"),
                    replace(duration, description=duration.description.replace("2.50", "nan"))):
            self.assertEqual(summarize(bad)[0], "摘要未确认")

    def test_summary_retains_multiple_live_and_explicit_not_applicable(self):
        card = replace(self.card, skills=(Skill("supportSkill", "延长", "装配此技能的成员的演出技能发动时间延长3.00秒"),
                                          Skill("supportSkill", "回复", "装配此技能的成员发动演出技能时，LIFE回复550")))
        self.assertEqual(entries(card), [("演出", "延长 +3秒", ""), ("演出", "LIFE回复 +550", ""), ("激奏", "不适用", "")])

    def setUp(self):
        self.card = SupportCard(61, "测试支援", "甲 / 乙", ("甲", "乙"), 10, 1,
            1, 2, 3, "2026-01-01T00:00:00Z", "full", "thumb",
            skills=(Skill("supportSkill", "延长", "通常2.50秒；成员条件成立5.00秒。"),
                    Skill("supportSkill", "回复", "LIFE回复550。")),
            catalog={"stats_level1": [5.69, 4.88, 5.28], "categories": {
                "live": ["duration", "life"], "gekisou": ["not_applicable"]}})

    def capture(self, card):
        texts = []
        original = ImageDraw.ImageDraw.text
        def text(draw, xy, value, **kwargs):
            texts.append(value)
            return original(draw, xy, value, **kwargs)
        with patch("ournotes_bot.visuals._asset", return_value=None), patch.object(ImageDraw.ImageDraw, "text", text):
            render_detail(card)
        return "".join(texts)

    def test_ex_keeps_both_skills_and_conditions_and_percent_stats(self):
        result = self.capture(self.card)
        self.assertEqual(result.count("演出支援 · Lv.5"), 2)
        for value in ("通常2.50秒；成员条件成立5.00秒。", "LIFE回复550。", "不适用", "5.69%", "甲 / 乙"):
            self.assertIn(value, result)
        self.assertNotIn("综合力", result)
        self.assertNotIn("队长技能", result)

    def test_missing_is_not_not_applicable_or_zero(self):
        result = self.capture(replace(self.card, skills=(), catalog={"stats_level1": [float('nan'), 0, 1]}))
        self.assertEqual(result.count("详情未获取，不代表无技能。"), 2)
        self.assertNotIn("不适用", result)
        self.assertNotIn("nan", result)
        self.assertIn("数值状态未核实", result)

    def test_single_condition_stays_list_and_id_stays_detail(self):
        with patch("ournotes_bot.support_visuals.render_list", return_value=b'list') as listing, \
             patch("ournotes_bot.support_visuals.render_detail", return_value=b'detail') as details:
            self.assertEqual(grid([self.card], "EX", support=True), b'list')
            self.assertEqual(detail(self.card), b'detail')
            listing.assert_called_once()
            details.assert_called_once()

    def test_full_landscape_is_contained_and_list_does_not_fetch_full(self):
        from ournotes_bot.card_visuals import _paste
        art = Image.new('RGBA', (900, 450), 'red')
        with patch('ournotes_bot.visuals._asset', return_value=art) as asset, \
             patch('ournotes_bot.support_visuals._paste', wraps=_paste) as paste:
            render_detail(self.card)
            asset.assert_called_once_with('full', (1800, 1800), contain=True)
            self.assertEqual(paste.call_args.args[2][2:], (450, 225))
            asset.reset_mock()
            render_list([self.card], 'EX')
            self.assertFalse(any(c.args[0] == 'full' for c in asset.call_args_list))


if __name__ == '__main__':
    unittest.main()
