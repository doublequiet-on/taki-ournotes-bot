"""Visible rarity glyphs, including narrow R/SR, share the same inner corner."""
import io
import unittest
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from ournotes_bot.rendering.haneoka_theme import BG, Canvas, game_assets
from ournotes_bot.rendering.catalog_layout import card_face


class RarityBadgeAlignmentTests(unittest.TestCase):
    def test_badge_asset_states_preserve_mastery_snap_and_rarity_labels(self):
        source=Image.new('RGBA',(24,22),'red')
        raw=io.BytesIO();source.save(raw,format='PNG');source.close()
        cases=[(f'CardRank{rank}.png',f'专{rank}') for rank in range(6)]
        cases += [(f'snaplimit_{rank}.png',f'觉{rank}') for rank in range(6)]
        cases += [(f'RarityIconCenter_{name}.png',name) for name in ('R','SR','SSR','BD','EX')]
        for state,data in (('present',raw.getvalue()),('missing',None),('corrupt',b'not an image')):
            for name,label in cases:
                with self.subTest(state=state,name=name):
                    c=Canvas(100,80)
                    with game_assets(lambda requested:data),patch.object(c,'text',wraps=c.text) as text:
                        c.badge(name,20,40,24,22)
                    if state=='present':
                        text.assert_not_called()
                        self.assertEqual(c.image.getpixel((20,29)),(255,0,0))
                    else:
                        self.assertEqual([call.args[2] for call in text.call_args_list],[label])
                    c.image.close()

    def test_rarity_corner_keeps_its_label_when_image_cannot_be_decoded(self):
        for rarity,label in ((2,'R'),(3,'SR'),(4,'SSR'),(10,'EX'),(20,'BD')):
            with self.subTest(rarity=rarity):
                c=Canvas(220,150)
                with game_assets(lambda name:b'not an image'),patch.object(c,'text',wraps=c.text) as text:
                    c.rarity_badge(rarity,200,130,80,58)
                self.assertEqual(text.call_args.args[2],label)
                self.assertEqual(text.call_args.args[-1],'right')
                c.image.close()

    def test_asset_fetch_failure_uses_the_same_semantic_fallback(self):
        from ournotes_bot.sources.haneoka import game_assets as source
        from ournotes_bot.sources.errors import ChartDataError
        with TemporaryDirectory() as cache,patch.object(source,'_MEMORY',{}),patch.object(source,'_FAILED',{}), \
             patch.object(source,'_fetch',side_effect=ChartDataError('fixture load failure')) as fetch:
            c=Canvas(220,150)
            with game_assets(lambda name:source.asset_bytes(name,cache)),patch.object(c,'text') as text:
                c.badge('CardRank2.png',20,40,24,22)
                c.badge('snaplimit_4.png',60,40,24,22)
                c.rarity_badge(4,200,130,80,58)
            self.assertEqual([call.args[2] for call in text.call_args_list],['专2','觉4','SSR'])
            self.assertEqual(fetch.call_count,3)
            c.image.close()

    def test_ranking_render_reaches_badges_for_favorite_and_both_deck_types(self):
        from ournotes_bot.data import SupportCard
        from ournotes_bot.rendering import ranking_layout
        from ournotes_bot.query.event_cutoff_query import CutoffAnswer, CutoffRequest
        from ournotes_bot.sources.moenotes_events import EventSnapshot, EventSong, BoardSnapshot, display_time
        member=SimpleNamespace(id=1,rarity=4,full_url='',thumbnail_url='',catalog={})
        support=SupportCard(2,'fixture','fixture',(),3,1,0,0,0,'','','')
        song=EventSong('7','101','fixture')
        event=EventSnapshot('jp','3','fixture','collecting',1792274400000,1792964940000,(song,))
        # Favorite mastery, member mastery, member training and support rank differ.
        detail={'favoriteMemberCardId':1,'favoriteMemberRank':2,'favoriteMemberAwakeCount':1,
                'cards':[{'memberCardId':1,'memberRank':5,'memberAwakeCount':3,
                          'supportCardId':2,'supportRank':4}]}
        board=BoardSnapshot(song,scores=(123,),player_details=(detail,))
        source=Image.new('RGBA',(24,22),'red')
        raw=io.BytesIO();source.save(raw,format='PNG');source.close()
        original_badge,original_text=Canvas.badge,Canvas.text
        for mode in ('discrete','trend'):
            for state,data in (('present',raw.getvalue()),('missing',None),('corrupt',b'not an image')):
                with self.subTest(mode=mode,state=state):
                    names=[];labels=[]
                    def badge(c,name,*args):
                        names.append(name)
                        return original_badge(c,name,*args)
                    def text(c,x,y,value,*args,**kwargs):
                        labels.append(value)
                        return original_text(c,x,y,value,*args,**kwargs)
                    answer=CutoffAnswer(CutoffRequest(ranks=(1,),mode=mode),event,(board,))
                    with game_assets(lambda name:data),patch.object(Canvas,'badge',badge),patch.object(Canvas,'text',text):
                        pages=ranking_layout.render(answer,assets={},load=lambda *a:None,display_time=display_time,
                                                    server_label='日服',timezone_label='UTC+09:00',
                                                    catalogs=({1:member},{2:support}))
                    self.assertTrue(pages)
                    self.assertEqual(names,['CardRank2.png']+(['CardRank5.png','snaplimit_4.png'] if mode=='trend' else []))
                    expected=[] if state=='present' else ['专2']+(['专5','觉4'] if mode=='trend' else [])
                    self.assertEqual([value for value in labels if value.startswith(('专','觉'))],expected)

    def test_each_rarity_anchors_its_cropped_actual_size_not_a_shared_left_edge(self):
        # Different aspect ratios and transparent source margins reproduce R/SR drift.
        for rarity, width in ((2,18),(3,42),(4,68),(10,48),(20,60)):
            with self.subTest(rarity=rarity):
                source=Image.new('RGBA',(100,70))
                ImageDraw.Draw(source).rectangle((7,11,7+width,55),fill='red')
                raw=io.BytesIO();source.save(raw,format='PNG')
                c=Canvas(220,150)
                with game_assets(lambda name:raw.getvalue()):
                    c.rarity_badge(rarity,200,130,80,58)
                diff=ImageChops.difference(c.image,Image.new('RGB',c.image.size,BG))
                box=diff.getbbox()
                self.assertIsNotNone(box)
                self.assertEqual(box[2:],(200,130))
                self.assertGreaterEqual(box[0],120)
                self.assertGreaterEqual(box[1],72)
                c.image.close();source.close();diff.close()

    def test_missing_asset_text_is_right_aligned_and_unknown_rarity_is_omitted(self):
        c=Canvas(220,150)
        with patch.object(c,'text') as text:
            c.rarity_badge(2,200,130,80,58)
            self.assertEqual(text.call_args.args[0],200)
            self.assertEqual(text.call_args.args[-1],'right')
            text.reset_mock();c.rarity_badge(999,200,130,80,58)
            text.assert_not_called()
        c.image.close()

    def test_list_and_rank_faces_keep_distinct_rarity_and_mastery_semantics(self):
        card=SimpleNamespace(rarity=2,full_url='',thumbnail_url='',catalog={})
        c=Canvas(300,300)
        with patch.object(c,'rarity_badge') as rarity,patch.object(c,'badge') as badge:
            card_face(c,card,20,20,192,192,lambda *a:None)
            self.assertEqual(rarity.call_args.args,(2,206,206,80,58))
            badge.assert_not_called()
            rarity.reset_mock()
            card_face(c,card,20,20,90,120,lambda *a:None,level=50,rank=3)
            rarity.assert_not_called()
            self.assertEqual(badge.call_args.args[0],'CardRank3.png')
        c.image.close()

    def test_unknown_avatar_growth_does_not_turn_into_rarity_or_zero_rank(self):
        card=SimpleNamespace(rarity=4,full_url='',thumbnail_url='',catalog={})
        c=Canvas(180,180)
        with patch.object(c,'rarity_badge') as rarity,patch.object(c,'badge') as badge,patch.object(c,'text') as text:
            card_face(c,card,20,20,76,76,lambda *a:None,growth=True)
            rarity.assert_not_called()
            badge.assert_not_called()
            self.assertIn('Lv.—',[call.args[2] for call in text.call_args_list])
            card_face(c,card,20,20,76,76,lambda *a:None,level=1,rank=0,growth=True)
            self.assertEqual(badge.call_args.args[0],'CardRank0.png')
        c.image.close()

    def test_ranking_avatar_uses_own_exp_and_rank_not_high_score_deck(self):
        from ournotes_bot.rendering import ranking_layout
        from ournotes_bot.query.event_cutoff_query import CutoffAnswer, CutoffRequest
        from ournotes_bot.sources.moenotes_events import EventSnapshot, EventSong, BoardSnapshot, display_time
        card=SimpleNamespace(id=1,rarity=4,full_url='',thumbnail_url='',catalog={
            'level_curve':[{'level':1,'exp':0},{'level':50,'exp':100}],
            'level_limits':[{'_awakeCount':5,'_limitLevel':100}]})
        song=EventSong('7','101','fixture')
        event=EventSnapshot('jp','3','fixture','collecting',1792274400000,1792964940000,(song,))
        for favorite in ({'favoriteMemberExp':100,'favoriteMemberAwakeCount':5,'favoriteMemberRank':2},{}):
            detail={'favoriteMemberCardId':1,'cards':[{'memberCardId':1,'memberExp':0,'memberAwakeCount':1,'memberRank':5}],**favorite}
            board=BoardSnapshot(song,scores=(123,),player_details=(detail,))
            answer=CutoffAnswer(CutoffRequest(ranks=(1,)),event,(board,))
            with patch.object(ranking_layout,'card_face') as face:
                pages=ranking_layout.render(answer,assets={},load=lambda *a:None,display_time=display_time,
                                            server_label='日服',timezone_label='UTC+09:00',catalogs=({1:card},{}))
                self.assertTrue(pages)
                self.assertEqual(face.call_args.kwargs,{'level':50 if favorite else None,
                                                       'rank':2 if favorite else None,'growth':True})
