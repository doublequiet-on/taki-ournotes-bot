"""Information images retain full text, complete rows and the established font scale."""
import io
import unittest
from unittest.mock import patch

from PIL import Image

from ournotes_bot.query.contracts.info import InfoAnswer, InfoRow, InfoView, info_text
from ournotes_bot.rendering import info_layout, readable_pages
from ournotes_bot.rendering.image_output import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS


class InformationLayoutTests(unittest.TestCase):
    def test_roster_keeps_band_headers_and_names_but_commands_only_in_text(self):
        rows=tuple(InfoRow(f'角色{i}',(f'角色 ID {i}',),image_url=f'synthetic:{i}',
                          command=f'/查角色 {i}',group=f'乐队{i//5}') for i in range(25))
        view=InfoView('角色目录',rows=rows,layout='directory')
        drawn=[];original=info_layout.Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            drawn.append(str(value));return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(info_layout.Canvas,'text',capture):pages=info_layout.render_info(self.answer(view))
        self.assertEqual(len(pages),1)
        with Image.open(io.BytesIO(pages[0].image)) as im:self.assertLess(im.height,3000)
        for group in range(5):self.assertEqual(drawn.count(f'乐队{group}'),1)
        for row in rows:
            self.assertEqual(pages[0].text.splitlines().count(row.command),1)
            self.assertNotIn(row.command,drawn)
            self.assertIn(row.title,drawn)
        with patch.object(readable_pages,'MAX_IMAGE_EDGE',1200):parts=info_layout.render_info(self.answer(view))
        self.assertGreater(len(parts),1)
        for row in rows:self.assertEqual(sum(p.text.splitlines().count(row.command) for p in parts),1)

    def test_profile_places_portrait_beside_facts_and_prose_below(self):
        rows=tuple(InfoRow(f'字段{i}',(f'资料{i}',),kind='fact') for i in range(10))+(InfoRow('简介',('完整介绍。',),kind='prose'),)
        view=InfoView('角色详情',hero_url='synthetic:portrait',rows=rows,layout='character')
        calls=[]
        def load(url,size):calls.append((url,size));return Image.new('RGBA',(300,900),'red')
        pages=info_layout.render_info(self.answer(view),load=load)
        self.assertEqual(calls,[('synthetic:portrait',(330,860))])
        with Image.open(io.BytesIO(pages[0].image)) as im:self.assertLess(im.height,1600)
        self.assertIn('完整介绍。',pages[0].text)
        for row in rows:self.assertEqual(pages[0].text.splitlines().count(row.title),1)

    def test_profile_hides_card_links_and_prose_commands_without_losing_text(self):
        rows=(InfoRow('成员卡',('3张',),kind='links',command='/查卡 高松灯'),
              InfoRow('留影卡',('5张',),kind='links',command='/查留影卡 高松灯'),
              InfoRow('角色介绍',('完整资料。'*180,),kind='prose',command='/查角色 1'))
        drawn=[];original=info_layout.Canvas.text
        def capture(canvas,x,y,value,*args,**kw):
            drawn.append(str(value));return original(canvas,x,y,value,*args,**kw)
        with patch.object(readable_pages,'MAX_IMAGE_EDGE',1300),patch.object(info_layout.Canvas,'text',capture):
            pages=info_layout.render_info(self.answer(InfoView('高松灯',rows=rows,layout='character')))
        self.assertGreater(len(pages),1)
        combined='\n'.join(p.text for p in pages)
        for row in rows:
            self.assertNotIn(row.command,drawn)
            self.assertEqual(combined.splitlines().count(row.command),1)
            self.assertIn(row.title,drawn)
        self.assertIn('3张',drawn)
        self.assertIn('5张',drawn)

    def test_character_candidates_hide_commands_and_keep_names_and_ids(self):
        rows=tuple(InfoRow(f'候选角色{i}',(f'角色 ID {i}',),command=f'/查角色 {i}') for i in range(3))
        drawn=[];original=info_layout.Canvas.text
        def capture(canvas,x,y,value,*args,**kw):
            drawn.append(str(value));return original(canvas,x,y,value,*args,**kw)
        with patch.object(info_layout.Canvas,'text',capture):
            pages=info_layout.render_info(self.answer(InfoView('角色候选',rows=rows,layout='list',presentation='character')))
        for row in rows:
            self.assertIn(row.title,drawn)
            self.assertIn(row.body[0],drawn)
            self.assertNotIn(row.command,drawn)
            self.assertIn(row.command,pages[0].text)

    def test_profile_without_source_art_uses_full_width_instead_of_an_empty_column(self):
        view=InfoView('角色',rows=(InfoRow('基本资料',('角色 ID 1',),kind='fact'),),layout='character')
        pages=info_layout.render_info(self.answer(view))
        with Image.open(io.BytesIO(pages[0].image)) as im:self.assertLess(im.height,500)
        self.assertIn('角色 ID 1',pages[0].text)

    def test_support_art_preserves_landscape_ratio_and_native_rarity_mark(self):
        from ournotes_bot.rendering.haneoka_theme import game_assets
        row=InfoRow('留影卡',image_url='synthetic:wide',image_kind='wide',rarity=4)
        names=[]
        def game(name):names.append(name);return None
        calls=[]
        def load(url,size):calls.append((url,size));return Image.new('RGBA',size,'red')
        with game_assets(game):info_layout.render_info(self.answer(InfoView('卡池',rows=(row,),layout='detail')),load=load)
        self.assertIn(('synthetic:wide',(290,164)),calls)
        self.assertIn('RarityIconCenter_SSR.png',names)

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
