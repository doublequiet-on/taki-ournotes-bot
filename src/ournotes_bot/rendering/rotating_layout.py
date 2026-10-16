# L3
# Input: Captured activity/gacha views with explicit schedules and typed card rows.
# Output: Measured banner grids, overview panels and grouped card tiles.
# Pos: Rendering / Information; shares the established Haneoka theme.
# Effects: Pillow drawing only; no queries, network, accounts or platform calls.
"""Activity and recruitment composition based on Haneoka's rotating pages."""
from dataclasses import dataclass

from ..query.contracts.info import row_text
from .haneoka_theme import ACCENT, INK, MUTED, SOFT, SURFACE, frame, lines
from .image_output import ImageBudgetError

LEFT, RIGHT, GAP = 54, 1026, 24
WIDTH = RIGHT - LEFT
TILE = (WIDTH - GAP * 2) // 3
BANNER = (WIDTH - GAP) // 2


@dataclass(frozen=True)
class Panel:
    height: int
    kind: str
    text: str = ''
    rows: tuple = ()
    title: str = ''
    body: tuple[str, ...] = ()
    requests: tuple = ()
    badges: tuple[str, ...] = ()


def _card_size(row):
    return (TILE, round(TILE * 9 / 16)) if row.image_kind == 'wide' else (192, 192)


def _card_lines(row):
    return (lines(row.display_title or row.title, TILE, 28, True),
            tuple(line for value in row.body for line in lines(value, TILE, 24)),
            lines(row.label, TILE, 22) if row.label else (),
            lines(row.command, TILE, 24) if row.command else ())


def _card_height(row):
    title, body, label, command = _card_lines(row)
    return 204 + len(title) * 38 + len(body) * 34 + len(label) * 30 + len(command) * 34 + 20


def _banner_height(row):
    return round(BANNER * 3 / 7) + len(lines(row.title, BANNER - 32, 30, True)) * 42 + 224


def blocks(view, limit, prose_blocks):
    result = []
    # A grid row stays complete on a page, including its section heading.
    rows = view.rows
    for row in rows:
        if row.kind == 'schedule':
            if row.schedule is None:
                raise ValueError('Schedule row requires captured schedule facts')
            size=(BANNER, round(BANNER * 3 / 4)) if view.hero_kind == 'event' else (420,180)
            requests=((view.hero_url,size),) if view.hero_url else ()
            if view.hero_kind == 'event' and view.hero_logo_url:
                requests+=((view.hero_logo_url,(210,110)),)
            result.append(Panel(max(size[1],286)+28 if view.hero_url else 314, 'overview',
                                row_text(row), rows=(row,),requests=requests))
    banners = tuple(row for row in rows if row.kind == 'banner')
    for start in range(0, len(banners), 2):
        group = banners[start:start + 2]
        height = max(_banner_height(row) for row in group) + 24
        result.append(Panel(height, 'banners', '\n'.join(row_text(r) for r in group), rows=group,
                            requests=tuple((r.image_url, (420,180)) for r in group if r.image_url)))
    # Preserve explanations before the card sections; songs follow the cards.
    for row in rows:
        if row.kind not in {'schedule', 'banner', 'card', 'song'}:
            result.extend(prose_blocks(row, limit))
    cards = tuple(row for row in rows if row.kind == 'card')
    for kind in dict.fromkeys(row.group for row in cards):
        group = tuple(row for row in cards if row.group == kind)
        for start in range(0, len(group), 3):
            chunk = group[start:start + 3]
            title = ('UP · ' if view.presentation == 'gacha' else '关联') + kind + f' · {len(group)}'
            if start:
                title += '（续）'
            requests = tuple((r.image_url, _card_size(r)) for r in chunk if r.image_url)
            requests += tuple((r.attribute_url, (32, 32)) for r in chunk if r.attribute_url)
            badges = tuple(f'RarityIconCenter_{name}.png' for r in chunk
                           if (name := {2:'R',3:'SR',4:'SSR',20:'BD',10:'EX'}.get(r.rarity)))
            result.append(Panel(72 + max(_card_height(r) for r in chunk), 'cards',
                                '\n'.join(row_text(r) for r in chunk), chunk, title,
                                requests=requests, badges=badges))
    for row in rows:
        if row.kind == 'song':
            result.extend(prose_blocks(row, limit))
    notes = view.footer if view.visual_footer is None else view.visual_footer
    wrapped = tuple(line for value in notes for line in lines(value, WIDTH, 22))
    # Exact version/fetch diagnostics remain in the complete page text fallback.
    capacity = max(1, (limit - 32) // 32)
    for start in range(0, max(1, len(wrapped)), capacity):
        chunk = wrapped[start:start + capacity]
        result.append(Panel(32 + len(chunk) * 32, 'footer',
                            '\n'.join(view.footer) if start == 0 else '', body=chunk))
    if any(block.height > limit for block in result):
        raise ImageBudgetError('One activity/recruitment section exceeds the readable page budget')
    return tuple(result)


def _status(c, value, x, y, right):
    colors = {'时间窗内': ('#e0e9e1', '#396848'), '进行中': ('#e0e9e1', '#396848'),
              '尚未开始': (SOFT, ACCENT)}
    fill, color = colors.get(value, (SURFACE, MUTED))
    width = min(right - x, max(104, len(value) * 24 + 32))
    c.box((x, y, x + width, y + 40), fill, 20)
    c.text(x + width / 2, y + 20, value, 24, color, True, 'center')


def draw(c, panel, y, load):
    if panel.kind == 'overview':
        schedule = panel.rows[0].schedule
        x=LEFT
        if panel.requests:
            url,(w,h)=panel.requests[0]
            c.paste(load(url,(w,h)),LEFT+(BANNER-w)//2,y+(panel.height-28-h)//2,w,h,True,radius=16)
            if len(panel.requests)>1:
                logo,size=panel.requests[1]
                c.paste(load(logo,size),LEFT+w-size[0]-12,y+(h-size[1])//2,*size,True)
            x+=BANNER+GAP
        c.text(x,y+20,schedule.identity,24,MUTED)
        _status(c,schedule.status,x,y+50,RIGHT)
        for cy,label,value in ((y+125,'开始',schedule.start),(y+201,'结束',schedule.end)):
            c.text(x,cy,label,22,MUTED)
            c.text(x,cy+34,value,26,INK,True)
        c.text(x,y+277,schedule.zone,22,MUTED)
    elif panel.kind == 'banners':
        for i, row in enumerate(panel.rows):
            x = LEFT + i * (BANNER + GAP)
            h = panel.height - 24
            c.box((x, y, x + BANNER, y + h), SURFACE, 16)
            ah = round(BANNER * 3 / 7)
            c.paste(load(row.image_url, (420,180)), x+(BANNER-420)//2, y+(ah-180)//2, 420,180, True, radius=12)
            cy = y + ah + 26
            for value in lines(row.title, BANNER - 32, 30, True):
                c.text(x + 16, cy, value, 30, INK, True)
                cy += 42
            schedule = row.schedule
            if schedule is None:
                raise ValueError('Banner row requires captured schedule facts')
            c.text(x + 16, cy + 4, schedule.identity, 22, MUTED)
            cy += 28
            _status(c, schedule.status, x + 16, cy, x + BANNER - 16)
            c.text(x + 16, cy + 62, '起 ' + schedule.start, 24, MUTED)
            c.text(x + 16, cy + 96, '止 ' + schedule.end, 24, MUTED)
            c.text(x + 16, cy + 140, row.command, 24, ACCENT)
    elif panel.kind == 'cards':
        c.line(LEFT, y, RIGHT, '#e1dce7')
        c.text(LEFT, y + 34, panel.title, 30, INK, True)
        for i, row in enumerate(panel.rows):
            x, top = LEFT + i * (TILE + GAP), y + 72
            w, h = _card_size(row)
            ax, ay = x + (TILE - w) // 2, top + (192 - h) // 2
            if row.rarity is not None:
                border = frame(w, h, row.rarity)
                c.image.paste(border, (ax - 4, ay - 4), border)
            c.paste(load(row.image_url, (w, h)), ax, ay, w, h, True, radius=8)
            if row.rarity is not None:
                c.rarity_badge(row.rarity, ax + w - 6, ay + h - 6, 66, 48)
            if row.attribute_url:
                c.paste(load(row.attribute_url, (32, 32)), ax + 2, ay + 2, 32, 32, True)
            cy = top + 204
            title, body, label, command = _card_lines(row)
            for values, size, step, color, bold in ((title, 28, 38, INK, True), (body, 24, 34, MUTED, False),
                                                    (label, 22, 30, ACCENT, False), (command, 24, 34, ACCENT, False)):
                for value in values:
                    c.text(x, cy + step / 2, value, size, color, bold)
                    cy += step
    elif panel.kind == 'footer':
        c.line(LEFT, y + 4, RIGHT, '#e1dce7')
        for i, value in enumerate(panel.body):
            c.text(LEFT, y + 30 + i * 32, value, 22, MUTED)
