"""Asset ownership, source boundaries, native card semantics and failure delivery."""
import hashlib
import io
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
from ournotes_bot.data import Card
from ournotes_bot.rendering.catalog_layout import card_level
from ournotes_bot.rendering.card_detail_layout import table_rows
from ournotes_bot.sources.haneoka import game_assets
from ournotes_bot.sources.haneoka.event_cutoffs import player_detail, HaneokaEventCutoffRepository
from ournotes_bot.platforms.qq.qq import PreparedReply, _deliver_reply, _fallback_cursor, _expand_replies


class VisualSourceTests(unittest.TestCase):
    def test_game_asset_checksum_cache_and_unknown_names(self):
        raw=b'controlled public bytes';sha=hashlib.sha256(raw).hexdigest()
        with tempfile.TemporaryDirectory() as directory, patch.dict(game_assets.MANIFEST, {'test.png':{'sha256':sha,'url':'https://haneoka.org/test'}}), patch.dict(game_assets._MEMORY, clear=True), patch.dict(game_assets._FAILED, clear=True), patch.object(game_assets,'_fetch',return_value=raw) as fetch:
            self.assertEqual(game_assets.asset_bytes('test.png',directory),raw)
            self.assertEqual(game_assets.asset_bytes('test.png',directory),raw)
            self.assertEqual(fetch.call_count,1)
            self.assertEqual((Path(directory)/(sha+'.png')).read_bytes(),raw)
            game_assets._MEMORY.clear()
            fetch.side_effect=AssertionError('valid cache must be offline')
            self.assertEqual(game_assets.asset_bytes('test.png',directory),raw)
            with self.assertRaises(ValueError):game_assets.asset_bytes('../secret',directory)

    def test_game_asset_hash_failure_is_not_cached_as_content(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(game_assets.MANIFEST, {'test.png':{'sha256':'a'*64,'url':'https://haneoka.org/test'}}), patch.dict(game_assets._MEMORY, clear=True), patch.dict(game_assets._FAILED, clear=True), patch.object(game_assets,'_fetch',return_value=b'wrong'):
            self.assertIsNone(game_assets.asset_bytes('test.png',directory))
            self.assertNotIn('test.png',game_assets._MEMORY)
            self.assertFalse(list(Path(directory).iterdir()))

    def test_roster_rejects_duplicate_slots_and_out_of_range_markers(self):
        value=player_detail({'rankExp':True,'favoriteMemberCardId':0,'cards':[{'slot':0,'memberAwakeCount':99,'supportRank':3}]})
        self.assertIsNone(value['rankExp']);self.assertIsNone(value['favoriteMemberCardId'])
        self.assertIsNone(value['cards'][0]['memberAwakeCount'])
        self.assertEqual(value['cards'][0]['supportRank'],3)
        self.assertEqual(player_detail({'cards':[{'slot':0},{'slot':0}]})['cards'],[])

    def test_optional_roster_cache_is_validated_and_old_cache_remains_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=HaneokaEventCutoffRepository(Path(directory))
            payload={'scores':[1],'player_ids':[None],'player_names':[None], 'stale':False,'unsafe':False,'fetched':None,'server':None}
            self.assertTrue(repo._valid_payload('board:jp:1:1',payload))
            self.assertFalse(repo._valid_payload('board:jp:1:1',{**payload,'player_details':[{'cards':'bad'}]}))
            self.assertTrue(repo._valid_payload('board:jp:1:1',{**payload,'player_details':[player_detail({})]}))

    def test_event_bonus_uses_game_region_date_index_not_translation_index(self):
        from ournotes_bot.sources.haneoka.ranking_art import event_marks
        event=SimpleNamespace(source='haneoka',server='tw',event_id='2',start_ms=100,end_ms=200)
        saved={'identities':{'intl':{'releaseId':'r-1234567890abcdef1234'}},'documents':{'intl':{
            'events':{'2':{'startAt':[None,None,100,None,None],'endAt':[None,None,200,None,None],
                          'effects':[{'raw':{'_cardType':1}}]}}, 'bands':{},
            'ui-marks':{'CardType-Red.png':'runtime/unity/Assets/AddressableResources/UI/Atlas/FixUiSpriteAtlas.spriteatlasv2/CardType-Red--Sprite-1.png'}}}}
        self.assertEqual(len(event_marks(saved,event)),1)
        event.start_ms=99
        self.assertEqual(event_marks(saved,event),())

    def test_card_level_uses_exact_progress_cap_and_unknown_stays_unknown(self):
        card=Card(1,1,'卡','角色','乐队',4,1,1,1,1,'','','','',catalog={
            'level_curve':[{'level':1,'exp':0},{'level':80,'exp':100},{'level':90,'exp':200}],
            'level_limits':[{'_awakeCount':0,'_limitLevel':80},{'_awakeCount':1,'_limitLevel':90}]})
        self.assertEqual(card_level(card,200,0),80)
        self.assertEqual(card_level(card,200,1),90)
        self.assertIsNone(card_level(card,200,None))
        self.assertIsNone(card_level(card,200,2))

    def test_skill_table_distinguishes_base_and_condition(self):
        rows=table_rows([f'得分提升{n}%，若为成员则提升{n*2}%' for n in range(1,6)])
        self.assertEqual([r[0] for r in rows],['基础效果','条件成立'])
        self.assertEqual(rows[1][1][-1],'10%')

    def test_native_asset_cache_returns_owned_copies(self):
        from ournotes_bot import visuals
        url='https://haneoka.org/assets/jp/Assets/AddressableResources/MemberCard/1/member_thumbnail.png?release=r-1234567890abcdef1234'
        with patch.dict(visuals._NATIVE_ASSETS,clear=True), patch.object(visuals,'_NATIVE_ASSET_BYTES',0), patch.object(visuals,'_asset',side_effect=lambda *a,**k:Image.new('RGBA',(8,8),'red')) as fetch:
            first=visuals.load_visual_asset(url,(8,8));first.putpixel((0,0),(0,0,0,0));first.close()
            second=visuals.load_visual_asset(url,(8,8))
            self.assertEqual(second.getpixel((0,0)),(255,0,0,255));second.close()
            self.assertEqual(fetch.call_count,1)


class FallbackDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_upload_retains_every_character_for_continue(self):
        message=SimpleNamespace(id='message',_api=SimpleNamespace(_http=SimpleNamespace(request=AsyncMock(side_effect=OSError('upload'))),post_group_message=AsyncMock(return_value={'id':'receipt'})))
        text='完整技能说明\n'*900
        reply=PreparedReply(text,b'image')
        result=await _deliver_reply(message,'group',True,reply)
        self.assertEqual(result.status,'fallback_pending')
        cursor=_fallback_cursor(reply)
        self.assertEqual(''.join(p.text for p in cursor.remaining),text)
        self.assertTrue(all(len(p.text)<=1700 for p in cursor.remaining))
        self.assertEqual(message._api.post_group_message.await_count,1)
