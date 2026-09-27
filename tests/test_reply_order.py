"""Contract tests for reply ordering and QQ gateway normalization."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from ournotes_bot.config import Settings, read_flag
from ournotes_bot.qq import ReplySequencer, install_gateway_host, normalize_gateway_url


class ReplyOrderFlagTests(unittest.TestCase):
    def settings(self, **env):
        with patch("ournotes_bot.config.load_dotenv"), patch.dict(os.environ, env, clear=True):
            return Settings.from_env()

    def test_default_and_invalid_values_keep_ordering_enabled(self):
        self.assertTrue(self.settings().reply_order)
        for value in ("", "maybe", "2"):
            with self.subTest(value=value):
                self.assertTrue(self.settings(OURNOTES_REPLY_ORDER=value).reply_order)
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(read_flag("OURNOTES_REPLY_ORDER", True))
            self.assertFalse(read_flag("OURNOTES_REPLY_ORDER", False))

    def test_explicit_boolean_spellings_are_accepted(self):
        for value, expected in (("1", True), (" yes ", True), ("enabled", True),
                                ("0", False), (" no ", False), ("disabled", False)):
            with self.subTest(value=value):
                self.assertEqual(
                    self.settings(OURNOTES_REPLY_ORDER=value).reply_order,
                    expected,
                )


class GatewayHostTests(unittest.TestCase):
    def test_known_legacy_hosts_are_rewritten_without_losing_url_parts(self):
        cases = (
            ("wss://api.sgroup.qq.com/websocket", "wss://api.bot.qq.com/websocket"),
            ("wss://sandbox.api.sgroup.qq.com/websocket", "wss://api.bot.qq.com/websocket"),
            ("wss://api.sgroup.qq.com:443/websocket?x=1",
             "wss://api.bot.qq.com:443/websocket?x=1"),
        )
        for source, expected in cases:
            with self.subTest(source=source):
                self.assertEqual(normalize_gateway_url(source, "api.bot.qq.com"), expected)

    def test_unknown_urls_and_an_empty_target_are_left_alone(self):
        for url, host in (
            ("wss://gateway.example.com/websocket", "api.bot.qq.com"),
            ("wss://api.bot.qq.com/websocket", "api.bot.qq.com"),
            ("ws://127.0.0.1:8181/websocket", "api.bot.qq.com"),
            ("not a url", "api.bot.qq.com"),
            ("wss://api.sgroup.qq.com/websocket", ""),
        ):
            with self.subTest(url=url, host=host):
                self.assertEqual(normalize_gateway_url(url, host), url)


class GatewayInstallTests(unittest.IsolatedAsyncioTestCase):
    def patch_api(self):
        from botpy.api import BotAPI
        original = BotAPI.get_ws_url
        self.addCleanup(setattr, BotAPI, "get_ws_url", original)
        return BotAPI

    async def test_install_rewrites_once_and_preserves_the_payload(self):
        BotAPI = self.patch_api()

        async def fake(self):
            return {"url": "wss://api.sgroup.qq.com/websocket", "shards": 1,
                    "session_start_limit": {"max_concurrency": 1, "remaining": 1}}

        BotAPI.get_ws_url = fake
        self.assertTrue(install_gateway_host("api.bot.qq.com"))
        installed = BotAPI.get_ws_url
        self.assertFalse(install_gateway_host("api.bot.qq.com"))
        self.assertIs(BotAPI.get_ws_url, installed)
        with self.assertLogs("ournotes_bot.qq", level="INFO"):
            payload = await BotAPI.get_ws_url(None)
        self.assertEqual(payload["url"], "wss://api.bot.qq.com/websocket")
        self.assertEqual(payload["shards"], 1)

    async def test_payload_without_a_url_is_passed_through(self):
        BotAPI = self.patch_api()

        async def fake(self):
            return {"unexpected": True}

        BotAPI.get_ws_url = fake
        install_gateway_host("api.bot.qq.com")
        self.assertEqual(await BotAPI.get_ws_url(None), {"unexpected": True})


class ReplySequencerTests(unittest.IsolatedAsyncioTestCase):
    async def test_batches_send_in_arrival_order_even_when_ready_in_reverse(self):
        sequencer = ReplySequencer(timeout=5)
        tickets = [await sequencer.issue() for _ in range(3)]
        sent: list[int] = []

        async def batch(ticket, delay):
            await asyncio.sleep(delay)
            await sequencer.wait_turn(ticket)
            sent.append(ticket)
            await sequencer.release(ticket)

        await asyncio.gather(batch(tickets[0], 0.05), batch(tickets[1], 0.02),
                             batch(tickets[2], 0))
        self.assertEqual(sent, [0, 1, 2])

    async def test_timeout_is_bounded_and_release_recovers_the_queue(self):
        sequencer = ReplySequencer(timeout=0.1)
        first = await sequencer.issue()
        second = await sequencer.issue()
        with self.assertLogs("ournotes_bot.qq", level="WARNING") as log:
            await asyncio.wait_for(sequencer.wait_turn(second), timeout=5)
        self.assertIn("超时", " ".join(log.output))
        await sequencer.release(first)
        await asyncio.wait_for(sequencer.wait_turn(second), timeout=1)

    async def test_out_of_order_release_does_not_rewind_the_cursor(self):
        sequencer = ReplySequencer(timeout=5)
        first = await sequencer.issue()
        second = await sequencer.issue()
        await sequencer.release(second)
        await sequencer.release(first)
        third = await sequencer.issue()
        await asyncio.wait_for(sequencer.wait_turn(third), timeout=1)

    async def test_concurrent_issue_keeps_tickets_unique(self):
        sequencer = ReplySequencer(timeout=5)
        tickets = await asyncio.gather(*(sequencer.issue() for _ in range(50)))
        self.assertEqual(sorted(tickets), list(range(50)))


class DisabledOrderingTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_sequencer_is_inert(self):
        sequencer = ReplySequencer(timeout=0.01, enabled=False)
        first = await sequencer.issue()
        second = await sequencer.issue()
        self.assertIsNone(first)
        self.assertIsNone(second)
        with self.assertNoLogs("ournotes_bot.qq", level="WARNING"):
            await asyncio.wait_for(sequencer.wait_turn(first), timeout=0.5)
        await sequencer.release(first)

    async def test_disabling_allows_a_fast_later_batch_to_reply_first(self):
        async def run(enabled):
            sequencer = ReplySequencer(timeout=5, enabled=enabled)
            sent: list[str] = []

            async def batch(delay, label):
                ticket = await sequencer.issue()
                try:
                    await asyncio.sleep(delay)
                    await sequencer.wait_turn(ticket)
                    sent.append(label)
                finally:
                    await sequencer.release(ticket)

            await asyncio.gather(batch(0.05, "slow"), batch(0.0, "fast"))
            return sent

        self.assertEqual(await run(False), ["fast", "slow"])
        self.assertEqual(await run(True), ["slow", "fast"])


if __name__ == "__main__":
    unittest.main()
