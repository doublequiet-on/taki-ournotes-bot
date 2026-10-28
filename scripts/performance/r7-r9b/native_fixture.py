"""Synthetic inputs only; keep real ModelRuntime, scheduling, permits and usage."""
import asyncio
import json
import time


def install(backend,state,measured):
    from ournotes_bot.application.model_runtime import ModelRuntime
    from ournotes_bot.application.model_config import ConfigSnapshot
    from ournotes_bot.application.knowledge_service import KnowledgeService
    from ournotes_bot.application import knowledge_service
    from ournotes_bot.sources.knowledge_files import parse_document
    from ournotes_bot.domain.model_calls import ModelProfile,Price,AttemptResult,ToolCall,Usage
    class Encoder:
        identity='fixed-local-benchmark/1';dimensions=2
        def encode(self,texts,*,query=False):return tuple((1.,0.) for _ in texts)
    service=KnowledgeService(state/'synthetic-knowledge',state/'knowledge.sqlite3',Encoder())
    approvals=[]
    for n in range(50):
        title='工程规则' if n==0 else f'静态主题{n}'
        meta=dict(schema='taki-knowledge/1',id=f'fixture-{n}',title=title,keywords=[title],aliases=[],
            game='bang-dream-our-notes',servers=['jp'],applicability=dict(mode='current',game_version=None,
            valid_from=None,valid_to=None,scope_note='isolated engineering fixture'),
            sources=[dict(source_id='fixture',kind='fixture',uri='fixture://isolated',locator='isolated',accessed_at='2026-10-27')],
            evidence_type='reviewed_analysis',review_status='approved',reviewed_by='FIXTURE',reviewed_at='2026-10-27',limitations=['not game facts'])
        body='\n\n'.join(f'## {title}片段{i}\n'+(title+'只用于隔离测量，不代表游戏事实。')*8 for i in range(6))
        text='---\n'+json.dumps(meta,ensure_ascii=False)+'\n---\n'+body+'\n'
        doc=parse_document(text);(service.root/f'{doc.id}.md').write_text(text)
        approvals.append(dict(id=doc.id,content_sha256=doc.content_sha256,approval_revision=1,
                              approved_by='FIXTURE',approved_at='2026-10-27',confirmation_ref='fixture://isolated'))
    (service.root/'approvals.json').write_text(json.dumps(dict(schema='taki-knowledge-approvals/1',revision=1,approved=approvals,revoked=[])))
    assert service.refresh()['code']=='ready'
    service.observe=measured('knowledge_observe',service.observe)
    service.retrieve=measured('knowledge_retrieve',service.retrieve)
    # Count calls separately from generator body timing; completion is included by observe/check.
    knowledge_service.split_document=measured('knowledge_split',knowledge_service.split_document)
    backend.knowledge=service
    class Client:
        async def price(self,profile):return profile.price
        async def request(self,profile,body,*,timeout):
            await asyncio.sleep(.020)
            usage=Usage(100,0,20,None,120,'known')
            if body.get('tools'):
                return AttemptResult('ok',usage,(ToolCall('fixture-call','knowledge_search',
                    json.dumps(dict(server='jp',version=None,date=None,followup=False,mode='explain'))),))
            return AttemptResult('ok',usage,text='工程规则仅用于隔离测量，不代表游戏事实。')
    runtime=ModelRuntime(backend,state/'usage.sqlite3',client=Client())
    profile=ModelProfile('deepseek','fixture-native','fixture-v1','deepseek-chat-v1','fixture/probe',
        Price('USD',1000000,100000,2000000,1000000,'fixture-price',1,True,'registered-default'),'fixture-only-key')
    snapshot=ConfigSnapshot(profile,(),group_admissions=('benchmark-group',),identity_namespace=('benchmark-instance','benchmark-app'))
    runtime.config.snapshot=lambda:snapshot
    runtime.config.refresh_price=lambda client:asyncio.sleep(0)  # Fixed registered fixture price.
    runtime.check=measured('model_check',runtime.check)
    backend.model_runtime=runtime
