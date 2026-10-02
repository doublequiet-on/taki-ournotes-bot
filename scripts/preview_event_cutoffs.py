# L3
# Input: sanitized read-only snapshot JSONs plus independently saved public artwork.
# Output: three real-snapshot views, synthetic stress pages, mobile previews and a manifest.
# Pos: Scripts / offline visual acceptance; see L2.md.
# Effects: reads explicit inputs, writes only the requested output directory; never fetches or sends.
"""Offline preview. Requires the sanitized 2026-10-02 investigation snapshot format."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from PIL import Image

from ournotes_bot.query.event_cutoff_query import CutoffAnswer, CutoffRequest
from ournotes_bot.rendering.event_cutoff_visuals import render_cutoff
from ournotes_bot.sources.moenotes_events import EventCutoffRepository, EventSnapshot, EventSong, BoardSnapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-dir", required=True, type=Path)
    parser.add_argument("--art-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    inputs = {}
    def read(name):
        raw = (args.snapshot_dir / name).read_bytes()
        inputs[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    body = next(row["body"] for row in read("current-activity-samples.json") if row["server"] == "jp")
    meta = read("jp-metadata-sample.json")
    tables = dict(zip(("MasterEvent", "MasterLiveMusic", "MasterStoryChapter", "MasterText"),
                      (meta["event"], meta["songs"], meta["chapters"], meta["texts"])))
    tables["MasterChallengeMusic"] = read("challenge-identity-sample.json")["rows"]
    tables["version"] = meta["version"]
    release = read("asset-version.json")["regions"]["jp"]
    tables["asset_version"] = release["resource_version"] + ":" + release["locales"]["ja"]["snapshot"]
    event = EventSnapshot("jp", body["eventId"], "", body["eventStatus"], body["startAt"], body["endAt"],
                          tuple(EventSong(row["challengeMusicId"], row["musicId"], "") for row in body["challengeRankings"]))
    event = EventCutoffRepository._enrich(event, tables)
    boards = []
    for song in event.songs:
        data = read(f"jp-challenge-{song.challenge_id}.json")
        fetched, server = (int(data["headers"][field]) for field in ("x-fetched-at", "x-server-time"))
        boards.append(BoardSnapshot(song, tuple(row["score"] for row in data["records"]), fetched, server,
                                   int(datetime.fromisoformat(data["local_received_at"]).timestamp() * 1000),
                                   (server - fetched) / 1000, "离线脱敏取样", ("来源时钟与本机有偏差",)))
    overview = CutoffAnswer(CutoffRequest(), event, tuple(boards))
    def artwork(evt, urls):
        result = {}
        for url in urls:
            filename = Path(url).name
            path = args.art_dir / filename
            if not path.is_file():
                path = args.snapshot_dir / ("jp-banner.webp" if "Banner/Chapter" in url else "jp-jacket.webp" if "100109" in url else filename)
            if path.is_file():
                with Image.open(path) as image:
                    result[url] = image.convert("RGB")
        return result
    variants = [("overview", overview), ("song", replace(overview, request=CutoffRequest(query=boards[0].song.title), boards=(boards[0],))),
                ("rank37", replace(overview, request=CutoffRequest(query=boards[0].song.title, rank=37), boards=(boards[0],)))]
    stress = replace(overview, event=replace(event, title="合成压力样例 · 中文と日本語 / 长标题换行验收", banner="", notes=("合成分数和名称，仅用于排版验收。",)),
                     boards=tuple(replace(boards[i % 3], song=replace(boards[i % 3].song,
                        title=f"合成歌曲 {i + 1} · これはぼくたちの生存のあらすじ / 中文长名称 37", jacket=""),
                        scores=(12345678901234567890,) * 100 if i != 2 else (), status="合成测试" if i != 2 else "本曲来源暂不可用") for i in range(8)))
    variants.append(("synthetic-stress", stress))
    outputs = []
    for name, answer in variants:
        synthetic = name.startswith("synthetic")
        pages = render_cutoff(answer, asset_loader=artwork,
                              preview_label="合成离线测试 · 非真实榜分" if synthetic else "2026-10-02 脱敏取样 · 离线预览")
        for index, page in enumerate(pages, 1):
            target = args.output_dir / (name + (f"-{index}" if len(pages) > 1 else "") + ".jpg")
            target.write_bytes(page.image)
            target.with_suffix(".txt").write_text(page.text, encoding="utf-8")
            with Image.open(io.BytesIO(page.image)) as image:
                width, height = image.size
                image.resize((430, round(height * 430 / width)), Image.Resampling.LANCZOS).save(target.with_name(target.stem + "-mobile.jpg"), quality=95)
            outputs.append({"file": target.name, "bytes": len(page.image), "size": [width, height],
                            "sha256": hashlib.sha256(page.image).hexdigest(), "synthetic": synthetic})
    (args.output_dir / "manifest.json").write_text(json.dumps({"input_sha256": inputs, "outputs": outputs,
         "source": "MoeNotes", "evidence": "offline rendering only; not a live query or QQ delivery"}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(outputs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
