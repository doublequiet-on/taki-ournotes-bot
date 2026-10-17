# L3
# Input: Captured song/charts and optional validated score events.
# Output: Approved song detail layout with source skill timing on a real time axis.
# Pos: Rendering / Song details.
# Effects: Pillow and injected asset loading only.
from .labels import label as ui_label
from ..data import localized_text
from .haneoka_theme import Canvas,MUTED,DIFFICULTIES,SURFACE,ACCENT,lines,mission_label,left_logo,prepared_render_assets,font
from .image_output import encode_image,check_geometry


def seconds_at(tick,bpms):
    rows=sorted(bpms,key=lambda e:e['t'])
    if not rows or rows[0]['t']!=0 or any(e['bpm']<=0 for e in rows):return None
    import math
    import struct
    f=lambda value:struct.unpack('f',struct.pack('f',value))[0]
    total=0.0
    for i,row in enumerate(rows):
        if i+1<len(rows) and rows[i+1]['t']<=tick:
            total+=(rows[i+1]['t']-row['t'])*60000/f(f(row['bpm'])*480)
        else:
            return (round(total)+math.floor((tick-row['t'])*60000/f(f(row['bpm'])*480)))/1000
    return round(total)/1000



def judged_times(score):
    """Count eighth-beat judgments using the source meter and binary32 clock.

    Visible/automatic guides use the same source conversion as merged slide
    endpoints; hidden guide nodes remain structural.
    """
    import math
    import struct
    events=(score or {}).get('events',{});bpms=events.get('bpm',[])
    if not bpms:return None
    f=lambda value:struct.unpack('f',struct.pack('f',value))[0]
    bases=[];elapsed=0.0;previous=0;tempo=120.0
    for row in sorted(bpms,key=lambda r:r['t']):
        if row['bpm']<=0:return None
        elapsed+=(row['t']-previous)*60000/f(f(tempo)*480)
        bases.append((row['t'],round(elapsed),f(row['bpm'])))
        previous,tempo=row['t'],row['bpm']
    meters=[];bar=0;previous=0;measure=1920
    for row in sorted(events.get('sig',[]) or [{'t':0,'sig':[4,4]}],key=lambda r:r['t']):
        t=row['t'];num,den=row['sig']
        if num<=0 or den<=0 or num*1920//den<=0:return None
        bar+=(t-previous)//measure
        measure=num*1920//den
        meters.append((t,bar,measure,f(f(num*4)/den)))
        previous=t
    if meters[0][0]>0:meters.insert(0,(0,0,1920,4.0))
    def position(tick):
        t,bar,unit,_=next((r for r in reversed(meters) if r[0]<=tick),meters[0])
        offset,part=divmod(tick-t,unit)
        return bar+offset,f(f(part)/f(unit))
    def source_ms(tick):
        t,base,bpm=next((r for r in reversed(bases) if r[0]<=tick),bases[0])
        return math.floor((tick-t)*60000/f(bpm*480)+base)
    tempo_changes=[(position(t),source_ms(t),bpm) for t,_,bpm in bases]
    meter_changes=[(position(t),source_ms(t),beats) for t,_,_,beats in meters]
    def ms_position(bar,progress):
        target=(bar,progress)
        met=next((r for r in reversed(meter_changes) if r[0]<target),((0,0),0,4.0))
        bpm=next((r for r in reversed(tempo_changes) if r[0]<target),((0,0),0,160.0))
        (basebar,baseprogress),base_ms,_=met if met[1]>bpm[1] else bpm
        seconds_per_bar=f(f(met[2]*60)/bpm[2])
        elapsed=f(f(seconds_per_bar*f(bar-basebar))+f(seconds_per_bar*f(progress-baseprogress)))
        return base_ms+math.floor(f(elapsed*1000))
    def ms(tick):return ms_position(*position(tick))
    def beats_in_bar(bar):
        return next((beats for _,start,_,beats in reversed(meters) if start<=bar),4.0)
    def eighths(start,end):
        bar,progress=position(start)
        step=1/(2*beats_in_bar(bar))
        while True:
            progress+=step
            if progress>=1:
                bar+=1;progress-=1;step=1/(2*beats_in_bar(bar))
            now=ms_position(bar,f(progress))
            if now>=ms(end):return
            yield now
    from .chart_judgments import judgments
    return judgments((score or {}).get('notes',[]), position, ms_position, bases, eighths)




def fever_spans(events):
    """Only the captured start/end-pair format defines drawable intervals."""
    return [v for v in events.get('fever',[]) if isinstance(v,(list,tuple)) and len(v)==2
            and all(type(t) in (int,float) for t in v) and 0<=v[0]<v[1]]


def timeline(score):
    """Source skill times are independent of optional judgment-count support."""
    import math
    events=(score or {}).get('events',{});bpms=events.get('bpm',[])
    times=judged_times(score)
    nodes=[v for n in (score or {}).get('notes',[]) for v in (n.get('node',[]) if n.get('type') in ('long','guide') else [n])]
    end=seconds_at(max((v['t'] for v in nodes),default=0),bpms)
    if end is None:return None
    duration=max([end]+[seconds_at(t,bpms)+5 for t in events.get('skill',[])])+2
    density=[0]*(math.ceil(duration*2)+1) if times else []
    for t in times or ():density[int(t*2)]+=1
    windows=[]
    for tick in sorted(events.get('skill',[])):
        start=seconds_at(tick,bpms)
        if start is None:continue
        count=sum(start<=t<start+5 for t in times) if times else None
        windows.append(dict(start=start,end=min(duration,start+5),count=count,percent=count/len(times)*100 if times else None))
    for w in windows:w['rank']=1+sum(v['count']>w['count'] for v in windows) if times else None
    fever=[(seconds_at(a,bpms),seconds_at(b,bpms)) for a,b in fever_spans(events)]
    return dict(duration=duration,density=density,windows=windows,fever=fever)


def render(song,score,locale='zh',*,load,band_logos=()):
    import math
    title=localized_text(song,'title',locale);tl=lines(title,862,58,True)
    band=localized_text(song,'band',locale);bl=lines(band,570,38)
    extra=max(0,len(tl)-1)*74+max(0,len(bl)-1)*50
    credits=[(label,localized_text(song,key,locale) or ui_label('未获取',locale)) for label,key in ((ui_label('作曲',locale),'composer'),(ui_label('作词',locale),'lyricist'),(ui_label('编曲',locale),'arranger'))]
    credit_heights=[max(58,len(lines(value,404,30))*38+18) for _,value in credits]
    credit_extra=sum(credit_heights)-174
    data=timeline(score);events=(score or {}).get('events',{});bpms=events.get('bpm',[])
    windows=data['windows'] if data else []
    timing_extra=max(0,math.ceil(len(windows)/3)-2)*86
    reward_rows=max(len(song.catalog.get('score_rewards',[])),len(song.catalog.get('combo_rewards',{}).get('expert',[])))
    height=2080+extra+credit_extra+timing_extra+max(0,reward_rows-5)*60
    logos=band_logos or song.catalog.get('band_logos',[])
    requests={(song.jacket_url,(386,386))}
    if logos:requests.add((logos[0],(190,68)))
    if song.traits and song.traits.attribute_icon:requests.add((song.traits.attribute_icon,(54,54)))
    for row in song.catalog.get('score_rewards',[])+song.catalog.get('combo_rewards',{}).get('expert',[]):
        if row['image']:requests.add((row['image'],(42,42)))
    icons=tuple(f'gekisou-{v.lower()}.png' for v in (song.traits.missions or ()) if v in ('COMBO','JUST','LUCK')) if song.traits else ()
    with prepared_render_assets(requests,load,icons) as assets:
        def art(url,size):
            return assets[(url,size)]
        def section(y,title):
            c.line(54,y-38,1346);c.text(54,y,title,38,bold=True)
        check_geometry((1400,height))
        c=Canvas(1400,height);c.header(ui_label('歌曲详情',locale),f'/查曲 {song.id}')
        c.paste(art(song.jacket_url,(386,386)),54,176,386,386,radius=16)
        c.text(247,596,f'ID {song.id}',32,MUTED,align='center')
        for i,t in enumerate(tl):c.text(484,204+i*74,t,58,bold=True)
        by=277+(len(tl)-1)*74
        logos=band_logos or song.catalog.get('band_logos',[])
        bx=left_logo(c,art(logos[0],(190,68)),484,by,190,68) if logos else 484
        for i,b in enumerate(bl):c.text(bx,by+i*50,b,38)
        if song.traits and song.traits.attribute_icon:
            ax=min(1292,bx+font(38,False,band.isascii()).getlength(bl[-1])+24)
            c.paste(art(song.traits.attribute_icon,(54,54)),round(ax),by+(len(bl)-1)*50-27,54,54,True,trim=True)
        cy=329+extra
        for (label,value),row_height in zip(credits,credit_heights):
            c.text(484,cy+19,label,30,MUTED)
            c.paragraph(584,cy,value,404,30,38)
            cy+=row_height
        c.text(484,cy+19,ui_label('发布',locale),30,MUTED);c.text(584,cy+19,str(song.start_at or ui_label('未获取',locale))[:10].replace('-','/'),32)
        duration=(song.catalog.get('duration_ms') or 0)/1000 or (data['duration']-2 if data else 0)
        c.text(1004,348+extra,ui_label('时长',locale),30,MUTED);c.text(1346,348+extra,f'{int(duration)//60}:{int(duration)%60:02}' if duration else ui_label('未获取',locale),34,align='right')
        vals=[v['bpm'] for v in bpms];bpm=f'{min(vals):.2f}–{max(vals):.2f}' if vals and min(vals)!=max(vals) else f'{vals[0]:g}' if vals else ui_label('未获取',locale)
        c.text(1004,406+extra,'BPM',30,MUTED);c.text(1346,406+extra,bpm,32,align='right')
        extra+=credit_extra
        section(663+extra,ui_label('难度与 Note',locale))
        for i,(name,(bg,fg)) in enumerate(DIFFICULTIES.items()):
            ch=next((v for v in song.charts if v.difficulty==name),None);cx=216+i*322
            c.box((cx-80,708+extra,cx+80,808+extra),bg,18)
            c.text(cx,758+extra,f'{ch.level}' if ch else '—',58,fg,True,'center')
            c.text(cx,850+extra,f'{ch.notes} Note' if ch and ch.notes is not None else ui_label('Note 未获取',locale),32,MUTED,align='center')
        section(938+extra,ui_label('技能点位',locale));c.text(max(282,78+font(38,True).getlength(ui_label('技能点位',locale))),938+extra,ui_label('EXPERT · 5秒',locale),28,'#bb4f81')
        left,right=54,1346;py=984+extra;pb=1204+extra
        c.box((left,py,right,pb),'#f6f3f8',16)
        if data:
            tx=lambda t:left+(right-left)*t/data['duration']
            for i,(a,b) in enumerate(data['fever']):
                if a is None or b is None:continue
                x0,x1=round(tx(a)),round(tx(b));c.draw.rectangle((x0,py,x1,pb),fill='#eee1ec');c.text(x0+12,py+22,ui_label('激奏段 {n}',locale).format(n=i+1),25,'#77536e')
            density=data['density']
            if density:
                peak=max(density) or 1
                pts=[(left,1118+extra)]+[(min(right,tx(i/2)),1118+extra-v/peak*86) for i,v in enumerate(density)]+[(right,1118+extra)]
                c.draw.polygon(pts,fill='#c0bdc6')
            else:c.text(78,1090+extra,ui_label('已获取技能时间 · 此谱面的音符统计暂未支持',locale),28,MUTED)
            for i,w in enumerate(windows):
                x0,x1=round(tx(w['start'])),round(tx(w['end']));c.box((x0,1132+extra,x1,1188+extra),ACCENT,7);c.text(x0+12,1160+extra,str(i+1),28,'white',True)
            for sec in range(0,math.ceil(data['duration']),30):c.text(tx(sec),1240+extra,f'{sec//60}:{sec%60:02}',26,MUTED,align='left' if sec==0 else 'center')
            for i,w in enumerate(windows):
                x=54+(i%3)*440;y=1298+extra+(i//3)*86;c.box((x,y-18,x+36,y+18),ACCENT,6);c.text(x+18,y,str(i+1),24,'white',True,'center')
                c.text(x+56,y,f"{w['start']:.3f}s",29)
                if w['count'] is not None:
                    c.text(x+394,y,f"{w['count']} Note",28,MUTED,align='right')
                    c.text(x+56,y+37,ui_label('占比 {percent:.1f}% · #{rank}',locale).format(**w),28,MUTED)
        else:c.text(54,1100+extra,ui_label('技能点位资料未获取',locale),32,MUTED)
        offset=extra+timing_extra;section(1500+offset,ui_label('激奏段',locale))
        missions=song.traits.missions if song.traits else None
        for i,value in enumerate(missions or (ui_label('未获取',locale),)):
            x=54+i*440;c.box((x,1540+offset,x+412,1604+offset),SURFACE,12);c.text(x+28,1572+offset,f'{i+1:02}',30,MUTED);mission_label(c,x+110,1572+offset,value,40,48,True)
        section(1660+offset,ui_label('奖励',locale));c.text(54,1722+offset,ui_label('得分系数奖励',locale),34,bold=True);c.text(752,1722+offset,ui_label('连击奖励',locale),34,bold=True);c.text(max(916,776+font(34,True).getlength(ui_label('连击奖励',locale))),1722+offset,'EXPERT',28,'#bb4f81')
        ranks={v['scoreRank']:v['requiredScore'] for v in song.catalog.get('score_ranks',[])}
        expert=next((v for v in song.charts if v.difficulty=='EXPERT'),None)
        for col,values in enumerate((song.catalog.get('score_rewards',[]),song.catalog.get('combo_rewards',{}).get('expert',[]))):
            for i,row in enumerate(values):
                x=54 if col==0 else 752;y=1784+offset+i*60;tier=row['tier']
                label=({3:'C',4:'B',5:'A',6:'S',7:'SS'} if col==0 else {0:'25%',1:'50%',2:'75%',3:'FULL COMBO'}).get(tier,str(tier));c.text(x,y,label,28,ACCENT,col==0)
                value=ranks.get(tier) if col==0 else math.ceil(expert.notes*(tier+1)/4) if expert and expert.notes and 0<=tier<=3 else None
                c.text(410 if col==0 else 1030,y,f'{value:,}' if value is not None else ui_label('未知',locale),32,align='right')
                if row['image']:c.paste(art(row['image'],(42,42)),532 if col==0 else 1186,y-21,42,42,True,trim=True)
                c.text(636 if col==0 else 1346,y,f'×{row["count"]:,}',30,align='right')
        return encode_image(c.image,close_input=True,preserve_size=True)
