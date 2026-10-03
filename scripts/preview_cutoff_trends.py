"""Offline synthetic trend previews; no source data, network or bot startup."""
from __future__ import annotations

import argparse
import io
import json
from dataclasses import replace
from pathlib import Path

from PIL import Image

from ournotes_bot.query.event_cutoff_query import CutoffAnswer, CutoffRequest
from ournotes_bot.sources.moenotes_events import EventSnapshot, EventSong, BoardSnapshot
from ournotes_bot.sources.cutoff_history import HistoryPoint, HistoryView
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    start = 1790937600000
    song = EventSong("1", "101", "合成歌曲 · 观测趋势", collect_status="collecting", position_source="responseOrder")
    event = EventSnapshot("jp", "1", "合成活动 · 查询升级视觉验收", "nowOn", start, start + 86400000, (song,))
    series = tuple(HistoryPoint(start + i * 300000, tuple(3000000 - k * 300000 + i * 1500 - (20000 if 100 < i < 115 else 0)
                                                       for k in range(5)), start + i * 300000, i == 150)
                   for i in range(288) if not 140 < i < 150)
    latest = [None] * 100
    for rank, value in zip((1, 2, 3, 10, 100), series[-1].scores):
        latest[rank - 1] = value
    board = BoardSnapshot(song, tuple(latest), series[-1].time_ms, series[-1].time_ms, series[-1].time_ms, 0, "合成当前值")
    base = CutoffAnswer(CutoffRequest(), event, (board,), histories=((song.challenge_id, HistoryView(series)),))
    variants = {"five-ranks": base,
                "zero-points": replace(base, histories=(("1", HistoryView(warning="暂无历史，从启用后采集。")),)),
                "one-point": replace(base, histories=(("1", HistoryView(series[-1:])),)),
                "overlap": replace(base, histories=(("1", HistoryView(tuple(replace(p, scores=(p.scores[0],) * 5) for p in series))),))}
    giant = 10**25
    huge_board = replace(board, scores=tuple(n + giant if n is not None else None for n in board.scores),
                         song=replace(song, title="合成长歌名 · 五个排名与超长整数在手机上的可读性检查"))
    variants["large-integers"] = replace(base, boards=(huge_board,), histories=(("1", HistoryView(tuple(
        replace(p, scores=tuple(n + giant for n in p.scores)) for p in series))),))
    report = []
    for name, answer in variants.items():
        page = render_cutoff(answer, preview_label="合成历史，仅用于离线视觉验收")[0]
        (args.output / f"{name}.jpg").write_bytes(page.image)
        with Image.open(io.BytesIO(page.image)) as source:
            preview = source.resize((430, round(source.height * 430 / source.width)), Image.Resampling.LANCZOS)
            preview.save(args.output / f"{name}-430.png")
            report.append({"name": name, "bytes": len(page.image), "width": source.width, "height": source.height})
    (args.output / "previews.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
