# L3
# Input: Captured activity/gacha views, schedules and typed related card rows.
# Output: Vertical entries with banner/time summaries and compact native-ratio cards.
# Pos: Rendering / Information; Haneoka supplies art, the user's sample supplies composition.
# Effects: Pillow drawing only; no queries, network, accounts or platform calls.
"""Vertical information entries and compact card strips from the supplied sample."""
from dataclasses import dataclass, replace

from PIL import ImageOps

from ..query.contracts.info import row_text
from .haneoka_theme import ACCENT, INK, MUTED, SOFT, SURFACE, frame, lines
from .image_output import ImageBudgetError

LEFT, RIGHT, GAP = 54, 1026, 20
WIDTH = RIGHT - LEFT
OVERVIEW_WIDTH = 474
# Only card titles and character names are shown beneath the artwork.
CAPTION_STYLES = ((22, 32, INK, True), (20, 28, MUTED, False))


@dataclass(frozen=True)
class Panel:
    height: int
    kind: str
    text: str = ''
    rows: tuple = ()
    title: str = ''
    body: tuple[str, ...] = ()
    requests: tuple = ()
    badges: tuple[str, ...] = ()
    children: tuple = ()


def _card_size(row):
    # Released member thumbnails are 384x512; SNAP thumbnails are 512x288.
    return (228, 128) if row.image_kind == 'wide' else (144, 192)


def _caption_width(row):
    return 228 if row.image_kind == 'wide' else 176


def _name_lines(title, width, size=22, bold=True):
    wrapped = lines(title, width, size, bold) if title else ()
    # Avoid leaving a lone final character while preserving every name glyph.
    if len(wrapped)>1 and len(wrapped[-1].strip())==1 and len(wrapped[-2])>2 and '\n' not in title:
        count=2 if wrapped[-2][-1] in '，。、；：！？）】」』〉》)]}%' else 1
        wrapped=(*wrapped[:-2],wrapped[-2][:-count],wrapped[-2][-count:]+wrapped[-1])
    return wrapped


def _captions(row):
    width = _caption_width(row)
    title = _name_lines(row.display_title, width)
    people = tuple(line for value in row.body for line in _name_lines(value, width, 20, False))
    return title, people


def _caption_heights(rows):
    captions = tuple(_captions(row) for row in rows)
    return tuple(max(len(caption[i]) for caption in captions) * style[1]
                 for i, style in enumerate(CAPTION_STYLES))


def _card_panels(rows, presentation):
    result=[]
    for kind in dict.fromkeys(row.group for row in rows):
        group=tuple(row for row in rows if row.group==kind)
        columns=(WIDTH+GAP)//(_caption_width(group[0])+GAP)
        for start in range(0,len(group),columns):
            chunk=group[start:start+columns]
            title=('UP · ' if presentation=='gacha' else '关联')+kind+f' · {len(group)}'
            if start:title+='（续）'
            height=62+max(_card_size(r)[1] for r in chunk)+16
            height+=sum(_caption_heights(chunk))+20
            requests=tuple((r.image_url,_card_size(r)) for r in chunk if r.image_url)
            requests+=tuple((r.attribute_url,(28,28)) for r in chunk if r.attribute_url)
            badges=tuple(f'RarityIconCenter_{name}.png' for r in chunk
                         if (name:={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(r.rarity)))
            result.append(Panel(height,'cards','\n'.join(row_text(r) for r in chunk),chunk,title,
                                requests=requests,badges=badges))
    return result


def _entry_panels(row, limit, presentation):
    if row.schedule is None:raise ValueError('Banner row requires captured schedule facts')
    title_height=len(lines(row.title,648,30,True))*42
    header=Panel(max(129,title_height+108)+24,'entry-head',row_text(replace(row,children=())),
                 rows=(row,),requests=((row.image_url,(300,129)),) if row.image_url else ())
    parts=_card_panels(tuple(r for r in row.children if r.kind=='card'),presentation)
    for child in row.children:
        if child.kind=='card':continue
        wrapped=tuple(line for value in (child.title,*child.body) for line in lines(value,WIDTH,22))
        parts.append(Panel(len(wrapped)*32+20,'copy',row_text(child),body=wrapped))
    # Keep a complete entry together when it fits. Large entries continue at a
    # complete card strip with a repeated visual identity, never duplicate text.
    content_limit=limit-20
    target=min(content_limit,2400);result=[];chunk=[header];height=header.height
    for part in parts:
        if height+part.height>target and len(chunk)>1:
            result.append(_entry(chunk))
            chunk=[replace(header,text='',title='（续）')];height=header.height
        if height+part.height>content_limit:
            raise ImageBudgetError('One information entry strip exceeds the readable page budget')
        chunk.append(part);height+=part.height
    result.append(_entry(chunk))
    return result


def _entry(parts):
    return Panel(sum(p.height for p in parts)+20,'entry','\n'.join(p.text for p in parts if p.text),
                 requests=tuple(request for p in parts for request in p.requests),
                 badges=tuple(name for p in parts for name in p.badges),children=tuple(parts))


def _gallery_panels(row,limit):
    result=[]
    for start in range(0,len(row.children),4):
        children=row.children[start:start+4]
        captions=tuple((lines(r.title,224,24,True),
                        tuple(line for value in r.body for line in lines(value,224,24))) for r in children)
        art_h=max(192 if r.image_kind=='portrait' else 128 if r.image_kind=='wide' else 96 for r in children)
        caption_h=max(len(a)*34+len(b)*34 for a,b in captions)
        shared=tuple(line for value in row.body for line in lines(value,WIDTH,28))
        height=64+art_h+16+caption_h+len(shared)*40+28
        if height>limit:raise ImageBudgetError('Activity gallery item exceeds readable budget')
        result.append(Panel(height,'gallery',row_text(replace(row,children=children)),rows=children,
            title=row.title+('（续）' if start else ''),body=shared,children=captions,
            requests=tuple((r.image_url,_card_size(r) if r.image_kind!='square' else (96,96)) for r in children if r.image_url)))
    return result


def _activity_overview(view,limit,prose_blocks):
    result=[]
    song_sections=set()
    for row in view.rows:
        if row.kind=='schedule':
            if row.schedule is None:raise ValueError('Activity overview requires schedule')
            result.append(Panel(584 if view.hero_url else 184,'event-banner',row_text(row),rows=(row,),
                requests=((view.hero_url,(924,396)),) if view.hero_url else ()))
        elif row.kind=='gallery':result.extend(_gallery_panels(row,limit))
        elif row.kind=='recruitment':
            copy=tuple(line for value in (row.title,*row.body) for line in lines(value,WIDTH,28))
            result.append(Panel(460+len(copy)*40,'recruitment',row_text(row),rows=(row,),
                title='关联卡池',body=copy,requests=((row.image_url,(924,396)),) if row.image_url else ()))
        elif row.kind=='card':result.extend(_card_panels((row,),'activity'))
        elif row.kind=='song':
            heading=row.label or '关联歌曲'
            if heading not in song_sections:
                result.append(Panel(60,'section-title',title=heading));song_sections.add(heading)
            result.extend(prose_blocks(row,limit,show_command=False))
        else:result.extend(prose_blocks(row,limit,show_command=False))
    for note in view.footer if view.visual_footer is None else view.visual_footer:
        wrapped=lines(note,WIDTH,22)
        for start in range(0,len(wrapped),max(1,(limit-32)//32)):
            chunk=wrapped[start:start+max(1,(limit-32)//32)]
            result.append(Panel(32+len(chunk)*32,'footer',note if start==0 else '',body=chunk))
    if any(block.height>limit for block in result):raise ImageBudgetError('Activity overview section exceeds budget')
    return tuple(result)


def blocks(view, limit, prose_blocks):
    if view.layout=='activity-overview':return _activity_overview(view,limit,prose_blocks)
    result=[];rows=view.rows
    for row in rows:
        if row.kind!='schedule':continue
        if row.schedule is None:raise ValueError('Schedule row requires captured schedule facts')
        size=(OVERVIEW_WIDTH,356) if view.hero_kind=='event' else (420,180)
        requests=((view.hero_url,size),) if view.hero_url else ()
        if view.hero_kind=='event' and view.hero_logo_url:requests+=((view.hero_logo_url,(210,110)),)
        result.append(Panel(max(size[1],286)+28 if view.hero_url else 314,'overview',
                            row_text(row),rows=(row,),requests=requests))
    for row in rows:
        if row.kind=='banner':result.extend(_entry_panels(row,limit,view.presentation))
    for row in rows:
        if row.kind not in {'schedule','banner','card','song'}:result.extend(prose_blocks(row,limit,show_command=False))
    result.extend(_card_panels(tuple(r for r in rows if r.kind=='card'),view.presentation))
    for row in rows:
        if row.kind=='song':result.extend(prose_blocks(row,limit,show_command=False))
    notes=view.footer if view.visual_footer is None else view.visual_footer
    wrapped=tuple(line for value in notes for line in lines(value,WIDTH,22))
    capacity=max(1,(limit-32)//32)
    for start in range(0,max(1,len(wrapped)),capacity):
        chunk=wrapped[start:start+capacity]
        result.append(Panel(32+len(chunk)*32,'footer','\n'.join(view.footer) if start==0 else '',body=chunk))
    # The final source note belongs with the last list entry, never on a page
    # of its own after shorter captions let one more entry fit the prior page.
    if len(result)>1 and result[-1].kind=='footer' and result[-2].kind=='entry':
        joined=_entry((*result[-2].children,result[-1]))
        if joined.height<=limit:result[-2:]=[joined]
    if any(p.height>limit for p in result):
        raise ImageBudgetError('One activity/recruitment section exceeds the readable page budget')
    return tuple(result)


def _status(c,value,x,y,right,size=24):
    fill,color={'时间窗内':('#e0e9e1','#396848'),'进行中':('#e0e9e1','#396848'),
                '尚未开始':(SOFT,ACCENT)}.get(value,(SURFACE,MUTED))
    width=min(right-x,max(104,len(value)*size+32))
    c.box((x,y,x+width,y+36),fill,18)
    c.text(x+width/2,y+18,value,size,color,True,'center')


def _card_art(c,row,x,y,load):
    width,height=_card_size(row);picture=load(row.image_url,(width,height))
    fitted=ImageOps.contain(picture,(width,height)) if picture is not None else None
    try:
        # The border and all marks follow the fitted art, including a source
        # with a different ratio. Letterbox space is never a filled card frame.
        w,h=fitted.size if fitted is not None else (width,height)
        top=y+height-h
        if row.rarity is not None:
            border=frame(w,h,row.rarity);c.image.paste(border,(x-4,top-4),border)
        c.paste(fitted,x,top,w,h,True,radius=8)
        if row.rarity is not None:c.rarity_badge(row.rarity,x+w-5,top+h-5,48,35)
        if row.attribute_url:c.paste(load(row.attribute_url,(28,28)),x+2,top+2,28,28,True)
    finally:
        if fitted is not None:fitted.close()


def draw(c,panel,y,load):
    if panel.kind=='section-title':
        width=max(160,len(panel.title)*28+32)
        c.box((LEFT,y+10,LEFT+width,y+50),ACCENT,10)
        c.text(LEFT+16,y+30,panel.title,26,'#ffffff',True)
        return
    if panel.kind in {'gallery','event-banner','recruitment'}:
        c.line(LEFT,y,RIGHT,'#d9d4df')
        if panel.kind=='event-banner':
            offset=0
            if panel.requests:
                url,size=panel.requests[0];c.paste(load(url,size),LEFT+24,y+12,*size,True,radius=10);offset=408
            schedule=panel.rows[0].schedule
            c.text(LEFT,y+offset+28,schedule.identity,26,MUTED)
            _status(c,schedule.status,RIGHT-max(120,len(schedule.status)*24+32),y+offset+10,RIGHT)
            c.text(LEFT,y+offset+76,'开始  '+schedule.start,28)
            c.text(LEFT,y+offset+116,'结束  '+schedule.end,28)
            c.text(LEFT,y+offset+153,schedule.zone,22,MUTED)
        elif panel.kind=='recruitment':
            c.box((30,y-4,1050,y+16),'#fcf8fd',0)
            c.box((LEFT,y+12,LEFT+164,y+50),ACCENT,10)
            c.text(LEFT+16,y+31,panel.title,26,'#ffffff',True)
            c.paste(load(panel.rows[0].image_url,(924,396)),LEFT+24,y+56,924,396,True,radius=10)
            for i,value in enumerate(panel.body):c.text(LEFT,y+476+i*40,value,28)
        else:
            c.text(LEFT,y+28,panel.title,28,ACCENT,True)
            art_h=max(192 if r.image_kind=='portrait' else 128 if r.image_kind=='wide' else 96 for r in panel.rows)
            caption_h=0
            for i,(row,(titles,body)) in enumerate(zip(panel.rows,panel.children)):
                x=LEFT+i*244;w,h=_card_size(row) if row.image_kind!='square' else (96,96)
                c.paste(load(row.image_url,(w,h)),x,y+64+art_h-h,w,h,True,radius=6)
                cy=y+64+art_h+16
                for value in titles:c.text(x,cy+17,value,24,INK,True);cy+=34
                for value in body:c.text(x,cy+17,value,24,MUTED);cy+=34
                caption_h=max(caption_h,cy-y)
            cy=y+caption_h+8
            for value in panel.body:c.text(LEFT,cy+20,value,28);cy+=40
        return
    if panel.kind=='entry':
        c.line(LEFT,y,RIGHT,'#d9d4df')
        cy=y+12
        for child in panel.children:draw(c,child,cy,load);cy+=child.height
    elif panel.kind=='entry-head':
        row=panel.rows[0];schedule=row.schedule
        c.paste(load(row.image_url,(300,129)),LEFT,y+8,300,129,True,radius=10)
        x=LEFT+324;cy=y+20
        for value in lines(row.title+panel.title,648,30,True):c.text(x,cy,value,30,INK,True);cy+=42
        c.text(x,cy+4,schedule.identity,22,MUTED)
        _status(c,schedule.status,RIGHT-max(120,len(schedule.status)*22+32),cy-14,RIGHT,22)
        c.text(x,cy+42,'开始 '+schedule.start,24,MUTED)
        c.text(x,cy+76,'结束 '+schedule.end,24,MUTED)
    elif panel.kind=='overview':
        schedule=panel.rows[0].schedule;x=LEFT
        if panel.requests:
            url,(w,h)=panel.requests[0]
            c.paste(load(url,(w,h)),LEFT+(OVERVIEW_WIDTH-w)//2,y+(panel.height-28-h)//2,w,h,True,radius=16)
            if len(panel.requests)>1:
                logo,size=panel.requests[1];c.paste(load(logo,size),LEFT+w-size[0]-12,y+(h-size[1])//2,*size,True)
            x+=OVERVIEW_WIDTH+24
        c.text(x,y+20,schedule.identity,24,MUTED);_status(c,schedule.status,x,y+50,RIGHT)
        for cy,label,value in ((y+125,'开始',schedule.start),(y+201,'结束',schedule.end)):
            c.text(x,cy,label,22,MUTED);c.text(x,cy+34,value,26,INK,True)
        c.text(x,y+277,schedule.zone,22,MUTED)
    elif panel.kind=='cards':
        c.text(LEFT,y+26,panel.title,26,INK,True)
        art_h=max(_card_size(r)[1] for r in panel.rows)
        caption_heights=_caption_heights(panel.rows)
        for i,row in enumerate(panel.rows):
            w,h=_card_size(row);x=LEFT+i*(_caption_width(row)+GAP)
            _card_art(c,row,x,y+62,load)
            cy=y+62+art_h+16
            for values,(size,step,color,bold),height in zip(_captions(row),CAPTION_STYLES,caption_heights):
                for index,value in enumerate(values):c.text(x,cy+step/2+index*step,value,size,color,bold)
                cy+=height
    elif panel.kind in {'footer','copy'}:
        if panel.kind=='footer':c.line(LEFT,y+4,RIGHT,'#e1dce7')
        for i,value in enumerate(panel.body):c.text(LEFT,y+30+i*32,value,22,MUTED)
