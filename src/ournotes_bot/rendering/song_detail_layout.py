# L3
# Input: Captured song/charts and optional validated score events.
# Output: Approved song detail layout with source skill timing on a real time axis.
# Pos: Rendering / Song details.
# Effects: Pillow and injected asset loading only.
from ..data import localized_text
from .haneoka_theme import Canvas,MUTED,DIFFICULTIES,SURFACE,ACCENT,lines
from .image_output import encode_image


def seconds_at(tick,bpms):
    rows=sorted(bpms,key=lambda e:e['t'])
    if not rows or rows[0]['t']!=0 or any(e['bpm']<=0 for e in rows):return None
    total=0
    for i,row in enumerate(rows):
        if row['t']>=tick:break
        end=min(tick,rows[i+1]['t'] if i+1<len(rows) else tick)
        total+=(end-row['t'])/480*60/row['bpm']
    return total


def render(song,score,locale='zh',*,load,band_logos=()):
    title=localized_text(song,'title',locale);tl=lines(title,770,56,True)
    band=localized_text(song,'band',locale);bl=lines(band,850,36)
    top=max(520,192+len(tl)*72+len(bl)*48+(144 if band_logos else 70))
    top=max(top,520)
    reward_rows=max(len(song.catalog.get('score_rewards',[])),len(song.catalog.get('combo_rewards',{}).get('expert',[])))
    height=top+1140+reward_rows*60
    c=Canvas(1400,height);c.header('歌曲详情',f'/查曲 {song.id}')
    images=[]
    def art(url,size):
        image=load(url,size);images.append(image);return image
    try:
        c.paste(art(song.jacket_url,(320,320)),54,170,320,320)
        y=192
        for line in tl:c.text(426,y+28,line,56,bold=True);y+=72
        for line in bl:c.text(426,y+24,line,36,MUTED);y+=48
        if band_logos:c.paste(art(band_logos[0],(220,80)),426,y+12,220,80,True)
        if song.traits and song.traits.attribute_icon:c.paste(art(song.traits.attribute_icon,(64,64)),1260,176,64,64,True)
        c.text(426,top-24,f'ID {song.id}',30,MUTED)
        top=max(top,520);c.line(54,top,1346)
        for i,(name,(bg,fg)) in enumerate(DIFFICULTIES.items()):
            ch=next((v for v in song.charts if v.difficulty==name),None);x=54+i*326
            c.box((x,top+28,x+304,top+176),bg,18);c.text(x+152,top+65,name,30,fg,True,'center')
            c.text(x+152,top+129,f'{ch.display_level:g}' if ch else '—',64,fg,True,'center')
            c.text(x+152,top+219,f'{ch.notes} Note' if ch and ch.notes is not None else 'Note 未获取',30,MUTED,align='center')
        events=(score or {}).get('events',{});bpms=events.get('bpm',[])
        missions=song.traits.missions if song.traits else None
        c.line(54,top+270,1346);c.text(54,top+320,'激奏顺序',36,bold=True)
        c.text(54,top+380,' → '.join(missions) if missions else '未获取',38,ACCENT)
        c.line(54,top+432,1346);c.text(54,top+482,'技能点位',38,bold=True)
        ticks=events.get('skill',[])
        notes=(score or {}).get('notes',[]);last=max((v['t'] for n in notes for v in (n.get('node',[]) if n.get('type') in ('long','guide') else [n])),default=0)
        duration=seconds_at(last,bpms) if last else None
        if duration:
            left,right=88,1300;y=top+588;c.line(left,y,right)
            for index,tick in enumerate(ticks):
                sec=seconds_at(tick,bpms)
                if sec is None:continue
                x=left+(right-left)*min(1,sec/duration)
                c.draw.line((x,y-34,x,y+34),fill=ACCENT,width=4)
                c.text(x,y-61,f'{index+1}',28,ACCENT,True,'center')
                c.text(x,y+68,f'{sec:.1f}s',25,MUTED,align='center')
            c.text(left,y+126,'0:00',26,MUTED);c.text(right,y+126,f'{int(duration)//60}:{int(duration)%60:02}',26,MUTED,align='right')
        else:c.text(54,top+580,'技能点位资料未获取',32,MUTED)
        c.line(54,top+800,1346);c.text(54,top+852,'BPM',32,MUTED)
        if bpms:
            vals=[v['bpm'] for v in bpms];text=f'{min(vals):.2f}–{max(vals):.2f}' if min(vals)!=max(vals) else f'{min(vals):g}'
        else:text='未获取'
        c.text(54,top+918,text,48,bold=True)
        duration_ms=song.catalog.get('duration_ms')
        if type(duration_ms) in (int,float) and duration_ms>0:
            c.text(740,top+852,'乐曲时长',32,MUTED)
            c.text(740,top+918,f'{int(duration_ms)//60000}:{int(duration_ms)//1000%60:02}',48,bold=True)
        if reward_rows:
            c.line(54,top+978,1346)
            c.text(54,top+1022,'得分系数奖励',36,bold=True)
            c.text(740,top+1022,'连击奖励 · EXPERT',36,bold=True)
            ranks={v['scoreRank']:v['requiredScore'] for v in song.catalog.get('score_ranks',[])}
            for col,values in enumerate((song.catalog.get('score_rewards',[]),song.catalog.get('combo_rewards',{}).get('expert',[]))):
                for i,row in enumerate(values):
                    x=54+col*686;y=top+1080+i*60;tier=row['tier']
                    label=({3:'C',4:'B',5:'A',6:'S',7:'SS'} if col==0 else {0:'25%',1:'50%',2:'75%',3:'FULL COMBO'}).get(tier,str(tier))
                    c.text(x,y,label,28,ACCENT,True)
                    if col==0:c.text(x+340,y,f'{ranks[tier]:,}' if tier in ranks else '未知',30,align='right')
                    if row['image']:c.paste(art(row['image'],(42,42)),x+396,y-21,42,42,True)
                    c.text(x+586,y,f'×{row["count"]:,}',30,align='right')
        return encode_image(c.image,close_input=True,preserve_size=True)
    finally:
        for image in images:
            if image is not None:image.close()
