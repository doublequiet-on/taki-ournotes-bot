# L3
# Input: 分数表问题或含 MetaRequest 的 QuerySpec、SongRepository；旧来源开关保留旧请求。
# Output: MetaAnswer 捕获同份事实、排行、可读图文及逐行时长回退；版本诊断独立保存，供续查和审计。
# Pos: Query / Deterministic 的分数表入口与结果捕获；参数和有限求值见同域模块。
# Effects/Dependencies: 入口惰性取得公开快照、审核别名；有界本地计算和结果缓存；显式回退才读旧分析。

"""Platform-neutral parsing and presentation of verified song meta."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from ..data import Song, normalize
from .entity_lexicon import EntityRef, find_anchor, resolve_entity
from .song_conditions import extract_song_conditions, remove_song_conditions
from .song_identity import exact_songs, protect_entity, QueryProblem
from .song_query import parse_filter, serialize_filter, traits_match
from ..sources.haneoka.song_meta import DIFFICULTIES, REFERENCE_LABELS, MetaRow
from ..sources.haneoka.song_traits import describe as describe_song
from .meta_model import EvaluatedRow
from .meta_parameters import HELP

PAGE_SIZE = 30
META_WORD = r"(?<![A-Za-z])meta(?=$|[\s?？。！!，,])"
MARKER = re.compile(r"分数表|效率|" + META_WORD, re.I)
FORBIDDEN = re.compile(r"推荐|攻略|预测|档线|代练|代肝|账号|编成|配队|抽卡|怎么打|如何打|最强|哪个好|哪个更好|比较强弱")


def parse_efficiency(query: str, repository, *, direct=False):
    if not direct and not MARKER.search(query):
        return None
    if getattr(repository, "meta_source", "haneoka") == "moenotes":
        from .meta_parameters import parse_meta
        return parse_meta(query, repository, natural=not direct)
    return _parse_legacy(query, repository, direct=direct)


def _parse_legacy(query: str, repository, *, direct=False):
    from ..commands import _split_page
    from ..structured_query import QuerySpec
    if not direct and not MARKER.search(query):
        return None
    text = unicodedata.normalize("NFKC", query).strip().strip("?？。！!")
    text = re.sub(r"^/?查(?:分数表|效率)\s*", "", text)
    text, protected = protect_entity(text, repository)
    if FORBIDDEN.search(text):
        return "效率查询不支持攻略、配队、推荐、账号操作或预测。\n" + HELP
    text, page = _split_page(text)
    if not 1 <= page <= 100:
        return "页码需为 1～100。\n" + HELP
    metric, order = "eff", "asc" if "最低" in text else "desc"
    for pattern, allowed, label in ((r"(?:指标|metric)\s*=\s*(\w+)", {"eff", "score"}, "指标"),
                                     (r"(?:排序|sort)\s*=\s*(\w+)", {"asc", "desc"}, "排序")):
        found = list(re.finditer(pattern, text, re.I))
        if len(found) > 1 or (found and found[0].group(1).lower() not in allowed):
            return f"不支持此{label}条件。\n" + HELP
        if found:
            if label == "指标":
                metric = found[0].group(1).lower()
            else:
                order = found[0].group(1).lower()
            text = text[:found[0].start()] + " " + text[found[0].end():]
    evidence = extract_song_conditions(text, include_explicit_lv=True)
    if evidence.level_conflict:
        return "目前只支持一个等级边界。\n" + HELP
    comparison, level = evidence.comparison, evidence.level
    if level is not None and not 1 <= level <= 40:
        return "等级需为 1～40。\n" + HELP
    all_difficulties = bool(re.search(r"全难度|所有难度|\bALL\b", text, re.I))
    if evidence.difficulty_conflict or (all_difficulties and evidence.has_difficulty):
        return "可省略难度查看全难度榜，或指定一种难度筛选。\n" + HELP
    difficulty = evidence.difficulty
    text = remove_song_conditions(text, evidence)
    limits = list(re.finditer(r"前\s*([\d一二三四五六七八九十百]+)\s*(?:首(?:歌曲|歌)?|条)?", text))
    limit = PAGE_SIZE
    if limits:
        counts = {"10": 10, "十": 10, "20": 20, "二十": 20, "30": 30, "三十": 30}
        if len(limits) > 1 or limits[0].group(1) not in counts:
            return "支持前 10、20 或 30 条，更多结果请使用页码翻页。\n" + HELP
        limit = counts[limits[0].group(1)]
        text = text[:limits[0].start()] + " " + text[limits[0].end():]
    ranking = bool(limits or re.search(r"排行|排名|效率榜|分数表|最高|最低|哪些歌", text))
    text = re.sub(r"查一下|查询|查看|请|帮我|有哪些|哪些歌|怎么样|如何|排行榜|排行|排名|分数表|效率榜|每分钟得分效率|得分效率|效率|全难度|所有难度|"
                  + META_WORD + r"|\bALL\b|最高|最低", "", text, flags=re.I)
    term = text.strip(" 的，,。？?！! ").strip().replace("TAKIENTITYTOKEN", protected)
    try:
        shared = parse_filter(term, repository, force=True, efficiency=True)
    except ValueError as exc:
        return str(exc)
    term = shared.term
    subject = None
    if term:
        exact = exact_songs(repository, term)
        if len(exact) > 1:
            return QueryProblem("匹配到多首候选歌曲，请用 ID 选择：\n" + "\n".join(
                f"{song.title} → /查分数表 {song.id}" for song in exact), "ambiguous")
        if exact:
            term = str(exact[0].id)
        elif term.isdecimal() and 1 <= int(term) < 100000 and not ranking:
            term = str(100000 + int(term))
        canonical = resolve_entity("song", term, repository)
        anchor = find_anchor("song", term, repository) if canonical else None
        if anchor and not anchor.ambiguous:
            subject = anchor.entity
        if subject is None:
            matches = repository.search(term, limit=10)
            if len(matches) == 1 and not ranking:
                # A direct name search may suggest candidates, but must not drop unknown conditions.
                exact = normalize(term) in {normalize(t) for t in (matches[0].title, *matches[0].titles)}
                if exact:
                    subject = EntityRef("song", matches[0].id)
            if subject is None:
                if matches:
                    return QueryProblem("请明确歌曲或条件；候选：\n" + "\n".join(f"{s.title} → /查分数表 {s.id}" for s in matches) + "\n" + HELP, "ambiguous")
                return QueryProblem(f"未找到歌曲或乐队「{term}」，也可能包含不支持的条件；未忽略任何条件。\n" + HELP, "unknown_entity")
    if subject and subject.kind not in {"song", "band"}:
        return "效率查询只支持歌曲或乐队。\n" + HELP
    if all_difficulties and subject and subject.kind == "song":
        difficulty = "ALL"
    shared = replace(shared, term=str(subject.value) if subject else "", difficulty=difficulty,
                     comparison=comparison, level=level)
    shared = replace(shared, query=serialize_filter(shared))
    return QuerySpec("efficiency", subject, difficulty, comparison, level, page,
                     str(subject.value) if subject else "", metric=metric, order=order, limit=limit,
                     song_filter=shared)


@dataclass(frozen=True)
class MetaAnswer:
    text: str
    rows: tuple[MetaRow | EvaluatedRow, ...] = ()
    status: str = "success"
    columns: tuple[str, ...] = ()
    cells: tuple[tuple[str, ...], ...] = ()
    scope: str = ""
    page_notice: str = ""
    notes: tuple[str, ...] = ()
    jackets: tuple[str, ...] = ()
    song_records: tuple[Song, ...] = ()
    total: int = 0
    unknown_songs: int = 0
    unknown_rows: int = 0
    versions: tuple[str, ...] = ()
    complete_text: bool = False
    title: str = "日服 · 歌曲分数表"
    diagnostics: tuple[str, ...] = ()


def execute_efficiency(spec, repository) -> MetaAnswer:
    if spec.meta_request is not None:
        return _execute_moenotes(spec, repository)
    return _execute_legacy(spec, repository)


def _execute_legacy(spec, repository) -> MetaAnswer:
    with repository._song_lock:
        songs = tuple(repository.songs)
        traits_version = "/".join(sorted({s.traits.release for s in songs if s.traits and s.traits.release}))
        catalog_version = str(repository.metadata.get("cached_at", ""))
    snapshot = repository.song_meta.get()
    if snapshot is None:
        return MetaAnswer("效率数据暂不可用，请稍后重试；歌曲、卡牌查询仍可使用。", status="data_unavailable")
    references = {row.reference for row in snapshot.rows if row.reference in REFERENCE_LABELS}
    if not references or len(references) > 1:
        reason = "上游数据口径变化，尚无已确认的参考条件" if not references else "上游包含不同 Fever 参考条件，不能混合排名"
        return MetaAnswer(f"效率数据暂不可用：{reason}。请联系维护者核对 Haneoka 数据口径后更新；"
                          f"调整筛选条件无法解决。\n上游版本：{snapshot.release} / {snapshot.source_version}",
                          status="data_unavailable")
    reference = next(iter(references))
    assumptions = (f"模式：上游理想参考；全 PERFECT、{REFERENCE_LABELS[reference]}、"
                   "技能×2.5/10秒、曲间30秒。非官方结论或实得分保证。")
    subject = spec.subject
    difficulty = spec.difficulty or ("EXPERT" if subject and subject.kind == "song" else "")
    if difficulty == "ALL":
        difficulty = ""
    difficulty_label = difficulty or "全难度"
    metric = spec.metric
    if metric not in {"eff", "score"} or spec.order not in {"asc", "desc"} or spec.limit not in {10, 20, 30}:
        return MetaAnswer("不支持此排序口径。\n" + HELP, status="invalid_arguments")
    names = {}
    for song in songs:
        names.setdefault(song.id, []).append(song)
    mapped = []
    for row in snapshot.rows:
        local = names.get(row.song_id, [])
        if len(local) != 1:
            continue
        titles = {normalize(t) for t in (local[0].title, *local[0].titles,
                                        *local[0].localized.get("title", {}).values()) if t}
        if titles.intersection(normalize(t) for t in row.titles):
            mapped.append(row)
    eligible = [r for r in mapped if (not difficulty or r.difficulty == difficulty) and r.reference == reference
                and getattr(r, metric) is not None]
    # Numeric song ID breaks ties deterministically; no missing value becomes zero.
    eligible.sort(key=lambda r: ((-1 if spec.order == "desc" else 1) * getattr(r, metric), r.song_id,
                                 DIFFICULTIES.index(r.difficulty)))
    scope = f"{difficulty_label}{'（默认）' if not spec.difficulty else ''} · {'eff 每分钟得分效率' if metric == 'eff' else 'score 得分系数'} {'降序' if spec.order == 'desc' else '升序'}"
    footer = (f"Haneoka分析数据 · 日服 · {'旧缓存（刷新失败或过期）' if snapshot.stale else '有效缓存'}\n"
              f"本机获取：{snapshot.fetched_at}；上游版本：{snapshot.release} / {snapshot.source_version}\n"
              f"覆盖：{len(eligible)} 条{difficulty_label}有效谱面样本；同曲不同难度分别计数，非完整全曲榜。\n{assumptions}")
    if any(r.warnings for r in eligible):
        footer += "\n上游含参数默认值或谱面差异警告，请以来源页说明为准。"
    scoped = [r for r in eligible if (not subject or (subject.kind == "song" and r.song_id == subject.value)
                or (subject.kind == "band" and normalize(names[r.song_id][0].band) == normalize(str(subject.value))))
                and (not spec.comparison or _level_matches(r.level, spec.comparison, spec.level))]
    flags = {r.song_id: traits_match(spec.song_filter, names[r.song_id][0]) if spec.song_filter else True for r in scoped}
    unknown_ids = {key for key, value in flags.items() if value is None}
    unknown_rows = sum(r.song_id in unknown_ids for r in scoped)
    filtered = [r for r in scoped if flags[r.song_id] is True]
    coverage = (f"有 {len(unknown_ids)} 首歌曲／{unknown_rows} 条谱面缺少所需颜色或激奏资料，未参与筛选。" if unknown_ids else "")
    versions = (catalog_version, traits_version, snapshot.release, snapshot.source_version)
    if coverage:
        footer += "\n" + coverage
    if scoped and unknown_rows == len(scoped):
        return MetaAnswer("所需歌曲属性资料暂不可用，不能判断筛选结果。\n" + footer,
                          status="data_unavailable", unknown_songs=len(unknown_ids), unknown_rows=unknown_rows,
                          versions=versions)
    if subject and subject.kind == "song" and difficulty:
        song = names.get(subject.value, [])
        if len(song) != 1:
            return MetaAnswer("歌曲不存在或 ID 冲突。", status="unknown_entity" if not song else "ambiguous")
        row = next((r for r in mapped if r.song_id == subject.value and r.difficulty == difficulty), None)
        if row is None or getattr(row, metric) is None:
            options = sorted({r.difficulty for r in mapped if r.song_id == subject.value and getattr(r, metric) is not None})
            return MetaAnswer(f"{song[0].title}（{subject.value}）已收录，但 {difficulty} 效率数据缺失、口径未确认或 ID 映射未确认。\n"
                              f"可用难度：{'、'.join(options) or '暂无'}；未自动更换难度。\n" + footer, status="data_unavailable")
        if spec.comparison and not _level_matches(row.level, spec.comparison, spec.level):
            return MetaAnswer("该曲不符合等级条件。\n" + footer, status="empty")
        if row not in filtered:
            return MetaAnswer("该曲不符合乐队、颜色或激奏条件。\n" + footer, status="empty")
        if spec.page != 1:
            return MetaAnswer("单曲结果只有 1 页。", status="empty")
        def percentage(value):
            return "未知" if value is None else f"{value * 100:.2f}%"
        rank = eligible.index(row) + 1
        text = (f"[歌曲效率] {song[0].title}（{row.song_id}）· {song[0].band}\n{scope}\n"
                f"等级：{row.level if row.level is not None else '未知'}\n"
                f"eff：{percentage(row.eff)}；score：{percentage(row.score)}\n"
                f"末判定点时长：{row.seconds if row.seconds is not None else '未知'} 秒；技能覆盖贡献比 sr：{percentage(row.skill_ratio)}\n"
                f"同难度有效映射样本中的排序位置：{rank}/{len(eligible)}\n{footer}")
        return MetaAnswer(text + "\n" + describe_song(song[0]), (row,), song_records=(song[0],),
                          total=1, versions=versions)
    if subject and subject.kind == "song" and not filtered:
        available = any(r.song_id == subject.value for r in eligible)
        explanation = ("该曲不符合等级条件。" if available else
                       "歌曲已收录，但全难度分析数据缺失、口径未确认或 ID 映射未确认。")
        return MetaAnswer(explanation + "\n" + footer, status="empty" if available else "data_unavailable")
    pages = max(1, (len(filtered) + spec.limit - 1) // spec.limit)
    subject_label = (names[subject.value][0].title if subject and subject.kind == "song" else
                     str(subject.value) if subject else "全部已映射歌曲")
    table_scope = f"{difficulty_label}{'（默认）' if not spec.difficulty else ''} · {subject_label}"
    if spec.comparison:
        table_scope += f" · 等级{spec.comparison}{spec.level:g}"
    if spec.song_filter and spec.song_filter.query:
        table_scope += " · " + spec.song_filter.query
    table_scope += f" · {'每分钟得分效率' if metric == 'eff' else '得分系数'}{'从高到低' if spec.order == 'desc' else '从低到高'}"
    if spec.page > pages:
        return MetaAnswer(f"页码超出范围，共 {pages} 页。\n/" + replace(spec, page=1).command_label(), status="empty")
    visible = filtered[(spec.page - 1) * spec.limit:spec.page * spec.limit]
    columns = ("序号", "歌曲", "难度", "等级", "时长", "得分系数", "每分钟得分效率")
    cells = tuple((str(i), names[r.song_id][0].title, r.difficulty,
                   f"{r.level:g}" if r.level is not None else "未知", _duration(r.seconds),
                   _percentage(r.score), _percentage(r.eff))
                  for i, r in enumerate(visible, 1))
    page_notice = f"筛选内排序 · 第 {spec.page}/{pages} 页 · 共 {len(filtered)} 条谱面"
    if visible:
        page_notice += f" · 本页排序 {(spec.page - 1) * spec.limit + 1}～{(spec.page - 1) * spec.limit + len(visible)}"
    if spec.page < pages:
        page_notice += "\n下一页：/" + replace(spec, page=spec.page + 1).command_label()
    metric_note = "得分系数 = score；每分钟得分效率 = eff，均为上游参考系数，百分数显示，不是实际得分。"
    notes = (f"Haneoka分析数据 · 日服 · {'旧缓存（刷新失败或过期）' if snapshot.stale else '有效缓存'} · "
             f"{len(eligible)} 条{difficulty_label}有效谱面样本（非完整全曲榜）",
             metric_note,
             assumptions,
             "同曲不同难度分别排名；时长为上游末判定点时长，不含曲间30秒，显示到秒。",
             "本机获取：" + datetime.fromisoformat(snapshot.fetched_at).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
             + f" · 版本：{snapshot.release}")
    if any(r.warnings for r in eligible):
        notes += ("上游含参数默认值或谱面差异警告，详见来源页。",)
    if coverage:
        notes += (coverage,)
    if spec.song_filter and (spec.song_filter.colors or spec.song_filter.mode):
        notes += (f"属性版本：{traits_version or '未获取'}；主资料版本：{catalog_version or '未标注'}",)
    text = f"[分数表]\n{table_scope}\n"
    traits = tuple(describe_song(names[r.song_id][0]) for r in visible)
    text += (" | ".join(columns) + "\n" + "\n".join(" | ".join(row) + "\n" + trait for row, trait in zip(cells, traits))
             if visible else "没有符合条件的有效效率数据。")
    text += "\n" + page_notice + "\n" + metric_note + "\n" + footer
    return MetaAnswer(text, tuple(visible), "success" if visible else "empty",
                      columns=columns, cells=cells, scope=table_scope, page_notice=page_notice, notes=notes,
                      jackets=tuple(names[r.song_id][0].jacket_url for r in visible), song_records=tuple(names[r.song_id][0] for r in visible),
                      total=len(filtered), unknown_songs=len(unknown_ids), unknown_rows=unknown_rows, versions=versions)


def _percentage(value):
    return "未知" if value is None else f"{value * 100:,.2f}%"


def _duration(seconds):
    if seconds is None:
        return "未知"
    whole = int(seconds + 0.5)
    return f"{whole // 60}:{whole % 60:02d}"


def _level_matches(actual, comparison, level):
    if actual is None or level is None:
        return False
    return {">=": actual >= level, ">": actual > level, "<=": actual <= level, "<": actual < level}.get(comparison, False)


def _execute_moenotes(spec, repository):
    import math
    import time
    from dataclasses import replace
    from .meta_model import EVALUATOR_VERSION, evaluate, frontier, dominates
    from .entity_lexicon import alias_version
    from ..sources.moenotes_music_data import text_values, number
    request = spec.meta_request
    snapshot = request.snapshot or repository.music_data.get()
    if snapshot is None:
        return MetaAnswer("分数表公开快照暂不可用。", status="data_unavailable")
    from ..sources.moenotes_music_data import TTL, MAX_STALE
    age = repository.music_data.clock() - snapshot.checked_at
    if not 0 <= age <= MAX_STALE:
        return MetaAnswer("分数表捕获快照已过期，请重新查询。", status="data_unavailable")
    snapshot = replace(snapshot, stale=snapshot.stale or age >= TTL)
    if spec.limit not in {10, 20, 30} or not 1 <= spec.page <= 100:
        return MetaAnswer("数量或页码不正确。", status="invalid_arguments")
    versions = (snapshot.version, EVALUATOR_VERSION, str(alias_version()))
    # Cache the complete comparison/result pool; page and explicit spelling do not
    # change semantics. All fact records below belong to this captured snapshot.
    key = (snapshot.version, EVALUATOR_VERSION, replace(request, score_id=None))
    cache = repository.music_data._results
    with repository.music_data._results_lock:
        cached = cache.get(key)
        if cached is not None:
            cache.move_to_end(key)
    songs = {s.id: s for s in snapshot.songs()}
    started = time.monotonic()
    unknown_ids, unknown_rows, missing = set(), 0, 0
    if cached is None:
        rows = []
        for song in snapshot.data["songs"]:
            if request.band and (not set(request.band_ids).intersection(song.get("bandIds", [])) if request.band_ids else normalize(request.band) != normalize(songs[song["id"]].band)):
                continue
            for chart in song["charts"]:
                if time.monotonic() - started > 8:
                    return MetaAnswer("分数表完整求值超时，请稍后重试；未返回部分排行。", status="data_unavailable")
                if chart["difficulty"] not in request.difficulties:
                    continue
                level = number(chart.get("displayLevel", chart.get("level")))
                if request.filters.comparison and not _level_matches(level, request.filters.comparison, request.filters.level):
                    continue
                flag = traits_match(request.filters, songs[song["id"]])
                if flag is None:
                    unknown_ids.add(song["id"])
                    unknown_rows += 1
                    continue
                if not flag:
                    continue
                try:
                    row = evaluate(song, chart, snapshot, request, deadline=started + 8)
                except TimeoutError:
                    return MetaAnswer("分数表完整求值超时，请稍后重试；未返回部分排行。", status="data_unavailable")
                if row.metric is None or not math.isfinite(row.metric):
                    missing += 1
                    continue
                if request.front and (row.figures is None or row.seconds is None
                                      or request.ranking == "event" and any(t is None for t in row.thresholds)):
                    missing += 1
                    continue
                rows.append(row)
                if time.monotonic() - started > 8:
                    return MetaAnswer("分数表完整求值超时，请稍后重试；未返回部分排行。", status="data_unavailable")
        rows.sort(key=lambda r: ((-1 if request.order == "desc" else 1) * r.metric, r.score_id))
        baseline = max((r.eff for r in rows if r.eff is not None), default=None)
        try:
            dominance = frontier(rows, request.ranking, deadline=started + 8) if request.front else {}
        except TimeoutError:
            return MetaAnswer("分数表完整前沿计算超时，请稍后重试；未返回部分排行。", status="data_unavailable")
        if time.monotonic() - started > 8:
            return MetaAnswer("分数表完整前沿计算超时，请稍后重试；未返回部分排行。", status="data_unavailable")
        cached = (tuple(rows), baseline, dominance, tuple(unknown_ids), unknown_rows, missing)
        with repository.music_data._results_lock:
            cache[key] = cached
            cache.move_to_end(key)
            while len(cache) > 64:
                cache.popitem(last=False)
    pool, baseline, dominance, unknown_ids, unknown_rows, missing = cached
    search_text = {}
    bands = {b.get("id"): b.get("name") for b in snapshot.data["bands"]}
    for song in snapshot.data["songs"]:
        vals = [str(song["id"])]
        for field in ("title", "ruby", "phonetic", "lyricist", "composer", "arranger", "bandName"):
            vals.extend(text_values(song.get(field)))
        for band_id in song.get("bandIds", []):
            vals.extend(text_values(bands.get(band_id)))
        search_text[song["id"]] = tuple(v.casefold() for v in vals)
    selected = [r for r in pool if (request.song_id is None or r.song_id == request.song_id)
                and (not request.search or any(request.search.casefold() in v for v in search_text[r.song_id]))
                and (not request.front or r.score_id in dominance and not dominance[r.score_id])
                and (request.score_id is None or r.score_id == request.score_id)]
    total, pages = len(selected), max(1, (len(selected) + spec.limit - 1) // spec.limit)
    if request.song_id is not None:
        pages = 1
    if spec.page > pages:
        return MetaAnswer(f"页码超出范围，共 {pages} 页。\n/" + replace(spec, page=1).command_label(), status="empty")
    visible = selected if request.song_id is not None else selected[(spec.page - 1) * spec.limit:spec.page * spec.limit]
    conditions = request.display_summary()
    source = (f"来源：Moenotes · TW共通参考 · {'旧缓存' if snapshot.stale else '有效缓存'}"
              + (" · 未落盘" if snapshot.unsaved else ""))
    assumptions = "普通全队5秒加分技能；准度为近似、幸运取样本均值，非游戏实得分保证。"
    if request.ranking == "event":
        assumptions += "达标机会按120种技能顺序估算，不是活动积分。"
    if request.ranking == "skip":
        assumptions += "跳过系数乘综合力不等于逐Note取整的游戏跳过整数分数。"
    coverage = f"比较池 {len(pool)} 条有效谱面；缺主指标{'／前沿证明字段' if request.front else ''} {missing} 条；缺所需属性 {len(unknown_ids)} 首／{unknown_rows} 条。"
    model_identity = snapshot.data["provenance"].get("deck")
    model_format = model_identity.get("format", "未知") if isinstance(model_identity, dict) else "未知"
    fetched = datetime.fromtimestamp(snapshot.fetched_at, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
    notes = (source, coverage)
    if request.ranking in {"efficiency", "score", "event", "skip"}:
        notes += (assumptions,)
    notes += (f"本机获取：{fetched} UTC+8（非数据生成时间）",)
    diagnostics = (f"正文SHA：{snapshot.version}；模型：{model_format} · {EVALUATOR_VERSION}",
                   f"HTTP发布头：{snapshot.last_modified or '未提供'}（不是生成时间）")
    if not snapshot.model_supported:
        notes += ("模型未支持，计算指标不可用；基础事实排行仍可使用。",)
    absent_difficulties = ()
    if request.song_id is not None:
        chart_difficulties = {c["difficulty"] for s in snapshot.data["songs"] if s["id"] == request.song_id for c in s["charts"]}
        absent_difficulties = tuple(d for d in request.difficulties if d not in chart_difficulties)
        if absent_difficulties:
            available = "、".join(d for d in DIFFICULTIES if d in chart_difficulties) or "暂无"
            notes += (f"歌曲已收录，但请求谱面未收录：{'、'.join(absent_difficulties)}；已收录难度：{available}。未自动更换难度。",)
    for actual, label in (("bgm", "BGM"), ("chart", "谱面")):
        positions = [str(i) for i, r in enumerate(visible, 1) if r.warnings and r.length_source == actual]
        if positions:
            notes += (f"* 本页第{'、'.join(positions)}条：时长回退为{label}长度。",)
    page_notice = f"第 {spec.page}/{pages} 页 · 共 {total} 条谱面 · 每页 {spec.limit} 条"
    if spec.page < pages:
        page_notice += "\n下一页：/" + replace(spec, page=spec.page + 1).command_label()

    def fmt(v, places=3):
        return "未知" if v is None else f"{v:.{places}f}"

    def numeric_cells(row):
        if request.ranking in {"efficiency", "score"}:
            return ("分/综合力", "分/综合力/分钟"), (fmt(row.score), fmt(row.eff))
        if request.ranking == "event":
            if request.power:
                return ("模型达标机会", "模型达标局/小时"), ("未知" if row.chance is None else f"{100 * row.chance:.2f}%", fmt(row.goal))
            return ("均值所需综合力", "局/小时"), ("未知" if row.need is None else str(math.ceil(row.need)), fmt(row.per_hour))
        if request.ranking == "speed":
            return (("判定Note", "N/s"), (str(row.notes) if row.notes is not None else "未知", fmt(row.density))) if request.speed == "density" else (("主要BPM", "最大BPM"), (fmt(row.bpm), fmt(row.bpm_max)))
        if request.ranking == "level":
            return ("判定Note", "显示等级"), (str(row.notes) if row.notes is not None else "未知", "未知" if row.level is None else f"{row.level:g}")
        if request.ranking == "notes":
            return ("主要BPM", "判定Note"), (fmt(row.bpm), str(row.notes) if row.notes is not None else "未知")
        if request.ranking in {"longest", "shortest"}:
            return ("判定Note", "主要BPM"), (str(row.notes) if row.notes is not None else "未知", fmt(row.bpm))
        return ("判定Note", "跳过/综合力"), (str(row.notes) if row.notes is not None else "未知", fmt(row.skip))
    cells, details = [], []
    numeric_labels = ("分/综合力", "分/综合力/分钟")
    for i, row in enumerate(visible, 1):
        numeric_labels, values = numeric_cells(row)
        duration = "未知" if row.seconds is None else f"{row.seconds:.1f}秒"
        if row.warnings:
            duration += "*"
        cells.append((str(i), songs[row.song_id].title, row.difficulty, "未知" if row.level is None else f"{row.level:g}", duration, *values))
        detail = f"页内{i} · 全榜{selected.index(row) + 1} · 比较池{pool.index(row) + 1} · ID {row.song_id} / scoreId {row.score_id}\n" + describe_song(songs[row.song_id])
        if row.warnings:
            detail += "\n" + "；".join(row.warnings)
        if request.song_id is not None and request.ranking in {"efficiency", "score", "event"}:
            detail += f"\n单局 {fmt(row.score)} 分/综合力；效率 {fmt(row.eff)} 分/综合力/分钟"
            if baseline and row.eff is not None:
                detail += f"；相对所选比较池最高效率 {row.eff / baseline * 100:.2f}%（与搜索、分页和排序方向无关）"
            if row.figures:
                detail += f"\nseed样本 {row.figures.seeds}；无技能系数样本区间 {fmt(row.figures.base_range[0])}～{fmt(row.figures.base_range[1])}（原生分布未知）"
            if request.ranking in {"efficiency", "event"}:
                if row.figures is None or row.seconds is None or request.ranking == "event" and any(t is None for t in row.thresholds):
                    detail += "\n前沿证明所需字段不完整，支配关系未知。"
                else:
                    ids = dominance.get(row.score_id) if request.front else tuple(r.score_id for r in pool if r.score_id != row.score_id and dominates(r, row, request.ranking))
                    detail += (f"\n被比较池内 {len(ids)} 条谱面强支配。"
                               if ids else "\n所选完整有效比较池内未被强支配。")
        if request.ranking == "event":
            detail += f"\n均值所需综合力：{'未知' if row.need is None else math.ceil(row.need)}（均值估算）；局/小时：{fmt(row.per_hour)}"
            if request.power:
                detail += f"；达标技能顺序：{row.successes}/120"
        details.append(detail)
    columns = ("序号", "歌曲", "难度", "等级", "时长", *numeric_labels)
    text = "[分数表]\n" + conditions + "\n" + " | ".join(columns) + "\n"
    text += "\n".join(" | ".join(c) + "\n" + d for c, d in zip(cells, details)) if visible else "没有符合条件的有效谱面；未自动更换来源、场景或难度。"
    text += "\n" + page_notice + "\n" + "\n".join(notes)
    status = "success" if visible else "data_unavailable" if missing or unknown_rows or absent_difficulties else "empty"
    # Keep the existing precise one-chart text card; only multi-chart results
    # enter the frozen list table. Text and rows still share this capture.
    single_chart = request.song_id is not None and len(request.difficulties) == 1 and len(visible) == 1
    if single_chart:
        row, cell, song = visible[0], cells[0], songs[visible[0].song_id]
        lines = [f"{song.title}（{song.id}） · {song.band}",
                 f"{row.difficulty} · 等级 {cell[3]}",
                 f"{numeric_labels[0]}：{cell[5]}；{numeric_labels[1]}：{cell[6]}",
                 "时长：" + (f"{cell[4]}（{'BGM' if row.length_source == 'bgm' else '谱面'}）" if row.seconds is not None else "未知"),
                 f"所选比较池排名：{pool.index(row) + 1}/{len(pool)}"]
        # The table fallback includes row identity, but the one-song card already
        # names the song above; present its facts once, without a pipe table.
        detail_lines = details[0].splitlines()[1:]
        if request.ranking in {"efficiency", "score"}:
            detail_lines = [line for line in detail_lines if not line.startswith("单局 ")]
            if baseline and row.eff is not None:
                detail_lines.append(f"相对所选比较池最高效率：{row.eff / baseline * 100:.2f}%")
        lines += detail_lines + ["", "条件：" + conditions, "", *notes]
        text = "\n".join(lines)
    return MetaAnswer(text, tuple(visible), status, () if single_chart else columns, () if single_chart else tuple(cells), conditions, page_notice, notes,
                      tuple(songs[r.song_id].jacket_url for r in visible), tuple(songs[r.song_id] for r in visible),
                      total, len(unknown_ids), unknown_rows, versions, True, "TW共通参考 · 歌曲分数表", diagnostics)
