"""Console read/observation contracts, isolated from QQ, sources and models."""
import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ournotes_bot.application.admin import Admin, delivery_state
from ournotes_bot.application.backend import Backend, create_app
from ournotes_bot.application.backend_protocol import PROTOCOL, CAPABILITY, ProtocolError, digest
from ournotes_bot.application.delivery_ledger import Ledger
from ournotes_bot.application.diagnostics import Diagnostics
from ournotes_bot.application.replies import PreparedReply
from ournotes_bot.config import Settings
from ournotes_bot.natural_query.query_metrics import QueryMetrics


class ProjectionTests(unittest.TestCase):
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


class AdminTests(unittest.IsolatedAsyncioTestCase):
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
            capability=CAPABILITY,gateway_epoch='epoch',ingress_seq=index,
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
        # Preparation already ended, although first request still owns reply order.
        early=self.backend.diagnostics.read(second['request_id'])['spans']
        self.assertIn('prepare',[s['kind'] for s in early])
        self.assertNotIn('reply_order_wait',[s['kind'] for s in early])
        self.backend.finish(self.backend.works[first['delivery']],'finished')
        await asyncio.sleep(.02)
        spans=self.backend.diagnostics.read(second['request_id'])['spans']
        durations={s['kind']:s['duration_ms'] for s in spans}
        self.assertGreater(durations['reply_order_wait'],40)
        self.assertLess(durations['prepare'],durations['reply_order_wait'])

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
