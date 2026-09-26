"""One message may carry several commands, one per line.

Preparation is bounded by QueryGate but replies must follow the order the user
asked for, so a slow query cannot make its answer appear after a later one.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from ournotes_bot.commands import split_commands
from ournotes_bot.config import (MAX_MULTI_COMMAND_LIMIT, QQ_PASSIVE_REPLY_LIMIT, Settings)
from ournotes_bot.data import SongRepository
from ournotes_bot.i18n import MESSAGES, tr
from ournotes_bot.qq import PreparedReply, QueryGate, _deliver_reply, prepare_commands
from ournotes_bot.yatta import BASE


class SplitCommandsTests(unittest.TestCase):
    def test_single_command_is_unchanged(self):
        self.assertEqual(split_commands("/查卡 1"), ["查卡 1"])
        self.assertEqual(split_commands("  查曲 迷星叫  "), ["查曲 迷星叫"])

    def test_newlines_split_into_separate_commands(self):
        message = "/查谱面 100003 EXPERT\n/查卡 1\n/查缩写 skk"
        self.assertEqual(split_commands(message),
                         ["查谱面 100003 EXPERT", "查卡 1", "查缩写 skk"])

    def test_windows_and_blank_lines_are_tolerated(self):
        message = "/查卡 1\r\n\r\n   \r\n/查曲 mygo\r\n"
        self.assertEqual(split_commands(message), ["查卡 1", "查曲 mygo"])

    def test_mention_does_not_leak_into_any_command(self):
        message = "<@!12345> /查卡 1\n<@!12345> /查曲 mygo"
        self.assertEqual(split_commands(message), ["查卡 1", "查曲 mygo"])

    def test_empty_message_yields_nothing(self):
        self.assertEqual(split_commands("   \n\n  "), [])
        self.assertEqual(split_commands(""), [])


class MultiCommandLimitSettingTests(unittest.TestCase):
    def settings(self, **env):
        with patch("ournotes_bot.config.load_dotenv"), \
             patch.dict(os.environ, env, clear=True):
            return Settings.from_env()

    def test_default_limit_leaves_room_for_the_over_limit_notice(self):
        """QQ rejects the sixth passive reply, so commands stop one slot short."""
        self.assertEqual(QQ_PASSIVE_REPLY_LIMIT, 5)
        self.assertEqual(MAX_MULTI_COMMAND_LIMIT, 4)
        self.assertEqual(self.settings().multi_command_limit, 4)

    def test_configured_limit(self):
        self.assertEqual(self.settings(OURNOTES_MULTI_COMMAND_LIMIT="2").multi_command_limit, 2)

    def test_limit_is_clamped_to_the_platform_ceiling(self):
        self.assertEqual(self.settings(OURNOTES_MULTI_COMMAND_LIMIT="0").multi_command_limit, 1)
        self.assertEqual(self.settings(OURNOTES_MULTI_COMMAND_LIMIT="-9").multi_command_limit, 1)
        self.assertEqual(self.settings(OURNOTES_MULTI_COMMAND_LIMIT="999").multi_command_limit,
                         MAX_MULTI_COMMAND_LIMIT)

    def test_example_env_documents_the_setting(self):
        example = Path(__file__).resolve().parents[1] / ".env.example"
        self.assertIn("OURNOTES_MULTI_COMMAND_LIMIT", example.read_text(encoding="utf-8"))


class PassiveReplySequenceTests(unittest.IsolatedAsyncioTestCase):
    """QQ discards a passive reply that repeats an earlier (msg_id, msg_seq)."""

    @staticmethod
    def message():
        api = SimpleNamespace(
            _http=SimpleNamespace(request=AsyncMock(return_value={"file_info": "f"})),
            post_group_message=AsyncMock(),
            post_c2c_message=AsyncMock(),
        )
        return SimpleNamespace(id="msg-1", _api=api)

    async def test_group_replies_use_increasing_msg_seq(self):
        message = self.message()
        for seq in (1, 2, 3):
            await _deliver_reply(message, "group-1", True, PreparedReply(f"text{seq}"), msg_seq=seq)
        sent = [call.kwargs for call in message._api.post_group_message.await_args_list]
        self.assertEqual([kw["msg_seq"] for kw in sent], [1, 2, 3])
        self.assertEqual([kw["msg_id"] for kw in sent], ["msg-1", "msg-1", "msg-1"])

    async def test_default_sequence_still_matches_a_single_reply(self):
        message = self.message()
        await _deliver_reply(message, "user-1", False, PreparedReply("only"))
        kw = message._api.post_c2c_message.await_args.kwargs
        self.assertEqual(kw["msg_seq"], 1)

    async def test_media_reply_also_carries_its_sequence(self):
        message = self.message()
        await _deliver_reply(message, "group-1", True, PreparedReply("t", b"image"), msg_seq=2)
        kw = message._api.post_group_message.await_args.kwargs
        self.assertEqual((kw["msg_type"], kw["msg_seq"]), (7, 2))


class OverflowNoticeTests(unittest.TestCase):
    def test_notice_exists_in_every_locale(self):
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
        """The first command is deliberately slow; its reply must still come first."""
        delays = {"a": 0.30, "b": 0.02, "c": 0.02}

        def fake_prepare(content, repository, ai_parser):
            time.sleep(delays[content])
            return PreparedReply(f"回复{content}")

        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=fake_prepare):
            replies, overflow = await prepare_commands(
                ["a", "b", "c"], gate, self.repo, None, 5)

        self.assertEqual(overflow, 0)
        self.assertEqual([r.text for r in replies], ["回复a", "回复b", "回复c"])

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
                ["a", "b", "c", "d", "e", "f"], gate, self.repo, None, 6)

        self.assertEqual(len(replies), 6)
        self.assertEqual(running["peak"], 2)

    async def test_overflow_is_reported_and_not_run(self):
        started: list[str] = []

        def fake_prepare(content, repository, ai_parser):
            started.append(content)
            return PreparedReply(content)

        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=fake_prepare):
            replies, overflow = await prepare_commands(
                ["a", "b", "c"], gate, self.repo, None, 2)

        self.assertEqual(overflow, 1)
        self.assertEqual(started, ["a", "b"])
        self.assertEqual([r.text for r in replies], ["a", "b"])

    async def test_one_failure_does_not_drop_the_other_replies(self):
        def fake_prepare(content, repository, ai_parser):
            if content == "boom":
                raise RuntimeError("Bearer secret-value")
            return PreparedReply(content)

        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=fake_prepare), \
             self.assertLogs("ournotes_bot.qq", level="ERROR") as log:
            replies, _ = await prepare_commands(
                ["a", "boom", "c"], gate, self.repo, None, 5)

        self.assertEqual([r.text for r in replies], ["a", "c"])
        self.assertNotIn("secret-value", " ".join(log.output))

    async def test_none_results_are_skipped(self):
        """An unrecognised line produces no reply, same as a single message."""
        gate = QueryGate(2, 4)
        with patch("ournotes_bot.qq._prepare_reply", side_effect=lambda c, r, a: None):
            replies, overflow = await prepare_commands(
                ["闲聊", "聊天"], gate, self.repo, None, 5)
        self.assertEqual(replies, [])
        self.assertEqual(overflow, 0)


if __name__ == "__main__":
    unittest.main()
