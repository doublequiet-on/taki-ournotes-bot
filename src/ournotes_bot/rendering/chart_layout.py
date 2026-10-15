# L3
# Input: Validated source score, song and selected difficulty.
# Output: Original-note dark-track images; complete columns, bottom-to-top then left-to-right.
# Pos: Rendering / Song; approved sparse full-chart layout.
# Effects: Pillow and injected artwork; measured native-size encoding, no data queries.
import math
from .labels import label as ui_label
from PIL import Image,ImageDraw
from ..data import localized_text
from .haneoka_theme import Canvas,MUTED,DIFFICULTIES,ACCENT,lines,font,mission_label,left_logo,prepare_assets
from .image_output import encode_image,ImageBudgetError,MAX_IMAGE_PIXELS,MAX_IMAGE_EDGE
from .note_skin import draw_note


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


def draw_markers(c, events, x, w, top, bottom, lo, hi):
    """Use source ticks and half-open columns; fever spans continue across columns."""
    y=lambda tick:bottom-(tick-lo)/1920*480
    from .song_detail_layout import fever_spans
    for i,(start,end) in enumerate(fever_spans(events),1):
        a,b=max(start,lo),min(end,hi)
        if a>=b:continue
        ya,yb=y(b),y(a)
        c.draw.line((x+w+17,ya,x+w+17,yb),fill='#bf568f',width=6)
        for yy in (ya,yb):c.draw.line((x+w+13,yy,x+w+23,yy),fill='#bf568f',width=3)
        c.text(x+w+33,(ya+yb)/2,str(i),30,'#bf568f',True,'center')
    for i,tick in enumerate(sorted(events.get('skill',[])),1):
        end=skill_end_tick(tick,events.get('bpm',[]))
        if end is not None and max(tick,lo)<min(end,hi):
            c.draw.line((x-16,y(min(end,hi)),x-16,y(max(tick,lo))),fill=ACCENT,width=3)
        for point,is_end in ((tick,False),(end,True)):
            if point is None or not lo<=point<hi:continue
            yy=y(point)
            c.box((x-62,yy-18,x-20,yy+18),ACCENT,7)
            if is_end:c.box((x-59,yy-15,x-23,yy+15),'#fcf8fd',5)
            if is_end:
                c.draw.line((x-52,yy-24,x-30,yy-24),fill=ACCENT,width=4)
            else:
                c.draw.polygon(((x-41,yy-29),(x-51,yy-19),(x-31,yy-19)),fill=ACCENT)
            c.text(x-41,yy,f'S{i}',23,ACCENT if is_end else 'white',True,'center')
            c.draw.line((x-18,yy,x,yy),fill=ACCENT,width=3)


def render(song,charts,score,preview,locale='zh',*,load,notice=''):
    # Auto control points have no drawable lane/width. As in the legacy chart
    # renderer, connect the surrounding positioned anchors and omit that glyph.
    def positioned(node):return all(type(node.get(k)) in (int,float) for k in ('t','pos','size'))
    notes=[{**n,'node':[v for v in n.get('node',[]) if positioned(v)]} if n.get('type') in ('long','guide') else n
           for n in (score or {}).get('notes',[]) if n.get('type') in ('long','guide') or positioned(n)]
    nodes=[v for n in notes for v in (n.get('node',[]) if n.get('type') in ('long','guide') else [n])]
    bars=math.ceil(max((v['t'] for v in nodes),default=0)/1920)+1
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
    def paint(first,count,pitch=420):
        width=max(1800,count*pitch+60);title=localized_text(song,'title',locale)
        titlewidth=width-1180;titlelines=lines(title,max(620,titlewidth),108,True)
        extra=(len(titlelines)-1)*126
        header=1000+extra;tracktop=header+110;trackbottom=tracktop+3840
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
        c.text(width-88,987+extra,ui_label('每列 32 拍 · 下 → 上 · 左 → 右',locale),36,MUTED,align='right')
        if not nodes:
            c.text(60,header+98,ui_label('完整谱面暂不可用',locale),30,MUTED);return c.image
        c.text(88,1044+extra,ui_label('技能 5 秒 · ▲ 开始 / ━ 结束',locale),27,ACCENT);c.text(610,1044+extra,ui_label('粉色侧线：激奏段 · 数字对应激奏顺序',locale),27,'#bf568f')
        for local,col in enumerate(range(first,first+count)):
            x=100+local*pitch;w=pitch-108;lo=col*8*1920;hi=(col+1)*8*1920
            c.text(x,tracktop-28,f"{col+1:02} / {ui_label('拍',locale)} {col*32+1}–{(col+1)*32}",26,MUTED)
            c.box((x-12,tracktop-4,x+w+12,trackbottom+14),'#141723',12)
            draw_markers(c,(score or {}).get('events',{}),x,w,tracktop,trackbottom,lo,hi)
            for lane in range(7):c.draw.line((x+lane*w/6,tracktop,x+lane*w/6,trackbottom),fill='#2c3145',width=1)
            for beat in range(33):
                yy=trackbottom-beat*120;c.draw.line((x,yy,x+w,yy),fill='#4b5066' if beat%4==0 else '#252b3c',width=2 if beat%4==0 else 1)
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
        # Try a complete chart before splitting. Reduce horizontal lane width only;
        # time scale, note spacing and text sizes remain unchanged.
        for pitch in (420,360,320,288):
            width=max(1800,columns*pitch+60)
            title_count=len(lines(localized_text(song,'title',locale),max(620,width-1180),108,True))
            height=5062+(title_count-1)*126
            if width>MAX_IMAGE_EDGE or height>MAX_IMAGE_EDGE or width*height>MAX_IMAGE_PIXELS:continue
            try:raw=encode_image(paint(0,columns,pitch),close_input=True,preserve_size=True)
            except ImageBudgetError:continue
            else:return (raw,)
        while first<columns:
            count=min(columns-first,5)
            while count:
                image=paint(first,count)
                try:raw=encode_image(image,close_input=True,preserve_size=True)
                except ImageBudgetError:count-=1
                else:result.append(raw);first+=count;break
            else:raise ImageBudgetError('One complete chart column exceeds the readable budget')
        return tuple(result)
    finally:
        for image in assets.values():
            if image is not None:image.close()
