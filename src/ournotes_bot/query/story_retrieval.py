# L3: Scene-level Japanese lexical retrieval with explicit cross-language aliases; no IO.
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
import re
import unicodedata

from ..domain.story import StoryBundle,require


def normalized(text):
    return unicodedata.normalize('NFKC',text).casefold()


def terms(text):
    result=[]
    for word in re.findall(r'[a-z0-9_]+|[\u3040-\u30ff\u3400-\u9fff]+',normalized(text)):
        result.append(word)
        if re.search('[\u3040-\u30ff\u3400-\u9fff]',word):
            result.extend(word[i:i+2] for i in range(max(1,len(word)-1)))
    return Counter(result)


def rewrite(query,aliases):
    value=normalized(query);trace=[]
    for alias,target in sorted(aliases,key=lambda row:(-len(row[0]),row[0])):
        needle,replacement=normalized(alias),normalized(target)
        if needle in value and needle!=replacement:
            value=value.replace(needle,replacement)
            require(len(value)<=1200 and len(trace)<16,'story_query_too_complex')
            trace.append({'from':alias,'to':target})
    return value,trace


def search_scenes(bundle:StoryBundle,query,filters=None,*,limit=6,excluded_sources=()):
    require(isinstance(query,str) and 0<len(query.strip())<=300,'story_query')
    require(type(limit) is int and 1<=limit<=10,'story_limit')
    filters={} if filters is None else filters
    require(isinstance(filters,dict) and set(filters)<={'work','source_kind','character','source_id','chapter'},'story_filter')
    require(all(isinstance(value,str) and 0<len(value)<=100 for value in filters.values()),'story_filter')
    rewritten,trace=rewrite(query,bundle.aliases)
    needles=terms(rewritten);excluded=set(excluded_sources)
    sources={source.source_id:source for source in bundle.sources}
    scored=[]
    for scene in bundle.scenes:
        source=sources[scene.source_id]
        if scene.source_id in excluded:continue
        match_values={'work':source.work,'source_kind':source.source_kind,'source_id':source.source_id,'chapter':scene.chapter}
        if any(normalized(value)!=normalized(match_values[key]) for key,value in filters.items() if key!='character'):continue
        if 'character' in filters:
            character,_=rewrite(filters['character'],bundle.aliases)
            if not any(character==normalized(name) for name in scene.participants):continue
        # Translation helps finding Japanese original but never replaces its layer.
        haystack=' '.join((scene.title,scene.chapter,*scene.participants,
                          *(utterance.text_ja for utterance in scene.utterances),
                          *(utterance.translation or '' for utterance in scene.utterances)))
        words=terms(haystack)
        score=sum(min(count,words[term])*(2 if len(term)>2 else 1) for term,count in needles.items())
        if rewritten in normalized(haystack):score+=8
        if score:scored.append((score,scene.scene_id,scene))
    scored.sort(key=lambda row:(-row[0],row[1]))
    return {'query_rewrites':trace,'rewritten_query':rewritten,'total_matches':len(scored),
            'truncated':len(scored)>limit,'scenes':[row[2] for row in scored[:limit]]}


def scene_data(scene,source):
    return {'scene_id':scene.scene_id,'source_id':scene.source_id,'title':scene.title,'chapter':scene.chapter,
            'locator':scene.locator,'participants':list(scene.participants),'story_time':scene.story_time,
            'utterances':[asdict(utterance) for utterance in scene.utterances],
            'source_kind':source.source_kind,'language':source.language,'source_uri':source.uri,
            'source_version':source.version,'source_content_sha256':source.content_sha256,
            'published_at':source.published_at,'acquired_at':source.acquired_at,'use_scope':source.use_scope}
