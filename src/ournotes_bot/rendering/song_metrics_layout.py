# L3: One captured Meta card/ranking image per page, with fixed global indices.
# Effects: Injected bounded artwork loader, existing theme/fonts and image budget.
from ..domain.meta_query import FIELDS, format_value
from .haneoka_theme import Canvas, MUTED, ACCENT, lines, prepare_assets
from .image_output import MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS, ImageBudgetError, encode_image
from .readable_pages import ImagePart

LAYOUT_VERSION = 'song-meta/1'


def render(result, *, load):
    if not result.rows:
        return ()
    width = 1080
    cap = min(2400, MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS // width)
    fields = result.fields
    source = result.source
    title = '歌曲指标' if len(result.rows) == 1 else '歌曲指标排行'
    subtitle = ('日服' if source.server == 'jp' else '国际服') + (' · '+result.spec.difficulty.upper() if result.spec.difficulty else '')
    if result.status == 'partial':
        subtitle += ' · 已知数据中'
    order = ' / '.join(FIELDS[o.field].label + (' ↑' if o.direction=='asc' else ' ↓')
                       for o in result.spec.order_by if o.field != 'song_id')
    if order:
        subtitle += ' · ' + order
    head = 126 + len(lines(subtitle, width-108, 24))*34
    c = result.coverage
    footer = f'符合条件 {c.eligible} 首 · 指标完整 {c.complete} 首 · 待确认 {c.unknown} 首'
    missing = '缺失：' + '、'.join(f'{FIELDS[f].label} {n}' for f,n in c.missing_fields) if c.missing_fields else ''
    footheight = 104 + len(lines(missing, width-108, 24))*32 if missing else 104
    def metrics(row):
        return '   ·   '.join(FIELDS[f].label+' '+format_value(f,row.value(f)) for f in fields)
    def height(row):
        return (max(114, len(lines(row.song.title,740,36,True))*44+len(lines(row.song.band,740,26))*34+30)
                + len(lines(metrics(row),width-108,28))*40 + 48)
    parts = []
    start = 0
    while start < len(result.rows):
        stop = start
        pageheight = head + footheight
        while stop < len(result.rows) and pageheight + height(result.rows[stop]) <= cap:
            pageheight += height(result.rows[stop]);stop += 1
        if stop == start:
            raise ImageBudgetError('Meta row exceeds readable image budget')
        while stop > start:
            assets = prepare_assets({(r.song.jacket,(114,114)) for r in result.rows[start:stop]},load)
            canvas = Canvas(width, head+footheight+sum(height(r) for r in result.rows[start:stop]))
            try:
                canvas.header(title,subtitle)
                y = head
                for index in range(start,stop):
                    row = result.rows[index]
                    canvas.box((28,y-6,width-28,y+height(row)-14),'white',20)
                    canvas.paste(assets[(row.song.jacket,(114,114))],54,y+12,114,114,radius=12)
                    canvas.text(width-54,y+30,f'{index+1:02}',28,ACCENT,True,'right')
                    end = canvas.paragraph(194,y+10,row.song.title,740,36,44,bold=True)
                    end = canvas.paragraph(194,end+8,row.song.band,740,26,34,color=MUTED)
                    canvas.text(194,end+20,f'ID {row.song.song_id}',24,MUTED)
                    mh = max(114,len(lines(row.song.title,740,36,True))*44+len(lines(row.song.band,740,26))*34+30)
                    canvas.paragraph(54,y+mh+16,metrics(row),width-108,28,40)
                    y += height(row)
                canvas.text(54,y+14,footer,24,MUTED)
                if missing:
                    y = canvas.paragraph(54,y+36,missing,width-108,24,32,color=MUTED)-28
                canvas.text(54,y+58,f'Haneoka · {source.server} · {source.release_id}',22,MUTED)
                canvas.text(width-54,y+86,f'{start+1}–{stop} / {len(result.rows)}',22,MUTED,align='right')
                raw = encode_image(canvas.image,close_input=True,preserve_size=True)
            except ImageBudgetError:
                stop -= 1
            else:
                parts.append(ImagePart(raw,start,stop));start=stop;break
            finally:
                canvas.image.close()
                for art in assets.values():
                    if art is not None: art.close()
        else:
            raise ImageBudgetError('Meta page exceeds encoded image budget')
    return tuple(parts)
