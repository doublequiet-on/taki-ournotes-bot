# L3: Volatile, bounded same-user continuation windows after a confirmed reply.
from collections import OrderedDict
from dataclasses import dataclass, replace
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
    window: float = 180
    observed_ids: tuple = ()
    request_id: str = ''


class Followups:
    def __init__(self, *, clock=time.monotonic, window=180, capacity=1024, schedule=None, on_expire=None):
        self.clock, self.window, self.capacity = clock, window, capacity
        self.on_expire=on_expire
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
            if self.questions.get(scope) is question:self._expire(scope,question)
        self.timers[scope]=self.schedule(max(0,question.window-(self.clock()-question.at)),expire)

    def _expire(self,key,question):
        self._remove(key)
        if self.on_expire:self.on_expire(key[0],question)

    def sweep(self):
        now = self.clock()
        for scope, question in list(self.questions.items()):
            if now - question.at >= question.window or now < question.at:
                self._expire(scope,question)

    def changed(self, scope, user_id=None):
        self.sweep()
        key=(scope,user_id)
        generation=uuid4().hex
        self.generations[key]=generation
        self.generations.move_to_end(key)
        if user_id is None:
            for item in tuple(self.questions):
                if item[0]==scope:self._remove(item)
        else:self._remove(key)
        while len(self.generations)>self.capacity:
            old,_=self.generations.popitem(last=False)
            self._remove(old)
        return generation

    def delivered(self, scope, generation, user_id, source_id, source_text, source_reply_to, source_display_name, source_time, source_seq, gateway_epoch, message_id, text, delivered_ms, *, window=None, request_id=''):
        self.sweep();key=(scope,user_id)
        expected=self.generations.get(key,self.generations.get((scope,None)))
        if (expected!=generation or not user_id or not message_id or not text.strip()
                or len(text.encode())>4096 or len(source_text.encode())>4096):return
        self._set(key,Question(user_id,source_id,source_text,source_reply_to,source_display_name,source_time,source_seq,gateway_epoch,
            message_id,text,self.clock(),generation,delivered_ms,window=window or self.window,request_id=request_id))
        while len(self.questions)>self.capacity:self._remove(next(iter(self.questions)))

    def observe(self, scope, user_id, message_id, timestamp_ms, ingress_seq, gateway_epoch):
        self.sweep()
        for key,current in tuple(self.questions.items()):
            if key[0]==scope and message_id not in current.observed_ids:
                self._set(key,replace(current,observed_ids=(*current.observed_ids,message_id)[-12:]))
        key=(scope,user_id);question=self.questions.get(key)
        if (question and timestamp_ms>=question.source_time and ingress_seq>question.source_seq
                and gateway_epoch==question.gateway_epoch and message_id not in (question.source_id,question.message_id)):
            self._set(key,replace(question,answer_id=message_id))

    def match(self, scope, message_id):
        self.sweep()
        return next((question for (group,_),question in self.questions.items()
                     if group==scope and question.answer_id==message_id),None)

    def current(self,scope,user_id):
        self.sweep()
        return self.questions.get((scope,user_id))

    def resize(self,scope,window):
        for key,question in tuple(self.questions.items()):
            if key[0]==scope:self._set(key,replace(question,window=window))
        self.sweep()

    def clear(self, scope, user_id=None):
        for key in tuple(self.questions):
            if key[0]==scope and (user_id is None or key[1]==user_id):self._remove(key)
        for key in tuple(self.generations):
            if key[0]==scope and (user_id is None or key[1]==user_id):self.generations.pop(key,None)

    def close(self):
        for scope in tuple(self.questions):self._remove(scope)
        self.generations.clear()
