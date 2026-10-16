# L3
# Input: Complete captured cards, locale and an injected validated artwork loader.
# Output: Compact, ordered readable image parts and matching card order.
# Pos: Rendering / Card; approved role and memory list layout.
# Effects: Pillow rendering only; assets loaded through the supplied callback.
"""Colour rows flow left; rarity never reserves an empty column."""
from .labels import label as ui_label
from collections import defaultdict
from dataclasses import dataclass
import re

from ..data import SupportCard, localized_text, normalize, character_identity
from .haneoka_theme import Canvas, BG, INK, MUTED, frame, lines, prepare_assets
from .readable_pages import render_parts

RARITIES = {4:0, 20:1, 10:1, 3:2, 2:3}
LABELS = {'leaderSkill':('队','#65509a'), 'liveSkill':('演','#237967'),
          'gekisouSkill':('激','#b2476e'), 'supportSkill':('支','#237967'),
          'gekisouSupportSkill':('激','#b2476e')}


def person_key(card):
    people = card.catalog.get('people', [])
    # Multi-member memories have one canonical placement; query identity never
    # changes the tile content or the related-character portraits.
    return min(((p.get('band') or 999, p.get('order') if p.get('order') is not None else 999, p['id']) for p in people),
               default=(999,999,card.character))


def ordered(cards):
    return tuple(sorted(cards,key=lambda c:(person_key(c),c.card_type,RARITIES.get(c.rarity,9),c.id)))


def compact(text):
    substitutions = [('表演','表'),('技巧','技'),('形象','形'),('全参数','全'),
        ('LUCK槽获取','L槽'),('JUST获取','J'),('COMBO获取','C'),('LUCKY RUSH中奖：','中奖'),
        ('发动L≥','血≥'),('演出LIFE型','血判'),('演出简单型','简型'),('RUSH得分','RUSH'),
        ('延时+','延时'),(' · 全段',''),('得分',''),('：下个P转J','转J')]
    for old,new in substitutions:
        text=text.replace(old,new)
    return text


def memory_summary(value):
    """The approved comparison keeps the normal value and names its condition."""
    text=compact(value['short']);target=value.get('enhanced_target','').removesuffix('!!!!!')
    if not target:return text
    family=value.get('family')
    if family in {'演出延时','LIFE回复','LUCK得分'}:
        match=re.fullmatch(r'(.*?)(\d+(?:\.\d+)?)→(\d+(?:\.\d+)?)(.*)',text)
        if match:
            prefix,normal,enhanced,suffix=match.groups()
            return prefix+normal+suffix+'\n'+target+'：'+enhanced+suffix
    if family=='PERFECT转JUST':
        match=re.fullmatch(r'每(\d+)→(\d+)P转J×(\d+)',text)
        if match:
            normal,enhanced,limit=match.groups()
            return f'每{normal}P转J×{limit}\n{target}：每{enhanced}P'
    if family in {'演出判定强化','COMBO判定强化'}:
        match=re.fullmatch(r'(?:COMBO )?G→G/GOOD转P×(\d+)',text)
        if match:
            prefix='COMBO判强' if family=='COMBO判定强化' else '判强'
            duration=value.get('duration_seconds',0)
            duration=f'/{duration:g}s' if duration else ''
            return f'{prefix}G×{match[1]}{duration}\n{target}：含GOOD'
    return text+('\n'+target if '→' in text or 'enhanced_target' in value else '')


def summaries(card,locale='zh',*,shared_duration=None,shared_target=None):
    contracts=card.catalog.get('compact_skills',[])
    result=[]
    for i,skill in enumerate(card.skills):
        label,color=LABELS.get(skill.kind,('技',MUTED))
        contract=contracts[i] if i<len(contracts) else None
        value=contract.get('levels',{}).get('5') if contract and locale=='zh' else None
        if isinstance(value,dict):
            text=memory_summary(value) if isinstance(card,SupportCard) else value['short']
            # Arrow means the stated target bonus, never skill level growth.
            target=value.get('enhanced_target')
            if not isinstance(card,SupportCard) and target and target!=shared_target and '→' in text:
                text+=f' · {target}'
        elif isinstance(value,str):
            text=value
        else:
            text=localized_text(skill,'description',locale)
        if locale=='zh' and value:
            text=compact(text)
            if skill.kind=='leaderSkill':
                band=getattr(card,'band','')
                if band and text.startswith(band+' '):text=text[len(band)+1:]
                text=re.sub(r'(血判|简型)\s*([表技形])(?=\+)',r'\1',text).replace('；','\n')
            if skill.kind=='liveSkill':
                if shared_duration:text=text.removesuffix(' · '+shared_duration+'秒')
                text=text.replace(' · ','\n')
            if skill.kind=='gekisouSkill':
                if text.startswith('首次JUST起：获取') and text.endswith(' · L≥700'):
                    text=text.replace('首次JUST起：获取','血判J').removesuffix(' · L≥700')
                text=text.replace('COMBO≥25时获取','25C起')
        result.append((label,color,text))
    expected=('leaderSkill','liveSkill','gekisouSkill') if not isinstance(card,SupportCard) else ()
    present={s.kind for s in card.skills}
    for kind in expected:
        if kind not in present:
            label,color=LABELS[kind];result.append((label,color,'未知'))
    if isinstance(card,SupportCard):
        return sorted(result,key=lambda item:item[0]=='激') or [('技',MUTED,'未知')]
    grouped={}
    for label,color,text in result:
        if label in grouped:grouped[label]=(color,grouped[label][1]+'\n'+text)
        else:grouped[label]=(color,text)
    return [(label,*grouped[label]) for label in ('队','演','支','激','技') if label in grouped] or [('技',MUTED,'未知')]


def card_face(canvas,card,x,y,w,h,load,*,level=None,awake=None):
    border=frame(w,h,card.rarity)
    canvas.image.paste(border,(x-4,y-4),border)
    canvas.paste(load(card.full_url if w<h else card.thumbnail_url,(w,h)),x,y,w,h,radius=6)
    attr=card.catalog.get('attribute_icon')
    if attr:
        side=max(18,min(40,w//4))
        canvas.paste(load(attr,(side,side)),x+2,y+2,side,side,True)
    rarity={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(card.rarity)
    badge=f'RarityIconCenter_{rarity}.png'
    if awake is None and rarity:
        bw=max(40,min(72,w//3));bh=round(bw*.72)
        canvas.badge(badge,x+w-bw,y+h-bh//2,bw,bh)
    if level is not None:
        canvas.text(x+3,y+h-10,f'Lv.{level}',max(14,w//7),'white',True,stroke=1)
    if awake is not None and 0<=awake<=5:
        badge=f"{'snaplimit_' if isinstance(card,SupportCard) else 'CardRank'}{awake}.png"
        canvas.badge(badge,x+w-26,y+h-12,24,22)


@dataclass(frozen=True)
class CardRow:
    cards: tuple
    person: dict
    group_start: bool
    offsets: tuple
    texts: tuple
    height: int


def render_list(cards,query,locale='zh',*,load,characters=(),bands=()):
    cards=ordered(cards)
    if not cards:return (),cards,()
    support=isinstance(cards[0],SupportCard)
    # Approved member-list growth V3 and SNAP-list V4, rendered at 2x scale.
    # The left person gutter and each attribute's own row are intentional.
    pitch,artw,arth,textw=(432,288,162,280) if support else (256,192,192,196)
    if support:
        portrait_columns=(max(len(c.catalog.get('people',[])) for c in cards)+2)//3
        pitch=max(pitch,artw+20+portrait_columns*48+28)
    left=260 if support else 264
    durations=set();targets=set()
    for card in cards:
        for contract in card.catalog.get('compact_skills',[]):
            if not contract:continue
            value=contract['levels'].get('5')
            if contract['kind']=='live' and isinstance(value,str):
                m=re.search(r' · ([0-9.]+)秒$',value)
                durations.add(m.group(1) if m else None)
            if isinstance(value,dict) and value.get('enhanced_target'):targets.add(value['enhanced_target'])
    duration=next(iter(durations)) if len(durations)==1 and not support else None
    target=next(iter(targets)) if len(targets)==1 and not support else None
    requested={normalize(character_identity(n)) for n in characters or (query,)}
    people={p['id']:p for card in cards for p in card.catalog.get('people',[])}
    selected={pid for pid,p in people.items() if requested & {normalize(character_identity(n)) for n in p.get('name',{}).values()}}
    requested_bands={normalize(name) for name in bands}
    selected_bands={ident for card in cards for ident,name in zip(card.catalog.get('band_ids',()),card.catalog.get('bands',())) if normalize(name) in requested_bands}
    groups=defaultdict(list)
    for card in cards:
        related=card.catalog.get('people',[])
        if related:
            for p in related:
                if (not selected or p['id'] in selected) and (selected or not selected_bands or p.get('band') in selected_bands):
                    key=(p.get('band') or 999,p.get('order') if p.get('order') is not None else 999,p['id'])
                    groups[key].append(card)
        else:
            groups[person_key(card)].append(card)
    rows=[];records=[]
    for key,group in sorted(groups.items()):
        person=people.get(key[2],{'name':{locale:group[0].character}})
        attributes=defaultdict(list)
        for card in sorted(group,key=lambda c:(c.card_type,RARITIES.get(c.rarity,9),c.id)):
            attributes[card.card_type].append(card)
        first=True
        for same_colour in attributes.values():
            for start in range(0,len(same_colour),8):
                batch=same_colour[start:start+8]
                items=tuple((len(records)+i,card) for i,card in enumerate(batch))
                records.extend(batch)
                texts=tuple(tuple((label,color,lines(text,textw,32)) for label,color,text in summaries(c,locale,shared_duration=duration,shared_target=target)) for c in batch)
                slots=max(map(len,texts)); heights=[max((len(t[i][2]) if i<len(t) else 0 for t in texts),default=1)*40 for i in range(slots)]
                offsets=tuple(sum(heights[:i]) for i in range(slots))
                rows.append(CardRow(items,person,first,offsets,texts,(224 if support else 254)+sum(heights)+36+(36 if first else 0)))
                first=False
    unique_count=len(cards)
    cards=tuple(records)
    width=max(1168 if support else 568,left+max(len(r.cards) for r in rows)*pitch+(44 if support else 48))
    title=ui_label('留影卡列表',locale) if support else ui_label('角色卡列表',locale)
    title_lines=lines(title,width-112,80,True)
    title_extra=(len(title_lines)-1)*96
    query_label=f'{query or ui_label("全部",locale)} · {unique_count} '+ui_label('张',locale)
    skill_label=ui_label('技能',locale)+' Lv.5'+(f' · 演{duration}s' if duration else '')+(f' · →{target}强化' if target else '')
    subtitle=query_label+' · '+skill_label
    if len(lines(subtitle,width-112,42))>1:
        subtitle=query_label+'\n'+skill_label
    notices = []
    if any(c.catalog.get('detail_stale') for c in cards):
        notices.append(ui_label('部分详情使用上次有效缓存。',locale))
    if any(not c.catalog.get('detail_loaded') for c in cards):
        notices.append(ui_label('部分技能资料未获取，未知分类不会当作无技能。',locale))
    notice = ' '.join(notices)
    footer_lines = lines(notice, width-96, 28) if notice else []
    footer_h = 112 + len(footer_lines)*40
    head=252+title_extra+len(lines(subtitle,width-112,42))*52
    assets={}
    def cached(url,size):
        key=(url,size)
        if key not in assets:assets[key]=load(url,size)
        return assets[key]
    requests=set()
    for card in cards:
        requests.add((card.thumbnail_url,(artw,arth)))
        if card.catalog.get('attribute_icon'):
            side=max(18,min(40,artw//4));requests.add((card.catalog['attribute_icon'],(side,side)))
        for person in card.catalog.get('people',[]):
            if person.get('face'):
                requests.add((person['face'],(96,96)))
                requests.add((person['face'],(44,44)))
    def paint(start,stop,height):
        c=Canvas(width,height)
        for n,line in enumerate(title_lines):c.text(56,72+n*96,line,80,bold=True)
        c.paragraph(56,176+title_extra,subtitle,width-112,42,52,color=MUTED)
        y=head
        for row_index,row in enumerate(rows[start:stop],start):
            person=row.person
            if row.group_start:
                if row_index!=start:c.line(48,y,width-48,'#e1dce7')
                y+=36
            if row.group_start or row_index==start:
                if person.get('face'):c.paste(cached(person['face'],(96,96)),86,y+8,96,96,True,radius=48)
                name=person.get('name',{}).get(locale) or row.cards[0][1].character
                for n,line in enumerate(lines(name,208,34,True)):
                    c.text(134,y+152+n*44,line,34,bold=True,align='center')
            for col,((index,card),text) in enumerate(zip(row.cards,row.texts)):
                x=left+col*pitch;card_face(c,card,x,y+8,artw,arth,cached)
                if support and len(card.catalog.get('people',[]))>1:
                    related=sorted(card.catalog['people'],key=lambda p:(p.get('band') or 999,p.get('order') if p.get('order') is not None else 999,p['id']))
                    portrait_rows=min(3,len(related));py=y+8+(arth-(portrait_rows*48-4))//2
                    for j,p in enumerate(related):
                        if p.get('face'):c.paste(cached(p['face'],(44,44)),x+artw+20+(j//3)*48,py+(j%3)*48,44,44,True,radius=22)
                c.text(x,y+(202 if support else 230),f'ID {card.id}',30 if support else 32,MUTED)
                base=y+(224 if support else 254)
                for i,(label,color,wrapped) in enumerate(text):
                    cy=base+row.offsets[i]+18;c.text(x,cy,ui_label(label,locale),32,color,True)
                    for n,line in enumerate(wrapped):c.text(x+44,cy+n*40,line,32)
            y+=row.height-(36 if row.group_start else 0)
        c.text(48,height-footer_h+28,ui_label('展示位置',locale)+f' {rows[start].cards[0][0]+1}–{rows[stop-1].cards[-1][0]+1} / {len(cards)}',28,MUTED)
        c.text(48,height-30,ui_label('按 ID 查详情',locale),28,MUTED)
        for n, text in enumerate(footer_lines):
            c.text(48,height-footer_h+68+n*40,text,28,MUTED)
        return c.image
    try:
        rarities={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}
        badges={f'RarityIconCenter_{rarities[c.rarity]}.png' for c in cards if c.rarity in rarities}
        assets.update(prepare_assets(requests,load,badges))
        parts=render_parts(tuple(r.height for r in rows),width,head+footer_h,paint)
        ranges=tuple((rows[p.start].cards[0][0],rows[p.stop-1].cards[-1][0]+1) for p in parts)
        return parts,cards,ranges
    finally:
        for asset in assets.values():
            if asset is not None:asset.close()


def card_level(card, exp, progress):
    if type(exp) is not int or type(progress) is not int:
        return None
    support = isinstance(card, SupportCard)
    limits = [v['_limitLevel'] for v in card.catalog.get('level_limits', [])
              if v.get('_rank' if support else '_awakeCount') == progress]
    values = [v['level'] for v in card.catalog.get('level_curve', []) if v['exp'] <= exp]
    return min(max(values), limits[0]) if values and len(limits) == 1 else None
