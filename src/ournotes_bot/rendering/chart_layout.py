# L3
# Input: Validated source score, song and selected difficulty.
# Output: Readable native-note columns with source-clock ticks and interval edges.
# Pos: Rendering / Song; bottom-to-top, left-to-right with independent annotation gutters.
# Effects: Pillow and injected artwork; measured native-size encoding, no data queries.
import math
from .labels import label as ui_label
from PIL import Image,ImageDraw
from ..data import localized_text
from .haneoka_theme import Canvas,MUTED,DIFFICULTIES,ACCENT,lines,font,mission_label,left_logo,prepare_assets
from .image_output import encode_image,ImageBudgetError,MAX_IMAGE_PIXELS,MAX_IMAGE_EDGE
from .note_skin import draw_note

TRACK_WIDTH = 312
COLUMN_PITCH = 568
TRACK_LEFT = 180
FEVER = '#bf568f'
INTERVAL_WIDTH = 8


def endpoint(c, cx, y, label, kind, is_end):
    """One endpoint vocabulary, also used by the legend."""
    color = ACCENT if kind == 'skill' else FEVER
    if kind == 'skill':
        c.box((cx-28,y-22,cx+28,y+22),color,9)
        if is_end:c.box((cx-24,y-18,cx+24,y+18),'#fcf8fd',6)
        c.text(cx,y,label,26,color if is_end else 'white',True,'center')
    else:
        if is_end:c.draw.rectangle((cx-14,y-14,cx+14,y+14),fill=color)
        else:c.draw.polygon(((cx,y-18),(cx-16,y+14),(cx+16,y+14)),fill=color)
        c.text(cx+20,y,label,26,color,True)


def continuation(c, cx, y, label, color, at_top, label_x=None):
    # A gap with two diagonal strokes is a cut, never an endpoint.
    c.draw.rectangle((cx-6,y-9,cx+6,y+9),fill='#fcf8fd')
    for dy in (-5,5):c.draw.line((cx-8,y+dy+4,cx+8,y+dy-4),fill=color,width=3)
    if label:c.text(cx if label_x is None else label_x,y+(30 if at_top else -30),label,26,color,True,'center')


def draw_legend(c, extra, locale):
    for y,kind,label,key in ((1044+extra,'skill','S1','技能 · 5 秒参考窗口'),
                             (1100+extra,'fever','G1','激奏 · 来源实际区间')):
        color=ACCENT if kind=='skill' else FEVER
        c.text(88,y,ui_label(key,locale),28,color)
        for cx,is_end,key in ((640,False,'开始'),(960,True,'结束')):
            endpoint(c,cx,y,label,kind,is_end)
            c.text(cx+90,y,ui_label(key,locale),28,MUTED)
        continuation(c,1270,y,label,color,True)
        c.text(1320,y,ui_label('跨栏延续',locale),28,MUTED)


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


def draw_times(c, markers, x, w, top, bottom, lo, hi, locale):
    visible=[v for v in markers if lo<=v[0]<hi]
    if not visible:return
    ys=[bottom-(tick-lo)/4 for tick,_,_ in visible]
    # Label placement is independent of its exact tick, retained by a leader.
    centers=[min(bottom-34,max(top+34,y)) for y in ys]
    for i in range(1,len(centers)):centers[i]=min(centers[i],centers[i-1]-76)
    if centers[-1]<top+34:
        centers[-1]=top+34
        for i in range(len(centers)-2,-1,-1):centers[i]=max(centers[i],centers[i+1]+76)
    for (_,seconds,is_end),y,cy in zip(visible,ys,centers):
        c.draw.line((x,y,x+w,y),fill='#646a7b',width=2)
        c.draw.line((x-78,cy,x-74,cy,x-74,y,x-4,y),fill='#777985',width=2)
        c.box((x-166,cy-32,x-78,cy+32),'#efedf2',8)
        text=f'{seconds//60}:{seconds%60:02}'
        c.text(x-122,cy-(12 if is_end else 0),text,28,MUTED,False,'center')
        if is_end:c.text(x-122,cy+18,ui_label('结束',locale),22,MUTED,False,'center')


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
        cx=x-40 if kind=='skill' else x+w+30
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
                                     cx if kind=='skill' else cx+20)
            for point,is_end in ((start,False),(end,True)):
                if point is None or not lo<=point<hi or (end_tick is not None and point>end_tick):continue
                yy=y(point)
                c.draw.line((rail,yy,edge,yy),fill=color,width=3)
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
    assets=prepare_assets(requests,load,icons+missions)
    def art(url,size):
        key=(url,size)
        if key not in assets:assets[key]=load(url,size)
        return assets[key]
    bpms=(score or {}).get('events',{}).get('bpm',[])
    times=time_markers(bpms,end_tick) if nodes else ()
    def paint(first,count):
        width=max(1800,TRACK_LEFT+max(0,count-1)*COLUMN_PITCH+TRACK_WIDTH+88);title=localized_text(song,'title',locale)
        titlewidth=width-1180;titlelines=lines(title,max(620,titlewidth),108,True)
        extra=(len(titlelines)-1)*126
        header=1000+extra;tracktop=header+230;trackbottom=tracktop+3840
        height=trackbottom+112 if nodes else header+130
        c=Canvas(width,height);c.text(88,78,ui_label('谱面详情',locale),50,bold=True);c.text(width-84,78,'Taki',40,ACCENT,True,'right')
        c.paste(art(song.jacket_url,(300,300)),88,164,300,300,radius=20)
        for i,line in enumerate(titlelines):c.text(452,239+i*126,line,108,bold=True)
        by=365+extra;logos=song.catalog.get('band_logos',[])
        bx=left_logo(c,art(logos[0],(230,100)),452,by,230,100) if len(logos)==1 else 452
        band=localized_text(song,'band',locale)
        c.text(bx,by,band,64)
        if song.traits and song.traits.attribute_icon:
            ax=bx+font(64,False,band.isascii()).getlength(band)+18
            c.paste(art(song.traits.attribute_icon,(64,64)),round(ax),by-32,64,64,True,trim=True)
        c.text(452,459+extra,f'ID {song.id}',40,MUTED)
        if selected:
            bg,fg=DIFFICULTIES[selected.difficulty];x=width-628;c.box((x,164,width-88,464),bg,28)
            c.text(width-358,233,selected.difficulty,60,fg,True,'center');c.text(width-358,362,f'{selected.level}',140,fg,True,'center')
        c.line(88,532+extra,width-88)
        from .song_detail_layout import seconds_at
        bpms=(score or {}).get('events',{}).get('bpm',[])
        vals=[e['bpm'] for e in bpms if e.get('bpm')]
        bpm=(f'{min(vals):g}' if min(vals)==max(vals) else f'{min(vals):.2f}–{max(vals):.2f}') if vals else ui_label('未获取',locale)
        duration=seconds_at(max((v['t'] for v in nodes),default=0),bpms)
        metrics=[('谱面时长',f'{int(duration)//60}:{int(duration)%60:02}' if duration else ui_label('未获取',locale)),('BPM',bpm),
                 ('音符数',str(selected.notes) if selected and selected.notes is not None else ui_label('未获取',locale)),
                 ('每秒音符数',f'{selected.notes/duration:.1f}' if selected and selected.notes and duration else ui_label('未获取',locale))]
        for i,(label,value) in enumerate(metrics):
            x=88+i*(width-176)/4;c.text(x,610+extra,ui_label(label,locale),44,MUTED)
            value_size=64 if label=='BPM' else 72
            # Measure the full value before placing adjacent metric columns.
            value_size=min(value_size,int(value_size*((width-176)/4-28)/max(1,font(value_size,True,value.isascii()).getlength(value))))
            c.text(x,699+extra,value,value_size,bold=True)
        reference=song.catalog.get('reference_metrics',{}).get(preview,{})
        for i,(label,key) in enumerate((('技能覆盖率 · 参考','sr'),('倍率 · 参考','score'),('效率 · 参考','eff'))):
            value=reference.get(key);x=88+i*(width-176)/4
            c.text(x,772+extra,ui_label(label,locale),36,MUTED)
            c.text(x,841+extra,f'{math.floor(value*100+.5)}%' if value is not None else ui_label('未获取',locale),64,bold=True)
        x=88+3*(width-176)/4;c.text(x,772+extra,ui_label('激奏类型',locale),36,MUTED)
        missions=song.traits.missions if song.traits else None
        if missions:
            for i,value in enumerate(missions):mission_label(c,x+i*(width-176)/12,841+extra,value,32 if width>=2200 else 24,40 if width>=2200 else 30)
        else:c.text(x,841+extra,ui_label('未获取',locale),36,MUTED)
        note=ui_label('Haneoka 理论参考 · 效率含 30 秒间隔',locale)
        if 'canonical-full-combo-mismatch' in reference.get('warnings',[]):note+=ui_label(' · 源数据音符数不一致',locale)
        c.text(width-88,901+extra,note,26,MUTED,align='right')
        c.line(88,932+extra,width-88);c.text(88,987+extra,ui_label('完整谱面',locale),48,bold=True)
        c.text(width-88,987+extra,ui_label('下 → 上 · 左 → 右',locale),36,MUTED,align='right')
        if not nodes:
            c.text(60,header+98,ui_label('完整谱面暂不可用',locale),30,MUTED);return c.image
        draw_legend(c,extra,locale)
        for local,col in enumerate(range(first,first+count)):
            x=TRACK_LEFT+local*COLUMN_PITCH;w=TRACK_WIDTH;lo=col*8*1920;hi=(col+1)*8*1920
            c.text(x,tracktop-60,f'{col+1:02}',26,MUTED)
            c.box((x-12,tracktop-4,x+w+12,trackbottom+14),'#141723',12)
            draw_markers(c,(score or {}).get('events',{}),x,w,tracktop,trackbottom,lo,hi,end_tick=end_tick)
            for lane in range(7):c.draw.line((x+lane*w/6,tracktop,x+lane*w/6,trackbottom),fill='#2c3145',width=1)
            for beat in range(33):
                yy=trackbottom-beat*120;c.draw.line((x,yy,x+w,yy),fill='#4b5066' if beat%4==0 else '#252b3c',width=2 if beat%4==0 else 1)
            draw_times(c,times,x,w,tracktop,trackbottom,lo,hi,locale)
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
        c.text(60,trackbottom+66,f"{ui_label('列',locale)} {first+1}–{first+count} / {columns} · "+notice,24,MUTED)
        return c.image
    result=[];first=0
    try:
        if not columns:return (encode_image(paint(0,0),close_input=True,preserve_size=True),)
        # Keep readable lanes and independent annotation gutters on every page.
        while first<columns:
            count=min(columns-first,4)
            while count:
                width=max(1800,TRACK_LEFT+(count-1)*COLUMN_PITCH+TRACK_WIDTH+88)
                title_count=len(lines(localized_text(song,'title',locale),max(620,width-1180),108,True))
                height=5182+(title_count-1)*126
                if width>MAX_IMAGE_EDGE or height>MAX_IMAGE_EDGE or width*height>MAX_IMAGE_PIXELS:
                    count-=1;continue
                try:raw=encode_image(paint(first,count),close_input=True,preserve_size=True)
                except ImageBudgetError:count-=1
                else:result.append(raw);first+=count;break
            else:raise ImageBudgetError('One complete chart column exceeds the readable budget')
        return tuple(result)
    finally:
        for image in assets.values():
            if image is not None:image.close()
