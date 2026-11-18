"""Observe actual evidence at the simulated provider boundary."""
import json
from unittest.mock import patch

from behavior import BehaviorProbe as RoundTwoProbe, registry
from test_console_policies import NativeRuntimeTests


def payloads(body):
    calls = {}
    for message in body['messages']:
        if message['role'] == 'assistant':
            calls.update((call['id'], call['function']) for call in message.get('tool_calls', []))
        elif message['role'] == 'tool':
            call = calls.get(message['tool_call_id'])
            if call:
                yield call['name'], json.loads(call['arguments']), json.loads(message['content'])
        elif message['role'] == 'user':
            data = json.loads(message['content'])
            if 'story_evidence' in data:
                yield 'prefetch', {}, data['story_evidence']


class BehaviorProbe(RoundTwoProbe):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        budget = self.backend.model_runtime.agent.budget
        self.assertEqual((budget.model_calls, budget.tool_calls, budget.execution_seconds, budget.final_seconds),
                         (6, 8, 60, 5), 'active host budget differs from the frozen panel')

    async def chapters(self):
        service, expected, source = self.install_story()
        published = service.status()
        revisions = {k: published[k] for k in ('source_revision', 'revocation_revision')}
        self.observed['source'] = dict(source_id=source['source_id'], uri=source['uri'], version=source['version'],
                                       sha256=source['content_sha256'], **revisions)
        read_chapters = set()
        def matches(row):
            gold = expected.get(row.get('chapter'))
            return (gold and all(row.get(k) == gold[k] for k in
                                 ('scene_id', 'source_id', 'locator', 'participants', 'utterances'))
                    and all(row.get(k) == source[v] for k, v in (
                        ('source_content_sha256','content_sha256'), ('source_kind','source_kind'),
                        ('source_uri','uri'), ('source_version','version'), ('work','work'),
                        ('language','language'), ('use_scope','use_scope'))))

        original_read = service.read
        def record_read(*args, **kwargs):
            evidence = original_read(*args, **kwargs)
            if evidence['status'] == 'ok' and all(evidence.get(k) == v for k,v in revisions.items()):
                read_chapters.update(row['chapter'] for row in evidence['scenes'] if matches(row))
            return evidence

        trace = self.observed['requests'] = []
        tried = set()
        searches = set()

        async def respond(profile, body, **kwargs):
            tools = registry(body)
            visible = set()
            handles = []
            context = []
            for origin, _, evidence in payloads(body):
                if origin not in ('prefetch', 'story_read', 'story_search'):
                    continue
                if evidence.get('status') != 'ok':
                    continue
                if not all(evidence.get(k) == v for k,v in revisions.items()) or not service.valid(**revisions):
                    continue
                if origin != 'story_search':
                    visible.update(row['chapter'] for row in evidence.get('scenes', [])
                                   if matches(row) and row['chapter'] in read_chapters)
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
        with patch.object(service, 'search', wraps=service.search) as search, patch.object(service, 'read', side_effect=record_read) as read:
            plan, permits = await self.prepare_native('/聊 ' + self.case['question'])
        part = plan['parts'][0]
        self.observed.update(model_calls=len(trace), permits=len(permits), search_calls=search.call_count,
                             read_calls=read.call_count, prepared=part['text'], status=part['business_status'])
        self.assertTrue(plan['send_allowed'])
        self.assertEqual(part['business_status'], 'success')
        self.assertLessEqual(read.call_count + search.call_count, self.backend.model_runtime.agent.budget.tool_calls)
        self.assertEqual(len(trace), len(permits))
        self.assertLessEqual(len(trace), self.backend.model_runtime.agent.budget.model_calls)
        self.assertTrue(service.valid(**revisions))
        self.assertEqual(trace[-1]['action'], 'final')
        self.assertEqual(trace[-1]['chapters'], sorted(expected), 'missing verified chapter in the final provider input')
        self.assertEqual([p['text'].rstrip('。') for p in plan['parts']], ['两章均有正文'])

    async def coverage(self):
        case = self.case
        NativeRuntimeTests.reviewed_fixture(self, case['body'])
        items = [dict(label=label, entity_refs=[], fields=[], knowledge=True, difficulty='')
                 for label in case['requirements']]
        trace = self.observed['requests'] = []
        sent = set()
        leases = []
        service = self.backend.knowledge
        retrieve = service.retrieve
        def record_retrieval(*args, **kwargs):
            result = retrieve(*args, **kwargs)
            if result.code == 'ok':
                leases.append(result.lease)
            return result

        async def respond(profile, body, **kwargs):
            tools = registry(body)
            passages = []
            statuses = []
            searches = {}
            approved = {(c.doc_id, c.content_sha256, c.approval_revision, c.chunk_id, c.chunk_sha256, c.text)
                        for lease in leases if service.valid(lease) for c in lease.chunks}
            for origin, args, data in payloads(body):
                if origin == 'prefetch':
                    continue
                statuses.append(data['status'])
                if origin != 'knowledge_search':
                    continue
                searches[args['query']] = data['status']
                content = data.get('data')
                if data['status'] == 'ok' and isinstance(content, str) and content.lstrip().startswith('{'):
                    result = json.loads(content)
                    docs = result.get('documents', [])
                    for passage in result.get('passages', []):
                        index = passage.get('document')
                        if type(index) is int and 0 <= index < len(docs):
                            doc = docs[index]
                            identity = (doc.get('doc_id'), doc.get('content_sha256'), doc.get('approval_revision'),
                                        passage.get('chunk_id'), passage.get('chunk_sha256'), passage.get('text'))
                            if identity in approved:
                                passages.append(passage)
            supporting = next((p for p in passages if case['body'] in p['text']), None)
            step = dict(first_passage_visible=supporting is not None, tool_status=statuses, searches=searches, actions=[])
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
        with patch.object(service, 'retrieve', side_effect=record_retrieval):
            plan, permits = await self.prepare_native('/聊 ' + case['question'])
        part = plan['parts'][0]
        coverage = self.backend.works[plan['delivery_id']].chat_task.coverage()
        by_label = {row['label']: row['complete'] for row in coverage['items']}
        self.observed.update(model_calls=len(trace), permits=len(permits), status=part['business_status'],
                             coverage=by_label, prepared=part['text'])
        self.assertTrue(plan['send_allowed'])
        self.assertEqual(part['business_status'], 'success')
        self.assertEqual(len(trace), len(permits))
        self.assertLessEqual(len(trace), self.backend.model_runtime.agent.budget.model_calls)
        self.assertLessEqual(sum(len(step['actions']) for step in trace if step['actions'] != ['final']),
                             self.backend.model_runtime.agent.budget.tool_calls)
        self.assertEqual(trace[-1]['actions'], ['final'])
        self.assertEqual(set(trace[-1]['searches']), set(case['requirements']))
        self.assertEqual(trace[-1]['searches'][case['requirements'][0]], 'ok')
        self.assertIn(trace[-1]['searches'][case['requirements'][1]], ('ok', 'already_visible', 'missing'))
        self.assertTrue(trace[-1]['first_passage_visible'])
        self.assertEqual([p['text'].rstrip('。') for p in plan['parts']], [case['body'] + '第二项缺少资料'])
        self.assertEqual(len(coverage['items']), 2)
        first, second = case['requirements']
        self.assertEqual(by_label, {first: self.link, second: False})
        if self.link:
            self.assertIn('bind', sent)
