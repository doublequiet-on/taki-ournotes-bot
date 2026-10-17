# L3
# Input: Validated source score, song and selected difficulty.
# Output: Readable native-note columns with source-clock ticks and interval edges.
# Pos: Rendering / Song; bottom-to-top, left-to-right with independent annotation gutters.
# Effects: Pillow and injected artwork; measured native-size encoding, no data queries.
import math
from functools import lru_cache
from .labels import label as ui_label
from PIL import Image,ImageDraw
from ..data import localized_text
from .haneoka_theme import Canvas,MUTED,DIFFICULTIES,ACCENT,lines,font,mission_label,left_logo,prepared_render_assets
from .image_output import encode_image,ImageBudgetError
from .note_skin import draw_note

TRACK_WIDTH = 312
COLUMN_PITCH = 460
TRACK_LEFT = 660
FEVER = '#bf568f'
INTERVAL_WIDTH = 8
CHART_MAX_PIXELS = 96_000_000
CHART_MAX_EDGE = 30_000


@lru_cache(maxsize=4)
def endpoint_sprite(kind, is_end):
    """Local 4x coverage antialiasing keeps rounded contours crisp at native size."""
    scale=4
    mask=Image.new('L',(64*scale,64*scale));d=ImageDraw.Draw(mask)
    box=lambda a:tuple(round((v+32)*scale) for v in a)
    if kind=='skill':
        d.rounded_rectangle(box((-28,-22,28,22)),16*scale,fill=255)
        if is_end:d.rounded_rectangle(box((-24,-18,24,18)),12*scale,fill=0)
    elif is_end:
        d.rounded_rectangle(box((-14,-14,14,14)),6*scale,fill=255)
    else:
        points=[(32*scale,17*scale),(21*scale,43*scale),(43*scale,43*scale)]
        d.polygon(points,fill=255)
        d.line(points+[points[0]],fill=255,width=6*scale,joint='curve')
        for x,y in points:d.ellipse((x-3*scale,y-3*scale,x+3*scale,y+3*scale),fill=255)
    alpha=mask.resize((64,64),Image.Resampling.BOX);mask.close()
    tile=Image.new('RGBA',(64,64),ACCENT if kind=='skill' else FEVER)
    # A restrained top highlight gives all endpoint silhouettes the same finish.
    base=tile.getpixel((32,32))[:3];surface=ImageDraw.Draw(tile)
    for row in range(64):
        light=.16*(1-row/63)
        color=tuple(round(v+(255-v)*light) for v in base)
        surface.line((0,row,63,row),fill=color+(255,))
    tile.putalpha(alpha);alpha.close()
    return tile


def endpoint(c, cx, y, label, kind, is_end):
    """Glyphs stay outside the rail; only their short leader touches the rail."""
    color=ACCENT if kind=='skill' else FEVER
    tile=endpoint_sprite(kind,is_end)
    c.image.paste(tile,(round(cx)-32,round(y)-32),tile)
    if kind=='skill':c.text(cx,y,label,26,color if is_end else 'white',True,'center')
    else:c.text(cx,y+31,label,24,color,True,'center')


def continuation(c, cx, y, label, color, at_top, label_x=None):
    # A gap with two diagonal strokes is a cut, never an endpoint.
    c.draw.rectangle((cx-6,y-9,cx+6,y+9),fill='#fcf8fd')
    for dy in (-5,5):c.draw.line((cx-8,y+dy+4,cx+8,y+dy-4),fill=color,width=3)
    if label:c.text(cx if label_x is None else label_x,y+(30 if at_top else -30),label,26,color,True,'center')


def draw_legend(c, extra, locale):
    y=104+extra
    offset=560
    c.text(offset+88,y,ui_label('技能 · 5秒参考',locale),26,ACCENT)
    c.text(offset+850,y,ui_label('激奏',locale),26,FEVER)
    for kind,label,centers in (('skill','S1',(360,590)),('fever','G1',(1020,1250))):
        for cx,is_end in zip(centers,(False,True)):
            cx+=offset
            endpoint(c,cx,y,label,kind,is_end)
            c.text(cx+44,y,ui_label('结束' if is_end else '开始',locale),26,MUTED)
    continuation(c,offset+1480,y,'',MUTED,True)
    c.text(offset+1510,y,ui_label('跨栏延续',locale),26,MUTED)


def time_markers(bpms, end_tick):
    """Invert the existing millisecond source clock, including tempo changes."""
    from .song_detail_layout import seconds_at
    duration=seconds_at(end_tick,bpms)
    if duration is None:return ()
    result=[(0.0,0,False)]
    for seconds in range(15,math.ceil(duration),15):
        if seconds>=duration:break
        low,high=0.0,float(end_tick)
        for _ in range(48):
            mid=(low+high)/2
            if seconds_at(mid,bpms)<seconds:low=mid
            else:high=mid
        tick=high
        if abs(tick-round(tick))<1e-6:tick=float(round(tick))
        result.append((tick,seconds,False))
    if duration==0:result.clear()
    result.append((end_tick,math.floor(duration),True))
    return tuple(result)


def draw_times(c, markers, x, w, top, bottom, lo, hi, locale, *, occupied=()):
    visible=[v for v in markers if lo<=v[0]<hi]
    if not visible:return
    ys=[bottom-(tick-lo)/4 for tick,_,_ in visible]
    centers=[]
    for y in ys:
        cy=y
        # Share the left gutter without covering endpoint or continuation labels.
        blocked=list(occupied)+[(v,38) for v in centers]
        for _ in range(len(blocked)+1):
            conflicts=[(pos,gap) for pos,gap in blocked if abs(cy-pos)<gap]
            if not conflicts:break
            cy=min(pos-gap for pos,gap in conflicts)
        if cy<top+18:
            cy=max(top+18,y)
            for pos,gap in sorted(blocked):
                if abs(cy-pos)<gap:cy=pos+gap
        centers.append(cy)
    for (_,seconds,_),y,cy in zip(visible,ys,centers):
        c.draw.line((x,y,x+w,y),fill='#646a7b',width=2)
        c.text(x-24,cy,f'{seconds//60}:{seconds%60:02}',28,MUTED,False,'right')
        if abs(cy-y)>1:
            c.draw.line((x-20,cy,x-18,cy,x-18,y,x-16,y),fill='#777985',width=1)


def skill_end_tick(start, bpms, duration=5):
    """Advance seconds across tempo changes, rather than assuming a fixed beat span."""
    rows=sorted(bpms,key=lambda row:row['t'])
    if not rows or rows[0]['t']!=0 or any(row['bpm']<=0 for row in rows):return None
    tempo=next(row['bpm'] for row in reversed(rows) if row['t']<=start)
    tick=start;remaining=duration
    for row in rows:
        if row['t']<=start:continue
        seconds=(row['t']-tick)*60/(tempo*480)
        if seconds>=remaining:return tick+remaining*tempo*480/60
        remaining-=seconds;tick=row['t'];tempo=row['bpm']
    return tick+remaining*tempo*480/60


def draw_markers(c, events, x, w, top, bottom, lo, hi, *, end_tick=None):
    """Real endpoints are half-open; cuts retain identity without endpoint caps."""
    y=lambda tick:bottom-(tick-lo)/1920*480
    from .song_detail_layout import fever_spans
    skills=[(t,skill_end_tick(t,events.get('bpm',[]))) for t in sorted(events.get('skill',[]))]
    for kind,prefix,spans in (('skill','S',skills),('fever','G',fever_spans(events))):
        color=ACCENT if kind=='skill' else FEVER
        rail=x-16 if kind=='skill' else x+w+18
        cx=x-56 if kind=='skill' else x+w+44
        edge=x if kind=='skill' else x+w
        limit=min(hi,end_tick) if end_tick is not None else hi
        for i,(start,end) in enumerate(spans,1):
            label=f'{prefix}{i}'
            if end is not None:
                a,b=max(start,lo),min(end,limit)
                if a<b:
                    c.draw.line((rail,y(a),rail,y(b)),fill=color,width=INTERVAL_WIDTH)
                    for cut,at_top in ((lo,False),(limit,True)):
                        if not start<cut<end:continue
                        nearby=any(lo<=p<hi and (end_tick is None or p<=end_tick) and abs(y(p)-y(cut))<76 for p in (start,end))
                        continuation(c,rail,y(cut),'' if nearby else label,color,at_top,
                                     cx)
            for point,is_end in ((start,False),(end,True)):
                if point is None or not lo<=point<hi or (end_tick is not None and point>end_tick):continue
                yy=y(point)
                tip=cx+28 if kind=='skill' else cx-14
                c.draw.line((tip,yy,edge,yy),fill=color,width=3)
                endpoint(c,cx,yy,label,kind,is_end)


def render(song,charts,score,preview,locale='zh',*,load,notice=''):
    # Auto control points have no drawable lane/width. As in the legacy chart
    # renderer, connect the surrounding positioned anchors and omit that glyph.
    def positioned(node):return all(type(node.get(k)) in (int,float) for k in ('t','pos','size'))
    notes=[{**n,'node':[v for v in n.get('node',[]) if positioned(v)]} if n.get('type') in ('long','guide') else n
           for n in (score or {}).get('notes',[]) if n.get('type') in ('long','guide') or positioned(n)]
    nodes=[v for n in notes for v in (n.get('node',[]) if n.get('type') in ('long','guide') else [n])]
    end_tick=max((v['t'] for v in nodes),default=0)
    bars=math.ceil(end_tick/1920)+1
    columns=math.ceil(bars/8) if nodes else 0
    selected=next((c for c in charts if c.difficulty==preview),charts[-1] if charts else None)
    requests={(song.jacket_url,(300,300))}
    logos=song.catalog.get('band_logos',[])
    if len(logos)==1:requests.add((logos[0],(230,100)))
    if song.traits and song.traits.attribute_icon:requests.add((song.traits.attribute_icon,(64,64)))
    icons=('skin001.json','skin001.png') if nodes else ()
    missions=tuple(f'gekisou-{v.lower()}.png' for v in (song.traits.missions or ()) if v in ('COMBO','JUST','LUCK')) if song.traits else ()
    def art(url,size):
        key=(url,size)
        return assets[key]
    bpms=(score or {}).get('events',{}).get('bpm',[])
    times=time_markers(bpms,end_tick) if nodes else ()
    def paint():
        width=max(2360,TRACK_LEFT+max(0,columns-1)*COLUMN_PITCH+TRACK_WIDTH+80)
        titlelines=lines(localized_text(song,'title',locale),452,50,True)
        bandlines=lines(localized_text(song,'band',locale),390,34)
        tracktop=200;trackbottom=tracktop+3840
        height=max(trackbottom+112,2400+len(titlelines)*72+len(bandlines)*50)
        if width>CHART_MAX_EDGE or height>CHART_MAX_EDGE or width*height>CHART_MAX_PIXELS:
            raise ImageBudgetError("Complete chart exceeds its native-size budget")
        c=Canvas(width,height)
        c.text(48,64,ui_label('谱面详情',locale),36,bold=True)
        c.text(500,64,'Taki',30,ACCENT,True,'right')
        c.paste(art(song.jacket_url,(300,300)),48,110,240,240,radius=18)
        y=406
        for line in titlelines:
            c.text(48,y,line,50,bold=True);y+=62
        logos=song.catalog.get('band_logos',[])
        if len(logos)==1:
            left_logo(c,art(logos[0],(230,100)),48,y+18,156,64);y+=74
        for i,line in enumerate(bandlines):
            c.text(48,y,line,34)
            if i==0 and song.traits and song.traits.attribute_icon:
                ax=48+font(34,False,line.isascii()).getlength(line)+14
                c.paste(art(song.traits.attribute_icon,(64,64)),round(ax),y-24,48,48,True,trim=True)
            y+=44
        c.text(48,y+6,f'ID {song.id}',26,MUTED);y+=58
        if selected:
            bg,fg=DIFFICULTIES[selected.difficulty]
            level=str(selected.level)
            name_width=font(30,True,True).getlength(selected.difficulty)
            badge_width=round(name_width+font(40,True,True).getlength(level)+64)
            c.box((48,y,48+badge_width,y+68),bg,16)
            c.text(68,y+34,selected.difficulty,30,fg,True)
            c.text(48+badge_width-20,y+34,level,40,fg,True,'right');y+=98
        c.line(48,y,500);y+=46
        from .song_detail_layout import seconds_at
        bpms=(score or {}).get('events',{}).get('bpm',[])
        vals=[e['bpm'] for e in bpms if e.get('bpm')]
        bpm=(f'{min(vals):g}' if min(vals)==max(vals) else f'{min(vals):.2f}–{max(vals):.2f}') if vals else ui_label('未获取',locale)
        duration=seconds_at(max((v['t'] for v in nodes),default=0),bpms)
        metrics=[('谱面时长',f'{int(duration)//60}:{int(duration)%60:02}' if duration else ui_label('未获取',locale)),('BPM',bpm),
                 ('音符数',str(selected.notes) if selected and selected.notes is not None else ui_label('未获取',locale)),
                 ('每秒音符数',f'{selected.notes/duration:.1f}' if selected and selected.notes and duration else ui_label('未获取',locale))]
        reference=song.catalog.get('reference_metrics',{}).get(preview,{})
        for label,key in (('技能覆盖率 · 参考','sr'),('倍率 · 参考','score'),('效率 · 参考','eff')):
            value=reference.get(key)
            metrics.append((label,f'{math.floor(value*100+.5)}%' if value is not None else ui_label('未获取',locale)))
        for label,value in metrics:
            c.text(48,y,ui_label(label,locale),26,MUTED)
            c.text(48,y+42,value,44,bold=True);y+=108
        c.text(48,y,ui_label('激奏类型',locale),26,MUTED);y+=52
        missions=song.traits.missions if song.traits else None
        if missions:
            for value in missions:
                mission_label(c,48,y,value,28,36);y+=52
        else:c.text(48,y,ui_label('未获取',locale),38,MUTED);y+=52
        note=ui_label('Haneoka 理论参考 · 效率含 30 秒间隔',locale)
        if 'canonical-full-combo-mismatch' in reference.get('warnings',[]):note+=ui_label(' · 源数据音符数不一致',locale)
        y+=24
        for line in lines(note,452,24):
            c.text(48,y,line,24,MUTED);y+=36
        c.draw.line((544,48,544,height-64),fill='#ded8e2',width=2)
        c.text(TRACK_LEFT-12,46,ui_label('完整谱面',locale),36,bold=True)
        c.text(width-88,46,ui_label('下 → 上 · 左 → 右',locale),28,MUTED,align='right')
        if not nodes:
            c.text(TRACK_LEFT,tracktop+98,ui_label('完整谱面暂不可用',locale),30,MUTED);return c.image
        draw_legend(c,0,locale)
        for col in range(columns):
            x=TRACK_LEFT+col*COLUMN_PITCH;w=TRACK_WIDTH;lo=col*8*1920;hi=(col+1)*8*1920
            c.text(x,tracktop-28,f'{col+1:02}',26,MUTED)
            c.box((x-12,tracktop-4,x+w+12,trackbottom+14),'#141723',12)
            draw_markers(c,(score or {}).get('events',{}),x,w,tracktop,trackbottom,lo,hi,end_tick=end_tick)
            for lane in range(7):c.draw.line((x+lane*w/6,tracktop,x+lane*w/6,trackbottom),fill='#2c3145',width=1)
            for beat in range(33):
                yy=trackbottom-beat*120;c.draw.line((x,yy,x+w,yy),fill='#4b5066' if beat%4==0 else '#252b3c',width=2 if beat%4==0 else 1)
            occupied=[]
            for tick in sorted((score or {}).get('events',{}).get('skill',[])):
                end=skill_end_tick(tick,bpms)
                occupied.extend((trackbottom-(p-lo)/4,44) for p in (tick,end) if p is not None and lo<=p<hi and p<=end_tick)
                if end is not None:
                    if tick<lo<end:occupied.append((trackbottom-30,44))
                    limit=min(hi,end_tick)
                    if tick<limit<end:occupied.append((trackbottom-(limit-lo)/4+30,44))
            draw_times(c,times,x,w,tracktop,trackbottom,lo,hi,locale,occupied=occupied)
            # A per-column ribbon avoids allocating an entire multi-megapixel
            # transparent canvas for every column.
            ribbon=Image.new('RGBA',(w,3841));rd=ImageDraw.Draw(ribbon)
            for n in notes:
                if n.get('type') not in ('long','guide'):continue
                for a,b in zip(n['node'],n['node'][1:]):
                    if b['t']<=lo or a['t']>=hi or b['t']<=a['t']:continue
                    lefts=[];rights=[];start=max(lo,a['t']);end=min(hi,b['t'])
                    for step in range(33):
                        t=start+(end-start)*step/32;u=(t-a['t'])/(b['t']-a['t'])
                        f=1-(1-u)**2 if a.get('ease')=='out' else u*u if a.get('ease')=='in' else u
                        pos=a['pos']+(b['pos']-a['pos'])*f;size=a['size']+(b['size']-a['size'])*f;y=3840-(t-lo)/1920*480
                        lefts.append((pos/24*w,y));rights.append(((pos+size)/24*w,y))
                    rd.polygon(lefts+rights[::-1],fill=(93,111,233,75));rd.line(lefts,fill=(148,167,255,125),width=2);rd.line(rights,fill=(148,167,255,125),width=2)
            c.image.paste(ribbon,(x,tracktop),ribbon);ribbon.close()
            for n in notes:
                if n.get('type')=='guide':continue
                seq=n['node'] if n.get('type')=='long' else [n]
                for i,node in enumerate(seq):
                    if not lo<=node['t']<hi or node.get('visible') is False:continue
                    kind='trace' if n.get('type')=='trace' else 'tap'
                    if n.get('type')=='long':kind='slide-start' if i==0 else 'slide-end' if i==len(seq)-1 else 'slide-node'
                    if node.get('type')=='flick' or n.get('type')=='flick':
                        direction=node.get('dir',n.get('dir'));kind='flick-left' if direction=='left' else 'flick-right' if direction=='right' else 'flick'
                    draw_note(c,x+(node['pos']+node['size']/2)/24*w,trackbottom-(node['t']-lo)/1920*480,node['size']/24*w,kind,node['size'])
        c.text(TRACK_LEFT,trackbottom+66,f"{ui_label('列',locale)} 1–{columns} / {columns} · "+notice,24,MUTED)
        return c.image
    with prepared_render_assets(requests,load,icons+missions) as assets:
        return (encode_image(paint(),close_input=True,preserve_size=True,
                                 max_pixels=CHART_MAX_PIXELS,max_edge=CHART_MAX_EDGE,
                                 quality_steps=(95,90,88,84,80,76,72)),)
