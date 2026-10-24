"""Per-request policy execution, cache provenance, concurrency and controller recovery."""
import asyncio
import json
import tempfile
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from test_continuation import Fixture
from ournotes_bot.domain.query_policy import CURRENT_POLICY, ExecutionPolicy
from ournotes_bot.application.replies import prepare_reply, prepare_followup, DeliveryCursor, PreparedReply, QueryGate, prepare_commands
from ournotes_bot.query.continuation import Operation
from ournotes_bot.natural_query.query_agent import CachedOutcome
from ournotes_bot.natural_query.query_validation import OutcomeCode
from ournotes_bot.application.backend_protocol import Request, ProtocolError
from deploy.group_policy_guard import transition, verify_start, DEFAULT


def native_profile(**changes):
    from ournotes_bot.domain.model_calls import Price, ModelProfile
    return ModelProfile('deepseek','fixture-native','fixture-v1','deepseek-chat-v1','fixture/probe',
                        Price('USD',1000000,100000,2000000,1000000,'fixture-price',1,True,'registered-default'),
                        'fixture-key',**changes)


class WeightedUsageTests(unittest.TestCase):
    def setUp(self):
        from ournotes_bot.application.usage_ledger import UsageLedger
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.now=[1792670000.0]
        self.path=Path(self.temp.name)/'usage.sqlite3'
        self.ledger=UsageLedger(self.path,'fixture',clock=lambda:self.now[0]);self.addCleanup(lambda:self.ledger.close())
        self.profile=native_profile()

    def test_atomic_group_reservation_and_known_failure_idempotent(self):
        from fractions import Fraction
        from ournotes_bot.domain.model_calls import Usage,ModelError
        profile=replace(self.profile,group_daily_limit=2000)
        self.ledger.reserve('r','a','u','g',profile,100)
        with self.assertRaisesRegex(ModelError,'quota_exceeded'):
            self.ledger.reserve('r2','b','v','g',profile,100)
        self.assertEqual(self.ledger.db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)
        self.ledger.start('a',profile)
        usage=Usage(100,40,20,10,120,'known')
        self.ledger.settle('a',usage,'model_protocol_error')
        self.assertFalse(self.ledger.settle('a',usage,'model_protocol_error'))
        self.assertEqual(Fraction(self.ledger.get('a')['charged']),104)
        self.assertEqual(self.ledger.get('a')['reserved'],'0')

    def test_unknown_restart_window_late_usage_original_day(self):
        from ournotes_bot.domain.model_calls import Usage
        self.ledger.reserve('r','a','u',None,self.profile,10);self.ledger.start('a',self.profile)
        original=self.ledger.get('a')['day']
        self.ledger.close()
        from ournotes_bot.application.usage_ledger import UsageLedger
        self.ledger=UsageLedger(self.path,'fixture',clock=lambda:self.now[0])
        self.assertNotEqual(self.ledger.get('a')['reserved'],'0')
        self.now[0]+=301;self.ledger.reconcile()
        self.assertEqual(self.ledger.get('a')['reserved'],'0')
        self.assertEqual(self.ledger.summary()['usage_unknown'],1)
        self.now[0]+=86400
        self.ledger.settle('a',Usage(100,0,20,None,120,'known'))
        self.assertEqual(self.ledger.get('a')['day'],original)
        self.assertEqual(self.ledger.get('a')['charged'],'140')

    def test_total_only_is_partial_not_guessed_and_price_immutable(self):
        from ournotes_bot.domain.model_calls import Usage
        self.ledger.reserve('r','a','u',None,self.profile,10);self.ledger.start('a',self.profile)
        self.ledger.settle('a',Usage(total=50,status='partial'))
        self.assertEqual(self.ledger.get('a')['charged'],'0')
        self.assertEqual(self.ledger.summary()['billing_incomplete'],1)
        self.profile=replace(self.profile,price=replace(self.profile.price,input_rate=9999999))
        self.ledger.settle('a',Usage(40,0,10,None,50,'known'))
        self.assertEqual(self.ledger.get('a')['charged'],'60')

    def test_native_transport_usage_survives_invalid_tools(self):
        from ournotes_bot.sources.model_provider import decode_attempt
        payload={'usage':{'prompt_tokens':20,'prompt_cache_hit_tokens':5,'completion_tokens':10,'total_tokens':30},
                 'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','tool_calls':[{},{}]}}]}
        result=decode_attempt(json.dumps(payload),self.profile)
        self.assertEqual(result.error_code,'multiple_tools')
        self.assertEqual(result.usage.status,'known')
        self.assertEqual(result.usage.input_cached,5)
        self.assertEqual(decode_attempt(json.dumps(payload),self.profile,http_status=500).usage.status,'known')

    def test_usage_consistency_and_reasoning_are_not_double_counted(self):
        from ournotes_bot.sources.model_provider import usage_from
        p=replace(self.profile,provider='openai',wire_profile='openai-chat-v1',parallel_control=True,reasoning_usage=True)
        usage=usage_from({'prompt_tokens':100,'completion_tokens':50,'total_tokens':150,
                         'prompt_tokens_details':{'cached_tokens':20},'completion_tokens_details':{'reasoning_tokens':30}},p)
        self.assertEqual(p.price.amount(usage)[0],182)
        self.assertEqual(usage_from({'prompt_tokens':3,'completion_tokens':2,'total_tokens':1},p).status,'unknown')

    def test_native_arguments_are_closed_and_json_content_is_not_a_tool(self):
        from ournotes_bot.natural_query.native_tools import validate_call
        from ournotes_bot.domain.model_calls import ToolCall,ModelError
        for name,args in [('shell','{}'),('knowledge_search','{"server":"jp","version":null,"date":null,"url":"https://x"}'),
                          ('song_search','{"query":"a","query":"b"}')]:
            with self.assertRaises(ModelError): validate_call(ToolCall('c',name,args))

    def test_invalid_unicode_and_unsupported_thinking_preserve_known_usage(self):
        from ournotes_bot.sources.model_provider import decode_attempt,usage_from
        payload={'usage':{'prompt_tokens':20,'prompt_cache_hit_tokens':0,'completion_tokens':10},
                 'choices':[{'finish_reason':'stop','message':{'role':'assistant','content':'\ud800'}}]}
        broken=decode_attempt(json.dumps(payload),self.profile)
        self.assertEqual(broken.error_code,'model_protocol_error')
        self.assertEqual(broken.usage.input_total,20)
        payload['choices'][0]['message']={'role':'assistant','content':'answer','reasoning_content':'thinking'}
        thinking=decode_attempt(json.dumps(payload),self.profile)
        self.assertEqual(thinking.error_code,'wire_profile_unsupported')
        self.assertEqual(thinking.usage.output_total,10)
        unknown_cache=usage_from(payload['usage'],replace(self.profile,cache_usage=False))
        self.assertIsNone(unknown_cache.input_cached)
        self.assertEqual(unknown_cache.status,'partial')

    def test_shared_trusted_subject_day_rollover_and_currency_guard(self):
        from ournotes_bot.application.model_config import ConfigSnapshot
        from ournotes_bot.application.usage_ledger import day
        from ournotes_bot.domain.model_calls import ModelError
        from types import SimpleNamespace
        snapshot=ConfigSnapshot(self.profile,(('group','g1','member','same'),('c2c','private','private','same')))
        group=snapshot.subject(SimpleNamespace(kind='group',conversation_id='g1',user_id='member'))
        private=snapshot.subject(SimpleNamespace(kind='c2c',conversation_id='private',user_id='private'))
        self.assertEqual(self.ledger.key('user',group),self.ledger.key('user',private))
        self.now[0]=1792684799
        self.ledger.reserve('r','a','u',None,self.profile,10)
        self.now[0]+=86400
        self.ledger.start('a',self.profile)
        self.assertEqual(self.ledger.get('a')['day'],day(self.now[0]))
        other=replace(self.profile,price=replace(self.profile.price,currency='CNY'))
        with self.assertRaisesRegex(ModelError,'reference_unit_mismatch'):
            self.ledger.reserve('r2','b','u',None,other,10)

    def test_config_snapshot_rejects_unknown_fields_and_preserves_old_price(self):
        from dataclasses import asdict
        from ournotes_bot.application.model_config import ModelConfiguration
        from ournotes_bot.domain.model_calls import ModelError
        path=Path(self.temp.name)/'profile.json'
        profile=asdict(self.profile);profile.pop('api_key')
        data=dict(schema='taki-model-config/1',instance_id='i',app_id='a',profile=profile,bindings=[])
        path.write_text(json.dumps(data),encoding='utf-8')
        config=ModelConfiguration(path,'SECRET_FIXTURE','i','a')
        old=config.snapshot()
        self.assertNotIn('SECRET_FIXTURE',repr(old));self.assertNotIn('SECRET_FIXTURE',str(config.status()))
        data['profile']['price']['output_rate']=3000000
        path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaisesRegex(ModelError,'config_version_reused'): config.snapshot()
        data['profile']['config_version']='v2';path.write_text(json.dumps(data),encoding='utf-8')
        self.assertEqual(config.snapshot().profile.price.output_rate,3000000)
        self.assertEqual(old.profile.price.output_rate,2000000)
        data['profile']['url']='http://localhost/';path.write_text(json.dumps(data),encoding='utf-8')
        with self.assertRaisesRegex(ModelError,'model_configuration'): config.snapshot()

    def test_openrouter_price_is_exact_and_requires_same_currency(self):
        from ournotes_bot.sources.model_provider import openrouter_price
        from ournotes_bot.domain.model_calls import ModelError
        profile=replace(self.profile,provider='openrouter',wire_profile='openrouter-chat-v1')
        data={'data':[{'id':profile.model,'supported_parameters':['tools'],'pricing':{
            'prompt':'0.000001','completion':'0.000002','input_cache_read':'0.0000001','request':'0'}}]}
        price=openrouter_price(data,profile)
        self.assertEqual((price.input_rate,price.cached_rate,price.output_rate),(1000000,100000,2000000))
        self.assertFalse(price.estimated)
        with self.assertRaises(ModelError): openrouter_price(data,replace(profile,price=replace(profile.price,currency='CNY')))
        data['data'][0]['pricing']['request']='0.01'
        with self.assertRaisesRegex(ModelError,'price_dimensions_unsupported'): openrouter_price(data,profile)


class NativeTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_http_body_native_tools_error_usage_and_no_redirect_retry(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        from ournotes_bot.sources.model_provider import NativeClient,ENDPOINTS,request_body
        from ournotes_bot.natural_query.native_tools import TOOLS
        calls=[]
        async def handler(request):
            body=await request.json();calls.append(body)
            self.assertIn('tools',body);self.assertNotIn('response_format',body)
            self.assertNotIn('parallel_tool_calls',body)
            return web.json_response({'usage':{'prompt_tokens':8,'prompt_cache_hit_tokens':0,'completion_tokens':4,'total_tokens':12},
                'choices':[{'finish_reason':'tool_calls','message':{'role':'assistant','tool_calls':[{'id':'c','type':'function',
                  'function':{'name':'field_query','arguments':'not json'}}]}}]},status=500)
        app=web.Application();app.router.add_post('/chat',handler)
        server=TestServer(app);await server.start_server();self.addAsyncCleanup(server.close)
        profile=native_profile();body=request_body(profile,[{'role':'user','content':'TEST'}],[TOOLS['field_query'].wire()])
        with patch.dict(ENDPOINTS,deepseek=str(server.make_url('/chat'))):
            result=await NativeClient().request(profile,body,timeout=2)
        self.assertEqual(len(calls),1);self.assertEqual(result.usage.status,'known')
        self.assertEqual(result.error_code,'provider_error')

    async def test_actual_http_timeout_and_redirect_do_not_retry(self):
        from aiohttp import web
        from aiohttp.test_utils import TestServer
        from ournotes_bot.sources.model_provider import NativeClient,ENDPOINTS
        calls=[]
        async def handler(request):
            calls.append(request.path)
            if request.path=='/redirect': return web.HTTPFound('/target')
            await asyncio.sleep(2);return web.json_response({})
        app=web.Application();app.router.add_post('/{name}',handler)
        server=TestServer(app);await server.start_server();self.addAsyncCleanup(server.close)
        for path in ('/redirect','/timeout'):
            with patch.dict(ENDPOINTS,deepseek=str(server.make_url(path))):
                result=await NativeClient().request(native_profile(),{},timeout=1 if path=='/redirect' else .5)
            self.assertEqual(result.status,'error');self.assertEqual(result.usage.status,'unknown')
        self.assertEqual(calls,['/redirect','/timeout'])


class NativeRuntimeTests(Fixture,unittest.IsolatedAsyncioTestCase):
    from test_continuation import BackendContinuationTests as Helpers
    request=Helpers.request
    event=Helpers.event

    async def asyncSetUp(self):
        from ournotes_bot.application.model_runtime import ModelRuntime
        from ournotes_bot.application.model_config import ConfigSnapshot
        from ournotes_bot.domain.model_calls import AttemptResult,Usage
        from unittest.mock import AsyncMock
        await self.Helpers.asyncSetUp(self)
        for target in ('ournotes_bot.sources.haneoka.game_assets.asset_bytes','ournotes_bot.application.replies.visual_asset'):
            asset=patch(target,return_value=None);asset.start();self.addCleanup(asset.stop)
        self.repo.current_query_view=Mock(return_value=self.repo)
        self.client=Mock()
        self.client.request=AsyncMock(return_value=AttemptResult('ok',Usage(100,0,20,None,120,'known'),text='你好。想查什么？'))
        runtime=ModelRuntime(self.backend,self.repo.cache_file.with_name('usage-v1.sqlite3'),client=self.client)
        self.backend.model_runtime=runtime
        self.snapshot=ConfigSnapshot(native_profile(),(('group','g1','u1','trusted-player'),))
        runtime.config.snapshot=Mock(side_effect=lambda:self.snapshot)

    async def prepare_native(self,body,*,seq=1,allow=lambda n:True):
        request=self.request('native'+str(seq),seq);request['body']=body
        await self.backend.submit(request)
        attempts=[]
        for _ in range(1500):
            plan=self.backend.lookup(request['request_id'],'e1')
            challenge=plan.get('pending_model_permission')
            if challenge and challenge['attempt_id'] not in attempts:
                attempts.append(challenge['attempt_id'])
                decision={k:v for k,v in challenge.items() if k not in ('remaining_ms','admission_policy_version')}
                decision.update(decision='allow' if allow(len(attempts)) else 'deny',reason='fixture',ttl_ms=1000,
                                current_policy_version=dict(epoch='fixture',global_revision=0,group_revision=0))
                self.backend.permissions.decide(request['request_id'],challenge['attempt_id'],decision,'e1')
            if plan['status']=='prepared': return plan,attempts
            if plan['status'] not in ('preparing','prepared'): self.fail('native stopped: '+plan['status'])
            await asyncio.sleep(.005)
        self.fail('native did not finish')

    async def test_chat_uses_one_permit_and_ledger_then_ack_context(self):
        plan,attempts=await self.prepare_native('/问 你好')
        self.assertEqual(len(attempts),1);self.assertEqual(self.client.request.await_count,1)
        self.assertIn('你好',plan['parts'][0]['text'])
        row=self.backend.model_runtime.usage.get(attempts[0]);self.assertEqual(row['charged'],'140')
        self.assertNotIn('你好',str(row));self.assertNotIn('u1',str(row))
        self.event(plan,'intent');self.event(plan,'ack')
        self.assertEqual(len(self.backend.conversations.anchors),1)
        self.repo.current_query_view.assert_not_called()

    async def test_local_query_does_not_reserve_or_call_model(self):
        plan,attempts=await self.prepare_native('/问 合成曲100001 EX Note')
        self.assertFalse(attempts);self.client.request.assert_not_awaited()
        self.assertIn('200',plan['parts'][0]['text'])
        self.assertEqual(self.backend.model_runtime.usage.summary()['attempts'],0)
        self.repo.current_query_view.assert_called_once()

    def test_native_chart_preserves_explicit_difficulty_and_captured_result(self):
        from ournotes_bot.domain.model_calls import ToolCall,ModelError
        from ournotes_bot.natural_query.native_tools import validate_call,execute
        tool,args=validate_call(ToolCall('chart','chart_get','{"query":"合成曲100001","difficulty":"EXPERT"}'))
        question='请展示合成曲100001 EX 完整谱面资料'
        result=execute(tool,args,question,self.repo)
        self.assertEqual(result.results[0].chart[0].id,100001)
        self.assertEqual(result.results[0].chart[1][0].difficulty,'EXPERT')
        self.assertIn('200',result.texts[0])
        for difficulty in ('HARD',''):
            with self.assertRaises(ModelError): execute(tool,{**args,'difficulty':difficulty},question,self.repo)
        with self.assertRaises(ModelError): execute(tool,args,'请展示合成曲100001 完整谱面资料',self.repo)

    async def test_deny_releases_reservation_before_any_http(self):
        plan,attempts=await self.prepare_native('/问 你好',allow=lambda _:False)
        self.client.request.assert_not_awaited()
        self.assertEqual(self.backend.model_runtime.usage.get(attempts[0])['state'],'released')
        self.assertEqual(plan['parts'][0]['business_status'],'policy_denied')

    async def test_empty_knowledge_stops_after_tool_without_generation(self):
        from ournotes_bot.domain.model_calls import AttemptResult,ToolCall,Usage
        from ournotes_bot.application.knowledge_service import KnowledgeService
        from ournotes_bot.sources.knowledge_embeddings import LocalBgeEncoder
        self.backend.knowledge=KnowledgeService(self.repo.cache_file.parent/'knowledge',self.repo.cache_file.with_name('knowledge.sqlite3'),LocalBgeEncoder(None))
        self.backend.knowledge.refresh()
        self.client.request.return_value=AttemptResult('ok',Usage(10,0,2,None,12,'known'),
            (ToolCall('c','knowledge_search','{"server":"jp","version":null,"date":null}'),))
        plan,attempts=await self.prepare_native('/问 这个规则是什么意思')
        self.assertEqual(len(attempts),1);self.assertIn('尚无已审核资料',plan['parts'][0]['text'])

    async def test_two_business_tools_fail_once_without_repair(self):
        from ournotes_bot.domain.model_calls import AttemptResult,ToolCall,Usage
        self.client.request.return_value=AttemptResult('ok',Usage(10,0,2,None,12,'known'),
            (ToolCall('c','shell','{}'),))
        plan,attempts=await self.prepare_native('/问 你好')
        self.assertEqual(len(attempts),1);self.client.request.assert_awaited_once()
        self.assertEqual(plan['parts'][0]['business_status'],'tool_unregistered')

    async def test_unknown_identity_does_not_create_separate_scene_budget(self):
        self.snapshot=replace(self.snapshot,bindings=())
        plan,attempts=await self.prepare_native('/问 你好')
        self.assertFalse(attempts);self.assertEqual(plan['parts'][0]['business_status'],'identity_unverified')
        self.client.request.assert_not_awaited()

    async def test_queued_request_keeps_admitted_configuration_and_price(self):
        original=self.snapshot.profile
        self.backend.models.limit=0
        waiting=asyncio.create_task(self.prepare_native('/问 你好'))
        try:
            for _ in range(200):
                if self.backend.models.queue: break
                await asyncio.sleep(.005)
            self.assertTrue(self.backend.models.queue)
            self.snapshot=replace(self.snapshot,profile=replace(original,config_version='fixture-v2',
                price=replace(original.price,input_rate=9000000,pricing_version='fixture-price-v2')))
            self.backend.models.limit=1;self.backend.models.dispatch()
            plan,attempts=await waiting
            self.assertIs(self.client.request.call_args.args[0],original)
            self.assertEqual(plan['parts'][0]['business_status'],'success')
            row=self.backend.model_runtime.usage.get(attempts[0])
            self.assertEqual((row['config_version'],row['charged']),('fixture-v1','140'))
        finally:
            self.backend.models.limit=1;self.backend.models.dispatch()
            if not waiting.done(): waiting.cancel()
            await asyncio.gather(waiting,return_exceptions=True)

    async def test_explicit_field_explanation_gets_real_evidence_and_second_permit(self):
        from ournotes_bot.domain.model_calls import AttemptResult,ToolCall,Usage
        responses=[AttemptResult('ok',Usage(10,0,2,None,12,'known'),(ToolCall('field','field_query','{}'),)),
                   AttemptResult('ok',Usage(20,0,5,None,25,'known'),text='这张 EX 谱面的 Note 数为 200。')]
        bodies=[]
        async def provider(profile,body,**_):
            bodies.append(json.loads(json.dumps(body)));return responses[len(bodies)-1]
        self.client.request.side_effect=provider
        plan,attempts=await self.prepare_native('/问 解释 合成曲100001 EX Note')
        self.assertEqual(len(attempts),2,plan)
        self.assertIn('200',bodies[1]['messages'][-1]['content'])
        self.assertEqual(bodies[1]['messages'][-1]['role'],'tool')
        self.assertNotIn('tools',bodies[1])
        self.assertIn('Note 数为 200',plan['parts'][0]['text'])

    async def test_visible_explanation_uses_confirmed_local_answer_without_refetch(self):
        from ournotes_bot.domain.model_calls import AttemptResult,ToolCall,Usage
        first,_=await self.prepare_native('/问 合成曲100001 EX Note')
        self.event(first,'intent');self.event(first,'ack')
        shown=first['parts'][0]['text'];self.repo.current_query_view.reset_mock()
        replies_=[AttemptResult('ok',Usage(10,0,2,None,12,'known'),(ToolCall('shown','visible_result','{}'),)),
                  AttemptResult('ok',Usage(20,0,5,None,25,'known'),text='上条显示的是这张谱面的 Note 数。')]
        bodies=[]
        async def provider(profile,body,**_):
            bodies.append(json.loads(json.dumps(body)));return replies_[len(bodies)-1]
        self.client.request.side_effect=provider
        plan,attempts=await self.prepare_native('/问 解释一下上条结果',seq=2)
        self.assertEqual(len(attempts),2,plan)
        self.assertEqual(bodies[1]['messages'][-1]['content'],shown)
        self.repo.current_query_view.assert_not_called()
        self.assertIn('上条显示',plan['parts'][0]['text'])
        self.event(plan,'intent');self.event(plan,'ack')
        self.client.request.side_effect=[replies_[0],replies_[1]]
        next_plan,next_attempts=await self.prepare_native('/问 再解释一下',seq=3)
        self.assertEqual(len(next_attempts),2,next_plan)
        self.assertEqual(self.backend.works[next_plan['delivery_id']].prepared[0].context.facts,shown)
        self.repo.current_query_view.assert_not_called()

    async def test_two_native_calls_are_rejected_and_usage_is_still_charged(self):
        from ournotes_bot.domain.model_calls import AttemptResult,ToolCall,Usage
        self.client.request.return_value=AttemptResult('ok',Usage(10,0,2,None,12,'known'),
            (ToolCall('a','catalog_info','{"kind":"help","query":""}'),
             ToolCall('b','catalog_info','{"kind":"status","query":""}')))
        plan,attempts=await self.prepare_native('/问 你好')
        self.assertEqual(plan['parts'][0]['business_status'],'multiple_tools')
        self.assertIn('分开发送',plan['parts'][0]['text'])
        self.client.request.assert_awaited_once()
        self.assertEqual(self.backend.model_runtime.usage.get(attempts[0])['charged'],'14')

    async def test_grouped_meta_uses_one_current_snapshot_and_one_model_call(self):
        from dataclasses import asdict
        from ournotes_bot.domain.meta_query import MetaSnapshot,MetaSource,MetaSong
        from ournotes_bot.domain.model_calls import AttemptResult,ToolCall,Usage,ModelError
        from ournotes_bot.natural_query.native_tools import grounded_meta,meta_questions,META_SCHEMA
        from ournotes_bot.query.song_metrics import parse_meta_question
        question='MyGO EX 27级以上最短和最长的前3首'
        specs=[parse_meta_question(q,self.repo) for q in meta_questions(question)]
        args=json.loads(json.dumps({'queries':[{k:v for k,v in asdict(s).items() if k in META_SCHEMA['properties']} for s in specs]}))
        bad=json.loads(json.dumps(args));bad['queries'][1]['filters']=[]
        with self.assertRaises(ModelError): grounded_meta(question,bad,self.repo)
        charts=((),(),(),(('level',28),('notes',200),('nps',2),('bpm_first',100),('bpm_min',100),('bpm_max',100)))
        rows=tuple(MetaSong(str(i),'工程合成曲'+str(i),(1,),'MyGO!!!!!','',float(i),charts) for i in (100,200,300,400))
        self.repo.song_metrics.current=Mock(return_value=MetaSnapshot(MetaSource('jp','fixture-release','fixture-source',1),rows))
        self.client.request.return_value=AttemptResult('ok',Usage(10,0,2,None,12,'known'),
            (ToolCall('metrics','song_metrics',json.dumps(args)),))
        with patch('ournotes_bot.visuals._asset',return_value=None):
            plan,attempts=await self.prepare_native('/问 '+question)
        self.assertEqual(len(attempts),1,plan)
        self.client.request.assert_awaited_once()
        self.repo.song_metrics.current.assert_called_once_with('jp')
        self.assertEqual(len(plan['parts']),2,plan)
        prepared=self.backend.works[plan['delivery_id']].prepared
        self.assertIn('工程合成曲100',prepared[0].text)
        self.assertIn('工程合成曲400',prepared[1].text)

    async def test_native_detail_uses_current_view_when_legacy_repository_is_older(self):
        from copy import copy
        view=copy(self.repo);view.metadata={'release':'new-fixture'}
        self.repo.current_query_view.return_value=view
        with patch('ournotes_bot.visuals._asset',return_value=None), \
             patch('ournotes_bot.application.replies.load_chart_data',side_effect=ValueError('isolated chart unavailable')), \
             patch('ournotes_bot.application.replies.load_chart_score',side_effect=ValueError('isolated chart unavailable')):
            first,_=await self.prepare_native('/问 查曲 MyGO')
            self.event(first,'intent');self.event(first,'ack')
            second,attempts=await self.prepare_native('/问 第一个',seq=2)
        self.assertFalse(attempts)
        self.assertNotIn('资料版本',second['parts'][0]['text'])
        self.assertIn('合成曲100001',second['parts'][0]['text'])
        self.assertGreaterEqual(self.repo.current_query_view.call_count,2)

    def reviewed_fixture(self):
        from ournotes_bot.application.knowledge_service import KnowledgeService
        from ournotes_bot.sources.knowledge_files import parse_document
        root=self.repo.cache_file.parent/'knowledge';root.mkdir()
        meta=dict(schema='taki-knowledge/1',id='fixture',title='工程夹具',keywords=['红色'],aliases=[],
            game='bang-dream-our-notes',servers=['jp'],applicability=dict(mode='current',game_version=None,
            valid_from=None,valid_to=None,scope_note='仅工程夹具'),sources=[dict(source_id='fixture',kind='fixture',
            uri='fixture://isolated',locator='isolated',accessed_at='2026-10-23')],evidence_type='reviewed_analysis',
            review_status='approved',reviewed_by='ISOLATED-FIXTURE',reviewed_at='2026-10-23',limitations=['不是游戏事实'])
        text='---\n'+json.dumps(meta,ensure_ascii=False)+'\n---\n红色夹具标记用于工程测试，不是游戏事实。\n'
        (root/'fixture.md').write_text(text,encoding='utf-8')
        parsed=parse_document(text)
        approval=dict(schema='taki-knowledge-approvals/1',revision=1,approved=[dict(id='fixture',
            content_sha256=parsed.content_sha256,approval_revision=1,approved_by='ISOLATED-FIXTURE',
            approved_at='2026-10-23',confirmation_ref='fixture://isolated')],revoked=[])
        (root/'approvals.json').write_text(json.dumps(approval),encoding='utf-8')
        class Encoder:
            identity='isolated-runtime/1';dimensions=2
            def encode(self,texts,*,query=False): return tuple((1.,0.) for _ in texts)
        self.backend.knowledge=KnowledgeService(root,self.repo.cache_file.with_name('knowledge.sqlite3'),Encoder())
        self.assertEqual(self.backend.knowledge.refresh()['code'],'ready')
        return root/'fixture.md'

    def knowledge_responses(self):
        from ournotes_bot.domain.model_calls import AttemptResult,ToolCall,Usage
        usage=Usage(10,0,2,None,12,'known')
        return [AttemptResult('ok',usage,(ToolCall('c','knowledge_search','{"server":"jp","version":null,"date":null}'),)),
                AttemptResult('ok',usage,text='红色标记是工程夹具，不能当作游戏事实。')]

    async def test_knowledge_second_attempt_gets_fresh_permit_without_tools(self):
        self.reviewed_fixture();results=self.knowledge_responses();bodies=[]
        async def provider(profile,body,**_):
            bodies.append(json.loads(json.dumps(body)));return results[len(bodies)-1]
        self.client.request.side_effect=provider
        plan,attempts=await self.prepare_native('/问 红色规则是什么意思')
        self.assertEqual(len(set(attempts)),2)
        self.assertIn('tools',bodies[0]);self.assertNotIn('tools',bodies[1])
        self.assertEqual(bodies[1]['messages'][-1]['role'],'tool')
        self.assertIn('content_sha256',bodies[1]['messages'][-1]['content'])
        self.assertIn('工程夹具',plan['parts'][0]['text'])
        self.event(plan,'intent');self.event(plan,'ack')
        anchor=next(iter(self.backend.conversations.anchors.values()))
        self.assertTrue(anchor.context.evidence)

    async def test_revoked_before_second_permit_releases_without_generation(self):
        file=self.reviewed_fixture();self.client.request.side_effect=self.knowledge_responses()
        def allow(n):
            if n==2: file.unlink()
            return True
        plan,attempts=await self.prepare_native('/问 红色规则是什么意思',allow=allow)
        self.assertEqual(len(attempts),2);self.assertEqual(self.client.request.await_count,1)
        self.assertEqual(plan['parts'][0]['business_status'],'knowledge_revoked')
        self.assertEqual(self.backend.model_runtime.usage.get(attempts[1])['state'],'released')

    async def test_revoked_during_generation_discards_answer_but_settles_both(self):
        file=self.reviewed_fixture();results=self.knowledge_responses();calls=[]
        async def provider(*args,**kwargs):
            calls.append(1)
            if len(calls)==2: file.unlink()
            return results[len(calls)-1]
        self.client.request.side_effect=provider
        plan,attempts=await self.prepare_native('/问 红色规则是什么意思')
        self.assertEqual(plan['parts'][0]['business_status'],'knowledge_revoked')
        self.assertEqual([self.backend.model_runtime.usage.get(a)['charged'] for a in attempts],['14','14'])
        self.assertNotIn('红色标记',plan['parts'][0]['text'])

    async def test_revoked_after_intent_keeps_ack_fact_without_context(self):
        file=self.reviewed_fixture();self.client.request.side_effect=self.knowledge_responses()
        plan,_=await self.prepare_native('/问 红色规则是什么意思')
        self.event(plan,'intent');file.unlink()
        receipt=self.event(plan,'ack')
        self.assertEqual(receipt['parts'][0]['status'],'success')
        self.assertFalse(self.backend.conversations.anchors)

    async def test_second_generation_denial_keeps_first_usage(self):
        self.reviewed_fixture();self.client.request.side_effect=self.knowledge_responses()
        plan,attempts=await self.prepare_native('/问 红色规则是什么意思',allow=lambda n:n==1)
        self.assertEqual(self.client.request.await_count,1)
        self.assertEqual(plan['parts'][0]['business_status'],'policy_denied')
        self.assertEqual(self.backend.model_runtime.usage.get(attempts[0])['charged'],'14')
        self.assertEqual(self.backend.model_runtime.usage.get(attempts[1])['charged'],'0')

    async def test_late_uncancellable_usage_settles_but_no_answer(self):
        from ournotes_bot.domain.model_calls import AttemptResult,Usage
        self.backend.models.execution_seconds=.3
        async def provider(*args,**kwargs):
            try: await asyncio.sleep(1)
            except asyncio.CancelledError: await asyncio.sleep(.03)
            return AttemptResult('ok',Usage(20,0,3,None,23,'known'),text='LATE_CONTENT_SENTINEL')
        self.client.request.side_effect=provider
        plan,attempts=await self.prepare_native('/问 你好')
        await asyncio.gather(*tuple(self.backend.models.tasks),return_exceptions=True)
        self.assertEqual(plan['parts'][0]['business_status'],'execution_timeout')
        row=self.backend.model_runtime.usage.get(attempts[0])
        self.assertEqual(row['charged'],'26',row)
        self.assertNotIn('LATE_CONTENT_SENTINEL',str(self.backend.conversations.snapshots))
        self.assertFalse(self.backend.models.running)

    async def test_current_source_failure_does_not_fall_back_or_call_model(self):
        from ournotes_bot.data import DataError
        self.repo.current_query_view.side_effect=DataError('fixture upstream unavailable')
        plan,attempts=await self.prepare_native('/问 合成曲100001 EX Note')
        self.assertFalse(attempts);self.client.request.assert_not_awaited()
        self.assertEqual(plan['parts'][0]['business_status'],'source_unavailable')

    async def test_slow_hint_uses_same_ledger_sequence_without_context(self):
        from ournotes_bot.domain.model_calls import AttemptResult,Usage
        import time
        held=asyncio.Event();entered=asyncio.Event()
        async def provider(*args,**kwargs):
            entered.set();await held.wait()
            return AttemptResult('ok',Usage(20,0,3,None,23,'known'),text='最终回答')
        self.client.request.side_effect=provider
        task=asyncio.create_task(self.prepare_native('/问 你好'))
        await asyncio.wait_for(entered.wait(),1)
        work=next(iter(self.backend.works.values()));work.model_started_at=time.monotonic()-9
        plan=self.backend.lookup(work.request.request_id,'e1')
        self.assertEqual(plan['status'],'preparing');self.assertTrue(plan['send_allowed'])
        self.event(plan,'intent');self.event(plan,'ack')
        self.assertFalse(self.backend.conversations.anchors)
        self.assertEqual(len(self.backend.lookup(work.request.request_id,'e1')['parts']),1)
        held.set();plan,_=await task
        self.assertEqual(len(plan['parts']),2);self.assertEqual(plan['parts'][0]['status'],'success')
        self.assertEqual(plan['parts'][1]['text'],'最终回答')

    def test_progress_slot_preserves_all_pages_and_their_entity_contexts(self):
        from ournotes_bot.application.replies import expand_replies,reserve_progress_slot
        context=replace(self.context(),origin='model')
        pages=tuple(PreparedReply('item-'+str(i),context=replace(context,page=i+1),parse_origin='model') for i in range(6))
        initial=expand_replies([PreparedReply('',pages=pages,parse_origin='model')])
        limited=reserve_progress_slot(initial)
        self.assertEqual(len(limited),4)
        for index,part in enumerate(limited):
            self.assertEqual(part.context.query_context.page,index+1)
            self.assertEqual(part.context.origin,'model')
            self.assertEqual(part.text.count('结果 '),1)
        remaining=limited[-1].context.remaining
        self.assertEqual([p.text for p in remaining],['item-4','item-5'])
        self.assertEqual([p.context.page for p in remaining],[5,6])
        ordinary=[PreparedReply('command-'+str(i),parse_origin='direct') for i in range(5)]
        limited=reserve_progress_slot(ordinary)
        self.assertEqual(limited[-1].context.remaining[0].text,'command-4')
        self.assertEqual(limited[-1].context.origin,'mixed')


class NaturalPolicyTests(Fixture, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.setup_repo()
        self.parser._agent.ai_enabled=True

    async def test_failed_observation_worker_cannot_consume_shutdown_cancellation(self):
        import threading
        from ournotes_bot.application.runtime_tasks import finish_thread
        started,release=threading.Event(),threading.Event()
        def worker():
            started.set();release.wait(2);raise OSError('fixture failed during cancellation')
        task=asyncio.create_task(finish_thread(worker))
        await asyncio.to_thread(started.wait,1)
        task.cancel();release.set()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,1)

    def run_mode(self,mode,content):
        token=CURRENT_POLICY.set(ExecutionPolicy(natural_query=mode))
        try:return self.parser.answer_with_outcome(content,self.repo)
        finally:CURRENT_POLICY.reset(token)

    def test_off_local_normal_invalid_and_repair_paths_never_call_model_or_quota(self):
        with patch.object(self.parser,'_request',side_effect=AssertionError('model forbidden')) as model, \
             patch.object(self.parser._quota,'reserve',side_effect=AssertionError('quota forbidden')) as quota:
            for mode in ('off','local'):
                for question in ('/问 查曲 MyGO','/问 那首歌的谱面','/问','/问 推荐最强阵容','/问 展示合成曲100001完整谱面资料'):
                    result=self.run_mode(mode,question)
                    self.assertEqual(result.state.model_calls,0)
            model.assert_not_called();quota.assert_not_called()

    def test_model_and_unknown_origin_caches_are_not_reused_in_local(self):
        self.parser._agent._sync_cache(self.repo)
        question='一个无法本地理解的测试问法'
        for origin in ('model','unknown'):
            self.parser._agent._cache_put(question,CachedOutcome('terminal',OutcomeCode.UNSUPPORTED,message='MODEL_CACHE_SENTINEL',origin=origin))
            with patch.object(self.parser,'_request',side_effect=AssertionError('model forbidden')):
                self.assertNotIn('MODEL_CACHE_SENTINEL',self.run_mode('local','/问 '+question).text)
        self.parser._agent._cache_put(question,CachedOutcome('terminal',OutcomeCode.UNSUPPORTED,message='LOCAL_CACHE_SENTINEL',origin='local'))
        self.assertEqual(self.run_mode('local','/问 '+question).text,'LOCAL_CACHE_SENTINEL')

    async def test_concurrent_scopes_do_not_mutate_shared_model_switch(self):
        seen=[]
        def model(*args):
            seen.append(CURRENT_POLICY.get().natural_query)
            return {'intent':'unsupported','query':'','difficulty':''}
        async def worker(mode):
            token=CURRENT_POLICY.set(ExecutionPolicy(natural_query=mode))
            try:
                return await asyncio.to_thread(self.parser.answer_with_outcome,'/问 那首歌的谱面',self.repo)
            finally:CURRENT_POLICY.reset(token)
        with patch.object(self.parser,'_request',side_effect=model):
            results=await asyncio.gather(worker('local'),worker('model'),worker('off'))
        self.assertTrue(seen)
        self.assertEqual(set(seen),{'model'})
        self.assertEqual(results[0].state.model_calls,0)
        self.assertEqual(results[2].state.model_calls,0)
        self.assertTrue(self.parser._agent.ai_enabled)

    async def test_mixed_direct_commands_keep_order_while_ask_is_off(self):
        token=CURRENT_POLICY.set(ExecutionPolicy(natural_query='off'))
        try:
            with patch.object(self.parser,'_request',side_effect=AssertionError('model forbidden')) as model:
                parts,overflow=await prepare_commands(['/帮助','/问 那首歌的谱面','/查卡 947'],QueryGate(2,4),self.repo,self.parser,5)
            self.assertEqual(overflow,0)
            self.assertEqual([p.command_index for p in parts],[0,1,2])
            self.assertIn('关闭',parts[1].text)
            self.assertTrue(parts[2].image)
            model.assert_not_called()
        finally:CURRENT_POLICY.reset(token)

    def test_continuation_requires_origin_under_restricted_policy(self):
        for mode in ('off','local'):
            token=CURRENT_POLICY.set(ExecutionPolicy(natural_query=mode))
            try:
                for origin in ('model','unknown'):
                    context=DeliveryCursor(None,(PreparedReply('PRIVATE_MODEL_SENTINEL'),),1,2,origin=origin)
                    result=prepare_followup(context,Operation('continue'),self.repo)
                    self.assertIn('不允许',result.text)
                    self.assertFalse(result.pages)
                direct=DeliveryCursor(None,(PreparedReply('direct'),),1,2,origin='direct')
                self.assertEqual(prepare_followup(direct,Operation('continue'),self.repo).pages[0].text,'direct')
            finally:CURRENT_POLICY.reset(token)

    def test_mixed_continuation_preserves_direct_order_without_model_content(self):
        token=CURRENT_POLICY.set(ExecutionPolicy(natural_query='off'))
        try:
            context=DeliveryCursor(None,(PreparedReply('MODEL_SECRET',parse_origin='model'),
                PreparedReply('DIRECT_SENTINEL',parse_origin='direct')),1,3,origin='mixed')
            result=prepare_followup(context,Operation('continue'),self.repo)
            self.assertNotIn('MODEL_SECRET',str(result.pages))
            self.assertIn('不允许',result.pages[0].text)
            self.assertEqual(result.pages[1].text,'DIRECT_SENTINEL')
        finally:CURRENT_POLICY.reset(token)


class RuntimeRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_knowledge_watch_runs_while_candidate_build_is_blocked(self):
        import threading
        from types import SimpleNamespace
        from ournotes_bot.application.runtime_tasks import RuntimeTasks
        started, release, observed_twice = threading.Event(), threading.Event(), threading.Event()
        watches = []
        def build():
            started.set(); release.wait(10)
            return {'code': 'empty'}
        def watch():
            watches.append(True)
            if len(watches) >= 2: observed_twice.set()
            return {'code': 'empty'}
        repository = SimpleNamespace(refresh_song_traits=lambda: None)
        owner = RuntimeTasks(repository, SimpleNamespace(cutoff_sampling_enabled=False),
                             SimpleNamespace(refresh=build, watch=watch))
        try:
            owner.start()
            self.assertTrue(await asyncio.to_thread(started.wait, 2))
            self.assertTrue(await asyncio.to_thread(observed_twice.wait, 3))
            observation = owner.observation_snapshot()['refreshes']['watch_knowledge']
            self.assertEqual(observation['consecutive_failures'], 0)
        finally:
            release.set()
            await owner.close()

    async def test_backend_bootstrap_owns_knowledge_and_releases_it(self):
        from types import SimpleNamespace
        from ournotes_bot.application.backend_bootstrap import build_backend
        from ournotes_bot.application.knowledge_service import KnowledgeService
        from ournotes_bot.sources.knowledge_embeddings import LocalBgeEncoder
        from ournotes_bot.config import Settings
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = Settings('', '', 'https://fixture.invalid', root/'data/cache.json', 6,
                                update_notices=False, knowledge_dir=root/'knowledge')
            args = SimpleNamespace(port=8765, instance_id='fixture', app_id='fixture', state_dir=str(root/'state'))
            with patch.dict(os.environ, {'OURNOTES_BACKEND_TOKEN': 'x'*32, 'OURNOTES_RUNTIME_DATA_DIR': str(root/'data')}), \
                 patch('ournotes_bot.application.backend_bootstrap.create_app', side_effect=lambda backend, *args: backend):
                backend = build_backend(args, settings)
            try:
                self.assertEqual(backend.knowledge.status()['code'], 'empty')
                self.assertIs(backend.runtime_tasks.knowledge, backend.knowledge)
            finally:
                await backend.close()
            replacement = KnowledgeService(root/'knowledge', root/'state/knowledge-v1.sqlite3', LocalBgeEncoder(None))
            try:
                self.assertEqual(replacement.refresh()['code'], 'empty')
            finally:
                replacement.close()

    async def test_recovery_replaces_only_exited_task_and_blocks_shutdown(self):
        from types import SimpleNamespace
        from ournotes_bot.application.runtime_tasks import RuntimeTasks
        import asyncio
        repository=SimpleNamespace(refresh_song_traits=lambda:None)
        owner=RuntimeTasks(repository,SimpleNamespace(cutoff_sampling_enabled=False))
        self.assertEqual(owner.start()['restored'],['catalog','traits'])
        first=dict(owner.named_tasks)
        self.assertEqual(owner.recover()['restored'],[])
        first['catalog'].cancel()
        await asyncio.gather(first['catalog'],return_exceptions=True)
        result=owner.recover()
        self.assertEqual(result['restored'],['catalog'])
        self.assertIs(owner.named_tasks['traits'],first['traits'])
        await owner.close()
        with self.assertRaisesRegex(RuntimeError,'runtime_closing'):owner.recover()

    async def test_manual_catalog_reports_unsaved_without_false_success(self):
        from types import SimpleNamespace
        from ournotes_bot.application.runtime_tasks import RuntimeTasks
        repository=SimpleNamespace(synchronize_catalog=lambda force,expected:{'code':'updated_unsaved'})
        owner=RuntimeTasks(repository,SimpleNamespace(cutoff_sampling_enabled=False))
        self.assertEqual(owner.synchronize_catalog(False,None)['code'],'updated_unsaved')
        observed=owner.observation_snapshot()['refreshes']['refresh_catalog']
        self.assertEqual(observed['consecutive_failures'],1)
        self.assertNotIn('last_successful_at',observed)
        self.assertFalse(observed['running'])
        await owner.close()


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.console=self.root/'console';self.console.mkdir()
        self.journal=self.root/'trusted'/'freeze.json'
        self.config=self.console/'settings.json';self.config.write_text(json.dumps(DEFAULT))
        self.original=self.config.read_bytes()
        self.policy=self.console/'group-policies.v1.json';self.policy.write_text('{"schema":1,"fixture":true}')

    def test_freeze_before_old_start_and_exact_restore_keep_policy_and_business_files(self):
        business=self.root/'business.sqlite3';business.write_bytes(b'BUSINESS_SENTINEL')
        policy=self.policy.read_bytes()
        permit=transition(self.console,self.journal,False)
        self.assertFalse(json.loads(self.config.read_bytes())['queriesEnabled'])
        self.assertFalse(json.loads(self.config.read_bytes())['notificationsEnabled'])
        verify_start(self.console,permit,False)
        self.assertEqual(transition(self.console,self.journal,False)['state'],'frozen')
        self.assertEqual(transition(self.console,self.journal,True)['state'],'restored')
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertEqual(self.policy.read_bytes(),policy)
        self.assertEqual(business.read_bytes(),b'BUSINESS_SENTINEL')

    def test_combination_verifier_does_not_create_bytecode_in_payload(self):
        import hashlib,shutil,zipfile
        from scripts.check_koishi_release import verify
        stage=self.root/'combination';(stage/'controller').mkdir(parents=True)
        shutil.copyfile(Path(__file__).resolve().parents[1]/'deploy/koishi_control.py',stage/'controller/koishi_control.py')
        commit='a'*40
        with zipfile.ZipFile(stage/'python.zip','w') as archive:archive.writestr('manifest.json',json.dumps({'commit':commit}))
        files={p.relative_to(stage).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.rglob('*') if p.is_file()}
        node={'version':'v24.19.0','platform':'linux','arch':'x64'}
        manifest={'schema':1,'commit':commit,'protocol':{'major':1,'minor':1},'capabilities':['group_policies_v1'],
            'node':node,'files':files,'python_artifact':'python.zip','python_sha256':files['python.zip']}
        (stage/'combination.json').write_text(json.dumps(manifest))
        with patch('scripts.check_koishi_release.subprocess.check_output',return_value=json.dumps(node)),patch('scripts.check_koishi_release.subprocess.run'):
            self.assertEqual(verify(stage,commit),manifest)
        self.assertFalse(list(stage.rglob('*.pyc')))
        from deploy.koishi_control import verify_combination
        upgraded={**manifest,'protocol':{'major':2,'minor':0},
                  'capabilities':['group_policies_v1','query_chat_contract_v1']}
        (stage/'combination.json').write_text(json.dumps(upgraded))
        self.assertEqual(verify_combination(stage,commit),upgraded)
        (stage/'combination.json').write_text(json.dumps({**upgraded,'capabilities':['group_policies_v1']}))
        with self.assertRaisesRegex(ValueError,'combination_capabilities'):verify_combination(stage,commit)
        for protocol in ({'major':True,'minor':1},{'major':1.0,'minor':1},{'major':1,'minor':1,'extra':True}):
            (stage/'combination.json').write_text(json.dumps({**manifest,'protocol':protocol}))
            with self.assertRaisesRegex(ValueError,'combination_protocol'):verify_combination(stage,commit)

    def test_manual_settings_or_policy_edit_is_preserved_and_blocks_old_start(self):
        permit=transition(self.console,self.journal,False)
        changed=json.loads(self.config.read_bytes());changed['privateEnabled']=False
        self.config.write_text(json.dumps(changed))
        before=self.config.read_bytes()
        self.assertEqual(transition(self.console,self.journal,True)['state'],'conflict')
        self.assertEqual(self.config.read_bytes(),before)
        with self.assertRaises(ValueError):transition(self.console,self.journal,False)
        with self.assertRaises(ValueError):verify_start(self.console,permit,False)

    def test_write_failure_keeps_stopped_and_can_complete_prepared_journal(self):
        import deploy.group_policy_guard as guard
        original=guard.atomic
        def failing(path,data,attributes=None):
            if Path(path)==self.config:raise OSError('disk full')
            return original(path,data,attributes)
        with patch.object(guard,'atomic',side_effect=failing),self.assertRaises(OSError):
            transition(self.console,self.journal,False)
        self.assertEqual(self.config.read_bytes(),self.original)
        self.assertEqual(transition(self.console,self.journal,False)['state'],'frozen')

    def test_policy_change_prevents_restore_and_symlinks_rejected(self):
        transition(self.console,self.journal,False)
        self.policy.write_text('changed policy')
        self.assertEqual(transition(self.console,self.journal,True)['state'],'conflict')
        self.assertFalse(json.loads(self.config.read_bytes())['queriesEnabled'])

    @unittest.skipIf(os.name == 'nt','POSIX mode and ownership require Linux')
    def test_freeze_restore_preserves_posix_owner_mode_and_rejects_symlink(self):
        self.config.chmod(0o640)
        before=self.config.stat()
        transition(self.console,self.journal,False)
        transition(self.console,self.journal,True)
        after=self.config.stat()
        self.assertEqual((before.st_uid,before.st_gid,before.st_mode),(after.st_uid,after.st_gid,after.st_mode))
        self.config.unlink();self.config.symlink_to(self.policy)
        with self.assertRaisesRegex(ValueError,'policy_guard_file'):transition(self.console,self.journal,False)

    def test_launcher_checks_frozen_config_before_backend_and_gateway_exec(self):
        from deploy import koishi_launcher as launcher
        base=self.root/'base';base.mkdir();code=base/'current';code.mkdir()
        console=base/'console-state';console.mkdir()
        (console/'group-policies.v1.json').write_text('{}')
        (console/'settings.json').write_text(json.dumps(DEFAULT))
        guard=transition(console,self.root/'guard.json',False)
        commit='b3d7c4267f8dd304ff0ce59dde2d9e3ff287c35a';guard['legacy_commit']=commit
        combo=self.root/'combinations'/commit;combo.mkdir(parents=True)
        (combo/'combination.json').write_text(json.dumps({'commit':commit,'protocol':{'major':1,'minor':0}}))
        permit=self.root/'permit.json';permit.write_text(json.dumps({'release':{'code_dir':str(code),'commit':commit,'venv_dir':str(base/'venv')},'boot_id':'fixture','policy_guard':guard}))
        boot=self.root/'boot';boot.write_text('fixture')
        def paths(value):
            return {'/run/taki-updater/allowed.json':permit,'/proc/sys/kernel/random/boot_id':boot,'/opt/taki-combinations':combo.parent}.get(str(value),Path(value))
        environment={'TAKI_CONSOLE_STATE_DIR':str(console),'TAKI_CONSOLE_ENABLED':'1','TAKI_INSTANCE_ID':'fixture','QQ_APP_ID':'app'}
        with patch.object(launcher,'Path',side_effect=paths),patch.object(launcher.os,'chdir'),patch.object(launcher.os,'execv') as execute,patch.dict(os.environ,environment,clear=True):
            launcher.launch(base,'backend');launcher.launch(base,'gateway')
            self.assertEqual(execute.call_count,2)
            (console/'settings.json').write_text(json.dumps(DEFAULT))
            with self.assertRaisesRegex(ValueError,'not_frozen'):launcher.launch(base,'gateway')
            self.assertEqual(execute.call_count,2)

    def test_controller_keeps_unreviewed_legacy_runtime_stopped_after_freeze(self):
        from types import SimpleNamespace
        from deploy.koishi_control import KoishiControl
        base=self.root/'base';base.mkdir();console=base/'console-state';console.mkdir()
        (console/'group-policies.v1.json').write_text('{}');(console/'settings.json').write_text(json.dumps(DEFAULT))
        backend=SimpleNamespace(config={'base':str(base)},state=self.root/'trusted-state',current_release=lambda:None)
        control=KoishiControl(backend);control.combination=Mock(return_value=(None,{'protocol':{'major':1,'minor':0},'commit':'f'*40}))
        with self.assertRaisesRegex(ValueError,'legacy_unverified'):control.prepare_start()
        self.assertFalse(json.loads((console/'settings.json').read_bytes())['queriesEnabled'])

    def test_controller_accepts_formal_v1a_only_with_verified_freeze(self):
        from types import SimpleNamespace
        from deploy.koishi_control import KoishiControl
        base=self.root/'base';base.mkdir();console=base/'console-state';console.mkdir()
        (console/'group-policies.v1.json').write_text('{}')
        (console/'settings.json').write_text(json.dumps(DEFAULT))
        backend=SimpleNamespace(config={'base':str(base)},state=self.root/'trusted-state',current_release=lambda:None)
        commit='38ce5a8a8fcae32e29df3e9c925282722d60528e'
        control=KoishiControl(backend)
        control.combination=Mock(return_value=(None,{'protocol':{'major':1,'minor':0},'commit':commit}))
        permit=control.prepare_start()
        self.assertEqual(permit['legacy_commit'],commit)
        self.assertEqual(permit['state'],'frozen')
        verify_start(console,permit,False)
        frozen=json.loads((console/'settings.json').read_bytes())
        self.assertFalse(frozen['queriesEnabled'])
        self.assertFalse(frozen['notificationsEnabled'])
