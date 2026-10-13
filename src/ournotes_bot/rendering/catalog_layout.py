# L3
# Input: Complete captured cards, locale and an injected validated artwork loader.
# Output: Compact, ordered readable image parts and matching card order.
# Pos: Rendering / Card; approved role and memory list layout.
# Effects: Pillow rendering only; assets loaded through the supplied callback.
"""Colour rows flow left; rarity never reserves an empty column."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import re

from ..data import SupportCard, localized_text
from .haneoka_theme import Canvas, BG, INK, MUTED, frame, lines
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


def summaries(card,locale='zh'):
    contracts=card.catalog.get('compact_skills',[])
    result=[]
    for i,skill in enumerate(card.skills):
        label,color=LABELS.get(skill.kind,('技',MUTED))
        contract=contracts[i] if i<len(contracts) else None
        value=contract.get('levels',{}).get('5') if contract and locale=='zh' else None
        if isinstance(value,dict):
            text=value['short']
            # Arrow means the stated target bonus, never skill level growth.
            target=value.get('enhanced_target')
            if target and '→' in text:
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
                text=re.sub(r'(血判|简型) ([表技形])(?=\+)',r'\1',text).replace('；','\n')
            if skill.kind=='liveSkill':
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
    grouped={}
    for label,color,text in result:
        if label in grouped:grouped[label]=(color,grouped[label][1]+'\n'+text)
        else:grouped[label]=(color,text)
    return [(label,*grouped[label]) for label in ('队','演','支','激','技') if label in grouped] or [('技',MUTED,'未知')]


def card_face(canvas,card,x,y,w,h,load,*,level=None,awake=None):
    border=frame(w,h,card.rarity)
    canvas.image.paste(border,(x-4,y-4),border)
    canvas.paste(load(card.thumbnail_url,(w,h)),x,y,w,h)
    attr=card.catalog.get('attribute_icon')
    if attr:
        canvas.paste(load(attr,(max(18,w//4),max(18,w//4))),x+2,y+2,max(18,w//4),max(18,w//4),True)
    rarity={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(card.rarity)
    badge=f'RarityIconCenter_{rarity}.png'
    if awake is None and rarity:
        canvas.badge(badge,x+w-max(28,w//3),y+h-13,max(26,w//3),24)
    if level is not None:
        canvas.text(x+3,y+h-10,f'Lv.{level}',max(14,w//7),'white',True,stroke=1)
    if awake is not None and 0<=awake<=5:
        badge=f"{'snaplimit_' if isinstance(card,SupportCard) else 'CardRank'}{awake}.png"
        canvas.badge(badge,x+w-26,y+h-12,24,22)


@dataclass(frozen=True)
class CardRow:
    cards: tuple
    offsets: tuple
    texts: tuple
    height: int


def render_list(cards,query,locale='zh',*,load):
    cards=ordered(cards)
    if not cards:return (),cards,()
    support=isinstance(cards[0],SupportCard)
    pitch,artw,arth,textw=(216,144,81,166) if support else (128,96,96,98)
    groups=defaultdict(list)
    for index,card in enumerate(cards):groups[(person_key(card),card.card_type)].append((index,card))
    rows=[]
    for group in groups.values():
        for start in range(0,len(group),8):
            items=group[start:start+8]
            texts=tuple(tuple((label,color,lines(text,textw,16)) for label,color,text in summaries(c,locale)) for _,c in items)
            slots=max(map(len,texts)); heights=[max((len(t[i][2]) if i<len(t) else 0 for t in texts),default=1)*20 for i in range(slots)]
            offsets=tuple(sum(heights[:i]) for i in range(slots))
            rows.append(CardRow(tuple(items),offsets,texts,max(arth,22*max((len(c.catalog.get('people',[])) for _,c in items),default=0) if support else arth)+34+sum(heights)+14))
    columns=max(len(r.cards) for r in rows);width=max(284,130+columns*pitch+24)
    title='留影卡列表' if support else '角色卡列表'
    subtitle=f'{query} · 技能 Lv.5 · 共 {len(cards)} 张'
    head=100+len(lines(subtitle,width-60,20))*28
    assets={}
    def cached(url,size):
        key=(url,size)
        if key not in assets:assets[key]=load(url,size)
        return assets[key]
    requests=set()
    for card in cards:
        requests.add((card.thumbnail_url,(artw,arth)))
        if card.catalog.get('attribute_icon'):
            side=max(18,artw//4);requests.add((card.catalog['attribute_icon'],(side,side)))
        for person in card.catalog.get('people',[]):
            if person.get('face'):
                requests.add((person['face'],(44,44)))
                if support:requests.add((person['face'],(22,22)))
    def paint(start,stop,height):
        c=Canvas(width,height);c.text(28,43,title,36,bold=True)
        c.paragraph(28,76,subtitle,width-56,20,28,color=MUTED)
        y=head;last_person=None
        for row in rows[start:stop]:
            first=row.cards[0][1];person=first.catalog.get('people',[{}])[0] if first.catalog.get('people') else {}
            if person_key(first)!=last_person:
                if person.get('face'):c.paste(cached(person['face'],(44,44)),28,y+4,44,44,True)
                name=person.get('name',{}).get(locale) or first.character
                c.paragraph(24,y+54,name,90,16,22)
                last_person=person_key(first)
            for col,((index,card),text) in enumerate(zip(row.cards,row.texts)):
                x=130+col*pitch;card_face(c,card,x,y+4,artw,arth,cached)
                if support:
                    for j,p in enumerate(card.catalog.get('people',[])):
                        if p.get('face'):c.paste(cached(p['face'],(22,22)),x+artw+8,y+4+j*22,22,22,True)
                base=y+max(arth,22*len(card.catalog.get('people',[])) if support else arth)+18
                c.text(x,base,f'{index+1} · ID {card.id}',14,MUTED)
                base=y+row.height-sum(max((len(t[i][2]) if i<len(t) else 0 for t in row.texts),default=1)*20 for i in range(len(row.offsets)))-14
                for i,(label,color,wrapped) in enumerate(text):
                    cy=base+row.offsets[i]+10;c.text(x,cy,label,16,color,True)
                    for n,line in enumerate(wrapped):c.text(x+22,cy+n*20,line,16)
            y+=row.height
        c.text(28,height-18,f'{rows[start].cards[0][0]+1}–{rows[stop-1].cards[-1][0]+1} / {len(cards)} · /选 编号',16,MUTED)
        return c.image
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            for key,image in zip(sorted(requests),pool.map(lambda key:load(*key),sorted(requests))):assets[key]=image
        parts=render_parts(tuple(r.height for r in rows),width,head+38,paint)
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
