# L3
# Input: Captured detailed card, translated skill levels, validated artwork loader.
# Output: Complete, measured detail parts retaining all skill levels and conditions.
# Pos: Rendering / Card; approved 1080px details and shared header alignment.
# Effects: Pillow and injected artwork I/O only.
import re
from ..data import SupportCard, localized_text
from .haneoka_theme import Canvas, MUTED, ACCENT, SOFT, SURFACE, frame, lines
from .readable_pages import render_parts

NAMES={'leaderSkill':'队长技能','liveSkill':'演出技能','gekisouSkill':'激奏技能',
       'supportSkill':'支援技能','gekisouSupportSkill':'激奏支援技能'}


def table_rows(descriptions):
    # Keep the full Lv.5 explanation above the table. Only extract numeric
    # columns when every level has exactly the same surrounding wording.
    pattern=r'([+-]?\d+(?:\.\d+)?(?:%|秒)?)'
    split=[re.split(pattern,d) for d in descriptions]
    shapes=[p[::2] for p in split]
    if all(shape==shapes[0] for shape in shapes) and len(split[0])>1:
        changed=[i for i in range(1,len(split[0]),2) if len({p[i] for p in split})>1]
        if changed:
            conditional = len(changed)==2 and ('若' in descriptions[-1] or '場合' in descriptions[-1])
            labels = ('基础效果','条件成立') if conditional else tuple(f'效果 {n+1}' for n in range(len(changed)))
            return [(labels[n],[p[i] for p in split]) for n,i in enumerate(changed)]
    return [('',descriptions)]


def render(card,locale='zh',*,load):
    support=isinstance(card,SupportCard);assets={}
    def art(url,size):
        if not url:return None
        key=(url,size)
        if key not in assets:assets[key]=load(url,size)
        return assets[key]
    title=localized_text(card,'title',locale);titlelines=lines(title,820,44,True)
    identity=localized_text(card,'character',locale)
    logos=card.catalog.get('band_logos',[])
    header_h=max(152,82+len(titlelines)*56)
    blocks=[]
    picture=art(card.full_url,(972,1458))
    ah=round(972*picture.height/picture.width) if picture else (547 if support else 1296)
    blocks.append(('art',ah+34,None))
    stats=card.catalog.get('stats_level1')
    blocks.append(('stats',300 if stats else 140,stats))
    if support and card.catalog.get('people'):
        blocks.append(('people',100+max(len(lines(p.get('name',{}).get(locale) or '未知',150,24)) for p in card.catalog['people'])*32,card.catalog['people']))
    if not support:
        tags=card.catalog.get('tags',{})
        tagtext=' / '.join(names.get(locale) or names.get('ja') or f'ID {key}（名称未获取）' for key,names in tags.items()) or '未获取'
        blocks.append(('tags',68+len(lines(tagtext,780,28))*40,tagtext))
    levels=card.catalog.get('skill_levels',[])
    for index,skill in sorted(enumerate(card.skills),key=lambda pair:{'liveSkill':0,'gekisouSkill':1,'leaderSkill':2}.get(pair[1].kind,0)):
        desc=localized_text(skill,'description',locale)
        body=lines(desc,972,32)
        rows=[]
        if index<len(levels):
            descriptions=[v.get(locale) or v.get('ja') or '未知' for v in levels[index]]
            for label,values in table_rows(descriptions):
                wrapped=[lines(v,176,26) for v in values]
                rows.append((label,wrapped,max(map(len,wrapped))*36+12+(36 if label else 0)))
        heading=NAMES.get(skill.kind,'其他技能')+' · '+localized_text(skill,'name',locale)
        hh=len(lines(heading,890,36,True))*48
        height=hh+len(body)*44+70+(56+sum(r[2] for r in rows) if rows else 0)
        blocks.append(('skill',height,(index,heading,body,rows,hh)))
    if not card.skills:blocks.append(('unknown',100,None))
    # Alternative names are retained, but the rejected release-date/server/rarity
    # facts block is deliberately absent.
    alternate=[v for k,v in card.localized.get('title',{}).items() if v and v!=title]
    alternate=list(dict.fromkeys(alternate))
    if alternate:blocks.append(('names',70+sum(len(lines(v,972,28))*40 for v in alternate),alternate))
    for relation in card.catalog.get('relations',[]):
        name=relation.get('name',{}).get(locale) or relation.get('name',{}).get('ja') or '未知'
        rh=max(310,100+len(lines(name,528,36,True))*48+110)
        blocks.append(('related',rh,(relation,name)))
    head=160+header_h+24
    def paint(start,stop,height):
        c=Canvas(1080,height);c.header('留影卡详情' if support else '角色卡详情',f"/{'查留影卡' if support else '查卡'} {card.id}")
        c.box((30,160,1050,160+header_h),SURFACE,24)
        for i,line in enumerate(titlelines):c.text(54,208+i*56,line,44,bold=True)
        cy=160+header_h-45
        if len(logos)==1:
            c.paste(art(logos[0],(140,56)),54,cy-28,140,56,True)
            if not support:c.paragraph(218,cy-22,identity,640,30,40,color=MUTED)
        else:c.text(54,cy,f'跨乐队 · {len(card.catalog.get("people",[]))} 位成员' if support else identity,30,MUTED)
        attr=card.catalog.get('attribute_icon')
        if attr:c.paste(art(attr,(64,64)),950,176,64,64,True)
        c.text(1014,cy,f'ID {card.id}',30,ACCENT,align='right')
        y=head
        for kind,bh,data in blocks[start:stop]:
            if kind=='art':
                border=frame(972,ah,card.rarity);c.image.paste(border,(50,y-4),border);c.paste(picture,54,y,972,ah,True)
                rarity={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(card.rarity)
                badge=f'RarityIconCenter_{rarity}.png'
                if rarity:c.badge(badge,880,y+ah-55,126,92)
            elif kind=='stats':
                c.line(54,y+4,1026);c.text(54,y+48,'属性加成' if support else '综合力',40,bold=True)
                if isinstance(data,(list,tuple)) and len(data)==3:

                    if not support:c.text(1026,y+48,f'{sum(data):,}',48,bold=True,align='right')
                    c.text(54,y+102,'Lv.1 · Rank 1 · 基础数值',28,MUTED)
                    for i,(name,value,color) in enumerate(zip(('表演','技巧','形象'),data,('#d2518b','#248ba6','#b97913'))):
                        cy=y+157+i*54;c.text(54,cy,name,32,color);c.box((194,cy-10,830,cy+10),SURFACE,10)
                        if value:c.box((194,cy-10,194+max(1,round(636*value/max(data or [1]))),cy+10),color,10)
                        c.text(1026,cy,f'{value:g}%' if support else f'{value:,}',34,align='right')
                else:c.text(54,y+112,'基础数值未确认',30,MUTED)
            elif kind=='tags':
                c.text(54,y+28,'得意乐曲',30,bold=True);c.paragraph(230,y+8,data,780,28,40,color=MUTED)
            elif kind=='people':
                for i,p in enumerate(data):
                    x=54+i*190;c.paste(art(p.get('face'),(60,60)),x,y,60,60,True)
                    c.paragraph(x,y+70,p.get('name',{}).get(locale) or '未知',150,24,32)
            elif kind=='skill':
                index,heading,body,rows,hh=data;c.line(54,y,1026)
                icons=card.catalog.get('skill_icons',[])
                if index<len(icons) and icons[index]:c.paste(art(icons[index],(56,56)),54,y+20,56,56,True)
                c.paragraph(130 if index<len(icons) and icons[index] else 54,y+20,heading,890,36,48,bold=True)
                ty=y+hh+36
                for line in body:c.text(54,ty+22,line,32);ty+=44
                if rows:
                    ty+=16;c.box((54,ty,1026,ty+56+sum(r[2] for r in rows)),'#f6f3f8',12)
                    c.box((832,ty,1026,ty+56+sum(r[2] for r in rows)),SOFT,12)
                    for k in range(5):c.text(54+(k+.5)*194.4,ty+28,f'Lv.{k+1}',28,MUTED,align='center')
                    ty+=56
                    for label,values,rh in rows:
                        offset=36 if label else 0
                        if label:c.text(66,ty+18,label,22,MUTED)
                        for k,wrapped in enumerate(values):
                            for n,line in enumerate(wrapped):c.text(54+(k+.5)*194.4,ty+offset+18+n*36,line,26,ACCENT if k==4 else '#1b1b1f',k==4,'center')
                        ty+=rh
            elif kind=='related':
                relation,name=data;c.line(54,y,1026);c.text(54,y+40,relation['kind'],38,bold=True)
                c.text(1026,y+40,'ID '+str(relation['id']),28,MUTED,align='right')
                c.paste(art(relation['image'],(420,180)),54,y+84,420,180,True)
                c.paragraph(498,y+90,name,528,36,48,bold=True)
                c.text(54,y+bh-24,(relation['start']+' — '+relation['end']).replace('T',' ').replace(':00+09:00',' JST').replace(':00+00:00',' UTC'),24,MUTED)
            elif kind=='names':
                c.line(54,y,1026);c.text(54,y+34,'其他名称',30,bold=True);ty=y+64
                for value in data:ty=c.paragraph(54,ty,value,972,28,40)
            else:c.text(54,y+40,'技能详情未获取',32,MUTED)
            y+=bh
        if card.catalog.get('detail_stale'):c.text(54,height-24,'使用上次有效资料',22,MUTED)
        return c.image
    try:return render_parts(tuple(b[1] for b in blocks),1080,head+48,paint)
    finally:
        for asset in assets.values():
            if asset is not None:asset.close()
