# L3
# Input: Captured ranking answer, source-scoped card catalogue and validated artwork.
# Output: Readable complete rank/trend parts with real score history and card markers.
# Pos: Rendering / Song rankings; approved overview and range geometry.
# Effects: Pillow rendering; no ranking/history queries or platform operations.
from dataclasses import replace
import math
from .haneoka_theme import Canvas,MUTED,ACCENT,SOFT,SURFACE,lines,font
from .catalog_layout import card_face, card_level as level
from .readable_pages import render_parts
from .cutoff_trends import segments,coordinates,COLORS
from .event_cutoff_visuals import CutoffPage


def chart_ranks(answer):
    values=answer.history_ranks or answer.ranks
    return tuple(dict.fromkeys(values[round(i*(len(values)-1)/4)] for i in range(5)))


def chart(c,answer,board,box,display_time):
    x,y,w,h=box;view=answer.history_for(board)
    c.text(x,y+22,'分数走势',30,bold=True);c.text(x+w,y+22,'分数（百万）',26,MUTED,align='right')
    values=[v for p in view.points for v in p.scores if v is not None]
    if not values:
        c.text(x+w/2,y+h/2,view.warning or '暂无历史观测',28,MUTED,align='center');return
    first,last=view.points[0].time_ms,view.points[-1].time_ms
    low,high=min(values),max(values);pad=max(1,(high-low)//8);low=max(0,low-pad);high+=pad
    plot=(x+96,y+72,w-106,h-148);bounds=(first,last,low,high)
    for i in range(4):
        value=low+(high-low)*i//3;py=plot[1]+plot[3]-(value-low)*plot[3]//max(1,high-low)
        c.line(plot[0],py,plot[0]+plot[2],color='#e5e0e9');c.text(plot[0]-18,py,f'{value/1000000:.{min(6,max(2,math.ceil(-math.log10(max(1,high-low)/1000000))+1))}f}',24,MUTED,align='right')
    for i,rank in enumerate(chart_ranks(answer)):
        if not view.points or i>=len(view.points[0].scores):continue
        for group in segments(view,i):
            pts=[coordinates(t,s,bounds,plot) for t,s in group];color=COLORS[i%len(COLORS)]
            if len(pts)>1:c.draw.line(pts,fill=color,width=3)
            if pts:
                px,py=pts[-1];c.draw.ellipse((px-3,py-3,px+3,py+3),fill=color)
    for stamp,px,align in ((first,plot[0],'left'),(last,plot[0]+plot[2],'right')):
        label=display_time(stamp,answer.event.server)
        c.text(px,y+h-45,label[5:10],22,MUTED,align=align)
        c.text(px,y+h-16,label[11:16],22,MUTED,align=align)
    if len(view.points)==1:c.text(x+110,y+h-12,'仅 1 个历史点，尚未形成曲线',22,MUTED)


def render(answer,*,assets,load,display_time,server_label,timezone_label,catalogs=({},{}),bonus_marks=()):
    if answer.event is None or answer.message or answer.request.numeric_only or not answer.boards:return ()
    trend=answer.request.mode=='trend';boards=answer.boards
    width=2600 if trend else (1080 if len(boards)==1 else 1800)
    member,support=catalogs;cache={}
    def art(url,size):
        if not url:return None
        key=(url,size)
        if key not in cache:cache[key]=load(url,size)
        return cache[key]
    header_offset=max(0,146+len(lines('/'+answer.request.command_label(),width-108,24))*34-170)
    event=answer.event;title=lines(event.title,width-1010 if trend else width-580,44,True)
    banner_width,banner_height=(840,350) if trend else (420,180)
    text_x=108+banner_width
    period=display_time(event.start_ms,event.server)+'\n'+display_time(event.end_ms,event.server)+' '+timezone_label
    period_top=182+header_offset+len(title)*58+58
    period_height=len(lines(period,width-text_x-54,28))*38
    bonus_top=period_top+period_height+46
    bonus_rows=1 if width-text_x-54>=570 else len(bonus_marks)
    head=max((600 if trend else 420)+header_offset,bonus_top+bonus_rows*78+40)
    event_head=head
    footer=[' · '.join((b.song.title,b.status,'采集 '+display_time(b.fetched_ms,event.server))) for b in boards]
    footer += [n for b in boards for n in b.notes]+list(event.notes)
    footer += [answer.history_for(b).warning for b in boards if answer.history_for(b).warning]
    footer += [answer.source_label+' · 非官方 · 挑战歌曲 Top 100 · 非预测、非确认终榜']
    footlines=tuple(l for v in footer for l in lines(v,width-108,24));footheight=len(footlines)*32+38
    def score_text(board,rank):
        value=answer.score_label(board,rank)
        return f'{int(value):,}' if value.isdigit() else value
    def player_info(board,rank):
        detail=board.player_detail(rank) or {};exp=detail.get('rankExp')
        table=next(iter(member.values())).catalog.get('player_levels',[]) if member else ()
        ranks=[r['rank'] for r in table if type(exp) is int and r['exp']<=exp]
        values=([f'等级 {max(ranks)}'] if ranks else [])
        if detail.get('totalPower') is not None:values.append(f'综合力 {detail["totalPower"]:,}')
        return ' · '.join(values)
    def avatar(c,board,rank,x,y,size):
        detail=board.player_detail(rank) or {};card=member.get(detail.get('favoriteMemberCardId'))
        if card:
            slot=next((v for v in detail.get('cards',[]) if v.get('memberCardId')==card.id),{})
            card_face(c,card,x,y,size,size,art,level=level(card,slot.get('memberExp'),slot.get('memberAwakeCount')),awake=slot.get('memberAwakeCount'))
        else:c.box((x,y,x+size,y+size));c.text(x+size/2,y+size/2,'?',28,MUTED,align='center')
    def header(c):
        c.header('歌曲榜线总览' if trend else '歌曲榜线','/'+answer.request.command_label())
        bw,bh=(840,350) if trend else (420,180)
        c.paste(assets.get(event.banner),54,170+header_offset,bw,bh,True)
        tx=54+bw+54;ty=182+header_offset
        for line in title:c.text(tx,ty+22,line,44,bold=True);ty+=58
        c.text(tx,ty+24,f'{server_label} · 活动 {event.event_id}',30,ACCENT);ty+=58
        c.paragraph(tx,ty,period,width-tx-54,28,38,color=MUTED)
        bx=tx;by=bonus_top
        for label,url in bonus_marks:
            c.text(bx,by,label,28,MUTED)
            bw=64 if label=='加成属性' else 190
            c.paste(art(url,(bw,64)),round(bx+134),round(by-32),bw,64,True)
            if bonus_rows>1:by+=78
            else:bx+=160+bw
        c.line(54,event_head-48,width-54)
        for i,rank in enumerate(chart_ranks(answer) if trend else ()):
            x=width-920+i*174;c.draw.line((x,head-20,x+35,head-20),fill=COLORS[i%5],width=4);c.text(x+44,head-20,f'#{rank}',26,MUTED)
    blocks=[]
    if trend:
        for board in boards:
            th=max(126,len(lines(answer.song_label(board),width-270,42,True))*56+24)
            blocks.append(('song',th,board))
            for rank in answer.ranks:
                name=lines(answer.name_label(board,rank),450,34)
                identity=lines('ID '+answer.id_label(board,rank),650,26)
                blocks.append(('rank',max(164,32+len(name)*42+len(identity)*34), (board,rank)))
            blocks.append(('chart',660 if answer.history_for(board).points else 180,board))
    else:
        cw=(width-200)/len(boards)
        columns=max(max(len(lines(answer.song_label(b),int(cw)-28,30,True)) for b in boards)*40+30,126)
        def name_width(board,rank):
            score_width=font(32,True,True).getlength(score_text(board,rank))
            return max(90,int(cw)-144-int(score_width))
        for rank in answer.ranks:
            rh=max(max(len(lines(answer.name_label(b,rank),name_width(b,rank),28))*36+
                       len(lines('ID '+answer.id_label(b,rank),int(cw)-124,24))*32+44 for b in boards),112)
            blocks.append(('table',rh,rank))
        plot_height=404 if any(answer.history_for(b).points for b in boards) else 180
        head+=columns+plot_height+88
    def paint(start,stop,height):
        c=Canvas(width,height);header(c);y=head
        if not trend:
            for i,b in enumerate(boards):
                x=156+i*cw
                c.box((x-8,event_head-8,x+cw-18,head-74),'white',18)
                c.paste(assets.get(b.song.jacket),round(x),event_head+8,72,72,True)
                c.paragraph(x+86,event_head+8,answer.song_label(b),int(cw)-110,30,40,bold=True)
                chart(c,answer,b,(x,event_head+columns,int(cw)-34,plot_height),display_time)
            ranks=chart_ranks(answer)
            span=min(174,(width-200)/len(ranks));left=(width-span*len(ranks))/2
            for i,rank in enumerate(ranks):
                x=left+i*span;c.draw.line((x,head-42,x+28,head-42),fill=COLORS[i%5],width=4)
                c.text(x+38,head-42,f'#{rank}',26,MUTED)
        elif start and blocks[start][0] not in ('song',):
            board=blocks[start][2][0] if blocks[start][0]=='rank' else blocks[start][2]
            c.text(54,head-20,answer.song_label(board)+'（续）',26,MUTED)
        for kind,bh,data in blocks[start:stop]:
            if kind=='song':
                c.box((54,y,width-54,y+bh-14),SURFACE,16);c.paste(assets.get(data.song.jacket),66,y+12,96,96,True)
                c.paragraph(188,y+20,answer.song_label(data),width-270,42,56,bold=True)
            elif kind=='chart':chart(c,answer,data,(148,y,width-202,bh),display_time)
            elif kind=='rank':
                board,rank=data;detail=board.player_detail(rank) or {};color=COLORS[answer.ranks.index(rank)%5]
                c.draw.line((40,y+18,40,y+bh-18),fill=color,width=5);c.text(94,y+62,f'#{rank}',32,color,align='center')
                avatar(c,board,rank,148,y+18,100)
                c.paragraph(276,y+16,answer.name_label(board,rank),450,34,42,bold=True)
                c.paragraph(276,y+bh-66,'ID '+answer.id_label(board,rank),650,26,34,color=MUTED)
                c.text(1028,y+38,score_text(board,rank),42,bold=True,align='right')
                if player_info(board,rank):c.text(1028,y+94,player_info(board,rank),24,MUTED,align='right')
                for i,slot in enumerate(detail.get('cards',[])):
                    mc=member.get(slot.get('memberCardId'));sc=support.get(slot.get('supportCardId'))
                    if mc:card_face(c,mc,1070+i*104,y+18,94,124,art,level=level(mc,slot.get('memberExp'),slot.get('memberAwakeCount')),awake=slot.get('memberAwakeCount'))
                    if sc:card_face(c,sc,1610+i*186,y+30,174,98,art,level=level(sc,slot.get('supportExp'),slot.get('supportRank')),awake=slot.get('supportRank'))
                if not detail.get('cards'):c.text(1070,y+74,'卡组未获取',28,MUTED)
            else:
                rank=data
                if rank==answer.request.target_rank:c.box((48,y,width-48,y+bh),SOFT,8)
                c.text(100,y+36,f'#{rank}',30,ACCENT,align='center')
                for i,b in enumerate(boards):
                    x=156+i*cw;avatar(c,b,rank,round(x),y+12,76)
                    ty=c.paragraph(x+92,y+8,answer.name_label(b,rank),name_width(b,rank),28,36,bold=True)
                    ty=c.paragraph(x+92,ty,'ID '+answer.id_label(b,rank),int(cw)-124,24,32,color=MUTED)
                    info=player_info(b,rank)
                    if info:c.paragraph(x+92,ty,info,int(cw)-124,22,30,color=MUTED)
                    c.text(x+cw-24,y+26,score_text(b,rank),32,bold=True,align='right')
                c.line(54,y+bh,width-54,'#e5e0e9')
            y+=bh
        c.line(54,y+10,width-54)
        for i,line in enumerate(footlines):c.text(54,y+34+i*32,line,24,MUTED)
        return c.image
    try:
        parts=render_parts(tuple(b[1] for b in blocks),width,head+footheight,paint)
        return tuple(CutoffPage(p.image,answer.text if trend else replace(answer,display_ranks=tuple(b[2] for b in blocks[p.start:p.stop])).text) for p in parts)
    finally:
        for image in cache.values():
            if image is not None:image.close()
