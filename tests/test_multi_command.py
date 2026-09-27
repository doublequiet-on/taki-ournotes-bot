"""Contract tests for bounded multi-command execution and ordered replies."""
from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ournotes_bot.commands import split_commands
from ournotes_bot.config import MAX_MULTI_COMMAND_LIMIT, QQ_PASSIVE_REPLY_LIMIT, Settings
from ournotes_bot.data import SongRepository
from ournotes_bot.i18n import MESSAGES, tr
from ournotes_bot.qq import PreparedReply, QueryGate, _deliver_reply, prepare_commands
from ournotes_bot.yatta import BASE


class SplitCommandsTests(unittest.TestCase):
    def test_command_lines_are_normalized_without_leaking_mentions(self):
        cases = (
            ("/查卡 1", ["查卡 1"]),
            ("  查曲 迷星叫  ", ["查曲 迷星叫"]),
            ("/查谱面 100003 EXPERT\n/查卡 1\n/查缩写 skk",
             ["查谱面 100003 EXPERT", "查卡 1", "查缩写 skk"]),
            ("/查卡 1\r\n\r\n   \r\n/查曲 mygo\r\n", ["查卡 1", "查曲 mygo"]),
            ("<@!12345> /查卡 1\n<@!12345> /查曲 mygo", ["查卡 1", "查曲 mygo"]),
            ("   \n\n  ", []),
            ("", []),
        )
        for message, expected in cases:
            with self.subTest(message=message):
                self.assertEqual(split_commands(message), expected)


class MultiCommandLimitSettingTests(unittest.TestCase):
    def settings(self, **env):
        with patch("ournotes_bot.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
            return Settings.from_env()

    def test_limit_defaults_to_a_safe_slot_and_is_clamped(self):
        self.assertEqual(QQ_PASSIVE_REPLY_LIMIT, 5)
        self.assertEqual(MAX_MULTI_COMMAND_LIMIT, 4)
        self.assertEqual(self.settings().multi_command_limit, 4)
        for value, expected in (("2", 2), ("0", 1), ("-9", 1), ("999", 4)):
            with self.subTest(value=value):
                self.assertEqual(
                    self.settings(OURNOTES_MULTI_COMMAND_LIMIT=value).multi_command_limit,
                    expected,
                )


class PassiveReplySequenceTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def message():
        api = SimpleNamespace(
            _http=SimpleNamespace(request=AsyncMock(return_value={"file_info": "f"})),
            post_group_message=AsyncMock(),
            post_c2c_message=AsyncMock(),
        )
        return SimpleNamespace(id="msg-1", _api=api)

    async def test_text_and_media_replies_carry_the_expected_sequence(self):
        message = self.message()
        for seq in (1, 2, 3):
            await _deliver_reply(message, "group-1", True, PreparedReply(f"text{seq}"), msg_seq=seq)
        sent = [call.kwargs for call in message._api.post_group_message.await_args_list]
        self.assertEqual([kw["msg_seq"] for kw in sent], [1, 2, 3])
        self.assertEqual([kw["msg_id"] for kw in sent], ["msg-1", "msg-1", "msg-1"])

        await _deliver_reply(message, "group-1", True, PreparedReply("t", b"image"), msg_seq=4)
        media = message._api.post_group_message.await_args.kwargs
        self.assertEqual((media["msg_type"], media["msg_seq"]), (7, 4))

        await _deliver_reply(message, "user-1", False, PreparedReply("only"))
        self.assertEqual(message._api.post_c2c_message.await_args.kwargs["msg_seq"], 1)


class OverflowNoticeTests(unittest.TestCase):
    def test_notice_is_complete_in_every_locale(self):
        for locale in ("zh", "en", "ja"):
            text = tr(locale, "too_many_commands", limit=5, total=8)
            self.assertIn("5", text)
            self.assertIn("8", text)
            self.assertNotIn("{", text)
        self.assertEqual(set(MESSAGES), {"zh", "en", "ja"})


class PrepareCommandsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = SongRepository(BASE, Path(self.temp.name) / "cache.json")

    async def test_replies_follow_asked_order_even_when_later_commands_are_faster(self):
        delays = {"a": 0.30, "b": 0.02, "c": 0.02}

        def fake_prepare(content, repository, ai_parser):
            time.sleep(delays[content])
            return PreparedReply(f"回复{content}")

        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=fake_prepare):
            replies, overflow = await prepare_commands(["a", "b", "c"], gate, self.repo, None, 5)
        self.assertEqual(overflow, 0)
        self.assertEqual([reply.text for reply in replies], ["回复a", "回复b", "回复c"])

    async def test_peak_concurrency_respects_the_gate(self):
        running = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def fake_prepare(content, repository, ai_parser):
            with lock:
                running["now"] += 1
                running["peak"] = max(running["peak"], running["now"])
            time.sleep(0.05)
            with lock:
                running["now"] -= 1
            return PreparedReply(content)

        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=fake_prepare):
            replies, _ = await prepare_commands(
                ["a", "b", "c", "d", "e", "f"], gate, self.repo, None, 6,
            )
        self.assertEqual(len(replies), 6)
        self.assertEqual(running["peak"], 2)

    async def test_overflow_is_reported_and_not_run(self):
        started: list[str] = []

        def fake_prepare(content, repository, ai_parser):
            started.append(content)
            return PreparedReply(content)

        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=fake_prepare):
            replies, overflow = await prepare_commands(["a", "b", "c"], gate, self.repo, None, 2)
        self.assertEqual(overflow, 1)
        self.assertEqual(started, ["a", "b"])
        self.assertEqual([reply.text for reply in replies], ["a", "b"])

    async def test_one_failure_does_not_drop_other_replies_or_leak_details(self):
        def fake_prepare(content, repository, ai_parser):
            if content == "boom":
                raise RuntimeError("Bearer secret-value")
            return PreparedReply(content)

        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=fake_prepare), \
             self.assertLogs("ournotes_bot.qq", level="ERROR") as log:
            replies, _ = await prepare_commands(["a", "boom", "c"], gate, self.repo, None, 5)
        self.assertEqual([reply.text for reply in replies], ["a", "c"])
        self.assertNotIn("secret-value", " ".join(log.output))

    async def test_unrecognised_lines_are_skipped(self):
        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=lambda content, repo, parser: None):
            replies, overflow = await prepare_commands(["闲聊", "聊天"], gate, self.repo, None, 5)
        self.assertEqual(replies, [])
        self.assertEqual(overflow, 0)


if __name__ == "__main__":
    unittest.main()
