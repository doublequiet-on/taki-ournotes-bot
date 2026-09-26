"""A handled query must leave a trace.

The bot used to log nothing on success, so an operator could not tell whether a
message ever arrived or what was sent back. These tests pin the new lines, and
pin the boundary: natural-language bodies are deliberately kept out of the log.
"""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Song, SongRepository
from ournotes_bot.qq import PreparedReply, _deliver_reply, _prepare_reply, describe
from ournotes_bot.yatta import BASE


class DescribeTests(unittest.TestCase):
    def test_direct_commands_are_shown(self):
        self.assertEqual(describe("/查卡 1"), "/查卡 1")
        self.assertEqual(describe("  查谱面 100001 EXPERT  "), "查谱面 100001 EXPERT")

    def test_natural_language_body_is_withheld(self):
        secret = "/问 我的手机号是13800000000"
        text = describe(secret)
        self.assertNotIn("13800000000", text)
        self.assertIn("/问", text)
        self.assertIn(str(len(secret)), text)

    def test_long_input_is_truncated(self):
        self.assertLessEqual(len(describe("查曲 " + "长" * 200)), 60)

    def test_empty_input_is_safe(self):
        self.assertEqual(describe(""), "")


class PrepareLoggingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SongRepository(BASE, Path(self.temp.name) / "cache.json")
        self.repo.songs = [Song(
            id=100001, title="迷星叫", titles=("迷星叫",), band="MyGO!!!!!",
            composer="", lyricist="", arranger="", start_at="", jacket_url="",
            charts=(Chart("EXPERT", 25, 25.0, 768, ""),),
            localized={"title": {"zh": "迷星叫"}},
        )]
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=0, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="", localized={},
        )]
        self.repo.metadata = {"cached_at": "test"}
        self.parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6))

    def prepare(self, content):
        return _prepare_reply(content, self.repo, self.parser)

    def test_handled_query_logs_a_completion_line(self):
        with self.assertLogs("ournotes_bot.qq", level="INFO") as log:
            reply = self.prepare("/查曲 迷星叫")
        self.assertIsNotNone(reply)
        joined = "\n".join(log.output)
        self.assertIn("查询完成", joined)
        self.assertIn("迷星叫", joined)
        self.assertIn("耗时", joined)

    def test_unrecognised_text_logs_that_nothing_was_sent(self):
        with self.assertLogs("ournotes_bot.qq", level="INFO") as log:
            self.assertIsNone(self.prepare("今天天气不错"))
        self.assertIn("不回复", "\n".join(log.output))

    def test_failure_still_logs_without_leaking_the_error(self):
        with self.assertLogs("ournotes_bot.qq", level="ERROR") as log:
            with unittest.mock.patch("ournotes_bot.qq.handle_command",
                                     side_effect=RuntimeError("Bearer secret-value")):
                reply = self.prepare("/查曲 迷星叫")
        joined = "\n".join(log.output)
        self.assertEqual(reply.text, "查询暂时失败，请稍后重试。")
        self.assertNotIn("secret-value", joined)


class DeliverLoggingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def message():
        api = SimpleNamespace(
            _http=SimpleNamespace(request=AsyncMock(return_value={"file_info": "f"})),
            post_group_message=AsyncMock(),
            post_c2c_message=AsyncMock(),
        )
        return SimpleNamespace(id="msg-1", _api=api)

    async def test_text_delivery_logs_success(self):
        message = self.message()
        with self.assertLogs("ournotes_bot.qq", level="INFO") as log:
            await _deliver_reply(message, "group-1", True, PreparedReply("文字回复"), msg_seq=2)
        joined = "\n".join(log.output)
        self.assertIn("已发送", joined)
        self.assertIn("msg_seq=2", joined)

    async def test_image_delivery_logs_success(self):
        message = self.message()
        with self.assertLogs("ournotes_bot.qq", level="INFO") as log:
            await _deliver_reply(message, "group-1", True, PreparedReply("t", b"x" * 2048))
        self.assertIn("图片", "\n".join(log.output))

    async def test_upload_failure_is_logged_and_still_sends_text(self):
        message = self.message()
        message._api._http.request = AsyncMock(side_effect=TimeoutError("Bearer secret-value"))
        with self.assertLogs("ournotes_bot.qq", level="WARNING") as log:
            await _deliver_reply(message, "group-1", True, PreparedReply("退化为文字", b"x"))
        self.assertNotIn("secret-value", "\n".join(log.output))
        self.assertEqual(message._api.post_group_message.await_args.kwargs["content"], "退化为文字")

    async def test_send_failure_is_logged_as_an_error(self):
        message = self.message()
        message._api.post_c2c_message = AsyncMock(side_effect=RuntimeError("Bearer secret-value"))
        with self.assertLogs("ournotes_bot.qq", level="ERROR") as log:
            await _deliver_reply(message, "user-1", False, PreparedReply("t"))
        joined = "\n".join(log.output)
        self.assertIn("发送失败", joined)
        self.assertNotIn("secret-value", joined)


if __name__ == "__main__":
    unittest.main()
