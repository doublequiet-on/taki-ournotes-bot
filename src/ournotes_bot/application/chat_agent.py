# L3: Single dialogue loop over existing permits, usage ledger and read-only tools.
# Effects: ModelRuntime owns HTTP/accounting; tools read registered sources only.
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import replace
from functools import partial

from ..domain.chat import PERSONA, TurnBudget
from ..domain.model_calls import ModelError, require
from ..natural_query.native_tools import TOOLS, obj, string, validate_call, execute, scope_for
from ..sources.errors import ChartDataError
from ..data import DataError
from .runtime_tasks import finish_thread
from .site_lookup import SiteLookup
from .chat_context import ChatContext
from .chat_store import ChatStore
from .replies import PreparedReply

logger=logging.getLogger(__name__)


def chat_tools():
    tools={name:tool for name,tool in TOOLS.items()
           if tool.kind not in ('social','conversation','clarification','gap','visible')}
    for name in ('site_search','site_read'):
        tools[name]=replace(tools[name],description=(
            '查询已准入的 Haneoka 目录与公告，可改写、翻译检索词。保留用户明确的区服、日期、版本。'
            '候选不证明正文，使用本轮返回的 handle 连续读取；不支持任意 URL、剧情或联网搜索。'
            if name=='site_search' else '读取本轮返回的 handle，可继续阅读已知关联。'))
    tool=tools['knowledge_search']
    tools[tool.name]=replace(tool,schema=obj({**tool.schema['properties'],'query':string(120)},tool.schema['required']),
        description='检索已审核 ON 规则与玩法。query 可改写检索词；区服、日期、版本仍由宿主核对。支持日服、国际服和明确的共通范围。')
    return tools


class ChatAgent:
    def __init__(self,runtime,path):
        self.runtime=runtime
        self.context=ChatContext()
        self.store=ChatStore(path,runtime.backend.instance_id+'\0'+runtime.backend.app_id)
        self.budget=TurnBudget()
        self.tools=chat_tools()
        self.audit_failures=0

    def audit(self,operation,*args):
        """Visible audit is secondary to usage and durable delivery facts."""
        try:
            operation(*args)
        except sqlite3.Error:
            self.audit_failures+=1
            logger.error('Chat audit unavailable operation=%s failures=%s',operation.__name__,self.audit_failures)

    async def run(self,work,question,user,group,deadline):
        runtime=self.runtime
        deadline=min(deadline,work.content_deadline)
        work.model_attempt_limit=self.budget.model_calls
        self.context.trigger(work.request,question)
        history=self.context.messages(work.request)
        # A validated explicit reference to a business display is conversation
        # material, never a new system message or a substitute source lease.
        if work.anchor:
            snapshot=runtime.backend.conversations.snapshots.get(work.anchor.snapshot_id)
            require(snapshot is not None and 0<=work.anchor.page<len(snapshot.pages),'context_expired')
            history.insert(0,{'role':'user','content':json.dumps({
                'referenced_message_id':work.request.reference['message_id'] if work.request.reference else None,
                'delivered_display':snapshot.pages[work.anchor.page].text,
                'note':'已送达引用；历史陈述本身不证明外部事实'},ensure_ascii=False)})
        self.audit(self.store.begin,work,history)
        messages=[{'role':'system','content':PERSONA},*history]
        leases=()
        seen={}
        output_ids={}
        operations=0
        final_attempt=None
        force_final=False
        site=SiteLookup(runtime.backend.repository,question,allow_rewrite=True,max_operations=self.budget.tool_calls)
        try:
            for index in range(self.budget.model_calls):
                runtime.check(work,deadline,leases)
                final=force_final or index==self.budget.model_calls-1 or operations>=self.budget.tool_calls or deadline-time.monotonic()<=self.budget.final_seconds
                if final:
                    messages.append({'role':'system','content':'本轮检索预算结束。根据已有结果回答；缺失细节说明具体缺口。不要再调用工具。'})
                reservation=final_attempt if final else None
                try:
                    result=await runtime.attempt(work,messages,[] if final else [tool.wire() for tool in self.tools.values()],
                                                 user,group,deadline,leases,reservation=reservation)
                except ModelError as exc:
                    if str(exc)!='quota_exceeded' or final or final_attempt is None: raise
                    # No HTTP/permit has started on a failed reserve. Consume the
                    # already held final request instead of another retrieval.
                    final=True
                    messages.append({'role':'system','content':'剩余额度用于收束。根据已有资料回答，不再查询。'})
                    reservation=final_attempt
                    result=await runtime.attempt(work,messages,[],user,group,deadline,leases,reservation=reservation)
                if not result.tool_calls:
                    require(isinstance(result.text,str) and bool(result.text.strip()),'model_protocol_error')
                    text=result.text
                    if site.source_notes: text+=site.attribution()
                    runtime.check(work,deadline,leases)
                    self.audit(self.store.generated,work.delivery,text)
                    return PreparedReply(text,parse_origin='model',processing_route='model',business_status='success',
                                         evidence=leases,evidence_deadline=work.content_deadline if leases else None)
                require(not final,'model_attempt_limit')
                if final_attempt is None and not force_final:
                    candidate=uuid.uuid4().hex
                    try:
                        # Reserve a conservative complete final input and output
                        # before any tool work; other requests cannot spend it.
                        runtime.usage.reserve(work.request.request_id,candidate,user,group,
                                              work.model_snapshot.profile,work.model_snapshot.profile.max_input_tokens)
                        final_attempt=candidate
                    except ModelError as exc:
                        if str(exc)!='quota_exceeded': raise
                        force_final=True
                # One assistant response per round. Full encrypted/reasoning items
                # live only in this transcript and never enter ChatStore.
                calls=[]
                for call in result.tool_calls:
                    signature=(call.name,call.arguments)
                    require(call.id not in seen or seen[call.id][0]==signature,'tool_call_conflict')
                    if call.id not in seen: calls.append(call)
                items=result.output_items or (result.tool_calls[0].response_items if len(result.tool_calls)==1 else ())
                if items:
                    fresh=[]
                    for item in items:
                        item_id=item.get('id')
                        if item_id and item_id in output_ids:
                            require(output_ids[item_id]==item,'model_protocol_error')
                            continue
                        if item_id: output_ids[item_id]=item
                        if item.get('type')!='function_call' or item.get('call_id') not in seen:
                            fresh.append(item)
                    items=tuple(fresh)
                messages.append({'role':'assistant','content':None,'response_items':items,
                    'tool_calls':[{'id':call.id,'type':'function','function':{'name':call.name,'arguments':call.arguments}} for call in calls]})
                for call in calls:
                    runtime.check(work,deadline,leases)
                    if force_final or operations>=self.budget.tool_calls or deadline-time.monotonic()<=self.budget.final_seconds:
                        output={'status':'budget_exhausted','retryable':False}
                    else:
                        operations+=1
                        output,leases=await self.execute(work,call,question,site,leases,deadline-self.budget.final_seconds,deadline)
                    seen[call.id]=((call.name,call.arguments),output)
                    provenance=output.get('data') if isinstance(output.get('data'),dict) else {}
                    self.audit(self.store.event,work.delivery,'tool/'+call.id,'tool',{'name':call.name,'status':output['status'],
                        'source':{key:provenance[key] for key in ('source','server','release','text_release','observed_at','source_revision')
                                  if key in provenance}})
                    messages.append({'role':'tool','tool_call_id':call.id,'content':json.dumps(output,ensure_ascii=False)})
            raise ModelError('model_attempt_limit')
        finally:
            if final_attempt is not None:
                runtime.usage.release(final_attempt,'final_not_needed')
            messages.clear()

    async def execute(self,work,call,question,site,leases,deadline,turn_deadline):
        runtime=self.runtime
        tool,args=validate_call(call,self.tools)
        try:
            if tool.kind=='site':
                if call.name=='site_search': scope_for(question,dict(server=args['server'],date=None,version=None))
                evidence=await finish_thread(lambda:site.execute(call.name,args,deadline))
                if site.expires_at is not None:
                    work.content_deadline=min(work.content_deadline,time.monotonic()+site.expires_at-time.time())
                output={'status':'ok','data':json.loads(evidence)}
            elif tool.kind=='knowledge':
                knowledge=runtime.backend.knowledge
                if knowledge is None: return {'status':'not_available','retryable':False},leases
                scope=scope_for(question,args)
                await finish_thread(knowledge.observe)
                retrieval=await finish_thread(partial(knowledge.retrieve,args.get('query') or question,scope,knowledge.approved_set_revision))
                if retrieval.code!='ok':
                    return {'status':retrieval.code,'data':retrieval.message,'retryable':True},leases
                leases=(*leases,retrieval.lease)
                evidence=await finish_thread(partial(knowledge.prepare_evidence,retrieval.lease))
                output={'status':'ok','data':evidence}
            else:
                repository=await runtime.current_repository()
                output=await runtime.backend.gate.run(execute,tool,args,question,repository)
                if isinstance(output,PreparedReply):
                    output={'status':output.business_status or 'not_available','retryable':True}
                else: output={'status':output.status or 'ok','data':list(output.texts)}
            runtime.check(work,turn_deadline,leases)
            return output,leases
        except ModelError as exc:
            # Scope, permission, cancellation and source revocation terminate the
            # host turn. Source availability affects only this tool result.
            if str(exc)=='execution_timeout':
                runtime.check(work,turn_deadline,leases)
                return {'status':'tool_timeout','retryable':False},leases
            if str(exc) not in ('source_unavailable','model_input_too_large'):
                raise
            return {'status':str(exc),'retryable':True},leases
        except (DataError,ChartDataError,OSError):
            return {'status':'source_unavailable','retryable':True},leases

    def acknowledge(self,work,part_id,message_id,text,status,*,allow_context):
        self.audit(self.store.event,work.delivery,'delivery/'+part_id,'delivery',
                         {'message_id':message_id,'status':status,'text':text if status=='success' else '',
                          'allow_context':allow_context})
        if status=='success' and allow_context:
            self.context.delivered(work.request,message_id,text)

    def close(self):
        self.store.close()
