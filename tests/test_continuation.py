"""Visible-page identity, bounded isolation, receipts and asynchronous supersession."""
import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import resolve_command, handle_command
from ournotes_bot.config import Settings
from ournotes_bot.data import Chart, Song, SongRepository
from ournotes_bot.query.continuation import (ContextKey, ContextStore, Operation, capture_context,
    catalog_version, execute_followup, parse_operation, CHANGED, MISSING)
from ournotes_bot.platforms.qq.qq import (PreparedReply, QueryGate, ReplySequencer, _prepare_reply,
    _prepare_followup, _deliver_reply, context_key, reply_commands)
from ournotes_bot.sources.haneoka.song_meta import parse_payload
from ournotes_bot.sources.haneoka.song_traits import SongTraits
from ournotes_bot.sources.yatta import build_data, build_support_cards
from ournotes_bot.structured_query import QuerySpec, resolve_query
from test_song_meta import payload, STAMP
from test_query import CHARACTERS, CARDS, SONGS, META, SUPPORTS


class Fixture:
    def setup_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = SongRepository("https://bdon.yatta.moe", Path(temp.name) / "cache.json")
        ident, songs, meta = payload(35)
        self.repo.songs = [Song(int(k), s["musicTitle"][0], tuple(s["musicTitle"]), "MyGO!!!!!", "", "", "", "", "",
                              (Chart("HARD", 20, 20, 100, ""), Chart("EXPERT", 25, 25, 200, "")),
                              traits=SongTraits(2, ("JUST", "COMBO"))) for k, s in songs.items()]
        snapshot = parse_payload(ident, songs, meta, STAMP)
        self.repo.song_meta = SimpleNamespace(_snapshot=snapshot, get=Mock(return_value=snapshot))
        self.parser = AIQueryParser(Settings("", "", self.repo.data_base, self.repo.cache_file, 6, ""))
        self.key = ContextKey("qq", "group", "g1", "u1")

    def context(self, query="/查曲 MyGO"):
        return capture_context(resolve_command(query, self.repo), self.repo)


class ContinuationCoreTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.setup_repo()

    def test_explicit_and_bounded_chinese_grammar(self):
        for text, kind, value in (("/下一页", "page", 1), ("/问 上一页", "page", -1),
                                 ("/选 2", "select", 2), ("/问 第十二个", "select", 12),
                                 ("/问 选二十", "select", 20), ("/详情", "detail", ""),
                                 ("/问 看详情", "detail", ""), ("/难度 EX", "difficulty", "EXPERT"),
                                 ("/问 换成困难", "difficulty", "HARD")):
            self.assertEqual(parse_operation(text), Operation(kind, value))
        self.assertIsNone(parse_operation("帮我看看第一个最适合什么队伍"))
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            for command in ("/问 下一页", "/问 第三个", "/问 看详情", "/问 换成 EX"):
                self.assertIn("重新发送", self.parser.answer_with_plan(command, self.repo)[0])
        self.assertEqual(handle_command("/下一页", self.repo), MISSING)

    def test_visible_snapshot_page_select_detail_difficulty(self):
        first = self.context()
        result, second = execute_followup(first, Operation("page", 1), self.repo)
        self.assertEqual(second.page, 2)
        self.assertEqual(second.visible[0].entity_id, self.repo.songs[16].id)
        selected_id = second.visible[1].entity_id
        result, selected = execute_followup(second, Operation("select", 2), self.repo)
        self.assertEqual(result.chart[0].id, selected_id)
        self.assertEqual(result.chart[1][0].difficulty, "EXPERT")
        result, changed = execute_followup(selected, Operation("difficulty", "HARD"), self.repo)
        self.assertEqual((result.chart[0].id, result.chart[1][0].difficulty), (selected_id, "HARD"))
        result, same = execute_followup(changed, Operation("detail"), self.repo)
        self.assertEqual(result.chart[1][0].difficulty, "HARD")
        result, third = execute_followup(same, Operation("page", 1), self.repo)
        self.assertEqual(third.page, 3)
        self.assertIsNone(third.selected)

    def test_invalid_choice_does_not_guess(self):
        context = self.context()
        for operation in (Operation("detail"), Operation("select", 0), Operation("select", 17),
                          Operation("page", -1), Operation("difficulty", "EXPERT")):
            self.assertIsInstance(execute_followup(context, operation, self.repo), str)
        with self.assertRaises(Exception):
            context.visible[0].entity_id = 1

    def test_versions_and_changed_during_capture_fail_closed(self):
        context = self.context()
        before = catalog_version(self.repo)
        result = resolve_command("/查曲 MyGO", self.repo)
        self.repo.songs.reverse()
        self.assertIsNone(capture_context(result, self.repo, catalog_before=before))
        self.assertEqual(execute_followup(context, Operation("select", 1), self.repo), CHANGED)
        current = self.context()
        with patch("ournotes_bot.query.continuation.alias_version", return_value=-999):
            self.assertEqual(execute_followup(current, Operation("page", 1), self.repo), CHANGED)

    def test_meta_selection_keeps_row_and_filters_and_version(self):
        source = self.repo.song_meta._snapshot
        hard = replace(source.rows[0], difficulty="HARD", eff=99, level=20)
        snapshot = replace(source, rows=(hard, *source.rows))
        self.repo.song_meta._snapshot = snapshot
        self.repo.song_meta.get.return_value = snapshot
        context = self.context("/查分数表 乐队=MyGO 颜色=蓝 激奏=JUST lv<=25 前10")
        self.assertEqual(context.visible[0].difficulty, "HARD")
        result, chosen = execute_followup(context, Operation("select", 1), self.repo)
        self.assertEqual(result.meta.rows[0].difficulty, "HARD")
        self.assertEqual(result.spec.song_filter.colors, (2,))
        self.assertEqual(result.spec.song_filter.mode, "contains")
        result, next_page = execute_followup(chosen, Operation("page", 1), self.repo)
        self.assertEqual(next_page.query.limit, 10)
        self.assertIn("颜色=蓝", next_page.query.command_label())
        self.repo.song_meta._snapshot = replace(snapshot, source_version="changed")
        self.assertEqual(execute_followup(next_page, Operation("select", 1), self.repo), CHANGED)

    def test_card_page_normalization_and_typed_selection(self):
        _, cards = build_data(CHARACTERS, CARDS, SONGS, META)
        self.repo.cards = [replace(cards[0], id=i) for i in range(1, 36)]
        self.repo.support_cards = build_support_cards(CHARACTERS, SUPPORTS)
        spec = QuerySpec("card", card_query="页2")
        self.assertEqual((spec.page, spec.card_query), (2, ""))
        result = resolve_query(spec, self.repo)
        self.assertEqual(result.catalog.visible[0].id, 17)
        first = self.context("/查卡")
        result, second = execute_followup(first, Operation("page", 1), self.repo)
        with patch.object(self.repo, "card_with_detail", side_effect=lambda c: c) as detail, \
             patch.object(self.repo, "support_card_with_detail", side_effect=AssertionError("wrong type")):
            result, selected = execute_followup(second, Operation("select", 2), self.repo)
        self.assertEqual(result.catalog.cards[0].id, 18)
        detail.assert_called_once()
        self.assertIsInstance(execute_followup(selected, Operation("difficulty", "EXPERT"), self.repo), str)

    def test_store_scope_ttl_capacity_receipts_and_generation(self):
        now = [0]
        store = ContextStore(capacity=3, clock=lambda: now[0])
        context = self.context()
        ticket = store.begin(self.key)
        self.assertFalse(store.commit(ticket, context, confirmed=False))
        self.assertIsNone(store.get(self.key))
        self.assertTrue(store.commit(ticket, context, confirmed=True))
        for key in (replace(self.key, user="u2"), replace(self.key, conversation="g2"),
                    replace(self.key, kind="c2c"), replace(self.key, platform="other")):
            self.assertIsNone(store.get(key))
        newer = store.begin(self.key)
        self.assertFalse(store.commit(ticket, context, confirmed=True))
        self.assertTrue(store.commit(newer, context, confirmed=True))
        now[0] = 590
        failed = store.begin(self.key, clear=False)
        store.commit(failed, context, confirmed=False)
        now[0] = 601
        self.assertIsNone(store.get(self.key))
        for i in range(5):
            store.begin(replace(self.key, user=str(i)))
        self.assertEqual(len(store._entries), 3)
        self.assertIsNone(store.get(replace(self.key, user="0")))
        self.assertIsNone(ContextStore().get(self.key))


class ContinuationDeliveryTests(Fixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_repo()
        self.store = ContextStore()
        self.gate = QueryGate(2, 4)
        self.sequencer = ReplySequencer(enabled=False)
        rendering = patch("ournotes_bot.platforms.qq.qq._image_from_result", return_value=None)
        rendering.start()
        self.addCleanup(rendering.stop)
        ai = patch.object(self.parser, "_request", side_effect=AssertionError("model called"))
        ai.start()
        self.addCleanup(ai.stop)

    def message(self, user="u1", receipt=None):
        api = SimpleNamespace(_http=SimpleNamespace(request=AsyncMock(return_value={"file_info": "file"})),
                              post_group_message=AsyncMock(return_value={"id": "ack"} if receipt is None else receipt),
                              post_c2c_message=AsyncMock(return_value={"id": "ack"}))
        return SimpleNamespace(id="incoming", author=SimpleNamespace(member_openid=user, user_openid=user), _api=api)

    async def send(self, commands, message=None, **kwargs):
        message = message or self.message()
        await reply_commands(message, "g1", True, commands, gate=kwargs.get("gate", self.gate),
                             repository=self.repo, ai_parser=self.parser, limit=4,
                             sequencer=self.sequencer, contexts=self.store)
        return message

    async def test_successful_receipt_then_followup_and_user_isolation(self):
        await self.send(["/查曲 MyGO"])
        self.assertEqual(self.store.get(self.key).page, 1)
        other = await self.send(["/问 下一页"], self.message("u2"))
        self.assertEqual(other._api.post_group_message.await_args.kwargs["content"], MISSING)
        await self.send(["/下一页"])
        self.assertEqual(self.store.get(self.key).page, 2)
        await self.send(["/问 选二"])
        self.assertEqual(self.store.get(self.key).selected.entity_id, self.repo.songs[17].id)

    async def test_missing_failure_and_exception_receipts_do_not_commit(self):
        for receipt in ({}, {"code": 1}, {"id": ""}):
            await self.send(["/查曲 MyGO"], self.message(receipt=receipt))
            self.assertIsNone(self.store.get(self.key))
        await self.send(["/查曲 MyGO"])
        message = self.message()
        message._api.post_group_message.side_effect = TimeoutError("private")
        await self.send(["/下一页"], message)
        self.assertEqual(self.store.get(self.key).page, 1)
        message._api.post_group_message.assert_awaited_once()
        message = self.message()
        message._api.post_group_message.return_value = None
        await self.send(["/下一页"], message)
        self.assertEqual(self.store.get(self.key).page, 1)

    async def test_upload_fallback_confirms_the_same_captured_selection(self):
        message = self.message()
        message._api._http.request.side_effect = TimeoutError()
        with patch("ournotes_bot.platforms.qq.qq._image_from_result", return_value=b"image"):
            await self.send(["/查曲 MyGO"], message)
        self.assertEqual(self.store.get(self.key).visible[0].entity_id, self.repo.songs[0].id)
        self.assertEqual(message._api.post_group_message.await_args.kwargs["msg_type"], 0)

    async def test_multiple_lists_and_same_message_chain(self):
        await self.send(["/查曲 MyGO", "/查曲 100001"])
        self.assertIsNone(self.store.get(self.key))
        message = await self.send(["/查曲 MyGO", "/下一页"])
        self.assertEqual(self.store.get(self.key).page, 1)
        self.assertIn("不会按依赖链", message._api.post_group_message.await_args.kwargs["content"])

    async def test_late_full_result_does_not_replace_newer_query(self):
        entered, release = asyncio.Event(), asyncio.Event()
        old_reply = PreparedReply("old", context=self.context())
        new_reply = PreparedReply("new", context=self.context("/查曲 100002"))
        async def prepare(command, *_):
            if command == "old":
                entered.set()
                await release.wait()
                return old_reply
            return new_reply
        gate = SimpleNamespace(prepare=prepare)
        old = asyncio.create_task(self.send(["old"], gate=gate))
        await entered.wait()
        await self.send(["new"], gate=gate)
        release.set()
        await old
        self.assertEqual(self.store.get(self.key).visible[0].entity_id, 100002)

    async def test_new_full_query_supersedes_pending_continuation(self):
        await self.send(["/查曲 MyGO"])
        entered, release = asyncio.Event(), asyncio.Event()
        message = self.message()
        async def send(**_):
            entered.set()
            await release.wait()
            return {"id": "ack"}
        message._api.post_group_message.side_effect = send
        pending = asyncio.create_task(self.send(["/下一页"], message))
        await entered.wait()
        await self.send(["/查曲 100002"])
        release.set()
        await pending
        self.assertEqual(self.store.get(self.key).visible[0].entity_id, 100002)

    async def test_same_user_next_pages_are_serial_and_cancel_keeps_previous(self):
        await self.send(["/查曲 MyGO"])
        await asyncio.gather(self.send(["/下一页"]), self.send(["/问 下一页"]))
        self.assertEqual(self.store.get(self.key).page, 3)

    async def test_queued_old_followup_cannot_attach_to_new_full_query(self):
        await self.send(["/查曲 MyGO"])
        entered, release = asyncio.Event(), asyncio.Event()
        first = self.message()
        async def sending(**_):
            entered.set()
            await release.wait()
            return {"id": "ack"}
        first._api.post_group_message.side_effect = sending
        running = asyncio.create_task(self.send(["/下一页"], first))
        await entered.wait()
        queued_message = self.message()
        queued = asyncio.create_task(self.send(["/下一页"], queued_message))
        await asyncio.sleep(0)
        await self.send(["/查曲 100002"])
        release.set()
        await asyncio.gather(running, queued)
        self.assertEqual(queued_message._api.post_group_message.await_args.kwargs["content"], MISSING)
        self.assertEqual(self.store.get(self.key).visible[0].entity_id, 100002)

    async def test_followup_while_new_query_pending_cannot_invalidate_it(self):
        entered, release = asyncio.Event(), asyncio.Event()
        reply = PreparedReply("new", context=self.context())
        async def prepare(*_):
            entered.set()
            await release.wait()
            return reply
        pending = asyncio.create_task(self.send(["full"], gate=SimpleNamespace(prepare=prepare)))
        await entered.wait()
        message = await self.send(["/下一页"])
        self.assertEqual(message._api.post_group_message.await_args.kwargs["content"], MISSING)
        release.set()
        await pending
        self.assertEqual(self.store.get(self.key).page, 1)

    async def test_cancel_during_send_keeps_previous_page(self):
        await self.send(["/查曲 MyGO 页3"])
        entered = asyncio.Event()
        message = self.message()
        async def send(**_):
            entered.set()
            await asyncio.Event().wait()
        message._api.post_group_message.side_effect = send
        pending = asyncio.create_task(self.send(["/上一页"], message))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(self.store.get(self.key).page, 3)

    async def test_identity_missing_fails_closed_and_current_query_independent(self):
        message = self.message()
        message.author.member_openid = None
        self.assertIsNone(context_key(message, "g1", True))
        await self.send(["/查曲 MyGO"], message)
        self.assertIsNone(self.store.get(self.key))
        await self.send(["/查曲 MyGO 颜色=蓝 EX"])
        await self.send(["/查曲 100001"])
        self.assertEqual(self.store.get(self.key).query, "/查曲 100001")


if __name__ == "__main__":
    unittest.main()
