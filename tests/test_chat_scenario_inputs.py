"""Synthetic contrast inputs; labels are manual, never a measured model score."""
import json
from pathlib import Path
import unittest


def evaluation_inputs():
    document=json.loads((Path(__file__).parent/'fixtures/chat_turn_taking_cases.json').read_text(encoding='utf-8'))
    return [{key:case[key] for key in ('id','context','currentMessage')} for case in document['cases']]


class ScenarioInputTests(unittest.TestCase):
    def test_model_inputs_exclude_manual_gold(self):
        inputs=evaluation_inputs()
        self.assertEqual(len(inputs),16)
        self.assertEqual(len({case['id'] for case in inputs}),16)
        for case in inputs:
            self.assertEqual(set(case),{'id','context','currentMessage'})
            seen={row['id'] for row in case['context']}
            self.assertNotIn(case['currentMessage']['id'],seen)
            for row in [*case['context'],case['currentMessage']]:
                if row['replyTo']:self.assertIn(row['replyTo'],seen)

    def test_pairs_share_context_and_change_the_current_message(self):
        document=json.loads((Path(__file__).parent/'fixtures/chat_turn_taking_cases.json').read_text(encoding='utf-8'))
        for first,second in zip(document['cases'][::2],document['cases'][1::2]):
            self.assertEqual(first['pair'],second['pair'])
            self.assertEqual(first['context'],second['context'])
            self.assertNotEqual(first['currentMessage'],second['currentMessage'])
            self.assertNotEqual(first['expectedAction'],second['expectedAction'])
            self.assertTrue({first['expectedAction'],second['expectedAction']}<={'reply','skip','defer'})
