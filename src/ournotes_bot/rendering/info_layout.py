# L3
# Input: Pure InfoAnswer views and an injected validated artwork loader.
# Output: Complete theme-matched image parts with the text shown on each part.
# Pos: Rendering / Information; activity, gacha and character layouts.
# Effects: Pillow encoding and injected artwork loading only; no queries or QQ.
"""Information pages use the same typography and palette as card/song pages."""
from dataclasses import dataclass, replace

from ..query.contracts.info import InfoAnswer, InfoRow, row_text
from . import readable_pages
from .haneoka_theme import Canvas, ACCENT, INK, MUTED, SOFT, SURFACE, frame, lines
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
                  row.rarity, show_image=image and bool(row.image_url or force_image))


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
    if view.hero_url or view.layout in {"detail", "character"}:
        result.append(_Block(660 if view.layout == "character" else 344,
                             image_url=view.hero_url, kind="hero"))
    for row in view.rows:
        result.extend(_row_blocks(row, limit, force_image=view.layout == "list"))
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


def _draw_block(c, block, y, load):
    if block.kind == "hero":
        c.box((30, y, 1050, y + block.height - 18), SURFACE, 24)
        c.paste(load(block.image_url, (972, block.height - 48)), LEFT, y + 12,
                972, block.height - 48, True, radius=16)
        return
    if block.kind == "note":
        for i, value in enumerate(block.body):
            c.text(LEFT, y + 18 + i * 34, value, 24, MUTED)
        return
    c.line(LEFT, y, RIGHT, '#e1dce7')
    x, cy = LEFT, y + 20
    if block.show_image:
        w, h = _picture_size(block.image_kind)
        if block.rarity is not None:
            border = frame(w, h, block.rarity)
            c.image.paste(border, (x - 4, cy - 4), border)
        c.paste(load(block.image_url, (w, h)), x, cy, w, h, True, radius=8)
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
