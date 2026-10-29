from __future__ import annotations

import asyncio
import base64
from io import BytesIO
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from botpy.message import GroupMessage
from ournotes_bot.platforms.qq.qq import _upload_image, install_group_parser, register_group_message_parser
from ournotes_bot.domain.chat_images import Attachment, ImageError, MAX_IMAGE_BYTES, parse_attachments
from ournotes_bot.sources.chat_images import ImageCache, ImageLoader, PublicQQResolver, decode_image, validate_image_url


class FakeHttp:
    def __init__(self) -> None:
        self.route = None
        self.payload = None

    async def request(self, route, **kwargs):
        self.route = route
        self.payload = kwargs["json"]
        return {"file_info": "uploaded-image"}


class FakeApi:
    def __init__(self) -> None:
        self._http = FakeHttp()


class MediaTests(unittest.TestCase):
    def test_local_image_is_uploaded_for_group(self) -> None:
        api = FakeApi()
        media = asyncio.run(_upload_image(api, "group-123", b"\x89PNG", group=True))
        self.assertEqual(api._http.route.url, "https://api.bot.qq.com/v2/groups/group-123/files")
        self.assertEqual(base64.b64decode(api._http.payload["file_data"]), b"\x89PNG")
        self.assertEqual(media, {"file_info": "uploaded-image"})

    def test_new_group_event_dispatches_only_queries(self) -> None:
        parser = {}
        dispatch = Mock()
        api = FakeApi()
        register_group_message_parser(parser, api, dispatch, "12345")
        parser["group_message_create"]({"id": "event-1", "d": {
            "id": "message-1", "group_openid": "group-123", "content": "聊天内容",
        }})
        dispatch.assert_not_called()
        parser["group_message_create"]({"id": "event-2", "d": {
            "id": "message-2", "group_openid": "group-123", "content": "/查卡 1",
        }})
        dispatch.assert_not_called()
        parser["group_message_create"]({"id": "event-3", "d": {
            "id": "message-3", "group_openid": "group-123", "content": "<@!other> /查卡 1",
            "mentions": [{"is_you": False}],
        }})
        dispatch.assert_not_called()
        parser["group_message_create"]({"id": "event-4", "d": {
            "id": "message-4", "group_openid": "group-123", "content": "<@!12345> /查卡 1",
        }})
        dispatch.assert_called_once()
        event, message = dispatch.call_args.args
        self.assertEqual(event, "group_at_message_create")
        self.assertIsInstance(message, GroupMessage)
        self.assertEqual(message.id, "message-4")
        dispatch.reset_mock()
        parser["group_message_create"]({"id": "event-5", "d": {
            "id": "message-5", "group_openid": "group-123", "content": "/查卡 1",
            "mentions": [{"is_you": True}],
        }})
        dispatch.assert_called_once()

    def test_parser_installs_on_legacy_sdk_connection(self) -> None:
        connection = SimpleNamespace(parser={}, state=SimpleNamespace(api=FakeApi()))
        install_group_parser(connection, Mock(), "12345")
        self.assertIn("group_message_create", connection.parser)

    def test_group_discovery_does_not_turn_chat_into_queries(self) -> None:
        parser, observe, dispatch = {}, Mock(), Mock()
        register_group_message_parser(parser, FakeApi(), dispatch, "12345", observe)
        parser["group_message_create"]({"d": {"group_openid": "group", "content": "普通聊天"}})
        observe.assert_called_once_with("group")
        dispatch.assert_not_called()


def chat_image_fixture():
    output = BytesIO()
    Image.new('RGB', (4, 3), '#4080ff').save(output, format='PNG')
    data = output.getvalue()
    attachment = Attachment('image-1', 'https://multimedia.nt.qq.com.cn/download?fileid=fixture-signed-secret',
                            'image/png', len(data), 4, 3)
    return attachment, data


class ChatImageTests(unittest.TestCase):
    def test_image_types_and_actual_metadata_are_checked(self):
        attachment, data = chat_image_fixture()
        loaded = decode_image(attachment, data, 'image/png; charset=binary')
        self.assertEqual((loaded.mime, loaded.width, loaded.height), ('image/png', 4, 3))
        self.assertEqual(loaded.data, data)
        self.assertNotIn('fixture-signed-secret', repr(attachment))
        self.assertNotIn(repr(data), repr(loaded))
        for source, payload, mime, code in (
            (attachment, b'not an image', 'image/png', 'image_invalid_content'),
            (attachment, data, 'image/jpeg', 'image_type_mismatch'),
            (Attachment('a', attachment.url, 'image/jpeg'), data, 'image/jpeg', 'image_type_mismatch'),
            (Attachment('a', attachment.url, 'image/png', width=5), data, 'image/png', 'image_metadata_mismatch'),
            (Attachment('a', attachment.url, 'image/png', size=1), data, 'image/png', 'image_metadata_mismatch'),
            (attachment, b'x' * (MAX_IMAGE_BYTES + 1), 'image/png', 'image_too_large'),
        ):
            with self.subTest(code=code), self.assertRaises(ImageError) as raised:
                decode_image(source, payload, mime)
            self.assertEqual(raised.exception.code, code)

    def test_wire_count_duplicates_and_numeric_metadata(self):
        attachment, _ = chat_image_fixture()
        self.assertEqual(parse_attachments(None), ())
        self.assertEqual(parse_attachments([attachment]), (attachment,))
        for value in ([attachment] * 4, [attachment, attachment], [{'id': 'a', 'url': attachment.url,
                  'content_type': 'image/png', 'width': True}], [{'id': 'a', 'url': attachment.url,
                  'content_type': ['image/png']}], [{'id': 'a', 'url': attachment.url,
                  'content_type': 'image/png', 'width': 8193}], [{'id': 'a', 'url': attachment.url,
                  'content_type': 'image/png', 'height': 5000, 'width': 5000}]):
            with self.subTest(value=repr(value)[:60]), self.assertRaises(ImageError):
                parse_attachments(value)

    def test_source_allowlist_has_no_generic_qq_or_url_fallback(self):
        attachment, _ = chat_image_fixture()
        self.assertEqual(validate_image_url(attachment.url), attachment.url)
        for value in ('http://multimedia.nt.qq.com.cn/download?a=b',
                      'https://multimedia.nt.qq.com.cn.evil.invalid/download?a=b',
                      'https://multimedia.nt.qq.com.cn/download?a=b#fragment',
                      'https://user@multimedia.nt.qq.com.cn/download?a=b',
                      'https://multimedia.nt.qq.com.cn:444/download?a=b',
                      'https://multimedia.nt.qq.com.cn/other?a=b',
                      'https://127.0.0.1/download?a=b', '//multimedia.nt.qq.com.cn/download?a=b',
                      'https://gchat.qpic.cn/download?a=b', None):
            with self.subTest(value=value), self.assertRaises(ImageError):
                validate_image_url(value)

    def test_reference_cache_retains_origin_and_expires_by_scope(self):
        attachment, data = chat_image_fixture()
        now = [1000.0]
        cache = ImageCache(wall_clock=lambda: now[0], max_entries=2)
        image = decode_image(attachment, data, 'image/png')
        cache.remember('instance/app/group/a', 'message', 'original-speaker', 1000000, [attachment], [image])
        reference = cache.get('instance/app/group/a', 'message')
        self.assertEqual((reference.message_id, reference.speaker_id), ('message', 'original-speaker'))
        self.assertEqual(reference.images, (image,))
        with self.assertRaises(ImageError):
            cache.get('instance/app/group/b', 'message')
        with self.assertRaisesRegex(ImageError, 'image_identity_mismatch'):
            cache.remember('instance/app/group/a', 'message', 'requester', 1000000, [attachment])
        now[0] += 300
        with self.assertRaisesRegex(ImageError, 'image_not_available'):
            cache.get('instance/app/group/a', 'message')

    def test_cache_bounds_and_animated_image_rejection(self):
        attachment, data = chat_image_fixture()
        cache = ImageCache(wall_clock=lambda: 1000, max_entries=1)
        cache.remember('scope', 'old', 'speaker', 1000000, [attachment])
        cache.remember('scope', 'new', 'speaker', 1000000, [attachment])
        with self.assertRaisesRegex(ImageError, 'image_not_available'):
            cache.get('scope', 'old')
        first, second, output = Image.new('RGB', (4, 3), 'red'), Image.new('RGB', (4, 3), 'blue'), BytesIO()
        first.save(output, format='GIF', save_all=True, append_images=[second], duration=100, loop=0)
        with self.assertRaisesRegex(ImageError, 'image_unsupported_animation'):
            decode_image(Attachment('a', attachment.url, 'image/gif'), output.getvalue(), 'image/gif')

    def test_reference_expiration_survives_clock_rollback(self):
        attachment, _ = chat_image_fixture()
        now, monotonic = [1000.0], [10.0]
        cache = ImageCache(wall_clock=lambda: now[0], monotonic=lambda: monotonic[0])
        cache.remember('scope', 'message', 'speaker', 1000000, [attachment])
        now[0], monotonic[0] = 900.0, 310.0
        with self.assertRaisesRegex(ImageError, 'image_not_available'):
            cache.get('scope', 'message')


class ChatImageAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_connector_resolver_rejects_private_and_mixed_answers(self):
        class Resolver:
            addresses = []
            async def resolve(self, host, port, family):
                return [{'host': address} for address in self.addresses]
            async def close(self):
                pass
        inner = Resolver()
        resolver = PublicQQResolver(inner)
        for addresses in (['127.0.0.1'], ['169.254.169.254'], ['::1'], ['198.18.0.1'],
                          ['8.8.8.8', '10.1.1.1'], ['::ffff:127.0.0.1']):
            inner.addresses = addresses
            with self.subTest(addresses=addresses), self.assertRaisesRegex(ImageError, 'image_untrusted_source'):
                await resolver.resolve('multimedia.nt.qq.com.cn', 443)
        inner.addresses = ['8.8.8.8', '2606:4700:4700::1111']
        self.assertEqual(len(await resolver.resolve('multimedia.nt.qq.com.cn', 443)), 2)

    async def test_download_does_not_follow_redirects_or_forward_credentials(self):
        attachment, data = chat_image_fixture()
        state = {'status': 200, 'content': data, 'headers': {'Content-Type': 'image/png'}, 'length': len(data)}
        calls = []
        class Content:
            async def iter_chunked(self, size):
                yield state['content']
        class Response:
            content = Content()
            @property
            def status(self): return state['status']
            @property
            def headers(self): return state['headers']
            @property
            def content_length(self): return state['length']
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
        class Session:
            def __init__(self, **kwargs): calls.append(('session', kwargs))
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            def get(self, url, **kwargs):
                calls.append(('get', kwargs))
                return Response()
        loader = ImageLoader(wall_clock=lambda: 1000, monotonic=lambda: 10)
        with patch('ournotes_bot.sources.chat_images.aiohttp.ClientSession', Session), \
                patch('ournotes_bot.sources.chat_images.aiohttp.TCPConnector', return_value=object()):
            loaded = await loader.load_many([attachment], timestamp_ms=1000000, deadline=20)
            self.assertEqual(loaded[0].data, data)
            self.assertFalse(calls[0][1]['trust_env'])
            self.assertNotIn('Authorization', calls[0][1]['headers'])
            self.assertFalse(calls[1][1]['allow_redirects'])
            for status in (302, 401, 403, 404, 410, 500):
                state['status'] = status
                with self.subTest(status=status), self.assertRaisesRegex(ImageError, 'image_not_available'):
                    await loader.load_many([attachment], timestamp_ms=1000000, deadline=20)
            state['status'], state['length'] = 200, MAX_IMAGE_BYTES + 1
            with self.assertRaisesRegex(ImageError, 'image_too_large'):
                await loader.load_many([attachment], timestamp_ms=1000000, deadline=20)
            state['length'], state['content'] = None, b'x' * (MAX_IMAGE_BYTES + 1)
            with self.assertRaisesRegex(ImageError, 'image_too_large'):
                await loader.load_many([attachment], timestamp_ms=1000000, deadline=20)
        for timestamp, deadline in ((700000, 20), (1000000, 10), (1031000, 20)):
            with self.assertRaisesRegex(ImageError, 'image_not_available'):
                await loader.load_many([attachment], timestamp_ms=timestamp, deadline=deadline)
