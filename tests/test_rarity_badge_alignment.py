"""Visible rarity glyphs, including narrow R/SR, share the same inner corner."""
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageChops, ImageDraw

from ournotes_bot.rendering.haneoka_theme import BG, Canvas, game_assets
from ournotes_bot.rendering.catalog_layout import card_face


class RarityBadgeAlignmentTests(unittest.TestCase):
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

    def test_list_and_rank_faces_keep_distinct_rarity_and_awakening_semantics(self):
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
