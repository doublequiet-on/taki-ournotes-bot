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
            card_face(c,card,20,20,90,120,lambda *a:None,level=50,awake=3)
            rarity.assert_not_called()
            self.assertEqual(badge.call_args.args[0],'CardRank3.png')
        c.image.close()
