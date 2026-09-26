"""Replies must leave in arrival order, and the gateway host must be current.

QueryGate frees a worker as soon as one frees up, so a slow first query can be
overtaken by a faster later one. ReplySequencer re-serializes only the sending
step, which is the part a reader perceives as order.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from ournotes_bot.qq import (LEGACY_GATEWAY_HOSTS, ReplySequencer, install_gateway_host,
                             normalize_gateway_url)


class GatewayHostTests(unittest.TestCase):
    def rewrite(self, url, host="api.bot.qq.com"):
        return normalize_gateway_url(url, host)

    def test_advertised_legacy_host_is_replaced(self):
        self.assertEqual(self.rewrite("wss://api.sgroup.qq.com/websocket"),
                         "wss://api.bot.qq.com/websocket")

    def test_legacy_http_host_is_replaced(self):
        self.assertEqual(self.rewrite("wss://sandbox.api.sgroup.qq.com/websocket"),
                         "wss://api.bot.qq.com/websocket")

    def test_port_and_path_are_preserved(self):
        self.assertEqual(self.rewrite("wss://api.sgroup.qq.com:443/websocket?x=1"),
                         "wss://api.bot.qq.com:443/websocket?x=1")

    def test_unknown_host_is_left_alone(self):
        """A future region-specific address must not be clobbered."""
        for url in ("wss://gateway.example.com/websocket",
                    "wss://api.bot.qq.com/websocket",
                    "ws://127.0.0.1:8181/websocket",
                    "not a url"):
            with self.subTest(url=url):
                self.assertEqual(self.rewrite(url), url)

    def test_empty_host_disables_rewriting(self):
        url = "wss://api.sgroup.qq.com/websocket"
        self.assertEqual(self.rewrite(url, host=""), url)

    def test_legacy_set_covers_what_the_server_advertises(self):
        self.assertIn("api.sgroup.qq.com", LEGACY_GATEWAY_HOSTS)
        self.assertNotIn("api.bot.qq.com", LEGACY_GATEWAY_HOSTS)


class GatewayInstallTests(unittest.IsolatedAsyncioTestCase):
    def patch_api(self):
        from botpy.api import BotAPI
        original = BotAPI.get_ws_url
        self.addCleanup(setattr, BotAPI, "get_ws_url", original)
        return BotAPI

    async def test_installed_get_ws_url_rewrites_the_advertised_address(self):
        BotAPI = self.patch_api()

        async def fake(self):
            return {"url": "wss://api.sgroup.qq.com/websocket", "shards": 1,
                    "session_start_limit": {"max_concurrency": 1, "remaining": 1}}

        BotAPI.get_ws_url = fake
        self.assertTrue(install_gateway_host("api.bot.qq.com"))
        with self.assertLogs("ournotes_bot.qq", level="INFO"):
            payload = await BotAPI.get_ws_url(None)
        self.assertEqual(payload["url"], "wss://api.bot.qq.com/websocket")
        self.assertEqual(payload["shards"], 1)

    async def test_install_stays_idempotent(self):
        BotAPI = self.patch_api()
        self.assertTrue(install_gateway_host("api.bot.qq.com"))
        installed = BotAPI.get_ws_url
        self.assertFalse(install_gateway_host("api.bot.qq.com"))
        self.assertIs(BotAPI.get_ws_url, installed)

    async def test_payload_without_a_url_is_passed_through(self):
        BotAPI = self.patch_api()

        async def fake(self):
            return {"unexpected": True}

        BotAPI.get_ws_url = fake
        install_gateway_host("api.bot.qq.com")
        self.assertEqual(await BotAPI.get_ws_url(None), {"unexpected": True})


class ReplySequencerTests(unittest.IsolatedAsyncioTestCase):
    async def test_later_ticket_waits_for_the_earlier_one(self):
        """The batch that arrived first sends first, even if it took longer."""
        sequencer = ReplySequencer(timeout=5)
        first = await sequencer.issue()
        second = await sequencer.issue()
        sent: list[int] = []

        async def batch(ticket):
            await sequencer.wait_turn(ticket)
            sent.append(ticket)
            await sequencer.release(ticket)

        # The later batch reaches its turn first and must still wait.
        await asyncio.gather(batch(second), batch(first))
        self.assertEqual(sent, [0, 1])

    async def test_three_batches_keep_arrival_order(self):
        sequencer = ReplySequencer(timeout=5)
        tickets = [await sequencer.issue() for _ in range(3)]
        sent: list[int] = []

        async def batch(ticket, delay):
            await asyncio.sleep(delay)
            await sequencer.wait_turn(ticket)
            sent.append(ticket)
            await sequencer.release(ticket)

        # Completion order is reversed; send order must not follow it.
        await asyncio.gather(batch(tickets[0], 0.05), batch(tickets[1], 0.02),
                             batch(tickets[2], 0))
        self.assertEqual(sent, [0, 1, 2])

    async def test_first_ticket_never_blocks_on_itself(self):
        sequencer = ReplySequencer(timeout=5)
        ticket = await sequencer.issue()
        await asyncio.wait_for(sequencer.wait_turn(ticket), timeout=1)

    async def test_release_never_rewinds_the_cursor(self):
        """Out-of-order releases must not make later batches wait on old ones."""
        sequencer = ReplySequencer(timeout=5)
        first = await sequencer.issue()
        second = await sequencer.issue()
        await sequencer.release(second)
        await sequencer.release(first)
        third = await sequencer.issue()
        await asyncio.wait_for(sequencer.wait_turn(third), timeout=1)

    async def test_wait_is_bounded_so_a_wedged_batch_cannot_silence_the_rest(self):
        sequencer = ReplySequencer(timeout=0.1)
        await sequencer.issue()             # never released
        stuck_behind = await sequencer.issue()
        with self.assertLogs("ournotes_bot.qq", level="WARNING") as log:
            await asyncio.wait_for(sequencer.wait_turn(stuck_behind), timeout=5)
        self.assertIn("超时", " ".join(log.output))

    async def test_release_after_timeout_lets_the_next_one_through(self):
        sequencer = ReplySequencer(timeout=0.1)
        first = await sequencer.issue()
        second = await sequencer.issue()
        with self.assertLogs("ournotes_bot.qq", level="WARNING"):   # expected timeout
            await asyncio.wait_for(sequencer.wait_turn(second), timeout=5)
        await sequencer.release(first)
        await asyncio.wait_for(sequencer.wait_turn(second), timeout=1)

    async def test_concurrent_issue_keeps_tickets_unique(self):
        sequencer = ReplySequencer(timeout=5)
        tickets = await asyncio.gather(*(sequencer.issue() for _ in range(50)))
        self.assertEqual(sorted(tickets), list(range(50)))


if __name__ == "__main__":
    unittest.main()
