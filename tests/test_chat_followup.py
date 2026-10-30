import unittest
from ournotes_bot.application.chat_followup import Followups


class FollowupTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.scheduled=[]
        def schedule(delay,callback):
            class Timer:
                cancelled=False
                def cancel(self):self.cancelled=True
            timer=Timer();self.scheduled.append((self.now+delay,callback,timer));return timer
        self.tracker=Followups(clock=lambda:self.now,window=45,capacity=2,schedule=schedule)
        self.scope=('group','one')

    def delivered(self,text='你说哪个方面？'):
        generation=self.tracker.changed(self.scope)
        self.tracker.delivered(self.scope,generation,'u1','source','原问题',None,'',100,1,'epoch','sent',text,200)

    def test_only_same_person_and_newer_message_matches_once(self):
        self.delivered()
        self.tracker.observe(self.scope,'u1','answer',201,2,'epoch')
        self.assertEqual(self.tracker.match(self.scope,'answer').text,'你说哪个方面？')
        self.tracker.observe(self.scope,'u1','next',202,3,'epoch')
        self.assertIsNone(self.tracker.match(self.scope,'next'))

    def test_intervening_user_invalidates(self):
        self.delivered();self.tracker.observe(self.scope,'u2','other',201,2,'epoch')
        self.assertIsNone(self.tracker.match(self.scope,'other'))

    def test_platform_clock_skew_is_not_used_for_order(self):
        self.delivered();self.tracker.observe(self.scope,'u1','answer',101,2,'epoch')
        self.assertIsNotNone(self.tracker.match(self.scope,'answer'))

    def test_equal_platform_timestamp_is_ambiguous(self):
        self.delivered();self.tracker.observe(self.scope,'u1','answer',100,2,'epoch')
        self.assertIsNone(self.tracker.match(self.scope,'answer'))

    def test_expiry_and_late_ack_after_new_activity(self):
        self.delivered();self.now+=45
        self.tracker.observe(self.scope,'u1','answer',201,2,'epoch')
        self.assertIsNone(self.tracker.match(self.scope,'answer'))
        old=self.tracker.changed(self.scope);self.tracker.changed(self.scope)
        self.tracker.delivered(self.scope,old,'u1','source','原问题',None,'',100,1,'epoch','sent','问题？',200)
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
        self.tracker.delivered(self.scope,old,'u1','source','原问题',None,'',100,1,'epoch','sent','问题？',200)
        self.assertFalse(self.tracker.questions)

    def test_idle_expiration_runs_without_new_messages(self):
        self.delivered();self.now=45
        for at,callback,timer in self.scheduled:
            if at<=self.now and not timer.cancelled:callback()
        self.assertFalse(self.tracker.questions)
        self.assertFalse(self.tracker.timers)
