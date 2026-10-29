# L3: Strict local story-bundle boundary and atomic publication; no source URL fetches.
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import date,datetime
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from ..domain.story import (DerivedClaim,PersonaRelease,Scene,SourceDocument,StoryBundle,
                           StoryError,Utterance,require)
from .json_validation import decode_json

MAX_BUNDLE_BYTES=32*1024*1024
MAX_SCENES=10000
KINDS=('official_original','official_summary','translation','fan_analysis','synthetic_fixture')
COVERAGE=('collected','missing_source','pending_validation','unavailable')


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def identifier(value):
    require(isinstance(value,str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}',value),'story_id')
    return value


def text(value,*,maximum=2000,empty=False):
    require(isinstance(value,str) and len(value)<=maximum and (empty or bool(value.strip()))
            and not any(ord(c)<32 and c not in '\n\t' or 0xD800<=ord(c)<=0xDFFF for c in value),'story_text')
    return value


def optional_time(value):
    if value is None:return None
    text(value,maximum=40)
    try:
        if len(value)==10:require(date.fromisoformat(value).isoformat()==value,'story_time')
        else:require(datetime.fromisoformat(value.replace('Z','+00:00')).tzinfo is not None,'story_time')
    except ValueError:raise StoryError('story_time') from None
    return value


def shape(value,record):
    require(isinstance(value,dict) and set(value)=={field.name for field in fields(record)},'story_shape')
    return dict(value)


def strings(value,*,ids=False,maximum=100):
    require(isinstance(value,list) and len(value)<=maximum,'story_list')
    parsed=tuple(identifier(v) if ids else text(v,maximum=100) for v in value)
    require(len(set(parsed))==len(parsed),'story_duplicate')
    return parsed


def review(value):
    require(value['review_status'] in ('draft','approved','withdrawn'),'story_review')
    approved=value['review_status']=='approved'
    for key in ('reviewed_by','confirmation_ref'):
        require((isinstance(value[key],str) and bool(text(value[key],maximum=300))) if approved else value[key] is None,'story_review')
    optional_time(value['reviewed_at'])
    require((value['reviewed_at'] is not None)==approved,'story_review')


def source_content_hash(scenes):
    return digest([asdict(scene) if isinstance(scene,Scene) else scene for scene in scenes])


def parse_bundle(value,*,allow_fixture=False):
    require(isinstance(value,dict) and set(value)=={'schema','sources','scenes','claims','personas','aliases'}
            and value['schema']=='taki-story-bundle/1','story_schema')
    for key,limit in (('sources',1000),('scenes',MAX_SCENES),('claims',10000),('personas',50)):
        require(isinstance(value[key],list) and len(value[key])<=limit,'story_capacity')
    sources=[]
    for row in value['sources']:
        row=shape(row,SourceDocument);identifier(row['source_id'])
        require(row['source_kind'] in KINDS and row['coverage'] in COVERAGE,'story_source')
        require(row['use_scope'] in ('public_reference','permitted_local','licensed_reuse','fixture_only'),'story_use_scope')
        require(row['source_kind']!='synthetic_fixture' or allow_fixture and row['use_scope']=='fixture_only','story_fixture_not_publishable')
        require(row['use_scope']!='fixture_only' or allow_fixture,'story_fixture_not_publishable')
        for key in ('work','medium','language','uri','acquisition','version'):text(row[key],maximum=1000)
        require(row['language'] in ('ja','zh','en','mixed'),'story_language')
        require(row['uri'].startswith(('https://','local:')),'story_source_uri')
        text(row['coverage_note'],empty=True)
        optional_time(row['published_at']);optional_time(row['acquired_at'])
        require(row['content_sha256'] is None or isinstance(row['content_sha256'],str)
                and re.fullmatch('[a-f0-9]{64}',row['content_sha256']),'story_hash')
        require(row['coverage']!='collected' or row['content_sha256'] is not None and row['acquired_at'] is not None,'story_source')
        sources.append(SourceDocument(**row))
    by_source={source.source_id:source for source in sources}
    require(len(by_source)==len(sources),'story_duplicate')
    scenes=[]
    for row in value['scenes']:
        row=shape(row,Scene);identifier(row['scene_id']);identifier(row['source_id'])
        require(row['source_id'] in by_source and by_source[row['source_id']].coverage=='collected','story_scene_source')
        for key in ('title','chapter','locator'):text(row[key],maximum=300)
        row['participants']=strings(row['participants'])
        if row['story_time'] is not None:text(row['story_time'],maximum=200)
        for key in ('previous_scene','next_scene'):
            if row[key] is not None:identifier(row[key])
        require(isinstance(row['utterances'],list) and 0<len(row['utterances'])<=300,'story_utterances')
        utterances=[]
        for index,utterance in enumerate(row['utterances'],1):
            utterance=shape(utterance,Utterance)
            require(type(utterance['order']) is int and utterance['order']==index,'story_utterance_order')
            text(utterance['speaker'],maximum=100);text(utterance['text_ja'],maximum=4000);text(utterance['locator'],maximum=300)
            require(utterance['speaker'] in row['participants'],'story_speaker')
            if utterance['translation'] is not None:text(utterance['translation'],maximum=4000)
            utterances.append(Utterance(**utterance))
        row['utterances']=tuple(utterances)
        scene=Scene(**row)
        require(len(canonical(asdict(scene)).encode())<=16000,'story_scene_capacity')
        scenes.append(scene)
    by_scene={scene.scene_id:scene for scene in scenes}
    require(len(by_scene)==len(scenes),'story_duplicate')
    for scene in scenes:
        for direction,opposite in (('previous_scene','next_scene'),('next_scene','previous_scene')):
            adjacent=getattr(scene,direction)
            if adjacent is not None:
                require(adjacent in by_scene and adjacent!=scene.scene_id and by_scene[adjacent].source_id==scene.source_id
                        and getattr(by_scene[adjacent],opposite)==scene.scene_id,'story_adjacency')
    for source in sources:
        content=[scene for scene in scenes if scene.source_id==source.source_id]
        if source.coverage=='collected':require(bool(content),'story_collected_without_scenes')
        if content:require(source.content_sha256==source_content_hash(content),'story_hash_mismatch')
    done=set()
    for scene in scenes:
        current=scene.scene_id;path=set()
        while current is not None and current not in done:
            require(current not in path,'story_adjacency_cycle')
            path.add(current);current=by_scene[current].next_scene
        done.update(path)
    claims=[]
    for row in value['claims']:
        row=shape(row,DerivedClaim);identifier(row['claim_id']);text(row['text'],maximum=4000)
        require(row['kind'] in ('original_fact','analysis'),'story_claim_kind')
        row['source_scene_ids']=strings(row['source_scene_ids'],ids=True)
        require(bool(row['source_scene_ids']) and set(row['source_scene_ids'])<=by_scene.keys(),'story_claim_source')
        if row['kind']=='original_fact':
            require(all(by_source[by_scene[s].source_id].source_kind=='official_original' for s in row['source_scene_ids']),'story_claim_layer')
        review(row);claims.append(DerivedClaim(**row))
    by_claim={claim.claim_id:claim for claim in claims};require(len(by_claim)==len(claims),'story_duplicate')
    personas=[]
    for row in value['personas']:
        row=shape(row,PersonaRelease);identifier(row['persona_id']);text(row['prompt'],maximum=6000)
        row['source_claim_ids']=strings(row['source_claim_ids'],ids=True);optional_time(row['covers_until'])
        require(bool(row['source_claim_ids']) and set(row['source_claim_ids'])<=by_claim.keys(),'story_persona_source')
        review(row)
        if row['review_status']=='approved':
            require(all(by_claim[c].review_status=='approved' for c in row['source_claim_ids']),'story_persona_unapproved_claim')
            require(not any(by_source[by_scene[s].source_id].source_kind=='synthetic_fixture'
                for c in row['source_claim_ids'] for s in by_claim[c].source_scene_ids),'story_persona_fixture')
        personas.append(PersonaRelease(**row))
    require(len({persona.persona_id for persona in personas})==len(personas),'story_duplicate')
    require(isinstance(value['aliases'],dict) and len(value['aliases'])<=2000,'story_aliases')
    aliases=tuple((text(k,maximum=100),text(v,maximum=100)) for k,v in value['aliases'].items())
    require(len(canonical(value).encode())<=MAX_BUNDLE_BYTES,'story_capacity')
    return StoryBundle(digest(value),tuple(sources),tuple(scenes),tuple(claims),tuple(personas),aliases)


def read_json(path):
    path=Path(path)
    require(not path.is_symlink() and path.is_file() and path.stat().st_size<=MAX_BUNDLE_BYTES,'story_file')
    with path.open('rb') as stream:raw=stream.read(MAX_BUNDLE_BYTES+1)
    require(len(raw)<=MAX_BUNDLE_BYTES,'story_file')
    try:return decode_json(raw)
    except (ValueError,UnicodeError,RecursionError):raise StoryError('story_json') from None


def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    require(not path.is_symlink(),'story_file')
    descriptor,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8',newline='\n') as stream:
            stream.write(canonical(value)+'\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
    finally:
        if os.path.exists(name):os.unlink(name)


@contextmanager
def publish_lock(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    require(not root.is_symlink(),'story_file')
    lock_path=root/'.publish.lock';require(not lock_path.is_symlink(),'story_file')
    with lock_path.open('a+b') as stream:
        try:
            if os.name=='nt':
                import msvcrt
                stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise StoryError('story_publish_busy') from None
        try:yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)
