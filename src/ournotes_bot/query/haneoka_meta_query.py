# L3
# Input: Explicit site-meta query and one Haneoka JP reference snapshot.
# Output: Captured ordinary/gekisou panels, selection numbers and truthful source notices.
# Pos: Query / Deterministic Haneoka site reference presentation; see L2-2.md.
# Effects: Source get may refresh its independent cache; no local score simulation or QQ.
from __future__ import annotations

import re
from dataclasses import replace

HELP = ("/查分数表 [普通/激奏] [歌名/乐队/颜色/EX] [指标=eff或score] [前10/20/30] [页N]；"
        "默认并列普通与激奏两榜。eff 为效率，score 为倍率，使用 Haneoka 日服页面的参考结果；"
        "不提供旧模型的活动积分、跳过得分或队伍计算。")
LABELS = {"normal": "普通", "gekisou": "激奏"}


def parse_site_meta(query, repository, *, direct=False):
    from .efficiency_query import _parse_legacy
    from ..structured_query import QuerySpec
    aliases = {"普通": "normal", "自由": "normal", "单人": "normal", "normal": "normal",
               "激奏": "gekisou", "击奏": "gekisou", "gekisou": "gekisou"}
    found = list(re.finditer(r"(?<!\S)(普通|自由|单人|激奏|击奏|normal|gekisou)(?!\S)", query, re.I))
    scenes = {aliases[m[1].lower()] for m in found}
    if len(scenes) > 1:
        return "一次可选普通或激奏；省略场景查看双榜。\n" + HELP
    text = re.sub(r"(?<!\S)(普通|自由|单人|激奏|击奏|normal|gekisou)(?!\S)", " ", query, flags=re.I)
    for old, new in (("排行=效率", "指标=eff"), ("排行=分数", "指标=score"), ("排行=倍率", "指标=score")):
        text = text.replace(old, new)
    if "排行=" in text or any(token in text for token in ("积分", "跳过", "加成=", "技能=")):
        return "Haneoka 参考分数表不支持该旧模型参数。\n" + HELP
    result = _parse_legacy(text, repository, direct=direct)
    if isinstance(result, QuerySpec):
        return replace(result, meta_scene=next(iter(scenes), "both"))
    if isinstance(result, str):
        return result.split("\n", 1)[0] + "\n" + HELP
    return result


def execute_site_meta(spec, repository):
    from .efficiency_query import MetaAnswer, _percentage, _duration, _level_matches
    from .song_query import traits_match
    from ..data import normalize
    snapshot = repository.song_meta.get()
    if snapshot is None:
        return MetaAnswer("Haneoka 普通／激奏分析暂不可用，请稍后重试。", status="data_unavailable")
    with repository._song_lock:
        songs = {song.id: song for song in repository.songs}
        catalog_release = str(repository.metadata.get("data_version", ""))
    if (spec.meta_scene not in {*LABELS, "both"} or spec.metric not in {"eff", "score"}
            or spec.order not in {"asc", "desc"} or spec.limit not in {10, 20, 30} or not 1 <= spec.page <= 100):
        return MetaAnswer(HELP, status="invalid_arguments")
    scenes = tuple(LABELS) if spec.meta_scene == "both" else (spec.meta_scene,)
    panels, choice = [], 1
    for scene in scenes:
        selected = []
        unknown = set()
        missing = 0
        difficulty = spec.difficulty or ("EXPERT" if spec.subject and spec.subject.kind == "song" else "")
        if difficulty == "ALL":
            difficulty = ""
        for row in snapshot.rows:
            song = songs.get(row.song_id)
            if row.scene != scene or song is None:
                continue
            if not {normalize(t) for t in row.titles} & {normalize(t) for t in (song.title, *song.titles)}:
                continue
            if difficulty and row.difficulty != difficulty:
                continue
            if spec.subject and ((spec.subject.kind == "song" and row.song_id != spec.subject.value)
                    or (spec.subject.kind == "band" and normalize(song.band) != normalize(str(spec.subject.value)))):
                continue
            if spec.comparison and not _level_matches(row.level, spec.comparison, spec.level):
                continue
            matches = traits_match(spec.song_filter, song) if spec.song_filter else True
            if matches is None:
                unknown.add(song.id)
            if matches is True:
                if getattr(row, spec.metric) is None:
                    missing += 1
                else:
                    selected.append(row)
        references = {row.reference_id for row in selected}
        if len(references) > 1:
            panels.append(MetaAnswer(f"{LABELS[scene]}参考口径不一致，暂不混合排名。", status="data_unavailable",
                                     title=LABELS[scene] + " · Haneoka"))
            continue
        selected.sort(key=lambda row: ((-1 if spec.order == "desc" else 1) * getattr(row, spec.metric),
                                       row.song_id, ("EASY", "NORMAL", "HARD", "EXPERT").index(row.difficulty)))
        pages = max(1, (len(selected) + spec.limit - 1) // spec.limit)
        visible = selected[(spec.page - 1)*spec.limit:spec.page*spec.limit]
        numbers = tuple(range(choice, choice + len(visible)))
        choice += len(visible)
        scope = f"Haneoka 日服 · {LABELS[scene]} · {difficulty or '全难度'} · {'效率' if spec.metric == 'eff' else '倍率'}"
        columns = ("排名", "歌曲", "难度", "等级", "时长", "倍率", "效率")
        cells = tuple((str(n), songs[r.song_id].title, r.difficulty,
            f"{r.level:g}" if r.level is not None else "未知", _duration(r.seconds),
            _percentage(r.score), _percentage(r.eff)) for n, r in enumerate(visible, (spec.page-1)*spec.limit+1))
        page_notice = f"第 {spec.page}/{pages} 页 · 共 {len(selected)} 条谱面"
        if numbers:
            page_notice += f" · /选 {numbers[0]}～{numbers[-1]}"
        if spec.page < pages:
            page_notice += "\n下一页：/" + replace(spec, page=spec.page+1).command_label()
        notes = ("Haneoka 页面参考结果；倍率与效率以百分数展示，不是游戏实得分。",
                 "普通与激奏分别排序；效率含上游参考曲间时间，激奏采用上游近似参考模型。",
                 f"{'旧缓存' if snapshot.stale else '已获取'} · 本机获取：{snapshot.fetched_at}",
                 f"分析版本：{snapshot.release} · 参考：{next(iter(references), '暂无有效结果')}")
        if snapshot.unsaved:
            notes += ("本次数据尚未成功保存，当前使用内存结果。",)
        if catalog_release and catalog_release != snapshot.release:
            notes += ("主资料与分析版本不同，仅展示已核对身份的歌曲。",)
        if unknown:
            notes += (f"{len(unknown)} 首歌曲缺少所需属性，未参与筛选。",)
        if missing:
            notes += (f"{missing} 条谱面的本场景指标缺失或口径未确认，未参与排名。",)
        if any(r.warnings for r in visible):
            notes += ("上游参考含默认参数或近似条件，详见 Haneoka 乐曲分析说明。",)
        status = "success" if visible else "data_unavailable" if unknown or missing else "empty"
        body = "\n".join(str(n) + " | " + " | ".join(c) for n,c in zip(numbers,cells)) if cells else "本场景暂无符合条件的有效数据。"
        text = f"[{scope}]\n选择号 | " + " | ".join(columns) + "\n" + body + "\n" + page_notice + "\n" + "\n".join(notes)
        panels.append(MetaAnswer(text, tuple(visible), status, columns=columns, cells=cells, scope=scope,
            page_notice=page_notice, notes=notes, jackets=tuple(songs[r.song_id].jacket_url for r in visible),
            song_records=tuple(songs[r.song_id] for r in visible), total=len(selected), unknown_songs=len(unknown),
            versions=(catalog_release, snapshot.release, snapshot.source_version), complete_text=True,
            title=LABELS[scene] + " · Haneoka 分数表", selection_numbers=numbers))
    if len(panels) == 1:
        return panels[0]
    rows = tuple(row for panel in panels for row in panel.rows)
    return MetaAnswer("\n\n".join(panel.text for panel in panels), rows,
        "success" if rows else "data_unavailable" if any(p.status == "data_unavailable" for p in panels) else "empty",
        total=max(panel.total for panel in panels), versions=(catalog_release, snapshot.release, snapshot.source_version),
        complete_text=True, panels=tuple(panels), title="普通／激奏 · Haneoka 分数表")
