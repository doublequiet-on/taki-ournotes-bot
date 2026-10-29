# L3: Bounded, scope-local triggered dialogue; only ACK creates assistant history.
# Effects: Bounded volatile cache; optional ChatStore restores only scoped delivered history.
import json
import time
from collections import OrderedDict
from dataclasses import asdict

from ..domain.chat import ChatMessage


class ChatContext:
    def __init__(self, *, clock=time.time, max_bytes=8*1024*1024,store=None):
        self.clock,self.max_bytes=clock,max_bytes
        self.scopes=OrderedDict()
        self.store=store
        self.revisions=OrderedDict()

    @staticmethod
    def scope(request):
        return tuple(request.scope.values())

    def sweep(self):
        cutoff=int((self.clock()-900)*1000)
        for scope,rows in list(self.scopes.items()):
            rows[:]=[row for row in rows if row.timestamp_ms>cutoff]
            if not rows: del self.scopes[scope]
        while self.scopes and (len(self.scopes)>1024 or self.retained_bytes()>self.max_bytes):
            self.scopes.popitem(last=False)

    def _restore(self,request):
        if self.store is None: return
        self.store.sweep()
        scope=self.scope(request)
        revision=self.store.revision
        if self.revisions.get(scope)==revision and scope in self.scopes: return
        rows=self.store.recent(request.scope,since_ms=int((self.clock()-900)*1000))
        self.scopes[scope]=[ChatMessage(**row) for row in rows]
        self.scopes.move_to_end(scope)
        self.revisions[scope]=revision
        self.revisions.move_to_end(scope)
        while len(self.revisions)>1024: self.revisions.popitem(last=False)
        # Empty scopes still need a revision marker until the next trigger add.
        if rows: self.sweep()

    def retained_bytes(self):
        return sum(len(json.dumps(asdict(row),ensure_ascii=False).encode()) for rows in self.scopes.values() for row in rows)

    def add(self,scope,message):
        self.sweep()
        rows=self.scopes.setdefault(scope,[])
        if any(row.message_id==message.message_id for row in rows): return
        rows.append(message)
        # Drop complete old exchanges, never the current function-call transcript.
        while len(rows)>48 or sum(len(row.text.encode()) for row in rows)>24576:
            rows.pop(0)
            while rows and rows[0].role=='assistant': rows.pop(0)
        self.scopes.move_to_end(scope)
        self.sweep()

    def trigger(self,request,question):
        self._restore(request)
        self.add(self.scope(request),ChatMessage(request.message_id,request.user_id,question,request.timestamp_ms,
                 reply_to=request.reference['message_id'] if request.reference else None,display_name=request.sender_name))

    def delivered(self,request,message_id,text):
        self.add(self.scope(request),ChatMessage(message_id,'Taki',text,int(self.clock()*1000),'assistant',request.message_id))

    def messages(self,request):
        self._restore(request)
        self.sweep()
        rows=list(self.scopes.get(self.scope(request),()))
        # Explicit references may use still-retained material older than the
        # automatic 15-minute window, without permanently expanding that window.
        if self.store is not None and request.reference:
            mid=request.reference['message_id']
            if not any(row.message_id==mid for row in rows):
                rows[:0]=[ChatMessage(**row) for row in self.store.read(request.scope,[mid])['messages']]
        return [{'role':row.role,'content':json.dumps(asdict(row),ensure_ascii=False)} for row in rows]

    def contains(self,request,message_id):
        self._restore(request)
        self.sweep()
        return (any(row.message_id==message_id for row in self.scopes.get(self.scope(request),()))
                or self.store is not None and bool(self.store.read(request.scope,[message_id])['messages']))
