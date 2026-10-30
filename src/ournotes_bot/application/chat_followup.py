# L3: Volatile, bounded opportunities to answer a recently delivered question.
from collections import OrderedDict
from dataclasses import dataclass
import time
from uuid import uuid4


@dataclass(frozen=True)
class Question:
    user_id: str
    source_id: str
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
    def __init__(self, *, clock=time.monotonic, window=45, capacity=1024):
        self.clock, self.window, self.capacity = clock, window, capacity
        self.generations = OrderedDict()
        self.questions = OrderedDict()

    def sweep(self):
        now = self.clock()
        for scope, question in list(self.questions.items()):
            if now - question.at >= self.window or now < question.at:
                del self.questions[scope]

    def changed(self, scope):
        self.sweep()
        generation = uuid4().hex
        self.generations[scope] = generation
        self.generations.move_to_end(scope)
        self.questions.pop(scope, None)
        while len(self.generations) > self.capacity:
            old, _ = self.generations.popitem(last=False)
            self.questions.pop(old, None)
        return generation

    def delivered(self, scope, generation, user_id, source_id, source_time, source_seq, gateway_epoch, message_id, text, delivered_ms):
        self.sweep()
        if (self.generations.get(scope) != generation or not user_id or not message_id
                or not text.rstrip().endswith(('?', '？')) or len(text.encode()) > 4096):
            return
        self.questions[scope] = Question(user_id, source_id, source_time, source_seq, gateway_epoch, message_id, text,
                                         self.clock(), generation, delivered_ms)
        self.questions.move_to_end(scope)
        while len(self.questions) > self.capacity:
            self.questions.popitem(last=False)

    def observe(self, scope, user_id, message_id, timestamp_ms, ingress_seq, gateway_epoch):
        self.sweep()
        question = self.questions.pop(scope, None)
        generation = self.changed(scope)
        if (question and not question.answer_id and question.user_id == user_id and timestamp_ms > question.delivered_ms
                and ingress_seq == question.source_seq + 1
                and gateway_epoch == question.gateway_epoch
                and message_id not in (question.source_id, question.message_id)):
            self.questions[scope] = Question(question.user_id, question.source_id, question.source_time, question.source_seq, question.gateway_epoch,
                question.message_id, question.text, question.at, generation, question.delivered_ms, message_id)

    def match(self, scope, message_id):
        self.sweep()
        question = self.questions.get(scope)
        return question if question and question.answer_id == message_id else None

    def clear(self, scope):
        self.questions.pop(scope, None)
        self.generations.pop(scope, None)
