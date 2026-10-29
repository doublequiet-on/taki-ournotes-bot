# L3: Bounded metadata, full-snapshot preview and explicit publication for existing knowledge owners.
# Effects: Ephemeral previews; delegates guarded writes to existing services, no models/network/chat memory.
from collections import Counter
from contextlib import contextmanager
from dataclasses import asdict
import difflib
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
import threading
import uuid

from ..domain.knowledge import KnowledgeError
from ..sources.knowledge_files import scan_directory, safe_path, validate_delivery, canonical, eligible
from ..sources.json_validation import decode_json
from ..sources.story_files import parse_bundle, read_json


def check(value,code='library_shape'):
    if not value:raise KnowledgeError(code)


def changes(before,after):
    keys=sorted(before.keys()|after.keys())
    added=[k for k in keys if k not in before]
    removed=[k for k in keys if k not in after]
    modified=[k for k in keys if k in before and k in after and before[k]!=after[k]]
    selected=added+removed+modified
    bounded=len(selected)<=100 and sum(len(before.get(k,''))+len(after.get(k,'')) for k in selected)<=20000
    if not bounded:
        return dict(added=added[:100],removed=removed[:100],modified=modified[:100],total_changes=len(selected),diff='',truncated=True)
    diff=''.join(line for k in selected[:100] for line in difflib.unified_diff(
        before.get(k,'')[:20000].splitlines(True),after.get(k,'')[:20000].splitlines(True),fromfile='当前/'+k,tofile='候选/'+k))
    bounded=bounded and len(diff.encode())<=30000
    return dict(added=added[:100],removed=removed[:100],modified=modified[:100],
                total_changes=len(selected),diff=diff.encode()[:30000].decode('utf-8','ignore'),truncated=not bounded)


class LibraryAdmin:
    def __init__(self,backend):
        self.backend=backend
        self.plans={}
        self.plan_lock=threading.Lock()

    def owner(self,kind):
        check(kind in ('knowledge','story'))
        if kind=='knowledge':owner=self.backend.knowledge
        else:
            runtime=self.backend.model_runtime
            owner=runtime.agent.story if runtime and runtime.agent else None
        check(owner is not None,'library_not_configured')
        return owner

    def guard(self,kind):
        owner=self.owner(kind)
        if kind=='story':
            bundle=owner.current()
            version=[bundle.revision if bundle else None,owner.revocations()['revision']]
        else:
            with owner.lock:
                owner.observe()
                version=[scan_directory(owner.root).fingerprint,owner.approved_set_revision]
        return {'backend_epoch':self.backend.runtime,'kind':kind,'version':version}

    @contextmanager
    def stage(self,value):
        check(isinstance(value,dict) and set(value)=={'schema','files','approvals','delivery'} and value['schema']=='taki-console-knowledge/1')
        check(isinstance(value['files'],dict) and len(value['files'])<=40)
        owner=self.owner('knowledge')
        with tempfile.TemporaryDirectory(prefix='.library-preview-',dir=owner.state_path.parent) as folder:
            root=Path(folder)
            for name,body in value['files'].items():
                check(isinstance(name,str) and name.endswith('.md') and isinstance(body,str))
                target=safe_path(root,name);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(body,encoding='utf-8')
            (root/'approvals.json').write_text(canonical(value['approvals']),encoding='utf-8')
            scan=scan_directory(root);validate_delivery(scan,value['delivery'])
            yield root,scan

    def metadata(self,kind,offset=0):
        check(type(offset) is int and 0<=offset<=10000,'library_page')
        owner=self.owner(kind);guard=self.guard(kind)
        if kind=='knowledge':
            with owner.lock:
                scan=scan_directory(owner.root)
                approvals={a.id:a for a in scan.manifest.approved}
                rows=[dict(id=d.id,title=d.title,hash=d.content_sha256,review=d.review_status,
                    published=owner.available and d.id in owner.allowed,confirmation_ref=approvals[d.id].confirmation_ref if d.id in approvals else None,blocked=owner.authority['blocked'].get(d.id),servers=list(d.servers),
                    reviewed_by=d.reviewed_by,reviewed_at=d.reviewed_at,scope=asdict(d.applicability),
                    source_count=len(d.sources),sources=[asdict(s) for s in d.sources[:3]]) for d in scan.documents]
                summary=owner.status()
                # Issues may contain operator-owned relative filenames, never local absolute paths.
                result=dict(kind=kind,guard=guard,revision=guard['version'][1],summary=summary,
                    items=rows[offset:offset+20],total=len(rows),offset=offset,next_offset=offset+20 if offset+20<len(rows) else None)
        else:
            bundle=owner.current();revoked=owner.revocations()
            rows=[] if bundle is None else [dict(id=s.source_id,title=s.work,kind=s.source_kind,version=s.version,
                uri=s.uri,coverage=s.coverage,published_at=s.published_at,acquired_at=s.acquired_at,
                withdrawn=s.source_id in revoked['sources'],withdrawal=revoked['sources'].get(s.source_id),scene_count=sum(c.source_id==s.source_id for c in bundle.scenes)) for s in bundle.sources]
            persona=owner.persona()
            if persona:persona={k:v for k,v in persona.items() if k!='prompt'}
            releases=owner.root/'releases'
            versions=sorted(p.stem for p in releases.glob('*.json') if not p.is_symlink() and re.fullmatch('[a-f0-9]{64}',p.stem))
            result=dict(kind=kind,guard=guard,revision=guard['version'][0],items=rows[offset:offset+20],total=len(rows),offset=offset,
                next_offset=offset+20 if offset+20<len(rows) else None,versions=versions[:50],version_count=len(versions),persona=persona,claim_counts=dict(Counter(c.review_status for c in bundle.claims)) if bundle else {},
                claims=[] if bundle is None else [dict(id=c.claim_id,review=c.review_status) for c in bundle.claims[:20]],
                personas=[] if bundle is None else [dict(id=p.persona_id,review=p.review_status) for p in bundle.personas[:20]])
        result['observed_at']=self.backend.clock()
        check(len(json.dumps(result,ensure_ascii=False).encode())<=100000,'library_metadata_too_large')
        return result

    def preview(self,value):
        check(isinstance(value,dict) and set(value)=={'kind','content'})
        kind=value['kind'];owner=self.owner(kind)
        check(isinstance(value['content'],str) and len(value['content'].encode())<=18000,'library_import_size')
        raw=value['content']
        try:data=decode_json(raw)
        except (ValueError,RecursionError):raise KnowledgeError('library_json') from None
        guard=self.guard(kind)
        if kind=='story':
            bundle=parse_bundle(data)
            check(all(p.review_status!='approved' for p in bundle.personas),'story_import_requires_draft')
            current=owner.current()
            current_value={} if current is None else asdict(current)
            if current is not None:current_value['aliases']=dict(current.aliases)
            before={k:json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2) for k,v in current_value.items() if k!='revision'}
            after={k:json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2) for k,v in data.items() if k!='schema'}
            revision=bundle.revision
        else:
            with self.stage(data) as (_,scan):
                with owner.lock:
                    owner.transition(scan)
                    check(all(a.approval_revision>owner.authority['blocked'].get(d.id,0) for d,a in eligible(scan).values()),'approval_requires_new_confirmation')
                current=scan_directory(owner.root)
                before=dict(current.files);before['approvals.json']=current.manifest_text
                after=dict(scan.files);after['approvals.json']=scan.manifest_text
                revision=str(scan.manifest.revision)
        delta=changes(before,after)
        check(len(json.dumps(delta,ensure_ascii=False).encode())<=100000,'library_preview_too_large')
        check(self.guard(kind)==guard,'library_preview_conflict')
        with self.plan_lock:
            self.plans={k:p for k,p in self.plans.items() if time.monotonic()<p['expires']}
            token=None
            if not delta['truncated']:
                check(len(self.plans)<8,'library_preview_capacity')
                token=uuid.uuid4().hex
                self.plans[token]=dict(kind=kind,content=raw,guard=guard,expires=time.monotonic()+600)
        return dict(kind=kind,guard=guard,candidate_revision=revision,changes=delta,preview_id=token,expires_in=600,
                    content_sha256=hashlib.sha256(raw.encode()).hexdigest(),fact_review=False)

    def compare(self,value):
        check(isinstance(value,dict) and set(value)=={'revision'} and isinstance(value['revision'],str)
              and re.fullmatch('[a-f0-9]{64}',value['revision']))
        owner=self.owner('story')
        raw=canonical(read_json(owner.release_path(value['revision'])))
        return self.preview({'kind':'story','content':raw})

    def publish(self,value):
        check(isinstance(value,dict) and set(value)=={'preview_id','confirm_reviewed'} and value['confirm_reviewed'] is True,'library_confirmation_required')
        check(isinstance(value['preview_id'],str))
        with self.plan_lock:plan=self.plans.pop(value['preview_id'],None)
        check(plan is not None and time.monotonic()<plan['expires'],'library_preview_expired')
        kind=plan['kind'];owner=self.owner(kind)
        check(self.guard(kind)==plan['guard'],'library_publication_conflict')
        data=decode_json(plan['content'])
        if kind=='story':owner.import_value(data,expected=plan['guard']['version'])
        else:
            with self.stage(data) as (root,_):owner.import_confirmed(root,data['delivery'],expected=plan['guard']['version'])
        return {'status':'published','kind':kind,'guard':self.guard(kind)}

    def withdraw(self,value):
        check(isinstance(value,dict) and set(value)=={'kind','id','reason','guard','confirm_withdraw'} and value['confirm_withdraw'] is True,'library_confirmation_required')
        kind=value['kind'];owner=self.owner(kind)
        check(isinstance(value['reason'],str) and 1<=len(value['reason'].strip())<=1000)
        check(value['guard']==self.guard(kind),'library_publication_conflict')
        if kind=='story':owner.withdraw(value['id'],value['reason'],expected=value['guard']['version'])
        else:owner.withdraw_document(value['id'],value['reason'],expected=value['guard']['version'])
        return {'status':'withdrawn','kind':kind,'guard':self.guard(kind)}
