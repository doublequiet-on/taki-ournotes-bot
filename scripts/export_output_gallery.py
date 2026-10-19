# L3
# Input: repository fixtures, optional read-only public cache copy, font and output path.
# Output: offline gallery, actual encoded images, derived mobile previews, manifests and design worksheets.
# Pos: Scripts / offline output inventory; see L2.md and ../docs/OUTPUT_GALLERY.md.
# Effects: blocks network, uses production reply/render functions with isolated inputs; writes only output.
"""Export current output families without starting QQ, refreshing data or changing layouts."""
from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
from dataclasses import replace
import hashlib
import html
import io
import json
import logging
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from unittest.mock import patch

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from output_observation import Observation

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "6563f0a01ea8a0f4f817e4c744401b6e932f61a7"
FIXED_TIME = 1791763200  # 2026-10-12 00:00:00 UTC; only business wall clock is fixed.
FAMILIES = {
    "song-detail": "歌曲详情", "activity": "活动资料", "gacha": "卡池资料", "character": "人物资料",
    "song-list": "歌曲列表", "chart": "谱面资料与音符图",
    "member-list": "成员卡列表", "member-detail": "成员卡详情", "member-art": "成员原卡面",
    "snap-list": "SNAP列表", "snap-detail": "SNAP详情", "snap-art": "SNAP原卡面",
    "cutoff-trend": "歌曲榜线趋势", "cutoff-ranks": "榜线排名表", "easter-947": "固定彩蛋", "text": "纯文字与终态",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args, optional=False):
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        if optional:
            return None
        raise subprocess.CalledProcessError(result.returncode,result.args,result.stdout,result.stderr)
    return result.stdout.strip()


def version_info():
    version=git("rev-parse","HEAD",optional=True)
    available=bool(version and git("rev-parse","--verify",BASELINE+"^{commit}",optional=True))
    changes=git("diff","--stat",BASELINE,version) if available else None
    return version,available,changes


def guard_paths(output, inputs):
    for source in inputs:
        if output == source or output.is_relative_to(source) or source.is_relative_to(output):
            raise ValueError("output must be separate from every input directory")
    if output == ROOT or ROOT.is_relative_to(output):
        raise ValueError("output cannot contain the source checkout")


class Gallery:
    def __init__(self, output, only, cache, font, observe=None):
        from ournotes_bot import visuals
        from ournotes_bot.application import replies as qq
        self.v, self.qq = visuals, qq
        self.output, self.only, self.cache = output, set(only), cache
        self.rows, self.errors, self.inputs = [], [], {}
        self.trace, self.canvases, self.missing, self.assets = [], [], set(), set()
        self.font = font
        self.datasets = {}
        self.observation = Observation(output, observe) if observe else None
        self.list_only = False
        self.expected_families = set(FAMILIES)
        version,self.baseline_available,self.baseline_changes = version_info()
        self.version = version or "源码归档（Git 版本不可用；以文件哈希追溯）"
        self.code_hashes = {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p)
                            for p in (ROOT / "src/ournotes_bot").rglob("*.py")}

    def read(self, path):
        self.inputs[str(path)] = digest(path)
        return json.loads(path.read_bytes())

    def asset(self, url, size, *, contain=False, native=False):
        path = self.cache / "asset-cache" / (hashlib.sha256(url.encode()).hexdigest() + ".png") if self.cache else None
        if not path or not path.is_file():
            self.missing.add(url)
            return None
        self.inputs[str(path)] = digest(path)
        self.assets.add(url)
        with Image.open(path) as picture:
            picture = picture.convert("RGBA" if contain else "RGB")
            if native:return picture
            transform = ImageOps.contain if contain else ImageOps.fit
            return transform(picture, size, method=Image.Resampling.LANCZOS)

    def watch(self, name, function):
        def call(*args, **kwargs):
            self.trace.append(name)
            try:
                return function(*args, **kwargs)
            except Exception as exc:
                self.render_errors.append({"function": name, "error": type(exc).__name__, "message": str(exc)})
                raise
        return call

    def encode(self, image):
        self.canvases.append(list(image.size))
        return self.encoder(image)

    def sample(self, family, name, command, run, *, source="公开Haneoka固定测试夹具；素材缺失保留占位",
               synthetic=(), expected="image", route="_render_prepared → _image_from_result", ids=(), params=None):
        key = family + "-" + name
        if self.only and key not in self.only and family not in self.only:
            return None
        if self.list_only:
            self.rows.append({'id':key})
            return None
        self.trace, self.canvases, self.missing, self.assets, self.render_errors = [], [], set(), set(), []
        if self.observation:
            self.observation.reset(key)
        start, cpu = time.perf_counter(), time.process_time()
        row = {"id": key, "template_id": family, "command": command, "route": route,
               "data_source": source, "synthetic_fields": list(synthetic), "parameters": params or {},
               "code_version": self.version, "expected_mode": expected, "record_ids": list(ids)}
        try:
            reply = run()
            if reply is None:
                raise AssertionError("reply unexpectedly absent")
            if reply.text == "查询暂时失败，请稍后重试。":
                raise AssertionError("reply preparation failed; see process diagnostic log")
            pages = reply.pages or (reply,)
            image_pages = sum(p.image is not None for p in pages)
            mode = "image" if image_pages else "text"
            if mode != expected:
                raise AssertionError(f"expected {expected}, got {mode}")
            if self.render_errors:
                raise AssertionError("renderer raised: " + repr(self.render_errors))
            row.update(status="success", mode=mode, page_count=len(pages), image_page_count=image_pages,
                       visible_ids=[c.entity_id for c in reply.context.visible] if reply.context else None,
                       continuation_choices=len(reply.context.visible) if reply.context else None,
                       context_pages=reply.context.pages if reply.context else None, files=[])
            row['display_positions'] = [dict(position=i, entity_id=c.entity_id)
                                       for i,c in enumerate(reply.context.visible,1)] if reply.context else []
            row["query_page"] = reply.context.page if reply.context else row["parameters"].get("page")
            row["query_pages"] = reply.context.pages if reply.context else row["parameters"].get("query_pages")
            if not row["record_ids"]:
                row["record_ids"] = row["visible_ids"]
            row["record_ids_note"] = None if row["record_ids"] else "无实体记录或未捕获选择；详见route与parameters"
            with self.observation.phase("io") if self.observation else nullcontext():
                for number, page in enumerate(pages, 1):
                    stem = f"{key}-p{number}"
                    text_file = "texts/" + stem + ".txt"
                    (self.output / text_file).write_text(page.text, encoding="utf-8")
                    item = {"page": number, "text_file": text_file, "text": page.text, "image_file": None,
                            "original_size": None, "encoded_size": None, "bytes": None, "thumbnail_file": None,
                            "original_size_note": "纯文字无画布" if page.image is None else "预编码素材，无Pillow画布" if not self.canvases else None}
                    if page.image:
                        with Image.open(io.BytesIO(page.image)) as picture:
                            suffix = ".jpg" if picture.format == "JPEG" else ".png"
                            filename = "images/" + stem + suffix
                            (self.output / filename).write_bytes(page.image)
                            thumbnail = None
                            # Image.open reads headers lazily. Timing must not decode
                            # output pixels or allocate/encode a derived preview.
                            if not self.observation or self.observation.mode != 'timing':
                                thumb = picture.copy()
                                try:
                                    thumb.thumbnail((430, 100000), Image.Resampling.LANCZOS)
                                    thumbnail = "thumbnails/" + stem + ".png"
                                    thumb.save(self.output / thumbnail)
                                finally:
                                    thumb.close()
                            item.update(image_file=filename, format=picture.format, mime=Image.MIME.get(picture.format), encoded_size=list(picture.size), bytes=len(page.image),
                                        original_size=self.canvases[number - 1] if number <= len(self.canvases) else None,
                                        thumbnail_file=thumbnail, sha256=hashlib.sha256(page.image).hexdigest())
                        if self.observation:
                            matches = [c for c in self.observation.canvases if c.get('encoded_sha256') == item['sha256']]
                            item['canvas_indices'] = [c['index'] for c in matches]
                            item['original_size'] = matches[0]['size'] if matches else None
                    row["files"].append(item)
        except Exception as exc:
            row.update(status="failed", mode=None, page_count=None, files=[], error=f"{type(exc).__name__}: {exc}")
            self.errors.append(key)
        row.update(seconds=round(time.perf_counter() - start, 4), cpu_seconds=round(time.process_time() - cpu, 4),
                   functions=list(dict.fromkeys(self.trace)), missing_assets=sorted(self.missing), asset_urls=sorted(self.assets),
                   renderer_errors=self.render_errors, timing_scope="一次离线准备/绘图/编码/落盘总时长；未分段计时")
        if self.observation:
            row['observation'] = self.observation.result()
            row['timing_scope'] = '独占阶段墙钟；observe为取证开销；进程峰值只有独立单例可归因'
        row['evidence_class'] = ('缺素材验证' if self.missing else '合成边界' if synthetic else '固定输入视觉对照')
        row['visual_acceptance'] = '未人工验收'
        self.rows.append(row)
        print(f"{key}: {row['status']} {row.get('page_count')} page(s)", flush=True)
        return reply if row["status"] == "success" else None

    def finish(self):
        for filename, before in self.inputs.items():
            if digest(Path(filename)) != before:
                raise AssertionError("input changed during preview: " + filename)
        if self.code_hashes != {str(p.relative_to(ROOT)).replace("\\", "/"): digest(p)
                               for p in (ROOT / "src/ournotes_bot").rglob("*.py")}:
            raise AssertionError("production source changed during preview")
        present = {r["template_id"] for r in self.rows}
        if not self.only and present != self.expected_families:
            raise AssertionError("missing output families: " + repr(self.expected_families - present))
        manifest = {"schema": 2, "preview_only": True, "code_version": self.version,
                    "review_baseline": BASELINE, "baseline_available": self.baseline_available,
                    "changes_since_baseline": self.baseline_changes,
                    "production_code_sha256": self.code_hashes, "tool_sha256": digest(Path(__file__)),
                    "observation_tool_sha256": digest(Path(__file__).with_name('output_observation.py')),
                    "fixed_clock_utc": "2026-10-12T00:00:00Z", "font": {"path": str(self.font), "sha256": digest(self.font)},
                    "environment": {"python": sys.version, "platform": platform.platform(), "pillow": Image.__version__},
                    "datasets": self.datasets,
                    "input_sha256": self.inputs, "inputs_unchanged": True, "network": "blocked", "qq_delivery": "未测",
                    "scope": "selected subset" if self.only else "all registered families and representative states",
                    "samples": self.rows}
        manifest['full_activity_view'] = getattr(self, 'full_activity_view', False)
        manifest['encoding_strategy'] = getattr(self, 'encoding_strategy', 'legacy')
        manifest['observation'] = self.observation.mode if self.observation else 'disabled'
        manifest['unexecuted_families'] = sorted(self.expected_families - present)
        manifest['packaged_fonts'] = [{'path': str(p.relative_to(ROOT)), 'sha256': digest(p)}
                                     for p in sorted((ROOT/'src/ournotes_bot/rendering/assets').glob('*.ttf'))]
        self.write_json("manifest.json", manifest)
        benchmark = {"scope": "基础离线记录；非线上性能，非稳定p95", "sample_count": len(self.rows),
                     "environment": manifest["environment"], "font": manifest["font"],
                     "unmeasured": ["冷网络素材", "排队", "顺序等待", "上传", "发送回执", "真实客户端", "受控并发", "峰值内存", "编码尝试次数"],
                     "samples": [{k: r.get(k) for k in ("id", "seconds", "cpu_seconds", "status")} | {
                         "images": [{k: f[k] for k in ("original_size", "encoded_size", "bytes")} for f in r["files"]]} for r in self.rows]}
        self.write_json("benchmark.json", benchmark)
        self.write("benchmark.md", "# 基础性能记录\n\n" + benchmark["scope"] + "。单进程顺序导出，每例一次；字体/背景缓存随运行逐步变热，素材从磁盘读取并解码。耗时包含样张落盘，不用于对比生产查询等待。\n\n"
                   "|用例|墙钟秒|CPU秒|页数|状态|\n|---|---:|---:|---:|---|\n" + "\n".join(
                       f"|{r['id']}|{r['seconds']}|{r['cpu_seconds']}|{r.get('page_count')}|{r['status']}|" for r in self.rows) +
                   "\n\n未测：" + "、".join(benchmark["unmeasured"]) + "。\n")
        for name in ("field-map.md", "sketch-worksheet.md", "theme-reference.md"):
            self.write(name, (ROOT / "docs/output-gallery" / name).read_text(encoding="utf-8"))
        inventory = (ROOT / "docs/output-gallery/inventory.md").read_text(encoding="utf-8")
        inventory += f"\n\n## 本次导出\n\n代码 `{self.version}`；字体 `{self.font.name}`；输入前后哈希一致。\n\n"
        inventory += "|模板|用例数|图片页数|失败|\n|---|---:|---:|---:|\n" + "\n".join(
            f"|{family}|{sum(r['template_id']==family for r in self.rows)}|{sum(r.get('image_page_count',0) for r in self.rows if r['template_id']==family)}|{sum(r['status']!='success' for r in self.rows if r['template_id']==family)}|" for family in FAMILIES)
        self.write("inventory.md", inventory + "\n\n详情、未测与缺素材见 manifest.json；原卡面缺素材只能展示文字回退，不能凭占位声称已验收原图。\n")
        self.write("index.html", gallery_html(self.rows, self.version))

    def write(self, name, value):
        (self.output / name).write_text(value, encoding="utf-8")

    def write_json(self, name, value):
        self.write(name, json.dumps(value, ensure_ascii=False, indent=2))


def gallery_html(rows, version):
    escape = html.escape
    sections = []
    for family, label in FAMILIES.items():
        cards = []
        for row in rows:
            if row["template_id"] != family:
                continue
            pages = []
            for item in row["files"]:
                image = (f'<a href="{item["image_file"]}" target="_blank"><img loading="lazy" src="{item["thumbnail_file"] or item["image_file"]}" alt="{escape(row["id"])} 第{item["page"]}页"></a>' if item["image_file"] else "")
                dimensions = f'原画布 {item["original_size"] or "未经过画布绘制"} → 编码 {item["encoded_size"]} · {item["bytes"]} bytes' if item["image_file"] else "纯文字，无图片尺寸和字节数"
                opened = "" if item["image_file"] else " open"
                pages.append(f'<div class="page"><p>本次输出第 {item["page"]}/{row["page_count"]} 页 · {dimensions}</p>{image}<details{opened}><summary>文字回复 / 回退</summary><pre>{escape(item["text"])}</pre></details></div>')
            badge = "合成测试数据，不代表游戏实况" if row["synthetic_fields"] else "公开固定资料 / 当前素材"
            pagination = f'查询第 {row["query_page"]}/{row["query_pages"]} 页。' if row.get("query_pages") else ""
            cards.append(f'<article><h3>{escape(row["id"])}</h3><code>{escape(row["command"])}</code><p class="badge">{badge}</p><p>{escape(row["data_source"])}</p><p>合成字段：{escape("、".join(row["synthetic_fields"]) or "无")} · 缺素材 {len(row["missing_assets"])} 项</p><p>{pagination}{escape(row["status"])} · {row.get("mode")} · {row["seconds"]} 秒 · CPU {row["cpu_seconds"]} 秒</p><p>{escape(row.get("error", ""))}</p>{"".join(pages)}</article>')
        sections.append(f'<section id="{family}"><h2>{label} · {family}</h2>{"".join(cards)}</section>')
    links = " ".join(f'<a href="#{family}">{label}</a>' for family, label in FAMILIES.items())
    return '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Taki 现状样张画廊</title><style>body{margin:0;background:#eef0f5;color:#202b49;font:16px system-ui,sans-serif}header,main{max-width:1500px;margin:auto;padding:24px}nav{display:flex;gap:12px;flex-wrap:wrap}a{color:#394f90}section{display:flex;gap:20px;flex-wrap:wrap;align-items:flex-start;margin:40px 0}h2{width:100%}article{background:white;border:1px solid #bac2d5;border-radius:12px;padding:18px;width:min(450px,100%);box-sizing:border-box}img{display:block;width:100%;height:auto}p{overflow-wrap:anywhere}code{white-space:pre-wrap}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto}.badge{background:#fff1ca;padding:8px}details{margin:12px 0}.page{margin:18px 0;border-top:1px solid #ddd}</style>' + f'<header><h1>Taki 现状样张画廊</h1><p>代码 {version[:12]} · 当前生产绘图函数的离线输出 · 点击图片查看实际编码原图。</p><p>430px 图仅为派生阅读预览。每个用例保留现有分页；合成字段及缺素材逐张标注。未连接 QQ。</p><p><a href="inventory.md">覆盖盘点</a> · <a href="field-map.md">字段表</a> · <a href="sketch-worksheet.md">草图填写表</a> · <a href="theme-reference.md">主题参考</a> · <a href="benchmark.md">基础性能</a> · <a href="manifest.json">完整清单</a></p><nav>{links}</nav></header><main>{"".join(sections)}</main></html>'


def export_cases(g):
    from ournotes_bot.commands import CommandResult, handle_command, resolve_command
    from ournotes_bot.config import Settings
    from ournotes_bot.ai_query import AIQueryParser
    from ournotes_bot.data import SongRepository, Skill
    from ournotes_bot.sources.haneoka.catalog import validate
    from ournotes_bot.sources.chart_data import ChartData, ChartDataError
    from ournotes_bot.query.card_catalog import CardAnswer, CardRequest
    from ournotes_bot.query.song_query import SongAnswer, SongFilter
    from ournotes_bot.query.continuation import capture_context, Operation
    from ournotes_bot.structured_query import QuerySpec, QueryResult
    from ournotes_bot.sources.yatta import BASE

    fixture = g.read(ROOT / "tests/fixtures/haneoka_catalog.json")
    records, metadata = validate(fixture["snapshot"], FIXED_TIME)
    g.datasets["haneoka-fixture"] = metadata
    repo = SongRepository("", g.output / "isolated/cache.json", data_source="haneoka", chart_source="haneoka")
    repo.songs, repo.cards, repo.support_cards = map(list, records)
    repo.metadata, repo.cache_state = metadata, "fresh"
    repo.last_successful_sync_at = metadata["cached_at"]
    parser = AIQueryParser(Settings("", "", BASE, g.output / "isolated/cache.json", 6))
    song, card, support = repo.songs[0], repo.cards[-2], next(c for c in repo.support_cards if c.rarity == 10)
    q = g.qq
    prepare = lambda command: q.prepare_reply(command, repo, parser)
    render = lambda result: q.render_prepared(handle_command("/查曲", repo, resolved=result), result, repo, "zh", capture_context(result, repo))
    def song_result(rows, page=1, label=""):
        return CommandResult(("songs", label, page), song_selection=SongAnswer(SongFilter(query=label), tuple(rows), page))
    def card_result(rows, support_mode=False, mode="list", page=1, label=""):
        request = CardRequest(support=support_mode, mode=mode, card_id=rows[0].id if mode != "list" else None, page=page, query=label)
        return CommandResult(("support_cards" if support_mode else "cards", label, page), catalog=CardAnswer(request, tuple(rows)))

    score = {"events": {"bpm": [{"t": 0, "bpm": 190}], "sig": [{"t": 0, "sig": [4, 4]}]},
             "notes": [{"t": 9600, "pos": 6, "size": 6}, {"type": "flick", "t": 10080, "pos": 12, "size": 6, "dir": "right"},
                       {"type": "long", "node": [{"t": 10560, "pos": 6, "size": 6}, {"t": 11520, "pos": 12, "size": 6}]}]}
    with ExitStack() as stack:
        stack.enter_context(patch.object(q, "load_chart_data", return_value=ChartData(score, "haneoka", cache_state="fresh")))
        for family, command in (("song-detail", f"/查曲 {song.id}"), ("member-list", "/查卡 SSR"), ("member-detail", f"/查卡 {card.id}"),
                                ("snap-list", "/查SNAP EX"), ("snap-detail", f"/查支援卡 {support.id}")):
            g.sample(family, "normal", command, lambda command=command: prepare(command), route="_prepare_reply → resolve_command → _render_prepared → _image_from_result")
        for family, base, support_mode in (("member", card, False), ("snap", support, True)):
            one = card_result([base], support_mode)
            g.sample(family+"-list", "one-condition", "/查支援卡 EX" if support_mode else "/查卡 SSR", lambda one=one: render(one), ids=[base.id])
            unknown = replace(base, skills=(), card_type=99, rarity=99, catalog={"source": "haneoka", "detail_loaded": True, "stats_level1": None})
            for mode in ("list", "detail"):
                result = card_result([unknown], support_mode, mode)
                g.sample(family+"-"+mode, "unknown", "捕获结果夹具：未知字段", lambda result=result: render(result), synthetic=("类型", "稀有度", "属性状态", "缺技能"), ids=[unknown.id])
            long = replace(base, title="合成超长卡名 · 中文と日本語 / Long card title " * 6, localized={},
                           skills=tuple(replace(s, name="合成多效果技能", localized={}, description=s.description + "\n合成附加条件：用于长文本换行检查。" * 8) for s in base.skills),
                           catalog={**base.catalog, "detail_stale": True})
            result = card_result([long], support_mode, "detail")
            g.sample(family+"-detail", "long-stale", "捕获结果夹具：长名称/完整技能/旧缓存", lambda result=result: render(result), synthetic=("卡名", "技能附加条件", "旧缓存状态"), ids=[base.id])
            result = card_result([base, replace(base, id=900002)], support_mode, label="角色="+"长条件"*30)
            g.sample(family+"-list", "long-filter", "捕获结果夹具：长筛选条件", lambda result=result: render(result), synthetic=("查询条件", "复制卡ID"))
            for count in (1, 16, 17, 30, 31, 32, 64):
                rows = tuple(replace(base, id=900000+i, rarity=(2,3,4,10 if support_mode else 20)[i%4], card_type=i%5+1) for i in range(count))
                for page in range(1, (count+15)//16+1):
                    result = card_result(rows, support_mode, page=page)
                    g.sample(family+"-list", f"{count}-page{page}", f"合成{count}条条件查询 页{page}", lambda result=result: render(result),
                             synthetic=("记录数量", "ID", "稀有度", "类型"), ids=[c.id for c in result.catalog.visible], params={"total": count, "page": page, "query_pages": (count+15)//16})
            art = card_result([base], support_mode, "art")
            with patch.object(g, "cache", None):
                g.sample(family+"-art", "missing", "/查支援卡面 ID" if support_mode else "/查卡面 ID", lambda art=art: render(art), expected="text", ids=[base.id], source="固定资料；原卡面缺失")

        bd = next(c for c in repo.cards if c.rarity == 20)
        for mode in ("list", "detail"):
            result = card_result([bd], mode=mode, label="BD")
            g.sample("member-"+mode, "bd", "/查卡 BD" if mode=="list" else f"/查卡 {bd.id}", lambda result=result: render(result), ids=[bd.id])
        multiple = replace(card, skills=card.skills + (Skill("liveSkill", "合成第二效果", "合成效果：用于验证同类多个技能完整保留。"),))
        g.sample("member-detail", "multiple-effects", "捕获结果夹具：同类多技能", lambda: render(card_result([multiple], mode="detail")), synthetic=("第二演出技能",))
        g.sample("snap-list", "multi-character", "/查SNAP 灯 爱音", lambda: prepare("/查SNAP 灯 爱音"))
        for count in (1, 4, 16, 17, 30, 31, 32, 64):
            rows = tuple(replace(song, id=900000+i, title=f"合成歌曲 {i+1}", localized={}) for i in range(count))
            for page in range(1, (count+15)//16+1):
                result = song_result(rows, page)
                g.sample("song-list", f"{count}-page{page}", f"合成{count}首查询 页{page}", lambda result=result: render(result), synthetic=("ID", "歌曲名", "数量"),
                         ids=[s.id for s in rows[(page-1)*16:page*16]], params={"total":count,"page":page,"query_pages":(count+15)//16})
        long_song = replace(song, title="合成长歌曲名 · これはぼくたちの生存のあらすじ / Long song title " * 5, localized={},
                            charts=tuple(replace(c, notes=None) for c in song.charts))
        g.sample("song-list", "long-unknown", "捕获结果夹具：长标题/未知Note", lambda: render(song_result([long_song], label="多条件 · 颜色与激奏")), synthetic=("标题", "Note缺失", "查询标签"))
        g.sample("song-list", "traits", "/查曲 黄色 激奏=JUST", lambda: prepare("/查曲 黄色 激奏=JUST"))
        g.sample("song-list", "mixed-traits", "/查曲 激奏=混合", lambda: prepare("/查曲 激奏=混合"))
        for locale, command in (("en", f"/song {song.id}"), ("ja", f"/曲 {song.id}")):
            # Capture a known selection and let production locale rendering consume it.
            result = song_result([song])
            g.sample("song-list", locale, command, lambda locale=locale,result=result: q.render_prepared(result.song_selection.text(locale), result, repo, locale), params={"locale":locale})
        for diff in ("EASY", "NORMAL", "HARD", "EXPERT"):
            command = f"/查谱面 {song.id} {diff}"
            g.sample("chart", diff.lower(), command, lambda command=command: prepare(command), synthetic=("音符和BPM：沿用test_chart_data的布局夹具",))
        with patch.object(q, "load_chart_data", side_effect=ChartDataError("offline missing score")):
            g.sample("chart", "missing-score", f"/查谱面 {song.id}", lambda: prepare(f"/查谱面 {song.id}"))
        with patch.object(q, "load_chart_data", return_value=ChartData(score, "haneoka", cache_state="stale")):
            g.sample("chart", "stale-source", f"/查谱面 {song.id}", lambda: prepare(f"/查谱面 {song.id}"), synthetic=("音符和BPM", "旧缓存状态"))
        long_score = {**score, "notes": score["notes"] + [{"type":"long", "node":[{"t":19000,"pos":0,"size":6},{"t":70000,"pos":18,"size":6}]}]}
        with patch.object(q, "load_chart_data", return_value=ChartData(long_score, "haneoka")):
            g.sample("chart", "long-cross-column", f"/查谱面 {song.id}", lambda: prepare(f"/查谱面 {song.id}"), synthetic=("音符和BPM", "跨栏长键"))
        for family, command in (("song-list", "/问 MyGO的歌有哪些"), ("member-list", "/问 SSR成员卡有哪些"), ("snap-list", "/问 EX支援卡有哪些")):
            g.sample(family, "natural-local", command, lambda command=command: prepare(command), route="_prepare_reply → AIQueryParser本地解析 → _render_prepared")
        for family, command, operation in (("member-detail", "/查卡 SSR", Operation("select",1)), ("snap-detail", "/查SNAP EX", Operation("detail")), ("chart", f"/查曲 {song.id}", Operation("difficulty","HARD"))):
            context = capture_context(resolve_command(command,repo),repo)
            g.sample(family, "followup", command + " → /"+operation.kind, lambda context=context,operation=operation: q.prepare_followup(context,operation,repo), route="_prepare_followup → execute_followup → _render_prepared", synthetic=("音符和BPM",) if family=="chart" else ())
        for count in (30,31):
            result = QueryResult(QuerySpec("song"), songs=tuple(replace(song,id=900000+i) for i in range(count)))
            # Current pagination shows only 16: total 31 does not itself exceed the visible-choice bound.
            g.sample("text", f"choice-total-{count}", f"续查边界：总记录{count}条", lambda result=result: q.PreparedReply(f"总数 {count}；当前分页可见 {len(capture_context(result,repo).visible)}；30上限针对可见选择，当前列表页16条。"), expected="text", synthetic=("记录数量", "ID"), route="capture_context（当前分页16条；说明文字为预览工具）")
        for name,command in (("help","/帮助"), ("about","/介绍"), ("language","/语言"), ("status","/数据状态"),
                             ("debug","/调试数据"), ("abbrev","/查缩写 skk"), ("no-results","/查曲 不存在的完整歌曲名称xyz"),
                             ("invalid-page","/查卡 SSR 页999"), ("short-count","/问 MyGO歌曲有多少首"), ("unavailable","/查卡池")):
            g.sample("text", name, command, lambda command=command: prepare(command), expected="text", route="_prepare_reply → 纯文字分支")
        with patch.object(q,"song_detail_reply",side_effect=ValueError("controlled renderer failure")):
            g.sample("text","render-fallback",f"/查曲 {song.id}",lambda: prepare(f"/查曲 {song.id}"),expected="text",synthetic=("受控绘图失败",),route="_render_prepared异常 → 文字回退")
        g.sample("easter-947","normal","/查卡 947",lambda: prepare("/查卡 947"),source="仓库内固定彩蛋JPEG；不经过重编码",route="_prepare_reply → _easter_egg_reply")
        with patch.object(q,"files",side_effect=FileNotFoundError("controlled missing egg")):
            g.sample("easter-947","missing","/查卡 947",lambda: prepare("/查卡 947"),expected="text",synthetic=("彩蛋文件缺失",),route="_easter_egg_reply读取失败 → 文字")
    export_info(g, repo)
    export_cutoffs(g, repo)
    if g.cache:
        export_cached(g, parser)


def export_info(g, repo):
    from ournotes_bot.query.contracts.info import InfoAnswer, InfoRow, InfoView, info_text
    from ournotes_bot.commands import CommandResult
    for family, presentation in (('activity','activity'), ('gacha','gacha'), ('character','character')):
        for count in (1,17,32,64):
            rows = tuple(InfoRow(f'合成资料 {i+1}', (f'条目 {i+1}：中文と日本語 / English，未知状态保持。',),
                                command=f'/查角色 {i+1}') for i in range(count))
            view = InfoView('固定布局边界', rows=rows, presentation=presentation,
                            footer=('合成夹具；不代表当前游戏资料。',))
            answer = InfoAnswer(info_text((view,)), views=(view,))
            result = CommandResult(('activity' if family=='activity' else 'info','',1),
                                   **{('activity' if family=='activity' else 'info'): answer})
            g.sample(family, str(count), '捕获信息视图边界',
                     lambda result=result: g.qq.render_prepared(answer.text,result,repo,'zh'),
                     synthetic=('资料内容','条目数'), ids=list(range(count)), route='捕获 InfoAnswer → 真实 QQ 绘图分发')


def export_cached(g, parser):
    from ournotes_bot.data import SongRepository
    from ournotes_bot.sources.yatta import BASE
    from ournotes_bot.sources.chart_data import ChartDataError
    repo = SongRepository(BASE, g.cache / "ournotes-cache.json")
    g.read(repo.cache_file)
    repo._load_cache()  # Only explicit read; never load()/refresh().
    g.datasets["public-yume-cache"] = repo.metadata
    detail_path = g.cache / "preview-details/support-51.json"
    if detail_path.exists():
        base = next(c for c in repo.support_cards if c.id == 51)
        repo._detail_support_cards[51] = repo._merge_support_detail(base, g.read(detail_path))
    def chart(song, chart):
        stem = f"{song.id-100000:04d}_{song.id-100000:04d}_{('EASY','NORMAL','HARD','EXPERT').index(chart.difficulty):02d}.json"
        path = g.cache / "chart-cache" / stem
        if not path.is_file():
            raise ChartDataError("no local score")
        return g.read(path)["score"]
    with patch.object(g.qq,"load_chart_score",side_effect=chart):
        for family, command in (("song-list","/查曲 MyGO"),("chart","/查谱面 100001 EXPERT"),
                                ("song-detail","/查曲 100001"),
                                ("member-list","/查卡 立希"),("member-detail","/查卡 51"),
                                ("member-art","/查卡面 51"),("snap-list","/查支援卡 立希"),
                                ("snap-detail","/查支援卡 51"),("snap-art","/查支援卡面 51")):
            result = g.qq.resolve_command(command, repo)
            records = result.catalog.cards if result.catalog else result.songs or result.cards or result.support_cards
            g.sample(family,"cached-art",command,lambda command=command:g.qq.prepare_reply(command,repo,parser), ids=[r.id for r in records],
                     source="本地公开Project Yume旧快照/素材；当前代码绘制，不能代表当前Haneoka资料版本",route="_prepare_reply → 只读旧来源资料 → 当前绘图入口")


def export_cutoffs(g, repo):
    from ournotes_bot.sources.moenotes_events import EventSong, EventSnapshot, BoardSnapshot
    from ournotes_bot.sources.cutoff_history import HistoryPoint, HistoryView
    from ournotes_bot.query.event_cutoff_query import CutoffAnswer, CutoffRequest
    from ournotes_bot.commands import CommandResult
    from ournotes_bot.rendering import event_cutoff_visuals as cv
    start = 1790937600000
    songs = tuple(EventSong(str(i),str(100+i),f"合成歌曲 {i}",collect_status="collecting",position_source="responseOrder") for i in range(1,4))
    event = EventSnapshot("jp","1","合成活动 · 不代表游戏实况","nowOn",start,start+86400000,songs)
    boards = tuple(BoardSnapshot(s,tuple(20000000-i*300000-r*12345 for r in range(100)),start+86100000,start+86100000,start+86100000,0,"合成当前观测",
                                 player_ids=tuple(str(11000000000+r) for r in range(100)),player_names=tuple(f"合成玩家{r+1}" for r in range(100))) for i,s in enumerate(songs))
    points = tuple(HistoryPoint(start+i*300000,tuple(20000000-r*300000+i*1500 for r in range(5)),start+i*300000,i==150)
                   for i in range(288) if not 140<i<150)
    histories = tuple((s.challenge_id,HistoryView(points)) for s in songs)
    base = CutoffAnswer(CutoffRequest(),event,boards,histories=histories)
    variants = {"three":base,"single":replace(base,boards=boards[:1],request=CutoffRequest(song_number=1)),
                "two":replace(base,boards=boards[:2],request=CutoffRequest(extra_queries=tuple(s.title for s in songs[:2]))),
                "zero":replace(base,histories=tuple((s.challenge_id,HistoryView(warning="暂无历史，从启用后采集。")) for s in songs)),
                "one":replace(base,histories=tuple((s.challenge_id,HistoryView(points[-1:])) for s in songs)),
                "overlap":replace(base,histories=tuple((s.challenge_id,HistoryView(tuple(replace(p,scores=(p.scores[0],)*5) for p in points))) for s in songs)),
                "mixed-missing":replace(base,boards=(boards[0],replace(boards[1],scores=(),status="本曲来源暂不可用"),boards[2]),histories=(histories[0],(songs[1].challenge_id,HistoryView()),(songs[2].challenge_id,HistoryView(points[-1:]))))}
    for name, request in (("near-50",CutoffRequest(rank=50)),("near-100",CutoffRequest(rank=100)),("range",CutoffRequest(mode="range",range_bounds=(20,40))),
                          ("discrete",CutoffRequest(ranks=(1,3,37))), ("exact",CutoffRequest(mode="range",range_bounds=(37,37))),
                          ("full-100",CutoffRequest(mode="range",range_bounds=(1,100))), ("numeric",CutoffRequest(rank=50,numeric_only=True))):
        variants[name]=replace(base,request=request,histories=())
    variants["single-full-100"] = replace(variants["full-100"],boards=boards[:1],request=CutoffRequest(mode="range",range_bounds=(1,100),song_number=1))
    variants["long-fields"] = replace(variants["near-50"], boards=tuple(replace(b,song=replace(b.song,title="合成长歌曲名 · 中文と日本語 / Long title "*3),
                                   scores=tuple(10**20+r for r in range(100)),player_ids=("12345678901234567890",)*100,
                                   player_names=("合成超长用户名_中文と日本語_LongName"*2,)*100) for b in boards))
    for server in ("tw","kr","en"):
        variants["server-"+server] = replace(base,event=replace(event,server=server),request=CutoffRequest(server=server))
    with patch.object(cv,"load_artwork",return_value={}):
        with patch.object(repo.event_cutoffs,"event",return_value=event):
            g.sample("text","activity","/查活动",lambda:g.qq.prepare_reply("/查活动",repo,None),expected="image",
                     synthetic=("活动", "歌曲", "时间"),source="合成活动，由真实活动查询生成图片",route="_prepare_reply → execute_activity → 活动图片")
        for name, answer in variants.items():
            family = "cutoff-trend" if answer.request.mode=="trend" else "cutoff-ranks"
            result = CommandResult(("cutoff","",None),cutoff=answer)
            g.sample(family,name,"/"+answer.request.command_label(),lambda result=result:g.qq.render_prepared(result.cutoff.text,result,repo,"zh"),
                     source="固定合成活动/榜单/历史；封面占位",synthetic=("活动", "歌曲", "分数", "玩家ID/名", "历史点", "时间"),
                     expected="text" if answer.request.numeric_only else "image",ids=[b.song.music_id for b in answer.boards],
                     params={"server":answer.request.server,"ranks":answer.request.ranks,"boards":len(answer.boards),
                             "songs":[{"music_id":b.song.music_id,"challenge_id":b.song.challenge_id,"title":b.song.title,"fetched_ms":b.fetched_ms,"status":b.status} for b in answer.boards]},
                     route="捕获CutoffAnswer → _render_prepared → render_cutoff → render_trends/_tables")
        # Actual reply-budget path, all source pages remain available in the full-100 example.
        result=CommandResult(("cutoff","",None),cutoff=variants["full-100"])
        g.sample("text","reply-budget","三曲100名 + 保留其他回复槽",lambda:g.qq.PreparedReply("\n\n".join(p.text for p in g.qq.expand_replies([g.qq.render_prepared(result.cutoff.text,result,repo,"zh")],reserve=4))),
                 expected="text",synthetic=("活动", "榜单", "回复预算"),route="_render_prepared → _expand_replies预算终态")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/output-gallery")
    parser.add_argument("--cache-dir", type=Path, help="read-only public preview_visuals snapshot, optional")
    parser.add_argument("--game-assets-dir", type=Path, help="read-only hash-addressed original note/icon cache")
    parser.add_argument("--font", type=Path, help="explicit CJK font; its hash is recorded, never copied")
    parser.add_argument("--only", action="append", default=[], help="family or exact case ID; omit for complete gallery")
    parser.add_argument('--observe', choices=('visual','timing'), help='opt-in canvas/phase evidence; timing never saves raw canvases')
    parser.add_argument('--benchmark-runs', type=int, choices=(3,5), help='each selected case in a fresh child process; aggregate medians and ranges')
    parser.add_argument('--encoding-strategy', choices=('legacy','png-first'), default='legacy', help='gallery-only native canvas encoding experiment')
    parser.add_argument('--full-activity-view', action='store_true', help='gallery-only complete single activity InfoView attempt')
    args = parser.parse_args()
    output = args.output.resolve()
    cache = args.cache_dir.resolve() if args.cache_dir else None
    guard_paths(output, [ROOT / "tests/fixtures", ROOT / "docs", ROOT / "src", *([cache] if cache else [])])
    os.environ.update(QQ_APP_ID="", QQ_APP_SECRET="", AI_API_KEY="", MOENOTES_OPEN_SECRET="", BDON_OPENPLATFORM="")
    def offline(event, arguments):
        if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo", "socket.sendto"}:
            raise RuntimeError("output gallery network is blocked")
    sys.addaudithook(offline)
    sys.path.insert(0, str(ROOT / "src"))
    from ournotes_bot import visuals
    from ournotes_bot.application import replies as qq
    from ournotes_bot.rendering import card_visuals, event_cutoff_visuals
    font = args.font.resolve() if args.font else next((Path(p) for p in visuals.FONT_PATHS if Path(p).is_file()),None)
    if font is None or not font.is_file():
        parser.error("a usable CJK font is required")
    for folder in ("images","thumbnails","texts","isolated"):
        (output / folder).mkdir(parents=True,exist_ok=True)
    g = Gallery(output,args.only,cache,font,args.observe)
    g.full_activity_view = args.full_activity_view
    g.encoding_strategy = args.encoding_strategy
    g.list_only = bool(args.benchmark_runs)
    if g.observation:
        g.asset = g.observation.wrap(g.asset, 'assets')
    logging.basicConfig(level=logging.ERROR)
    with ExitStack() as stack:
        stack.enter_context(patch.object(visuals,"FONT_PATHS",[str(font)]))
        stack.enter_context(patch.object(visuals,"_asset",side_effect=g.asset))
        original_asset = qq.visual_asset
        def tracked_asset(url, size):
            image = original_asset(url, size)
            if image is None and url: g.missing.add(url)
            return image
        stack.enter_context(patch.object(qq,'visual_asset',side_effect=tracked_asset))
        stack.enter_context(patch("ournotes_bot.rendering.member_list_visuals.get_snapshot",return_value=None))
        stack.enter_context(patch.object(visuals,"runtime_data_dir",return_value=output/"isolated"))
        from ournotes_bot.sources.haneoka import game_assets
        def offline_game_asset(name, cache_dir):
            if args.game_assets_dir is None:
                g.missing.add('game:'+name)
                return None
            spec=game_assets.MANIFEST.get(name)
            if spec is None:return None
            path=args.game_assets_dir / (spec['sha256']+Path(name).suffix)
            if not path.is_file():
                g.missing.add('game:'+name)
                return None
            raw=path.read_bytes()
            g.inputs[str(path)] = hashlib.sha256(raw).hexdigest()
            if hashlib.sha256(raw).hexdigest()!=spec['sha256']:g.missing.add('invalid-game:'+name)
            return raw if hashlib.sha256(raw).hexdigest()==spec['sha256'] else None
        stack.enter_context(patch.object(game_assets,'asset_bytes',side_effect=offline_game_asset))
        stack.enter_context(patch("time.time",return_value=FIXED_TIME))
        if args.full_activity_view:
            from ournotes_bot.rendering import info_layout
            render_info = info_layout.render_info
            def full_activity(answer, **kwargs):
                return render_info(answer, **kwargs, try_full_activity=True)
            stack.enter_context(patch.object(info_layout, 'render_info', full_activity))
        if args.encoding_strategy != 'legacy':
            from output_observation import experimental_encoding
            stack.enter_context(experimental_encoding(args.encoding_strategy))
        if g.observation:
            g.observation.install(stack)
        g.encoder = visuals._bytes
        stack.enter_context(patch.object(visuals,"_bytes",side_effect=g.encode))
        for module,names in ((qq,("render_song_list","render_chart","render_card_list","render_card","render_support_card_list","render_support_card")),
                             (card_visuals,("art",)), (event_cutoff_visuals,("render_cutoff",))):
            for name in names:
                stack.enter_context(patch.object(module,name,side_effect=g.watch(module.__name__+"."+name,getattr(module,name))))
        export_cases(g)
    if args.only and not g.rows:
        parser.error("--only matched no cases")
    if args.benchmark_runs:
        samples = []
        for case in g.rows:
            runs = []
            for number in range(args.benchmark_runs):
                target = output / 'runs' / case['id'] / str(number+1)
                command = [sys.executable,'-B',str(Path(__file__).resolve()),'--only',case['id'],
                           '--observe','timing','--font',str(font),'--output',str(target)]
                for option,value in (('--cache-dir',cache),('--game-assets-dir',args.game_assets_dir)):
                    if value: command += [option,str(value)]
                command += ['--encoding-strategy', args.encoding_strategy]
                if args.full_activity_view: command += ['--full-activity-view']
                child = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=300,
                                       env={**os.environ,'PYTHONIOENCODING':'utf-8'})
                if child.returncode:
                    raise RuntimeError(f"benchmark {case['id']} failed: {child.stdout}\n{child.stderr}")
                sample = json.loads((target/'manifest.json').read_bytes())['samples'][0]
                runs.append(sample)
            def summary(values):
                return {'median': statistics.median(values),'min':min(values),'max':max(values)}
            samples.append({'id':case['id'],'runs':len(runs),
                            'seconds':summary([r['seconds'] for r in runs]),
                            'peak_memory_bytes':summary([r['observation']['process_peak_memory']['bytes'] for r in runs]),
                            'stages_seconds':{k:summary([r['observation']['stages_seconds'][k] for r in runs])
                                              for k in runs[0]['observation']['stages_seconds']},
                            'output_sha256':[[f.get('sha256') for f in r['files']] for r in runs]})
            print('benchmark: '+case['id'],flush=True)
        g.write_json('benchmark.json',{'code_version':g.version,'runs_per_case':args.benchmark_runs,
                     'scope':'isolated processes; offline disk assets; no pixel capture or derived previews; delivery bytes and header metadata only; not production p95',
                     'samples':samples})
        return 0
    g.finish()
    print(json.dumps({"output":str(output),"cases":len(g.rows),"failed":g.errors},ensure_ascii=False))
    return bool(g.errors)


if __name__ == "__main__":
    raise SystemExit(main())
