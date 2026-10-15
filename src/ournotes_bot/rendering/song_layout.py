# L3
# Input: Complete captured songs, query label and validated artwork loader.
# Output: Readable Haneoka-style song-list image parts with continuous numbering.
# Pos: Rendering / Song; approved paired-column list.
# Effects: Pillow only; no query or platform operations.
from ..data import localized_text
from .haneoka_theme import Canvas, MUTED, DIFFICULTIES, lines, font, mission_label
from .readable_pages import ImagePart
from .image_output import MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS, ImageBudgetError, encode_image


def render_list(songs,query,locale='zh',*,load):
    songs=tuple(songs)
    if not songs:return (),()
    cols=1 if len(songs)==1 else 2
    width=892 if cols==1 else 1760
    def page_rows(start,stop):
        middle=start+(stop-start+cols-1)//cols
        left=list(enumerate(songs[start:middle],start))
        right=list(enumerate(songs[middle:stop],middle)) if cols==2 else []
        rows=[(item,)+((right[i],) if i<len(right) else ()) for i,item in enumerate(left)]
        heights=tuple(max(68,max(len(lines(localized_text(s,'title',locale),342,36,True))*44 for _,s in row))+
                      max(48,max(len(lines(localized_text(s,'band',locale),550,34))*42 for _,s in row))+80 for row in rows)
        return rows,heights
    subtitle=f'{query} · 共 {len(songs)} 首'
    head=136+len(lines(subtitle,width-108,24))*34
    def paint(start,stop,height):
        rows,heights=page_rows(start,stop)
        c=Canvas(width,height);c.header('歌曲列表',subtitle);y=head
        for col in range(cols):c.box((28+col*868,head-22,864+col*868,height-42),'white',24)
        for ri in range(len(rows)):
            row=rows[ri];titleh=max(68,max(len(lines(localized_text(s,'title',locale),342,36,True))*44 for _,s in row))
            bandheight=max(48,max(len(lines(localized_text(s,'band',locale),550,34))*42 for _,s in row))
            for col,(index,song) in enumerate(row):
                x=48+col*868
                asset=load(song.jacket_url,(128,128));c.paste(asset,x,y+4,128,128,radius=12)
                if asset is not None:asset.close()
                c.text(x+64,y+158,f'{index+1} · {song.id}',24,MUTED,align='center')
                title=localized_text(song,'title',locale);c.paragraph(x+148,y+(titleh-len(lines(title,342,36,True))*44)/2,title,342,36,44,bold=True)
                band=localized_text(song,'band',locale);c.paragraph(x+148,y+titleh+6,band,550,34,42,color=MUTED)
                traits=song.traits
                if traits and traits.attribute_icon:
                    last_band=lines(band,550,34)[-1]
                    ax=x+148+min(580,font(34,False,last_band.isascii()).getlength(last_band))+16
                    asset=load(traits.attribute_icon,(48,48));c.paste(asset,round(ax),y+titleh+3+(len(lines(band,550,34))-1)*42,48,48,True,trim=True)
                    if asset is not None:asset.close()
                for j,(name,(bg,fg)) in enumerate(DIFFICULTIES.items()):
                    chart=next((ch for ch in song.charts if ch.difficulty==name),None)
                    bx=x+516+j*72;by=y+(titleh-64)//2
                    c.box((bx,by,bx+64,by+64),bg,14)
                    value=f'{chart.level}' if chart else '—'
                    c.text(bx+32,by+32,value,30 if len(value)>2 else 38,fg,True,'center')
                missions=traits.missions if traits else None
                if missions:
                    mx=x+148;my=y+titleh+bandheight+33
                    for j,value in enumerate(missions):
                        mission_label(c,round(mx),my,value)
                        mx+=44+font(29,False,value.isascii()).getlength(value)
                        if j<len(missions)-1:
                            c.text(round(mx+10),my,'→',26,MUTED)
                            mx+=20+font(26).getlength('→')
                else:c.text(x+148,y+titleh+bandheight+33,'激奏资料未确认',29,MUTED)
                c.line(x,y+heights[ri]-10,x+796)
            y+=heights[ri]
        c.text(54,height-22,f'{start+1}–{stop} / {len(songs)} · /查谱面 ID 难度',22,MUTED)
        return c.image
    parts=[];start=0
    limit=min(MAX_IMAGE_EDGE,MAX_IMAGE_PIXELS//width)
    while start<len(songs):
        # A complete row is at least 196px. Test candidates from largest to smallest:
        # moving the column break can change which long titles share a row.
        stop=min(len(songs),start+max(1,(limit-head-44)//196)*cols)
        while stop>start:
            _,heights=page_rows(start,stop);height=head+44+sum(heights)
            if height<=limit:
                try:raw=encode_image(paint(start,stop,height),close_input=True,preserve_size=True)
                except ImageBudgetError:pass
                else:
                    parts.append(ImagePart(raw,start,stop));start=stop;break
            stop-=1
        else:raise ImageBudgetError('One complete song exceeds the readable image budget')
    return tuple(parts),tuple((p.start,p.stop) for p in parts)
