"""Offline probes using the existing isolated backend, approval and story fixtures."""
import json
from hashlib import sha256
from unittest import SkipTest
from unittest.mock import patch

from test_console_policies import ChatResearchTaskTests, NativeRuntimeTests
from test_platform_boundary import KnowledgeAuthorityTests, StoryPublicationTests

from ournotes_bot.domain.knowledge import KnowledgeScope
from ournotes_bot.sources.story_files import source_content_hash


class RuleProbe(KnowledgeAuthorityTests):
    def measure(self):
        case = self.case
        self.write_fixture(text=case['body'])
        mode = case['mode']
        self.encoder.fail = mode == 'build'
        self.service.refresh()
        scope = KnowledgeScope(server='intl' if mode == 'outside' else 'jp')
        encode = self.encoder.encode

        def encode_or_fail(texts, *, query=False):
            if mode == 'query' and query:
                raise RuntimeError('fixture query encoding failed')
            return encode(texts, query=query)

        with patch.object(self.encoder, 'encode', side_effect=encode_or_fail):
            result = self.service.retrieve(case['query'], scope, self.service.approved_set_revision,
                                           limit=6, max_chars=6000)
        chunks = result.lease.chunks
        passage = '\n'.join(chunk.text for chunk in chunks)
        valid = bool(chunks) and self.service.valid(result.lease)
        self.observed.update(code=result.code, index=self.service.status()['code'],
                             chunks=len(chunks), valid=valid,
                             matched=[marker in passage for marker in case['need']])
        if mode == 'outside':
            self.assertEqual((result.code, len(chunks)), ('missing', 0))
        else:
            self.assertEqual(result.code, 'ok')
            self.assertTrue(valid and all(self.observed['matched']))


class OfficialSummaryProbe(StoryPublicationTests):
    def measure(self):
        if self.sample is None:
            raise SkipTest('frozen official synopsis sample not supplied')
        raw = self.sample.read_bytes()
        if sha256(raw).hexdigest() != 'cee3faa26b01b402498a9f57a5b643065efb197e186ade4cd953e3a1da5747e9':
            raise ValueError('official synopsis sample fingerprint mismatch')
        value = json.loads(raw)
        self.service.import_value(value)
        hits = self.service.search(self.case['query'], limit=3, facts_only=True)
        self.observed['hits'] = len(hits['items'])
        self.assertGreater(self.observed['hits'], 0)
        evidence = self.service.read(hits['items'][0]['handle'], before=1, after=1)
        scene, = evidence['scenes']
        target, = value['scenes']
        self.observed.update(scene=scene['scene_id'], source_kind=scene['source_kind'],
                             revision=evidence['source_revision'])
        self.assertEqual(scene['scene_id'], target['scene_id'])
        self.assertEqual(scene['source_kind'], 'official_summary')
        self.assertEqual(scene['utterances'], target['utterances'])


class ChatProbe(ChatResearchTaskTests):
    async def measure(self):
        case = self.case
        agent = self.backend.model_runtime.agent
        bodies = self.capture_native_bodies()
        self.client.request.return_value = self.result('收到这条消息')
        if case['kind'] == 'chapters':
            story = StoryPublicationTests()
            story.setUp()
            self.addCleanup(story.doCleanups)
            bundle = story.bundle()
            # Use two distinct chapters at the same source layer, with no cross-chapter neighbor.
            scenes = bundle['scenes'][:2]
            for i, scene in enumerate(scenes):
                chapter = f'第{i + 1}章'
                scene.update(title=chapter, chapter=chapter, participants=case['people'],
                             previous_scene=None, next_scene=None)
                scene['utterances'][0].update(speaker=case['people'][i], text_ja=case['lines'][i],
                                               translation=case['translations'][i])
            bundle['scenes'] = scenes
            bundle['sources'][0]['content_sha256'] = source_content_hash(scenes)
            story.publish(bundle)
            agent.story = story.service
        elif case['kind'] == 'coverage':
            NativeRuntimeTests.reviewed_fixture(self, case['body'])
            requirements = [dict(label=label, entity_refs=[], fields=[], knowledge=True, difficulty='')
                            for label in case['requirements']]
            self.client.request.side_effect = [
                self.result(calls=(self.call('research_task', action='set', goal=case['question'],
                                             entity_refs=[], requirements=requirements),)),
                self.result(calls=(self.call('knowledge_search', server='jp', date=None,
                                             version=None, query=case['requirements'][0]),)),
                self.result('查到第一项，第二项还缺资料。'),
            ]
        plan, permits = await self.prepare_native('/聊 ' + case['question'])
        events = self.backend.diagnostics.read(plan['request_id'])['chat_events']
        self.observed.update(model_calls=self.client.request.await_count, permits=len(permits),
                             evidence=[e['data']['action'] for e in events if e['kind'] == 'evidence'],
                             status=plan['parts'][0]['business_status'])
        self.assertEqual(self.observed['status'], 'success')
        if case['kind'] == 'context':
            messages = bodies[-1]['messages']
            self.assertTrue(any(case['question'] in msg['content'] for msg in messages if msg['role'] == 'user'))
            self.assertEqual(self.observed['model_calls'], 1)
            self.assertFalse(set(self.observed['evidence']) & {'story_search', 'story_read', 'knowledge_search', 'catalog_info'})
            self.assertFalse(any(msg['role'] == 'tool' for msg in messages))
            self.assertEqual(plan['parts'][0]['text'], '收到这条消息')
        elif case['kind'] == 'chapters':
            messages = bodies[0]['messages']
            wire = '\n'.join(m['content'] for m in messages if m['role'] in ('user', 'tool'))
            fields = [line in wire for line in case['lines']]
            offered = {tool['function']['name'] for tool in bodies[0].get('tools', [])}
            can_continue = {'story_search', 'story_read'} <= offered
            self.observed.update(chapters_at_first_call=fields, story_tools_available=can_continue)
            self.assertTrue(all(fields) or can_continue, 'incomplete chapter evidence and no further story tools')
        elif case['kind'] == 'coverage':
            work = self.backend.works[plan['delivery_id']]
            coverage = work.chat_task.coverage()
            completed = [item['complete'] for item in coverage['items']]
            tools = [json.loads(m['content']) for m in bodies[-1]['messages'] if m['role'] == 'tool']
            self.observed.update(tool_status=[tool['status'] for tool in tools], completed=completed)
            self.assertTrue(tools and tools[-1]['status'] == 'ok')
            self.assertIn(case['body'], json.dumps(tools[-1], ensure_ascii=False))
            self.assertEqual(len(completed), 2)
            self.assertFalse(completed[1], 'unsupported second requirement marked complete')
