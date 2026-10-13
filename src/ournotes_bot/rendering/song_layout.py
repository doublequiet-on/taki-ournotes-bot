# L3
# Input: Complete captured songs, query label and validated artwork loader.
# Output: Readable Haneoka-style song-list image parts with continuous numbering.
# Pos: Rendering / Song; approved paired-column list.
# Effects: Pillow only; no query or platform operations.
from ..data import localized_text
from .haneoka_theme import Canvas, MUTED, DIFFICULTIES, lines, font
from .readable_pages import render_parts


def render_list(songs,query,locale='zh',*,load):
    songs=tuple(songs)
    if not songs:return (),()
    cols=1 if len(songs)==1 else 2
    width=892 if cols==1 else 1760
    rows=[songs[i:i+cols] for i in range(0,len(songs),cols)]
    heights=tuple(max(68,max(len(lines(localized_text(s,'title',locale),342,36,True))*44 for s in row))+
                  max(48,max(len(lines(localized_text(s,'band',locale),550,34))*42 for s in row))+98 for row in rows)
    subtitle=f'{query} · 共 {len(songs)} 首'
    head=134+len(lines(subtitle,width-108,24))*34
    def paint(start,stop,height):
        c=Canvas(width,height);c.header('歌曲列表',subtitle);y=head
        for ri in range(start,stop):
            row=rows[ri];titleh=max(68,max(len(lines(localized_text(s,'title',locale),342,36,True))*44 for s in row))
            bandheight=max(48,max(len(lines(localized_text(s,'band',locale),550,34))*42 for s in row))
            for col,song in enumerate(row):
                x=48+col*868
                asset=load(song.jacket_url,(128,128));c.paste(asset,x,y+4,128,128)
                if asset is not None:asset.close()
                c.text(x+64,y+151,f'{ri*cols+col+1} · {song.id}',21,MUTED,align='center')
                title=localized_text(song,'title',locale);c.paragraph(x+148,y,title,342,36,44,bold=True)
                band=localized_text(song,'band',locale);c.paragraph(x+148,y+titleh,band,550,34,42,color=MUTED)
                traits=song.traits
                if traits and traits.attribute_icon:
                    last_band=lines(band,550,34)[-1]
                    ax=x+148+min(580,font(34,False,last_band.isascii()).getlength(last_band))+16
                    asset=load(traits.attribute_icon,(48,48));c.paste(asset,round(ax),y+titleh+(len(lines(band,550,34))-1)*42,48,48,True)
                    if asset is not None:asset.close()
                for j,(name,(bg,fg)) in enumerate(DIFFICULTIES.items()):
                    chart=next((ch for ch in song.charts if ch.difficulty==name),None)
                    bx=x+516+j*72;by=y+(titleh-64)//2
                    c.box((bx,by,bx+64,by+64),bg,14)
                    c.text(bx+32,by+32,f'{chart.display_level:g}' if chart else '—',38,fg,True,'center')
                missions=traits.missions if traits else None
                c.text(x+148,y+titleh+bandheight+22,' → '.join(missions) if missions else '激奏资料未确认',29,MUTED)
                c.line(x,y+heights[ri]-10,x+796)
            y+=heights[ri]
        c.text(54,height-22,f'{start*cols+1}–{min(stop*cols,len(songs))} / {len(songs)} · /选 编号',22,MUTED)
        return c.image
    parts=render_parts(heights,width,head+44,paint)
    return parts,tuple((p.start*cols,min(p.stop*cols,len(songs))) for p in parts)
