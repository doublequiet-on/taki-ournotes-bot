# L3
# Input: a captured CutoffAnswer and a bounded/injectable artwork loader.
# Output: complete image pages with same-row scores/digital IDs/usernames and captured text fallbacks.
# Pos: Rendering / Song challenge-cutoff views; see L2-2-Song.md.
# Effects: Pillow rendering using existing theme/encoder; bounded asset-cache I/O and per-URL failure backoff.
from __future__ import annotations

import hashlib
import io
import math
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from PIL import Image, ImageOps

from .. import visuals as v
from ..query.event_cutoff_query import CutoffAnswer, event_status, song_period_lines
from ..sources.moenotes_events import ASSETS, SERVERS, SourceError, display_time, zone_label

WIDTH = 720
PAGE_HEIGHT = 2300  # At 2x this remains under the shared pixel/edge budgets.
PAGE_TEXT_BUDGET = 1780  # Reserve room for the page label within the 1800-character fallback limit.


@dataclass(frozen=True)
class CutoffPage:
    image: bytes
    text: str


def load_artwork(source, event, urls: tuple[str, ...]) -> dict[str, Image.Image | None]:
    """Only source-derived artwork paths; no redirects, huge images or retry chain."""
    deadline = source.monotonic() + 6
    prefix = f"{ASSETS}/{event.server}/{SERVERS[event.server][3]}/"
    def load(url):
        if not url.startswith(prefix) or not re.fullmatch(
                r"(?:Story/Banner/Chapter|Image/Jacket)/([A-Za-z0-9_-]{1,120})/\1\.webp", url[len(prefix):]):
            return None
        key = f"{event.server}:{event.event_id}:{event.metadata_version}:{event.asset_version}:{url}"
        path = source.cache_dir / "assets" / (hashlib.sha256(key.encode()).hexdigest() + ".webp")
        request_key = "asset:" + url
        lock = source._lock(request_key)
        if not lock.acquire(timeout=max(0, deadline - source.monotonic())):
            return None
        try:
            try:
                raw = None
                if path.is_file() and path.stat().st_size <= 8_000_000 and 0 <= source.clock() - path.stat().st_mtime < 3600:
                    raw = path.read_bytes()
                downloaded = raw is None
                if downloaded:
                    negative = source._negative.get(request_key)
                    if negative and negative[0] > source.monotonic():
                        return None
                    try:
                        raw, headers = source._get(url, deadline)
                    except SourceError as exc:
                        if exc.code != "budget":
                            source._negative[request_key] = (source.monotonic() + exc.retry_after, exc)
                        return None
                    source._negative.pop(request_key, None)
                    if not headers.get("content-type", "").lower().startswith("image/"):
                        return None
                with Image.open(io.BytesIO(raw)) as original:
                    if original.width * original.height > 8_000_000:
                        return None
                    original.load()
                    image = original.convert("RGB")
                if downloaded:
                    temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temp.write_bytes(raw)
                        os.replace(temp, path)
                    except OSError:
                        pass
                    finally:
                        temp.unlink(missing_ok=True)
                return image
            except Exception:
                return None
        finally:
            lock.release()
    unique = tuple(dict.fromkeys(url for url in urls if url))
    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(zip(unique, pool.map(load, unique)))


def _lines(draw, text: str, width: int, size: int) -> list[str]:
    return v._wrapped_lines(draw, text, width, size, len(text) + 1)


def _text(draw, lines, x, y, size=22, color=v.INK, step=None):
    for line in lines:
        draw.text((x, y), line, font=v._font(size), fill=color)
        y += step or math.ceil(size * 1.5)
    return y


def _art(canvas, draw, picture, box, label):
    x, y, w, h = box
    scale = v.RENDER_SCALE
    draw.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=v.SURFACE, outline=v.BORDER, width=1)
    if picture is None:
        size = 16 if w < 96 else 18
        lines = _lines(draw, label, w - 16, size)
        step = math.ceil(size * 1.4)
        _text(draw, lines, x + 8, y + (h - len(lines) * step) // 2, size, v.MUTED, step)
        return
    fitted = ImageOps.contain(picture, (w * scale, h * scale), Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x * scale + (w * scale - fitted.width) // 2, y * scale + (h * scale - fitted.height) // 2))



def _header_layout(answer, width, measure, preview_label):
    title = _lines(measure, answer.event.title, width - 76, 26)
    mode = _lines(measure, answer.rank_label, width - 76, 22)
    height = 222 + len(title) * 36 + len(mode) * 32 + (30 if preview_label else 0)
    return title, mode, height


def _draw_header(canvas, draw, answer, assets, width, layout, preview_label, heading):
    title, mode, height = layout
    event = answer.event
    draw.rounded_rectangle((20, 20, width - 20, height - 12), radius=22, fill=v.PAPER)
    _text(draw, [heading], 36, 32, 32)
    _art(canvas, draw, assets.get(event.banner), (width - 268, 28, 232, 90), '活动图暂缺')
    _text(draw, [f'{SERVERS[event.server][0]} · {event_status(event)} · 活动 {event.event_id}'], 38, 92, 20, v.ACCENT)
    y = _text(draw, title, 38, 134, 26, step=36)
    y = _text(draw, [f'开始 {display_time(event.start_ms, event.server)}  {zone_label(event.server)}',
                    f'结束 {display_time(event.end_ms, event.server)}  {zone_label(event.server)}'], 38, y + 5, 20, v.MUTED, 30)
    y = _text(draw, mode, 38, y + 8, 22, v.ACCENT, 32)
    if preview_label:
        _text(draw, [preview_label], 38, y + 4, 18, v.MUTED, 26)


def _footer_lines(answer, measure, width):
    notes = [*answer.event.notes, '来源 MoeNotes · 非官方 · 活动挑战歌曲 Top 100',
             '按来源响应位置；非预测、非确认终榜']
    return [line for note in notes if note for line in _lines(measure, note, width - 76, 18)]


def _table_details(answer, board, measure, width):
    details = [board.status, '源采集：', display_time(board.fetched_ms, answer.event.server), zone_label(answer.event.server),
               *song_period_lines(answer.event, board.song), *board.notes]
    return [line for item in details if item for line in _lines(measure, item, width, 18)]


def _tables(answer, assets, preview_label):
    # Every page has all selected songs. Only the requested rank axis is paged.
    width = max(720, min(1800, 124 + len(answer.boards) * 268))
    _, measure = v._background(width, 20, v.RENDER_SCALE)
    layout = _header_layout(answer, width, measure, preview_label)
    rank_width = 76
    cell_width = (width - 40 - rank_width) / len(answer.boards)
    titles = [_lines(measure, answer.song_label(board).partition(' · ')[2], cell_width - 24, 24) for board in answer.boards]
    column_height = 102 + max(len(lines) for lines in titles) * 34
    details = [_table_details(answer, board, measure, cell_width - 24) for board in answer.boards]
    detail_height = 30 + max(len(lines) for lines in details) * 27
    footer = _footer_lines(answer, measure, width)
    footer_height = 65 + len(footer) * 28
    rows = []
    for rank in answer.ranks:
        cells = [(_lines(measure, answer.score_label(board, rank), cell_width - 24, 24),
                  _lines(measure, answer.name_label(board, rank), cell_width - 24, 18),
                  _lines(measure, 'ID：' + answer.id_label(board, rank), cell_width - 24, 18)) for board in answer.boards]
        rows.append((rank, cells, 6 + max(len(scores) * 28 + (len(names) + len(ids)) * 20 for scores, names, ids in cells)))
    if any(len(scores) > 1 or len(ids) > 1 for _, cells, _ in rows for scores, _, ids in cells):
        footer.insert(0, '超长整数分行显示；同一格内按从上到下的数字顺序读取。')
        footer_height = 65 + len(footer) * 28
    max_height = min(PAGE_HEIGHT, 12_000_000 // (v.RENDER_SCALE**2 * width))
    fixed_height = layout[2] + column_height + detail_height + footer_height
    groups, current, height = [], [], fixed_height
    for row in rows:
        ranks = tuple(r[0] for r in (*current, row))
        projected = replace(answer, display_ranks=ranks if ranks != answer.request.ranks else ())
        too_large = height + row[2] > max_height or len(projected.text) > PAGE_TEXT_BUDGET or len(current) >= 25
        if too_large and current:
            groups.append(current)
            current, height = [], fixed_height
        if height + row[2] > max_height or len(replace(answer, display_ranks=(row[0],)).text) > PAGE_TEXT_BUDGET:
            raise ValueError('complete table row or metadata exceeds existing output budget')
        current.append(row)
        height += row[2]
    if current:
        groups.append(current)
    pages = []
    for page_number, group in enumerate(groups, 1):
        height = fixed_height + sum(r[2] for r in group)
        canvas, draw = v._background(width, height, v.RENDER_SCALE)
        _draw_header(canvas, draw, answer, assets, width, layout, preview_label, '歌曲榜线 · 排名分数表')
        top = layout[2]
        draw.rectangle((20, top, width - 20, top + column_height), fill=v.PAPER)
        _text(draw, ['排名'], 32, top + 28, 24, v.ACCENT)
        for index, board in enumerate(answer.boards):
            x = 20 + rank_width + index * cell_width
            _art(canvas, draw, assets.get(board.song.jacket), (int(x + 12), top + 12, 64, 64), '封面暂缺')
            _text(draw, [answer.song_label(board).partition(' · ')[0]], x + 88, top + 22, 26, v.ACCENT)
            _text(draw, titles[index], x + 12, top + 92, 24, step=34)
        y = top + column_height
        for rank, cells, row_height in group:
            selected = answer.request.mode == 'near' and rank == answer.request.target_rank
            draw.rectangle((20, y, width - 20, y + row_height), fill='#efe2ef' if selected else v.PAPER)
            if selected:
                draw.rectangle((20, y, 26, y + row_height), fill=v.ACCENT)
            draw.line((20, y + row_height, width - 20, y + row_height), fill=v.BORDER, width=1)
            _text(draw, [f'T{rank}'], 32, y + 6, 24, v.ACCENT if selected else v.INK)
            for index, (lines, names, ids) in enumerate(cells):
                right = 20 + rank_width + (index + 1) * cell_width - 12
                for offset, line in enumerate(lines):
                    x = right - measure.textlength(line, font=v._font(24))
                    _text(draw, [line], x, y + 3 + offset * 28, 24, v.ACCENT if selected else v.INK)
                for offset, line in enumerate(names):
                    x = right - measure.textlength(line, font=v._font(18))
                    _text(draw, [line], x, y + 3 + len(lines) * 28 + offset * 20, 18, v.INK)
                for offset, line in enumerate(ids):
                    x = right - measure.textlength(line, font=v._font(18))
                    _text(draw, [line], x, y + 3 + len(lines) * 28 + (len(names) + offset) * 20, 18, v.MUTED)
            y += row_height
        for index, lines in enumerate(details):
            x = 20 + rank_width + index * cell_width + 12
            _text(draw, lines, x, y + 15, 18, v.MUTED, 27)
        y += detail_height
        y = _text(draw, footer, 38, y + 8, 18, v.MUTED, 28)
        page_ranks = tuple(row[0] for row in group)
        _text(draw, [f'第 {page_number}/{len(groups)} 页 · 本页 T{page_ranks[0]}～T{page_ranks[-1]} · 完整请求 {answer.rank_label}'],
              38, y + 10, 18, v.ACCENT)
        subset = replace(answer, display_ranks=page_ranks if page_ranks != answer.request.ranks else ())
        text = (f'第 {page_number}/{len(groups)} 页\n' if len(groups) > 1 else '') + subset.text
        pages.append(CutoffPage(v._bytes(canvas), text))
    return tuple(pages)


def render_cutoff(answer: CutoffAnswer, *, asset_loader=None, preview_label: str = '') -> tuple[CutoffPage, ...]:
    if answer.event is None or answer.message or not answer.boards or answer.request.numeric_only:
        return ()
    event = answer.event
    assets = asset_loader(event, (event.banner, *(b.song.jacket for b in answer.boards))) if asset_loader else {}
    if answer.request.mode == 'trend':
        from .cutoff_trends import render_trends
        return render_trends(answer, assets, preview_label)
    return _tables(answer, assets, preview_label)
