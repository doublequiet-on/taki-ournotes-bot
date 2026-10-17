"""Geometry and grouping from the original approved member V3 / SNAP V4 art."""
import io
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from ournotes_bot.data import Card, SupportCard, Skill
from ournotes_bot.rendering import catalog_layout as layout
from ournotes_bot.rendering.haneoka_theme import Canvas
from ournotes_bot.query.card_catalog import parse_card_request


class ReferenceLayoutTests(unittest.TestCase):
    def person(self, ident, order):
        return {'id':ident,'band':1,'order':order,'name':{'zh':f'角色{ident}'},'face':f'face-{ident}'}

    def member(self):
        return Card(1,1,'卡','角色1','MyGO!!!!!',4,1,1,1,1,'','','','',
                    catalog={'people':[self.person(1,1)],'detail_loaded':True})

    def memory(self):
        people=[self.person(1,2),self.person(2,1)]
        return SupportCard(51,'多人卡','角色1',('角色1',),4,1,1,1,1,'','','',
                           catalog={'people':people,'character_ids':[1,2],'detail_loaded':True})

    def test_attribute_rows_left_pack_and_wrap_after_eight_without_empty_slots(self):
        base=self.member()
        cards=[replace(base,id=i,rarity=2 if i%2 else 4) for i in range(1,10)]
        cards+=[replace(base,id=10,card_type=5,rarity=3)]
        placements=[];original=layout.card_face
        def capture(canvas,card,x,y,*args,**kwargs):
            placements.append((card,x,y));return original(canvas,card,x,y,*args,**kwargs)
        with patch.object(layout,'card_face',capture):
            parts,records,ranges=layout.render_list(cards,'测试',load=lambda *a:None)
        self.assertEqual(len(parts),1)
        self.assertEqual(ranges,((0,10),))
        self.assertEqual([c.id for c in records],[2,4,6,8,1,3,5,7,9,10])
        rows={y:[(c,x) for c,x,cy in placements if cy==y] for _,_,y in placements}
        self.assertEqual([len(row) for row in rows.values()],[8,1,1])
        self.assertTrue(all(len({c.card_type for c,_ in row})==1 for row in rows.values()))
        self.assertTrue(all(row[0][1]==264 for row in rows.values()))
        with Image.open(io.BytesIO(parts[0].image)) as im:self.assertEqual(im.width,2360)

    def test_multi_memory_has_one_complete_group_even_with_character_filter(self):
        card=self.memory();drawn=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            drawn.append((str(value),x));return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            _,records,ranges=layout.render_list([card],'MyGO!!!!!',load=lambda *a:None)
        self.assertEqual([c.id for c in records],[51])
        self.assertEqual(ranges,((0,1),))
        headings=[name for name,x in drawn if name.startswith('角色') and x<260]
        self.assertEqual(headings,['角色1','角色2'])
        self.assertTrue(any('1 张' in text for text,_ in drawn))
        # A canonical character filter also covers alias/spaced command labels.
        _,records,_=layout.render_list([card],'角色=alias SSR',characters=('角色1',),load=lambda *a:None)
        self.assertEqual([c.id for c in records],[51])

    def test_related_portraits_are_left_and_stable_across_character_queries(self):
        card=self.memory();captures=[]
        def load(url,size):
            im=Image.new('RGB',size);im.info['source']=url;return im
        original=Canvas.paste
        def capture(canvas,im,x,y,w,h,*args,**kwargs):
            if im is not None and im.info.get('source','').startswith('face-') and w in (56,80,96):
                captures.append((im.info['source'],x,y,w,h))
            return original(canvas,im,x,y,w,h,*args,**kwargs)
        for name in ('角色1','角色2'):
            captures.clear()
            with patch.object(Canvas,'paste',capture):
                layout.render_list([card],name,load=load)
            self.assertEqual(len(captures),2)
            self.assertTrue(all(x+w<260 for _,x,_,w,_ in captures))
            if name=='角色1':first=tuple(captures)
            else:self.assertEqual(tuple(captures),first)

    def test_narrow_real_member_list_does_not_keep_four_empty_columns(self):
        base=self.member()
        cards=[replace(base,id=i,card_type=i) for i in range(1,4)]
        parts,_,_=layout.render_list(cards,'MyGO!!!!!',load=lambda *a:None)
        self.assertEqual(len(parts),1)
        with Image.open(io.BytesIO(parts[0].image)) as im:
            self.assertEqual(im.width,568)
            self.assertGreater(im.height,1000)

    def test_memory_conditions_and_two_support_labels_match_approved_compact_copy(self):
        value={'family':'演出延时','short':'延时+2.5→5秒','enhanced_target':'MyGO!!!!!'}
        self.assertEqual(layout.memory_summary(value),'延时2.5秒\nMyGO：5秒')
        value={'family':'PERFECT转JUST','short':'每40→20P：下个P转J×5','enhanced_target':'MyGO!!!!!'}
        self.assertEqual(layout.memory_summary(value),'每40P转J×5\nMyGO：每20P')
        card=replace(self.memory(),skills=(Skill('supportSkill','一','延时3秒'),Skill('supportSkill','二','回血550')))
        self.assertEqual([(label,text) for label,_,text in layout.summaries(card)],[('支','延时3秒'),('支','回血550')])

    def test_member_numeric_range_is_not_split_into_partial_numbers(self):
        from ournotes_bot.rendering.haneoka_theme import lines
        self.assertEqual(lines('+120→140%',196,32),('+120→140%',))

    def test_narrow_titles_and_all_text_remain_inside_canvas_in_three_locales(self):
        from ournotes_bot.rendering.haneoka_theme import font
        original=Canvas.text
        def checked(canvas,x,y,value,size=28,color=None,bold=False,align='left',**kwargs):
            width=font(size,bold,str(value).isascii()).getlength(str(value))
            left=x-width if align=='right' else x-width/2 if align=='center' else x
            self.assertGreaterEqual(left,-1,(value,canvas.width))
            self.assertLessEqual(left+width,canvas.width+1,(value,canvas.width))
            if color is None:return original(canvas,x,y,value,size,bold=bold,align=align,**kwargs)
            return original(canvas,x,y,value,size,color,bold,align,**kwargs)
        with patch.object(Canvas,'text',checked):
            for locale in ('zh','en','ja'):
                layout.render_list([self.member()],'MyGO!!!!!',locale,load=lambda *a:None)

    def test_band_filter_preserves_complete_character_combination(self):
        card=self.memory();people=card.catalog['people']+[dict(self.person(3,1),band=2)]
        card=replace(card,catalog={**card.catalog,'people':people,'band_ids':[1,2],'bands':['MyGO!!!!!','Ave Mujica']})
        headings=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            if x<260 and str(value).startswith('角色'):headings.append(str(value))
            return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            _,records,_=layout.render_list([card],'乐队=MyGO',bands=('MyGO!!!!!',),load=lambda *a:None)
        self.assertEqual(headings,['角色1','角色2','角色3'])
        self.assertEqual([c.id for c in records],[51])
        self.assertEqual([p['id'] for p in records[0].catalog['people']],[1,2,3])
        _,records,_=layout.render_list([card],'角色=角色1 乐队=Ave Mujica',characters=('角色1',),bands=('Ave Mujica',),load=lambda *a:None)
        self.assertEqual([c.id for c in records],[51])

    def test_actual_nickname_filter_uses_the_same_character_identity_as_query(self):
        people=[dict(self.person(1,2),name={'zh':'高松灯'}),dict(self.person(2,1),name={'zh':'千早爱音'})]
        card=replace(self.memory(),character='高松灯',characters=('高松灯','千早爱音'),catalog={**self.memory().catalog,'people':people})
        repo=SimpleNamespace(cards=[],support_cards=[card])
        request=parse_card_request('角色=tmr',repo,support=True)
        headings=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            if x<260 and str(value) in ('高松灯','千早爱音'):headings.append(str(value))
            return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            _,records,_=layout.render_list([card],request.query,characters=request.filters['character'],load=lambda *a:None)
        self.assertEqual(headings,['高松灯','千早爱音'])
        self.assertEqual([c.id for c in records],[51])


    def test_same_combination_groups_once_regardless_of_source_order(self):
        a=self.memory()
        b=replace(a,id=52,catalog={**a.catalog,'people':list(reversed(a.catalog['people']))})
        solo=replace(a,id=53,catalog={**a.catalog,'people':[self.person(1,2)]})
        positions=[]
        original=layout.card_face
        def capture(canvas,card,x,y,*args,**kwargs):
            positions.append((card.id,x,y));return original(canvas,card,x,y,*args,**kwargs)
        with patch.object(layout,'card_face',capture):
            _,records,ranges=layout.render_list([solo,b,a],'角色1',load=lambda *a:None)
        self.assertEqual([c.id for c in records],[51,52,53])
        self.assertEqual(ranges,((0,3),))
        self.assertEqual(positions[0][2],positions[1][2])
        self.assertGreater(positions[2][2],positions[1][2])

    def test_missing_catalog_preserves_all_captured_names(self):
        card=replace(self.memory(),catalog={},characters=('乙','甲'))
        drawn=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            if x<260:drawn.append(str(value))
            return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            _,records,_=layout.render_list([card],'乙',load=lambda *a:None)
        self.assertEqual([c.id for c in records],[51])
        self.assertIn('甲',drawn);self.assertIn('乙',drawn)

    def test_complete_group_repeats_on_split_pages_without_overflow(self):
        from ournotes_bot.rendering import readable_pages
        people=[dict(self.person(i,i),name={'zh':f'长角色名字{i}'}) for i in range(1,6)]
        base=replace(self.memory(),catalog={'people':people,'detail_loaded':True})
        cards=[replace(base,id=i,card_type=i) for i in range(1,4)]
        original=Canvas.text;names={}
        def capture(canvas,x,y,value,size=28,*args,**kwargs):
            self.assertLess(y,canvas.height)
            if x<260:names.setdefault(canvas,[]).append(str(value))
            return original(canvas,x,y,value,size,*args,**kwargs)
        with patch.object(readable_pages,'MAX_IMAGE_PIXELS',400_000),patch.object(Canvas,'text',capture):
            parts,records,ranges=layout.render_list(cards,'测试',load=lambda *a:None)
        self.assertEqual(len(parts),3);self.assertEqual(ranges,((0,1),(1,2),(2,3)))
        self.assertEqual(len(names),3)
        for drawn in names.values():
            for i in range(1,6):self.assertIn(f'长角色名字{i}',''.join(drawn))

    def test_memory_groups_merge_identity_cells_with_colour_rows_and_rarity_columns(self):
        base=self.memory()
        first=dict(self.person(1,9),name={'zh':'单人甲'})
        second=dict(self.person(2,1),name={'zh':'单人乙'})
        solo=replace(base,id=1,rarity=2,catalog={'people':[first],'detail_loaded':True})
        cards=[solo,replace(solo,id=2,rarity=4),replace(solo,id=3,card_type=2),
               replace(solo,id=4,catalog={'people':[second],'detail_loaded':True}),
               replace(base,id=5,catalog={'people':[first,second],'detail_loaded':True}),
               replace(base,id=6,card_type=5,catalog={'people':[second,first],'detail_loaded':True})]
        placements=[];names=[];separators=[]
        face=layout.card_face;text=Canvas.text;line=Canvas.line
        def capture_face(canvas,card,x,y,*args,**kwargs):
            placements.append((card.id,x,y));return face(canvas,card,x,y,*args,**kwargs)
        def capture_text(canvas,x,y,value,*args,**kwargs):
            if x<260 and str(value) in ('单人甲','单人乙'):names.append(str(value))
            return text(canvas,x,y,value,*args,**kwargs)
        def capture_line(canvas,x,y,right,*args,**kwargs):
            separators.append((x,y,right));return line(canvas,x,y,right,*args,**kwargs)
        with patch.object(layout,'card_face',capture_face),patch.object(Canvas,'text',capture_text),patch.object(Canvas,'line',capture_line):
            parts,records,ranges=layout.render_list(cards,'测试',load=lambda *a:None)
        self.assertEqual([c.id for c in records],[5,6,2,1,3,4])
        self.assertEqual(ranges,((0,6),))
        self.assertEqual(len(parts),1)
        positions={ident:(x,y) for ident,x,y in placements}
        self.assertEqual(positions[2][1],positions[1][1])
        self.assertLess(positions[2][0],positions[1][0])
        self.assertGreater(positions[3][1],positions[1][1])
        self.assertEqual(names,['单人甲','单人乙','单人甲','单人乙'])
        self.assertEqual(len(separators),3)  # Two group boundaries and the footer only.
        self.assertTrue(all(x==32 for x,_,_ in separators))

    def test_compact_memory_names_titles_and_skills_fit_three_locales(self):
        from ournotes_bot.rendering.haneoka_theme import font
        person=dict(self.person(1,1),name={locale:'Long character name / 很长的完整人物名字' for locale in ('zh','en','ja')})
        card=replace(self.memory(),catalog={'people':[person,self.person(2,2)],'detail_loaded':False,'detail_stale':True},
                     skills=(Skill('supportSkill','技能','完整技能描述 '+('long skill text '*12)),))
        original=Canvas.text
        def checked(canvas,x,y,value,size=28,color=None,bold=False,align='left',**kwargs):
            width=font(size,bold,str(value).isascii()).getlength(str(value))
            left=x-width if align=='right' else x-width/2 if align=='center' else x
            self.assertGreaterEqual(left,-1,(value,canvas.width))
            self.assertLessEqual(left+width,canvas.width+1,(value,canvas.width))
            self.assertGreaterEqual(y-size/2,0)
            self.assertLessEqual(y+size/2,canvas.height)
            return original(canvas,x,y,value,size,color or '#1b1b1f',bold,align,**kwargs)
        with patch.object(Canvas,'text',checked):
            for locale in ('zh','en','ja'):
                layout.render_list([card],'很长的查询条件 '*10,locale,load=lambda *a:None)
