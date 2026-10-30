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

    def test_mirror_chart_survives_difficulty_and_detail_followups(self):
        context = self.context("/查谱面 1 EX 镜像")
        self.assertTrue(context.query.chart_mirror)
        result, context = execute_followup(context, Operation("difficulty", "HARD"), self.repo)
        self.assertTrue(result.spec.chart_mirror)
        self.assertEqual(result.chart[1][0].difficulty, "HARD")
        with patch("ournotes_bot.application.replies.load_chart_score", return_value={"notes": []}), \
             patch("ournotes_bot.rendering.chart_layout.render", return_value=(b"chart",)) as render:
            from ournotes_bot.application.replies import complete_chart_reply
            reply = complete_chart_reply(result, self.repo, "zh", context)
            self.assertTrue(render.call_args.kwargs["mirror"])
            self.assertIn("镜像谱面", reply.text)
        result, _ = execute_followup(context, Operation("detail"), self.repo)
        self.assertTrue(result.spec.chart_mirror)
        self.assertEqual(result.chart[1][0].difficulty, "HARD")
        ordinary = self.context("/查谱面 1 EX")
        result, _ = execute_followup(ordinary, Operation("difficulty", "HARD"), self.repo)
        self.assertFalse(result.spec.chart_mirror)

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
        self.backend.reference_profiles = frozenset({'fixture'})
        await self.backend.register('e1')
        self.backend.gate.prepare = AsyncMock(return_value=PreparedReply('captured', context=self.context()))

    def request(self, msg, seq):
        from ournotes_bot.application.backend_protocol import PROTOCOL, CAPABILITY, digest
        return dict(protocol=PROTOCOL, request_id=digest(['fixture', 'app', 'qq', 'group', 'g1', 'u1', msg]),
                    instance_id='fixture', app_id='app', platform='qq', kind='group', conversation_id='g1',
                    user_id='u1', message_id=msg, timestamp_ms=int(self.now*1000), body='/查曲 MyGO',
                    capability=CAPABILITY, gateway_epoch='e1', ingress_seq=seq,trigger='command',reference=None,interaction=None,
                    policy=dict(natural_query='model',epoch='fixture',global_revision=0,group_revision=0))

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
                     gateway_epoch='e1', event=kind, part_id=plan['parts'][0]['id'], attempt_id='a1')
        if kind == 'ack':
            event.update(result='success', message_id='accepted-'+plan['request_id'][:8])
        return self.backend.apply_event(plan['delivery_id'], event)

    def assert_native_image(self,plan):
        self.assertEqual(plan['parts'][0]['kind'],'image')
        self.assertFalse(plan.get('processing_hint',False))
        return plan

    async def test_group_images_use_five_slots_without_synthetic_intro_or_page_loss(self):
        pages=tuple(PreparedReply('page-'+str(i),image=b'fixture',context=self.context()) for i in range(6))
        self.backend.gate.prepare.return_value=PreparedReply('',pages=pages,context=self.context())
        first=await self.prepare(self.request('images',1))
        self.assertEqual(len(first['parts']),5)
        first=self.assert_native_image(first)
        self.assertFalse(self.backend.conversations.anchors)
        sent=[]
        for part in first['parts']:
            current={**first,'parts':[part]}
            sent.append(self.backend.works[first['delivery_id']].prepared[part['position']].text.split('\n结果 ',1)[0])
            self.event(current,'intent');self.event(current,'ack')
        followup=self.request('rest-images',2);followup['body']='/继续'
        second=await self.prepare(followup)
        self.assertEqual(len(second['parts']),1)
        second=self.assert_native_image(second)
        sent.extend(self.backend.works[second['delivery_id']].prepared[p['position']].text.split('\n结果 ',1)[0] for p in second['parts'])
        self.assertEqual(sent,['page-'+str(i) for i in range(6)])
        self.backend.gate.prepare.assert_awaited_once()

    async def test_private_image_does_not_add_group_introduction(self):
        from ournotes_bot.application.backend_protocol import digest
        self.backend.gate.prepare.return_value=PreparedReply('private',image=b'fixture')
        request=self.request('private-image',1)
        request.update(kind='c2c',conversation_id='u1',request_id=digest(['fixture','app','qq','c2c','u1','u1','private-image']))
        plan=await self.prepare(request)
        self.assertEqual(len(plan['parts']),1)
        self.assertEqual(plan['parts'][0]['kind'],'image')
        self.assertFalse(plan.get('processing_hint',False))

    async def test_real_slow_hint_keeps_four_image_slots_and_all_remaining_pages(self):
        import time
        entered, held = asyncio.Event(), asyncio.Event()
        pages = tuple(PreparedReply('page-'+str(i), image=b'fixture', context=self.context()) for i in range(6))
        async def prepare(*args, **kwargs):
            entered.set()
            await held.wait()
            return PreparedReply('', pages=pages, context=self.context())
        self.backend.gate.prepare.side_effect = prepare
        request = self.request('slow-images', 1)
        task = asyncio.create_task(self.prepare(request))
        await asyncio.wait_for(entered.wait(), 1)
        work = next(iter(self.backend.works.values()))
        work.model_started_at = time.monotonic()-9
        hint = self.backend.lookup(request['request_id'], 'e1')
        self.assertEqual(len(hint['parts']), 1)
        self.assertEqual(hint['parts'][0]['business_status'], 'processing_hint')
        self.event(hint, 'intent'); self.event(hint, 'ack')
        self.assertFalse(self.backend.conversations.anchors)
        held.set()
        result = await task
        self.assertEqual([p['kind'] for p in result['parts']], ['text', 'image', 'image', 'image', 'image'])
        self.assertEqual(result['parts'][0]['status'], 'success')
        remaining = work.prepared[-1].context.remaining
        self.assertEqual(len(remaining), 2)
        self.assertEqual([p.text for p in remaining], ['page-4', 'page-5'])

    async def test_long_fallback_notice_keeps_continue_anchor_without_selectable_rows(self):
        self.backend.gate.prepare.return_value=PreparedReply('captured text '*200,image=b'fixture',context=self.context())
        first=await self.prepare(self.request('fallback',1))
        first=self.assert_native_image(first)
        first=self.event(first,'upload_failed')
        self.event(first,'intent');self.event(first,'ack')
        followup=self.request('continue',2);followup['body']='/继续'
        second=await self.prepare(followup)
        self.assertIn('captured text',second['parts'][0]['text'])
        anchor=self.backend.conversations.resolve(self.backend.works[second['delivery_id']].request)
        self.assertEqual(anchor.page,-1)
        self.assertIsNone(anchor.context.query_context)
        self.assertIsNone(self.backend.conversations.page(anchor,1))

    async def test_group_fallback_between_1500_and_1800_keeps_all_text_for_continue(self):
        text='文'*1600
        self.backend.gate.prepare.return_value=PreparedReply(text,image=b'fixture',context=self.context())
        first=await self.prepare(self.request('mention-fallback',1))
        first=self.assert_native_image(first)
        first=self.event(first,'upload_failed')
        self.assertIn('/继续',first['parts'][0]['text'])
        self.event(first,'intent');self.event(first,'ack')
        followup=self.request('mention-continue',2);followup['body']='/继续'
        second=await self.prepare(followup)
        self.assertEqual(len(second['parts']),2)
        self.assertTrue(all(len(p['text'])<=1500 for p in second['parts']))
        self.assertEqual(''.join(p['text'].split('\n结果 ',1)[0] for p in second['parts']),text)
        self.backend.gate.prepare.assert_awaited_once()

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

    async def test_independent_topics_do_not_replace_each_other_and_duplicate_ack_is_inert(self):
        first = await self.prepare(self.request('A', 1))
        first_key = self.backend.works[first['delivery_id']].key
        self.assertEqual(self.event(first, 'intent')['permission'], 'granted')
        self.assertEqual(self.event(first, 'intent')['permission'], 'existing')
        newer = self.request('B', 2)
        await self.backend.submit(newer)
        self.event(first, 'ack')
        self.assertIsNone(self.backend.contexts.get(self.key))
        second = await self.prepare(newer)
        second_key = self.backend.works[second['delivery_id']].key
        self.event(second, 'intent')
        self.event(second, 'ack')
        current = self.backend.contexts.get(second_key)
        self.assertIsNotNone(current)
        self.assertIsNotNone(self.backend.contexts.get(first_key))
        self.assertNotEqual(first_key, second_key)
        self.assertTrue(self.event(first, 'ack')['duplicate'])
        self.assertIs(self.backend.contexts.get(second_key), current)

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

    def quote(self, msg, seq, plan, body='/问 下一页', user='u1', group='g1'):
        from ournotes_bot.application.backend_protocol import digest
        value = self.request(msg, seq)
        value.update(body=body, user_id=user, conversation_id=group, trigger='quoted_reply',
                     reference={'message_id':'accepted-'+plan['request_id'][:8], 'adapter_profile_version':'fixture'})
        value['request_id'] = digest(['fixture','app','qq','group',group,user,msg])
        return value

    async def test_quote_shares_only_confirmed_same_group_context(self):
        first = await self.prepare(self.request('A',1))
        self.event(first,'intent');self.event(first,'ack')
        same = self.quote('B',2,first,body='/问 选 1',user='u2')
        self.backend.gate.prepare_followup=AsyncMock(return_value=PreparedReply('same-topic',context=self.context()))
        second=await self.prepare(same)
        self.assertEqual(second['parts'][0]['text'],'same-topic')
        self.assertEqual(self.backend.works[second['delivery_id']].key.user, first['request_id'])
        cross=await self.prepare(self.quote('C',3,first,user='u2',group='other'))
        self.assertEqual(cross['parts'][0]['text'],MISSING)

    async def test_snapshot_paging_does_not_read_repository_after_update(self):
        ctx=self.context()
        self.backend.gate.prepare=AsyncMock(return_value=PreparedReply('',pages=(
            PreparedReply('frozen-page-one',context=ctx),PreparedReply('frozen-page-two',context=ctx)),context=ctx))
        first=await self.prepare(self.request('A',1))
        self.event(first,'intent');self.event(first,'ack')  # Only page one is confirmed.
        followup=self.quote('B',2,first)
        await self.backend.submit(followup)
        await asyncio.sleep(.02)
        self.assertEqual(self.backend.lookup(followup['request_id'],'e1')['status'],'preparing')
        self.event(first,'aborted')  # End the predecessor; the unsent second page stays captured.
        self.repo.songs.reverse()
        self.backend.gate.prepare_followup=AsyncMock(side_effect=AssertionError('must not refetch'))
        second=await self.prepare(followup)
        self.assertIn('frozen-page-two',second['parts'][0]['text'])
        self.backend.gate.prepare_followup.assert_not_awaited()

    async def test_meta_entry_ack_pages_and_selection_keep_resource_snapshot(self):
        from ournotes_bot.domain.meta_query import MetaSnapshot, MetaSong, MetaSource
        from ournotes_bot.rendering.readable_pages import ImagePart
        songs = tuple(MetaSong(str(i), f'intl-{i}', (5,), 'fixture', '', float(i), ((),)*4) for i in range(10,20))
        snapshot = MetaSnapshot(MetaSource('intl','fixture-release','fixture-source',1),songs)
        request = self.request('meta',1);request['body']='/问 国际服最短的前10首'
        with patch.object(self.repo.song_metrics,'current',return_value=snapshot) as source, \
             patch('ournotes_bot.rendering.song_metrics_layout.render', return_value=(ImagePart(b'first',0,5),ImagePart(b'second',5,10))):
            first = await self.prepare(request)
        source.assert_called_once_with('intl')
        first=self.assert_native_image(first)
        self.event(first,'intent');self.event(first,'ack');self.event(first,'aborted')
        saved = self.backend.conversations.snapshots[first['delivery_id']]
        self.assertEqual(saved.layout_version,'song-meta/1')
        with patch.object(self.repo.song_metrics,'current',side_effect=AssertionError('must not refetch')):
            second = await self.prepare(self.quote('meta-page',2,first))
            second=self.assert_native_image(second)
            self.event(second,'intent');self.event(second,'ack')
            anchor = next(a for a in self.backend.conversations.anchors.values() if a.page==1)
            context = getattr(anchor.context,'query_context',anchor.context)
            self.assertEqual(context.visible_indices,tuple(range(6,11)))
            self.assertNotIsInstance(execute_followup(context,Operation('select',1),self.repo),tuple)
            result, detail = execute_followup(context,Operation('select',6),self.repo)
            self.assertEqual(result.meta.rows[0].song.song_id,'15')
            self.assertEqual(result.meta.source,snapshot.source)
            self.assertEqual(detail.meta.source.server,'intl')

    async def test_confirmed_page_exposes_only_its_global_indices_and_detail_keeps_selected_entity(self):
        context=capture_context(resolve_command('/查曲 MyGO',self.repo),self.repo,complete=True)
        self.backend.gate.prepare.return_value=PreparedReply('rows 17 to 32',context=context,visible_indices=tuple(range(17,33)))
        plan=await self.prepare(self.request('page',1));self.event(plan,'intent');self.event(plan,'ack')
        from ournotes_bot.application.backend_protocol import Request
        request=Request.parse(self.quote('select',2,plan), 'fixture','app',self.now,reference_profiles={'fixture'})
        anchor=self.backend.conversations.resolve(request)
        self.assertEqual(anchor.context.visible_indices,tuple(range(17,33)))
        result,_=execute_followup(anchor.context,Operation('select',18),self.repo)
        self.assertEqual(result.chart[0].id,self.repo.songs[17].id)
        self.assertNotIsInstance(execute_followup(anchor.context,Operation('select',1),self.repo),tuple)
        selected=replace(context,selected=context.visible[17])
        self.backend.gate.prepare.return_value=PreparedReply('selected detail',context=selected)
        detail=await self.prepare(self.request('detail',3));self.event(detail,'intent');self.event(detail,'ack')
        request=Request.parse(self.quote('difficulty',4,detail),'fixture','app',self.now,reference_profiles={'fixture'})
        anchor=self.backend.conversations.resolve(request)
        self.assertEqual(anchor.context.visible,(selected.selected,))
        self.assertEqual(anchor.context.selected,selected.selected)

    async def test_unconfirmed_quote_cannot_see_pending_snapshot(self):
        first=await self.prepare(self.request('A',1))
        second=await self.prepare(self.quote('B',2,first))
        self.assertEqual(second['parts'][0]['text'],MISSING)

    async def test_each_attempt_requires_bound_current_permission(self):
        from ournotes_bot.application.backend_protocol import ProtocolError
        import time
        first=await self.prepare(self.request('A',1));work=self.backend.works[first['delivery_id']]
        task=asyncio.create_task(self.backend.permissions.acquire(work,time.monotonic()+30))
        await asyncio.sleep(0)
        challenge=self.backend.permissions.challenge_view(work)
        body={k:v for k,v in challenge.items() if k not in ('remaining_ms','admission_policy_version')}
        body.update(decision='allow',current_policy_version=challenge['admission_policy_version'],reason='fixture',ttl_ms=1000)
        with self.assertRaisesRegex(ProtocolError,'permission_identity'):
            self.backend.permissions.decide(work.request.request_id,body['attempt_id'],{**body,'nonce':'wrong'},'e1')
        self.backend.permissions.decide(work.request.request_id,body['attempt_id'],body,'e1')
        self.assertTrue(self.backend.permissions.decide(work.request.request_id,body['attempt_id'],body,'e1')['duplicate'])
        self.assertGreater(await task,time.monotonic())
        next_task=asyncio.create_task(self.backend.permissions.acquire(work,time.monotonic()+30));await asyncio.sleep(0)
        next_challenge=self.backend.permissions.challenge_view(work)
        self.assertNotEqual(challenge['attempt_id'],next_challenge['attempt_id'])
        self.assertTrue(self.backend.permissions.decide(work.request.request_id,body['attempt_id'],body,'e1')['duplicate'])
        self.assertFalse(next_task.done())  # Recording the old fact cannot grant the new attempt.
        denial={k:v for k,v in next_challenge.items() if k not in ('remaining_ms','admission_policy_version')}
        denial.update(decision='deny',current_policy_version=next_challenge['admission_policy_version'],reason='revoked',ttl_ms=1000)
        self.backend.permissions.decide(work.request.request_id,denial['attempt_id'],denial,'e1')
        with self.assertRaisesRegex(ProtocolError,'policy_denied'):await next_task
        with self.assertRaisesRegex(ProtocolError,'model_attempt_limit'):
            await self.backend.permissions.acquire(work,time.monotonic()+30)

    async def test_hung_model_permission_does_not_block_direct_query(self):
        import time
        from ournotes_bot.natural_query.query_agent import DeferredModel,AgentState,AgentOutcome,TerminalOutcome
        from ournotes_bot.natural_query.query_validation import OutcomeCode
        from ournotes_bot.domain.query_policy import CURRENT_MODEL_START
        def local(content,repo):return DeferredModel(AgentState(content,time.monotonic(),time.monotonic()+30),True)
        def resume(deferred,repo):
            CURRENT_MODEL_START.get()()()
            deferred.state.final_code=OutcomeCode.SUCCESS
            return AgentOutcome('model-fixture',TerminalOutcome(OutcomeCode.SUCCESS,'model-fixture'),deferred.state)
        self.backend.parser=SimpleNamespace(prepare_local=local,resume_model=resume)
        request=self.request('AI',1);request.update(body='/问 fixture question',trigger='ask')
        await self.backend.submit(request)
        for _ in range(100):
            pending=self.backend.lookup(request['request_id'],'e1')
            if pending.get('pending_model_permission'):break
            await asyncio.sleep(.005)
        else:self.fail('model permission challenge was never published')
        self.assertEqual(self.backend.gate._pending,0)
        direct=await self.prepare(self.request('direct',2))
        self.assertTrue(direct['send_allowed'])
        self.event(direct,'intent');self.event(direct,'ack')
        self.assertEqual(self.backend.lookup(direct['request_id'],'e1')['status'],'finished')
        self.assertEqual(self.backend.lookup(request['request_id'],'e1')['status'],'preparing')
        challenge=pending['pending_model_permission']
        body={k:v for k,v in challenge.items() if k not in ('remaining_ms','admission_policy_version')}
        body.update(decision='allow',current_policy_version=challenge['admission_policy_version'],reason='fixture',ttl_ms=1000)
        with patch('ournotes_bot.application.replies.render_prepared',return_value=PreparedReply('model-fixture')):
            self.backend.permissions.decide(request['request_id'],body['attempt_id'],body,'e1')
            model=await self.prepare(request)
        self.assertEqual(model['parts'][0]['text'],'model-fixture')

    async def test_permission_expiry_and_retirement_are_closed(self):
        from ournotes_bot.application.backend_protocol import ProtocolError
        now=[1.0];self.backend.permissions.clock=lambda:now[0]
        plan=await self.prepare(self.request('A',1));work=self.backend.works[plan['delivery_id']]
        task=asyncio.create_task(self.backend.permissions.acquire(work,30));await asyncio.sleep(0)
        challenge=self.backend.permissions.challenge_view(work)
        body={k:v for k,v in challenge.items() if k not in ('remaining_ms','admission_policy_version')}
        body.update(decision='allow',current_policy_version=challenge['admission_policy_version'],reason='fixture',ttl_ms=1000)
        now[0]=5
        with self.assertRaisesRegex(ProtocolError,'permission_expired'):
            self.backend.permissions.decide(work.request.request_id,body['attempt_id'],body,'e1')
        await self.backend.register('e2')
        with self.assertRaisesRegex(ProtocolError,'inactive_request'):await task

    async def test_legacy_ack_records_fact_but_cannot_restore_context(self):
        plan=await self.prepare(self.request('A',1));self.event(plan,'intent')
        key=self.backend.works[plan['delivery_id']].key
        result=self.backend.apply_event(plan['delivery_id'],dict(protocol={'major':1,'minor':1},request_id=plan['request_id'],
            plan_version=1,gateway_epoch='e1',event='ack',part_id='0',attempt_id='a1',result='success',message_id='old-receipt'))
        self.assertEqual(result['parts'][0]['status'],'success')
        self.assertIsNone(self.backend.contexts.get(key))


class AbsoluteLifetimeTests(Fixture,unittest.TestCase):
    def setUp(self):self.setup_repo()

    def test_followup_commit_and_reads_cannot_extend_original_deadline(self):
        now=[0.0];store=ContextStore(clock=lambda:now[0]);ticket=store.begin(self.key)
        now[0]=10;self.assertTrue(store.commit(ticket,self.context(),confirmed=True))
        now[0]=599;ticket=store.begin(self.key,clear=False)
        self.assertTrue(store.commit(ticket,self.context(),confirmed=True))
        self.assertEqual(store.deadline(self.key),600)
        now[0]=600;store.sweep()
        self.assertEqual(store._entries,{})
        self.assertFalse(store.commit(ticket,self.context(),confirmed=True))

    def test_successful_plan_and_state_expire_without_another_query(self):
        agent=self.parser._agent;now=[0.0];agent.clock=lambda:now[0]
        self.parser.answer_with_outcome('/问 查曲 MyGO',self.repo)
        self.assertIsNotNone(self.parser.command_for('/问 查曲 MyGO'))
        now[0]=599;self.assertIsNotNone(self.parser.state_for('/问 查曲 MyGO'))
        now[0]=600;agent.sweep()
        self.assertEqual(agent._cache,{})
        self.assertEqual(agent._states,{})
        self.assertIsNone(self.parser.command_for('/问 查曲 MyGO'))

    def test_runtime_scopes_never_share_question_plans_or_states(self):
        from ournotes_bot.domain.query_policy import CURRENT_QUERY_SCOPE
        a=CURRENT_QUERY_SCOPE.set(('instance','topic-a'))
        try:
            self.parser.answer_with_outcome('/问 查曲 MyGO',self.repo)
            self.assertIsNotNone(self.parser.state_for('/问 查曲 MyGO'))
            self.assertIsNone(self.parser.command_for('/问 查曲 MyGO'))
        finally:CURRENT_QUERY_SCOPE.reset(a)
        b=CURRENT_QUERY_SCOPE.set(('instance','topic-b'))
        try:self.assertIsNone(self.parser.state_for('/问 查曲 MyGO'))
        finally:CURRENT_QUERY_SCOPE.reset(b)


class ChatMemoryStoreTests(unittest.TestCase):
    """C3: real scoped SQLite, receipt provenance and deletion/restore boundaries."""
    def setUp(self):
        from ournotes_bot.application.chat_store import ChatStore
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'chat.sqlite3';self.now=[1793270000.0]
        self.scope=dict(instance_id='fixture',app_id='app',platform='qq',kind='group',conversation_id='g1')
        self.store=ChatStore(self.path,'fixture\0app',clock=lambda:self.now[0])
        self.addCleanup(lambda:self.store.close())

    def message(self,mid='u1-message',text='我更喜欢鼓和现场音乐',*,speaker='u1',role='user',stamp=None):
        return dict(message_id=mid,speaker_id=speaker,text=text,timestamp_ms=int(self.now[0]*1000) if stamp is None else stamp,
                    role=role,reply_to=None,display_name='同一个显示名')

    def begin(self,run='run1',messages=None,scope=None,**kwargs):
        import json
        scope=scope or self.scope
        request=SimpleNamespace(scope=scope,timestamp_ms=int(self.now[0]*1000),trigger='ask')
        work=SimpleNamespace(delivery=run,request=request,model_snapshot=SimpleNamespace(profile=SimpleNamespace(config_version='fixture-v1')))
        messages=messages or [self.message()]
        self.store.begin(work,[dict(role=message['role'],content=json.dumps(message,ensure_ascii=False)) for message in messages],**kwargs)
        return request

    def delivered(self,run='run1',mid='assistant1',text='我也喜欢鼓。',status='success',allow=True):
        self.store.event(run,'delivery/'+mid,'delivery',dict(message_id=mid,text=text,status=status,allow_context=allow))

    def test_only_explicit_participation_and_success_receipts_become_memory(self):
        self.begin('ignored',participating=False)
        self.assertEqual(self.store.status(self.scope)['messages'],0)
        self.begin(messages=[self.message(),self.message('fake','未发出的模型历史',role='assistant',speaker='Taki')])
        self.store.generated('run1','尚未发送的可见生成正文')
        self.delivered(mid='uncertain',status='uncertain')
        self.delivered(mid='denied',allow=False)
        self.assertEqual([r['message_id'] for r in self.store.recent(self.scope)],['u1-message'])
        self.delivered();self.delivered()
        self.assertEqual([r['message_id'] for r in self.store.recent(self.scope)],['u1-message','assistant1'])
        self.assertEqual(self.store.read(self.scope,['fake','uncertain','denied'])['status'],'no_results')

    def test_same_name_and_message_id_remain_scoped_and_speakers_distinct(self):
        self.begin(messages=[self.message(),self.message('u2-message','我喜欢提前规划',speaker='u2')])
        other={**self.scope,'conversation_id':'g2'}
        self.begin('other',messages=[self.message(text='另一个群的秘密')],scope=other)
        rows=self.store.read(self.scope,['u1-message','u2-message'])['messages']
        self.assertEqual([r['speaker_id'] for r in rows],['u1','u2'])
        self.assertEqual(len({r['display_name'] for r in rows}),1)
        self.assertEqual(self.store.search(self.scope,'秘密')['status'],'no_results')
        self.assertEqual(self.store.read(other,['u1-message'])['messages'][0]['text'],'另一个群的秘密')
        with self.assertRaisesRegex(ValueError,'chat_memory_identity'):
            self.store.search({**self.scope,'instance_id':'other'},'秘密')

    def test_cjk_substrings_short_terms_and_fts_operator_text_are_data(self):
        self.begin(messages=[self.message(text='喜欢即兴鼓演奏和长期规划')])
        self.assertEqual(self.store.search(self.scope,'鼓演奏')['status'],'ok')
        self.assertEqual(self.store.search(self.scope,'即兴')['status'],'ok')
        self.assertEqual(self.store.search(self.scope,'鼓')['status'],'ok')
        self.assertEqual(self.store.search(self.scope,'鼓 OR another_scope')['status'],'no_results')
        with self.assertRaisesRegex(ValueError,'chat_memory_query'):self.store.search(self.scope,'x'*121)

    def test_delete_source_cascades_replies_summaries_index_and_c1_blobs(self):
        self.begin();self.delivered()
        first=self.store.read(self.scope,['u1-message','assistant1'])['messages']
        self.now[0]+=1
        self.begin('run2',messages=[*first,self.message('u2-message','再解释一下',speaker='u2')])
        self.delivered('run2','assistant2','这是承接前面观点的解释。')
        self.store.put_summary(self.scope,'summary','大家讨论了鼓与现场表现',['u1-message','assistant1'])
        self.assertEqual(len(self.store.summaries(self.scope)),1)
        self.store.delete_messages(self.scope,['u1-message'])
        self.assertEqual([r['message_id'] for r in self.store.recent(self.scope)],['u2-message'])
        self.assertEqual(self.store.summaries(self.scope),[])
        self.assertEqual(self.store.search(self.scope,'解释')['messages'][0]['message_id'],'u2-message')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_fts WHERE kind=\'summary\'').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM runs').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM events').fetchone()[0],0)

    def test_expiry_uses_oldest_source_and_summary_never_renews_retention(self):
        from ournotes_bot.application.chat_store import RETENTION_SECONDS
        old=int((self.now[0]-RETENTION_SECONDS+1)*1000)
        self.begin(messages=[self.message(stamp=old)])
        summary=self.store.put_summary(self.scope,'summary','一条刚生成的旧来源摘要',['u1-message'])
        self.assertEqual(summary['expires_at_ms'],old+RETENTION_SECONDS*1000)
        self.now[0]+=2
        self.assertEqual(self.store.summaries(self.scope),[])
        self.assertEqual(self.store.search(self.scope,'现场')['status'],'no_results')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM memory_fts').fetchone()[0],0)

    def test_backup_restore_applies_independent_journal_before_read(self):
        import sqlite3,shutil
        from contextlib import closing
        from ournotes_bot.application.chat_store import ChatStore
        self.begin();self.delivered()
        backup=self.path.with_name('backup.sqlite3')
        with closing(sqlite3.connect(backup)) as target:self.store.db.backup(target)
        self.store.delete_messages(self.scope,['u1-message'])
        self.store.close();shutil.copyfile(backup,self.path)
        self.store=ChatStore(self.path,'fixture\0app',clock=lambda:self.now[0])
        self.assertEqual(self.store.recent(self.scope),[])
        self.assertEqual(self.store.summaries(self.scope),[])
        self.assertEqual(self.store.status(self.scope)['message_tombstones'],1)

    def test_clear_group_preserves_other_group_and_accepts_later_new_messages(self):
        other={**self.scope,'conversation_id':'g2'}
        self.begin();self.delivered();self.begin('other',scope=other)
        self.store.clear_scope(self.scope)
        self.assertEqual(self.store.recent(self.scope),[])
        self.assertEqual(len(self.store.recent(other)),1)
        self.begin('repeated',messages=[self.message()])
        self.assertEqual(self.store.recent(self.scope),[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM runs WHERE id=\'repeated\'').fetchone()[0],0)
        self.now[0]+=1;self.begin('new',messages=[self.message('new')])
        self.assertEqual([r['message_id'] for r in self.store.recent(self.scope)],['new'])

    def test_corrupt_or_wrong_identity_deletion_journal_fails_closed(self):
        from ournotes_bot.application.chat_store import ChatStore
        self.begin();self.store.delete_messages(self.scope,['u1-message'])
        self.store.close()
        journal=self.path.with_suffix('.deletions.json')
        journal.write_text('broken',encoding='utf-8')
        with self.assertRaises(ValueError):ChatStore(self.path,'fixture\0app',clock=lambda:self.now[0])
        journal.unlink()
        self.store=ChatStore(self.path,'fixture\0app',clock=lambda:self.now[0])
        state=self.store.deletion_state();state['identity']='another-instance'
        with self.assertRaisesRegex(ValueError,'chat_deletion_journal'):self.store.restore_deletions(state)

    def test_c1_migration_keeps_meta_contract_and_backfills_only_receipted_assistant(self):
        import sqlite3,json
        from contextlib import closing
        from ournotes_bot.application.chat_store import ChatStore
        self.store.close()
        path=self.path.with_name('c1.sqlite3')
        messages=[self.message(),self.message('fake','不应承认这段假历史',role='assistant',speaker='Taki')]
        context=[dict(role=m['role'],content=json.dumps(m,ensure_ascii=False)) for m in messages]
        with closing(sqlite3.connect(path)) as db,db:
            db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.executemany('INSERT INTO meta VALUES (?,?)',[('schema','chat/1'),('identity','fixture\0app')])
            db.execute('CREATE TABLE runs(id TEXT PRIMARY KEY,scope TEXT,source_time REAL,trigger TEXT,persona TEXT,profile TEXT,context TEXT,visible TEXT,status TEXT)')
            db.execute('CREATE TABLE events(run_id TEXT,event_id TEXT,kind TEXT,data TEXT,PRIMARY KEY(run_id,event_id))')
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',('old',json.dumps(self.scope),self.now[0],'ask','v0','fixture',json.dumps(context),'生成内容','finished'))
            db.execute('INSERT INTO events VALUES (?,?,?,?)',('old','delivery/1','delivery',json.dumps(dict(message_id='actual',text='实际送达',status='success',allow_context=True))))
            follow_context=[dict(role='assistant',content=json.dumps(self.message('actual','实际送达',role='assistant',speaker='Taki'),ensure_ascii=False)),
                            dict(role='user',content=json.dumps(self.message('follow-user','追问一下'),ensure_ascii=False))]
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',('a-follow-before-old',json.dumps(self.scope),self.now[0],'ask','v0','fixture',json.dumps(follow_context),'追问回答','finished'))
            db.execute('INSERT INTO events VALUES (?,?,?,?)',('a-follow-before-old','delivery/1','delivery',json.dumps(dict(message_id='follow-answer',text='承接前面的回答',status='success',allow_context=True))))
        self.store=ChatStore(path,'fixture\0app',clock=lambda:self.now[0])
        self.assertEqual(dict(self.store.db.execute('SELECT key,value FROM meta')),{'schema':'chat/1','identity':'fixture\0app'})
        self.assertEqual({r['message_id'] for r in self.store.recent(self.scope)},{'u1-message','actual','follow-user','follow-answer'})
        self.assertEqual(self.store.read(self.scope,['fake'])['status'],'no_results')
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0],2)
        self.store.delete_messages(self.scope,['actual'])
        self.assertEqual(self.store.read(self.scope,['follow-answer'])['status'],'no_results')

    def test_summary_missing_or_deleted_sources_cannot_be_published(self):
        self.begin()
        with self.assertRaisesRegex(ValueError,'chat_memory_source_missing'):
            self.store.put_summary(self.scope,'summary','缺来源',['missing'])
        self.store.delete_messages(self.scope,['u1-message'])
        with self.assertRaisesRegex(ValueError,'chat_memory_source_missing'):
            self.store.put_summary(self.scope,'summary','已删除来源',['u1-message'])

    def test_c1_rollback_new_runs_are_imported_on_rollforward(self):
        import sqlite3,json
        from contextlib import closing
        from ournotes_bot.application.chat_store import ChatStore
        self.begin();self.delivered();self.store.close()
        context=[dict(role='user',content=json.dumps(self.message('during-rollback','回退期间的参与消息'),ensure_ascii=False))]
        # The unchanged C1 writes only its original runs/events tables.
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)',('rollback-run',json.dumps(self.scope),self.now[0],'ask','v0','fixture',json.dumps(context),'回退回答','finished'))
            db.execute('INSERT INTO events VALUES (?,?,?,?)',('rollback-run','delivery/1','delivery',json.dumps(dict(message_id='rollback-answer',text='回退时确实送达',status='success',allow_context=True))))
        self.store=ChatStore(self.path,'fixture\0app',clock=lambda:self.now[0])
        self.assertEqual(self.store.read(self.scope,['during-rollback','rollback-answer'])['status'],'ok')
        self.assertEqual(len(self.store.read(self.scope,['during-rollback','rollback-answer'])['messages']),2)

    def test_future_memory_schema_is_rejected_without_rewriting_it(self):
        import sqlite3
        from contextlib import closing
        from ournotes_bot.application.chat_store import ChatStore
        self.store.close()
        with closing(sqlite3.connect(self.path)) as db,db:db.execute('PRAGMA user_version=3')
        with self.assertRaisesRegex(ValueError,'chat_memory_version'):ChatStore(self.path,'fixture\0app')
        with closing(sqlite3.connect(self.path)) as db,db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],3)
            db.execute('PRAGMA user_version=2')
        self.store=ChatStore(self.path,'fixture\0app',clock=lambda:self.now[0])

    def test_context_restores_after_restart_and_invalidates_deleted_cache(self):
        from ournotes_bot.application.chat_context import ChatContext
        self.begin();self.delivered()
        context=ChatContext(store=self.store,clock=lambda:self.now[0])
        request=SimpleNamespace(scope=self.scope,reference=None,message_id='current',user_id='u2',sender_name='同一个显示名',timestamp_ms=int(self.now[0]*1000))
        context.trigger(request,'当前的新问题')
        self.assertEqual(len(context.messages(request)),3)
        self.store.delete_messages(self.scope,['u1-message'])
        self.assertFalse(context.contains(request,'assistant1'))
        self.assertFalse(any('实际' in item['content'] for item in context.messages(request)))

    def test_explicit_older_reference_is_available_only_in_current_scope(self):
        from ournotes_bot.application.chat_context import ChatContext
        self.begin()
        self.now[0]+=1800
        context=ChatContext(store=self.store,clock=lambda:self.now[0])
        request=SimpleNamespace(scope=self.scope,reference={'message_id':'u1-message'},message_id='current',user_id='u2',sender_name='',timestamp_ms=int(self.now[0]*1000))
        context.trigger(request,'引用半小时前的话')
        self.assertTrue(context.contains(request,'u1-message'))
        self.assertEqual(len(context.messages(request)),2)
        other=SimpleNamespace(**{**request.__dict__,'scope':{**self.scope,'conversation_id':'g2'}})
        self.assertFalse(context.contains(other,'u1-message'))
        self.assertEqual(context.messages(other),[])

    def test_initial_unpersisted_trigger_is_not_lost_during_cache_restore(self):
        from ournotes_bot.application.chat_context import ChatContext
        context=ChatContext(store=self.store,clock=lambda:self.now[0])
        request=SimpleNamespace(scope=self.scope,reference=None,message_id='current',user_id='u1',sender_name='',timestamp_ms=int(self.now[0]*1000))
        context.trigger(request,'第一条触发问题')
        self.assertEqual(len(context.messages(request)),1)
        self.assertIn('第一条触发问题',context.messages(request)[0]['content'])


class ProactiveControllerTests(unittest.TestCase):
    def setUp(self):
        from ournotes_bot.application.proactive_chat import ProactiveController, ProactivePolicy
        self.now = [0.0]
        self.Policy = ProactivePolicy
        self.controller = ProactiveController(clock=lambda: self.now[0])
        self.a, self.b = ('qq', 'group', 'a'), ('qq', 'group', 'b')
        self.policy = self.Policy(True, 'approved-group-service', 'admin-approval-17', 6, 3, 1000)
        self.controller.configure(self.a, self.policy)

    def observe(self, message_id='m1', text='我们聊电影吧', *, scope=None, **kwargs):
        from ournotes_bot.domain.chat import ChatMessage
        return self.controller.observe(scope or self.a, ChatMessage(message_id, 'u' + message_id,
            text, int(self.now[0]*1000), display_name='同名'), **kwargs)

    def ready(self, *, reserve=100, scope=None):
        self.now[0] += 1.5
        return self.controller.candidate(scope or self.a, reserve_cost_micro=reserve)

    def test_requires_host_approved_group_service_and_explicit_enable(self):
        for policy in (self.Policy(), self.Policy(True, 'group', '', 6, 3, 1000),
                       self.Policy(True, '', 'approval', 6, 3, 1000),
                       self.Policy(True, 'group', 'approval', 6, 3, 0)):
            self.controller.configure(self.a, policy)
            self.assertFalse(self.observe())
            self.assertIsNone(self.ready())
        self.assertFalse(self.observe(scope=self.b))
        self.controller.configure(self.a, self.policy)
        self.assertTrue(self.observe())
        candidate = self.ready()
        self.assertEqual(candidate.service_subject_id, 'approved-group-service')
        self.assertTrue(self.controller.valid(candidate))
        self.assertFalse(self.controller.can_send(candidate))

    def test_coalesces_without_topic_or_sentiment_filter(self):
        for message_id, text in (('a', '天气真差'), ('b', '这段剧情什么意思'), ('c', '嗯'), ('d', '昨晚的电影')):
            self.assertTrue(self.observe(message_id, text))
            self.assertIsNone(self.controller.candidate(self.a, reserve_cost_micro=100))
            self.now[0] += .3
        self.now[0] += 1.2
        candidate = self.controller.candidate(self.a, reserve_cost_micro=100)
        self.assertEqual([row.message_id for row in candidate.messages], ['a', 'b', 'c', 'd'])
        self.assertEqual(len({row.speaker_id for row in candidate.messages}), 4)
        self.assertIsNone(self.controller.candidate(self.a, reserve_cost_micro=100))

    def test_duplicates_and_bot_messages_do_not_cancel_human_candidate(self):
        self.observe()
        candidate = self.ready()
        self.assertFalse(self.observe())
        self.assertFalse(self.observe('bot', '机器人循环', is_bot=True))
        self.assertTrue(self.controller.valid(candidate))
        self.assertEqual(self.controller.status(self.a)['buffered_messages'], 1)

    def test_new_message_cancels_old_answer_but_stale_call_is_still_charged(self):
        self.observe()
        first = self.ready()
        self.observe('m2', '已经有人回答了')
        self.assertFalse(self.controller.valid(first))
        self.assertFalse(self.controller.decide(first, 'reply', actual_cost_micro=17))
        second = self.ready()
        self.assertIsNotNone(second)
        self.assertEqual(second.messages[-1].text, '已经有人回答了')
        self.assertEqual(self.controller.status(self.a)['hourly_cost_micro'], 117)

    def test_defer_is_version_bound_and_has_at_most_two_rechecks(self):
        self.observe()
        candidate = self.ready()
        self.assertFalse(candidate.deferred)
        for evaluation in (2, 3):
            self.assertTrue(self.controller.decide(candidate, 'defer', actual_cost_micro=10))
            self.now[0] += 1.9
            self.assertIsNone(self.controller.candidate(self.a, reserve_cost_micro=100))
            self.now[0] += .1
            candidate = self.controller.candidate(self.a, reserve_cost_micro=100)
            self.assertTrue(candidate.deferred)
            self.assertEqual(candidate.evaluation, evaluation)
        self.assertTrue(self.controller.decide(candidate, 'defer', actual_cost_micro=10))
        self.now[0] += 20
        self.assertIsNone(self.controller.candidate(self.a, reserve_cost_micro=100))
        self.observe('m2')
        fresh = self.ready()
        self.assertFalse(fresh.deferred)
        self.assertEqual(fresh.evaluation, 1)

    def test_new_message_replaces_deferred_scene_and_skip_does_not_repeat(self):
        self.observe()
        old = self.ready()
        self.controller.decide(old, 'defer', actual_cost_micro=0)
        self.observe('m2', '话题已经变了')
        fresh = self.ready()
        self.assertNotEqual(old.version, fresh.version)
        self.assertFalse(fresh.deferred)
        self.controller.decide(fresh, 'skip', actual_cost_micro=13)
        self.now[0] += 10
        self.assertIsNone(self.controller.candidate(self.a, reserve_cost_micro=100))
        self.assertFalse(self.controller.can_send(fresh))

    def test_explicit_dialogue_has_priority_until_receipt_and_groups_are_isolated(self):
        self.controller.configure(self.b, self.policy)
        self.observe()
        a = self.ready()
        self.observe('b1', scope=self.b)
        b = self.ready(scope=self.b)
        self.controller.set_addressed(self.a, True)
        self.assertFalse(self.controller.valid(a))
        self.assertTrue(self.controller.valid(b))
        self.observe('addressed', addressed=True)
        self.observe('m2')
        self.assertIsNone(self.ready())
        self.controller.set_addressed(self.a, False)
        self.assertIsNone(self.ready())
        self.observe('m3')
        self.assertIsNotNone(self.ready())

    def test_disabled_policy_and_clearing_drop_bodies_without_resetting_cost(self):
        self.observe(text='不能留在诊断中的正文')
        old = self.ready()
        self.controller.decide(old, 'reply', actual_cost_micro=21)
        self.controller.configure(self.a, replace(self.policy, enabled=False))
        self.assertFalse(self.controller.can_send(old))
        self.assertEqual(self.controller.retained_bytes(), 0)
        self.controller.configure(self.a, self.policy)
        self.controller.clear(self.a)
        status = self.controller.status(self.a)
        self.assertEqual((status['hourly_calls'], status['hourly_cost_micro']), (1, 21))
        self.assertNotIn('正文', str(status))

    def test_only_successful_ack_starts_cooldown_and_ack_is_idempotent(self):
        self.observe()
        candidate = self.ready()
        self.controller.decide(candidate, 'reply', actual_cost_micro=15)
        self.assertTrue(self.controller.can_send(candidate))
        self.assertEqual(self.controller.status(self.a)['hourly_replies'], 0)
        self.assertTrue(self.controller.delivered(candidate))
        self.assertFalse(self.controller.delivered(candidate))
        self.observe('m2')
        self.assertIsNone(self.ready())
        self.now[0] += 58.5
        self.assertIsNotNone(self.controller.candidate(self.a, reserve_cost_micro=100))

    def test_cooldown_is_per_group_and_zero_keeps_other_limits(self):
        self.controller.configure(self.a, replace(self.policy, cooldown_seconds=0, max_replies_per_hour=2))
        self.controller.configure(self.b, replace(self.policy, cooldown_seconds=180))
        for scope in (self.a, self.b):
            self.observe('first', scope=scope)
            candidate=self.ready(scope=scope)
            self.controller.decide(candidate,'reply',actual_cost_micro=10)
            self.assertTrue(self.controller.delivered(candidate))
        for scope in (self.a,self.b):
            self.observe('second',scope=scope)
        self.assertIsNone(self.controller.candidate(self.a,reserve_cost_micro=10))
        zero=self.ready(scope=self.a)
        self.assertIsNotNone(zero)
        self.assertIsNone(self.controller.candidate(self.b,reserve_cost_micro=10))
        self.controller.decide(zero,'reply',actual_cost_micro=10)
        self.assertTrue(self.controller.can_send(zero))
        self.controller.delivered(zero)
        self.observe('third')
        self.assertIsNone(self.ready())  # Hourly reply cap still applies with zero cooldown.
        self.now[0]+=180
        self.assertIsNotNone(self.controller.candidate(self.b,reserve_cost_micro=10))

    def test_cooldown_change_cancels_candidate_and_preserves_ack_facts(self):
        self.controller.configure(self.a,replace(self.policy,cooldown_seconds=0))
        self.observe();old=self.ready()
        self.controller.decide(old,'reply',actual_cost_micro=10)
        self.controller.delivered(old)
        self.observe('second');pending=self.ready()
        self.controller.configure(self.a,replace(self.policy,cooldown_seconds=180))
        self.assertFalse(self.controller.can_send(pending))
        self.assertEqual(self.controller.status(self.a)['hourly_replies'],1)
        self.observe('third')
        self.assertIsNone(self.ready())
        self.controller.configure(self.a,replace(self.policy,cooldown_seconds=0))
        self.observe('fourth')
        self.assertIsNotNone(self.ready())
        for value in (-1,True,1.5,86401):
            with self.assertRaisesRegex(ValueError,'proactive_policy_invalid'):
                replace(self.policy,cooldown_seconds=value)

    def test_late_successful_ack_cancels_replacement_and_counts_after_disable(self):
        self.observe()
        old = self.ready()
        self.controller.decide(old, 'reply', actual_cost_micro=10)
        self.observe('m2')
        new = self.ready()
        self.controller.decide(new, 'reply', actual_cost_micro=10)
        self.assertTrue(self.controller.delivered(old))
        self.assertFalse(self.controller.can_send(new))
        self.controller.configure(self.a, replace(self.policy, enabled=False))
        self.assertTrue(self.controller.delivered(new))
        self.assertEqual(self.controller.status(self.a)['hourly_replies'], 2)

    def test_hourly_evaluation_and_cost_budgets_count_skips_and_unknown_usage(self):
        self.controller.configure(self.a, replace(self.policy, max_calls_per_hour=2,
                                                  max_cost_micro_per_hour=150))
        self.observe()
        unknown = self.ready()
        self.controller.decide(unknown, 'skip')
        self.observe('m2')
        self.assertIsNone(self.ready(reserve=100))
        self.assertEqual(self.controller.status(self.a)['incomplete_costs'], 1)
        self.controller.settle(unknown, actual_cost_micro=20)
        second = self.controller.candidate(self.a, reserve_cost_micro=100)
        self.controller.decide(second, 'skip', actual_cost_micro=20)
        self.observe('m3')
        self.assertIsNone(self.ready(reserve=1))
        self.now[0] += 3600
        self.observe('m4')
        self.assertIsNotNone(self.ready(reserve=100))

    def test_unexpected_real_cost_is_recorded_and_conflicting_settlement_rejected(self):
        self.controller.configure(self.a, replace(self.policy, max_cost_micro_per_hour=100))
        self.observe()
        candidate = self.ready(reserve=80)
        self.assertFalse(self.controller.decide(candidate, 'reply', actual_cost_micro=110))
        self.assertFalse(self.controller.can_send(candidate))
        self.assertEqual(self.controller.status(self.a)['hourly_cost_micro'], 110)
        with self.assertRaisesRegex(ValueError, 'cost_conflict'):
            self.controller.settle(candidate, actual_cost_micro=0)
        self.controller.clear(self.a)
        self.assertEqual(self.controller.status(self.a)['hourly_cost_micro'], 110)

    def test_expired_candidate_can_settle_but_never_send(self):
        self.observe()
        candidate = self.ready()
        self.now[0] += 60
        self.assertFalse(self.controller.valid(candidate))
        self.assertFalse(self.controller.decide(candidate, 'reply', actual_cost_micro=17))
        self.assertEqual(self.controller.status(self.a)['hourly_cost_micro'], 17)

    def test_message_count_byte_ttl_and_global_memory_bounds(self):
        for index in range(130):
            self.observe(str(index))
        self.assertEqual(self.controller.status(self.a)['buffered_messages'], 120)
        from ournotes_bot.application.proactive_chat import ProactiveController
        bounded = ProactiveController(clock=lambda: self.now[0], group_bytes=900, max_bytes=1400)
        for scope in (self.a, self.b):
            bounded.configure(scope, self.policy)
            for index in range(3):
                from ournotes_bot.domain.chat import ChatMessage
                bounded.observe(scope, ChatMessage(str(index), 'u', '汉字'*90, 0))
                self.now[0] += .1
        self.assertLessEqual(bounded.retained_bytes(), 1400)
        self.assertTrue(all(bounded.status(scope)['buffered_bytes'] <= 900 for scope in (self.a,self.b)))
        self.now[0] += 900
        bounded.sweep()
        self.controller.sweep()
        self.assertEqual((bounded.retained_bytes(), self.controller.retained_bytes()), (0, 0))

    def test_oversized_new_message_invalidates_incomplete_scene(self):
        self.observe()
        candidate = self.ready()
        self.assertFalse(self.observe('huge', 'a'*(256*1024)))
        self.assertFalse(self.controller.valid(candidate))
        self.assertEqual(self.controller.retained_bytes(), 0)
        self.assertIsNone(self.ready())

    def test_policy_and_decision_validation_rejects_invalid_budget_values(self):
        for kwargs in ({'max_calls_per_hour':0}, {'max_cost_micro_per_hour':True},
                       {'max_replies_per_hour':121}, {'enabled':1}):
            with self.assertRaises(ValueError):
                replace(self.policy, **kwargs)
        self.observe()
        candidate = self.ready()
        for decision, fee in (('unknown', 1), ('reply', -1), ('skip', True)):
            with self.assertRaises(ValueError):
                self.controller.decide(candidate, decision, actual_cost_micro=fee)
        self.controller.decide(candidate, 'skip', actual_cost_micro=1)
        with self.assertRaisesRegex(ValueError, 'decision_conflict'):
            self.controller.decide(candidate, 'reply', actual_cost_micro=1)


if __name__ == "__main__":
    unittest.main()
