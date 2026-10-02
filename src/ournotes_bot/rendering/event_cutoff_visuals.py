# L3
# Input: a captured CutoffAnswer and a bounded/injectable artwork loader.
# Output: complete, ordered image pages with matching per-page text fallbacks.
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
        v._write(draw, label, x + 12, y + h // 2 - 12, w - 24, 18, v.MUTED)
        return
    fitted = ImageOps.contain(picture, (w * scale, h * scale), Image.Resampling.LANCZOS)
    canvas.paste(fitted, (x * scale + (w * scale - fitted.width) // 2, y * scale + (h * scale - fitted.height) // 2))


def render_cutoff(answer: CutoffAnswer, *, asset_loader=None, preview_label: str = "") -> tuple[CutoffPage, ...]:
    if answer.event is None or answer.message or not answer.boards:
        return ()
    event = answer.event
    assets = asset_loader(event, (event.banner, *(b.song.jacket for b in answer.boards))) if asset_loader else {}
    _, measure = v._background(WIDTH, 20, v.RENDER_SCALE)
    title_lines = _lines(measure, event.title, 652, 28)
    header_height = 282 + len(title_lines) * 40 + (34 if preview_label else 0)
    note_lines = [line for note in event.notes for line in _lines(measure, note, 644, 18)]
    footer_height = 112 + len(note_lines) * 27

    layouts = []
    for board in answer.boards:
        name_lines = _lines(measure, board.song.title, 510, 26)
        titles_height = max(100, len(name_lines) * 38 + 48)
        values = [str(board.score(r)) if board.score(r) is not None else "暂无数据" for r in answer.ranks]
        size = 40 if len(values) == 1 else 32
        longest = max(measure.textlength(value, font=v._font(size)) for value in values)
        columns = 3 if len(values) > 1 and longest <= 174 else 2 if len(values) > 1 and longest <= 282 else 1
        if longest > 604:
            size = 28
            longest = max(measure.textlength(value, font=v._font(size)) for value in values)
        if longest > 604:
            raise ValueError("Full integer does not fit legibly; use captured text")
        rows = math.ceil(len(values) / columns)
        details = (board.status + (" · " + "；".join(board.notes) if board.notes else ""),
                   *song_period_lines(event, board.song))
        state_lines = [line for detail in details for line in _lines(measure, detail, 620, 18)]
        height = titles_height + rows * 90 + 76 + len(state_lines) * 27
        layouts.append((board, name_lines, titles_height, columns, size, state_lines, height))

    groups, current, height = [], [], header_height + footer_height
    for layout in layouts:
        candidate_text = replace(answer, boards=tuple(row[0] for row in (*current, layout))).text
        if (height + layout[-1] + 16 > PAGE_HEIGHT or len(candidate_text) > 1600) and current:
            groups.append(current)
            current, height = [], header_height + footer_height
        if height + layout[-1] + 16 > PAGE_HEIGHT:
            raise ValueError("Unbounded source text; use captured text")
        if len(replace(answer, boards=(layout[0],)).text) > 1600:
            raise ValueError("Per-page text fallback exceeds budget")
        current.append(layout)
        height += layout[-1] + 16
    if current:
        groups.append(current)

    pages = []
    for page_number, group in enumerate(groups, 1):
        height = header_height + footer_height + sum(layout[-1] + 16 for layout in group)
        canvas, draw = v._background(WIDTH, height, v.RENDER_SCALE)
        draw.rounded_rectangle((20, 20, WIDTH - 20, header_height - 12), radius=22, fill=v.PAPER)
        _text(draw, ["歌曲榜线"], 36, 33, 38)
        mode = f"精确名次 T{answer.ranks[0]}" if len(answer.ranks) == 1 else "全部挑战歌曲" if len(answer.boards) > 1 else "单曲关键名次"
        _text(draw, [mode], 38, 92, 22, v.ACCENT)
        _art(canvas, draw, assets.get(event.banner), (438, 32, 246, 105), "活动图暂缺")
        y = _text(draw, title_lines, 36, 150, 28, step=40)
        _text(draw, [f"{SERVERS[event.server][0]} · {event_status(event)} · 活动 {event.event_id}"], 38, y + 4, 21, v.ACCENT)
        y += 42
        _text(draw, [f"开始 {display_time(event.start_ms, event.server)}", f"结束 {display_time(event.end_ms, event.server)}  {zone_label(event.server)}"], 38, y, 20, v.MUTED, 30)
        if preview_label:
            _text(draw, [preview_label], 38, y + 64, 18, v.ACCENT)
        top = header_height
        for board, name_lines, titles_height, columns, size, state_lines, card_height in group:
            draw.rounded_rectangle((20, top, WIDTH - 20, top + card_height), radius=20, fill=v.PAPER, outline=v.BORDER, width=1)
            _art(canvas, draw, assets.get(board.song.jacket), (36, top + 18, 90, 90), "封面暂缺")
            _text(draw, name_lines, 146, top + 16, 26, step=38)
            _text(draw, [f"曲目 {board.song.music_id} · 挑战 {board.song.challenge_id}"], 148, top + titles_height - 24, 17, v.MUTED)
            grid_y = top + titles_height + 16
            gap, inner = 12, 632
            cell_width = (inner - gap * (columns - 1)) / columns
            for index, rank in enumerate(answer.ranks):
                x = 36 + (index % columns) * (cell_width + gap)
                y = grid_y + index // columns * 90
                draw.rounded_rectangle((x, y, x + cell_width, y + 80), radius=12, fill=v.SURFACE)
                _text(draw, [f"T{rank}"], x + 14, y + 5, 17, v.ACCENT)
                value = str(board.score(rank)) if board.score(rank) is not None else "暂无数据"
                _text(draw, [value], x + 14, y + 27, size if value != "暂无数据" else 26)
            y = grid_y + math.ceil(len(answer.ranks) / columns) * 90
            _text(draw, state_lines, 38, y + 5, 18, v.MUTED, 27)
            _text(draw, [f"源采集 {display_time(board.fetched_ms, event.server)}  {zone_label(event.server)}"], 38, y + len(state_lines) * 27 + 10, 18, v.MUTED)
            top += card_height + 16
        y = _text(draw, note_lines, 38, top + 2, 18, v.MUTED, 27)
        _text(draw, ["来源 MoeNotes · 非官方 · 活动挑战歌曲 Top 100", "按来源响应位置；非预测，非确认终榜"], 38, y + 8, 18, v.MUTED, 28)
        v._write(draw, f"TAKI  ·  {page_number}/{len(groups)}", 38, y + 69, 644, 16, v.ACCENT)
        subset = replace(answer, boards=tuple(layout[0] for layout in group))
        pages.append(CutoffPage(v._bytes(canvas), subset.text))
    return tuple(pages)
