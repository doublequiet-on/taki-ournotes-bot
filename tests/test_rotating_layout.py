"""Activity/recruitment output preserves facts, native art scale and complete groups."""
import io
import unittest
from unittest.mock import patch

from PIL import Image

from ournotes_bot.query.contracts.info import InfoAnswer, InfoRow, InfoSchedule, InfoView, info_text
from ournotes_bot.rendering import info_layout, readable_pages, rotating_layout
from ournotes_bot.rendering.haneoka_theme import Canvas, BG, frame


class RotatingLayoutTests(unittest.TestCase):
    def render(self,view,**kwargs):
        return info_layout.render_info(InfoAnswer(info_text((view,)),views=(view,)),**kwargs)

    def test_detail_uses_native_banner_beside_dates_and_keeps_diagnostics_in_text(self):
        schedule=InfoSchedule('卡池 ID 12','开放时间未确认','未确认','未确认')
        row=InfoRow('卡池 ID 12',('完整时间事实',),kind='schedule',schedule=schedule)
        view=InfoView('卡池',hero_url='synthetic:banner',rows=(row,),layout='detail',presentation='gacha',
                      footer=('资料来源 Haneoka','资料版本 r-test','缓存过期提示'),visual_footer=('资料来源 Haneoka','缓存过期提示'))
        calls=[];drawn=[];original=Canvas.text
        def load(url,size):calls.append((url,size));return Image.new('RGB',size,'red')
        def text(c,x,y,value,*args,**kw):drawn.append((str(value),x,y));return original(c,x,y,value,*args,**kw)
        with patch.object(Canvas,'text',text):pages=self.render(view,load=load)
        self.assertEqual(calls,[('synthetic:banner',(420,180))])
        self.assertIn('资料版本 r-test',pages[0].text)
        self.assertIn('完整时间事实',pages[0].text)
        self.assertNotIn('资料版本 r-test',[v for v,x,y in drawn])
        self.assertIn('缓存过期提示',[v for v,x,y in drawn])
        self.assertTrue(all(x>500 for v,x,y in drawn if v in ('开始','结束','未确认')))

    def test_member_and_support_groups_keep_ratios_roles_commands_and_rarity_anchor(self):
        rows=tuple(InfoRow(f'{kind} ID {i}',('角色名',),image_url=f'synthetic:{kind}:{i}',
                  command=f'/{kind} {i}',label='指定加成卡 · 卡牌奖励',rarity=rarity,kind='card',group=kind,
                  display_title=f'标题{i}',image_kind=shape)
                   for kind,shape in (('成员卡','square'),('留影卡','wide')) for i,rarity in enumerate((4,3,2)))
        requests=[];anchors=[];original=Canvas.rarity_badge
        def load(url,size):requests.append((url,size));return Image.new('RGB',size,'red')
        def badge(c,rarity,right,bottom,w,h):anchors.append((rarity,right,bottom));return original(c,rarity,right,bottom,w,h)
        with patch.object(Canvas,'rarity_badge',badge):
            pages=self.render(InfoView('活动',rows=rows,presentation='activity'),load=load)
        self.assertEqual(len(pages),1)
        self.assertEqual({size for url,size in requests if '成员卡' in url},{(144,192)})
        self.assertEqual({size for url,size in requests if '留影卡' in url},{(228,128)})
        self.assertEqual([r for r,x,y in anchors],[4,3,2,4,3,2])
        self.assertEqual(len({y for r,x,y in anchors}),2)
        for row in rows:
            self.assertEqual(pages[0].text.splitlines().count(row.command),1)
            self.assertIn(row.label,pages[0].text)

    def test_portrait_card_frame_has_no_square_gradient_letterbox(self):
        from PIL import ImageColor
        canvas=Canvas(1080,500)
        picture=Image.new('RGB',(384,512),'red')
        try:
            anchors=[]
            with patch.object(rotating_layout,'frame',wraps=frame) as border, \
                 patch.object(Canvas,'rarity_badge',side_effect=lambda rarity,right,bottom,*a:anchors.append((right,bottom))):
                for index,rarity in enumerate((4,3,2)):
                    x=54+index*164
                    row=InfoRow('成员卡',image_url='native:384x512',rarity=rarity,image_kind='portrait')
                    rotating_layout._card_art(canvas,row,x,62,lambda *a:picture)
                    self.assertEqual(canvas.image.getpixel((x+1,150)),(255,0,0))
                    self.assertEqual(canvas.image.getpixel((x+143,150)),(255,0,0))
                    self.assertEqual(canvas.image.getpixel((x+150,150)),ImageColor.getrgb(BG))
                self.assertEqual([call.args[:2] for call in border.call_args_list],[(144,192)]*3)
            self.assertEqual(anchors,[(193,249),(357,249),(521,249)])
            # Source ownership stays with the caller; fitting must not close it.
            self.assertEqual(picture.size,(384,512))
        finally:
            picture.close();canvas.image.close()

    def test_nonstandard_source_ratio_anchors_border_to_actual_fitted_art(self):
        canvas=Canvas(1080,500);picture=Image.new('RGB',(320,512),'red')
        try:
            row=InfoRow('成员卡',image_url='native:narrow',rarity=3,image_kind='portrait')
            with patch.object(rotating_layout,'frame',wraps=frame) as border,patch.object(Canvas,'rarity_badge') as badge:
                rotating_layout._card_art(canvas,row,54,62,lambda *a:picture)
            self.assertEqual(border.call_args.args[:2],(120,192))
            self.assertEqual(badge.call_args.args[1:3],(169,249))
        finally:
            picture.close();canvas.image.close()

    def test_relationship_labels_stay_in_text_instead_of_card_captions(self):
        row=InfoRow('成员卡',('琴平凪',),display_title='划破黎明',image_kind='portrait',
                    label='指定加成卡 · 卡牌奖励 · 活动展示卡',tags=('加成','奖励','展示'),group='成员卡')
        self.assertEqual(rotating_layout._captions(row),(('划破黎明',),('琴平凪',)))
        self.assertIn(row.label,rotating_layout._card_panels((row,),'activity')[0].text)

    def test_member_titles_use_wider_text_cells_and_avoid_a_lone_final_character(self):
        row=InfoRow('成员卡',display_title='微笑，隐于幽暗',image_kind='portrait')
        self.assertEqual(rotating_layout._captions(row)[0],('微笑，隐于幽暗',))
        for title in ('迎着光芒走向新的明天','微笑，隐于幽暗之中'):
            wrapped=rotating_layout._name_lines(title,176)
            self.assertEqual(''.join(wrapped),title)
            self.assertGreater(len(wrapped[-1]),1)
        people=rotating_layout._name_lines('Oblivionis / 丰川祥子',176,20,False)
        self.assertEqual(''.join(people),'Oblivionis / 丰川祥子')
        self.assertGreater(len(people[-1]),1)

    def test_card_titles_and_all_character_names_are_drawn_without_truncation(self):
        title='在光芒之下迎接全新的一天'
        people='琴平凪 / 伊泽枣 / 和泉朋花'
        for presentation in ('activity','gacha'):
            for kind,shape in (('成员卡','portrait'),('留影卡','wide')):
                with self.subTest(presentation=presentation,kind=kind):
                    cards=tuple(InfoRow(f'{kind} {i}',(people if i else '琴平凪',),
                        display_title=title if i else '划破黎明',command=f'/查卡 {i}',
                        image_kind=shape,kind='card',group=kind,tags=('加成','奖励','展示'))
                        for i in range(2))
                    entry=InfoRow('活动或卡池',kind='banner',command='/查活动 2',
                        schedule=InfoSchedule('ID 2','已结束','未确认','未确认'),children=cards)
                    for rows in (cards,(entry,)):
                        drawn=[];original=Canvas.text
                        def text(c,x,y,value,*args,**kw):
                            self.assertLess(y,c.height-12)
                            drawn.append((str(value),x,y))
                            return original(c,x,y,value,*args,**kw)
                        with patch.object(Canvas,'text',text):
                            self.render(InfoView('查询',rows=rows,presentation=presentation))
                        for index,card in enumerate(cards):
                            x=54+index*((228 if shape=='wide' else 176)+20)
                            values=[value for value,px,y in drawn if px==x]
                            # Rejoin actual drawn lines to check the complete source facts.
                            self.assertIn(card.display_title,''.join(values))
                            self.assertIn(card.body[0],''.join(values))
                            self.assertNotIn(card.command,values)
                        people_y=[y for value,x,y in drawn if value.startswith('琴平凪')]
                        self.assertEqual(len(set(people_y)),1)
                        self.assertNotIn('加成/奖励/展示',[value for value,x,y in drawn])

    def test_activity_and_gacha_images_hide_commands_but_keep_text_query_entries(self):
        card=InfoRow('成员卡',('琴平凪',),command='/查卡 65',display_title='划破黎明',
                     kind='card',group='成员卡',image_kind='portrait')
        entry=InfoRow('列表条目',command='/查活动 2',kind='banner',children=(card,),
                      schedule=InfoSchedule('ID 2','已结束','未确认','未确认'))
        song=InfoRow('Odd Dice',command='/查曲 100111',kind='song')
        prose=InfoRow('说明',('完整段落。'*120,),command='/查卡池 12')
        for presentation in ('activity','gacha'):
            drawn=[];original=Canvas.text
            def text(c,x,y,value,*args,**kw):
                drawn.append(str(value));return original(c,x,y,value,*args,**kw)
            with patch.object(readable_pages,'MAX_IMAGE_EDGE',1300),patch.object(Canvas,'text',text):
                pages=self.render(InfoView('查询',rows=(entry,song,prose),presentation=presentation))
            combined='\n'.join(p.text for p in pages)
            for command in (card.command,entry.command,song.command,prose.command):
                self.assertNotIn(command,drawn)
                self.assertEqual(combined.splitlines().count(command),1)

    def test_vertical_entries_keep_banner_and_cards_together_and_text_once(self):
        rows=tuple(InfoRow(f'活动{i}',command=f'/查活动 {i}',kind='banner',
                  schedule=InfoSchedule(f'活动 ID {i}','已结束','2026-10-01 15:00','2026-10-10 14:59'),
                  children=(InfoRow(f'卡牌{i}',command=f'/查卡 {i}',kind='card',group='成员卡',
                                    image_kind='portrait',display_title=f'卡牌{i}'),)) for i in range(4))
        with patch.object(readable_pages,'MAX_IMAGE_EDGE',1300):
            pages=self.render(InfoView('活动列表',rows=rows,presentation='activity'))
        self.assertGreater(len(pages),1)
        combined='\n'.join(p.text for p in pages)
        for row in rows:
            owners=[p for p in pages if row.command in p.text.splitlines()]
            self.assertEqual(len(owners),1)
            self.assertIn(row.children[0].command,owners[0].text)
            self.assertEqual(combined.splitlines().count(row.children[0].command),1)

    def test_history_grid_paginates_complete_entries_without_clipping_commands(self):
        rows=tuple(InfoRow(f'很长的历史卡池名称 {i}',command=f'/查卡池 {i}',kind='banner',
                  schedule=InfoSchedule(f'卡池 ID {i}','已结束','2026-10-01 15:00','2026-10-10 14:59')) for i in range(15))
        drawn=[];original=Canvas.text
        def text(c,x,y,value,*args,**kw):
            self.assertLess(y,c.height-12)
            drawn.append(str(value));return original(c,x,y,value,*args,**kw)
        with patch.object(readable_pages,'MAX_IMAGE_EDGE',1300),patch.object(Canvas,'text',text):
            pages=self.render(InfoView('历史卡池',rows=rows,presentation='gacha'))
        self.assertGreater(len(pages),1)
        combined='\n'.join(p.text for p in pages)
        for row in rows:self.assertEqual(combined.splitlines().count(row.command),1)
        for page in pages:
            with Image.open(io.BytesIO(page.image)) as image:self.assertEqual(image.width,1080)

    def test_missing_banner_does_not_reserve_an_empty_art_column(self):
        row=InfoRow('活动 ID 1',kind='schedule',schedule=InfoSchedule('活动 ID 1','尚未开始','未确认','未确认'))
        drawn=[];original=Canvas.text
        def text(c,x,y,value,*a,**kw):drawn.append((str(value),x));return original(c,x,y,value,*a,**kw)
        with patch.object(Canvas,'text',text):self.render(InfoView('活动',rows=(row,),presentation='activity'))
        self.assertIn(('开始',54),drawn)
        self.assertNotIn('暂无图片',[v for v,x in drawn])


if __name__=='__main__':unittest.main()
