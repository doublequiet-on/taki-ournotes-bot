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


# Resolve only established names and pair shorthands. A shorthand identifies
# the people to look up, independently of what any source says about them.
_PAIR_NAMES = re.compile(r'海希|希海')
_SAKIKO_FATHER = re.compile(r'祥爹|祥子(?:他|她|的)?(?:爹|爸爸|父亲|父親)(?:大人)?')
_STORY_NAMES = (
    ('立希', '椎名立希'), ('海鈴', '海铃', '八幡海鈴', '八幡海铃'),
    ('愛音', '爱音', '千早愛音', '千早爱音'), ('燈', '灯', '高松燈', '高松灯'),
    ('楽奈', '乐奈'), ('祥子',), ('そよ', '爽世'), ('睦', '若叶睦', '若葉睦'),
    ('初華', '初华'), ('初音',), ('清告',), ('定治',),
)
_WORKS = (
    (re.compile(r'our\s*notes|(?<![a-z0-9])on(?![a-z0-9])'), re.compile(r'our\s*notes')),
    (re.compile(r'ガルパ|少女乐团派对|(?<![a-z0-9])gbp(?![a-z0-9])'), re.compile(r'ガルパ|girls band party|gbp')),
    (re.compile(r"it.s mygo|mygo.{0,10}(?:动画|動畫|第.{1,3}[集話话])"), re.compile(r"it.s mygo")),
    (re.compile(r'ave\s*mujica.{0,10}(?:动画|動畫|第.{1,3}[集話话])'), re.compile(r'ave\s*mujica')),
)
_NUMBER = r'[0-9一二三四五六七八九十]+'
_EPISODE = re.compile(rf'第\s*({_NUMBER})\s*[集話话]')
_KINSHIP_PASSAGE = (r'小姨|姨母|祖父|外公|姪|侄女|亲属|親屬|同父异母|同父異母|'
                    r'同母异父|同母異父|妹妹|姉妹|父亲|父親|母亲|母親|[姐姊妹]夫|同[辈輩]')
_FIELDS = (
    (r'生日|誕生日|诞生日', r'誕生日|诞生日|生日'),
    (r'喜欢吃|喜歡吃|爱吃|愛吃|喜欢的食物|喜歡的食物|好きな食べ物',
     r'喜欢吃|喜歡吃|爱吃|愛吃|喜欢的食物|喜歡的食物|好きな食べ物|好物'),
    (r'兴趣|興趣|爱好|愛好|趣味', r'[兴興]趣[是为為在:]|[爱愛]好|趣味'),
    (r'打工|兼职|兼職|バイト', r'打工|兼职|兼職|バイト|勤務'),
    (r'作曲', r'作曲'),
    (r'作词|作詞', r'作词|作詞'),
    (r'什么关系|什麼關係|关系怎么|關係怎麼', r'同じクラス|同級生|同班|同学|同學|相处|相處|搭档|搭檔|情侣|付き合|' + _KINSHIP_PASSAGE),
    (r'官配|官方情侣', r'同じクラス|同級生|同班|同学|同學|相处|相處|搭档|搭檔|情侣|付き合'),
    (r'[辈輩][分份]|亲属关系|親屬關係|血[缘緣]|外祖父|(?:父亲|父親|爸爸|母亲|母親|妈妈|媽媽)(?:是)?(?:谁|誰)',
     _KINSHIP_PASSAGE),
    (r'小姨|姨妈|姨媽|姨母', r'小姨|姨妈|姨媽|姨母|姪|侄女|同父异母|同父異母'),
    (r'(?:差|隔)(?:了)?[几幾][辈輩]|[姐姊妹]夫',
     r'同[辈輩]|[姐姊妹]夫|小姨子|姪|侄女|同父异母|同父異母'),
    (r'真正的|本来的|本來的|本名|真名|同一(?:个|個)?人|名[义義].{0,8}出道',
     r'本名|真名|妹妹|姉妹|借用.{0,8}名|冒名|冒用|名乗|以.{0,12}(?:之名|的名字)'),
    (r'[见見][过過]|[见見]面|碰[过過]面|[说說](?:[过過])?[话話]|交[谈談]',
     r'[见見]面|碰面|当面|當面|交[谈談]|会った|会う|向[^，。]{1,15}(?:透露|坦白)'),
    (r'(?:在哪|哪[里裡]|何[处處]).{0,24}(?:[见見]面|[说說](?:[过過])?[话話]|交[谈談])',
     r'会[场場]|[现現][场場]|開業|揭幕|活动上|活動上'),
    (r'(?:知道|知[晓曉]|得知).{0,22}(?:身世|出身|冒名|借用.{0,9}(?:名|身份))',
     r'(?:(?:听|聽)(?:完|到)|知[晓曉]|得知|才知道|听说|聽說|被告知).{0,18}(?:身世|出身|冒名|借用)'),
    (r'对.{0,8}(?:特别|特別)|對.{0,8}(?:特别|特別)', r'甘い|更宽容|更寬容|特别|特別|偏心'),
    (r'一辈子乐队|一輩子樂隊|一生.{0,5}バンド', r'一辈子.{0,4}乐队|一輩子.{0,4}樂隊|一生.{0,5}バンド|一直.{0,5}乐队'),
    (r'羁绊|羈絆|キズナ', r'羁绊|羈絆|キズナ'),
)
_FIELDS = tuple((re.compile(question), re.compile(content)) for question, content in _FIELDS)
_SOURCE_PRIORITY = {'official_original': 0, 'official_summary': 1}
_STORY_REGIONS = (
    (re.compile(r'日服|(?<!\w)jp(?!\w)'), 'jp'),
    (re.compile(r'国际服|國際服|台服|臺服|(?<!\w)intl(?!\w)'), 'intl'),
    (re.compile(r'国服|國服|港服|(?<!\w)(?:hk|tw)(?!\w)'), 'tw'),
    (re.compile(r'韩服|韓服|(?<!\w)kr(?!\w)'), 'kr'),
    (re.compile(r'英服|美服|欧服|歐服|(?<!\w)en(?!\w)'), 'en'),
)


def _story_region(source):
    if normalized(source.work) != 'bang dream! our notes':
        return None
    match = re.match(r'https://haneoka\.org/(jp|intl)/', source.uri)
    return match[1] if match else None


def _episode_number(value):
    if value.isdecimal():
        return int(value)
    digits = '零一二三四五六七八九'
    before, tens, after = value.partition('十')
    if {before, after} <= (set(digits) | {''}):
        if tens:
            return (digits.index(before) if before else 1) * 10 + (digits.index(after) if after else 0)
        return digits.index(before)
    return None


def _fact_score(scene, source, query, needles, groups, work, regions, episode, facets):
    title = normalized(scene.title)
    chapter = normalized(scene.chapter)
    if work and not work.search(normalized(source.work)):
        return None
    if regions and _story_region(source) not in regions:
        return None
    if episode is not None and not any(_episode_number(match) == episode
                                        for match in _EPISODE.findall(title + ' ' + chapter)):
        return None

    # The first passage of the IP reference records is the actual summary.
    # Their later fields contain citation inventories, negative examples and
    # collection notes, which can mention subjects absent from the summary.
    passages = scene.utterances[:1] if scene.scene_id.startswith('bdip-') else scene.utterances
    body = normalized(' '.join(passage.text_ja + ' ' + (passage.translation or '') for passage in passages))
    labels = ' '.join((title, chapter, *(normalized(name) for name in scene.participants)))
    named_titles = re.findall(r'《([^》]{2,80})》', query)
    if any(name not in labels + ' ' + body for name in named_titles):
        return None
    if any(not any(name in labels or name in body for name in group) for group in groups):
        return None
    if 'mygo' in query and not groups and 'mygo' not in labels + ' ' + body:
        return None
    covered = sum(bool(facet.search(body)) for facet in facets)
    # A named profile cannot cover an absent field. A story excerpt can be a
    # candidate for a named chapter or link story even if little text is kept.
    if facets and not covered and not (work and any(facet.search(title) for facet in facets)):
        return None
    haystack = labels + ' ' + body
    words = terms(haystack)
    score = sum(min(count, words[term]) * (2 if len(term) > 2 else 1) for term, count in needles.items())
    if not score and not covered:
        return None
    score += 8 * sum(any(name in title for name in group) for group in groups)
    main_title = re.split(r'[|｜／]', title, maxsplit=1)[0].strip()
    if len(main_title)>=4 and main_title in query:
        score += 12
    # Only promote a source after subject, scope and topic have matched.
    # Lexical scoring still decides among equally suitable sources.
    priority = _SOURCE_PRIORITY.get(source.source_kind, 2) if covered or episode is not None or work else 2
    return -covered, priority, -score, scene.scene_id


def search_scenes(bundle:StoryBundle,query,filters=None,*,limit=6,excluded_sources=(),facts_only=False):
    require(isinstance(query,str) and 0<len(query.strip())<=300,'story_query')
    require(type(limit) is int and 1<=limit<=10,'story_limit')
    filters={} if filters is None else filters
    require(isinstance(filters,dict) and set(filters)<={'work','source_kind','character','source_id','chapter'},'story_filter')
    require(all(isinstance(value,str) and 0<len(value)<=100 for value in filters.values()),'story_filter')
    if facts_only:
        question = normalized(query)
        expanded = _PAIR_NAMES.sub('八幡海鈴 椎名立希', question)
        trace = [{'from': name, 'to': '八幡海鈴 椎名立希'}
                 for name in dict.fromkeys(_PAIR_NAMES.findall(query))]
        father_roles = dict.fromkeys(_SAKIKO_FATHER.findall(expanded))
        if father_roles:
            expanded = _SAKIKO_FATHER.sub('豐川清告', expanded)
            trace.extend({'from': role, 'to': '豐川清告'} for role in father_roles)
        rewritten, alias_trace = rewrite(expanded, bundle.aliases)
        trace.extend(alias_trace)
        names = re.sub(r'初音(?:未来|未來)', '', rewritten)
        groups = tuple(group for group in _STORY_NAMES if any(name in names for name in group))
        work = next((target for pattern, target in _WORKS if pattern.search(rewritten)), None)
        regions = tuple(region for pattern, region in _STORY_REGIONS if pattern.search(rewritten))
        episode_match = _EPISODE.search(rewritten)
        episode = _episode_number(episode_match[1]) if episode_match else None
        facets = tuple(content for pattern, content in _FIELDS if pattern.search(normalized(query)))
    else:
        rewritten,trace=rewrite(query,bundle.aliases)
    needles=terms(rewritten);excluded=set(excluded_sources)
    sources={source.source_id:source for source in bundle.sources}
    scored=[]
    for scene in bundle.scenes:
        source=sources[scene.source_id]
        if scene.source_id in excluded:continue
        # The published expression namespace contains original dialogue
        # examples. Those examples are useful for tone, never story evidence.
        if facts_only and scene.scene_id.startswith('taki-expression-guide-'):continue
        match_values={'work':source.work,'source_kind':source.source_kind,'source_id':source.source_id,'chapter':scene.chapter}
        if any(normalized(value)!=normalized(match_values[key]) for key,value in filters.items() if key!='character'):continue
        if 'character' in filters:
            character,_=rewrite(filters['character'],bundle.aliases)
            if not any(character==normalized(name) for name in scene.participants):continue
        if facts_only:
            rank = _fact_score(scene, source, rewritten, needles, groups, work, regions, episode, facets)
            if rank is not None:
                scored.append((rank, scene))
            continue
        # Translation helps finding Japanese original but never replaces its layer.
        haystack=' '.join((scene.title,scene.chapter,*scene.participants,
                          *(utterance.text_ja for utterance in scene.utterances),
                          *(utterance.translation or '' for utterance in scene.utterances)))
        words=terms(haystack)
        score=sum(min(count,words[term])*(2 if len(term)>2 else 1) for term,count in needles.items())
        if rewritten in normalized(haystack):score+=8
        if score:
            scored.append(((-score, scene.scene_id),scene))
    scored.sort(key=lambda row:row[0])
    return {'query_rewrites':trace,'rewritten_query':rewritten,'total_matches':len(scored),
            'truncated':len(scored)>limit,'scenes':[row[1] for row in scored[:limit]]}


def scene_data(scene,source):
    return {'scene_id':scene.scene_id,'source_id':scene.source_id,'title':scene.title,'chapter':scene.chapter,
            'locator':scene.locator,'participants':list(scene.participants),'story_time':scene.story_time,
            'utterances':[asdict(utterance) for utterance in scene.utterances],
            'source_kind':source.source_kind,'work':source.work,'medium':source.medium,'server':_story_region(source),
            'language':source.language,'source_uri':source.uri,
            'source_version':source.version,'source_content_sha256':source.content_sha256,
            'published_at':source.published_at,'acquired_at':source.acquired_at,
            'use_scope':source.use_scope,'coverage_note':source.coverage_note}
