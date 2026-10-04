# L3
# Input: Captured current scores/digital IDs/usernames, selected ranks and score-only local history views.
# Output: A shared multi-song overview or a single-song card, with captured text fallback.
# Pos: Rendering / Song challenge trends; see L2-2-Song.md.
# Effects: Serialized Pillow rendering and existing encoder; no source/history queries.
"""Observed points on a real time axis, with explicit gaps and integer arithmetic."""
from __future__ import annotations

import threading
from dataclasses import replace

from PIL import ImageDraw

from .. import visuals as v
from ..query.event_cutoff_query import event_status, song_period_lines
from ..sources.moenotes_events import SERVERS, display_time, zone_label
from .event_cutoff_visuals import (CutoffPage, _art, _lines, _text, _header_layout,
                                    _draw_header, _footer_lines)

COLORS = ("#bd4769", "#367fb0", "#3c896d", "#9c69ba", "#a36d25")
_DRAW_SLOT = threading.BoundedSemaphore(1)


def segments(view, index):
    groups, current = [], []
    for point in view.points:
        value = point.scores[index]
        if point.break_before or value is None:
            if current:
                groups.append(tuple(current))
                current = []
        if value is not None:
            current.append((point.time_ms, value))
    if current:
        groups.append(tuple(current))
    return tuple(groups)


def coordinates(stamp, score, bounds, box):
    left, top, width, height = box
    first, last, low, high = bounds
    # All subtraction and multiplication stay integers, even above float precision.
    x = left + (stamp - first) * width // max(1, last - first)
    y = top + height - (score - low) * height // max(1, high - low)
    return x, y


def render_trends(answer, assets, preview_label=""):
    with _DRAW_SLOT:
        if len(answer.boards) == 1:
            return (_card(answer, answer.boards[0], assets, preview_label),)
        return _overview(answer, assets, preview_label)


def _card(answer, board, assets, preview_label):
    event = answer.event
    view = answer.history_for(board)
    _, measure = v._background(720, 20, v.RENDER_SCALE)
    title = _lines(measure, answer.song_label(board), 492, 28)
    event_lines = _lines(measure, event.title, 642, 22)
    header = 198 + 32 * len(event_lines) + max(90, len(title) * 40)
    ranks = answer.ranks
    id_lines = [_lines(measure, 'ID：' + answer.id_label(board, rank), 530, 18) for rank in ranks]
    name_lines = [_lines(measure, '用户名：' + answer.name_label(board, rank), 530, 18) for rank in ranks]
    for rank in ranks:
        value = str(board.score(rank)) if board.score(rank) is not None else "暂无数据"
        if measure.textlength(value, font=v._font(24)) > 530:
            raise ValueError("full score exceeds readable card width")
    values = [value for p in view.points for value in p.scores if value is not None]
    plot_height = 396
    zone = zone_label(event.server)
    details = [f"活动状态：{event_status(event)}",
               f"活动开始：{display_time(event.start_ms, event.server)} {zone}",
               f"活动结束：{display_time(event.end_ms, event.server)} {zone}",
               *song_period_lines(event, board.song), board.status,
               f"当前源采集：{display_time(board.fetched_ms, event.server)} {zone}"]
    if view.points:
        details.append(f"历史末次观测：{display_time(view.points[-1].time_ms, event.server)} {zone_label(event.server)}")
    coincident = {}
    for index, rank in enumerate(ranks):
        series = tuple(point.scores[index] for point in view.points)
        if any(value is not None for value in series):
            coincident.setdefault(series, []).append(f"T{rank}")
    for labels in coincident.values():
        if len(labels) > 1:
            details.append("同值历史重叠：" + " / ".join(labels) + "；各排名均保留。")
    details += [view.warning, *board.notes, *event.notes]
    if preview_label:
        details.insert(0, preview_label)
    detail_lines = [line for item in details if item for line in _lines(measure, item, 640, 18)]
    height = header + sum(46 + (len(ids) + len(names)) * 24 for ids, names in zip(id_lines, name_lines)) + plot_height + len(detail_lines) * 28 + 172
    if height > 2300:
        raise ValueError("trend card exceeds existing image budget")
    subset = replace(answer, boards=(board,))
    if len(subset.text) > 1700:
        raise ValueError("per-song fallback exceeds text budget")
    canvas, draw = v._background(720, height, v.RENDER_SCALE)
    draw.rounded_rectangle((20, 20, 700, height - 20), radius=22, fill=v.PAPER)
    _text(draw, ["歌曲榜线 · 观测趋势"], 36, 32, 32)
    _art(canvas, draw, assets.get(event.banner), (454, 28, 224, 92), "活动图暂缺")
    _text(draw, [f"{SERVERS[event.server][0]} · 活动 {event.event_id} · {zone_label(event.server)}"], 38, 90, 20, v.ACCENT)
    y = _text(draw, event_lines, 38, 134, 22, step=32)
    _art(canvas, draw, assets.get(board.song.jacket), (38, y + 12, 90, 90), "封面暂缺")
    _text(draw, title, 150, y + 12, 28, step=40)
    _text(draw, [f"曲目 {board.song.music_id} · 挑战 {board.song.challenge_id}"], 150, header - 44, 18, v.MUTED)
    y = header
    for index, rank in enumerate(ranks):
        color = COLORS[index]
        draw.rectangle((40, y + 9, 50, y + 23), fill=color)
        _text(draw, [f"T{rank}"], 60, y, 22, color)
        value = str(board.score(rank)) if board.score(rank) is not None else "暂无数据"
        _text(draw, [value], 146, y - 2, 24)
        _text(draw, name_lines[index], 146, y + 30, 18, v.INK, 24)
        _text(draw, id_lines[index], 146, y + 30 + len(name_lines[index]) * 24, 18, v.MUTED, 24)
        y += 46 + (len(id_lines[index]) + len(name_lines[index])) * 24
    chart_top = y + 60
    box = (140, chart_top, 526, 240)
    draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), outline=v.BORDER, width=1)
    valid_times = {p.time_ms for p in view.points if any(n is not None for n in p.scores)}
    if not values:
        _text(draw, ["暂无历史曲线", "启用采集后逐步积累；上方为当前值。"], 160, chart_top + 84, 20, v.MUTED)
    else:
        first, last = min(p.time_ms for p in view.points), max(p.time_ms for p in view.points)
        low, high = min(values), max(values)
        if low == high:
            low, high = max(0, low - 1), high + 1
        translated = len(str(high)) > 12
        base = low if translated else 0
        scale_label = f"纵轴 = {base} + 刻度差值" if translated else "纵轴：观测分数（线性）"
        axis_lines = _lines(measure, scale_label, 640, 18)
        if len(axis_lines) > 2 or len(str(high - base)) > 12:
            raise ValueError("axis integer exceeds readable plot budget")
        _text(draw, axis_lines, 38, y + 4, 18, v.MUTED, 25)
        for fraction in (0, 1, 2):
            tick = low + (high - low) * fraction // 2
            _, tick_y = coordinates(first, tick, (first, last, low, high), box)
            draw.line((140, tick_y, 666, tick_y), fill=v.BORDER, width=1)
            label = ("+" if translated else "") + str(tick - base)
            _text(draw, [label], 28, tick_y - 12, 17, v.MUTED)
        native = ImageDraw.Draw(canvas)
        factor = v.RENDER_SCALE
        for index, rank in enumerate(ranks):
            for group in segments(view, index):
                points = [coordinates(t, n, (first, last, low, high), box) for t, n in group]
                scaled = [(x * factor, py * factor) for x, py in points]
                if len(scaled) > 1:
                    native.line(scaled, fill=COLORS[index], width=2 * factor)
                # Nested outlines remain identifiable when ranks overlap exactly.
                radius = (2 + index) * factor
                # Dense nested markers obscure the inner ranks. Keep the line
                # through every observation, with at most eight marker positions.
                marker_indices = {i * (len(scaled) - 1) // 7 for i in range(8)}
                for x, py in (scaled[i] for i in sorted(marker_indices)):
                    native.rectangle((x - radius, py - radius, x + radius, py + radius),
                                     outline=COLORS[index], width=factor)
        _text(draw, [display_time(first, event.server)[5:16]], 135, chart_top + 250, 17, v.MUTED)
        _text(draw, [display_time(last, event.server)[5:16]], 520, chart_top + 250, 17, v.MUTED)
        caption = "仅 1 个有效观测点，不足以形成曲线。" if len(valid_times) == 1 else "方点为真实观测，直线仅辅助阅读；缺口处断线。"
        _text(draw, [caption], 38, chart_top + 284, 18, v.MUTED)
    y += plot_height
    y = _text(draw, detail_lines, 38, y + 12, 18, v.MUTED, 28)
    _text(draw, ["各排名独立保留；同值曲线会重叠。", "来源 MoeNotes · 非官方 · 活动挑战歌曲 Top 100", "非预测、非确认终榜；历史末值不代替当前值。"], 38, y + 18, 18, v.MUTED, 28)
    return CutoffPage(v._bytes(canvas), subset.text)


def _column_layout(answer, board, measure, width):
    inner = width - 32
    title = _lines(measure, answer.song_label(board).partition(' · ')[2], inner, 24)
    rows = []
    for rank in answer.ranks:
        value = str(board.score(rank)) if board.score(rank) is not None else '暂无数据'
        inline = measure.textlength(value, font=v._font(25)) <= inner - 66
        lines = [value] if inline else _lines(measure, value, inner, 22)
        ids = _lines(measure, 'ID：' + answer.id_label(board, rank), inner, 18)
        names = _lines(measure, '用户名：' + answer.name_label(board, rank), inner, 18)
        rows.append((rank, lines, inline, names, ids, (44 if inline else 30 + len(lines) * 32) + (len(names) + len(ids)) * 24))
    view = answer.history_for(board)
    values = [score for p in view.points for score in p.scores if score is not None]
    axis = []
    if values:
        base = min(values) if len(str(max(values))) > 9 else 0
        axis = _lines(measure, f'纵轴 = {base} + 刻度差值' if base else '纵轴：观测分数（线性）', inner, 17)
    valid_times = {p.time_ms for p in view.points if any(n is not None for n in p.scores)}
    caption = ('暂无历史曲线，上方为当前值。' if not values else
               '仅 1 个有效观测点，不足以形成曲线。' if len(valid_times) == 1 else
               '方点为真实观测，缺口处断线；不外推。')
    caption = _lines(measure, caption, inner, 18)
    details = [board.status, '当前源采集：', display_time(board.fetched_ms, answer.event.server), zone_label(answer.event.server),
               *song_period_lines(answer.event, board.song), view.warning, *board.notes]
    if view.points:
        details.extend(('历史末次观测：', display_time(view.points[-1].time_ms, answer.event.server)))
    coincident = {}
    for index, rank in enumerate(answer.ranks):
        series = tuple(point.scores[index] for point in view.points)
        if any(value is not None for value in series):
            coincident.setdefault(series, []).append(f'T{rank}')
    for labels in coincident.values():
        if len(labels) > 1:
            details.extend(('同值历史重叠：', ' / '.join(labels), '各排名均保留。'))
    lines = [line for item in details if item for line in _lines(measure, item, inner, 18)]
    title_height = 112 + len(title) * 34
    plot_height = len(axis) * 25 + 288 + len(caption) * 27 + 18
    height = title_height + sum(row[5] for row in rows) + plot_height + len(lines) * 27 + 30
    return title, rows, axis, caption, lines, title_height, height


def _column_plot(canvas, draw, answer, board, x, top, width, axis, caption, bounds):
    view = answer.history_for(board)
    top = _text(draw, axis, x + 16, top + 6, 17, v.MUTED, 25)
    box = (x + 100, top + 18, width - 118, 218)
    draw.rectangle((box[0], box[1], box[0] + box[2], box[1] + box[3]), outline=v.BORDER, width=1)
    values = [score for p in view.points for score in p.scores if score is not None]
    if values:
        first, last = bounds
        low, high = min(values), max(values)
        base = low if len(str(high)) > 9 else 0
        if low == high:
            low, high = max(0, low - 1), high + 1
        if len(str(high - base)) > 9:
            raise ValueError('integer axis exceeds readable overview budget')
        for part in (0, 1, 2):
            value = low + (high - low) * part // 2
            _, y = coordinates(first, value, (first, last, low, high), box)
            draw.line((box[0], y, box[0] + box[2], y), fill=v.BORDER, width=1)
            delta = value - base
            _text(draw, [('+' if base and delta >= 0 else '') + str(delta)], x + 16, y - 10, 16, v.MUTED)
        native = ImageDraw.Draw(canvas)
        factor = v.RENDER_SCALE
        for index, rank in enumerate(answer.ranks):
            for group in segments(view, index):
                points = [coordinates(t, n, (first, last, low, high), box) for t, n in group]
                scaled = [(px * factor, py * factor) for px, py in points]
                if len(scaled) > 1:
                    native.line(scaled, fill=COLORS[index], width=2 * factor)
                radius = (2 + index) * factor
                markers = {i * (len(scaled) - 1) // 7 for i in range(8)}
                for px, py in (scaled[i] for i in sorted(markers)):
                    native.rectangle((px - radius, py - radius, px + radius, py + radius), outline=COLORS[index], width=factor)
        _text(draw, ['始 ' + display_time(first, answer.event.server)[5:16],
                     '末 ' + display_time(last, answer.event.server)[5:16]], x + 100, top + 240, 17, v.MUTED, 24)
    else:
        _text(draw, ['暂无历史'], x + 110, top + 105, 18, v.MUTED)
    return _text(draw, caption, x + 16, top + 288, 18, v.MUTED, 27)


def _overview(answer, assets, preview_label):
    width = 960
    _, measure = v._background(width, 20, v.RENDER_SCALE)
    header = _header_layout(answer, width, measure, preview_label)
    footer = _footer_lines(answer, measure, width)
    times = [p.time_ms for _, view in answer.histories for p in view.points]
    bounds = (min(times), max(times)) if times else (0, 0)
    groups = [answer.boards[index:index + 3] for index in range(0, len(answer.boards), 3)]
    pages = []
    for page_number, group in enumerate(groups, 1):
        column_width = (width - 40 - 16 * (len(group) - 1)) // len(group)
        layouts = [_column_layout(answer, board, measure, column_width) for board in group]
        column_height = max(layout[-1] for layout in layouts)
        height = header[2] + column_height + len(footer) * 28 + 72
        subset = replace(answer, boards=tuple(group))
        text = (f'第 {page_number}/{len(groups)} 页\n' if len(groups) > 1 else '') + subset.text
        if height > 2300 or width * height * v.RENDER_SCALE**2 > 12_000_000 or len(text) > 1800:
            raise ValueError('complete overview exceeds existing image/text budget')
        canvas, draw = v._background(width, height, v.RENDER_SCALE)
        _draw_header(canvas, draw, answer, assets, width, header, preview_label, '歌曲榜线 · 观测趋势')
        for index, (board, layout) in enumerate(zip(group, layouts)):
            x, top = 20 + index * (column_width + 16), header[2]
            title, rows, axis, caption, details, title_height, _ = layout
            draw.rounded_rectangle((x, top, x + column_width, top + column_height), radius=18, fill=v.PAPER, outline=v.BORDER, width=1)
            _art(canvas, draw, assets.get(board.song.jacket), (x + 16, top + 14, 72, 72), '封面暂缺')
            _text(draw, [answer.song_label(board).partition(' · ')[0]], x + 106, top + 22, 26, v.ACCENT)
            _text(draw, title, x + 16, top + 100, 24, step=34)
            y = top + title_height
            for color_index, (rank, lines, inline, names, ids, row_height) in enumerate(rows):
                color = COLORS[color_index]
                draw.rectangle((x + 16, y + 10, x + 24, y + 24), fill=color)
                _text(draw, [f'T{rank}'], x + 34, y + 2, 22, color)
                if inline:
                    right = x + column_width - 16
                    _text(draw, lines, right - measure.textlength(lines[0], font=v._font(25)), y, 25)
                else:
                    _text(draw, lines, x + 16, y + 30, 22, step=32)
                _text(draw, names, x + 16, y + row_height - (len(names) + len(ids)) * 24 - 4, 18, v.INK, 24)
                _text(draw, ids, x + 16, y + row_height - len(ids) * 24 - 4, 18, v.MUTED, 24)
                y += row_height
            y = _column_plot(canvas, draw, answer, board, x, y, column_width, axis, caption, bounds)
            _text(draw, details, x + 16, y + 16, 18, v.MUTED, 27)
        y = _text(draw, footer, 38, header[2] + column_height + 14, 18, v.MUTED, 28)
        _text(draw, [f'TAKI · 第 {page_number}/{len(groups)} 页 · 各曲独立纵轴，当前值与历史分开'], 38, y + 12, 18, v.ACCENT)
        pages.append(CutoffPage(v._bytes(canvas), text))
    return tuple(pages)
