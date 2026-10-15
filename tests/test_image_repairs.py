"""Actual-output regressions: artwork routing, native pixels and preparation integrity."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from ournotes_bot.commands import CommandResult
from ournotes_bot.platforms.qq.qq import _render_prepared
from ournotes_bot.query.card_catalog import CardAnswer, CardRequest
from ournotes_bot.rendering import card_visuals
from ournotes_bot.rendering.song_detail_layout import judged_times
from ournotes_bot.sources.haneoka import prepare_catalog_assets as preparation
from test_pr60_regressions import ReviewRegressionTests


class ArtRoutingTests(unittest.TestCase):
    setUp = ReviewRegressionTests.setUp
    def test_numeric_art_route_never_calls_detail_for_either_card_type(self):
        for card, support in ((self.repo.cards[0],False),(self.repo.support_cards[0],True)):
            answer=CardAnswer(CardRequest(support=support,mode='art',card_id=card.id), (card,))
            result=CommandResult(('support_cards' if support else 'cards',str(card.id),1),catalog=answer)
            with patch.object(card_visuals,'art',return_value=b'original artwork') as art, patch('ournotes_bot.rendering.card_detail_layout.render',side_effect=AssertionError('wrong detail route')):
                reply=_render_prepared(answer.text(),result,self.repo,'zh')
            art.assert_called_once_with(card)
            self.assertEqual(reply.image,b'original artwork')

    def test_native_art_preserves_source_pixels_instead_of_upscaling(self):
        with patch('ournotes_bot.visuals._asset',return_value=Image.new('RGBA',(1440,1920),'red')) as load:
            raw=card_visuals.art(self.repo.cards[0])
        self.assertTrue(load.call_args.kwargs['native'])
        with Image.open(io.BytesIO(raw)) as image:self.assertEqual(image.size,(1440,1920))


class LocalizedDurationTests(unittest.TestCase):
    def test_audited_whole_fever_duration_has_no_chinese_in_english_table(self):
        from dataclasses import replace
        from ournotes_bot.rendering import card_detail_layout
        from ournotes_bot.rendering.haneoka_theme import Canvas
        from test_pr60_regressions import ReviewRegressionTests
        fixture=ReviewRegressionTests();fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        card=fixture.repo.cards[0]
        values=['COMBO获取 · 开场2秒','COMBO获取 · 开场3秒','COMBO获取 · 开场4秒','COMBO获取 · 开场5秒','COMBO获取']
        card=replace(card,catalog={**card.catalog,'compact_skills':[{'levels':{str(i+1):v for i,v in enumerate(values)}}],
            'skill_levels':[[{'en':'Combo boost'} for _ in range(5)]]})
        drawn=[];original=Canvas.text
        def capture(c,x,y,value,*args,**kwargs):
            drawn.append(str(value));return original(c,x,y,value,*args,**kwargs)
        with patch.object(Canvas,'text',capture):card_detail_layout.render(card,'en',load=lambda *a:None)
        self.assertIn('Full fever',drawn)
        self.assertIn('2s',drawn)
        self.assertNotIn('整个激奏',drawn)
        self.assertFalse(any('秒' in value for value in drawn))


class JudgmentVectorTests(unittest.TestCase):
    def test_captured_guide_auto_and_merged_judgments_match_independent_reference(self):
        vectors=json.loads((Path(__file__).parent/'fixtures/chart_judgment_vectors.json').read_text('utf-8'))
        for case in vectors['cases']:
            with self.subTest(chart=case['name']):
                self.assertEqual([round(t*1000) for t in judged_times(case['score'])],case['judgments_ms'])


class PreparationIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.cache=Path(self.temp.name)
        raw=io.BytesIO();Image.new('RGB',(20,30),'red').save(raw,'PNG');self.raw=raw.getvalue()
        self.url='https://haneoka.org/assets/jp/Assets/AddressableResources/MemberCard/1/member_full.png?release=r-'+('a'*20)
        self.patch=patch.object(preparation,'catalog_urls',return_value=(self.url,));self.patch.start();self.addCleanup(self.patch.stop)
        self.saved={'identities':{'jp':{'releaseId':'r-'+('a'*20)}}}

    def test_preparation_is_reused_after_restart_and_repairs_corrupt_image(self):
        calls=[]
        def fetch(url,**kwargs):calls.append((url,kwargs));return self.raw
        first=preparation.prepare(self.saved,self.cache,0,download=fetch)
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][1],{'timeout':45})
        self.assertEqual(preparation.prepare(self.saved,self.cache,0,download=lambda *a,**k:self.fail('network on restart')),first)
        path=self.cache/first['assets'][self.url]['file'];path.write_bytes(b'corrupt')
        preparation.prepare(self.saved,self.cache,0,download=fetch)
        self.assertEqual(len(calls),2)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),first['assets'][self.url]['sha256'])

    def test_failed_fetch_does_not_publish_a_complete_manifest(self):
        calls=[]
        def fail(*a,**k):calls.append(1);raise OSError('network failure')
        with self.assertRaises(OSError):preparation.prepare(self.saved,self.cache,0,download=fail)
        self.assertEqual(len(calls),2)
        self.assertFalse((self.cache/'prepared-artwork-v1.json').exists())
        self.assertFalse(list(self.cache.glob('*.tmp')))

    def test_invalid_download_is_not_cached(self):
        with self.assertRaises(OSError):preparation.prepare(self.saved,self.cache,0,download=lambda *a,**k:b'invalid')
        self.assertFalse(list(self.cache.glob('*.png')))
