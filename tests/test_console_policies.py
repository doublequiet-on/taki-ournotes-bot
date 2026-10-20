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
