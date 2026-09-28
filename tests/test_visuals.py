"""Visual contracts: correct labels, bounded text and readable complete details."""
from __future__ import annotations

from dataclasses import replace
import io
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from ournotes_bot.data import Card, Chart, Skill, Song, SupportCard
from ournotes_bot.efficiency_query import MetaAnswer
from ournotes_bot import visuals


class VisualTests(unittest.TestCase):
    def setUp(self):
        self.song = Song(100001, "迷星叫", ("迷星叫",), "MyGO!!!!!", "", "", "", "", "",
                         (Chart("EXPERT", 25, 25.5, 768, ""),))

    def capture(self, render):
        calls = []
        canvas_sizes = []
        original = ImageDraw.ImageDraw.text
        encoder = visuals._bytes

        def encode(image):
            canvas_sizes.append(image.size)
            return encoder(image)

        def text(draw, xy, value, *args, **kwargs):
            box = draw.textbbox(xy, value, font=kwargs["font"])
            calls.append((str(value), box))
            return original(draw, xy, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", text), patch.object(visuals, "_asset", return_value=None), \
             patch.object(visuals, "_bytes", side_effect=encode):
            raw = render()
        image = Image.open(io.BytesIO(raw))
        for value, (left, top, right, bottom) in calls:
            self.assertGreaterEqual(left, 0, value)
            self.assertGreaterEqual(top, 0, value)
            # Text coordinates belong to the drawing canvas, before the shared
            # upload encoder uniformly scales oversized images.
            self.assertLessEqual(right, canvas_sizes[-1][0], value)
            self.assertLessEqual(bottom, canvas_sizes[-1][1] - 32, value)
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
        self.assertTrue(all(box[2] < 1360 for box in title_boxes))
        self.assertEqual(sum(text == "EXPERT" for text, _ in calls), 16)

    def test_localized_headers_and_missing_assets_stay_in_canvas(self):
        for locale in ("zh", "en", "ja"):
            with self.subTest(locale=locale):
                self.capture(lambda: visuals.render_song_list([self.song], "MyGO!!!!!", locale))
                self.capture(lambda: visuals.render_song_list([], "MyGO!!!!!", locale))

    def test_song_header_hierarchy_and_native_mark_fallback(self):
        from ournotes_bot.song_traits import SongTraits
        song = replace(self.song, traits=SongTraits(2, ("JUST", "JUST", "COMBO")))
        for render in (lambda: visuals.render_song_list([song], "蓝色"),
                       lambda: visuals.render_chart(song, song.charts)):
            # With a real asset-shaped RGBA mark, no color words replace the icon.
            native = Image.new("RGBA", (76, 76), "blue")
            length = ImageDraw.ImageDraw.textlength
            def fractional(draw, text, *args, **kwargs):
                return length(draw, text, *args, **kwargs) + 0.25
            # Noto on Linux yields fractional advances; a rounded-down chip must not clip its last glyph.
            with patch.object(visuals, "_song_marks", return_value={2: native}), \
                 patch.object(ImageDraw.ImageDraw, "textlength", fractional):
                calls = self.capture(render)
            title = next(box for text, box in calls if text == song.title)
            chip = next(box for text, box in calls if "JUST → JUST → COMBO" in text)
            identity = next(box for text, box in calls if text.startswith("ID "))
            self.assertLess(title[3], chip[1])
            self.assertLess(chip[3], identity[1])
            self.assertFalse(any("属性：" in text or "颜色：" in text for text, _ in calls))
        missing = self.capture(lambda: visuals.render_chart(song, song.charts))
        self.assertTrue(any("蓝色（图标暂缺）" in text for text, _ in missing))

    def test_long_mission_sequence_and_title_keep_identity_below(self):
        from ournotes_bot.song_traits import SongTraits
        song = replace(self.song, title="长标题" * 20, traits=SongTraits(1, ("COMBO",) * 16, True))
        calls = self.capture(lambda: visuals.render_song_list([song], "激奏测试"))
        identity = next(box for text, box in calls if text.startswith("ID "))
        mission = [(text, box) for text, box in calls if "COMBO" in text or "旧缓存" in text]
        self.assertEqual("".join(text for text, _ in calls).count("COMBO"), 16)
        self.assertTrue(all(box[3] < identity[1] for _, box in mission))

    def test_tiny_text_area_terminates_without_overflow(self):
        draw = ImageDraw.Draw(Image.new("RGB", (50, 50)))
        with patch.object(draw, "text") as text:
            visuals._write(draw, "缺图", 0, 0, 1, 20)
        self.assertEqual(text.call_args.args[1], "")

    def test_reply_heading_has_no_brand_wordmark(self):
        calls = self.capture(lambda: visuals.render_song_list([self.song], "MyGO!!!!!"))
        self.assertNotIn("taki", [text.casefold() for text, _ in calls])
        self.assertIn("曲目检索", [text for text, _ in calls])

    def test_background_is_a_crop_of_one_fixed_pattern(self):
        large, _ = visuals._background(1500, 3600)
        for width, height in ((900, 760), (1100, 218), (1100, 2380)):
            small, _ = visuals._background(width, height)
            self.assertIsNone(ImageChops.difference(small, large.crop((0, 0, width, height))).getbbox())
        self.assertEqual({m[0] for m in visuals._decoration_layout(900, 760)},
                         {"meteor", "moon", "stone", "flower"})

    def test_badges_keep_color_and_text_padding_on_both_row_backgrounds(self):
        original = ImageDraw.ImageDraw.text
        for difficulty in visuals.DIFFICULTY_COLORS:
            for width, height in ((86, 66), (170, 78)):
                samples = []
                for background, label in (("#FFFFFF", difficulty), (visuals.SURFACE, difficulty.lower())):
                    canvas = Image.new("RGB", (width + 20, height + 20), background)
                    boxes = []
                    def record(draw, xy, text, *args, **kwargs):
                        boxes.append(draw.textbbox(xy, text, font=kwargs["font"]))
                        return original(draw, xy, text, *args, **kwargs)
                    with patch.object(ImageDraw.ImageDraw, "text", record):
                        visuals._difficulty_badge(ImageDraw.Draw(canvas), label, "25.5", (10, 10, width + 10, height + 10))
                    self.assertGreaterEqual(boxes[0][1], 15)
                    self.assertGreaterEqual(boxes[1][1] - boxes[0][3], 4)
                    self.assertLessEqual(boxes[1][3], height + 5)
                    samples.append(canvas.crop((34, 12, width - 14, height + 8)))
                self.assertIsNone(ImageChops.difference(*samples).getbbox())

    def test_meta_rows_have_no_header_and_keep_values_and_notes(self):
        cells = (("1", "迷星叫", "EXPERT", "25", "1:35", "1,164.72%", "560.25%"),
                 ("2", "影色舞", "HARD", "22", "1:30", "1,106.39%", "552.37%"))
        answer = MetaAnswer("", cells=cells, scope="全难度 · 每分钟得分效率从高到低",
                            page_notice="第 1/2 页\n下一页：/查分数表 页2",
                            notes=("旧缓存（刷新失败或过期）", "保留参考条件和来源说明"), jackets=("", ""))
        calls = self.capture(lambda: visuals.render_meta(answer))
        texts = [text for text, _ in calls]
        for text in ("排名", "歌曲", "难度"):
            self.assertNotIn(text, texts)
        for row in cells:
            for value in row:
                self.assertIn(value, texts)
        self.assertEqual(texts.count("得分系数"), len(cells))
        self.assertIn("下一页：/查分数表 页2", texts)
        self.assertIn("旧缓存（刷新失败或过期）", texts)

    def test_rounded_art_preserves_center_and_source_pixels(self):
        source = Image.new("RGB", (80, 80), "#ff0000")
        canvas = Image.new("RGB", (100, 100), "#ffffff")
        visuals._paste_loaded_asset(canvas, ImageDraw.Draw(canvas), source, (10, 10, 90, 90))
        self.assertEqual(canvas.getpixel((10, 10)), (255, 255, 255))
        self.assertEqual(canvas.getpixel((50, 50)), (255, 0, 0))
        self.assertEqual(source.getpixel((0, 0)), (255, 0, 0))
        self.assertTrue(any(0 < canvas.getpixel((x, y))[1] < 255
                            for x in range(10, 35) for y in range(10, 35)))

    def test_shapes_have_smooth_edges_without_blurring_interiors_or_leaving_seams(self):
        canvas = Image.new("RGB", (120, 420), "white")
        draw = visuals._ScaledDraw(canvas, 1)
        draw.rounded_rectangle((10, 10, 110, 100), radius=24, fill="black")
        self.assertEqual(canvas.getpixel((60, 50)), (0, 0, 0))
        self.assertEqual(canvas.getpixel((0, 0)), (255, 255, 255))
        self.assertTrue(any(0 < canvas.getpixel((x, y))[0] < 255
                            for x in range(10, 40) for y in range(10, 40)))
        draw.line(((10, 110), (110, 410)), fill="black", width=2)
        for y in range(112, 409):
            values = [canvas.getpixel((x, y))[0] for x in range(120)]
            self.assertLess(min(values), 100, f"missing diagonal at row {y}")
            self.assertTrue(any(0 < value < 255 for value in values), y)

    def test_high_resolution_uses_source_detail_and_larger_glyphs(self):
        canvas, draw = visuals._canvas(900, 760, "高清")
        self.assertEqual(canvas.size, (1800, 1520))
        source = Image.new("RGB", (32, 32), "white")
        for x in range(0, 32, 2):
            ImageDraw.Draw(source).line((x, 0, x, 31), fill="black")
        with patch.object(visuals, "_asset", return_value=source) as asset:
            visuals._paste_asset(canvas, draw, "sample", (50, 200, 66, 216))
        asset.assert_called_once_with("sample", (32, 32))
        self.assertEqual(canvas.getpixel((110, 416)), (0, 0, 0))
        self.assertEqual(canvas.getpixel((111, 416)), (255, 255, 255))
        with patch.object(ImageDraw.ImageDraw, "text") as text:
            visuals._write(draw, "高清文字", 100, 240, 200, 28)
        self.assertEqual(text.call_args.kwargs["font"].size, 56)
        self.assertEqual(text.call_args.args[0], [200, 480])

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
