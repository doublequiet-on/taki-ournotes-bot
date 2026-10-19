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
from ournotes_bot.platforms.qq.qq import (DeliveryCursor, PreparedReply, QueryGate, ReplySequencer, _prepare_reply,
    _prepare_followup, _deliver_reply, context_key, reply_commands)
from ournotes_bot.sources.haneoka.song_traits import SongTraits
from ournotes_bot.sources.chart_data import ChartDataError
from ournotes_bot.sources.yatta import build_data, build_support_cards
from ournotes_bot.structured_query import QuerySpec, resolve_query
from test_query import CHARACTERS, CARDS, SONGS, META, SUPPORTS


class Fixture:
    def setup_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = SongRepository("https://bdon.yatta.moe", Path(temp.name) / "cache.json")
        self.repo.songs = [Song(i, f"合成曲{i}", (f"合成曲{i}",), "MyGO!!!!!", "", "", "", "", "",
                              (Chart("HARD", 20, 20, 100, ""), Chart("EXPERT", 25, 25, 200, "")),
                              traits=SongTraits(2, ("JUST", "COMBO"))) for i in range(100001, 100036)]
        self.parser = AIQueryParser(Settings("", "", self.repo.data_base, self.repo.cache_file, 6, ""))
        self.key = ContextKey("qq", "group", "g1", "u1")

    def context(self, query="/查曲 MyGO"):
        return capture_context(resolve_command(query, self.repo), self.repo)


class ContinuationCoreTests(Fixture, unittest.TestCase):
    def setUp(self):
        self.setup_repo()

    def test_explicit_and_bounded_chinese_grammar(self):
        for text, kind, value in (("/问 下一页", "page", 1), ("/问 上一页", "page", -1),
                                 ("/问 选 2", "select", 2), ("/问 第十二个", "select", 12),
                                 ("/问 选二十", "select", 20), ("/详情", "detail", ""),
                                 ("/问 看详情", "detail", ""), ("/问 难度 EX", "difficulty", "EXPERT"),
                                 ("/问 换成困难", "difficulty", "HARD")):
            self.assertEqual(parse_operation(text), Operation(kind, value))
        self.assertIsNone(parse_operation("帮我看看第一个最适合什么队伍"))
        with patch.object(self.parser, "_request", side_effect=AssertionError("model called")):
            for command in ("/问 下一页", "/问 第三个", "/问 看详情", "/问 换成 EX"):
                self.assertIn("重新发送", self.parser.answer_with_plan(command, self.repo)[0])
        self.assertEqual(handle_command("/问 下一页", self.repo), MISSING)

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

    def test_chart_source_change_invalidates_previous_selection(self):
        context = self.context()
        self.repo.chart_source = "haneoka"
        self.assertEqual(execute_followup(context, Operation("select", 1), self.repo), CHANGED)


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
    async def test_completed_continue_is_idempotent_and_retains_selection(self):
        from ournotes_bot.rendering.readable_pages import ImagePart
        from ournotes_bot.query.continuation import QueryContext
        with patch('ournotes_bot.rendering.song_layout.render_list',
                   return_value=((ImagePart(b'first',0,1),ImagePart(b'second',1,35)),((0,1),(1,35)))):
            initial = await self.send(['/查曲 MyGO'])
        self.assertEqual(initial._api.post_group_message.await_count,2)
        cursor = self.store.get(self.key)
        self.assertEqual((cursor.sent,cursor.total,cursor.remaining),(2,2,()))
        visible = cursor.query_context.visible
        for _ in range(2):
            message = await self.send(['/继续'])
            send = message._api.post_group_message
            self.assertEqual(send.await_count,1)
            self.assertEqual(send.await_args.kwargs['msg_type'],0)
            self.assertNotIn('结果 ',send.await_args.kwargs['content'])
            self.assertFalse(message._api._http.request.called)
            self.assertEqual(self.current_query().visible,visible)
        with patch('ournotes_bot.rendering.song_detail_layout.render',return_value=b'detail'):
            await self.send(['/问 选二'])
        self.assertEqual(self.current_query().selected.entity_id,self.repo.songs[1].id)

    def setUp(self):
        self.setup_repo()
        self.store = ContextStore()
        self.gate = QueryGate(2, 4)
        self.sequencer = ReplySequencer(enabled=False)
        # Exercise native complete-list preparation and receipts without fetching
        # real assets for the synthetic song IDs used by this fixture.
        for target, options in (
            # Force a real pixel-budget split instead of depending on an
            # implementation-specific preferred page height.
            ('ournotes_bot.rendering.song_layout.MAX_IMAGE_PIXELS', {'new':1760*3600}),
            ('ournotes_bot.application.replies.visual_asset', {'return_value':None}),
            ('ournotes_bot.sources.haneoka.game_assets.asset_bytes', {'return_value':None}),
            ('ournotes_bot.application.replies.load_chart_data', {'side_effect':ChartDataError('fixture has no chart')})):
            source=patch(target,**options)
            source.start()
            self.addCleanup(source.stop)
        ai = patch.object(self.parser, "_request", side_effect=AssertionError("model called"))
        ai.start()
        self.addCleanup(ai.stop)

    def current_query(self):
        current = self.store.get(self.key)
        return current.query_context if isinstance(current, DeliveryCursor) else current

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
        self.assertEqual(self.current_query().page, 1)
        other = await self.send(["/问 下一页"], self.message("u2"))
        self.assertEqual(other._api.post_group_message.await_args.kwargs["content"], MISSING)
        await self.send(["/问 下一页"])
        self.assertEqual(self.current_query().page, 1)
        await self.send(["/问 选二"])
        self.assertEqual(self.current_query().selected.entity_id, self.repo.songs[1].id)

    async def test_missing_failure_and_exception_receipts_do_not_commit(self):
        for receipt in ({}, {"code": 1}, {"id": ""}):
            await self.send(["/查曲 MyGO"], self.message(receipt=receipt))
            cursor = self.store.get(self.key)
            if receipt.get("code"):
                self.assertIsNone(cursor)  # Explicit rejection is safe to retry.
            else:
                self.assertIsInstance(cursor, DeliveryCursor)
                self.assertTrue(cursor.uncertain)
                followup = await self.send(["/继续"])
                self.assertIn("发送回执不确定", followup._api.post_group_message.await_args.kwargs["content"])
        await self.send(["/查曲 MyGO"])
        message = self.message()
        message._api.post_group_message.side_effect = TimeoutError("private")
        await self.send(["/问 下一页"], message)
        self.assertEqual(self.current_query().page, 1)
        message._api.post_group_message.assert_awaited_once()
        message = self.message()
        message._api.post_group_message.return_value = None
        await self.send(["/问 下一页"], message)
        self.assertEqual(self.current_query().page, 1)

    async def test_upload_fallback_confirms_the_same_captured_selection(self):
        message = self.message()
        message._api._http.request.side_effect = TimeoutError()
        await self.send(["/查曲 MyGO"], message)
        self.assertEqual(self.store.get(self.key).sent, 0)
        await self.send(['/继续'])
        self.assertEqual(self.store.get(self.key).query_context.visible[0].entity_id, self.repo.songs[0].id)
        self.assertEqual(message._api.post_group_message.await_args.kwargs["msg_type"], 0)

    async def test_multiple_lists_and_same_message_chain(self):
        await self.send(["/查曲 MyGO", "/查曲 100001"])
        self.assertIsNone(self.current_query())
        self.assertFalse(self.store.get(self.key).remaining)
        message = await self.send(["/查曲 MyGO", "/问 下一页"])
        self.assertIsNone(self.current_query())
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
        pending = asyncio.create_task(self.send(["/问 下一页"], message))
        await entered.wait()
        await self.send(["/查曲 100002"])
        release.set()
        await pending
        self.assertEqual(self.store.get(self.key).visible[0].entity_id, 100002)

    async def test_same_user_next_pages_are_serial_and_cancel_keeps_previous(self):
        await self.send(["/查曲 MyGO"])
        await asyncio.gather(self.send(["/问 下一页"]), self.send(["/问 下一页"]))
        self.assertEqual(self.current_query().page, 1)

    async def test_queued_old_followup_cannot_attach_to_new_full_query(self):
        await self.send(["/查曲 MyGO"])
        entered, release = asyncio.Event(), asyncio.Event()
        first = self.message()
        async def sending(**_):
            entered.set()
            await release.wait()
            return {"id": "ack"}
        first._api.post_group_message.side_effect = sending
        running = asyncio.create_task(self.send(["/问 下一页"], first))
        await entered.wait()
        queued_message = self.message()
        queued = asyncio.create_task(self.send(["/问 下一页"], queued_message))
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
        message = await self.send(["/问 下一页"])
        self.assertEqual(message._api.post_group_message.await_args.kwargs["content"], MISSING)
        release.set()
        await pending
        self.assertEqual(self.current_query().page, 1)

    async def test_cancel_during_send_keeps_previous_page(self):
        await self.send(["/查曲 MyGO 页3"])
        entered = asyncio.Event()
        message = self.message()
        async def send(**_):
            entered.set()
            await asyncio.Event().wait()
        message._api.post_group_message.side_effect = send
        pending = asyncio.create_task(self.send(["/问 上一页"], message))
        await entered.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertEqual(self.current_query().page, 1)

    async def test_identity_missing_fails_closed_and_current_query_independent(self):
        message = self.message()
        message.author.member_openid = None
        self.assertIsNone(context_key(message, "g1", True))
        await self.send(["/查曲 MyGO"], message)
        self.assertIsNone(self.store.get(self.key))
        await self.send(["/查曲 MyGO 颜色=蓝 EX"])
        await self.send(["/查曲 100001"])
        self.assertEqual(self.store.get(self.key).query, "/查曲 100001")


class BackendContinuationTests(Fixture, unittest.IsolatedAsyncioTestCase):
    """The HTTP boundary must retain the same visible-context ownership rules."""

    async def asyncSetUp(self):
        import time
        from ournotes_bot.application.backend import Backend
        self.setup_repo()
        self.now = time.time()
        self.backend = Backend(self.repo, self.parser, Settings('', '', self.repo.data_base, self.repo.cache_file, 6),
                               instance_id='fixture', app_id='app',
                               ledger_path=self.repo.cache_file.with_name('delivery.sqlite3'), clock=lambda: self.now)
        self.addAsyncCleanup(self.backend.close)
        self.backend.ready = True
        await self.backend.register('e1')
        self.backend.gate.prepare = AsyncMock(return_value=PreparedReply('captured', context=self.context()))

    def request(self, msg, seq):
        from ournotes_bot.application.backend_protocol import PROTOCOL, CAPABILITY, digest
        return dict(protocol=PROTOCOL, request_id=digest(['fixture', 'app', 'qq', 'group', 'g1', 'u1', msg]),
                    instance_id='fixture', app_id='app', platform='qq', kind='group', conversation_id='g1',
                    user_id='u1', message_id=msg, timestamp_ms=int(self.now*1000), body='/查曲 MyGO',
                    capability=CAPABILITY, gateway_epoch='e1', ingress_seq=seq)

    async def prepare(self, request):
        await self.backend.submit(request)
        for _ in range(100):
            value = self.backend.lookup(request['request_id'], 'e1')
            if value['status'] == 'prepared':
                return value
            await asyncio.sleep(.005)
        self.fail('backend did not prepare the captured result')

    def event(self, plan, kind):
        from ournotes_bot.application.backend_protocol import PROTOCOL
        event = dict(protocol=PROTOCOL, request_id=plan['request_id'], plan_version=1,
                     gateway_epoch='e1', event=kind, part_id='0', attempt_id='a1')
        if kind == 'ack':
            event.update(result='success', message_id='accepted')
        return self.backend.apply_event(plan['delivery_id'], event)

    async def test_disposed_ack_records_fact_without_committing_when_retire_was_lost(self):
        from ournotes_bot.application.backend_protocol import PROTOCOL
        plan = await self.prepare(self.request('disposed', 1))
        self.event(plan, 'intent')
        result = self.backend.apply_event(plan['delivery_id'], dict(protocol=PROTOCOL, request_id=plan['request_id'],
            plan_version=1, gateway_epoch='e1', event='ack', part_id='0', attempt_id='a1', result='success',
            message_id='delivered-before-close', allow_context=False))
        self.assertEqual(result['parts'][0]['status'], 'success')
        self.assertEqual(result['status'], 'aborted')
        self.assertIsNone(self.backend.contexts.get(self.key))

    async def test_retiring_gateway_does_not_reactivate_it_or_commit_late_ack(self):
        from ournotes_bot.application.backend_protocol import ProtocolError
        plan = await self.prepare(self.request('retired', 1))
        self.event(plan, 'intent')
        self.backend.ledger.retire('e1')
        with self.assertRaisesRegex(ProtocolError, 'retired_epoch'):
            await self.backend.register('e1')
        self.event(plan, 'ack')
        self.assertIsNone(self.backend.contexts.get(self.key))

    async def test_http_admission_reordering_rejects_old_new_request_before_begin(self):
        from ournotes_bot.application.backend_protocol import ProtocolError
        newer = self.request('B', 2)
        plan = await self.prepare(newer)
        lease = self.backend.works[plan['delivery_id']].lease
        with self.assertRaisesRegex(ProtocolError, 'stale_admission'):
            await self.backend.submit(self.request('A', 1))
        self.assertEqual(self.backend.works[plan['delivery_id']].lease, lease)
        duplicate = await self.backend.submit(newer)
        self.assertEqual(duplicate['delivery_id'], plan['delivery_id'])
        self.backend.gate.prepare.assert_awaited_once()

    async def test_confirmed_ack_does_not_recommit_or_replace_newer_generation(self):
        first = await self.prepare(self.request('A', 1))
        self.assertEqual(self.event(first, 'intent')['permission'], 'granted')
        self.assertEqual(self.event(first, 'intent')['permission'], 'existing')
        newer = self.request('B', 2)
        await self.backend.submit(newer)
        self.event(first, 'ack')
        self.assertIsNone(self.backend.contexts.get(self.key))
        second = await self.prepare(newer)
        self.event(second, 'intent')
        self.event(second, 'ack')
        current = self.backend.contexts.get(self.key)
        self.assertIsNotNone(current)
        self.assertTrue(self.event(first, 'ack')['duplicate'])
        self.assertIs(self.backend.contexts.get(self.key), current)

    async def test_new_gateway_cannot_replay_or_commit_old_delivery(self):
        request = self.request('A', 1)
        plan = await self.prepare(request)
        self.event(plan, 'intent')
        await self.backend.register('e2')
        self.event(plan, 'ack')
        self.assertIsNone(self.backend.contexts.get(self.key))
        replay = await self.backend.submit({**request, 'gateway_epoch': 'e2'})
        self.assertFalse(replay['send_allowed'])
        self.assertEqual(replay['parts'][0]['status'], 'success')
        self.backend.gate.prepare.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
