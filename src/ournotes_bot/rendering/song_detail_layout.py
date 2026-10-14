# L3
# Input: Captured song/charts and optional validated score events.
# Output: Approved song detail layout with source skill timing on a real time axis.
# Pos: Rendering / Song details.
# Effects: Pillow and injected asset loading only.
from ..data import localized_text
from .haneoka_theme import Canvas,MUTED,DIFFICULTIES,SURFACE,ACCENT,lines,mission_label,left_logo
from .image_output import encode_image


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
    """ON long notes include eighth-beat ticks, with quarter-beat suppression.

    Match the published Haneoka runtime's float32 clock at judgment boundaries.
    Other meter/guide formats retain the chart but do not claim these counts.
    """
    import math
    import struct
    events=(score or {}).get('events',{});bpms=events.get('bpm',[])
    if not bpms or any(v.get('sig')!=[4,4] for v in events.get('sig',[])):return None
    if any(n.get('type')=='guide' for n in (score or {}).get('notes',[])):return None
    f=lambda value:struct.unpack('f',struct.pack('f',value))[0]
    bases=[];elapsed=0.0;previous=0;tempo=120.0
    for row in sorted(bpms,key=lambda r:r['t']):
        if row['bpm']<=0:return None
        elapsed+=(row['t']-previous)*60000/f(f(tempo)*480)
        bases.append((row['t'],round(elapsed),f(row['bpm'])))
        previous,tempo=row['t'],row['bpm']
    def ms(tick):
        t,base,bpm=next((r for r in reversed(bases) if r[0]<tick),bases[0])
        bar,part=divmod(tick,1920);basebar,basepart=divmod(t,1920)
        bar_seconds=f(f(4*60)/bpm)
        seconds=f(f(bar_seconds*f(bar-basebar))+f(bar_seconds*f(f(part/1920)-f(basepart/1920))))
        return base+math.floor(f(seconds*1000))
    result=[];merged=set()
    for note in (score or {}).get('notes',[]):
        if note.get('type')!='long':result.append(ms(note['t']));continue
        nodes=note['node']
        for i,node in enumerate(nodes):
            if node.get('visible') is False:continue
            key=(node['t'],node.get('pos'),node['size'],i==0,i==len(nodes)-1,node.get('type'),node.get('dir'),node.get('crit',False))
            if i in (0,len(nodes)-1) and key in merged:continue
            merged.add(key);result.append(ms(node['t']))
        anchors=[nodes[0]]+[v for v in nodes[1:-1] if v.get('visible') is not False]+[nodes[-1]]
        ticks=[t for a,b in zip(anchors,anchors[1:]) for t in range(a['t']+240,b['t'],240)]
        visible=[ms(v['t']) for v in nodes[1:-1] if v.get('visible') is not False]
        for i,tick in enumerate(ticks):
            now=ms(tick);bpm=next((v[2] for v in reversed(bases) if v[1]<=now),bases[0][2]);guard=15000/bpm
            if any(t-guard<=now<t for t in visible):continue
            if i==0 and guard>now-ms(nodes[0]['t']):continue
            if i==len(ticks)-1 and guard>ms(nodes[-1]['t'])-now:continue
            result.append(now)
    return [v/1000 for v in sorted(result)]


def timeline(score):
    """Density and skill windows use the same judgment-time sequence."""
    import math
    events=(score or {}).get('events',{});bpms=events.get('bpm',[])
    times=judged_times(score)
    if not times:return None
    duration=max(times)+2
    density=[0]*(math.ceil(duration*2)+1)
    for t in times:density[int(t*2)]+=1
    windows=[]
    for tick in events.get('skill',[]):
        start=seconds_at(tick,bpms)
        if start is None:continue
        count=sum(start<=t<start+5 for t in times)
        windows.append(dict(start=start,end=min(duration,start+5),count=count,percent=count/len(times)*100))
    for w in windows:w['rank']=1+sum(v['count']>w['count'] for v in windows)
    fever=[(seconds_at(a,bpms),seconds_at(b,bpms)) for a,b in events.get('fever',[])]
    return dict(duration=duration,density=density,windows=windows,fever=fever)


def render(song,score,locale='zh',*,load,band_logos=()):
    import math
    title=localized_text(song,'title',locale);tl=lines(title,862,58,True)
    band=localized_text(song,'band',locale);bl=lines(band,570,38)
    extra=max(0,len(tl)-1)*74+max(0,len(bl)-1)*50
    data=timeline(score);events=(score or {}).get('events',{});bpms=events.get('bpm',[])
    windows=data['windows'] if data else []
    timing_extra=max(0,math.ceil(len(windows)/3)-2)*86
    reward_rows=max(len(song.catalog.get('score_rewards',[])),len(song.catalog.get('combo_rewards',{}).get('expert',[])))
    height=2080+extra+timing_extra+max(0,reward_rows-5)*60
    c=Canvas(1400,height);c.header('歌曲详情',f'/查曲 {song.id}')
    images=[]
    def art(url,size):
        image=load(url,size);images.append(image);return image
    def section(y,title):
        c.line(54,y-38,1346);c.text(54,y,title,38,bold=True)
    try:
        c.paste(art(song.jacket_url,(386,386)),54,176,386,386,radius=16)
        c.text(247,596,f'ID {song.id}',32,MUTED,align='center')
        for i,t in enumerate(tl):c.text(484,204+i*74,t,58,bold=True)
        by=277+(len(tl)-1)*74
        logos=band_logos or song.catalog.get('band_logos',[])
        bx=left_logo(c,art(logos[0],(190,68)),484,by,190,68) if logos else 484
        for i,b in enumerate(bl):c.text(bx,by+i*50,b,38)
        if song.traits and song.traits.attribute_icon:
            from .haneoka_theme import font
            ax=min(1292,bx+font(38,False,band.isascii()).getlength(bl[-1])+24)
            c.paste(art(song.traits.attribute_icon,(54,54)),round(ax),by+(len(bl)-1)*50-27,54,54,True,trim=True)
        for i,(label,key) in enumerate((('作曲','composer'),('作词','lyricist'),('编曲','arranger'))):
            c.text(484,348+extra+i*58,label,30,MUTED)
            c.paragraph(584,329+extra+i*58,localized_text(song,key,locale) or '未获取',404,30,38)
        c.text(484,522+extra,'发布',30,MUTED);c.text(584,522+extra,str(song.start_at or '未获取')[:10].replace('-','/'),32)
        duration=data['duration']-2 if data else (song.catalog.get('duration_ms') or 0)/1000
        c.text(1004,348+extra,'时长',30,MUTED);c.text(1346,348+extra,f'{int(duration)//60}:{int(duration)%60:02}' if duration else '未获取',34,align='right')
        vals=[v['bpm'] for v in bpms];bpm=f'{min(vals):.2f}–{max(vals):.2f}' if vals and min(vals)!=max(vals) else f'{vals[0]:g}' if vals else '未获取'
        c.text(1004,406+extra,'BPM',30,MUTED);c.text(1346,406+extra,bpm,32,align='right')
        section(663+extra,'难度与 Note')
        for i,(name,(bg,fg)) in enumerate(DIFFICULTIES.items()):
            ch=next((v for v in song.charts if v.difficulty==name),None);cx=216+i*322
            c.box((cx-80,708+extra,cx+80,808+extra),bg,18)
            c.text(cx,758+extra,f'{ch.level}' if ch else '—',58,fg,True,'center')
            c.text(cx,850+extra,f'{ch.notes} Note' if ch and ch.notes is not None else 'Note 未获取',32,MUTED,align='center')
        section(938+extra,'技能点位');c.text(282,938+extra,'EXPERT · 5秒',28,'#bb4f81')
        left,right=54,1346;py=984+extra;pb=1204+extra
        c.box((left,py,right,pb),'#f6f3f8',16)
        if data:
            tx=lambda t:left+(right-left)*t/data['duration']
            for i,(a,b) in enumerate(data['fever']):
                if a is None or b is None:continue
                x0,x1=round(tx(a)),round(tx(b));c.draw.rectangle((x0,py,x1,pb),fill='#eee1ec');c.text(x0+12,py+22,f'激奏段 {i+1}',25,'#77536e')
            density=data['density'];peak=max(density) or 1
            pts=[(left,1118+extra)]+[(min(right,tx(i/2)),1118+extra-v/peak*86) for i,v in enumerate(density)]+[(right,1118+extra)]
            c.draw.polygon(pts,fill='#c0bdc6')
            for i,w in enumerate(windows):
                x0,x1=round(tx(w['start'])),round(tx(w['end']));c.box((x0,1132+extra,x1,1188+extra),ACCENT,7);c.text(x0+12,1160+extra,str(i+1),28,'white',True)
            for sec in range(0,math.ceil(data['duration']),30):c.text(tx(sec),1240+extra,f'{sec//60}:{sec%60:02}',26,MUTED,align='left' if sec==0 else 'center')
            for i,w in enumerate(windows):
                x=54+(i%3)*440;y=1298+extra+(i//3)*86;c.box((x,y-18,x+36,y+18),ACCENT,6);c.text(x+18,y,str(i+1),24,'white',True,'center')
                c.text(x+56,y,f"音符数 {w['count']}",29);c.text(x+394,y,f"{w['start']:.1f}s",28,MUTED,align='right')
                c.text(x+56,y+37,f"占比 {w['percent']:.1f}% · #{w['rank']}",28,MUTED)
        else:c.text(54,1100+extra,'技能点位资料未获取',32,MUTED)
        offset=extra+timing_extra;section(1500+offset,'激奏段')
        missions=song.traits.missions if song.traits else None
        for i,value in enumerate(missions or ('未获取',)):
            x=54+i*440;c.box((x,1540+offset,x+412,1604+offset),SURFACE,12);c.text(x+28,1572+offset,f'{i+1:02}',30,MUTED);mission_label(c,x+110,1572+offset,value,40,48,True)
        section(1660+offset,'奖励');c.text(54,1722+offset,'得分系数奖励',34,bold=True);c.text(752,1722+offset,'连击奖励',34,bold=True);c.text(916,1722+offset,'EXPERT',28,'#bb4f81')
        ranks={v['scoreRank']:v['requiredScore'] for v in song.catalog.get('score_ranks',[])}
        expert=next((v for v in song.charts if v.difficulty=='EXPERT'),None)
        for col,values in enumerate((song.catalog.get('score_rewards',[]),song.catalog.get('combo_rewards',{}).get('expert',[]))):
            for i,row in enumerate(values):
                x=54 if col==0 else 752;y=1784+offset+i*60;tier=row['tier']
                label=({3:'C',4:'B',5:'A',6:'S',7:'SS'} if col==0 else {0:'25%',1:'50%',2:'75%',3:'FULL COMBO'}).get(tier,str(tier));c.text(x,y,label,28,ACCENT,col==0)
                value=ranks.get(tier) if col==0 else math.ceil(expert.notes*(tier+1)/4) if expert and expert.notes and 0<=tier<=3 else None
                c.text(410 if col==0 else 1030,y,f'{value:,}' if value is not None else '未知',32,align='right')
                if row['image']:c.paste(art(row['image'],(42,42)),532 if col==0 else 1186,y-21,42,42,True,trim=True)
                c.text(636 if col==0 else 1346,y,f'×{row["count"]:,}',30,align='right')
        return encode_image(c.image,close_input=True,preserve_size=True)
    finally:
        for image in images:
            if image is not None:image.close()
