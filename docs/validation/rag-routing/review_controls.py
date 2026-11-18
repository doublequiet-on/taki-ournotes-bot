"""Public counterexamples at the provider and prepared-reply boundaries."""
from copy import deepcopy
import json
from unittest import SkipTest
from unittest.mock import patch


def measure_controls(cases):
    from behavior import BehaviorProbe as OldProbe
    from behavior_v3 import BehaviorProbe as ReviewedProbe
    from run_behavior import trial as old_trial
    from run_behavior_v3 import trial as reviewed_trial

    def run(probe, trial, change, case, method):
        if change == 'skip':
            with patch.object(probe, method, side_effect=SkipTest('public negative control')):
                return trial(case, method, link=True)['status']
        prepare = probe.prepare_native

        async def prepare_changed(self, *args, **kwargs):
            respond = self.client.request.side_effect
            async def changed_input(profile, body, **kw):
                wire = deepcopy(body)
                for message in wire['messages']:
                    if message['role'] != 'tool':
                        continue
                    data = json.loads(message['content'])
                    if not data.get('scenes'):
                        continue
                    if change == 'origin':
                        for row in data['scenes']:
                            row.update(source_uri='local:wrong-source', source_version='wrong-version')
                    elif change == 'revocation':
                        data['revocation_revision'] = -1
                    elif change == 'search-only':
                        for answer in wire['messages']:
                            for call in answer.get('tool_calls', []):
                                if call['id'] == message['tool_call_id']:
                                    call['function']['name'] = 'story_search'
                    message['content'] = json.dumps(data, ensure_ascii=False)
                return await respond(profile, wire, **kw)
            self.client.request.side_effect = changed_input
            plan, permits = await prepare(self, *args, **kwargs)
            if change == 'contradiction':
                plan['parts'][0]['text'] += '。归还地点是服务台。'
            return plan, permits
        with patch.object(probe, 'prepare_native', prepare_changed):
            return trial(case, method, link=True)['status']

    results = {}
    for change, case_id, method in (('origin', 'D15', 'chapters'), ('revocation', 'D15', 'chapters'),
                                    ('search-only', 'D15', 'chapters'), ('contradiction', 'D20', 'coverage'),
                                    ('skip', 'D20', 'coverage')):
        results[change] = {name: run(probe, trial, change, cases[case_id], method) for name, probe, trial in (
            ('v2', OldProbe, old_trial), ('v3', ReviewedProbe, reviewed_trial))}
    return results
