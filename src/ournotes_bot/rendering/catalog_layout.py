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
from .haneoka_theme import Canvas, BG, INK, MUTED, frame, font, lines, prepare_assets
from .readable_pages import render_parts

RARITIES = {4:0, 20:1, 10:1, 3:2, 2:3}
LABELS = {'leaderSkill':('队','#65509a'), 'liveSkill':('演','#237967'),
          'gekisouSkill':('激','#b2476e'), 'supportSkill':('支','#237967'),
          'gekisouSupportSkill':('激','#b2476e')}


def person_key(card):
    people = card.catalog.get('people', [])
    # Use source character order, independent of the query label.
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
        if contract and contract.get('automatic'):
            # Audited shorthand rewrites below may drop scope from a new template.
            result.append((label,color,value or localized_text(skill,'description',locale)))
            continue
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


def card_face(canvas,card,x,y,w,h,load,*,level=None,rank=None,growth=False):
    border=frame(w,h,card.rarity)
    canvas.image.paste(border,(x-4,y-4),border)
    canvas.paste(load(card.full_url if w<h else card.thumbnail_url,(w,h)),x,y,w,h,radius=6)
    attr=card.catalog.get('attribute_icon')
    if attr:
        side=max(18,min(40,w//4))
        canvas.paste(load(attr,(side,side)),x+2,y+2,side,side,True)
    if rank is None and not growth:
        support=isinstance(card,SupportCard)
        bw=max(40,min(72,w//4)) if support else max(40,min(80,round(w*5/12)))
        bh=round(bw*2/3) if support else round(bw*.725)
        canvas.rarity_badge(card.rarity,x+w-6,y+h-6,bw,bh)
    if level is not None:
        canvas.text(x+3,y+h-10,f'Lv.{level}',max(14,w//7),'white',True,stroke=1)
    elif growth:
        canvas.text(x+3,y+h-10,'Lv.—',max(14,w//7),'white',True,stroke=1)
    if rank is not None and 0<=rank<=5:
        badge=f"{'snaplimit_' if isinstance(card,SupportCard) else 'CardRank'}{rank}.png"
        canvas.badge(badge,x+w-26,y+h-12,24,22)
    elif growth:
        canvas.text(x+w-14,y+h-10,'—',max(14,w//7),'white',True,stroke=1)


@dataclass(frozen=True)
class CardRow:
    cards: tuple
    headings: tuple
    group_start: bool
    offsets: tuple
    texts: tuple
    height: int


def group_headings(people, locale, fallback):
    """Measure the complete identity gutter, including on continuation pages."""
    def name(person):
        names = person.get('name', {})
        return names.get(locale) or next((value for value in names.values() if value), fallback)
    if len(people) == 1:
        person = people[0]
        wrapped = lines(name(person), 208, 34, True)
        return ((person, 134, 0, 96, 34, wrapped),), 152 + len(wrapped)*44
    headings = []
    top = 0
    for start in range(0, len(people), 2):
        pair = people[start:start+2]
        wrapped = tuple(lines(name(p), 104, 26, True) for p in pair)
        for col, (person, text_lines) in enumerate(zip(pair, wrapped)):
            headings.append((person, 78+col*112, top, 80, 26, text_lines))
        top += 120 + max(map(len, wrapped))*38 + 16
    return tuple(headings), top


def _memory_headings(people, locale, fallback):
    """Measure complete names beneath compact portraits, including long aliases."""
    def name(person):
        names = person.get('name', {})
        return names.get(locale) or next((v for v in names.values() if v), fallback)
    if len(people) == 1:
        wrapped = tuple((line, 26) for line in lines(name(people[0]), 208, 26, True))
        return ((people[0], 136, 0, 80, wrapped),), 132 + (len(wrapped)-1)*34
    texts = []
    for person in people:
        wrapped = []
        for part in name(person).split(' / '):
            size = max(12, min(18, int(18*72/max(1, font(18, True, part.isascii()).getlength(part)))))
            wrapped.extend((line, size) for line in lines(part, 72, size, True))
        texts.append(tuple(wrapped))
    headings, top = [], 0
    for start in range(0, len(people), 3):
        stop = min(start+3, len(people))
        count = stop-start
        pitch = 96 if count == 2 else 84
        for col, i in enumerate(range(start, stop)):
            headings.append((people[i], 136-(count-1)*pitch//2+col*pitch, top, 56, texts[i]))
        top += 80 + 22*max(len(text) for text in texts[start:stop])
    return tuple(headings), top


def _render_memory_list(cards, query, locale, load):
    """Merged character cells, colour rows and descending rarity within each row."""
    groups = defaultdict(list)
    group_people = {}
    for card in cards:
        related = card.catalog.get('people', [])
        if related:
            related = tuple(sorted(related, key=lambda p: (p.get('band') or 999, p['id'])))
            key = (len(related) == 1, 0, tuple((p.get('band') or 999, p['id']) for p in related))
        else:
            names = sorted(set(card.characters or (card.character,)), key=lambda n: normalize(character_identity(n)))
            related = tuple({'name': {locale: name}} for name in names)
            key = (len(related) == 1, 1, tuple(normalize(character_identity(n)) for n in names))
        groups[key].append(card)
        group_people[key] = related
    rows, records, headings = [], [], {}
    for key, group in sorted(groups.items()):
        headings[key] = _memory_headings(group_people[key], locale, group[0].character)
        attributes = defaultdict(list)
        for card in sorted(group, key=lambda c: (c.card_type, RARITIES.get(c.rarity, 9), c.id)):
            attributes[card.card_type].append(card)
        for same_colour in attributes.values():
            for start in range(0, len(same_colour), 8):
                batch = same_colour[start:start+8]
                texts = tuple(tuple((label, color, lines(value.replace(' / 上限', '\n上限'), 268, 24))
                                    for label, color, value in summaries(card, locale)) for card in batch)
                text_height = max(sum(len(wrapped)*30 for _, _, wrapped in text)+8*(len(text)-1) for text in texts)
                items = tuple((len(records)+i, card) for i, card in enumerate(batch))
                records.extend(batch)
                # Every complete row can start a page and must fit its full identity.
                rows.append((key, items, texts, max(236+text_height, headings[key][1]+32)))
    records = tuple(records)
    width = 260 + max(len(row[1]) for row in rows)*348
    title = lines(ui_label('留影卡列表', locale), width-72, 44, True)
    subtitle = lines(f'{query or ui_label("全部", locale)} · {len(records)} '+ui_label('张', locale)
                     +' · '+ui_label('技能', locale)+' Lv.5', width-76, 25)
    head = 140+(len(title)-1)*56+(len(subtitle)-1)*34
    notices = []
    if any(c.catalog.get('detail_stale') for c in cards):
        notices.append(ui_label('部分详情使用上次有效缓存。', locale))
    if any(not c.catalog.get('detail_loaded') for c in cards):
        notices.append(ui_label('部分技能资料未获取，未知分类不会当作无技能。', locale))
    footer_lines = lines(' '.join(notices), width-76, 23) if notices else ()
    detail_label = ui_label('按 ID 查详情', locale)
    # Keep translated footer labels separate even on a single-column page.
    position_width = font(23).getlength(ui_label('展示位置', locale)+f' {len(records)}–{len(records)} / {len(records)}')
    separate_footer = position_width+font(23).getlength(detail_label)+24 > width-76
    footer_h = 76+34*separate_footer+32*len(footer_lines)
    assets = {}
    def cached(url, size):
        return assets[url, size]
    requests = {(card.thumbnail_url, (288, 162)) for card in cards}
    requests.update((card.catalog['attribute_icon'], (40, 40)) for card in cards if card.catalog.get('attribute_icon'))
    requests.update((person['face'], (side, side)) for entries, _ in headings.values()
                    for person, _, _, side, _ in entries if person.get('face'))
    def paint(start, stop, height):
        c = Canvas(width, height)
        for i, line in enumerate(title):
            c.text(36, 48+i*56, line, 44, bold=True)
        for i, line in enumerate(subtitle):
            c.text(38, 101+(len(title)-1)*56+i*34, line, 25, MUTED)
        y, index = head, start
        while index < stop:
            key = rows[index][0]
            end = index+1
            while end < stop and rows[end][0] == key:
                end += 1
            group_height = sum(row[3] for row in rows[index:end])
            if index != start:
                c.line(32, y, width-32, '#e1dce7')
            entries, heading_height = headings[key]
            top = y+(group_height-heading_height)//2
            for person, cx, offset, side, wrapped in entries:
                if person.get('face'):
                    c.paste(cached(person['face'], (side, side)), cx-side//2, top+offset, side, side, True, radius=side//2)
                for n, (line, size) in enumerate(wrapped):
                    c.text(cx, top+offset+(108 if side == 80 else 76)+n*(34 if side == 80 else 22),
                           line, size, bold=True, align='center')
            for _, items, texts, row_height in rows[index:end]:
                for col, ((_, card), text) in enumerate(zip(items, texts)):
                    x = 260+col*348
                    card_face(c, card, x, y+14, 288, 162, cached)
                    c.text(x, y+196, f'ID {card.id}', 24, MUTED)
                    cy = y+230
                    for label, color, wrapped in text:
                        c.text(x, cy, ui_label(label, locale), 24, color, True)
                        for n, line in enumerate(wrapped):
                            c.text(x+30, cy+n*30, line, 24)
                        cy += len(wrapped)*30+8
                y += row_height
            index = end
        c.line(32, y, width-32, '#e1dce7')
        c.text(38, y+38, ui_label('展示位置', locale)+f' {rows[start][1][0][0]+1}–{rows[stop-1][1][-1][0]+1} / {len(records)}', 23, MUTED)
        c.text(width-38, y+38+34*separate_footer, detail_label, 23, MUTED, align='right')
        for i, line in enumerate(footer_lines):
            c.text(38, y+74+34*separate_footer+i*32, line, 23, MUTED)
        return c.image
    try:
        rarities = {2: 'R', 3: 'SR', 4: 'SSR', 20: 'BD', 10: 'EX'}
        badges = {f'RarityIconCenter_{rarities[c.rarity]}.png' for c in cards if c.rarity in rarities}
        assets.update(prepare_assets(requests, load, badges))
        parts = render_parts(tuple(row[3] for row in rows), width, head+footer_h, paint)
        ranges = tuple((rows[p.start][1][0][0], rows[p.stop-1][1][-1][0]+1) for p in parts)
        return parts, records, ranges
    finally:
        for asset in assets.values():
            if asset is not None:
                asset.close()


def render_list(cards,query,locale='zh',*,load,characters=(),bands=()):
    cards=ordered(cards)
    if not cards:return (),cards,()
    support=isinstance(cards[0],SupportCard)
    if support:
        return _render_memory_list(cards, query, locale, load)
    # Approved member-list growth V3 and SNAP-list V4, rendered at 2x scale.
    # The left person gutter and each attribute's own row are intentional.
    pitch,artw,arth,textw=(432,288,162,280) if support else (256,192,192,196)
    left=260 if support else 264
    durations=set();targets=set()
    for card in cards:
        for contract in card.catalog.get('compact_skills',[]):
            if not contract or contract.get('automatic'):continue
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
    group_people={}
    for card in cards:
        related=card.catalog.get('people',[])
        if support:
            if related:
                related=tuple(sorted(related,key=lambda p:(p.get('band') or 999,p.get('order') if p.get('order') is not None else 999,p['id'])))
                key=(0,tuple((p.get('band') or 999,p.get('order') if p.get('order') is not None else 999,p['id']) for p in related))
            else:
                names=sorted(set(card.characters or (card.character,)),key=lambda n:normalize(character_identity(n)))
                related=tuple({'name':{locale:name}} for name in names)
                key=(1,tuple(normalize(character_identity(name)) for name in names))
            groups[key].append(card)
            group_people[key]=related
        elif related:
            for p in related:
                if (not selected or p['id'] in selected) and (selected or not selected_bands or p.get('band') in selected_bands):
                    key=(p.get('band') or 999,p.get('order') if p.get('order') is not None else 999,p['id'])
                    groups[key].append(card)
        else:
            groups[person_key(card)].append(card)
    rows=[];records=[]
    for key,group in sorted(groups.items()):
        related=group_people[key] if support else (people.get(key[2],{'name':{locale:group[0].character}}),)
        headings,heading_height=group_headings(related,locale,group[0].character)
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
                content_height=(224 if support else 254)+sum(heights)+36
                if support:content_height=max(content_height,heading_height+16)
                rows.append(CardRow(items,headings,first,offsets,texts,content_height+(36 if first else 0)))
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
    for row in rows:
        for person,_,_,side,_,_ in row.headings:
            if person.get('face'):requests.add((person['face'],(side,side)))
    def paint(start,stop,height):
        c=Canvas(width,height)
        for n,line in enumerate(title_lines):c.text(56,72+n*96,line,80,bold=True)
        c.paragraph(56,176+title_extra,subtitle,width-112,42,52,color=MUTED)
        y=head
        for row_index,row in enumerate(rows[start:stop],start):
            if row.group_start:
                if row_index!=start:c.line(48,y,width-48,'#e1dce7')
                y+=36
            if row.group_start or row_index==start:
                for person,cx,top,side,size,wrapped in row.headings:
                    if person.get('face'):c.paste(cached(person['face'],(side,side)),cx-side//2,y+top+8,side,side,True,radius=side//2)
                    for n,line in enumerate(wrapped):
                        c.text(cx,y+top+(152 if side==96 else 120)+n*(44 if side==96 else 38),line,size,bold=True,align='center')
            for col,((index,card),text) in enumerate(zip(row.cards,row.texts)):
                x=left+col*pitch;card_face(c,card,x,y+8,artw,arth,cached)
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
