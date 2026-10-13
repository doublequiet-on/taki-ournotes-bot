# L3
# Input: Validated source score, song and selected difficulty.
# Output: Original-note dark-track images; complete columns, bottom-to-top then left-to-right.
# Pos: Rendering / Song; approved sparse full-chart layout.
# Effects: Pillow and injected artwork; measured native-size encoding, no data queries.
import math
from PIL import Image,ImageDraw
from ..data import localized_text
from .haneoka_theme import Canvas,MUTED,DIFFICULTIES,lines
from .image_output import encode_image,ImageBudgetError,MAX_IMAGE_PIXELS,MAX_IMAGE_EDGE
from .note_skin import draw_note


def render(song,charts,score,preview,locale='zh',*,load,notice=''):
    notes=(score or {}).get('notes',[])
    nodes=[v for n in notes for v in (n.get('node',[]) if n.get('type') in ('long','guide') else [n])]
    bars=math.ceil(max((v['t'] for v in nodes),default=0)/1920)+1
    columns=math.ceil(bars/8) if nodes else 0
    selected=next((c for c in charts if c.difficulty==preview),charts[-1] if charts else None)
    assets={}
    def art(url,size):
        key=(url,size)
        if key not in assets:assets[key]=load(url,size)
        return assets[key]
    def paint(first,count):
        width=max(1400,count*420+60);title=localized_text(song,'title',locale)
        titlewidth=width-1000;titlelines=lines(title,max(340,titlewidth),72,True)
        header=max(800,620+len(titlelines)*90);tracktop=header+110;trackbottom=tracktop+3840
        height=trackbottom+110 if nodes else header+130
        c=Canvas(width,height);c.text(60,66,'谱面详情',50,bold=True)
        c.paste(art(song.jacket_url,(260,260)),60,154,260,260)
        for i,line in enumerate(titlelines):c.text(374,210+i*90,line,72,bold=True)
        by=234+len(titlelines)*90
        logos=song.catalog.get('band_logos',[])
        if len(logos)==1:c.paste(art(logos[0],(220,70)),374,by,220,70,True)
        else:c.paragraph(374,by,localized_text(song,'band',locale),max(340,titlewidth),40,54,color=MUTED)
        c.text(374,by+112,f'ID {song.id}',32,MUTED)
        if selected:
            bg,fg=DIFFICULTIES[selected.difficulty];x=width-430;c.box((x,154,width-60,414),bg,24)
            c.text(x+185,214,selected.difficulty,44,fg,True,'center');c.text(x+185,324,f'{selected.display_level:g}',100,fg,True,'center')
        c.line(60,header-180,width-60)
        bpms=[e['bpm'] for e in (score or {}).get('events',{}).get('bpm',[]) if e.get('bpm')]
        bpm=(f'{min(bpms):g}' if min(bpms)==max(bpms) else f'{min(bpms):.2f}–{max(bpms):.2f}') if bpms else '未获取'
        metrics=[('BPM',bpm),('音符数',str(selected.notes) if selected and selected.notes is not None else '未获取'),('难度',preview or '未指定')]
        for i,(label,value) in enumerate(metrics):
            x=60+i*(width-120)/3;c.text(x,header-128,label,30,MUTED);c.text(x,header-65,value,48,bold=True)
        c.line(60,header-16,width-60);c.text(60,header+38,'完整谱面',38,bold=True)
        c.text(width-60,header+38,'每列 8 小节 · 下 → 上 · 左 → 右',28,MUTED,align='right')
        if not nodes:
            c.text(60,header+98,'完整谱面暂不可用',30,MUTED);return c.image
        for local,col in enumerate(range(first,first+count)):
            x=100+local*420;w=336;lo=col*8*1920;hi=(col+1)*8*1920
            c.text(x,tracktop-28,f'{col+1:02} / 小节 {col*8+1:02}–{min((col+1)*8,bars):02}',26,MUTED)
            c.box((x-12,tracktop-4,x+w+12,trackbottom+14),'#141723',12)
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
                    kind='tap'
                    if n.get('type')=='long':kind='slide-start' if i==0 else 'slide-end' if i==len(seq)-1 else 'slide-node'
                    if node.get('type')=='flick' or n.get('type')=='flick':
                        direction=node.get('dir',n.get('dir'));kind='flick-left' if direction=='left' else 'flick-right' if direction=='right' else 'flick'
                    draw_note(c,x+(node['pos']+node['size']/2)/24*w,trackbottom-(node['t']-lo)/1920*480,node['size']/24*w,kind,node['size'])
        c.text(60,trackbottom+66,f'列 {first+1}–{first+count} / {columns} · '+notice,24,MUTED)
        return c.image
    result=[];first=0
    try:
        if not columns:return (encode_image(paint(0,0),close_input=True,preserve_size=True),)
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
