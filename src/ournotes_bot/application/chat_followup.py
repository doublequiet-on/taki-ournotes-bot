# L3: Volatile, bounded opportunities to answer a recently delivered question.
from collections import OrderedDict
from dataclasses import dataclass
import asyncio
import time
from uuid import uuid4


@dataclass(frozen=True)
class Question:
    user_id: str
    source_id: str
    source_text: str
    source_reply_to: str | None
    source_display_name: str
    source_time: int
    source_seq: int
    gateway_epoch: str
    message_id: str
    text: str
    at: float
    generation: str
    delivered_ms: int
    answer_id: str = ''


class Followups:
    def __init__(self, *, clock=time.monotonic, window=120, capacity=1024, schedule=None):
        self.clock, self.window, self.capacity = clock, window, capacity
        self.schedule=schedule or (lambda delay,callback:asyncio.get_running_loop().call_later(delay,callback))
        self.generations = OrderedDict()
        self.questions = OrderedDict()
        self.timers = {}

    def _remove(self,scope):
        self.questions.pop(scope,None)
        timer=self.timers.pop(scope,None)
        if timer:timer.cancel()

    def _set(self,scope,question):
        self._remove(scope)
        self.questions[scope]=question
        self.questions.move_to_end(scope)
        def expire():
            if self.questions.get(scope) is question:self._remove(scope)
        self.timers[scope]=self.schedule(max(0,self.window-(self.clock()-question.at)),expire)

    def sweep(self):
        now = self.clock()
        for scope, question in list(self.questions.items()):
            if now - question.at >= self.window or now < question.at:
                self._remove(scope)

    def changed(self, scope):
        self.sweep()
        generation = uuid4().hex
        self.generations[scope] = generation
        self.generations.move_to_end(scope)
        self._remove(scope)
        while len(self.generations) > self.capacity:
            old, _ = self.generations.popitem(last=False)
            self._remove(old)
        return generation

    def delivered(self, scope, generation, user_id, source_id, source_text, source_reply_to, source_display_name, source_time, source_seq, gateway_epoch, message_id, text, delivered_ms):
        self.sweep()
        if (self.generations.get(scope) != generation or not user_id or not message_id
                or not text.rstrip().endswith(('?', '？')) or len(text.encode()) > 4096 or len(source_text.encode()) > 4096):
            return
        self._set(scope,Question(user_id, source_id, source_text, source_reply_to, source_display_name, source_time, source_seq, gateway_epoch, message_id, text,
                                         self.clock(), generation, delivered_ms))
        while len(self.questions) > self.capacity:
            self._remove(next(iter(self.questions)))

    def observe(self, scope, user_id, message_id, timestamp_ms, ingress_seq, gateway_epoch):
        self.sweep()
        question = self.questions.get(scope)
        generation = self.changed(scope)
        if (question and not question.answer_id and question.user_id == user_id
                and timestamp_ms > question.source_time
                and ingress_seq == question.source_seq + 1
                and gateway_epoch == question.gateway_epoch
                and message_id not in (question.source_id, question.message_id)):
            self._set(scope,Question(question.user_id, question.source_id, question.source_text, question.source_reply_to, question.source_display_name, question.source_time, question.source_seq, question.gateway_epoch,
                question.message_id, question.text, question.at, generation, question.delivered_ms, message_id))

    def match(self, scope, message_id):
        self.sweep()
        question = self.questions.get(scope)
        return question if question and question.answer_id == message_id else None

    def clear(self, scope):
        self._remove(scope)
        self.generations.pop(scope, None)

    def close(self):
        for scope in tuple(self.questions):self._remove(scope)
        self.generations.clear()
