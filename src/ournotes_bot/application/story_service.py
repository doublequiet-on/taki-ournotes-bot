# L3: Versioned local story publication, revocation, read handles and explicit persona approval.
# Effects: Local operator-owned directory only; never fetches URLs, calls models or sends.
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime,timezone
from pathlib import Path
import re

from ..domain.story import StoryError,require
from ..sources.story_files import (atomic_json,canonical,digest,identifier,optional_time,parse_bundle,publish_lock,read_json,text)
from ..query.story_retrieval import scene_data,search_scenes


def stamp():
    return datetime.now(timezone.utc).isoformat()


class StoryService:
    def __init__(self,root):
        self.root=Path(root)
        require(not self.root.is_symlink(),'story_file')

    def current(self):
        path=self.root/'current.json'
        if not path.exists():return None
        pointer=read_json(path)
        require(isinstance(pointer,dict) and set(pointer)=={'schema','revision'}
                and pointer['schema']=='taki-story-pointer/1','story_pointer')
        revision=pointer['revision']
        require(isinstance(revision,str) and re.fullmatch('[a-f0-9]{64}',revision),'story_revision')
        bundle=parse_bundle(read_json(self.release_path(revision)),allow_fixture=True)
        require(bundle.revision==revision,'story_revision_mismatch')
        return bundle

    def release_path(self,revision):
        require(not (self.root/'releases').is_symlink(),'story_file')
        return self.root/'releases'/(revision+'.json')

    def revocations(self):
        path=self.root/'revocations.json'
        if not path.exists():return {'schema':'taki-story-revocations/1','revision':0,'sources':{},'personas':{}}
        row=read_json(path)
        require(isinstance(row,dict) and set(row)=={'schema','revision','sources','personas'}
                and row['schema']=='taki-story-revocations/1' and type(row['revision']) is int
                and row['revision']>=0 and isinstance(row['sources'],dict) and isinstance(row['personas'],dict),'story_revocations')
        for records in (row['sources'],row['personas']):
            for source_id,record in records.items():
                identifier(source_id)
                require(isinstance(record,dict) and set(record)=={'reason','withdrawn_at'},'story_revocations')
                text(record['reason']);optional_time(record['withdrawn_at'])
                require(record['withdrawn_at'] is not None,'story_revocations')
        return row

    def import_bundle(self,path,*,allow_fixture=False):
        return self.import_value(read_json(path),allow_fixture=allow_fixture)

    def import_value(self,value,*,allow_fixture=False,expected=None):
        bundle=parse_bundle(value,allow_fixture=allow_fixture)
        require(all(persona.review_status!='approved' for persona in bundle.personas),'story_import_requires_draft')
        with publish_lock(self.root):
            self.check_expected(expected)
            self.revocations()  # A corrupt withdrawal ledger must never revive content.
            target=self.release_path(bundle.revision)
            if target.exists():
                require(digest(read_json(target))==bundle.revision,'story_revision_mismatch')
            else:atomic_json(target,value)
            atomic_json(self.root/'current.json',{'schema':'taki-story-pointer/1','revision':bundle.revision})
        return self.status()

    def status(self):
        bundle=self.current();withdrawals=self.revocations()
        if bundle is None:return {'status':'not_available','source_revision':None,'sources':[],'scenes':0,'persona':None}
        excluded=set(withdrawals['sources'])
        persona=self.persona()
        if persona is not None and persona['source_revision']!=bundle.revision:persona=None
        return {'status':'ok','source_revision':bundle.revision,'revocation_revision':withdrawals['revision'],
                'sources':[{**asdict(source),'withdrawn':source.source_id in excluded,
                            'scene_count':sum(scene.source_id==source.source_id for scene in bundle.scenes)} for source in bundle.sources],
                'scenes':sum(scene.source_id not in excluded for scene in bundle.scenes),'persona':persona}

    def valid(self,source_revision,revocation_revision):
        """Generation/delivery owners recheck this captured read scope."""
        bundle=self.current();withdrawals=self.revocations()
        return bundle is not None and bundle.revision==source_revision and withdrawals['revision']==revocation_revision

    def search(self,query,filters=None,*,limit=6):
        bundle=self.current()
        if bundle is None:return {'status':'not_available','source_revision':None,'items':[],'query_rewrites':[]}
        withdrawals=self.revocations()
        result=search_scenes(bundle,query,filters,limit=limit,excluded_sources=withdrawals['sources'])
        sources={source.source_id:source for source in bundle.sources}
        token=digest(withdrawals)[:16]
        items=[];retained=0
        for scene in result.pop('scenes'):
            data=scene_data(scene,sources[scene.source_id])
            # The identifier is verified against this exact published snapshot,
            # never interpreted as a filename or URL supplied by a model.
            handle=bundle.revision+':'+token+':'+scene.scene_id
            item={'handle':handle,'scene_id':scene.scene_id,'source_id':scene.source_id,
                          'title':scene.title,'participants':list(scene.participants[:8]),'participants_truncated':len(scene.participants)>8,
                          'excerpt':scene.utterances[0].text_ja[:240],
                          'source_kind':data['source_kind'],'source_uri':data['source_uri'],'locator':scene.locator,
                          'source_content_sha256':data['source_content_sha256']}
            size=len(canonical(item).encode())
            if retained+size>12000:
                result['truncated']=True;break
            retained+=size;items.append(item)
        return {'status':'ok' if items else 'no_results','source_revision':bundle.revision,
                'revocation_revision':withdrawals['revision'],'items':items,**result}

    def read(self,handle,*,before=1,after=1):
        require(isinstance(handle,str) and len(handle)<=200,'story_handle')
        require(type(before) is int and type(after) is int and 0<=before<=2 and 0<=after<=2,'story_range')
        parts=handle.split(':',2)
        require(len(parts)==3,'story_handle')
        bundle=self.current();withdrawals=self.revocations()
        if bundle is None or parts[0]!=bundle.revision or parts[1]!=digest(withdrawals)[:16]:
            return {'status':'stale','source_revision':bundle.revision if bundle else None,'scenes':[]}
        scenes={scene.scene_id:scene for scene in bundle.scenes};sources={source.source_id:source for source in bundle.sources}
        scene=scenes.get(parts[2]);require(scene is not None,'story_handle')
        if scene.source_id in withdrawals['sources']:
            return {'status':'withdrawn','source_revision':bundle.revision,'scenes':[]}
        selected=[scene]
        retained=len(canonical(scene_data(scene,sources[scene.source_id])).encode())
        for direction,count in (('previous_scene',before),('next_scene',after)):
            current=scene
            for _ in range(count):
                adjacent=getattr(current,direction)
                if adjacent is None:break
                current=scenes[adjacent]
                size=len(canonical(scene_data(current,sources[current.source_id])).encode())
                if retained+size>24000:break
                retained+=size
                if direction=='previous_scene':selected.insert(0,current)
                else:selected.append(current)
        require(len({item.scene_id for item in selected})==len(selected),'story_adjacency_cycle')
        result=[scene_data(item,sources[item.source_id]) for item in selected]
        return {'status':'ok','source_revision':bundle.revision,'revocation_revision':withdrawals['revision'],
                'target_scene_id':scene.scene_id,'scenes':result,
                'previous_handle':bundle.revision+':'+parts[1]+':'+selected[0].previous_scene if selected[0].previous_scene else None,
                'next_handle':bundle.revision+':'+parts[1]+':'+selected[-1].next_scene if selected[-1].next_scene else None,
                'truncated_before':selected[0].previous_scene is not None,
                'truncated_after':selected[-1].next_scene is not None}

    def check_expected(self,expected):
        if expected is not None:
            current=self.current()
            require(expected==[current.revision if current else None,self.revocations()['revision']],'story_publication_conflict')

    def withdraw(self,source_id,reason,*,expected=None):
        identifier(source_id);text(reason)
        with publish_lock(self.root):
            self.check_expected(expected)
            bundle=self.current();require(bundle is not None and any(s.source_id==source_id for s in bundle.sources),'story_source')
            ledger=self.revocations()
            if source_id not in ledger['sources']:
                ledger['revision']+=1;ledger['sources'][source_id]={'reason':reason,'withdrawn_at':stamp()}
                atomic_json(self.root/'revocations.json',ledger)
        return self.status()

    def rollback(self,revision):
        require(isinstance(revision,str) and re.fullmatch('[a-f0-9]{64}',revision),'story_revision')
        with publish_lock(self.root):
            self.revocations()
            value=read_json(self.release_path(revision))
            require(parse_bundle(value,allow_fixture=True).revision==revision,'story_revision_mismatch')
            atomic_json(self.root/'current.json',{'schema':'taki-story-pointer/1','revision':revision})
        return self.status()

    def withdraw_persona(self,persona_id,reason):
        identifier(persona_id);text(reason)
        with publish_lock(self.root):
            bundle=self.current();require(bundle is not None and any(p.persona_id==persona_id for p in bundle.personas),'story_persona')
            ledger=self.revocations()
            if persona_id not in ledger['personas']:
                ledger['revision']+=1;ledger['personas'][persona_id]={'reason':reason,'withdrawn_at':stamp()}
                atomic_json(self.root/'revocations.json',ledger)
        return self.status()

    def approve_persona(self,persona_id,reviewer,confirmation_ref,*,revision):
        identifier(persona_id);text(reviewer,maximum=300);text(confirmation_ref,maximum=300)
        with publish_lock(self.root):
            bundle=self.current();require(bundle is not None and revision==bundle.revision,'story_revision_mismatch')
            persona=next((p for p in bundle.personas if p.persona_id==persona_id),None)
            require(persona is not None and persona.review_status=='draft','story_persona')
            require(persona_id not in self.revocations()['personas'],'story_persona_withdrawn')
            claims={claim.claim_id:claim for claim in bundle.claims}
            scene_ids={scene_id for claim_id in persona.source_claim_ids for scene_id in claims[claim_id].source_scene_ids}
            source_ids={scene.source_id for scene in bundle.scenes if scene.scene_id in scene_ids}
            require(all(claims[c].review_status=='approved' for c in persona.source_claim_ids),'story_persona_unapproved_claim')
            require(not source_ids & self.revocations()['sources'].keys(),'story_persona_withdrawn')
            require(not any(source.source_kind=='synthetic_fixture' or source.use_scope=='fixture_only' for source in bundle.sources if source.source_id in source_ids),'story_persona_fixture')
            atomic_json(self.root/'persona.json',{'schema':'taki-story-persona-approval/1','source_revision':revision,
                'persona_id':persona_id,'persona_sha256':digest(asdict(persona)),
                'reviewed_by':reviewer,'confirmation_ref':confirmation_ref,'approved_at':stamp()})
        return self.persona()

    def persona(self):
        pointer=self.root/'persona.json'
        if not pointer.exists():return None
        row=read_json(pointer)
        require(isinstance(row,dict) and set(row)=={'schema','source_revision','persona_id','persona_sha256','reviewed_by','confirmation_ref','approved_at'}
                and row['schema']=='taki-story-persona-approval/1','story_persona_approval')
        text(row['reviewed_by'],maximum=300);text(row['confirmation_ref'],maximum=300);optional_time(row['approved_at'])
        require(row['approved_at'] is not None,'story_persona_approval');identifier(row['persona_id'])
        require(all(isinstance(row[key],str) and re.fullmatch('[a-f0-9]{64}',row[key])
                    for key in ('source_revision','persona_sha256')),'story_persona_approval')
        bundle=self.current()
        if bundle is None or row['source_revision']!=bundle.revision:return None
        persona=next((p for p in bundle.personas if p.persona_id==row['persona_id']),None)
        require(persona is not None and digest(asdict(persona))==row['persona_sha256'],'story_persona_hash')
        claims={claim.claim_id:claim for claim in bundle.claims};scenes={scene.scene_id:scene for scene in bundle.scenes}
        source_ids={scenes[scene_id].source_id for cid in persona.source_claim_ids for scene_id in claims[cid].source_scene_ids}
        withdrawals=self.revocations()
        if source_ids & withdrawals['sources'].keys() or persona.persona_id in withdrawals['personas']:return None
        return {'persona_id':persona.persona_id,'prompt':persona.prompt,'source_revision':bundle.revision,
                'revocation_revision':withdrawals['revision'],
                'source_claim_ids':list(persona.source_claim_ids),'covers_until':persona.covers_until,
                'review_status':'approved','reviewed_by':row['reviewed_by'],'confirmation_ref':row['confirmation_ref'],
                'approved_at':row['approved_at'],'persona_sha256':row['persona_sha256']}
