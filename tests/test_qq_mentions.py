"""Capture real SDK payloads without logging in or sending QQ messages."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from botpy.api import BotAPI
from botpy.message import C2CMessage, GroupMessage
from ournotes_bot.platforms.qq.qq import PreparedReply, ReplySequencer, _prepare_followup, reply_commands
from ournotes_bot.query.continuation import ContextStore


class MentionPayloadTests(unittest.IsolatedAsyncioTestCase):
    def message(self, *, group=True, upload_failure=False):
        self.requests = []

        async def request(route, **kwargs):
            self.requests.append((route.url, kwargs['json']))
            if route.url.endswith('/files'):
                if upload_failure:
                    raise TimeoutError('synthetic upload failure')
                return {'file_info': 'synthetic-image'}
            return {'id': 'synthetic-receipt'}

        api = BotAPI(SimpleNamespace(request=request))
        author = {'member_openid': 'ISSUER', 'user_openid': 'PRIVATE'}
        data = {'id': 'incoming', 'author': author, 'content': '/查曲 odd dice',
                'group_openid': 'GROUP'}
        return (GroupMessage if group else C2CMessage)(api, 'event', data)

    async def send(self, message, reply, *, command='/查曲 odd dice', group=True, contexts=None):
        gate = SimpleNamespace(prepare=AsyncMock(return_value=reply),
                               prepare_followup=AsyncMock(side_effect=_prepare_followup))
        await reply_commands(message, 'GROUP' if group else 'PRIVATE', group, [command],
                             gate=gate, repository=None, ai_parser=None, limit=4,
                             sequencer=ReplySequencer(enabled=False), contexts=contexts if contexts is not None else ContextStore())
        return [payload for url, payload in self.requests if url.endswith('/messages')]

    async def test_group_replies_keep_native_reply_target_without_literal_tags(self):
        for image, failure in ((None, False), (b'image', False), (b'image', True)):
            with self.subTest(image=bool(image), upload_failure=failure):
                message = self.message(upload_failure=failure)
                payloads = await self.send(message, PreparedReply('正文', image))
                self.assertEqual(len(payloads), 1)
                payload = payloads[0]
                self.assertEqual(payload['content'], ' ' if image and not failure else '正文')
                self.assertEqual(payload['msg_type'], 7 if image and not failure else 0)
                self.assertEqual((payload['msg_id'], payload['msg_seq']), ('incoming', 1))
                self.assertEqual(payload['group_openid'], 'GROUP')

    async def test_all_image_pages_keep_native_reply_target_and_distinct_sequences(self):
        message = self.message()
        contexts = ContextStore()
        pages = tuple(PreparedReply('页', b'image') for _ in range(7))
        payloads = await self.send(message, PreparedReply('完整结果', pages=pages), contexts=contexts)
        self.assertEqual([p['msg_seq'] for p in payloads], [1, 2, 3, 4, 5])
        self.assertTrue(all(p['content'].startswith('图片 ') for p in payloads))
        self.assertTrue(all(p['msg_id'] == 'incoming' for p in payloads))
        self.assertIn('/继续', payloads[-1]['content'])
        followup = self.message()
        followup.id = 'followup'
        payloads = await self.send(followup, PreparedReply('unused'), command='/继续', contexts=contexts)
        self.assertEqual([p['msg_seq'] for p in payloads], [1, 2])
        self.assertTrue(all(p['msg_id'] == 'followup' for p in payloads))
        self.assertEqual(payloads[-1]['content'], '图片 7/7；本次已全部发送。')

    async def test_missing_continuation_and_long_upload_fallback_keep_native_target(self):
        message = self.message()
        payloads = await self.send(message, PreparedReply('unused'), command='/继续')
        self.assertEqual(len(payloads), 1)
        self.assertEqual(payloads[0]['msg_id'], 'incoming')
        self.assertNotIn('<qqbot-at-user', payloads[0]['content'])
        message = self.message(upload_failure=True)
        payloads = await self.send(message, PreparedReply('长' * 2000, b'image'))
        self.assertEqual(len(payloads), 1)
        self.assertTrue(payloads[0]['content'].startswith('图片上传失败'))
        self.assertEqual(payloads[0]['msg_id'], 'incoming')
        self.assertIn('/继续', payloads[0]['content'])

    async def test_private_and_missing_issuer_do_not_invent_a_mention(self):
        message = self.message(group=False)
        payloads = await self.send(message, PreparedReply('正文'), group=False)
        self.assertEqual(payloads[0]['content'], '正文')
        message = self.message()
        message.author.member_openid = None
        payloads = await self.send(message, PreparedReply('正文'))
        self.assertEqual(payloads[0]['content'], '正文')
