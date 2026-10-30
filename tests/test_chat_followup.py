import unittest
from ournotes_bot.application.chat_followup import Followups


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.tracker=Followups(clock=lambda:self.now,window=45,capacity=2)
        self.scope=('group','one')

    def delivered(self,text='你说哪个方面？'):
        generation=self.tracker.changed(self.scope)
        self.tracker.delivered(self.scope,generation,'u1','source',100,1,'epoch','sent',text,200)

    def test_only_same_person_and_newer_message_matches_once(self):
        self.delivered()
        self.tracker.observe(self.scope,'u1','answer',201,2,'epoch')
        self.assertEqual(self.tracker.match(self.scope,'answer').text,'你说哪个方面？')
        self.tracker.observe(self.scope,'u1','next',202,3,'epoch')
        self.assertIsNone(self.tracker.match(self.scope,'next'))

    def test_intervening_or_old_message_invalidates(self):
        for user,stamp in [('u2',201),('u1',199)]:
            with self.subTest(user=user,stamp=stamp):
                self.delivered();self.tracker.observe(self.scope,user,'other',stamp,2,'epoch')
                self.assertIsNone(self.tracker.match(self.scope,'other'))

    def test_expiry_and_late_ack_after_new_activity(self):
        self.delivered();self.now+=45
        self.tracker.observe(self.scope,'u1','answer',201,2,'epoch')
        self.assertIsNone(self.tracker.match(self.scope,'answer'))
        old=self.tracker.changed(self.scope);self.tracker.changed(self.scope)
        self.tracker.delivered(self.scope,old,'u1','source',100,1,'epoch','sent','问题？',200)
        self.assertIsNone(self.tracker.match(self.scope,'answer'))

    def test_non_question_and_bounded_scopes(self):
        self.delivered('说完了')
        self.tracker.observe(self.scope,'u1','answer',201,2,'epoch')
        self.assertIsNone(self.tracker.match(self.scope,'answer'))
        for index in range(3):self.tracker.changed(('group',str(index)))
        self.assertLessEqual(len(self.tracker.generations),2)

    def test_sequence_gap_or_gateway_restart_rejects_uncertain_order(self):
        for seq,epoch in ((3,'epoch'),(2,'new-epoch')):
            with self.subTest(seq=seq,epoch=epoch):
                self.delivered()
                self.tracker.observe(self.scope,'u1','answer',201,seq,epoch)
                self.assertIsNone(self.tracker.match(self.scope,'answer'))

    def test_cleared_and_recreated_scope_cannot_revive_old_ack(self):
        old=self.tracker.changed(self.scope)
        self.tracker.clear(self.scope);self.tracker.changed(self.scope)
        self.tracker.delivered(self.scope,old,'u1','source',100,1,'epoch','sent','问题？',200)
        self.assertFalse(self.tracker.questions)
