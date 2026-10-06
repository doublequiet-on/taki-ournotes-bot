"""Preserve complete comparison images while releasing temporary table canvases."""
import io
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, get_ident
import unittest
from unittest.mock import patch

from PIL import Image

from ournotes_bot import visuals
from ournotes_bot.query.efficiency_query import MetaAnswer


class MetaRenderMemoryTests(unittest.TestCase):
    def panel(self, count, title):
        return MetaAnswer(
            '', title=title, scope='同一捕获快照',
            cells=tuple((str(i + 1), f'歌曲 {i + 1}', 'EXPERT', '25', '1:30', '100%', '200%')
                        for i in range(count)),
            jackets=('',) * count, page_notice=f'完整 {count} 条', notes=('数据来源保持',),
            selection_numbers=tuple(range(1, count + 1)),
        )

    def reference(self, answer):
        panels = [visuals._meta_image(panel) for panel in answer.panels]
        gap = 20 * visuals.RENDER_SCALE
        width = max(panel.width for panel in panels)
        height = max(panel.height for panel in panels)
        image, _ = visuals._background(
            (width * len(panels) + gap * (len(panels) - 1)) // visuals.RENDER_SCALE,
            height // visuals.RENDER_SCALE, scale=visuals.RENDER_SCALE,
        )
        try:
            for index, panel in enumerate(panels):
                image.paste(panel, (index * (width + gap), 0))
            return visuals._bytes(image)
        finally:
            image.close()
            for panel in panels:
                panel.close()

    def test_comparison_preserves_reference_bytes_for_unequal_panels(self):
        for right in (self.panel(1, '激奏'), MetaAnswer('本场景没有记录', title='激奏', complete_text=True)):
            answer = MetaAnswer('', panels=(self.panel(3, '自由'), right))
            captured = repr(answer)
            with self.subTest(table=bool(right.cells)), patch.object(visuals, '_asset', return_value=None):
                expected = self.reference(answer)
                actual = visuals.render_meta(answer)
            self.assertEqual(actual, expected)
            self.assertEqual(repr(answer), captured)

    def test_comparison_releases_one_panel_before_allocating_the_next(self):
        answer = MetaAnswer('', panels=(self.panel(2, '自由'), self.panel(1, '激奏')))
        original = visuals._meta_image
        owned = []

        def observe(panel, **kwargs):
            if not kwargs.get('size_only'):
                for image in owned:
                    with self.assertRaises(ValueError):
                        image.getpixel((0, 0))
            result = original(panel, **kwargs)
            if isinstance(result, Image.Image):
                owned.append(result)
            return result

        with patch.object(visuals, '_asset', return_value=None), patch.object(visuals, '_meta_image', side_effect=observe):
            blob = visuals.render_meta(answer)
        with Image.open(io.BytesIO(blob)) as output:
            self.assertEqual(output.format, 'JPEG')
        self.assertEqual(len(owned), 2)
        for image in owned:
            with self.assertRaises(ValueError):
                image.getpixel((0, 0))

    def test_dimensions_use_captured_native_marks_once(self):
        answer = MetaAnswer('', panels=(self.panel(2, '自由'), self.panel(1, '激奏')))
        with patch.object(visuals, '_asset', return_value=None), \
             patch.object(visuals, '_song_marks', return_value={}) as song_marks, \
             patch.object(visuals, '_mission_marks', return_value={}) as mission_marks:
            visuals.render_meta(answer)
        song_marks.assert_called_once()
        mission_marks.assert_called_once()

    def test_concurrent_callers_reuse_one_render_thread(self):
        ready = Barrier(2)
        render_threads, caller_threads = [], []

        def render(answer):
            render_threads.append(get_ident())
            return answer.text.encode()

        def request(answer):
            caller_threads.append(get_ident())
            ready.wait(timeout=5)
            return visuals.render_meta(answer)

        with patch.object(visuals, '_render_meta', side_effect=render), ThreadPoolExecutor(max_workers=2) as callers:
            outputs = list(callers.map(request, (MetaAnswer('free'), MetaAnswer('battle'))))
        self.assertEqual(outputs, [b'free', b'battle'])
        self.assertEqual(len(set(caller_threads)), 2)
        self.assertEqual(len(set(render_threads)), 1)
        self.assertTrue(set(render_threads).isdisjoint(caller_threads))


if __name__ == '__main__':
    unittest.main()
