# L3
# Input: Pure InfoAnswer views and an injected validated artwork loader.
# Output: Complete theme-matched image parts with the text shown on each part.
# Pos: Rendering / Information; activity, gacha and character layouts.
# Effects: Pillow encoding and injected artwork loading only; no queries or QQ.
"""Information pages use the same typography and palette as card/song pages."""
from dataclasses import dataclass, replace

from ..query.contracts.info import InfoAnswer, InfoRow, row_text
from . import readable_pages
from .haneoka_theme import Canvas, ACCENT, INK, MUTED, SOFT, SURFACE, frame, lines, prepare_assets
from .image_output import ImageBudgetError, reclaim_after_render

WIDTH = 1080
LEFT, RIGHT = 54, 1026


@dataclass(frozen=True)
class InfoPage:
    text: str
    image: bytes


@dataclass(frozen=True)
class _Block:
    height: int
    text: str = ""
    title: tuple[str, ...] = ()
    label: tuple[str, ...] = ()
    body: tuple[str, ...] = ()
    command: tuple[str, ...] = ()
    image_url: str = ""
    image_kind: str = "square"
    rarity: int | None = None
    kind: str = "row"
    show_image: bool = False
    children: tuple = ()
    attribute_url: str = ""


def _picture_size(kind):
    return {"wide": (290, 164), "portrait": (156, 234)}.get(kind, (152, 152))


def _content_width(row, force_image=False):
    return RIGHT - LEFT - (_picture_size(row.image_kind)[0] + 28 if row.image_url or force_image else 0)


def _body_height(body):
    return sum(40 if value else 14 for value in body)


def _row(row, *, width=None, body=None, title=True, image=True, command=True, continued=False, force_image=False):
    width = width or _content_width(row, force_image)
    title_lines = lines(row.title + ("（续）" if continued else ""), width, 32, True) if title and row.title else ()
    label_lines = lines(row.label, width, 24) if title and row.label else ()
    body_lines = tuple(line for paragraph in row.body for line in (*lines(paragraph, width, 28), "")) if body is None else body
    if body_lines and body_lines[-1] == "":
        body_lines = body_lines[:-1]
    command_lines = lines(row.command, width, 24) if command and row.command else ()
    text_h = len(title_lines) * 44 + len(label_lines) * 34 + _body_height(body_lines)
    text_h += len(command_lines) * 34 + (12 if command_lines and text_h else 0)
    art_h = _picture_size(row.image_kind)[1] if image and (row.image_url or force_image) else 0
    text = row_text(row) if body is None else "\n".join(v for v in (
        row.title if title else "", row.label if title else "", *body_lines, row.command if command else "") if v)
    return _Block(max(art_h, text_h, 40) + 40, text, title_lines, label_lines,
                  body_lines, command_lines, row.image_url if image else "", row.image_kind,
                  row.rarity, show_image=image and bool(row.image_url or force_image),attribute_url=row.attribute_url)


def _row_blocks(row, limit, *, force_image=False):
    """Keep a list item whole when possible; split unusually long prose by lines."""
    whole = _row(row, force_image=force_image)
    if whole.height <= limit:
        return (whole,)
    # This is a single very long item, not a reason to crop it or shrink fonts.
    # Keep its first image, repeat its identifying heading on continuation parts.
    width = _content_width(row, force_image)
    paragraphs = [tuple(lines(value, width, 28)) for value in row.body]
    pending = [line for p in paragraphs for line in (*p, "")]
    if pending:
        pending.pop()
    first, result = True, []
    while pending:
        header = _row(row, body=(), command=False, image=first, continued=not first, force_image=force_image)
        capacity, used = 0, 0
        while capacity < len(pending) and used + (40 if pending[capacity] else 14) <= limit - header.height:
            used += 40 if pending[capacity] else 14
            capacity += 1
        if capacity < 1:
            raise ImageBudgetError("Information heading exceeds the readable page budget")
        # End at an existing paragraph boundary when one fits in this part.
        count = min(capacity, len(pending))
        if count < len(pending):
            boundary = max((i + 1 for i, value in enumerate(pending[:count]) if not value), default=0)
            if boundary:
                count = boundary
        chunk, pending = tuple(pending[:count]), pending[count:]
        block = _row(row, body=chunk, command=False, image=first, continued=not first, force_image=force_image)
        result.append(block)
        first = False
    if not result:
        raise ImageBudgetError("Information item exceeds the readable page budget")
    if row.command:
        last = result[-1]
        command = lines(row.command, width, 24)
        addition = len(command) * 34 + 12
        if last.height + addition <= limit:
            result[-1] = replace(last, height=last.height + addition,
                text=last.text + "\n" + row.command, command=command)
        else:
            result.append(_row(InfoRow(row.title, command=row.command), body=(), image=False, continued=True))
    return tuple(result)


def _blocks(view, limit):
    result = []
    rows=view.rows
    if view.layout=='character':
        facts=tuple(row for row in rows if row.kind=='fact')
        fact_width=426 if view.hero_url else 796
        measured=tuple(_Block(max(len(lines(row.title,156,26))*36,
                                  sum(len(lines(p,fact_width,28))*40 for p in row.body))+24,
                              row_text(row),title=lines(row.title,156,26),
                              body=tuple(line for p in row.body for line in lines(p,fact_width,28))) for row in facts)
        height=max(860 if view.hero_url else 0,sum(b.height for b in measured))+24
        if (view.hero_url or facts) and height<=limit:
            result.append(_Block(height,'\n'.join(b.text for b in measured),image_url=view.hero_url,kind='profile',children=measured))
            rows=tuple(row for row in rows if row.kind!='fact')
        elif view.hero_url:
            result.append(_Block(min(860,limit),image_url=view.hero_url,kind='hero'))
    elif view.layout=='directory':
        groups={}
        for row in rows:groups.setdefault(row.group,[]).append(row)
        for group,people in groups.items():
            for start in range(0,len(people),5):
                children=tuple(_row(row,width=166,force_image=True) for row in people[start:start+5])
                art_h=max(180 if b.image_kind=='portrait' else 120 for b in children)
                result.append(_Block(art_h+max(b.height for b in children)+72,
                    '\n'.join(b.text for b in children),title=(group or '未分组',),kind='directory',children=children))
        rows=()
    elif view.hero_url or view.layout=='detail':
        result.append(_Block(344,image_url=view.hero_url,kind='hero'))
    links=tuple(row for row in rows if row.kind=='links') if view.layout=='character' else ()
    for row in rows:
        if row in links:continue
        result.extend(_row_blocks(row, limit, force_image=view.layout == "list"))
    for start in range(0,len(links),2):
        children=tuple(_row(row,width=468) for row in links[start:start+2])
        result.append(_Block(max(b.height for b in children),'\n'.join(b.text for b in children),kind='links',children=children))
    # Footnotes can be long (source warnings, aliases). Measure them as content,
    # so they never consume the fixed page frame or overlap the last item.
    for note in view.footer:
        wrapped = lines(note, RIGHT - LEFT, 24)
        capacity = max(1, (limit - 30) // 34)
        for start in range(0, len(wrapped), capacity):
            chunk = wrapped[start:start + capacity]
            result.append(_Block(len(chunk) * 34 + 30, "\n".join(chunk), body=chunk, kind="note"))
    if not result:
        result.append(_Block(84, "暂无可展示资料。", body=("暂无可展示资料。",), kind="note"))
    return tuple(result)


def _draw_block(c, block, y, load, *, left=LEFT, right=RIGHT):
    if block.kind=='profile':
        if block.image_url:
            h=min(860,block.height-24)
            c.paste(load(block.image_url,(330,h)),LEFT,y,330,h,True)
        cy=y
        label_x,value_x=(424,600) if block.image_url else (LEFT,LEFT+176)
        for child in block.children:
            for i,value in enumerate(child.title):c.text(label_x,cy+18+i*36,value,26,MUTED)
            for i,value in enumerate(child.body):c.text(value_x,cy+20+i*40,value,28)
            cy+=child.height
        return
    if block.kind=='directory':
        c.line(LEFT,y,RIGHT,'#e1dce7');c.text(LEFT,y+34,block.title[0],32,INK,True)
        art_h=max(180 if b.image_kind=='portrait' else 120 for b in block.children)
        for i,child in enumerate(block.children):
            x=round(LEFT+i*(RIGHT-LEFT)/5);w,h=(120,180) if child.image_kind=='portrait' else (120,120)
            c.paste(load(child.image_url,(w,h)),x+24,y+72,w,h,True,radius=60 if h==w else 8)
            cy=y+72+art_h+16
            for value in child.title:c.text(x+90,cy+22,value,32,INK,True,'center');cy+=44
            for value in child.label:c.text(x+90,cy+17,value,24,ACCENT,align='center');cy+=34
            for value in child.body:
                if value:c.text(x+90,cy+20,value,28,align='center')
                cy+=40 if value else 14
            for value in child.command:c.text(x+90,cy+17,value,24,ACCENT,align='center');cy+=34
        return
    if block.kind=='links':
        for i,child in enumerate(block.children):_draw_block(c,child,y,load,left=LEFT+i*504,right=LEFT+i*504+468)
        return
    if block.kind == "hero":
        c.box((30, y, 1050, y + block.height - 18), SURFACE, 24)
        c.paste(load(block.image_url, (972, block.height - 48)), LEFT, y + 12,
                972, block.height - 48, True, radius=16)
        return
    if block.kind == "note":
        for i, value in enumerate(block.body):
            c.text(LEFT, y + 18 + i * 34, value, 24, MUTED)
        return
    c.line(left, y, right, '#e1dce7')
    x, cy = left, y + 20
    if block.show_image:
        w, h = _picture_size(block.image_kind)
        if block.rarity is not None:
            border = frame(w, h, block.rarity)
            c.image.paste(border, (x - 4, cy - 4), border)
        c.paste(load(block.image_url, (w, h)), x, cy, w, h, True, radius=8)
        rarity={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(block.rarity)
        if rarity:
            bw=max(40,min(72,w//3));bh=round(bw*.72)
            c.badge(f'RarityIconCenter_{rarity}.png',x+w-bw,cy+h-bh//2,bw,bh)
        if block.attribute_url:
            c.paste(load(block.attribute_url,(32,32)),x+2,cy+2,32,32,True)
        x += w + 28
    for value in block.title:
        c.text(x, cy + 22, value, 32, INK, True)
        cy += 44
    for value in block.label:
        c.text(x, cy + 17, value, 24, ACCENT)
        cy += 34
    for value in block.body:
        if value:
            c.text(x, cy + 20, value, 28, INK)
        cy += 40 if value else 14
    if block.command:
        cy += 12
        for value in block.command:
            c.text(x, cy + 17, value, 24, ACCENT)
            cy += 34


@reclaim_after_render
def render_info(answer: InfoAnswer, *, load=None) -> tuple[InfoPage, ...]:
    """Render captured views; loader returns owned images and no source is imported."""
    if answer.status != "success" or not answer.views:
        return ()
    assets = {}

    def artwork(url, size):
        key = (url, size)
        if key not in assets:
            assets[key] = load(url, size) if load and url else None
        return assets[key]

    pages = []
    try:
        for view in answer.views:
            title = lines(view.title, 820, 44, True)
            subtitle = lines(view.subtitle, 972, 24) if view.subtitle else ()
            head = 52 + len(title) * 56 + len(subtitle) * 34 + 28
            fixed = head + 46
            limit = min(readable_pages.MAX_IMAGE_EDGE, readable_pages.MAX_IMAGE_PIXELS // WIDTH) - fixed
            if limit < 120:
                raise ImageBudgetError("Information page heading exceeds the readable budget")
            blocks = _blocks(view, limit)
            requests=set();badges=set()
            def collect(block):
                if block.image_url:
                    if block.kind=='profile':size=(330,min(860,block.height-24))
                    elif block.kind=='hero':size=(972,block.height-48)
                    else:size=_picture_size(block.image_kind)
                    requests.add((block.image_url,size))
                if block.attribute_url:requests.add((block.attribute_url,(32,32)))
                rarity={2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(block.rarity)
                if rarity:badges.add(f'RarityIconCenter_{rarity}.png')
                for child in block.children:
                    if block.kind=='directory':
                        if child.image_url:requests.add((child.image_url,(120,180) if child.image_kind=='portrait' else (120,120)))
                    else:collect(child)
            for block in blocks:collect(block)
            requests.difference_update(assets)
            assets.update(prepare_assets(requests,load or (lambda *a:None),badges))

            def paint(start, stop, height):
                c = Canvas(WIDTH, height)
                try:
                    cy = 52
                    for value in title:
                        c.text(LEFT, cy + 28, value, 44, INK, True)
                        cy += 56
                    c.box((926, 54, 1026, 94), SOFT, 20)
                    c.text(976, 74, 'Taki', 22, '#3d417b', True, 'center')
                    for value in subtitle:
                        c.text(LEFT, cy + 17, value, 24, MUTED)
                        cy += 34
                    y = head
                    for block in blocks[start:stop]:
                        _draw_block(c, block, y, artwork)
                        y += block.height
                    c.text(LEFT, height - 22, 'Taki · Our Notes', 18, MUTED)
                    if start or stop < len(blocks):
                        c.text(RIGHT, height - 22, '内容续图' if start else '内容见后续图片', 18, MUTED, align='right')
                    return c.image
                except Exception:
                    c.image.close()
                    raise

            parts = readable_pages.render_parts(tuple(block.height for block in blocks), WIDTH, fixed, paint)
            for part in parts:
                text = '\n'.join(v for v in (view.title, view.subtitle,
                    *(block.text for block in blocks[part.start:part.stop])) if v)
                pages.append(InfoPage(text, part.image))
        return tuple(pages)
    finally:
        for asset in assets.values():
            if asset is not None:
                asset.close()
