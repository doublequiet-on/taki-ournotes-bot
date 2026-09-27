"""Privacy and operator-visible logging contracts."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.config import Settings
from ournotes_bot.data import Chart, Song, SongRepository
from ournotes_bot.qq import PreparedReply, _deliver_reply, _prepare_reply, describe
from ournotes_bot.yatta import BASE


class DescribeTests(unittest.TestCase):
    def test_description_keeps_commands_but_hides_natural_language_bodies(self):
        self.assertEqual(describe("/查卡 1"), "/查卡 1")
        self.assertEqual(describe("  查谱面 100001 EXPERT  "), "查谱面 100001 EXPERT")
        secret = "/问 我的手机号是13800000000"
        natural = describe(secret)
        self.assertNotIn("13800000000", natural)
        self.assertIn("/问", natural)
        self.assertIn(str(len(secret)), natural)
        self.assertLessEqual(len(describe("查曲 " + "长" * 200)), 60)
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
        self.repo.metadata = {"cached_at": "test"}
        self.parser = AIQueryParser(Settings("", "", BASE, self.repo.cache_file, 6))

    def test_handled_and_ignored_messages_leave_clear_completion_logs(self):
        with self.assertLogs("ournotes_bot.qq", level="INFO") as handled_log:
            reply = _prepare_reply("/查曲 迷星叫", self.repo, self.parser)
        self.assertIsNotNone(reply)
        handled = "\n".join(handled_log.output)
        self.assertIn("查询完成", handled)
        self.assertIn("迷星叫", handled)
        self.assertIn("耗时", handled)

        with self.assertLogs("ournotes_bot.qq", level="INFO") as ignored_log:
            self.assertIsNone(_prepare_reply("今天天气不错", self.repo, self.parser))
        self.assertIn("不回复", "\n".join(ignored_log.output))


class DeliverLoggingTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def message():
        api = SimpleNamespace(
            _http=SimpleNamespace(request=AsyncMock(return_value={"file_info": "f"})),
            post_group_message=AsyncMock(),
            post_c2c_message=AsyncMock(),
        )
        return SimpleNamespace(id="msg-1", _api=api)

    async def test_success_logs_distinguish_text_and_image_delivery(self):
        message = self.message()
        with self.assertLogs("ournotes_bot.qq", level="INFO") as text_log:
            await _deliver_reply(message, "group-1", True, PreparedReply("文字回复"), msg_seq=2)
        self.assertIn("已发送", "\n".join(text_log.output))
        self.assertIn("msg_seq=2", "\n".join(text_log.output))

        with self.assertLogs("ournotes_bot.qq", level="INFO") as image_log:
            await _deliver_reply(message, "group-1", True, PreparedReply("t", b"x" * 2048))
        self.assertIn("图片", "\n".join(image_log.output))


if __name__ == "__main__":
    unittest.main()
