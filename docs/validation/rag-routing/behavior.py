"""Round-two observer at the simulated provider boundary. No tool handles are pre-issued."""
import json
from unittest.mock import patch

from test_console_policies import ChatResearchTaskTests, NativeRuntimeTests
from test_platform_boundary import StoryPublicationTests
from ournotes_bot.sources.story_files import source_content_hash


def payloads(body):
    for message in body['messages']:
        if message['role'] in ('user', 'tool'):
            yield message['role'], json.loads(message['content'])


def registry(body):
    return {tool['function']['name']: tool['function']['parameters'] for tool in body.get('tools', [])}


class BehaviorProbe(ChatResearchTaskTests):
    def install_story(self):
        # Same source projection as the frozen ChatProbe. Only the model policy and observation change.
        story = StoryPublicationTests()
        story.setUp()
        self.addCleanup(story.doCleanups)
        bundle = story.bundle()
        scenes = bundle['scenes'][:2]
        for i, scene in enumerate(scenes):
            chapter = f'第{i + 1}章'
            scene.update(title=chapter, chapter=chapter, participants=self.case['people'],
                         previous_scene=None, next_scene=None)
            scene['utterances'][0].update(speaker=self.case['people'][i], text_ja=self.case['lines'][i],
                                          translation=self.case['translations'][i])
        bundle['scenes'] = scenes
        source = bundle['sources'][0]
        source['content_sha256'] = source_content_hash(scenes)
        story.publish(bundle)
        self.backend.model_runtime.agent.story = story.service
        return story.service, {s['chapter']: s for s in scenes}, source

    async def chapters(self):
        service, expected, source = self.install_story()
        trace = self.observed['requests'] = []
        tried = set()
        searches = set()

        async def respond(profile, body, **kwargs):
            tools = registry(body)
            visible = set()
            handles = []
            context = []
            for role, data in payloads(body):
                evidence = data.get('story_evidence', {}) if role == 'user' else data
                if evidence.get('status') != 'ok':
                    continue
                if not service.valid(evidence.get('source_revision'), evidence.get('revocation_revision')):
                    continue
                for row in evidence.get('scenes', []):
                    gold = expected.get(row['chapter'])
                    if (gold and row['source_content_sha256'] == source['content_sha256']
                            and row['source_kind'] == source['source_kind']
                            and all(row[k] == gold[k] for k in ('scene_id', 'source_id', 'utterances'))):
                        visible.add(row['chapter'])
                for row in [*evidence.get('unread_candidates', []), *evidence.get('items', [])]:
                    if row['title'] not in visible:
                        handles.append(row['handle'])
                context.extend(evidence.get('context_handles', []))
            step = dict(chapters=sorted(visible), tools=sorted(tools), action='final')
            trace.append(step)
            missing = [chapter for chapter in expected if chapter not in visible]
            if missing:
                if 'story_read' in tools:
                    for handle, window in [(h, 0) for h in handles] + [(h, 1) for h in context]:
                        if (handle, window) not in tried:
                            tried.add((handle, window))
                            step.update(action='story_read', window=window)
                            return self.result(calls=(self.call('story_read', handle=handle, before=window, after=window),))
                if 'story_search' in tools and missing[0] not in searches:
                    searches.add(missing[0])
                    step.update(action='story_search')
                    return self.result(calls=(self.call('story_search', query=missing[0], filters={'chapter': missing[0]}),))
            answer = '两章均有正文。' if not missing else '还缺' + '、'.join(missing) + '正文。'
            return self.result(answer)

        self.client.request.side_effect = respond
        with patch.object(service, 'search', wraps=service.search) as search, patch.object(service, 'read', wraps=service.read) as read:
            plan, permits = await self.prepare_native('/聊 ' + self.case['question'])
        part = plan['parts'][0]
        self.observed.update(model_calls=len(trace), permits=len(permits), search_calls=search.call_count,
                             read_calls=read.call_count, prepared=part['text'], status=part['business_status'])
        self.assertTrue(plan['send_allowed'])
        self.assertEqual(part['business_status'], 'success')
        self.assertEqual(trace[-1]['action'], 'final')
        self.assertEqual(trace[-1]['chapters'], sorted(expected), 'missing verified chapter in the final provider input')
        self.assertIn('两章均有正文', part['text'])

    async def coverage(self):
        case = self.case
        NativeRuntimeTests.reviewed_fixture(self, case['body'])
        items = [dict(label=label, entity_refs=[], fields=[], knowledge=True, difficulty='')
                 for label in case['requirements']]
        trace = self.observed['requests'] = []
        sent = set()

        async def respond(profile, body, **kwargs):
            tools = registry(body)
            passages = []
            statuses = []
            for role, data in payloads(body):
                if role != 'tool':
                    continue
                statuses.append(data['status'])
                content = data.get('data')
                if data['status'] == 'ok' and isinstance(content, str) and content.lstrip().startswith('{'):
                    passages.extend(json.loads(content).get('passages', []))
            supporting = next((p for p in passages if case['body'] in p['text']), None)
            step = dict(first_passage_visible=supporting is not None, tool_status=statuses, actions=[])
            trace.append(step)
            calls = []
            if 'research_task' in tools:
                schema = tools['research_task']['properties']['requirements']['items']['properties']
                bind = self.link and supporting and 'evidence' in schema and 'bind' not in sent
                if 'set' not in sent or bind:
                    args = [dict(item) for item in items]
                    if bind:
                        args[0]['evidence'] = [dict(entity_ref='', chunk_id=supporting['chunk_id'])]
                    action = 'bind-first' if bind else 'set'
                    sent.add('bind' if bind else 'set')
                    step['actions'].append(action)
                    calls.append(self.call('research_task', action='set', goal=case['question'],
                                           entity_refs=[], requirements=args))
            if 'knowledge_search' in tools:
                for i, item in enumerate(items):
                    if i not in sent:
                        sent.add(i)
                        calls.append(self.call('knowledge_search', query=item['label'], server='jp', date=None, version=None))
                        step['actions'].append(f'search-{i + 1}')
                        break
            if calls:
                return self.result(calls=tuple(calls))
            step['actions'].append('final')
            return self.result((case['body'] if supporting else '第一项缺少正文。') + '第二项缺少资料。')

        self.client.request.side_effect = respond
        plan, permits = await self.prepare_native('/聊 ' + case['question'])
        part = plan['parts'][0]
        coverage = self.backend.works[plan['delivery_id']].chat_task.coverage()
        self.observed.update(model_calls=len(trace), permits=len(permits), status=part['business_status'],
                             coverage=[row['complete'] for row in coverage['items']], prepared=part['text'])
        self.assertTrue(plan['send_allowed'])
        self.assertEqual(part['business_status'], 'success')
        self.assertTrue({0, 1} <= sent)
        self.assertEqual(trace[-1]['actions'], ['final'])
        self.assertTrue(trace[-1]['first_passage_visible'])
        self.assertIn(case['body'].rstrip('。'), part['text'])
        self.assertIn('第二项缺少资料', part['text'])
        if self.link:
            self.assertEqual(self.observed['coverage'], [True, False], 'supported first item and missing second item')
