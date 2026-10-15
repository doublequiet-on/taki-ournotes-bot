# L3
# Input: Captured ranking answer, source-scoped card catalogue and validated artwork.
# Output: Readable complete rank/trend parts with real score history and card markers.
# Pos: Rendering / Song rankings; approved overview and range geometry.
# Effects: Pillow rendering; no ranking/history queries or platform operations.
from dataclasses import replace
import math
from .haneoka_theme import Canvas,MUTED,ACCENT,SOFT,SURFACE,lines,font,icon,prepare_assets
from .catalog_layout import card_face, card_level as level
from .readable_pages import render_parts
from .cutoff_trends import segments,coordinates,COLORS
from .event_cutoff_visuals import CutoffPage


def rank_marker(c,rank,x,y,size,color=ACCENT):
    name={1:'ImageRanking1st.png',2:'ImageRanking2ng.png',3:'ImageRanking3rd.png'}.get(rank)
    mark=icon(name,size,size) if name else None
    if mark is not None:c.paste(mark,round(x-size/2),round(y-size/2),size,size,True)
    else:c.text(x,y,f'#{rank}',30 if size<70 else 40,color,align='center')


def chart_ranks(answer):
    values=answer.history_ranks or answer.ranks
    return tuple(dict.fromkeys(values[round(i*(len(values)-1)/4)] for i in range(5)))


def chart(c,answer,board,box,display_time,heading=True):
    x,y,w,h=box;view=answer.history_for(board)
    if heading:c.text(x,y+22,'分数走势',38 if w>1500 else 26,bold=True)
    c.text(x+w,y+(22 if heading else 53),'分数（百万）',32 if w>1500 else 24,MUTED,align='right')
    values=[v for p in view.points for v in p.scores if v is not None]
    if not values:
        message=lines('暂无历史观测',w-24,26)
        c.paragraph(x+12,y+max(72,(h-len(message)*36)/2),'暂无历史观测',w-24,26,36,color=MUTED)
        return
    first,last=view.points[0].time_ms,view.points[-1].time_ms
    low,high=min(values),max(values);pad=max(1,(high-low)//8);low=max(0,low-pad);high+=pad
    plot=(x if w>1500 else x+76,y+(58 if w>1500 else 76),w if w>1500 else w-86,h-(126 if w>1500 else 172));bounds=(first,last,low,high)
    for i in range(4):
        value=low+(high-low)*i//3;py=plot[1]+plot[3]-(value-low)*plot[3]//max(1,high-low)
        c.line(plot[0],py,plot[0]+plot[2],color='#e5e0e9');c.text(plot[0]-18,py,f'{value/1000000:.{min(6,max(2,math.ceil(-math.log10(max(1,high-low)/1000000))+1))}f}',34 if w>1500 else 24,MUTED,align='right')
    for i,rank in enumerate(chart_ranks(answer)):
        if not view.points or i>=len(view.points[0].scores):continue
        for group in segments(view,i):
            pts=[coordinates(t,s,bounds,plot) for t,s in group];color=COLORS[i%len(COLORS)]
            if len(pts)>1:c.draw.line(pts,fill=color,width=5 if w>1500 else 3)
            if pts:
                px,py=pts[-1];c.draw.ellipse((px-3,py-3,px+3,py+3),fill=color)
    for stamp,px,align in ((first,plot[0],'left'),((first+last)//2,plot[0]+plot[2]/2,'center'),(last,plot[0]+plot[2],'right')):
        label=display_time(stamp,answer.event.server)
        c.text(px,y+h-45,label[5:10],34 if w>1500 else 22,MUTED,align=align)
        c.text(px,y+h-16,label[11:16],32 if w>1500 else 22,MUTED,align=align)
    if len(view.points)==1:c.text(x+110,y+h-12,'仅 1 个历史点，尚未形成曲线',22,MUTED)


def render(answer,*,assets,load,display_time,server_label,timezone_label,catalogs=({},{}),bonus_marks=(),song_bands=None):
    if answer.event is None or answer.message or answer.request.numeric_only or not answer.boards:return ()
    trend=answer.request.mode=='trend';boards=answer.boards
    width=2600 if trend else (1080 if len(boards)==1 else 1800)
    member,support=catalogs;cache={};song_bands=song_bands or {}
    def song_title(board):return board.song.title if answer.event.challenge_order_verified else answer.song_label(board)
    def art(url,size):
        if not url:return None
        key=(url,size)
        if key not in cache:cache[key]=load(url,size)
        return cache[key]
    header_offset=max(0,146+len(lines('/'+answer.request.command_label(),width-108,24))*34-170)
    event=answer.event;title=lines(event.title,width-1010 if trend else (760 if width==1800 else width-580),64 if trend else 42,True)
    banner_width,banner_height=(840,360) if trend else (420,180)
    text_x=108+banner_width
    period=display_time(event.start_ms,event.server)[5:16]+' — '+display_time(event.end_ms,event.server)[5:16]+'  ('+timezone_label+')'
    period_top=182+header_offset+len(title)*(78 if trend else 58)+(76 if trend else 58)
    period_height=len(lines(period,width-text_x-54,42 if trend else 30))*(54 if trend else 38)
    bonus_top=period_top+period_height+46
    bonus_rows=1 if width-text_x-54>=570 else len(bonus_marks)
    head=max((632 if trend else 368)+header_offset,bonus_top+bonus_rows*78+40)
    if not trend and width==1800:head=max(368+header_offset,period_top+period_height+28)
    if not trend and width==1080:head=max(480+header_offset,182+len(title)*58+260)
    event_head=head
    footer=[' · '.join((b.song.title,b.status,'采集 '+display_time(b.fetched_ms,event.server))) for b in boards]
    footer += [n for b in boards for n in b.notes]+list(event.notes)
    footer += [answer.history_for(b).warning for b in boards if answer.history_for(b).warning]
    footer=list(dict.fromkeys(footer))
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
        bw,bh=(840,360) if trend else (420,180)
        c.paste(assets.get(event.banner),54,160+header_offset,bw,bh,False,radius=16)
        tx=54+bw+54;ty=182+header_offset
        for line in title:c.text(tx,ty+24,line,64 if trend else 42,bold=True);ty+=78 if trend else 58
        c.text(tx,ty+24,f'{server_label} · 活动 {event.event_id}',44 if trend else 30,ACCENT);ty+=76 if trend else 58
        c.paragraph(tx,ty,period if width!=1080 else display_time(event.start_ms,event.server)[5:10]+' — '+display_time(event.end_ms,event.server)[5:10]+' · '+timezone_label,width-tx-54,42 if trend else 30,54 if trend else 38,color=MUTED)
        bx=tx if trend else 54 if width<1800 else 1350;by=max(466+header_offset,182+header_offset+len(title)*78+76+period_height+46) if trend else 204+header_offset if width==1800 else event_head-78
        for label,url in bonus_marks:
            c.text(bx,by,label,44 if trend else 36,MUTED)
            bw=(102 if trend else 64) if label=='加成属性' else (420 if trend else 300 if width==1080 else 210)
            bh=114 if trend else 72;c.paste(art(url,(bw,bh)),round(bx+(210 if trend else 180)),round(by-bh/2),bw,bh,True,trim=True)
            if not trend and width==1800:by+=86
            elif trend and bonus_rows>1:by+=86
            else:bx+=380 if trend else 310
        c.line(54,event_head-(12 if width==1800 else 48),width-54)
        for i,rank in enumerate(chart_ranks(answer) if trend else ()):
            x=1690+i*170;c.draw.line((x,head-20,x+35,head-20),fill=COLORS[i%5],width=4);c.text(x+44,head-20,f'#{rank}',34,MUTED)
    blocks=[]
    if trend:
        for board in boards:
            th=max(148,len(lines(song_title(board),width-270,54,True))*64+76)
            blocks.append(('song',th,board))
            for rank in answer.ranks:
                name=lines(answer.name_label(board,rank),450,42)
                identity=lines('ID '+answer.id_label(board,rank),650,26)
                blocks.append(('rank',max(156,24+len(name)*50+len(identity)*38), (board,rank)))
            blocks.append(('chart',780 if answer.history_for(board).points else 180,board))
    else:
        cw=(width-200)/len(boards)
        title_heights={b.song.music_id:len(lines(song_title(b),int(cw)-110,30,True))*40 for b in boards}
        columns=max(max(title_heights.values())+60,126)
        def name_width(board,rank):
            score_width=font(32,True,True).getlength(score_text(board,rank))
            return max(90,int(cw)-144-int(score_width))
        for rank in answer.ranks:
            rh=max(max(len(lines(answer.name_label(b,rank),name_width(b,rank),36 if width==1080 else 28))*36+
                       len(lines('ID '+answer.id_label(b,rank),int(cw)-124,24))*32+44 for b in boards),112)
            blocks.append(('table',rh,rank))
        plot_height=404 if any(answer.history_for(b).points for b in boards) else 180
        head+=columns+plot_height+88
    def paint(start,stop,height):
        c=Canvas(width,height);header(c);y=head
        if not trend:
            for i,b in enumerate(boards):
                x=156+i*cw
                if width!=1080:c.box((x-8,event_head-8,x+cw-18,head-74),'white',18)
                c.paste(assets.get(b.song.jacket),round(x),event_head+8,72,72,radius=10)
                c.paragraph(x+86,event_head+8,song_title(b),int(cw)-110,30,40,bold=True)
                band=song_bands.get(str(b.song.music_id),'')
                if band:c.text(x+86,event_head+title_heights[b.song.music_id]+32,band,26,MUTED)
                chart(c,answer,b,(x,event_head+columns,int(cw)-34,plot_height),display_time,heading=width!=1080)
            ranks=chart_ranks(answer)
            span=min(174,(width-200)/len(ranks));left=(width-span*len(ranks))/2
            for i,rank in enumerate(ranks):
                x=left+i*span;ly=event_head+columns+22 if width==1080 else head-42;c.draw.line((x,ly,x+28,ly),fill=COLORS[i%5],width=4)
                c.text(x+38,ly,f'#{rank}',26,MUTED)
            c.text(100,head-18,'排名',26,MUTED,align='center')
            for i in range(len(boards)):
                c.text(156+i*cw,head-18,'玩家',26,MUTED);c.text(156+(i+1)*cw-24,head-18,'分数',26,MUTED,align='right')
        elif start and blocks[start][0] not in ('song',):
            board=blocks[start][2][0] if blocks[start][0]=='rank' else blocks[start][2]
            c.text(54,head-20,answer.song_label(board)+'（续）',26,MUTED)
        for kind,bh,data in blocks[start:stop]:
            if kind=='song':
                c.box((54,y,width-54,y+bh-14),SURFACE,16);c.paste(assets.get(data.song.jacket),66,y+8,112,112,radius=12)
                c.paragraph(206,y+6,song_title(data),width-270,54,64,bold=True)
                band=song_bands.get(str(data.song.music_id),'')
                if band:c.text(206,y+92+max(0,len(lines(song_title(data),width-270,54,True))-1)*64,band,38,MUTED)
            elif kind=='chart':chart(c,answer,data,(148,y,width-216,bh-24),display_time)
            elif kind=='rank':
                board,rank=data;detail=board.player_detail(rank) or {};color=COLORS[answer.ranks.index(rank)%5]
                c.draw.line((40,y+18,40,y+bh-18),fill=color,width=5);rank_marker(c,rank,94,y+78,72,color)
                avatar(c,board,rank,148,y+28,96)
                c.paragraph(274,y+23,answer.name_label(board,rank),450,42,50,bold=True)
                c.paragraph(274,y+bh-70,'ID '+answer.id_label(board,rank),650,32,38,color=MUTED)
                c.text(1030,y+48,score_text(board,rank),50,bold=True,align='right')
                if player_info(board,rank):c.text(1030,y+103,player_info(board,rank),32,MUTED,align='right')
                for i,slot in enumerate(detail.get('cards',[])):
                    mc=member.get(slot.get('memberCardId'));sc=support.get(slot.get('supportCardId'))
                    if mc:card_face(c,mc,1070+i*100,y+20,90,120,art,level=level(mc,slot.get('memberExp'),slot.get('memberAwakeCount')),awake=slot.get('memberAwakeCount'))
                    if sc:card_face(c,sc,1604+i*188,y+31,176,99,art,level=level(sc,slot.get('supportExp'),slot.get('supportRank')),awake=slot.get('supportRank'))
                if not detail.get('cards'):c.text(1070,y+74,'卡组未获取',28,MUTED)
            else:
                rank=data
                if rank!=answer.request.target_rank and answer.ranks.index(rank)%2==0:c.box((48,y,width-48,y+bh),'#f7f4f9',0)
                if rank==answer.request.target_rank:c.box((48,y,width-48,y+bh),SOFT,8)
                rank_marker(c,rank,100,y+42,64)
                for i,b in enumerate(boards):
                    x=156+i*cw;avatar(c,b,rank,round(x),y+14,84 if width==1080 else 76)
                    ty=c.paragraph(x+(118 if width==1080 else 92),y+(8 if width==1080 else 14),answer.name_label(b,rank),name_width(b,rank),36 if width==1080 else 28,40,bold=True)
                    ty=c.paragraph(x+(118 if width==1080 else 92),ty,'ID '+answer.id_label(b,rank),int(cw)-124,28 if width==1080 else 24,32,color=MUTED)
                    info=player_info(b,rank)
                    if info:
                        if width==1080:
                            left_info,*right_info=info.split(' · ',1)
                            c.text(x+118,y+93,left_info,26,MUTED)
                            if right_info:c.text(x+cw-24,y+84,right_info[0],28,MUTED,align='right')
                        else:c.paragraph(x+92,ty,info,int(cw)-124,22,30,color=MUTED)
                    c.text(x+cw-24,y+(28 if width==1080 else 34),score_text(b,rank),42 if width==1080 else 32,bold=True,align='right')
                c.line(54,y+bh,width-54,'#e5e0e9')
            y+=bh
        c.line(54,y+10,width-54)
        for i,line in enumerate(footlines):c.text(54,y+34+i*32,line,24,MUTED)
        return c.image
    requests=set();badges={'ImageRanking1st.png','ImageRanking2ng.png','ImageRanking3rd.png'}
    def request_card(card,w,h):
        requests.add((card.full_url if w<h else card.thumbnail_url,(w,h)))
        attr=card.catalog.get('attribute_icon')
        if attr:
            side=max(18,min(40,w//4));requests.add((attr,(side,side)))
    for board in boards:
        for rank in answer.ranks:
            detail=board.player_detail(rank) or {}
            favorite=member.get(detail.get('favoriteMemberCardId'))
            side=96 if trend else 84 if width==1080 else 76
            if favorite:request_card(favorite,side,side)
            if trend:
                for slot in detail.get('cards',[]):
                    mc=member.get(slot.get('memberCardId'));sc=support.get(slot.get('supportCardId'))
                    if mc:request_card(mc,90,120)
                    if sc:request_card(sc,176,99)
            for slot in detail.get('cards',[]):
                for prefix,key in (('CardRank','memberAwakeCount'),('snaplimit_','supportRank')):
                    value=slot.get(key)
                    if type(value) is int and 0<=value<=5:badges.add(f'{prefix}{value}.png')
    for label,url in bonus_marks:
        bw=(102 if trend else 64) if label=='加成属性' else (420 if trend else 300 if width==1080 else 210)
        requests.add((url,(bw,114 if trend else 72)))
    try:
        cache.update(prepare_assets(requests,load,badges))
        groups=[i for i,b in enumerate(blocks) if b[0]=='song']+[len(blocks)] if trend else []
        from .image_output import MAX_IMAGE_EDGE,MAX_IMAGE_PIXELS
        if trend:
            # Recover vertical space from plots before splitting complete song groups.
            # Player rows, card art, text and every history point keep their size/data.
            excess=max(0,head+footheight+sum(b[1] for b in blocks)-min(MAX_IMAGE_EDGE,MAX_IMAGE_PIXELS//width))
            plots=[i for i,b in enumerate(blocks) if b[0]=='chart' and b[1]>320]
            for n,i in enumerate(plots):
                kind,bh,data=blocks[i];cut=min(bh-320,math.ceil(excess/(len(plots)-n)))
                blocks[i]=(kind,bh-cut,data);excess-=cut
        sizes=tuple(sum(b[1] for b in blocks[a:z]) for a,z in zip(groups,groups[1:]))
        if sizes and max(sizes)+head+footheight<=min(MAX_IMAGE_EDGE,MAX_IMAGE_PIXELS//width):
            parts=render_parts(sizes,width,head+footheight,lambda a,z,h:paint(groups[a],groups[z],h),max_height=4200)
        else:
            parts=render_parts(tuple(b[1] for b in blocks),width,head+footheight,paint,max_height=3600)
        return tuple(CutoffPage(p.image,answer.text if trend else replace(answer,display_ranks=tuple(b[2] for b in blocks[p.start:p.stop])).text) for p in parts)
    finally:
        for image in cache.values():
            if image is not None:image.close()
