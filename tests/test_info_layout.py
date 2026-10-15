"""Information images retain full text, complete rows and the established font scale."""
import io
import unittest
from unittest.mock import patch

from PIL import Image

from ournotes_bot.query.contracts.info import InfoAnswer, InfoRow, InfoView, info_text
from ournotes_bot.rendering import info_layout, readable_pages
from ournotes_bot.rendering.image_output import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS


class InformationLayoutTests(unittest.TestCase):
    def answer(self, *views):
        return InfoAnswer(info_text(views), views=views)

    def test_text_contract_keeps_labels_paragraphs_commands_and_notes(self):
        view = InfoView('活动列表', '日服', rows=(InfoRow('活动一', ('首段', '第二段'),
            label='进行中', command='/查活动 1'),), footer=('资料来源 Haneoka',))
        self.assertEqual(info_text((view,)), '活动列表\n日服\n活动一\n进行中\n首段\n第二段\n/查活动 1\n资料来源 Haneoka')

    def test_three_layouts_keep_original_width_and_fit_transport_budgets(self):
        for layout in ('list', 'detail', 'character'):
            view = InfoView('资料详情', '名称与时间 · 日本时间', hero_url='synthetic:hero',
                rows=(InfoRow('信息', ('第一段介绍。', '第二段介绍。'), image_url='synthetic:row',
                command='/查角色 1', rarity=4),), footer=('缺失资料明确显示未知。',), layout=layout)
            pages = info_layout.render_info(self.answer(view))
            self.assertEqual(len(pages), 1)
            self.assertLessEqual(len(pages[0].image), MAX_IMAGE_BYTES)
            with Image.open(io.BytesIO(pages[0].image)) as im:
                self.assertEqual(im.width, 1080)
                self.assertLessEqual(im.width * im.height, MAX_IMAGE_PIXELS)
            self.assertIn('第二段介绍。', pages[0].text)
            self.assertIn('/查角色 1', pages[0].text)

    def test_overflow_preserves_every_complete_list_row_once(self):
        rows = tuple(InfoRow(f'条目 {i:02d}', (f'说明 {i:02d}',), command=f'/查卡池 {i:02d}') for i in range(30))
        with patch.object(readable_pages, 'MAX_IMAGE_EDGE', 1800):
            pages = info_layout.render_info(self.answer(InfoView('卡池列表', rows=rows)))
        self.assertGreater(len(pages), 1)
        for row in rows:
            owners = [p for p in pages if row.title in p.text]
            self.assertEqual(len(owners), 1)
            self.assertIn(row.body[0], owners[0].text)
            self.assertIn(row.command, owners[0].text)

    def test_long_biography_splits_at_paragraphs_without_losing_text_or_shrinking(self):
        paragraphs = tuple(f'第{i:02d}段：这是角色资料中的完整介绍，包含足够长的文字用于验证分页。' * 3 for i in range(12))
        view = InfoView('角色详情', rows=(InfoRow('角色简介', paragraphs),), layout='character')
        drawn = []
        original = info_layout.Canvas.text
        def capture(canvas, x, cy, value, size=28, *args, **kwargs):
            drawn.append((str(value), size))
            return original(canvas, x, cy, value, size, *args, **kwargs)
        with patch.object(readable_pages, 'MAX_IMAGE_EDGE', 1800), patch.object(info_layout.Canvas, 'text', capture):
            pages = info_layout.render_info(self.answer(view))
        self.assertGreater(len(pages), 1)
        combined = ''.join(p.text.replace('\n', '') for p in pages)
        for paragraph in paragraphs:
            self.assertIn(paragraph, combined)
        body = [(value, size) for value, size in drawn if '角色资料' in value]
        self.assertTrue(body)
        self.assertEqual({size for _, size in body}, {28})

    def test_one_unbroken_paragraph_is_wrapped_completely(self):
        value = ''.join(f'编号{i:03d}的内容。' for i in range(400))
        with patch.object(readable_pages, 'MAX_IMAGE_EDGE', 1800):
            pages = info_layout.render_info(self.answer(InfoView('角色详情', rows=(InfoRow('介绍', (value,)),))))
        combined = ''.join(p.text.replace('\n', '').replace('角色详情', '').replace('介绍', '') for p in pages)
        self.assertEqual(combined, value)

    def test_art_loader_is_injected_cached_and_owned(self):
        calls, assets = [], []
        def load(url, size):
            calls.append((url, size))
            image = Image.new('RGB', size, '#8174b7')
            assets.append(image)
            return image
        row = InfoRow('同一头像', image_url='synthetic:face')
        view = InfoView('角色目录', rows=(row, row))
        with patch('socket.create_connection', side_effect=AssertionError('network forbidden')):
            pages = info_layout.render_info(self.answer(view), load=load)
        self.assertEqual(len(pages), 1)
        self.assertEqual(calls, [('synthetic:face', (152, 152))])
        with self.assertRaises(ValueError):
            assets[0].getpixel((0, 0))

    def test_missing_art_keeps_a_placeholder_and_never_calls_loader_with_empty_url(self):
        values = []
        original = info_layout.Canvas.text
        def capture(canvas, x, cy, value, *args, **kwargs):
            values.append(str(value))
            return original(canvas, x, cy, value, *args, **kwargs)
        with patch.object(info_layout.Canvas, 'text', capture):
            pages = info_layout.render_info(self.answer(InfoView('角色目录', rows=(InfoRow('角色一'),))),
                load=lambda *args: self.fail('missing URL must not be loaded'))
        self.assertEqual(len(pages), 1)
        self.assertIn('暂无图片', values)

    def test_loader_failure_closes_previously_loaded_art_and_propagates(self):
        images = []
        def load(url, size):
            if url == 'synthetic:broken':
                raise OSError('synthetic failure')
            value = Image.new('RGB', size)
            images.append(value)
            return value
        view = InfoView('目录', rows=(InfoRow('正常', image_url='synthetic:ok'),
            InfoRow('失败', image_url='synthetic:broken')))
        with self.assertRaisesRegex(OSError, 'synthetic failure'):
            info_layout.render_info(self.answer(view), load=load)
        self.assertEqual(len(images), 1)
        with self.assertRaises(ValueError):
            images[0].getpixel((0, 0))

    def test_failed_answers_stay_text_only(self):
        self.assertEqual(info_layout.render_info(InfoAnswer('暂不可用', 'data_unavailable')), ())


if __name__ == '__main__':
    unittest.main()
