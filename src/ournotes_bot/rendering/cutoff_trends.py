# L3
# Input: Captured current scores, selected ranks and immutable local history views.
# Output: One complete trend card per challenge song, with matching text fallback.
# Pos: Rendering / Song challenge trends; see L2-2-Song.md.
# Effects: Serialized Pillow rendering and existing encoder; no source/history queries.
"""Observed points on a real time axis, with explicit gaps and integer arithmetic."""
from __future__ import annotations

import threading
from dataclasses import replace

from PIL import ImageDraw

from .. import visuals as v
from ..sources.moenotes_events import SERVERS, display_time, zone_label
from .event_cutoff_visuals import CutoffPage, _art, _lines, _text

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
        return tuple(_card(answer, board, assets, preview_label) for board in answer.boards)


def _card(answer, board, assets, preview_label):
    event = answer.event
    view = answer.history_for(board)
    _, measure = v._background(720, 20, v.RENDER_SCALE)
    title = _lines(measure, board.song.title, 492, 28)
    event_lines = _lines(measure, event.title, 642, 22)
    header = 198 + 32 * len(event_lines) + max(90, len(title) * 40)
    ranks = answer.ranks
    for rank in ranks:
        value = str(board.score(rank)) if board.score(rank) is not None else "暂无数据"
        if measure.textlength(value, font=v._font(24)) > 530:
            raise ValueError("full score exceeds readable card width")
    values = [value for p in view.points for value in p.scores if value is not None]
    plot_height = 396
    details = [board.status, f"当前源采集：{display_time(board.fetched_ms, event.server)} {zone_label(event.server)}"]
    if view.points:
        details.append(f"历史末次观测：{display_time(view.points[-1].time_ms, event.server)} {zone_label(event.server)}")
    details += [view.warning, *board.notes, *event.notes]
    if preview_label:
        details.insert(0, preview_label)
    detail_lines = [line for item in details if item for line in _lines(measure, item, 640, 18)]
    height = header + len(ranks) * 46 + plot_height + len(detail_lines) * 28 + 172
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
        y += 46
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
                for x, py in set(scaled):
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
