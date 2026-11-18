import asyncio
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from aiohttp.test_utils import TestClient, TestServer

from ournotes_bot.application.backend import create_app
from ournotes_bot.application.backend_protocol import Request, digest
from ournotes_bot.application.hermes_chat import HermesChat
import test_console_policies as fixtures


TOKEN = 'x' * 32


class CompactStandbyTests(unittest.IsolatedAsyncioTestCase):
    async def test_replenish_after_active_worker_exits(self):
        from unittest.mock import AsyncMock, Mock
        from ournotes_bot.application.hermes_standby import StandbyLane
        worker = Mock(alive=True, close=AsyncMock())
        prepare = AsyncMock(return_value=worker)
        lane = StandbyLane(prepare, 2, compact=True)
        lane.start()
        try:
            await asyncio.gather(*lane.initializing)
            acquired = await lane.claim(asyncio.get_running_loop().time()+2, priority=1, wait=False)
            self.assertIs(acquired, worker)
            self.assertFalse(lane.initializing)
            self.assertFalse(lane.ready)
            await lane.release(acquired)
            await asyncio.gather(*lane.initializing)
            self.assertEqual(prepare.await_count, 2)
            self.assertEqual(len(lane.ready), 1)
        finally:
            await lane.stop()


class HermesScratchTests(unittest.TestCase):
    def test_only_owned_runtime_scratch_is_reaped_on_restart(self):
        with TemporaryDirectory() as parent:
            scratch = Path(parent)
            old = scratch / ('run-' + '0' * 32 + '-scratch1')
            old.mkdir()
            (old / 'worker.log').write_text('stale synthetic transcript', encoding='utf-8')
            unrelated = scratch / 'unrelated'
            unrelated.mkdir()
            (unrelated / 'note.txt').write_text('retain', encoding='utf-8')
            owner = HermesChat(Path(sys.executable), scratch)
            self.addCleanup(owner.close)
            self.assertTrue(owner.available)
            self.assertFalse(old.exists())
            self.assertTrue((unrelated / 'note.txt').is_file())
            other = HermesChat(Path(sys.executable), scratch)
            self.addCleanup(other.close)
            self.assertFalse(other.available)
            owner.close()


class HermesRouteTests(fixtures.Fixture, unittest.IsolatedAsyncioTestCase):
    Helpers = fixtures.ChatServiceIntegrationTests.Helpers
    request = fixtures.ChatServiceIntegrationTests.request
    result = fixtures.ChatServiceIntegrationTests.result
    event = fixtures.ChatServiceIntegrationTests.event
    observation = fixtures.ChatServiceIntegrationTests.observation

    async def asyncSetUp(self):
        await fixtures.ChatServiceIntegrationTests.asyncSetUp(self)
        self.runtime = self.backend.model_runtime
        self.runtime.hermes.close()
        hermes_python = os.getenv('TAKI_TEST_HERMES_PYTHON')
        scratch = Path(os.getenv('TAKI_TEST_HERMES_SCRATCH', str(self.repo.cache_file.parent)))
        self.scratch = scratch / uuid4().hex
        self.runtime.hermes = HermesChat(Path(hermes_python) if hermes_python else None, self.scratch)
        self.console = TestClient(TestServer(create_app(self.backend, TOKEN)), headers={
            'Authorization': 'Bearer ' + TOKEN, 'X-Taki-Instance': 'fixture', 'X-Taki-Epoch': 'e1',
        })
        await self.console.start_server()
        self.addAsyncCleanup(self.console.close)

    async def group_settings(self, group='g1'):
        response = await self.console.get(f'/v1/admin/chat/groups/{group}/settings')
        self.assertEqual(response.status, 200, await response.text())
        return await response.json()

    async def select(self, enabled, *, group='g1', current=None, basic_only=False):
        current = current or await self.group_settings(group)
        settings = {**current['settings'], 'hermes_enabled': enabled}
        if basic_only:
            del settings['hermes_enabled']
        response = await self.console.post(f'/v1/admin/chat/groups/{group}/settings', json={
            'operation_id': uuid4().hex, 'guard': current['guard'], 'settings': settings,
        })
        return response.status, await response.json()

    def decide(self, request_id, challenge, allow):
        decision = {key: value for key, value in challenge.items() if key not in ('remaining_ms', 'admission_policy_version')}
        decision.update(decision='allow' if allow else 'deny', reason='fixture', ttl_ms=1000,
                        current_policy_version={'epoch': 'fixture', 'global_revision': 0, 'group_revision': 0})
        self.backend.permissions.decide(request_id, challenge['attempt_id'], decision, 'e1')

    async def wait_for_plan(self, request_id, allow):
        attempts = []
        deadline = asyncio.get_running_loop().time() + 65
        while asyncio.get_running_loop().time() < deadline:
            plan = self.backend.lookup(request_id, 'e1')
            challenge = plan.get('pending_model_permission')
            if challenge and challenge['attempt_id'] not in attempts:
                attempts.append(challenge['attempt_id'])
                self.decide(request_id, challenge, allow(len(attempts)))
            if plan['status'] in ('prepared', 'finished', 'expired'):
                return plan, attempts
            self.assertEqual(plan['status'], 'preparing', plan)
            await asyncio.sleep(.02)
        self.fail('Taki did not prepare a plan within its execution budget')

    async def prepare_native(self, body, *, seq=1, allow=lambda _: True):
        raw = self.request('native' + str(seq), seq)
        raw['body'] = body
        await self.backend.submit(raw)
        return await self.wait_for_plan(raw['request_id'], allow)

    async def candidate(self, observation, serial=1):
        raw = {**observation, 'trigger': 'proactive', 'proactive_id': format(serial, '032x')}
        raw['request_id'] = digest(['fixture', 'app', 'qq', 'group', 'g1', raw['user_id'],
                                    raw['message_id'], raw['proactive_id']])
        request = Request.parse(raw, 'fixture', 'app', self.now)
        candidate = self.agent.proactive.candidate(request)
        self.assertIsNotNone(candidate)
        await self.backend.submit(raw, proactive=candidate)
        plan, attempts = await self.wait_for_plan(raw['request_id'], lambda _: True)
        return plan, attempts, candidate

    async def test_disabled_default_and_reject_enable_without_runtime(self):
        self.runtime.hermes.close()
        current = await self.group_settings()
        self.assertFalse(current['settings']['hermes_enabled'])
        self.assertFalse(current['hermes_available'])
        status, _ = await self.select(True, current=current)
        self.assertEqual(status, 409)
        self.assertEqual((await self.group_settings())['guard'], current['guard'])
        self.assertEqual((await self.select(False, current=current, basic_only=True))[0], 200)

    async def test_replaced_cold_worker_stops_before_provider_dispatch(self):
        from unittest.mock import patch
        fixtures.ChatServiceIntegrationTests.enable_lightweight_fact_fixture(self)
        entered = asyncio.Event()
        async def wait_for_worker(work, profile, body, deadline, validate, dispatch):
            entered.set()
            await work.ended.wait()
            validate()
        def observe(mid, seq, text):
            raw = self.request(mid, seq)
            raw.update(trigger='observation', body=text)
            accepted, _, _ = self.agent.proactive.gateway_observation(Request.parse(raw, 'fixture', 'app', self.now))
            self.assertTrue(accepted)
            return raw
        with patch.object(self.runtime.admin, 'hermes_route', return_value=True), patch.object(
                self.runtime.hermes, 'request', side_effect=wait_for_worker):
            old = observe('first', 1, '大家排练时先练哪一段？')
            self.tick += 1.6
            raw = {**old, 'trigger': 'proactive', 'proactive_id': 'a' * 32}
            raw['request_id'] = digest(['fixture','app','qq','group','g1',raw['user_id'],'first',raw['proactive_id']])
            candidate = self.agent.proactive.candidate(Request.parse(raw, 'fixture', 'app', self.now))
            admitted = await self.backend.submit(raw, proactive=candidate)
            work = self.backend.works[admitted['delivery_id']]
            try:
                await asyncio.wait_for(entered.wait(), 3)
                self.now += .1
                observe('replacement', 2, '换个话题，大家排练完会一起吃饭吗？')
                self.assertTrue(work.ended.is_set())
                self.assertEqual(work.terminal_reason, 'observation_replaced')
                self.assertEqual(work.model_attempts, 0)
                self.client.request.assert_not_awaited()
            finally:
                work.ended.set()

    def check_scratch_empty(self):
        self.assertLessEqual({path.name for path in self.scratch.iterdir()}, {'.owner.sqlite3', '.owner.sqlite3-journal'})

    @unittest.skipUnless(os.getenv('TAKI_TEST_HERMES_PYTHON'), 'requires pinned Hermes Python')
    async def test_single_group_pilot_switches_falls_back_and_rolls_back(self):
        from time import monotonic
        from unittest.mock import patch
        from ournotes_bot.query.continuation import ContextKey

        hermes = self.runtime.hermes
        self.agent.store.clock = lambda: self.now
        hermes.pilot_group = 'g1'
        self.assertFalse(hermes.dialogue.enabled)
        self.assertEqual((await self.select(True))[0], 200)
        self.assertFalse((await self.group_settings('g2'))['settings']['hermes_enabled'])
        async def ready():
            while len(hermes.dialogue.ready)!=2 or len(hermes.proactive.ready)!=2:
                await asyncio.sleep(.025)
        await asyncio.wait_for(ready(), 55)
        self.client.request.assert_not_awaited()
        self.assertTrue(all(worker.assignment is None
                            for lane in (hermes.dialogue,hermes.proactive) for worker in lane.ready))

        self.client.request.return_value = self.result('先练尾段。')
        direct, permits = await self.prepare_native('/聊 今晚先练哪一段')
        self.assertTrue(direct['send_allowed'], direct)
        self.assertEqual(len(permits), 1)
        self.assertTrue(self.backend.works[direct['delivery_id']].hermes_pilot)
        self.assertFalse(self.backend.works[direct['delivery_id']].hermes_native_fallback)
        self.event(direct, 'intent')
        self.event(direct, 'ack')

        self.now += .02
        self.agent.services.policy.return_value = None
        self.client.request.return_value = self.result('{"participation":"reply","text":"尾段先过一遍"}')
        observed = self.observation('那尾段先过一遍？', seq=2)
        self.tick += 1.6
        publish = self.backend.ledger.prepared

        def near_cutoff(delivery, *args, **kwargs):
            self.backend.works[delivery].hermes_send_deadline = monotonic()+.4
            return publish(delivery, *args, **kwargs)

        with patch.object(self.backend.ledger, 'prepared', side_effect=near_cutoff):
            continued, permits, _ = await self.candidate(observed)
        self.assertTrue(continued['send_allowed'], continued)
        self.assertEqual(len(permits), 1)
        self.assertIsNotNone(self.backend.works[continued['delivery_id']].hermes_send_deadline)
        self.event(continued, 'intent')
        await asyncio.sleep(.45)
        self.assertFalse(self.backend.lookup(continued['request_id'], 'e1')['send_allowed'])
        receipt = self.event(continued, 'ack')
        self.assertTrue(receipt['recorded'])
        self.assertEqual(receipt['status'], 'finished')
        scope = Request.parse(observed, 'fixture', 'app', self.now).scope
        self.assertTrue(any(row['role']=='assistant' and '尾段先过一遍' in row['text']
                            for row in self.agent.store.recent(scope)))

        self.now += .02
        observed = self.observation('那下一遍还用同一个速度？', seq=3)
        self.tick += 1.6
        baseline = self.client.request.await_count
        lock = self.backend.contexts.lock(ContextKey('qq','group','g1','chat-agent'))
        await asyncio.wait_for(lock.acquire(), 2)
        try:
            with patch('ournotes_bot.application.backend.HERMES_PILOT_CONTINUATION_SECONDS', .15):
                expired, permits, _ = await self.candidate(observed, 2)
        finally:
            lock.release()
        self.assertEqual(expired['status'], 'expired')
        self.assertEqual(permits, [])
        self.assertEqual(self.client.request.await_count, baseline)

        self.now += 181
        self.tick += 181
        self.current_user = 'u2'
        self.agent.services.policy.return_value = self.policy
        hermes.proactive.capacity = 0
        baseline = self.client.request.await_count
        observed = self.observation('群友今天排练顺利', seq=4)
        self.tick += 1.6
        skipped, permits, _ = await self.candidate(observed, 3)
        self.assertEqual(skipped['parts'], [])
        self.assertEqual(permits, [])
        self.assertEqual(self.client.request.await_count, baseline)
        events = self.backend.diagnostics.read(skipped['request_id'])['chat_events']
        self.assertTrue(any(e['data'].get('status')=='hermes_proactive_busy' for e in events))

        self.now += .02
        self.client.request.return_value = self.result('备用节奏是五拍。')
        hermes.dialogue.capacity = 0
        started = monotonic()
        fallback, permits = await self.prepare_native('/聊 再确认备用节奏', seq=5)
        self.assertGreaterEqual(monotonic()-started, 1.95)
        self.assertTrue(fallback['send_allowed'], fallback)
        self.assertTrue(self.backend.works[fallback['delivery_id']].hermes_native_fallback)
        self.assertEqual(self.client.request.await_count, baseline+1)
        self.assertEqual(len(permits), 1)
        self.assertEqual(self.runtime.usage.get(permits[0])['state'], 'settled')
        self.event(fallback, 'intent')
        self.event(fallback, 'ack')

        self.now += .02
        hermes.dialogue.capacity = 2
        entered, release = asyncio.Event(), asyncio.Event()

        async def in_flight(*args, **kwargs):
            entered.set()
            await release.wait()
            return self.result('关闭后丢弃这条回复。')

        self.client.request.side_effect = in_flight
        raw = self.request('pilot-disable', 6)
        raw['body'] = '/聊 关闭时正在生成的合成请求'
        queued = await self.backend.submit(raw)
        work = self.backend.works[queued['delivery_id']]
        try:
            attempt = await self.grant_provider_permit(raw['request_id'], work, [])
            await asyncio.wait_for(entered.wait(), 5)
            self.assertEqual((await self.select(False))[0], 200)
            self.assertFalse(hermes.dialogue.enabled or hermes.proactive.enabled)
            self.check_scratch_empty()
            self.assertFalse(self.backend.lookup(raw['request_id'], 'e1')['send_allowed'])
            self.assertEqual(self.runtime.usage.get(attempt)['state'], 'started')
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(self.backend.models.tasks), return_exceptions=True), 5)
        self.assertEqual(self.runtime.usage.get(attempt)['state'], 'settled')
        self.client.request.side_effect = None

        self.now += .02
        direct, _ = await self.prepare_native('/聊 关闭后走原路径', seq=7)
        self.assertTrue(direct['send_allowed'])
        self.assertFalse(self.backend.works[direct['delivery_id']].hermes_chat)

    @unittest.skipUnless(os.getenv('TAKI_TEST_HERMES_PYTHON'), 'requires pinned Hermes Python')
    async def test_enabled_group_uses_hermes_for_named_continued_and_proactive_chat(self):
        self.assertEqual((await self.select(True))[0], 200)
        visited = []
        worker = self.runtime.hermes.worker

        async def observe(run, *args):
            visited.append((run.work.continuation, run.work.proactive is not None))
            return await worker(run, *args)

        self.runtime.hermes.worker = observe
        self.client.request.return_value = self.result('可以，先说具体问题。')
        direct, _ = await self.prepare_native('/聊 讲一下这件事')
        self.assertTrue(self.backend.works[direct['delivery_id']].hermes_chat)
        self.event(direct, 'intent')
        self.event(direct, 'ack')
        self.now += .01
        self.agent.services.policy.return_value = None
        self.client.request.return_value = self.result('{"participation":"reply","text":"接着说明第一点"}')
        next_message = self.observation('先说第一点', seq=2)
        self.tick += 1.6
        followup, permits, _ = await self.candidate(next_message)
        followup_work = self.backend.works[followup['delivery_id']]
        self.assertTrue(followup_work.continuation and followup_work.hermes_chat)
        self.assertTrue(followup['send_allowed'])
        self.assertEqual(len(permits), 1)
        self.event(followup, 'intent')
        self.event(followup, 'ack')
        self.tick += 181
        self.now += 181
        self.agent.services.policy.return_value = self.policy
        self.client.request.return_value = self.result('{"participation":"skip"}')
        next_message = self.observation('群友之间的闲聊', seq=3)
        self.tick += 1.6
        proactive, permits, _ = await self.candidate(next_message, 2)
        self.assertEqual(proactive['parts'], [])
        self.assertEqual(len(permits), 1)
        self.assertEqual(self.client.request.await_count, 3)
        self.assertEqual(visited, [(False, False), (True, True), (False, True)])
        self.check_scratch_empty()

    @unittest.skipUnless(os.getenv('TAKI_TEST_HERMES_PYTHON'), 'requires pinned Hermes Python')
    async def test_group_switch_rollback_and_legacy_settings_payload(self):
        self.client.request.return_value = self.result('路由夹具')
        original, _ = await self.prepare_native('/聊 默认路径')
        self.assertTrue(original['send_allowed'])
        self.assertFalse(self.backend.works[original['delivery_id']].hermes_chat)
        current = await self.group_settings()
        self.assertTrue(current['hermes_available'])
        status, saved = await self.select(True, current=current)
        self.assertEqual(status, 200, saved)
        self.assertFalse(self.backend.lookup(original['request_id'], 'e1')['send_allowed'])
        self.assertFalse((await self.group_settings('g2'))['settings']['hermes_enabled'])
        self.assertEqual((await self.select(False, current=current))[0], 409)
        _, saved = await self.select(False, current=saved, basic_only=True)
        self.assertTrue(saved['settings']['hermes_enabled'])
        raw = self.runtime.admin.db.execute("SELECT value FROM chat_settings WHERE group_id='g1'").fetchone()[0]
        self.assertNotIn('hermes_enabled', raw)

        plan, attempts = await self.prepare_native('/聊 Hermes 路径', seq=2)
        self.assertTrue(plan['send_allowed'], plan)
        self.assertEqual(plan['parts'][0]['text'], '路由夹具')
        self.assertTrue(self.backend.works[plan['delivery_id']].hermes_chat)
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.runtime.usage.get(attempts[0])['charged'], '14')
        self.check_scratch_empty()

        status, saved = await self.select(False)
        self.assertEqual(status, 200, saved)
        self.assertFalse(saved['settings']['hermes_enabled'])
        self.assertFalse(self.backend.lookup(plan['request_id'], 'e1')['send_allowed'])
        final, _ = await self.prepare_native('/聊 手动回退', seq=3)
        self.assertEqual(final['parts'][0]['text'], '路由夹具')
        self.assertFalse(self.backend.works[final['delivery_id']].hermes_chat)
        self.assertEqual(self.client.request.await_count, 3)
        self.check_scratch_empty()

    @unittest.skipUnless(os.getenv('TAKI_TEST_HERMES_PYTHON'), 'requires pinned Hermes Python')
    async def test_real_hermes_failure_dump_is_deleted(self):
        self.assertEqual((await self.select(True))[0], 200)
        original = self.runtime.hermes.worker
        marker = 'synthetic-hermes-retention-marker'
        dumped = []

        async def observe(run, directory, *args):
            try:
                return await original(run, directory, *args)
            finally:
                for path in (directory / 'hermes' / 'sessions').glob('request_dump_*.json'):
                    dumped.append(marker in path.read_text(encoding='utf-8'))

        self.runtime.hermes.worker = observe
        _, permits = await self.prepare_native('/聊 ' + marker, allow=lambda _: False)
        self.assertEqual(dumped, [True])
        self.assertEqual(len(permits), 1)
        self.assertEqual(self.runtime.usage.get(permits[0])['state'], 'released')
        self.client.request.assert_not_awaited()
        self.check_scratch_empty()

    async def grant_provider_permit(self, request_id, work, granted):
        deadline = asyncio.get_running_loop().time()+20
        while True:
            challenge = self.backend.permissions.challenge_view(work)
            if challenge and challenge['attempt_id'] not in granted:
                break
            self.assertLess(asyncio.get_running_loop().time(), deadline)
            await asyncio.sleep(.01)
        self.decide(request_id, challenge, True)
        attempt = challenge['attempt_id']
        granted.append(attempt)
        return attempt

    @unittest.skipUnless(os.getenv('TAKI_TEST_HERMES_PYTHON'), 'requires pinned Hermes Python')
    async def test_memory_withdraw_waits_for_worker_cleanup_without_waiting_for_provider(self):
        status, _ = await self.select(True)
        self.assertEqual(status, 200)
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_provider(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return self.result('撤回后禁止送达')

        self.client.request.side_effect = slow_provider
        raw = self.request('withdraw-fixture', 1)
        raw['body'] = '/聊 临时留存夹具'
        await self.backend.submit(raw)
        work = next(work for work in self.backend.works.values() if work.request.request_id == raw['request_id'])
        try:
            attempt = await self.grant_provider_permit(raw['request_id'], work, [])
            await asyncio.wait_for(entered.wait(), 5)
            workers = list(self.scratch.glob('run-*'))
            self.assertEqual(len(workers), 1)
            self.assertTrue((workers[0] / 'worker.log').is_file())
            self.assertTrue((workers[0] / 'hermes' / 'sessions').is_dir())

            response = await asyncio.wait_for(self.console.post('/v1/admin/chat/groups/g1/memory',
                json={'action': 'delete', 'message_ids': [raw['message_id']]}), 5)
            self.assertEqual(response.status, 200, await response.text())
            self.check_scratch_empty()
            self.assertFalse(self.backend.lookup(raw['request_id'], 'e1')['send_allowed'])
            self.assertEqual(self.runtime.usage.get(attempt)['state'], 'started')
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(self.backend.models.tasks), return_exceptions=True), 5)
        self.assertEqual(self.runtime.usage.get(attempt)['charged'], '14')
        self.assertNotIn('撤回后禁止送达', str(self.backend.lookup(raw['request_id'], 'e1')))
        self.check_scratch_empty()

    @unittest.skipUnless(os.getenv('TAKI_TEST_HERMES_PYTHON'), 'requires pinned Hermes Python')
    async def test_story_withdraw_evicts_the_inflight_worker_that_used_it(self):
        import json
        from ournotes_bot.application.library_admin import LibraryAdmin
        from ournotes_bot.domain.model_calls import ToolCall
        from test_platform_boundary import StoryPublicationTests

        fixture = StoryPublicationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.publish()
        self.agent.story = fixture.service
        guard = LibraryAdmin(self.backend).guard('story')
        handle = fixture.service.search('立希')['items'][0]['handle']
        results = iter((
            self.result(calls=(ToolCall('s', 'story_search', '{"query":"立希"}'),)),
            self.result(calls=(ToolCall('r', 'story_read', json.dumps({'handle': handle})),)),
        ))
        entered, release = asyncio.Event(), asyncio.Event()

        async def respond(*_args, **_kwargs):
            result = next(results, None)
            if result:
                return result
            entered.set()
            await release.wait()
            return self.result('已撤回的剧情内容不得发送')

        self.client.request.side_effect = respond
        self.assertEqual((await self.select(True))[0], 200)
        raw = self.request('story-withdraw-fixture', 1)
        raw['body'] = '/聊 查立希的剧情并解释'
        plan = await self.backend.submit(raw)
        work = self.backend.works[plan['delivery_id']]
        granted = []
        try:
            for _ in range(3):
                await self.grant_provider_permit(raw['request_id'], work, granted)
            await asyncio.wait_for(entered.wait(), 5)
            response = await asyncio.wait_for(self.console.post('/v1/admin/library/withdraw', json={
                'kind': 'story', 'id': 'test-story', 'reason': 'synthetic fixture',
                'guard': guard, 'confirm_withdraw': True,
            }), 5)
            self.assertEqual(response.status, 200, await response.text())
            self.check_scratch_empty()
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(self.backend.models.tasks), return_exceptions=True), 5)
        self.assertNotIn('已撤回的剧情内容不得发送', str(self.backend.lookup(raw['request_id'], 'e1')))
        self.assertEqual([self.runtime.usage.get(row)['charged'] for row in granted], ['14']*3)
