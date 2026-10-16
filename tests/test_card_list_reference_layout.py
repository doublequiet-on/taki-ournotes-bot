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

    def test_multi_memory_groups_repeat_association_but_named_filter_limits_headings(self):
        card=self.memory();drawn=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            drawn.append((str(value),x));return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            _,records,ranges=layout.render_list([card],'MyGO!!!!!',load=lambda *a:None)
        self.assertEqual([c.id for c in records],[51,51])
        self.assertEqual(ranges,((0,2),))
        headings=[name for name,x in drawn if name.startswith('角色') and x==134]
        self.assertEqual(headings,['角色2','角色1'])
        self.assertTrue(any('1 张' in text for text,_ in drawn))
        # A canonical character filter also covers alias/spaced command labels.
        _,records,_=layout.render_list([card],'角色=alias SSR',characters=('角色1',),load=lambda *a:None)
        self.assertEqual([c.id for c in records],[51])

    def test_related_portraits_are_beside_art_and_stable_across_character_queries(self):
        card=self.memory();captures=[]
        def load(url,size):
            im=Image.new('RGB',size);im.info['source']=url;return im
        original=Canvas.paste
        def capture(canvas,im,x,y,w,h,*args,**kwargs):
            if im is not None and im.info.get('source','').startswith('face-') and w==44:
                captures.append((im.info['source'],x,y,w,h))
            return original(canvas,im,x,y,w,h,*args,**kwargs)
        for name in ('角色1','角色2'):
            captures.clear()
            with patch.object(Canvas,'paste',capture):
                layout.render_list([card],name,load=load)
            self.assertEqual(len(captures),2)
            self.assertTrue(all(x==260+288+20 for _,x,_,_,_ in captures))
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

    def test_band_filter_restricts_headings_without_removing_related_portraits(self):
        card=self.memory();people=card.catalog['people']+[dict(self.person(3,1),band=2)]
        card=replace(card,catalog={**card.catalog,'people':people,'band_ids':[1,2],'bands':['MyGO!!!!!','Ave Mujica']})
        headings=[];original=Canvas.text
        def capture(canvas,x,y,value,*args,**kwargs):
            if x==134 and str(value).startswith('角色'):headings.append(str(value))
            return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            _,records,_=layout.render_list([card],'乐队=MyGO',bands=('MyGO!!!!!',),load=lambda *a:None)
        self.assertEqual(headings,['角色2','角色1'])
        self.assertEqual([c.id for c in records],[51,51])
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
            if x==134 and str(value) in ('高松灯','千早爱音'):headings.append(str(value))
            return original(canvas,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):
            _,records,_=layout.render_list([card],request.query,characters=request.filters['character'],load=lambda *a:None)
        self.assertEqual(headings,['高松灯'])
        self.assertEqual([c.id for c in records],[51])
