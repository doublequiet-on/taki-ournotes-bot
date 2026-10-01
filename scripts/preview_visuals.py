# L3
# Input: 公开资料／素材缓存副本、独立输出目录及预览参数。
# Output: 样图、手机检查图、概览及 manifest.json（含选定 JSON 缓存的哈希与样图元数据）。
# Pos: L2.md 的离线视觉验收工具；绘图语义见 ../src/L2-Rendering.md。
# Effects/Dependencies: 只读输入、写入输出，清空凭据并阻断网络；依赖查询／渲染代码和 Pillow，基线模式用 git show 读取已提交绘图实现。

"""Render local visual-review samples without QQ, credentials or network access.

Run with PYTHONPATH=src, --cache-dir pointing at public cached game data, and
--output pointing at a separate review folder. Inputs are read-only. Baseline
mode loads only the committed renderer via git show; it never switches branches.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import types
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageOps


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--stress", action="store_true")
    args = parser.parse_args()
    source, output = args.cache_dir.resolve(), args.output.resolve()
    if output == source or output.is_relative_to(source):
        parser.error("Output must be separate from the source cache directory")
    output.mkdir(parents=True, exist_ok=True)
    os.environ.update(QQ_APP_ID="", QQ_APP_SECRET="", AI_API_KEY="")

    def offline(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            raise RuntimeError("Visual previews must stay offline")

    sys.addaudithook(offline)
    from ournotes_bot import visuals
    from ournotes_bot.commands import resolve_command, page_notice
    from ournotes_bot.data import SongRepository
    from ournotes_bot.sources.haneoka.song_meta import MetaRepository
    from ournotes_bot.sources import yatta
    from ournotes_bot.sources.yatta import BASE

    if args.baseline:
        root = Path(__file__).resolve().parents[1]
        code = subprocess.check_output(["git", "show", "HEAD:src/ournotes_bot/visuals.py"], cwd=root)
        # The committed renderer may predate the directory migration. Keep its
        # implementation and comparison path, adapting only moved dependencies.
        for old, new in (("yatta", "sources.yatta"),
                         ("song_traits", "sources.haneoka.song_traits"),
                         ("efficiency_query", "query.efficiency_query"),
                         ("image_output", "rendering.image_output"),
                         ("card_visuals", "rendering.card_visuals")):
            code = code.replace(f"from .{old} import ".encode(), f"from .{new} import ".encode())
        module = types.ModuleType("ournotes_bot._baseline_visuals")
        module.__package__ = "ournotes_bot"
        exec(compile(code, "<baseline-visuals>", "exec"), module.__dict__)
        visuals = module

    cache_files = [source / "ournotes-cache.json", source / "haneoka-meta-jp.json",
                   source / "haneoka-song-traits-jp.json", source / "haneoka-member-list-jp.json"]
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in cache_files if p.exists()}
    repo = SongRepository(BASE, cache_files[0])
    repo._load_cache()  # Explicitly avoid load(), which may refresh and write.

    def no_fetch(_path):
        raise OSError("Offline preview uses the existing snapshot only")

    repo.song_meta = MetaRepository(cache_files[1], fetch=no_fetch)
    asset_reads, missing = set(), set()

    def asset(url, size, *, contain=False):
        filename = hashlib.sha256(url.encode()).hexdigest() + ".png"
        path = source / "asset-cache" / filename
        if not path.is_file():
            missing.add(url)
            return None
        asset_reads.add(url)
        with Image.open(path) as picture:
            if contain:
                return ImageOps.contain(picture.convert("RGBA"), size, method=Image.Resampling.LANCZOS)
            return ImageOps.fit(picture.convert("RGB"), size, method=Image.Resampling.LANCZOS)

    samples = {}
    def save(name, render):
        start = time.perf_counter()
        raw = render()
        (output / (name + ".jpg")).write_bytes(raw)
        with Image.open(io.BytesIO(raw)) as picture:
            samples[name] = {"size": list(picture.size), "bytes": len(raw),
                             "seconds": round(time.perf_counter() - start, 3)}
            phone = picture.copy()
            phone.thumbnail((430, 10000), Image.Resampling.LANCZOS)
            phone.save(output / (name + "-phone.png"))

    songs = list(resolve_command("/查曲 MyGO", repo).songs)
    cards = list(resolve_command("/查卡 立希", repo).cards)
    supports = list(resolve_command("/查支援卡 立希", repo).support_cards)
    if not songs or not cards or not supports:
        raise ValueError("Snapshot must contain MyGO songs and Taki member/support cards")
    card = max(cards, key=lambda item: (item.rarity, item.performance))
    support = supports[0]
    support_detail = source / "preview-details" / f"support-{support.id}.json"
    if support_detail.is_file():
        raw = json.loads(support_detail.read_text(encoding="utf-8"))
        with patch.object(yatta, "support_card_detail", return_value=raw):
            support = repo.support_card_with_detail(support)
    chart_song = next((song for song in songs if song.id == 100001), songs[0])
    chart_path = source / "chart-cache" / "0001_0001_03.json"
    score = json.loads(chart_path.read_text(encoding="utf-8"))["score"] if chart_path.exists() else None

    from ournotes_bot.sources.haneoka.haneoka_members import parse as parse_members
    member_snapshot = parse_members(json.loads(cache_files[3].read_text(encoding="utf-8"))) if cache_files[3].exists() else None
    with patch.object(visuals, "_asset", side_effect=asset), patch("ournotes_bot.rendering.member_list_visuals.get_snapshot", return_value=member_snapshot):
        save("01-songs", lambda: visuals.render_song_list(songs[:8], "MyGO!!!!!", footer="本地样例 · 展示前 8 首"))
        save("02-cards", lambda: visuals.render_card_list(cards, "立希"))
        save("03-card-detail", lambda: visuals.render_card(card))
        save("04-supports", lambda: visuals.render_support_card_list(supports, "立希"))
        save("05-support-detail", lambda: visuals.render_support_card(support))
        save("06-chart", lambda: visuals.render_chart(chart_song, chart_song.charts))
        if score:
            save("07-note-chart", lambda: visuals.render_chart(chart_song, chart_song.charts, score=score))
        answer = resolve_command("/查分数表", repo).meta
        if answer and answer.cells:
            save("08-score-table", lambda: visuals.render_meta(answer))
        for name, command in (("13-song-filter", "/查曲 蓝色 激奏=JUST"),
                              ("14-song-page2", "/查曲 激奏=JUST 页2"),
                              ("15-song-mixed", "/查曲 激奏=混合")):
            result = resolve_command(command, repo).song_selection
            if result and result.songs:
                from ournotes_bot.commands import page_slice
                save(name, lambda result=result: visuals.render_song_list(page_slice(result.songs, result.page),
                     result.request.query, footer=result.footer))
        if args.stress:
            for locale in ("en", "ja"):
                save("09-songs-" + locale, lambda locale=locale: visuals.render_song_list(songs[:8], "MyGO!!!!!", locale))
            save("10-full-page", lambda: visuals.render_song_list(repo.songs[:16], "全部歌曲", footer=page_notice("songs", "全部歌曲", 1, len(repo.songs), "zh")))
            long_song = replace(songs[0], title="非常长的歌曲标题／Long title／長い曲名" * 8, localized={},
                                charts=tuple(c for c in songs[0].charts if c.difficulty == "EXPERT"))
            with patch.object(visuals, "_asset", return_value=None):
                save("11-missing-and-long", lambda: visuals.render_song_list([long_song], "边界检查：长标题、缺图、仅 EXPERT"))
                save("12-empty", lambda: visuals.render_song_list([], "边界检查：无结果"))

    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in cache_files if p.exists()}
    if before != after:
        raise AssertionError("Source snapshot changed during preview; rerun with a stable copy")
    report = {"baseline": args.baseline, "source_sha256": before, "snapshot_time": repo.last_successful_sync_at,
              "inputs_unchanged": True, "asset_count": len(asset_reads), "missing_asset_count": len(missing),
              "missing_assets": sorted(missing), "samples": samples}
    (output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # The overview uses crops for review; the originals above always retain all data.
    names = [name for name in ("01-songs", "13-song-filter", "08-score-table", "02-cards", "03-card-detail",
                              "04-supports", "05-support-detail", "06-chart", "07-note-chart") if name in samples]
    sheet = Image.new("RGB", (1740, 960 * ((len(names) + 2) // 3)), "#F4F5FA")
    draw = ImageDraw.Draw(sheet)
    for index, name in enumerate(names):
        x, y = (index % 3) * 580, (index // 3) * 960
        draw.text((x + 20, y + 12), name, font=visuals._font(22), fill="#202B4A")
        with Image.open(output / (name + ".jpg")) as picture:
            picture = picture.resize((550, round(picture.height * 550 / picture.width)), Image.Resampling.LANCZOS)
            sheet.paste(picture.crop((0, 0, 550, min(900, picture.height))), (x + 15, y + 52))
    sheet.save(output / "overview.png")
    print(json.dumps({"output": str(output), "samples": len(samples), "assets": len(asset_reads),
                      "missing_assets": len(missing), "inputs_unchanged": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()
