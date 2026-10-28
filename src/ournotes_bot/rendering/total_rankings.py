# L3
# Input: One captured independent total snapshot and bounded local history view.
# Output: Total-ranking trend/table pages with the same captured text fallback.
# Pos: Rendering / Total rankings; see L2-2-Song.md and docs/EVENT_CUTOFFS.md.
# Effects: Pillow only; no source reads, fake songs or single-song score threshold.
from __future__ import annotations

from dataclasses import replace

from PIL import ImageDraw

from .. import visuals as v
from .haneoka_theme import Canvas
from .image_output import encode_image
from .event_cutoff_visuals import CutoffPage, _header_layout, _lines, _text
from .cutoff_trends import COLORS, segments, coordinates


def render_total(answer, assets, preview_label=''):
    board=answer.total
    width=720
    measure=Canvas(width,20).draw
    layout=_header_layout(answer,width,measure,preview_label)
    footer=[line for text in (board.status, f'源采集：{answer.total_time_label(board.fetched_ms)}',
            *board.notes,answer.total_history.warning,
            f'来源 {board.source} · 综合榜独立观测；非预测、非确认终榜。')
            if text for line in _lines(measure,text,640,18)]
    trend=answer.request.mode=='trend'
    plot_height=360 if trend else 0
    fixed=layout[2]+len(footer)*28+90+plot_height
    rows=[]
    for rank in answer.ranks:
        value=board.score(rank)
        score=_lines(measure,str(value) if value is not None else '暂无数据',500,26)
        names=_lines(measure,'用户名：'+(board.player_name(rank) or '未获取'),640,18)
        ids=_lines(measure,'ID：'+(board.player_id(rank) or '未获取'),640,18)
        rows.append((rank,score,names,ids,22+len(score)*32+(len(names)+len(ids))*25))
    groups=[];group=[];height=fixed
    for row in rows:
        projected=replace(answer,display_ranks=tuple(r[0] for r in (*group,row)))
        if group and (height+row[-1]>2300 or len(projected.total_text)>1700):
            groups.append(group);group=[];height=fixed
        if height+row[-1]>2300 or len(replace(answer,display_ranks=(row[0],)).total_text)>1700:
            raise ValueError('total ranking row exceeds readable output budget')
        group.append(row);height+=row[-1]
    if group:groups.append(group)
    pages=[]
    for number,group in enumerate(groups,1):
        height=fixed+sum(row[-1] for row in group)
        surface=Canvas(width,height)
        canvas,draw=surface.image,surface.draw
        _text(draw,['综合分数榜 · '+('观测趋势' if trend else '排名分数表')],36,32,32,v.ACCENT)
        surface.paste(assets.get(answer.event.banner),454,28,224,90,contain=True)
        _text(draw,[f'{answer.total_region_label} · 活动 {answer.event.event_id}'],38,92,20,v.ACCENT)
        y=_text(draw,layout[0],38,134,26,v.INK,36)
        y=_text(draw,['开始 '+answer.total_time_label(answer.event.start_ms),
                      '结束 '+answer.total_time_label(answer.event.end_ms)],38,y+5,20,v.MUTED,30)
        y=_text(draw,layout[1],38,y+8,22,v.ACCENT,32)
        if preview_label:_text(draw,[preview_label],38,y+12,18,v.MUTED)
        y=layout[2]
        for rank,score,names,ids,h in group:
            selected=answer.request.mode=='near' and rank==answer.request.target_rank
            draw.rounded_rectangle((20,y,700,y+h-5),radius=12,fill=v.SURFACE if selected else v.PAPER)
            _text(draw,[f'T{rank}'],38,y+10,24,v.ACCENT)
            _text(draw,score,180,y+8,26,v.INK,32)
            yy=_text(draw,names,38,y+12+len(score)*32,18,v.MUTED,25)
            _text(draw,ids,38,yy,18,v.MUTED,25)
            y+=h
        if trend:
            _plot(canvas,draw,answer,y)
            y+=plot_height
        y=_text(draw,footer,38,y+12,18,v.MUTED,28)
        _text(draw,[f'TAKI · 综合榜 · 第 {number}/{len(groups)} 页'],38,y+12,18,v.ACCENT)
        subset=replace(answer,display_ranks=tuple(r[0] for r in group))
        pages.append(CutoffPage(encode_image(canvas,close_input=True),f'第 {number}/{len(groups)} 页\n'+subset.total_text))
    return tuple(pages)


def _plot(canvas,draw,answer,top):
    view=answer.total_history
    points=[point for point in view.points if any(s is not None for s in point.scores)]
    caption='综合榜历史走势 · 缺采与异常处断线' if len(points)!=1 else '综合榜历史 · 仅1个观测点，尚不足形成曲线'
    _text(draw,[caption],38,top+10,20,v.ACCENT)
    if not points:
        _text(draw,['暂无综合榜历史；从启用采样后积累。'],38,top+120,20,v.MUTED)
        return
    values=[s for p in points for s in p.scores if s is not None]
    low,high=min(values),max(values)
    if low==high:low,high=max(0,low-1),high+1
    first,last=points[0].time_ms,points[-1].time_ms
    box=(210,top+65,460,210)
    draw.rectangle((box[0],box[1],box[0]+box[2],box[1]+box[3]),outline=v.BORDER,width=1)
    for part in (0,1,2):
        value=low+(high-low)*part//2
        _,y=coordinates(first,value,(first,last,low,high),box)
        _text(draw,[str(value)],35,y-10,16,v.MUTED)
        draw.line((210,y,670,y),fill=v.BORDER,width=1)
    native=ImageDraw.Draw(canvas);scale=1
    for i,rank in enumerate(answer.ranks):
        for segment in segments(view,i):
            xy=[coordinates(t,s,(first,last,low,high),box) for t,s in segment]
            xy=[(x*scale,y*scale) for x,y in xy]
            if len(xy)>1:native.line(xy,fill=COLORS[i],width=2*scale)
            for x,y in xy[::max(1,len(xy)//12)]:
                radius=(2+i)*scale
                native.rectangle((x-radius,y-radius,x+radius,y+radius),outline=COLORS[i],width=scale)
        _text(draw,[f'T{rank}'],210+i*88,top+34,16,COLORS[i])
    _text(draw,['始 '+answer.total_time_label(first),'末 '+answer.total_time_label(last)],210,top+288,18,v.MUTED,25)
