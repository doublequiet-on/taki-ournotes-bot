"""PR 60 regressions: complete fallback, source stats, cache notices and QQ mentions."""
import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from ournotes_bot.commands import CommandResult, HELP_TEXT
from ournotes_bot.data import SongRepository
from ournotes_bot.natural_query.local_query import parse_local_query
from ournotes_bot.platforms.qq.qq import PreparedReply, _deliver_reply, _render_prepared
from ournotes_bot.query.card_catalog import CardAnswer, CardRequest, detail_text
from ournotes_bot.query.continuation import parse_operation
from ournotes_bot.rendering import card_detail_layout, catalog_layout
from ournotes_bot.rendering.haneoka_theme import Canvas
from ournotes_bot.sources.haneoka.catalog import validate


class ReviewRegressionTests(unittest.TestCase):
    def setUp(self):
        saved = json.loads((Path(__file__).parent/'fixtures/haneoka_catalog.json').read_text('utf-8'))['snapshot']
        records, _ = validate(saved, 1791763200)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SongRepository('', Path(self.tmp.name)/'cache.json', data_source='haneoka')
        self.repo.songs, self.repo.cards, self.repo.support_cards = map(list, records)
        assets = patch('ournotes_bot.sources.haneoka.game_assets.asset_bytes', return_value=None)
        assets.start()
        self.addCleanup(assets.stop)

    def result(self, card, mode):
        support = card in self.repo.support_cards
        answer = CardAnswer(CardRequest(support=support, mode=mode, card_id=card.id, query='SSR'), (card,))
        return answer, CommandResult(('support_cards' if support else 'cards', str(card.id) if mode=='detail' else 'SSR', 1), catalog=answer)

    def test_detail_failure_retains_all_metadata_and_max_stats_for_both_types(self):
        for base in (self.repo.cards[0], self.repo.support_cards[0]):
            card = replace(base, catalog={**base.catalog, 'detail_stale': True,
                'relations': [{'kind': '活动', 'id': 123, 'name': {'zh': '关联测试'}}]})
            answer, result = self.result(card, 'detail')
            with patch.object(card_detail_layout, 'render', side_effect=ValueError('oversize')):
                reply = _render_prepared(answer.text(), result, self.repo, 'zh')
            self.assertEqual(reply.text, detail_text(card))
            for expected in ('满级基础值', '关联测试', '上次有效缓存', 'Haneoka JP', 'LIVE'):
                self.assertIn(expected, reply.text)
            self.assertNotIn('Lv.1 / Rank 1', reply.text)

    def test_detail_image_uses_same_max_values_as_text(self):
        for card in (self.repo.cards[0], self.repo.support_cards[0]):
            drawn = []
            original = Canvas.text
            def capture(c, x, y, value, *args, **kwargs):
                drawn.append(str(value))
                return original(c, x, y, value, *args, **kwargs)
            with patch.object(Canvas, 'text', capture):
                parts = card_detail_layout.render(card, load=lambda *args: None)
            self.assertTrue(parts)
            self.assertIn('满级基础值', drawn)
            support = card in self.repo.support_cards
            for value in (card.performance, card.technic, card.visual):
                self.assertIn(f'{value/100:g}%' if support else f'{value:,}', drawn)
                self.assertIn(f'{value/100:g}%' if support else f'{value:g}', detail_text(card))

    def test_stale_list_status_in_image_and_both_text_paths(self):
        card = replace(self.repo.cards[0], catalog={**self.repo.cards[0].catalog, 'detail_stale': True, 'detail_loaded': False})
        answer, result = self.result(card, 'list')
        drawn = []
        original = Canvas.text
        def capture(c, x, y, value, *args, **kwargs):
            drawn.append(str(value))
            return original(c, x, y, value, *args, **kwargs)
        with patch.object(Canvas, 'text', capture), patch('ournotes_bot.application.replies.visual_asset', return_value=None):
            reply = _render_prepared(answer.text(), result, self.repo, 'zh')
        for phrase in ('上次有效缓存', '资料未获取'):
            self.assertIn(phrase, ''.join(drawn))
            self.assertIn(phrase, reply.text)
        with patch.object(catalog_layout, 'render_list', side_effect=ValueError('oversize')):
            fallback = _render_prepared(answer.text(), result, self.repo, 'zh')
        self.assertIn('上次有效缓存', fallback.text)
        self.assertIn('资料未获取', fallback.text)

    def test_memory_name_covers_lists_filters_counts_and_skills(self):
        for question in ('留影卡有哪些', '留影卡 EX有哪些', '留影卡有多少张', '留影卡 51技能', '51留影卡技能'):
            spec = parse_local_query(question, self.repo)
            self.assertEqual(spec.intent, 'support_card', question)

    def test_removed_commands_are_not_continuations_or_help_entries(self):
        for command in ('下一页', '上一页', '选 2', '选二', '难度 EX', '难度EX'):
            for prefix in ('', '/', '／'):
                self.assertIsNone(parse_operation(prefix+command))
            self.assertNotIn('/'+command.split()[0], HELP_TEXT)
        self.assertEqual(parse_operation('/继续').kind, 'continue')


class MentionTests(unittest.IsolatedAsyncioTestCase):
    async def test_group_text_image_and_upload_failure_target_issuer(self):
        for image, failure, text in ((None, False, '正文'), (b'image', False, '正文'),
                                     (b'image', True, '正文'), (b'image', True, '长'*1900)):
            api = SimpleNamespace(post_group_message=AsyncMock(return_value={'id': 'ack'}))
            message = SimpleNamespace(id='event', author=SimpleNamespace(member_openid='issuer'), _api=api)
            with patch('ournotes_bot.platforms.qq.qq._upload_image', new=AsyncMock(side_effect=TimeoutError() if failure else None, return_value={'file_info': 'image'})):
                await _deliver_reply(message, 'group', True, PreparedReply(text, image), msg_seq=3)
            payload = api.post_group_message.await_args.kwargs
            self.assertNotIn('<qqbot-at-user', payload['content'])
            self.assertNotIn('<@', payload['content'])
            self.assertEqual((payload['msg_id'], payload['msg_seq'], payload['group_openid']), ('event', 3, 'group'))
            api.post_group_message.assert_awaited_once()

    async def test_private_text_has_no_mention(self):
        api = SimpleNamespace(post_c2c_message=AsyncMock(return_value={'id':'ack'}))
        message = SimpleNamespace(id='event', author=SimpleNamespace(member_openid='issuer', user_openid='user'), _api=api)
        await _deliver_reply(message, 'user', False, PreparedReply('正文'))
        self.assertEqual(api.post_c2c_message.await_args.kwargs['content'], '正文')


class DetailAssetScopeTests(unittest.TestCase):
    """Exercise real detail planning with independently counted owned images."""

    def card(self, relations=0, support=False):
        from ournotes_bot.data import Card, SupportCard, Skill
        common=dict(skills=(Skill('liveSkill','技能','完整工程条件。'),),full_url='full',
                    catalog=dict(band_logos=['logo'],attribute_icon='attribute',skill_icons=['skill'],
                        people=[dict(id=i,band=1,order=i,name={'zh':'工程人物'},face='face-'+str(i)) for i in (1,2)],
                        relations=[dict(kind='相关活动',id=i,name={'zh':'关联工程测试'},image='relation-'+str(i),start='',end='')
                                   for i in range(relations)]))
        if support:
            return SupportCard(id=1,title='工程留影',character='工程人物',characters=('工程人物','工程人物二'),rarity=4,card_type=1,performance=1,technic=2,visual=3,start_at='',thumbnail_url='',**common)
        return Card(id=1,asset_id=1,title='工程角色',character='工程人物',band='工程乐队',rarity=4,card_type=1,performance=1,technic=2,visual=3,start_at='',skill_name='',thumbnail_url='',**common)

    def assets(self, *, fail=None, missing=False, reject=0):
        from contextlib import contextmanager,ExitStack
        import threading
        from ournotes_bot.rendering import haneoka_theme,readable_pages
        owner=self
        @contextmanager
        def scope():
            state=dict(live=0,peak=0,canvases=0,loads=[],at_canvas=[],objects=[],game=[],attempts=0)
            thread=threading.get_ident()
            class Owned:
                def __init__(self,key,size,canvas=False):
                    self.key,self.size,self.canvas,self.closed=key,size,canvas,False
                    self.width,self.height=size;self.mode='RGB'
                    state['objects'].append(self)
                    if canvas:state['canvases']+=1
                    else:
                        state['live']+=1;state['peak']=max(state['peak'],state['live'])
                def copy(self):return Owned(self.key+':copy',self.size)
                def paste(self,*args):pass
                def close(self):
                    owner.assertFalse(self.closed,'double close '+self.key);self.closed=True
                    state['canvases' if self.canvas else 'live']-=1
            def load(url,size):
                owner.assertEqual(threading.get_ident(),thread,'detail must load on its caller thread')
                owner.assertEqual(state['canvases'],0,'late source access')
                state['loads'].append((url,size))
                if fail=='prepare' and url=='skill':raise RuntimeError('prepare failed')
                return None if missing else Owned(url,(972,500) if url=='full' else size)
            def game(name):
                owner.assertEqual(state['canvases'],0);state['game'].append(name);return None
            class Canvas:
                def __init__(self,width,height):
                    state['at_canvas'].append(len(state['loads']));self.image=Owned('canvas',(width,height),True)
                def paste(self,asset,*args,**kwargs):
                    if asset is not None:owner.assertFalse(asset.closed)
                def text(self,*args,**kwargs):
                    if fail=='paint':raise RuntimeError('paint failed')
                def rarity_badge(self,*args):haneoka_theme.game_asset('RarityIconCenter_SSR.png')
                def __getattr__(self,name):return lambda *a,**k:None
            def encode(image,**kwargs):
                owner.assertTrue(kwargs['close_input']);image.close();state['attempts']+=1
                if fail=='encode':raise OSError('encode failed')
                if state['attempts']<=reject:raise readable_pages.ImageBudgetError('fixture bytes')
                return b'encoded'
            def lines(text,*args,**kwargs):
                if fail=='measure':raise RuntimeError('measure failed')
                return [str(text)]
            with ExitStack() as stack:
                stack.enter_context(patch.object(card_detail_layout,'Canvas',Canvas))
                stack.enter_context(patch.object(card_detail_layout,'lines',lines))
                stack.enter_context(patch.object(card_detail_layout,'frame',return_value=None))
                stack.enter_context(patch.object(card_detail_layout.ImageOps,'contain',side_effect=lambda image,size:Owned('fitted',size)))
                stack.enter_context(patch.object(readable_pages,'MAX_IMAGE_PIXELS',4_000_000))
                stack.enter_context(patch.object(readable_pages,'encode_image',encode))
                stack.enter_context(haneoka_theme.game_assets(game))
                try:yield state,load
                finally:
                    owner.assertEqual(state['live'],0);owner.assertEqual(state['canvases'],0)
                    owner.assertTrue(all(image.closed for image in state['objects']))
        return scope()

    def test_shared_and_candidate_page_ownership_stays_bounded(self):
        for support,locale in ((False,"zh"),(False,"ja"),(False,"en"),(True,"zh"),(True,"ja"),(True,"en")):
            peaks=[]
            for count in (0,16,64):
                with self.subTest(support=support,count=count),self.assets() as (state,load):
                    result=card_detail_layout.render(self.card(count,support),locale,load=load)
                    self.assertTrue(result);self.assertEqual(result[0].start,0)
                    self.assertTrue(all(a.stop==b.start for a,b in zip(result,result[1:])))
                    self.assertEqual(state['game'],['RarityIconCenter_SSR.png'])
                    self.assertEqual(sum(url=='full' for url,_ in state['loads']),1)
                    peaks.append(state['peak'])
            self.assertEqual(peaks[1],peaks[2])

    def test_retries_reuse_candidate_including_missing_images(self):
        for missing in (False,True):
            with self.subTest(missing=missing),self.assets(missing=missing,reject=2) as (state,load):
                card_detail_layout.render(self.card(16),load=load)
                self.assertEqual(len(set(state['at_canvas'][:3])),1)
                self.assertEqual(state['game'],['RarityIconCenter_SSR.png'])

    def test_encoder_handoff_keeps_assets_and_reclaims_only_completed_paint(self):
        for fail in (None,'paint','encode'):
            with self.subTest(fail=fail),self.assets(fail=fail,reject=2 if fail is None else 0) as (state,load):
                calls=[]
                def reclaim(pixels):
                    self.assertEqual(state['canvases'],1)
                    self.assertGreater(state['live'],0)
                    self.assertGreater(pixels,0)
                    calls.append(state['attempts'])
                with patch.object(card_detail_layout,'_release_image_memory',reclaim):
                    if fail:
                        with self.assertRaisesRegex((RuntimeError,OSError),fail+' failed'):
                            card_detail_layout.render(self.card(16),load=load)
                    else:card_detail_layout.render(self.card(16),load=load)
                self.assertEqual(calls,list(range(state['attempts'])))

    def test_all_failure_stages_release_ownership(self):
        for fail,error in (('measure',RuntimeError),('prepare',RuntimeError),('paint',RuntimeError),('encode',OSError)):
            with self.subTest(stage=fail),self.assets(fail=fail) as (state,load):
                with self.assertRaisesRegex(error,fail+' failed'):
                    card_detail_layout.render(self.card(16),load=load)
                if fail=='measure':self.assertIn('full',[key for key,_ in state['loads']])

    def test_unplanned_key_does_not_fetch_while_drawing(self):
        from contextlib import contextmanager
        original=card_detail_layout.prepared_render_assets
        @contextmanager
        def incomplete(*args,**kwargs):
            with original(*args,**kwargs) as assets:
                for key in tuple(assets):
                    if key[0]=='skill':
                        image=assets.pop(key)
                        if image is not None:image.close()
                yield assets
        with self.assets() as (state,load),patch.object(card_detail_layout,'prepared_render_assets',incomplete):
            with self.assertRaises(KeyError):card_detail_layout.render(self.card(16),load=load)
            self.assertEqual(sum(url=='skill' for url,_ in state['loads']),1)

    def test_serial_preparation_failure_restores_context_and_closes_images(self):
        from ournotes_bot.rendering import haneoka_theme as theme
        images=[]
        def load(*args):
            image=Mock();images.append(image);return image
        def source(name):raise RuntimeError('icon failed')
        with theme.game_assets(source):
            with self.assertRaisesRegex(RuntimeError,'icon failed'):
                with theme.prepared_render_assets([('a',(1,1)),('b',(1,1))],load,['rarity'],synchronous=True):
                    self.fail('must fail before drawing')
            self.assertIs(theme._GAME_ASSETS.get(),source)
        for image in images:image.close.assert_called_once()

    def test_borrowed_frame_and_icon_remain_usable(self):
        from io import BytesIO
        from PIL import Image
        from ournotes_bot.rendering import haneoka_theme as theme
        raw=BytesIO()
        with Image.new('RGBA',(20,20),'red') as image:image.save(raw,format='PNG')
        border=theme.frame(32,40,4);icon=theme._icon(raw.getvalue(),12,12)
        with self.assets() as (state,load),patch.object(card_detail_layout,'frame',return_value=border):
            card_detail_layout.render(self.card(16),load=load)
        # These belong to theme caches, not the render's asset scope.
        self.assertIs(theme.frame(32,40,4),border)
        self.assertIs(theme._icon(raw.getvalue(),12,12),icon)
        self.assertTrue(border.getbbox());self.assertTrue(icon.getbbox())
