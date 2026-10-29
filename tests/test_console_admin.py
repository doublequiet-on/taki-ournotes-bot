"""Console read/observation contracts, isolated from QQ, sources and models."""
import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ournotes_bot.application.admin import Admin, delivery_state
from ournotes_bot.application.backend import Backend, create_app
from ournotes_bot.application.backend_protocol import PROTOCOL, CAPABILITY, ProtocolError, digest
from ournotes_bot.application.delivery_ledger import Ledger
from ournotes_bot.application.diagnostics import Diagnostics
from ournotes_bot.application.replies import PreparedReply
from ournotes_bot.config import Settings
from ournotes_bot.natural_query.query_metrics import QueryMetrics


class ProjectionTests(unittest.TestCase):
    def test_meta_observation_is_readonly_and_keeps_failure_visible(self):
        from ournotes_bot.sources.haneoka.song_metrics import SongMetricsRepository
        from ournotes_bot.domain.meta_query import MetaSnapshot,MetaSource,MetaSong,MetaError
        source=SongMetricsRepository(fetch=Mock(side_effect=AssertionError('no network')))
        self.assertEqual(source.status()['servers']['jp']['code'],'not_observed')
        song=MetaSong('1','fixture',(),'', '',90,((('notes',100),),(),(),()))
        source._collect=Mock(return_value=MetaSnapshot(MetaSource('jp','r-fixture','fixture',123),(song,)))
        source.current('jp')
        observed=source.status()['servers']['jp']
        self.assertEqual(observed['observed_at'],123)
        self.assertEqual(observed['field_coverage']['notes'],1)
        self.assertEqual(observed['field_coverage']['duration_seconds'],1)
        source._collect.side_effect=MetaError('source_unavailable','untrusted prose')
        with self.assertRaises(MetaError):source.current('jp')
        self.assertEqual(source.status()['servers']['jp']['code'],'source_unavailable')
        self.assertNotIn('untrusted prose',str(source.status()))
        source.fetch.assert_not_called()

    def test_diagnostic_export_anonymizes_groups_without_mutation(self):
        backend=SimpleNamespace(clock=lambda:123)
        admin=Admin(backend)
        admin.detail=lambda ident: {'group_id':'private-group','parts':[{'group_id':'private-group'}]}
        exported=admin.export('a'*64)
        self.assertNotIn('private-group',json.dumps(exported))
        self.assertEqual(exported['data']['request']['group_id'],exported['data']['request']['parts'][0]['group_id'])

    def test_exclusive_projection_including_zero_parts_and_uncertain(self):
        def state(status, *parts):
            return delivery_state({'status': status}, [{'status': p} for p in parts])
        self.assertEqual(state('finished'), 'closed_unsent')
        self.assertEqual(state('prepared'), 'processing')
        self.assertEqual(state('expired','success','uncertain'), 'partial')
        self.assertEqual(state('expired','uncertain'), 'uncertain')
        self.assertEqual(state('finished','success'), 'complete')
        self.assertEqual(state('finished','failure'), 'failed')

    def test_metrics_read_never_creates_or_disables_writer(self):
        with tempfile.TemporaryDirectory() as folder:
            metrics=QueryMetrics(Path(folder)/'metrics.json')
            self.assertFalse(metrics.snapshot()['available'])
            self.assertFalse(metrics.path.exists())
            metrics.path.write_text('broken')
            self.assertEqual(metrics.snapshot()['reason_code'],'metrics_snapshot_invalid')
            self.assertTrue(metrics.available)


class LedgerCapacityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'ledger.sqlite3'
        self.now = 1_000_000
        self.ledger = None
        self.addCleanup(lambda: self.ledger.close() if self.ledger is not None else None)

    def open(self, **limits):
        self.ledger = Ledger(self.path, 'fixture', 'runtime', self.now, **limits)
        return self.ledger

    def admit(self, index, *, context='context', epoch='epoch', now=None):
        now = self.now if now is None else now
        request = SimpleNamespace(request_id=str(index), content_digest='digest', gateway_epoch=epoch,
                                  context_id=context, ingress_seq=index, timestamp_ms=now*1000)
        self.ledger.admit(request, 'runtime', 'delivery-'+str(index), now)

    def test_defaults_and_legacy_capacity_remain_compatible(self):
        ledger = self.open()
        self.assertEqual(ledger.capacity_status()['limits'], {'requests':4096,'highwater':4096,'epochs':4096})
        self.assertEqual(ledger.retention,86400)
        ledger.close()
        ledger = self.open(capacity=2,request_capacity=4)
        self.assertEqual(ledger.capacity_status()['limits'], {'requests':4,'highwater':2,'epochs':2})

    def test_invalid_limits_fail_before_creating_state(self):
        for name in ('request','context','epoch'):
            for value in (0,-1,True,1.5,'2',2**63):
                with self.subTest(name=name,value=value), self.assertRaisesRegex(ProtocolError,'ledger_'+name+'_capacity_configuration'):
                    self.open(**{name+'_capacity':value})
                self.assertFalse(self.path.exists())
                self.assertFalse(self.path.with_suffix('.lock').exists())

    def test_request_context_and_epoch_gates_are_independent(self):
        ledger = self.open(request_capacity=3,context_capacity=1,epoch_capacity=2)
        self.assertTrue(ledger.register('epoch'))
        self.admit(1)
        self.admit(2)
        with self.assertRaisesRegex(ProtocolError,'context_capacity'):
            self.admit(3,context='another')
        self.admit(3)
        with self.assertRaisesRegex(ProtocolError,'ledger_capacity'):
            self.admit(4)
        self.assertTrue(ledger.register('next-epoch'))
        self.assertFalse(ledger.register('next-epoch'))
        self.assertEqual(ledger.capacity_status()['counts']['highwater'],0)
        with self.assertRaisesRegex(ProtocolError,'retired_epoch'):
            ledger.register('epoch')
        with self.assertRaisesRegex(ProtocolError,'epoch_capacity'):
            ledger.register('third-epoch')
        with self.assertRaisesRegex(ProtocolError,'inactive_epoch'):
            self.admit(4)
        self.assertTrue(ledger.active('next-epoch'))
        self.assertEqual(ledger.capacity_status()['reason_codes'], {'requests':'ledger_capacity','epochs':'epoch_capacity'})

    def test_lower_limit_retains_facts_and_counts_beyond_history_projection(self):
        ledger = self.open(request_capacity=3)
        ledger.register('epoch')
        for index in (1,2,3):
            self.admit(index)
            ledger.prepared('delivery-'+str(index),1)
        ledger.intent('delivery-1','0','attempt')
        ledger.finish('delivery-2','finished')
        ledger.finish('delivery-3','finished')
        ledger.close()
        ledger = self.open(request_capacity=2)
        self.assertEqual(len(ledger.admin_rows()),2)
        before = list(ledger.db.iterdump())
        status = ledger.capacity_status()
        self.assertEqual(status['counts'], {'requests':3,'highwater':0,'epochs':1,'unresolved_requests':1,'unresolved_parts':1})
        self.assertEqual(status['limits']['requests'],2)
        self.assertTrue(status['at_capacity']['requests'])
        self.assertEqual(before,list(ledger.db.iterdump()))
        ledger.register('next-epoch')
        with self.assertRaisesRegex(ProtocolError,'ledger_capacity'):
            self.admit(4,epoch='next-epoch')
        self.assertEqual(ledger.capacity_status()['counts']['requests'],3)
        self.admit(4,epoch='next-epoch',now=self.now+86401)
        self.assertIsNotNone(ledger.request('1'))
        self.assertIsNone(ledger.request('2'))
        self.assertIsNone(ledger.request('3'))
        self.assertEqual(ledger.capacity_status()['counts']['requests'],2)
        self.assertEqual(ledger.parts('delivery-1')[0]['status'],'uncertain')
        self.assertTrue(ledger.ack('delivery-1','0','attempt','success','late-receipt'))
        self.assertFalse(ledger.ack('delivery-1','0','attempt','success','late-receipt'))
        self.assertEqual(ledger.capacity_status()['counts']['unresolved_requests'],0)


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_admitted_initialization_failure_has_stable_terminal_fact(self):
        for index, (owner, method) in enumerate(((self.backend.diagnostics, 'admitted'),
                                               (self.backend.contexts, 'begin')), 1):
            with self.subTest(step=method), patch.object(owner, method, side_effect=RuntimeError('injected')):
                payload = self.payload(index)
                result = await self.backend.submit(payload)
            self.assertEqual(result['status'], 'failed')
            self.assertFalse(result['send_allowed'])
            self.assertFalse(self.backend.works)
            self.assertEqual((await self.backend.submit(payload))['status'], 'failed')
        self.backend.gate.prepare.assert_not_called()

    async def test_model_capture_storage_failure_is_owned_without_retrying_business(self):
        from types import MethodType
        from ournotes_bot.application.model_runtime import ModelRuntime
        snapshot = SimpleNamespace(fingerprint='fixture', profile=SimpleNamespace(config_version=1),
                                   subject=lambda request:'subject')
        snapshot.for_group = lambda group:snapshot
        runtime = SimpleNamespace(agent=None,config=SimpleNamespace(snapshot=lambda:snapshot),
            usage=SimpleNamespace(configuration=Mock(side_effect=sqlite3.OperationalError('injected')),
                                  reconcile=lambda:None), close=AsyncMock())
        runtime.capture = MethodType(ModelRuntime.capture, runtime)
        self.backend.model_runtime = runtime
        payload = self.payload(); payload['body']='/问 fixture'
        result = await self.backend.submit(payload)
        self.assertEqual(result['status'], 'failed')
        self.now += 600
        self.assertEqual((await self.backend.submit(payload))['status'], 'failed')
        self.assertFalse(self.backend.works)
        runtime.usage.configuration.assert_called_once()

    async def test_termination_save_failure_retains_owner_and_recovers_before_other_maintenance(self):
        payload = self.payload()
        with patch.object(self.backend.contexts, 'begin', side_effect=RuntimeError('injected')):
            with patch.object(self.backend.ledger, 'finish', side_effect=sqlite3.OperationalError('disk full')):
                result = await self.backend.submit(payload)
        work = next(iter(self.backend.works.values()))
        self.assertEqual(result['status'], 'preparing')
        self.assertFalse(result['send_allowed'])
        self.assertFalse(work.active)
        self.assertEqual(work.terminal_status, 'failed')
        self.assertFalse(self.backend.accepting())
        with self.assertRaises(ProtocolError) as caught:
            await self.backend.submit(self.payload(2))
        self.assertEqual(caught.exception.code, 'not_ready')
        self.backend.finish(work, 'expired')
        self.assertEqual(work.terminal_status, 'failed')
        self.assertEqual((await self.backend.submit(payload))['status'], 'preparing')
        work.terminal_retry_at = time.monotonic()-1
        with patch.object(self.backend, 'expire', side_effect=sqlite3.OperationalError('other maintenance')):
            self.backend.maintain()
        self.assertFalse(self.backend.works)
        self.assertEqual(self.backend.ledger.request(payload['request_id'])['status'], 'failed')
        self.assertEqual(self.backend.maintenance_status()['reason'], 'maintenance_failed')
        self.backend.maintain()
        self.assertTrue(self.backend.accepting())

    async def test_termination_retries_are_bounded_and_restart_preserves_dedupe(self):
        payload = self.payload()
        with patch.object(self.backend.contexts, 'begin', side_effect=RuntimeError('injected')):
            with patch.object(self.backend.ledger, 'finish', side_effect=sqlite3.OperationalError('disk full')) as save:
                await self.backend.submit(payload)
                work = next(iter(self.backend.works.values()))
                for attempt, delay in enumerate((1, 2, 4), 1):
                    due = work.terminal_retry_at
                    with patch('ournotes_bot.application.backend.time.monotonic', return_value=due-.01):
                        self.backend.retry_terminations()
                    self.assertEqual(save.call_count, attempt)
                    with patch('ournotes_bot.application.backend.time.monotonic', return_value=due):
                        self.backend.retry_terminations()
                    self.assertEqual(save.call_count, attempt+1)
                    if attempt < 3:
                        self.assertEqual(work.terminal_retry_at, due + (2,4)[attempt-1])
                self.assertIsNone(work.terminal_retry_at)
                self.backend.retry_terminations()
                self.assertEqual(save.call_count, 4)
        self.assertEqual(self.backend.maintenance_status()['exhausted_terminations'], 1)
        self.assertFalse(self.backend.accepting())
        await self.backend.close()
        restarted = Backend(self.repo, self.backend.parser, self.backend.settings, instance_id='fixture', app_id='app',
            ledger_path=self.root/'delivery.sqlite3', clock=lambda:self.now)
        self.addAsyncCleanup(restarted.close)
        restarted.ready=True
        await restarted.register('epoch2')
        payload['gateway_epoch']='epoch2'
        result = await restarted.submit(payload)
        self.assertEqual(result['status'], 'expired')
        self.assertFalse(result['send_allowed'])
        self.assertFalse(restarted.tasks)

    async def test_failed_preparation_terminal_save_keeps_owner_after_task_exits(self):
        with patch.object(self.backend.ledger, 'prepared', side_effect=RuntimeError('preparation failure')), \
             patch.object(self.backend.ledger, 'finish', side_effect=sqlite3.OperationalError('disk full')):
            await self.backend.submit(self.payload())
            work = next(iter(self.backend.works.values()))
            await work.task
            await asyncio.sleep(0)
        self.assertTrue(work.terminal_pending)
        self.assertEqual(work.terminal_status, 'failed')
        self.assertIs(self.backend.works[work.delivery], work)
        self.assertFalse(self.backend.accepting())
        work.terminal_retry_at = time.monotonic()-1
        self.backend.retry_terminations()
        self.assertFalse(self.backend.works)

    async def test_expire_then_close_drains_repeated_cancellation_before_storage_close(self):
        await self.assert_thread_drain_on_close('expire')

    async def test_retire_then_close_drains_repeated_cancellation_before_storage_close(self):
        await self.assert_thread_drain_on_close('retire')

    async def test_http_cleanup_cancellation_still_closes_after_observation_thread(self):
        await self.assert_http_cleanup_drain('observation')

    async def test_http_cleanup_cancellation_still_closes_after_initializer(self):
        await self.assert_http_cleanup_drain('initialize')

    async def test_cancelled_initializer_retains_thread_until_http_cleanup_closes(self):
        await self.assert_http_cleanup_drain('cancelled-initialize')

    async def assert_http_cleanup_drain(self, phase):
        import threading
        entered, release, ended = threading.Event(), threading.Event(), threading.Event()
        closed = Mock()
        self.backend.data_owner = SimpleNamespace(close=closed)

        def worker():
            entered.set()
            release.wait(5)
            closed.assert_not_called()
            ended.set()

        initialize = worker if phase != 'observation' else None
        app = create_app(self.backend, 'x'*32, initialize=initialize)
        lifetime = app.cleanup_ctx[0](app)
        shutdown = None
        with patch.object(self.backend.operations, 'flush_notices',
                          side_effect=worker if phase == 'observation' else lambda: None):
            try:
                await lifetime.__anext__()
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                if phase == 'cancelled-initialize':
                    initializer = next(task for task in asyncio.all_tasks() if task.get_name() == 'backend-initialize')
                    initializer.cancel()
                    await asyncio.sleep(0)
                    initializer.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(initializer.done())
                shutdown = asyncio.create_task(lifetime.__anext__())
                # The real cleanup owner has reached its maintenance drain.
                await asyncio.gather(self.backend.reaper_task, return_exceptions=True)
                shutdown.cancel()
                await asyncio.sleep(0)
                shutdown.cancel()
                await asyncio.sleep(0)
                self.assertFalse(shutdown.done())
                self.assertFalse(ended.is_set())
                closed.assert_not_called()
            finally:
                release.set()
                if shutdown is not None:
                    with self.assertRaises(asyncio.CancelledError):
                        await shutdown
                else:
                    await lifetime.aclose()
        self.assertTrue(ended.is_set())
        self.assertTrue(self.backend._close_task.done())
        closed.assert_called_once()

    async def assert_thread_drain_on_close(self, trigger):
        import threading
        client = None
        if trigger == 'retire':
            from aiohttp.test_utils import TestClient, TestServer
            client=TestClient(TestServer(create_app(self.backend,'x'*32)))
            await client.start_server();self.addAsyncCleanup(client.close)
        started, release = threading.Event(), threading.Event()
        def worker():
            started.set(); release.wait(5)
            return PreparedReply('late')
        async def prepare(work, command):
            return await self.backend.gate.run(worker)
        self.backend.prepare_command = prepare
        closed = Mock()
        self.backend.data_owner = SimpleNamespace(close=closed)
        await self.backend.submit(self.payload())
        work = next(iter(self.backend.works.values()))
        close = None
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 1))
            if trigger == 'expire':
                self.now += 600
                self.backend.expire()
            else:
                response=await client.post('/v1/gateways/retire',json={'gateway_epoch':'epoch'},
                    headers={'Authorization':'Bearer '+'x'*32,'X-Taki-Instance':'fixture'})
                self.assertEqual(response.status,200)
            await asyncio.sleep(0)
            close = asyncio.create_task(self.backend.close())
            await asyncio.sleep(0); await asyncio.sleep(0)
            close.cancel()
            await asyncio.sleep(0)
            close.cancel()
            await asyncio.sleep(0)
            self.assertFalse(work.task.done())
            self.assertFalse(close.done())
            self.assertEqual(self.backend.gate._pending, 1)
            closed.assert_not_called()
        finally:
            release.set()
            if close is not None:
                with self.assertRaises(asyncio.CancelledError): await close
        closed.assert_called_once()
        self.assertFalse(self.backend.tasks)

    async def test_cancel_before_preparation_starts_releases_owner_after_persisting(self):
        payload = self.payload()
        await self.backend.submit(payload)
        work = next(iter(self.backend.works.values()))
        work.task.cancel()
        await asyncio.gather(work.task, return_exceptions=True)
        await asyncio.sleep(0)
        self.assertEqual(self.backend.ledger.request(payload['request_id'])['status'], 'aborted')
        self.assertFalse(self.backend.works)
        self.backend.gate.prepare.assert_not_called()

    async def test_unexpected_preparation_owner_failure_is_consumed_and_terminated(self):
        self.backend.prepare = AsyncMock(side_effect=RuntimeError('owner failed before its try block'))
        payload = self.payload()
        await self.backend.submit(payload)
        work = next(iter(self.backend.works.values()))
        with self.assertLogs('ournotes_bot.backend', level='ERROR') as logs:
            await asyncio.gather(work.task, return_exceptions=True)
            await asyncio.sleep(0)
        self.assertTrue(any('Preparation owner failed' in line for line in logs.output))
        self.assertEqual(self.backend.ledger.request(payload['request_id'])['status'], 'failed')
        self.assertFalse(self.backend.works)

    async def test_ready_exposes_pending_terminal_without_fabricating_durable_status(self):
        from aiohttp.test_utils import TestClient, TestServer
        client=TestClient(TestServer(create_app(self.backend,'x'*32)))
        await client.start_server();self.addAsyncCleanup(client.close)
        headers={'Authorization':'Bearer '+'x'*32,'X-Taki-Instance':'fixture'}
        payload = self.payload()
        with patch.object(self.backend.contexts,'begin',side_effect=RuntimeError('injected')), \
             patch.object(self.backend.ledger,'finish',side_effect=sqlite3.OperationalError('injected')):
            await self.backend.submit(payload)
            response=await client.get('/v1/ready',headers=headers)
            result=await response.json()
        self.assertEqual(response.status,503)
        self.assertEqual(result['maintenance']['reason'],'delivery_storage_unavailable')
        self.assertEqual(result['maintenance']['pending_terminations'],1)
        self.assertGreaterEqual(result['maintenance']['oldest_pending_seconds'],0)
        self.assertEqual(self.backend.ledger.request(payload['request_id'])['status'],'preparing')

    async def test_reaper_storage_failure_is_reported_by_ready_and_can_recover(self):
        from aiohttp.test_utils import TestClient, TestServer
        client=TestClient(TestServer(create_app(self.backend,'x'*32)))
        await client.start_server();self.addAsyncCleanup(client.close)
        headers={'Authorization':'Bearer '+'x'*32,'X-Taki-Instance':'fixture'}
        called = asyncio.Event()
        def fail():
            called.set()
            raise sqlite3.OperationalError('injected')
        with patch.object(self.backend, 'expire', side_effect=fail):
            await asyncio.wait_for(called.wait(), 3)
            response=await client.get('/v1/ready',headers=headers)
            self.assertEqual(response.status,503)
            result=await response.json()
            self.assertFalse(result['ready'])
            self.assertEqual(result['maintenance']['reason'],'maintenance_failed')
            self.assertTrue(result['maintenance']['reaper_alive'])
        self.backend.maintain()
        response=await client.get('/v1/ready',headers=headers)
        self.assertEqual(response.status,200)

    async def test_reaper_unknown_failure_stays_failed_and_ready_reports_it(self):
        from aiohttp.test_utils import TestClient, TestServer
        client=TestClient(TestServer(create_app(self.backend,'x'*32)))
        await client.start_server();self.addAsyncCleanup(client.close)
        headers={'Authorization':'Bearer '+'x'*32,'X-Taki-Instance':'fixture'}
        with patch.object(self.backend, 'expire', side_effect=RuntimeError('unexpected')):
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(asyncio.shield(self.backend.reaper_task), 3)
        response=await client.get('/v1/ready',headers=headers)
        self.assertEqual(response.status,503)
        result=await response.json()
        self.assertFalse(result['maintenance']['reaper_alive'])
        self.assertEqual(result['maintenance']['reaper_error'],'RuntimeError')

    async def test_model_admin_http_requires_epoch_and_keeps_secret_out_of_reads(self):
        from dataclasses import replace
        from aiohttp.test_utils import TestClient,TestServer
        from ournotes_bot.application.model_runtime import ModelRuntime
        self.backend.settings=replace(self.backend.settings,ai_profile_file=self.root/'profile.json')
        self.backend.model_runtime=ModelRuntime(self.backend,self.root/'usage.sqlite3')
        client=TestClient(TestServer(create_app(self.backend,'x'*32)))
        await client.start_server();self.addAsyncCleanup(client.close)
        headers={'Authorization':'Bearer '+'x'*32,'X-Taki-Instance':'fixture'}
        self.assertEqual((await client.get('/v1/admin/model')).status,401)
        settings=await (await client.get('/v1/admin/model',headers=headers)).json()
        payload=dict(operation_id='a'*32,guard=settings['guard'],profile=settings['profile'],groups={},api_key='PRIVATE_TEST_KEY',api_url=settings['api_url'])
        payload['profile']['capability_evidence']='fixture/native'
        self.assertEqual((await client.post('/v1/admin/model',json=payload,headers=headers)).status,409)
        headers['X-Taki-Epoch']='epoch'
        response=await client.post('/v1/admin/model',json=payload,headers=headers)
        self.assertEqual(response.status,200)
        self.assertNotIn('PRIVATE_TEST_KEY',await response.text())
        group=await client.get('/v1/admin/model/groups/group1',headers=headers)
        self.assertEqual(group.status,200)
        self.assertEqual((await group.json())['daily_limit'],1000000)

    async def test_metrics_busy_does_not_block_event_loop_or_disable_writer(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        metrics=QueryMetrics(self.root/'metrics.json')
        entered, release=Event(), Event()
        def writer():
            with metrics._lock:
                entered.set()
                release.wait(2)
        self.backend.parser.metrics_snapshot=metrics.snapshot
        with ThreadPoolExecutor(max_workers=1) as executor:
            job=executor.submit(writer)
            await asyncio.to_thread(entered.wait)
            try:
                loop=asyncio.get_running_loop()
                heartbeat=loop.create_future()
                loop.call_later(.01,heartbeat.set_result,None)
                started=loop.time()
                result=self.backend.admin.metrics()
                await heartbeat
                self.assertLess(loop.time()-started,.5)
                self.assertEqual(result['sections']['metrics']['reason_code'],'metrics_snapshot_busy')
                self.assertTrue(metrics.available)
                self.assertFalse(metrics.path.exists())
            finally:
                release.set()
                await asyncio.wrap_future(job)
        self.assertTrue(metrics.increment('ask_total'))
        self.assertTrue(metrics.snapshot()['available'])

    async def asyncSetUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        self.now=time.time()
        self.repo=SimpleNamespace(capture_catalog=lambda: SimpleNamespace(cache_state='stale',songs=[],last_successful_sync_at=None))
        self.backend=Backend(self.repo,SimpleNamespace(metrics_snapshot=lambda: {'available':False,'reason_code':'metrics_not_collected'}),
            Settings('','','fixture',self.root/'cache.json',6),instance_id='fixture',app_id='app',
            ledger_path=self.root/'delivery.sqlite3',clock=lambda:self.now)
        self.addAsyncCleanup(self.backend.close)
        self.backend.ready=True
        await self.backend.register('epoch')
        self.backend.gate.prepare=AsyncMock(return_value=PreparedReply('fixture',business_status='success'))

    def payload(self,index=1):
        msg=str(index)
        return dict(protocol=PROTOCOL,request_id=digest(['fixture','app','qq','group','group1','secret-user',msg]),
            instance_id='fixture',app_id='app',platform='qq',kind='group',conversation_id='group1',
            user_id='secret-user',message_id=msg,timestamp_ms=int(self.now*1000),body='/帮助',
            capability=CAPABILITY,gateway_epoch='epoch',ingress_seq=index,trigger='command',reference=None,interaction=None,
            policy=dict(natural_query='model',epoch='fixture',global_revision=0,group_revision=0))

    async def admitted(self,index=1):
        for work in list(self.backend.works.values()):
            self.backend.finish(work,'finished')
        await asyncio.sleep(.001)
        payload=self.payload(index)
        await self.backend.submit(payload)
        return await self.wait_status(payload['request_id'],'prepared')

    async def wait_status(self,request_id,status):
        for _ in range(200):
            row=self.backend.ledger.request(request_id)
            if row['status']==status:return row
            await asyncio.sleep(.005)
        self.fail(f'fixture expected {status}, observed {row["status"]}')

    async def test_capacity_diagnostics_and_request_replay_at_limit(self):
        backend = Backend(self.repo,self.backend.parser,self.backend.settings,instance_id='fixture',app_id='app',
                          ledger_path=self.root/'limited.sqlite3',clock=lambda:self.now,
                          ledger_request_capacity=1,ledger_context_capacity=2,ledger_epoch_capacity=3)
        self.addAsyncCleanup(backend.close)
        backend.ready = True
        backend.gate.prepare = AsyncMock(return_value=PreparedReply('fixture',business_status='success'))
        await backend.register('epoch')
        payload = self.payload()
        first = await backend.submit(payload)
        self.assertEqual((await backend.submit(payload))['delivery_id'],first['delivery_id'])
        with self.assertRaisesRegex(ProtocolError,'request_conflict'):
            await backend.submit({**payload,'body':'/查曲 fixture'})
        with self.assertRaisesRegex(ProtocolError,'ledger_capacity'):
            await backend.submit(self.payload(2))
        before = list(backend.ledger.db.iterdump())
        status = backend.admin.overview()['sections']['ledger']['data']
        self.assertEqual(status['limits'], {'requests':1,'highwater':2,'epochs':3})
        self.assertEqual(status['counts']['requests'],1)
        self.assertEqual(status['reason_codes'], {'requests':'ledger_capacity'})
        self.assertEqual(before,list(backend.ledger.db.iterdump()))

    async def test_read_does_not_expire_or_change_ledger_and_privacy(self):
        row=await self.admitted()
        self.now+=400
        before=list(self.backend.ledger.db.iterdump())
        result=self.backend.admin.overview()
        detail=self.backend.admin.detail(row['id'])
        self.backend.admin.listing({})
        self.backend.admin.sources()
        self.backend.admin.notices()
        self.assertEqual(self.backend.admin.groups()['sections']['groups']['data'][0]['group_id'],'group1')
        self.backend.admin.metrics()
        self.assertEqual(before,list(self.backend.ledger.db.iterdump()))
        text=json.dumps(detail)
        self.assertNotIn('secret-user',text)
        self.assertNotIn('message_id',text)
        self.assertNotIn('/帮助',text)
        self.assertEqual(result['sections']['requests']['data']['states']['processing'],1)

    async def test_fixed_window_pagination_and_filter_binding(self):
        for i in range(1,5):
            await self.admitted(i)
        self.now+=.01
        first=self.backend.admin.listing({'limit':'2'})['sections']['requests']['data']
        self.now+=1
        await self.admitted(5)
        second=self.backend.admin.listing({'limit':'2','cursor':first['next_cursor']})['sections']['requests']['data']
        self.assertEqual(first['to'],second['to'])
        ids=[r['request_id'] for r in first['items']+second['items']]
        self.assertEqual(len(set(ids)),4)
        with self.assertRaises(ProtocolError):self.backend.admin.listing({'cursor':first['next_cursor'],'state':'failed'})
        for params in ({'limit':'101'},{'from':'nan'},{'sql':'x'},{'cursor':'bad'}):
            with self.assertRaises(ProtocolError):self.backend.admin.listing(params)

    async def test_historical_metadata_missing_not_inferred(self):
        row=await self.admitted()
        with self.backend.diagnostics.db:self.backend.diagnostics.db.execute('DELETE FROM requests')
        detail=self.backend.admin.detail(row['id'])['sections']['request']['data']
        self.assertIsNone(detail['group_id'])
        self.assertIsNone(detail['parts'][0]['primary_kind'])
        self.assertEqual(detail['business_outcome'],'unknown')

    async def test_attention_includes_retained_unresolved_before_window(self):
        row=await self.admitted()
        self.backend.ledger.intent(row['delivery'],'0','attempt')
        self.backend.finish(self.backend.works[row['delivery']],'expired')
        self.now+=86401
        self.assertEqual(self.backend.admin.listing({})['sections']['requests']['data']['items'],[])
        pending=self.backend.admin.listing({'attention':'true'})['sections']['requests']['data']['items']
        self.assertEqual(pending[0]['delivery_state'],'uncertain')

    async def test_policy_snapshot_is_part_of_idempotency_and_strict_wire_shape(self):
        payload=self.payload()
        from ournotes_bot.application.backend_protocol import event
        from ournotes_bot.application.notice_gateway import NoticeGateway
        for profile in ([], {}, 'unverified'):
            with self.assertRaisesRegex(ProtocolError,'reference_profile'):
                await self.backend.submit({**payload,'trigger':'quoted_reply',
                    'reference':{'message_id':'fixture-message','adapter_profile_version':profile}})
        for protocol in ({'major':True,'minor':1},{'major':1.0,'minor':1},{**PROTOCOL,'extra':True}):
            with self.assertRaisesRegex(ProtocolError,'protocol_version'):await self.backend.submit({**payload,'protocol':protocol})
            with self.assertRaisesRegex(ProtocolError,'protocol_version'):event({'protocol':protocol})
            with self.assertRaisesRegex(ProtocolError,'protocol_version'):
                NoticeGateway.__new__(NoticeGateway).common({'protocol':protocol,'instance_id':'fixture','app_id':'app','gateway_epoch':'epoch'},set())
        first=await self.backend.submit(payload)
        self.assertEqual((await self.backend.submit(payload))['delivery_id'],first['delivery_id'])
        changed={**payload,'policy':{**payload['policy'],'natural_query':'local','group_revision':1}}
        with self.assertRaisesRegex(ProtocolError,'request_conflict'):
            await self.backend.submit(changed)
        for policy in ({'natural_query':'local'}, {**payload['policy'],'global_revision':True},
                       {**payload['policy'],'natural_query':'unknown'}):
            with self.assertRaises(ProtocolError):
                await self.backend.submit({**payload,'policy':policy})

    async def test_spans_deduplicate_validate_and_do_not_change_permission(self):
        row=await self.admitted()
        event=dict(event_id='event',request_id=row['id'],delivery_id=row['delivery'],kind='upload',position=0,attempt=None,duration_ms=123,outcome='ok')
        batch={'backend_epoch':self.backend.runtime,'events':[event,event]}
        before=self.backend.ledger.parts(row['delivery'])
        self.backend.admin.telemetry(batch,'epoch')
        spans=self.backend.diagnostics.read(row['id'])['spans']
        self.assertEqual(sum(s['kind']=='upload' for s in spans),1)
        self.assertEqual(before,self.backend.ledger.parts(row['delivery']))
        for change in ({'position':5},{'duration_ms':float('nan')},{'duration_ms':-1},{'attempt':'false'}):
            with self.assertRaises(ProtocolError):
                self.backend.admin.telemetry({'backend_epoch':self.backend.runtime,'events':[event|change]},'epoch')
        with self.assertRaisesRegex(ProtocolError,'telemetry_conflict'):
            self.backend.admin.telemetry({'backend_epoch':self.backend.runtime,'events':[event|{'duration_ms':124}]},'epoch')

    async def test_mixed_outcomes_and_partial_uncertain_share_all_read_models(self):
        self.backend.gate.prepare=AsyncMock(side_effect=[PreparedReply('first',business_status='success'),
            PreparedReply('second',business_status='invalid_arguments'),PreparedReply('third')])
        payload=self.payload();payload['body']='/帮助\n/查曲 x\n/查卡 x'
        await self.backend.submit(payload)
        row=await self.wait_status(payload['request_id'],'prepared')
        self.backend.ledger.intent(row['delivery'],'0','a')
        self.backend.ledger.ack(row['delivery'],'0','a','success','receipt')
        self.backend.ledger.intent(row['delivery'],'1','b')
        self.backend.finish(self.backend.works[row['delivery']],'expired')
        self.now+=.01
        detail=self.backend.admin.detail(row['id'])['sections']['request']['data']
        listed=self.backend.admin.listing({})['sections']['requests']['data']['items'][0]
        summary=self.backend.admin.overview()['sections']['requests']['data']
        self.assertEqual(detail['business_outcome'],'mixed')
        self.assertEqual(detail['delivery_state'],listed['delivery_state'])
        self.assertEqual(detail['delivery_state'],'partial')
        self.assertEqual(detail['uncertain_parts'],1)
        self.assertEqual(summary['states']['partial'],1)
        self.assertEqual(sum(summary['states'].values()),summary['total'])

    async def test_fallback_keeps_upload_failure_span_at_same_position(self):
        self.backend.gate.prepare=AsyncMock(return_value=PreparedReply('fallback',image=b'fixture'))
        row=await self.admitted()
        self.backend.apply_event(row['delivery'],dict(protocol=PROTOCOL,request_id=row['id'],plan_version=1,
            gateway_epoch='epoch',event='upload_failed',part_id='0',attempt_id='none'))
        event=dict(event_id='upload-failure',request_id=row['id'],delivery_id=row['delivery'],kind='upload',position=0,
                   attempt=None,duration_ms=10,outcome='error')
        self.backend.admin.telemetry({'backend_epoch':self.backend.runtime,'events':[event]},'epoch')
        result=self.backend.admin.detail(row['id'])['sections']['request']['data']
        self.assertEqual(result['parts'][0]['id'],'0:f')
        self.assertEqual(result['parts'][0]['primary_kind'],'image')
        self.assertEqual(result['spans'][-1]['outcome'],'error')
        self.assertEqual(result['spans'][-1]['position'],0)

    async def test_disabled_and_full_observation_do_not_change_admission(self):
        self.backend.diagnostics.close()
        self.backend.diagnostics=Diagnostics(self.root/'disabled.sqlite3',enabled=False)
        self.assertFalse((self.root/'disabled.sqlite3').exists())
        row=await self.admitted()
        self.assertEqual(row['status'],'prepared')
        self.assertEqual(self.backend.diagnostics.reason,'diagnostics_disabled')
        self.backend.diagnostics=Diagnostics(self.root/'bounded.sqlite3')
        row=await self.admitted(2)
        for i in range(100):
            self.backend.diagnostics.span(str(i),row['id'],'prepare',1,self.now)
        self.assertEqual(len(self.backend.diagnostics.read(row['id'])['spans']),32)
        self.assertGreater(self.backend.diagnostics.dropped,0)
        self.assertEqual(self.backend.ledger.request(row['id'])['status'],'prepared')

    async def test_readonly_and_sqlite_full_diagnostics_do_not_fail_queries(self):
        diagnostics=self.backend.diagnostics
        diagnostics.db.execute('PRAGMA query_only=ON')
        self.assertEqual((await self.admitted())['status'],'prepared')
        self.assertEqual(diagnostics.reason,'diagnostics_write_failed')
        diagnostics.db.execute('PRAGMA query_only=OFF')
        pages=diagnostics.db.execute('PRAGMA page_count').fetchone()[0]
        diagnostics.db.execute(f'PRAGMA max_page_count={pages}')
        self.assertFalse(diagnostics.write(lambda db:db.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',
            ('full','full','epoch',self.now,'group','fixture','x'*100000,None))))
        self.assertEqual((await self.admitted(2))['status'],'prepared')

    async def test_notice_projection_reads_claims_without_leasing_or_generating(self):
        from contextlib import closing
        from ournotes_bot.application.notice_reports import NoticeStore
        from ournotes_bot.application.notice_gateway import NoticeGateway
        owner=NoticeStore(self.root/'notice-projection.sqlite3','app')
        owner.observe('unknown');owner.observe('leased');owner.permission('leased',True,0)
        reports=SimpleNamespace(store=owner,project_root=self.root,source=self.root,_report=Mock(side_effect=AssertionError('report')))
        gateway=NoticeGateway(self.backend,reports);gateway.deployment_identity=lambda:('a'*40,'b'*40)
        gateway.claims=Mock(side_effect=AssertionError('claims'))
        self.backend.notices=gateway
        with closing(owner.connect()) as db,db:
            db.execute("INSERT INTO gateway_notice_claims VALUES(?,?,?,?,?,?,?,?,?,NULL)",('app','a'*64,'claim','leased','a'*40,'epoch',1,self.now+60,'claimed'))
        with closing(owner.connect()) as db:before=list(db.iterdump())
        data=self.backend.admin.notices()['sections']['notices']['data']
        targets={r['group_id']:r for r in data['targets']}
        self.assertEqual(targets['unknown']['stage'],'permission_unknown')
        self.assertEqual(targets['leased']['stage'],'claimed')
        self.assertIsNone(targets['leased']['observation_times'])
        self.backend.operations.observe_notice('a'*64,'claimed');self.backend.operations.flush_notices()
        target=self.backend.admin.notice_target('leased')['sections']['notice_target']['data']['targets'][0]
        self.assertEqual(target['observation_times']['claimed']['first_observed_at'],self.now)
        with closing(owner.connect()) as db:self.assertEqual(list(db.iterdump()),before)
        reports._report.assert_not_called();gateway.claims.assert_not_called()

    async def test_read_notice_owner_is_side_effect_free(self):
        from contextlib import closing
        from ournotes_bot.application.notice_reports import NoticeStore
        owner=NoticeStore(self.root/'notices.sqlite3','app')
        owner.observe('group1')
        self.backend.notices=SimpleNamespace(store=owner,claims=Mock(side_effect=AssertionError('claim invoked')))
        with closing(owner.connect()) as db:before=list(db.iterdump())
        for _ in range(3):self.backend.admin.notices()
        with closing(owner.connect()) as db:self.assertEqual(list(db.iterdump()),before)
        self.backend.notices.claims.assert_not_called()

    async def test_notice_cursor_keeps_first_insertion_window_and_rejects_other_resource(self):
        from contextlib import closing
        from ournotes_bot.application.notice_reports import NoticeStore
        owner=NoticeStore(self.root/'notices.sqlite3','app')
        self.backend.notices=SimpleNamespace(store=owner)
        with closing(owner.connect()) as db,db:
            db.executemany("INSERT INTO notices(app,gid,revision,status) VALUES('app',?,?,'sending')",
                [(f'group{i}','a'*40) for i in range(30)])
        first=self.backend.admin.notices()['sections']['notices']
        self.assertEqual(first['reason_code'],'notice_timestamps_not_collected')
        self.assertEqual(len(first['data']['items']),25)
        cursor=first['data']['next_cursor']
        with closing(owner.connect()) as db,db:
            db.execute("INSERT INTO notices(app,gid,revision,status) VALUES('app','late',?,'success')",('b'*40,))
        second=self.backend.admin.notices({'cursor':cursor})['sections']['notices']['data']
        self.assertEqual(len(second['items']),5)
        self.assertEqual(len({v['group_id'] for v in first['data']['items']+second['items']}),30)
        self.assertNotIn('late',str(second))
        self.assertIsNone(second['next_cursor'])
        with self.assertRaises(ProtocolError):self.backend.admin.listing({'cursor':cursor})
        for params in ({'limit':'101'},{'from':'0'},{'cursor':cursor+'bad'}):
            with self.assertRaises(ProtocolError):self.backend.admin.notices(params)

    async def test_metrics_days_keep_missing_distinct_from_zero(self):
        from datetime import datetime,timezone,timedelta
        today=datetime(2026,10,20,0,1,tzinfo=timezone(timedelta(hours=8)))
        day=[today-timedelta(minutes=2)]
        metrics=QueryMetrics(self.root/'metrics.json',clock=lambda:day[0])
        metrics.increment_many({'ask_total':2,'route_local':2})
        day[0]=today;metrics.increment_many({'ask_total':1,'route_model':1})
        self.backend.parser=SimpleNamespace(metrics_snapshot=metrics.snapshot)
        self.backend.clock=lambda:today.timestamp()
        data=self.backend.admin.metrics()['sections']['metrics']['data']
        self.assertEqual(data['today']['ask_total'],1)
        self.assertEqual(data['last_seven_days']['ask_total'],3)
        self.assertEqual((data['observed_days'],data['missing_days']),(2,5))
        metrics.path.write_text('broken')
        self.assertEqual(self.backend.admin.metrics()['sections']['metrics']['availability'],'unavailable')
        self.assertTrue(metrics.available)

    async def test_command_metadata_is_bounded_and_counts_overflow(self):
        payload=self.payload();payload['body']='\n'.join(['/帮助']*1000)
        await self.backend.submit(payload);await self.wait_status(payload['request_id'],'prepared')
        observed=self.backend.diagnostics.read(payload['request_id'])
        self.assertLessEqual(len(observed['commands']),self.backend.settings.multi_command_limit+1)
        self.assertEqual(observed['commands'][0]['total_commands'],1000)

    async def test_broken_diagnostics_does_not_reject_business(self):
        self.backend.diagnostics.close()
        (self.root/'broken.sqlite3').write_bytes(b'not sqlite')
        self.backend.diagnostics=Diagnostics(self.root/'broken.sqlite3')
        row=await self.admitted()
        self.assertEqual(row['status'],'prepared')
        self.assertEqual(self.backend.diagnostics.health()['availability'],'partial')

    async def test_prepare_does_not_include_order_or_transport_wait(self):
        first=await self.admitted()
        second=self.payload(2)
        await self.backend.submit(second)
        await asyncio.sleep(.06)
        # Independent requests no longer wait on a global arrival ticket.
        early=self.backend.diagnostics.read(second['request_id'])['spans']
        self.assertIn('prepare',[s['kind'] for s in early])
        self.assertEqual(self.backend.lookup(second['request_id'],'epoch')['status'],'prepared')
        self.backend.finish(self.backend.works[first['delivery']],'finished')
        await asyncio.sleep(.02)
        spans=self.backend.diagnostics.read(second['request_id'])['spans']
        durations={s['kind']:s['duration_ms'] for s in spans}
        self.assertEqual(durations['reply_order_wait'],0)
        self.assertGreaterEqual(durations['prepare'],0)

    async def test_late_ack_updates_facts_without_reopening_lifecycle(self):
        row=await self.admitted()
        self.backend.ledger.intent(row['delivery'],'0','attempt')
        self.backend.finish(self.backend.works[row['delivery']],'expired')
        self.backend.apply_event(row['delivery'],dict(protocol=PROTOCOL,request_id=row['id'],plan_version=1,
            gateway_epoch='epoch',event='ack',part_id='0',attempt_id='attempt',result='success',
            message_id='platform-receipt',allow_context=False))
        view=self.backend.admin.detail(row['id'])['sections']['request']['data']
        self.assertEqual(view['lifecycle'],'expired')
        self.assertEqual(view['delivery_state'],'complete')
        self.assertNotIn('platform-receipt',json.dumps(view))

    async def test_zero_parts_is_closed_without_platform_success(self):
        self.backend.gate.prepare=AsyncMock(return_value=None)
        payload=self.payload();await self.backend.submit(payload);await self.wait_status(payload['request_id'],'finished')
        result=self.backend.admin.detail(payload['request_id'])['sections']['request']['data']
        self.assertEqual(result['lifecycle'],'finished')
        self.assertEqual(result['part_count'],0)
        self.assertEqual(result['delivery_state'],'closed_unsent')

    async def test_failed_refresh_preserves_cached_availability(self):
        from ournotes_bot.application.runtime_tasks import RuntimeTasks
        tasks=RuntimeTasks(self.repo,SimpleNamespace(cutoff_sampling_enabled=False))
        self.backend.runtime_tasks=tasks
        def refresh_catalog():raise OSError('fixture source offline')
        worker=asyncio.create_task(tasks.loop(refresh_catalog,100))
        for _ in range(200):
            if tasks.observation_snapshot()['refreshes'].get('refresh_catalog',{}).get('last_error_code'):
                break
            await asyncio.sleep(.005)
        worker.cancel();await asyncio.gather(worker,return_exceptions=True)
        self.repo.capture_catalog=lambda:SimpleNamespace(cache_state='stale',songs=[1],last_successful_sync_at='2026-10-19T00:00:00Z')
        snapshot=self.backend.admin.sources()['sections']
        self.assertEqual(snapshot['catalog']['data']['song_count'],1)
        self.assertEqual(snapshot['catalog']['data']['cache_state'],'stale')
        self.assertEqual(snapshot['background']['data']['refreshes']['refresh_catalog']['last_error_code'],'OSError')

    async def test_http_auth_and_read_routes(self):
        from aiohttp.test_utils import TestClient, TestServer
        client=TestClient(TestServer(create_app(self.backend,'x'*32)))
        await client.start_server()
        try:
            self.assertEqual((await client.get('/v1/admin/overview')).status,401)
            headers={'Authorization':'Bearer '+'x'*32,'X-Taki-Instance':'wrong'}
            self.assertEqual((await client.get('/v1/admin/overview',headers=headers)).status,403)
            headers['X-Taki-Instance']='fixture'
            self.assertEqual((await client.get('/v1/admin/overview',headers=headers)).status,200)
        finally:
            await client.close()
        # TestClient owns lifecycle and closed this backend already.
        self.backend.close=AsyncMock()


class OperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_full_rejects_before_task_execution(self):
        from ournotes_bot.application.operations import Operations
        with tempfile.TemporaryDirectory() as folder:
            jobs=Operations(Path(folder)/'ops.sqlite3','fixture')
            calls=[]
            jobs.register('fixture',lambda p:None,lambda p:calls.append(p) or {'code':'fixture_complete'})
            pages=jobs.db.execute('PRAGMA page_count').fetchone()[0]
            jobs.db.execute('PRAGMA max_page_count='+str(pages))
            with self.assertRaisesRegex(ProtocolError,'ops_store_unavailable'):
                await jobs.submit({'operation_id':'d'*32,'action':'fixture','params':{'value':'x'*4000}})
            self.assertEqual(calls,[])
            self.assertFalse(jobs.health()['available'])
            self.assertEqual(jobs.db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
            await jobs.close()

    async def test_idle_housekeeping_expires_completed_jobs(self):
        from ournotes_bot.application.operations import Operations
        with tempfile.TemporaryDirectory() as folder:
            now=[1000000]
            jobs=Operations(Path(folder)/'ops.sqlite3','fixture',clock=lambda:now[0])
            jobs.register('fixture',lambda p:None,lambda p:{'code':'fixture_complete'})
            await jobs.submit({'operation_id':'c'*32,'action':'fixture','params':{}})
            await jobs.worker
            now[0]+=7*86400+1
            jobs.housekeeping()
            self.assertEqual(jobs.listing()['items'],[])
            await jobs.close()

    async def test_durable_coalescing_conflict_and_interrupted_restart(self):
        from ournotes_bot.application.operations import Operations
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'operations.sqlite3'
            jobs=Operations(path,'fixture')
            entered,release=asyncio.Event(),asyncio.Event()
            async def run(params):
                entered.set();await release.wait();return {'code':'fixture_complete'}
            jobs.register('fixture',lambda p:None,run)
            first=await jobs.submit({'operation_id':'a'*32,'action':'fixture','params':{}})
            await entered.wait()
            second=await jobs.submit({'operation_id':'b'*32,'action':'fixture','params':{}})
            self.assertEqual(first['id'],second['id'])
            with self.assertRaisesRegex(ProtocolError,'ops_conflict'):
                await jobs.submit({'operation_id':'a'*32,'action':'fixture','params':{'different':True}})
            release.set();await jobs.worker
            self.assertEqual(jobs.detail(first['id'])['state'],'succeeded')
            with jobs.db:
                jobs.db.execute("UPDATE jobs SET state='running'")
            await jobs.close()
            restarted=Operations(path,'fixture')
            self.assertEqual(restarted.detail(first['id'])['state'],'interrupted')
            await restarted.close()

    async def test_corrupt_store_disables_operations_without_replacing_it(self):
        from ournotes_bot.application.operations import Operations
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'operations.sqlite3';path.write_bytes(b'broken')
            jobs=Operations(path,'fixture')
            self.assertFalse(jobs.health()['available'])
            self.assertEqual(path.read_bytes(),b'broken')
            await jobs.close()

    async def test_later_store_failure_disables_only_management_reads(self):
        from ournotes_bot.application.operations import Operations
        with tempfile.TemporaryDirectory() as folder:
            jobs=Operations(Path(folder)/'ops.sqlite3','fixture')
            jobs.db.execute('DROP TABLE jobs')
            with self.assertRaisesRegex(ProtocolError,'ops_store_unavailable'):jobs.listing()
            self.assertFalse(jobs.health()['available'])
            await jobs.close()
            self.assertIsNone(jobs.db)


class HTTPV2ContractTests(unittest.IsolatedAsyncioTestCase):
    """Shared examples are produced by Python and consumed by the installed TS gateway."""
    @staticmethod
    async def produce():
        stamp = 1792382400.0
        request = dict(protocol=PROTOCOL,
            request_id=digest(['fixture','app','qq','group','group1','fixture-user','message']),
            instance_id='fixture',app_id='app',platform='qq',kind='group',conversation_id='group1',
            user_id='fixture-user',message_id='message',timestamp_ms=int(stamp*1000),body='/帮助',
            capability=CAPABILITY,gateway_epoch='epoch',ingress_seq=1,trigger='command',reference=None,interaction=None,
            policy=dict(natural_query='model',epoch='fixture',global_revision=0,group_revision=0))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            repo=SimpleNamespace(capture_catalog=lambda: SimpleNamespace(cache_state='stale',songs=[],last_successful_sync_at=None))
            backend=Backend(repo,SimpleNamespace(),Settings('','','fixture',root/'cache.json',6),
                instance_id='fixture',app_id='app',ledger_path=root/'ledger.sqlite3',clock=lambda:stamp)
            backend.runtime='b'*32
            backend.ready=True
            permit_clock=[10.0]
            backend.permissions.clock=lambda:permit_clock[0]
            release=asyncio.Event()
            async def prepare(*args):
                await release.wait()
                return PreparedReply('fixture answer',image=b'\x89PNG\r\n\x1a\nfixture',business_status='success')
            backend.gate.prepare=prepare
            result={'python_to_ts':{},'ts_to_python':{'request':request,'events':{}},
                'identity_cases':{'accepted':['😀'*256,'\ufeffreceipt\ufeff'],
                                  'rejected':['😀'*257,'\x85receipt','receipt\x85']}}
            output=result['python_to_ts']
            try:
                app=create_app(backend,'x'*32)
                handlers={route.resource.canonical:route.handler for route in app.router.routes()}
                ready=json.loads((await handlers['/v1/ready'](None)).text)
                # These diagnostics are not protocol identities; freeze only volatile diagnostics.
                ready['pid']=0;ready['backend_code_digest']='0'*64
                output['ready']=ready
                output['classify']=json.loads((await handlers['/v1/classify'](SimpleNamespace(json=AsyncMock(return_value={'body':'/帮助'})))).text)
                output['registration']=await backend.register('epoch')
                with patch('ournotes_bot.application.backend.uuid.uuid4',return_value=SimpleNamespace(hex='d'*32)):
                    output['preparing']=await backend.submit(request)
                work=backend.works['d'*32]
                with patch('ournotes_bot.application.model_execution.uuid.uuid4',return_value=SimpleNamespace(hex='c'*32)):
                    permission_task=asyncio.create_task(backend.permissions.acquire(work,20.0,attempt_id='a'*32))
                    await asyncio.sleep(0)
                output['challenge']=backend.permissions.challenge_view(work)
                output['preparing_challenge']=backend.snapshot(backend.ledger.request(request['request_id']),'epoch')
                permit_clock[0]=12.9995
                output['challenge_zero']=backend.permissions.challenge_view(work)
                permit_clock[0]=10.0
                decision={k:v for k,v in output['challenge'].items() if k not in ('remaining_ms','admission_policy_version')}
                decision.update(decision='allow',current_policy_version={'epoch':'fixture','global_revision':0,'group_revision':0},reason='model_allowed',ttl_ms=1000)
                result['ts_to_python']['model_permission']=decision
                output['model_permission']=backend.permissions.decide(request['request_id'],'a'*32,decision,'epoch')
                await permission_task
                work.model_started_at=time.monotonic()-9
                output['hint_pending']=backend.lookup(request['request_id'],'epoch')
                common=dict(protocol=PROTOCOL,request_id=request['request_id'],plan_version=1,gateway_epoch='epoch',
                    part_id='0',attempt_id='fixture-attempt')
                def apply(name, payload):
                    result['ts_to_python']['events'][name]=payload
                    return backend.apply_event(work.delivery,payload)
                output['hint_intent']=apply('hint_intent',{**common,'event':'intent'})
                output['hint_sending']=backend.lookup(request['request_id'],'epoch')
                output['hint_ack']=apply('hint_ack',{**common,'event':'ack','result':'success','message_id':'hint-receipt','allow_context':True})
                output['hint_success']=backend.lookup(request['request_id'],'epoch')
                release.set()
                for _ in range(200):
                    if backend.ledger.request(request['request_id'])['status']=='prepared':break
                    await asyncio.sleep(.005)
                output['prepared_image']=backend.lookup(request['request_id'],'epoch')
                if output['prepared_image']['status']!='prepared':raise AssertionError('reply did not prepare')
                # R3 may revoke the in-memory work before a terminal write succeeds.
                work.active=False
                output['prepared_revoked']=backend.snapshot(backend.ledger.request(request['request_id']),'epoch')
                work.active=True
                output['fallback']=apply('upload_failed',{**common,'event':'upload_failed','part_id':'1','attempt_id':'none'})
                output['intent']=apply('intent',{**common,'event':'intent','part_id':'1:f'})
                output['existing_intent']=apply('intent_duplicate',{**common,'event':'intent','part_id':'1:f'})
                output['ack']=apply('ack',{**common,'event':'ack','part_id':'1:f','result':'success','message_id':'answer-receipt','allow_context':True})
                output['duplicate_ack']=apply('ack_duplicate',result['ts_to_python']['events']['ack'])
                output['terminal']=backend.lookup(request['request_id'],'epoch')
                output['batch_finished']=apply('batch_finished',{**common,'event':'batch_finished','part_id':'batch','attempt_id':'none'})
                output['aborted']=apply('aborted',{**common,'event':'aborted','part_id':'batch','attempt_id':'none'})
                legacy={**result['ts_to_python']['events']['ack'],'protocol':{'major':1,'minor':1}}
                legacy.pop('allow_context')
                result['ts_to_python']['events']['legacy_ack']=legacy
                output['legacy_ack']=backend.apply_event(work.delivery,legacy)
                output['retirement']=json.loads((await handlers['/v1/gateways/retire'](SimpleNamespace(json=AsyncMock(return_value={'gateway_epoch':'epoch'})))).text)
                return result
            finally:
                release.set()
                await backend.close()

    async def test_shared_http_v2_producer_outputs_and_request_event_consumers(self):
        from ournotes_bot.application.backend_protocol import Request, event
        fixture=Path(__file__).parents[1]/'koishi/scripts/fixtures/http-v2.json'
        expected=json.loads(fixture.read_text(encoding='utf-8'))
        self.assertEqual(await self.produce(),expected)
        request=expected['ts_to_python']['request']
        parsed=Request.parse(request,'fixture','app',request['timestamp_ms']/1000)
        self.assertEqual(parsed.request_id,request['request_id'])
        for name,payload in expected['ts_to_python']['events'].items():
            decoded=event(payload)
            if name=='legacy_ack':self.assertFalse(decoded['allow_context'])
        mutations=[{'protocol':{'major':1,'minor':1}},{'protocol':{'major':True,'minor':0}},
            {'request_id':'0'*64},{'timestamp_ms':True},{'ingress_seq':2**53},{'extra':True},
            {'policy':{**request['policy'],'global_revision':True}}]
        for change in mutations:
            with self.subTest(change=change),self.assertRaises(ProtocolError):Request.parse({**request,**change},'fixture','app',0)
        original=expected['ts_to_python']['events']['ack']
        for message in expected['identity_cases']['accepted']:
            self.assertEqual(event({**original,'message_id':message})['message_id'],message)
        for message in expected['identity_cases']['rejected']:
            with self.subTest(identity=repr(message)),self.assertRaises(ProtocolError):
                event({**original,'message_id':message})
        for change in ({'event':'unknown'},{'protocol':{'major':2,'minor':0,'extra':True}},
                       {'result':'unknown'},{'message_id':None},{'allow_context':1},{'extra':True}):
            with self.subTest(change=change),self.assertRaises(ProtocolError):event({**original,**change})
