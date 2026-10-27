# L3
# Input: Captured detailed card, translated skill levels, validated artwork loader.
# Output: Complete, measured detail parts retaining all skill levels and conditions.
# Pos: Rendering / Card; approved 1080px details and shared header alignment.
# Effects: Serial preparation before Canvas; shared and candidate-page artwork ownership.
import re
from contextlib import ExitStack, contextmanager
from .labels import label as ui_label
from datetime import datetime, timedelta, timezone
from PIL import ImageOps
from ..data import SupportCard, localized_text
from .haneoka_theme import Canvas, MUTED, ACCENT, SOFT, SURFACE, frame, lines, prepared_render_assets
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
            if len(changed)==3 and all(word in descriptions[-1] for word in ('每','得分提升','最高','若为')):
                labels=('每次增幅','累计上限','乐队增幅')
            return [(labels[n],[p[i] for p in split]) for n,i in enumerate(changed)]
    return [('',descriptions)]


def render(card,locale='zh',*,load):
    support=isinstance(card,SupportCard)
    logos=card.catalog.get('band_logos',[])
    shared_requests={(card.full_url,(972,1458)),(card.catalog.get('attribute_icon'),(64,64))}
    if len(logos)==1:shared_requests.add((logos[0],(328,96)))
    shared_requests={key for key in shared_requests if key[0]}
    rarity={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(card.rarity)
    game_names=(f'RarityIconCenter_{rarity}.png',) if rarity else ()
    # The scope starts before the first load, including aspect-ratio measurement.
    with prepared_render_assets(shared_requests,load,game_names,synchronous=True) as shared, ExitStack() as owned:
        logo=shared.get((logos[0],(328,96))) if len(logos)==1 else None
        fitted=None
        if logo is not None:
            trimmed=logo.crop(logo.getbbox()) if logo.mode=='RGBA' and logo.getbbox() else logo.copy()
            owned.callback(trimmed.close)
            fitted=ImageOps.contain(trimmed,(196,60) if support and len(card.catalog.get('people',[]))>1 else (164,48))
            owned.callback(fitted.close)
        return _render(card,locale,load,shared,game_names,fitted)


def _render(card,locale,load,shared,game_names,fitted_logo):
    support=isinstance(card,SupportCard);assets=shared
    def art(url,size):
        return assets[(url,size)] if url else None
    title=localized_text(card,'title',locale);titlelines=lines(title,820,44,True)
    identity=localized_text(card,'character',locale)
    logos=card.catalog.get('band_logos',[])
    header_h=max(152,82+len(titlelines)*56)
    blocks=[]
    picture=art(card.full_url,(972,1458))
    ah=round(972*picture.height/picture.width) if picture else (547 if support else 1296)
    blocks.append(('art',ah+34,None))
    stats=tuple(v/100 if support else v for v in (card.performance,card.technic,card.visual))
    if card.catalog.get('source') != 'haneoka' and not any(stats):
        stats = None
    people=sorted(card.catalog.get('people',[]),key=lambda p:(p.get('band') or 999,p.get('order',999),p['id']))
    if support and len(people)>1:
        blocks.append(('people',126,people))
    blocks.append(('stats',256 if support else 318,stats))
    levels=card.catalog.get('skill_levels',[])
    for index,skill in sorted(enumerate(card.skills),key=lambda pair:{'liveSkill':0,'gekisouSkill':1,'leaderSkill':2}.get(pair[1].kind,0)):
        desc=localized_text(skill,'description',locale)
        desc=re.sub(r'\d+\.\d+',lambda m:f'{float(m.group()):g}',desc)
        if locale=='zh':
            desc=desc.replace('装配此技能的成员的演出技能发动时间延长','演出技能延长').replace('若为「','若为').replace('」成员，','成员，').replace('」 成员','成员').replace('则演出技能发动时间延长','改为延长')
        if locale=='zh':
            desc=desc.replace('COMBO激奏期间\n激奏COMBO获得量提升','COMBO 激奏期间，激奏 COMBO 获得量 +')
            desc=re.sub(r'若为(.+?)」?\s*成员[，,]?\s*(?:则)?',r'\1 成员装配时，',desc)
        body=lines(desc,972,36 if support else 38)
        rows=[]
        if index<len(levels):
            descriptions=[v.get(locale) or v.get('ja') or ui_label('未知',locale) for v in levels[index]]
            contracts=card.catalog.get('compact_skills',[])
            contract=contracts[index] if index<len(contracts) else None
            if contract and contract.get('automatic'):contract=None
            brief=[contract['levels'].get(str(lv)) for lv in range(1,6)] if contract else []
            if brief and all(brief):
                brief=[v['short'] if isinstance(v,dict) else v for v in brief]
                # Full translated effects supply units and conditions; the short form
                # is used only for the audited whole-fever duration variant.
            extracted=table_rows(descriptions)
            if brief and all('COMBO获取' in v for v in brief):
                extracted=[('',[re.search(r'开场(.+)',v).group(1) if '开场' in v else '整个激奏' for v in brief])]
            for label,values in extracted:
                values=[re.sub(r'\d+\.\d+',lambda m:f'{float(m.group()):g}',v) for v in values]
                values=[ui_label(v,locale) if v=='整个激奏' else re.sub(r'([0-9.]+)秒',r'\1s',v) if locale=='en' else v for v in values]
                if len(extracted)==1 and label.startswith('效果'):
                    last=contract['levels'].get('5') if contract and locale=='zh' else None
                    label={'演出延时':'延长时间','LIFE回复':'回复量'}.get(last.get('family'),'') if isinstance(last,dict) else ''
                    if isinstance(last,str) and skill.kind=='leaderSkill' and '；' in last:
                        condition=re.match(r'(.*?)\s*(?:表演|技巧|形象|全参数)\+',last)
                        if condition:label=condition[1].replace('红属性','绯红成员').strip()
                if label=='基础效果':label='通常'
                if label=='条件成立' and contract and locale=='zh':
                    last=contract['levels']['5'];label=last.get('enhanced_target','条件成立') if isinstance(last,dict) else label
                if label.startswith('效果 '):label=label.replace('效果',ui_label('效果',locale),1)
                wrapped=[lines(v,150 if label else 182,32 if support else 36) for v in values]
                label=ui_label(label,locale)
                row_height=max(max(map(len,wrapped))*50,len(lines(label,150,28))*36+6 if label else 0)+2
                rows.append((label,wrapped,row_height))
        name=localized_text(skill,'name',locale)
        contracts=card.catalog.get('compact_skills',[])
        contract=contracts[index] if index<len(contracts) else None
        if contract and contract.get('automatic'):contract=None
        last=contract['levels'].get('5') if contract else None
        if locale=='zh' and isinstance(last,dict):name=last['family']
        if locale=='zh' and isinstance(last,str) and skill.kind=='leaderSkill':
            params=re.findall(r'(表演|技巧|形象|全参数)\+',last)
            if params and len(set(params))==1:
                name=params[0]+'值 UP' if params[0]!='全参数' else '全参数 UP'
                desc=last.replace('红属性','绯红成员').replace(params[0]+'+',' +').replace('；','； ')+'。'
                body=lines(desc,972,38)
        if locale=='zh' and last and skill.kind=='gekisouSkill' and 'COMBO获取' in str(last):name='COMBO'
        heading=ui_label(NAMES.get(skill.kind,'其他技能').replace('激奏支援技能','激奏支援'),locale)+' · '+name
        hh=len(lines(heading,890,40,True))*52
        height=hh+len(body)*50+94+(56+sum(r[2] for r in rows) if rows else 0)
        blocks.append(('skill',height,(index,heading,body,rows,hh)))
    if not card.skills:blocks.append(('unknown',100,None))
    for relation in card.catalog.get('relations',[]):
        name=relation.get('name',{}).get(locale) or relation.get('name',{}).get('ja') or ui_label('未知',locale)
        rh=max(364,100+len(lines(name,528,40,True))*52+160)
        blocks.append(('related',rh,(relation,name)))
    head=160+header_h+24
    def paint(start,stop,height):
        c=Canvas(1080,height)
        try:
            c.header(ui_label('留影卡详情',locale) if support else ui_label('角色卡详情',locale),f"/{'查留影卡' if support else '查卡'} {card.id}")
            c.box((30,160,1050,160+header_h),SURFACE,24)
            for i,line in enumerate(titlelines):c.text(54,208+i*56,line,44,bold=True)
            cy=160+header_h-45
            if len(logos)==1:
                logo_width=fitted_logo.width if fitted_logo is not None else 164
                if fitted_logo is not None:
                    c.paste(fitted_logo,54,round(cy-fitted_logo.height/2),fitted_logo.width,fitted_logo.height)
                else:c.paste(None,54,cy-24,164,48)
                if not support or len(people)==1:c.paragraph(54+logo_width+14,cy-22,identity,806-logo_width-14,34,44,color=MUTED)
            else:c.text(54,cy,ui_label('跨乐队 · {n} 位成员',locale).format(n=len(card.catalog.get('people',[]))) if support else identity,30,MUTED)
            attr=card.catalog.get('attribute_icon')
            if attr:c.paste(art(attr,(64,64)),962,176,64,64,True,trim=True)
            c.text(1026,cy,f'ID {card.id}',30,ACCENT,align='right')
            y=head
            visible=blocks[start:stop]
            info=[i for i,b in enumerate(visible) if b[0] in ('stats','skill','unknown')]
            if info:
                top=head+sum(b[1] for b in visible[:info[0]])
                bottom=head+sum(b[1] for b in visible[:info[-1]+1])
                c.box((30,top,1050,bottom),'white',24)
            for kind,bh,data in visible:
                if kind=='art':
                    border=frame(972,ah,card.rarity);c.image.paste(border,(50,y-4),border);c.paste(picture,54,y,972,ah,True,radius=16)
                    c.rarity_badge(card.rarity,1002 if support else 1006,y+ah-10 if support else y+ah-12,100 if support else 126,70 if support else 92)
                elif kind=='stats':
                    c.text(54,y+48,ui_label('属性加成',locale) if support else ui_label('综合力',locale),40,bold=True)
                    if isinstance(data,(list,tuple)) and len(data)==3:

                        if not support:c.text(1026,y+48,f'{sum(data):,}',48,bold=True,align='right')
                        if support:c.text(1026,y+48,ui_label('满级基础值',locale),32,MUTED,align='right')
                        else:c.text(54,y+108,ui_label('满级基础值',locale),34,MUTED)
                        for i,(name,value,color) in enumerate(zip((ui_label('表演值',locale),ui_label('技巧值',locale),ui_label('形象值',locale)),data,('#d2518b','#248ba6','#b97913'))):
                            cy=y+(104 if support else 165)+i*54;c.text(54,cy,name,36,color);right=900 if support else 850;c.box((194,cy-11,right,cy+11),'#eeebf0',11)
                            # Fixed reference scales preserve comparison across cards.
                            scale=45 if support else 44000
                            if value:c.box((194,cy-11,194+max(1,round((right-194)*min(value/scale,1))),cy+11),color,11)
                            c.text(1026,cy,f'{value:g}%' if support else f'{value:,}',38,align='right')
                    else:c.text(54,y+112,ui_label('基础数值未确认',locale),30,MUTED)
                elif kind=='people':
                    for i,p in enumerate(data):
                        cx=54+(i+.5)*972/len(data);c.paste(art(p.get('face'),(72,72)),round(cx-36),y-12,72,72,True,radius=36)
                        name=p.get('name',{}).get(locale) or ui_label('未知',locale)
                        if p.get('band')==2:name=name.rsplit(' / ',1)[-1]
                        c.text(cx,y+84,name,28,align='center')
                elif kind=='skill':
                    index,heading,body,rows,hh=data;c.line(54,y,1026)
                    icons=card.catalog.get('skill_icons',[])
                    if index<len(icons) and icons[index]:c.paste(art(icons[index],(56,56)),54,y+24,56,56,True,trim=True)
                    c.paragraph(130 if index<len(icons) and icons[index] else 54,y+22,heading,890,40,52,bold=True)
                    ty=y+hh+36
                    for line in body:c.text(54,ty+25,line,36 if support else 38);ty+=50
                    if rows:
                        ty+=16;c.box((54,ty,1026,ty+56+sum(r[2] for r in rows)),'#f6f3f8',12)
                        stub=240 if any(r[0] for r in rows) else 54;cw=(1026-stub)/5
                        c.box((round(stub+4*cw),ty,1026,ty+56+sum(r[2] for r in rows)),SOFT,12)
                        for k in range(5):c.text(stub+(k+.5)*cw,ty+28,f'Lv.{k+1}',30 if support else 32,MUTED,align='center')
                        ty+=56
                        for label,values,rh in rows:
                            offset=0
                            if label:c.paragraph(70,ty+6,ui_label(label,locale),150,28,36,color=MUTED)
                            for k,wrapped in enumerate(values):
                                for n,line in enumerate(wrapped):c.text(stub+(k+.5)*cw,ty+offset+24+n*50,line,32 if support else 36,ACCENT if k==4 else '#1b1b1f',k==4,'center')
                            ty+=rh
                elif kind=='related':
                    relation,name=data;c.line(54,y,1026);c.text(54,y+40,ui_label(relation['kind'],locale),38,bold=True)
                    c.text(1026,y+40,'ID '+str(relation['id']),34,MUTED,align='right')
                    c.paste(art(relation['image'],(420,180)),54,y+84,420,180,False,radius=10)
                    c.paragraph(498,y+90,name,528,40,52,bold=True)
                    def date(value):
                        try:
                            dt=datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=9)))
                            return dt.strftime('%m/%d %H:%M') if dt.year>1970 else ui_label('未获取',locale)
                        except (ValueError,TypeError):return ui_label('未获取',locale)
                    c.text(498,y+max(218,90+len(lines(name,528,40))*52+24),ui_label('活动加成卡',locale) if relation['kind']=='相关活动' else 'Pick Up',32,ACCENT)
                    c.text(54,y+bh-40,date(relation['start'])+' — '+date(relation['end'])+'  (UTC+09:00)',32,MUTED)
                else:c.text(54,y+40,ui_label('技能详情未获取',locale),32,MUTED)
                y+=bh
            if card.catalog.get('detail_stale'):c.text(54,height-24,ui_label('使用上次有效资料',locale),22,MUTED)
            return c.image
        except BaseException:
            c.image.close()
            raise
    @contextmanager
    def page_scope(start,max_stop):
        nonlocal assets
        requests=set()
        for kind,_,data in blocks[start:max_stop]:
            if kind=='people':
                requests.update((person.get('face'),(72,72)) for person in data)
            elif kind=='skill':
                icons=card.catalog.get('skill_icons',[])
                if data[0]<len(icons):requests.add((icons[data[0]],(56,56)))
            elif kind=='related':
                requests.add((data[0]['image'],(420,180)))
        requests={key for key in requests if key[0] and key not in shared}
        # This nested source reads the outer captured bytes, never the repository.
        with prepared_render_assets(requests,load,game_names,synchronous=True) as page:
            assets={**shared,**page}
            try:yield
            finally:assets=shared
    return render_parts(tuple(b[1] for b in blocks),1080,head+48,paint,page_scope=page_scope)
