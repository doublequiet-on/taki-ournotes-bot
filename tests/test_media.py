from __future__ import annotations

import asyncio
import base64
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from botpy.message import GroupMessage
from ournotes_bot.platforms.qq.qq import _upload_image, install_group_parser, register_group_message_parser


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
