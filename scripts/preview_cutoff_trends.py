# L3
# Input: fixed synthetic event/board/history examples and an explicit output directory.
# Output: trend/table originals, 430px layout previews and a hash/dimension manifest.
# Pos: Scripts / offline cutoff visual acceptance; see L2.md.
# Effects: writes only preview files; no source, history, model or QQ requests.
"""Offline synthetic cutoff previews; no source data, network or bot startup."""
from __future__ import annotations

import argparse
import io
import json
import hashlib
import os
import sys
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
    os.environ.update(QQ_APP_ID='', QQ_APP_SECRET='', AI_API_KEY='')
    def audit(event, arguments):
        if event in {'socket.connect', 'socket.getaddrinfo', 'socket.sendto'}:
            raise RuntimeError('preview external network blocked')
    sys.addaudithook(audit)
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
    board = BoardSnapshot(song, tuple(latest), series[-1].time_ms, series[-1].time_ms, series[-1].time_ms, 0, "合成当前值",
                          player_ids=tuple(str(11000000001 + rank) for rank in range(100)),
                          player_names=tuple(f'合成玩家{rank + 1}' for rank in range(100)))
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
    songs = tuple(replace(song, challenge_id=str(i), music_id=str(100 + i),
                          title=title, effective_start_ms=start, effective_end_ms=start + 86400000)
                  for i, title in enumerate(('夢我夢中 · 合成样例', 'Dumb Rock! · 合成样例', '長い名前の楽曲 · 合成样例'), 1))
    event3 = replace(event, songs=songs)
    boards = tuple(replace(board, song=s, scores=tuple(20_000_000 + i * 300_000 - r * 12345 for r in range(100)),
                           player_ids=tuple(str(11000000001 + i * 100 + rank) for rank in range(100)),
                           player_names=tuple(f'玩家{rank + 1}' for rank in range(100)),
                           fetched_ms=series[-1].time_ms - i * 20000, status='合成当前观测') for i,s in enumerate(songs))
    histories = tuple((s.challenge_id, HistoryView(tuple(replace(p, scores=tuple(
        boards[i].score(rank) - (len(series)-1-index)*1500 for rank in (1,2,3,10,100)))
        for index,p in enumerate(series)))) for i,s in enumerate(songs))
    triple = replace(base, event=event3, boards=boards, histories=histories)
    variants.update({
        'three-trends': triple,
        'single-song2-trend': replace(triple, boards=(boards[1],)),
        'three-near-50': replace(triple, request=CutoffRequest(rank=50), histories=()),
        'single-range-20-40': replace(triple, request=CutoffRequest(mode='range', range_bounds=(20,40)), boards=(boards[1],), histories=()),
        'boundary-100': replace(triple, request=CutoffRequest(rank=100), histories=()),
        'full-1-100': replace(triple, request=CutoffRequest(mode='range', range_bounds=(1,100)), histories=()),
        'three-zero-points': replace(triple, histories=tuple((s.challenge_id,HistoryView(warning='暂无历史，从启用后逐步积累。')) for s in songs)),
        'mixed-history': replace(triple, histories=(histories[0],(songs[1].challenge_id,HistoryView(series[-1:])),(songs[2].challenge_id,HistoryView()))),
    })
    long_songs = tuple(replace(s,title='合成长歌名 · これはぼくたちの生存のあらすじ / GO en 37 — 完整名称') for s in songs)
    long_boards = tuple(replace(b,song=long_songs[i],scores=tuple((10**20 + r * 1234567) if r != 49 else 0 for r in range(100)) if i != 1 else (),
                               status='合成当前值' if i != 1 else '本曲来源暂不可用') for i,b in enumerate(boards))
    variants['stress-table'] = replace(triple,event=replace(event3,songs=long_songs),boards=long_boards,
                                      request=CutoffRequest(rank=50),histories=())
    variants['long-player-id'] = replace(triple,boards=tuple(replace(b,player_ids=tuple('12345678901234567890' for _ in b.scores))
                                                          for b in boards),request=CutoffRequest(rank=50),histories=())
    flat_points = tuple(replace(p, scores=(10**15,) * 5) for p in series)
    variants['three-flat-large-integers'] = replace(triple, histories=tuple(
        (s.challenge_id, HistoryView(flat_points)) for s in songs))
    report = []
    for name, answer in variants.items():
        pages = render_cutoff(answer, preview_label="合成样例 · 离线布局验收（封面占位）")
        for index,page in enumerate(pages,1):
            stem = name if len(pages)==1 else f'{name}-p{index}'
            (args.output / f"{stem}.jpg").write_bytes(page.image)
            (args.output / f"{stem}.txt").write_text(page.text,encoding='utf-8')
            with Image.open(io.BytesIO(page.image)) as source:
                preview = source.resize((430, round(source.height * 430 / source.width)), Image.Resampling.LANCZOS)
                preview.save(args.output / f"{stem}-430.png")
                report.append({"name": stem, 'variant':name, 'page':index, 'pages':len(pages), 'synthetic':True,
                               'sha256':hashlib.sha256(page.image).hexdigest(), "bytes": len(page.image), "width": source.width, "height": source.height})
    (args.output / "previews.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
