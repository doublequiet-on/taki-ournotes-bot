"""Console read/observation contracts, isolated from QQ, sources and models."""
import asyncio
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from ournotes_bot.application.admin import Admin, delivery_state
from ournotes_bot.application.backend import Backend, create_app
from ournotes_bot.application.backend_protocol import PROTOCOL, CAPABILITY, ProtocolError, digest
from ournotes_bot.application.delivery_ledger import Ledger
from ournotes_bot.application.diagnostics import Diagnostics
from ournotes_bot.application.replies import PreparedReply
from ournotes_bot.config import Settings
from ournotes_bot.natural_query.query_metrics import QueryMetrics


class ProjectionTests(unittest.TestCase):
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
            capability=CAPABILITY,gateway_epoch='epoch',ingress_seq=index)

    async def admitted(self,index=1):
        for work in list(self.backend.works.values()):
            self.backend.finish(work,'finished')
        await asyncio.sleep(.001)
        payload=self.payload(index)
        await self.backend.submit(payload)
        for _ in range(30):
            row=self.backend.ledger.request(payload['request_id'])
            if row['status']=='prepared':return row
            await asyncio.sleep(.001)
        self.fail('fixture preparation did not finish')

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

    async def test_spans_deduplicate_validate_and_do_not_change_permission(self):
        row=await self.admitted()
        event=dict(event_id='event',request_id=row['id'],delivery_id=row['delivery'],kind='upload',position=0,attempt=None,duration_ms=123)
        batch={'backend_epoch':self.backend.runtime,'events':[event,event]}
        before=self.backend.ledger.parts(row['delivery'])
        self.backend.admin.telemetry(batch,'epoch')
        spans=self.backend.diagnostics.read(row['id'])['spans']
        self.assertEqual(sum(s['kind']=='upload' for s in spans),1)
        self.assertEqual(before,self.backend.ledger.parts(row['delivery']))
        for change in ({'position':5},{'duration_ms':float('nan')},{'attempt':'false'}):
            with self.assertRaises(ProtocolError):
                self.backend.admin.telemetry({'backend_epoch':self.backend.runtime,'events':[event|change]},'epoch')

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
        self.backend.ledger.ack(row['delivery'],'0','attempt','success','platform-receipt')
        view=self.backend.admin.detail(row['id'])['sections']['request']['data']
        self.assertEqual(view['lifecycle'],'expired')
        self.assertEqual(view['delivery_state'],'complete')
        self.assertNotIn('platform-receipt',json.dumps(view))

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
