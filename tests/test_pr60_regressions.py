"""PR 60 regressions: complete fallback, source stats, cache notices and QQ mentions."""
import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
        with patch.object(Canvas, 'text', capture), patch('ournotes_bot.platforms.qq.qq._visual_asset', return_value=None):
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
