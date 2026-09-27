"""Visual contracts: correct labels, bounded text and readable complete details."""
from __future__ import annotations

from dataclasses import replace
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

from ournotes_bot.data import Card, Chart, Skill, Song, SupportCard
from ournotes_bot import visuals


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.song = Song(100001, "迷星叫", ("迷星叫",), "MyGO!!!!!", "", "", "", "", "",
                         (Chart("EXPERT", 25, 25.5, 768, ""),))

    def capture(self, render):
        calls = []
        original = ImageDraw.ImageDraw.text

        def text(draw, xy, value, *args, **kwargs):
            box = draw.textbbox(xy, value, font=kwargs["font"])
            calls.append((str(value), box))
            return original(draw, xy, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", text), patch.object(visuals, "_asset", return_value=None):
            raw = render()
        image = Image.open(io.BytesIO(raw))
        for value, (left, top, right, bottom) in calls:
            self.assertGreaterEqual(left, 0, value)
            self.assertGreaterEqual(top, 0, value)
            self.assertLessEqual(right, image.width, value)
            self.assertLessEqual(bottom, image.height - 16, value)
        image.close()
        return calls

    def test_filtered_or_reordered_charts_keep_difficulty_identity(self):
        normal = Chart("NORMAL", 13, 13, 408, "")
        with patch.object(visuals, "_asset", return_value=None), \
             patch.object(visuals, "_difficulty_badge", wraps=visuals._difficulty_badge) as badges:
            visuals.render_song_list([replace(self.song, charts=(*self.song.charts, normal))], "EX/NM")
        self.assertEqual([(c.args[1], c.args[2]) for c in badges.call_args_list],
                         [("EASY", "—"), ("NORMAL", "13"), ("HARD", "—"), ("EXPERT", "25.5")])

    def test_long_titles_and_full_page_do_not_overlap_badges_or_footer(self):
        songs = [replace(self.song, id=100001 + i, title="很长的标题LongTitle" * 20) for i in range(16)]
        calls = self.capture(lambda: visuals.render_song_list(songs, "长度检查", footer="第 1/6 页\n下一页：/查曲 页2"))
        self.assertIn("下一页：/查曲 页2", [text for text, _ in calls])
        title_boxes = [box for text, box in calls if "Long" in text or "很长" in text]
        self.assertTrue(title_boxes)
        self.assertTrue(all(box[2] < 680 for box in title_boxes))
        self.assertEqual(sum(text == "EXPERT" for text, _ in calls), 16)

    def test_localized_headers_and_missing_assets_stay_in_canvas(self):
        for locale in ("zh", "en", "ja"):
            with self.subTest(locale=locale):
                self.capture(lambda: visuals.render_song_list([self.song], "MyGO!!!!!", locale))
                self.capture(lambda: visuals.render_song_list([], "MyGO!!!!!", locale))

    def test_tiny_text_area_terminates_without_overflow(self):
        draw = ImageDraw.Draw(Image.new("RGB", (50, 50)))
        with patch.object(draw, "text") as text:
            visuals._write(draw, "缺图", 0, 0, 1, 20)
        self.assertEqual(text.call_args.args[1], "")

    def test_reply_heading_has_no_brand_wordmark(self):
        calls = self.capture(lambda: visuals.render_song_list([self.song], "MyGO!!!!!"))
        self.assertNotIn("taki", [text.casefold() for text, _ in calls])
        self.assertIn("曲目检索", [text for text, _ in calls])

    def test_motifs_scale_with_canvas_and_repeat_down_long_images(self):
        small = visuals._decoration_layout(900, 1200)
        large = visuals._decoration_layout(1800, 2400)
        self.assertEqual(len(small), len(large))
        for before, after in zip(small, large):
            self.assertEqual(before[0], after[0])
            self.assertAlmostEqual(after[3], before[3] * 2, delta=1)
        tall = visuals._decoration_layout(900, 3600)
        self.assertGreater(len(tall), 2 * len(small))
        self.assertEqual(tall[0][3], small[0][3])
        self.assertEqual({m[0] for m in tall}, {"meteor", "moon", "stone", "flower"})
        for width, height in ((900, 760), (1100, 218), (1100, 2380), (1500, 3134)):
            for _, x, y, size, _ in visuals._decoration_layout(width, height):
                self.assertGreaterEqual(min(x, y), 0)
                self.assertLessEqual(x + size * 1.06, width)
                self.assertLessEqual(y + size, height)

    def test_rounded_art_preserves_center_and_source_pixels(self):
        source = Image.new("RGB", (80, 80), "#ff0000")
        canvas = Image.new("RGB", (100, 100), "#ffffff")
        visuals._paste_loaded_asset(canvas, ImageDraw.Draw(canvas), source, (10, 10, 90, 90))
        self.assertEqual(canvas.getpixel((10, 10)), (255, 255, 255))
        self.assertEqual(canvas.getpixel((50, 50)), (255, 0, 0))
        self.assertEqual(source.getpixel((0, 0)), (255, 0, 0))

    def test_detail_height_expands_without_dropping_skill_text(self):
        description = "在指定条件下演出效果提高，持续时间与实际技能资料一致。" * 8 + "末尾验证"
        skills = tuple(Skill("liveSkill", f"技能 {i}", description) for i in range(4))
        card = Card(1, 1, "很长的卡牌名称" * 15, "椎名立希", "MyGO!!!!!", 4, 1,
                    100, 100, 100, "", "", "", "", skills=skills)
        support = SupportCard(1, "很长的支援卡名称" * 10, "高松灯 / 千早爱音 / 要乐奈 / 长崎素世 / 椎名立希",
                              (), 4, 1, 100, 100, 100, "", "", "", skills=skills)
        for item, renderer in ((card, visuals.render_card), (support, visuals.render_support_card)):
            with self.subTest(renderer=renderer.__name__):
                calls = self.capture(lambda: renderer(item))
                joined = "".join(text for text, _ in calls)
                self.assertEqual(joined.count(description), len(skills))
                self.assertNotIn("…", joined)


if __name__ == "__main__":
    unittest.main()
