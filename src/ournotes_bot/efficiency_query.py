"""Platform-neutral parsing and presentation of verified song meta."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .data import normalize
from .entity_lexicon import EntityRef, find_anchor, resolve_entity
from .song_meta import DIFFICULTIES, MetaRow

PAGE_SIZE = 30
HELP = ("用法：/查分数表；/查分数表 [乐队] [EX] [lv<=25] [页2]；/查分数表 歌名或ID [EX]。\n"
        "默认全难度、每分钟得分效率从高到低，每页 30 条（同曲不同难度分别列出）；支持 前10/前20、指标=eff/score、排序=asc/desc。单曲默认 EXPERT。仅支持日服固定理想参考条件，不能指定队伍或其他模式。")
META_WORD = r"(?<![A-Za-z])meta(?=$|[\s?？。！!，,])"
MARKER = re.compile(r"分数表|效率|" + META_WORD, re.I)
FORBIDDEN = re.compile(r"推荐|攻略|预测|档线|代练|代肝|账号|编成|配队|抽卡|怎么打|如何打|最强|哪个好|哪个更好|比较强弱")


def parse_efficiency(query: str, repository, *, direct=False):
    from .commands import _split_page
    from .local_query import _DIFFICULTY, _DIFFICULTY_NAMES, _LEVEL_PHRASES
    from .structured_query import QuerySpec
    if not direct and not MARKER.search(query):
        return None
    if FORBIDDEN.search(query):
        return "效率查询不支持攻略、配队、推荐、账号操作或预测。\n" + HELP
    text = unicodedata.normalize("NFKC", query).strip().strip("?？。！!")
    text = re.sub(r"^/?查(?:分数表|效率)\s*", "", text)
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
    comparison, level = "", None
    explicit = re.search(r"lv\s*(>=|<=|>|<)\s*(\d+(?:\.\d+)?)", text, re.I)
    found = [(m, op) for pattern, op in _LEVEL_PHRASES if (m := pattern.search(text))] if not explicit else []
    if explicit:
        comparison, level = explicit.group(1), float(explicit.group(2))
        text = text[:explicit.start()] + " " + text[explicit.end():]
    elif len(found) == 1:
        match, comparison = found[0]
        level = float(match.group("level"))
        text = text[:match.start()] + " " + text[match.end():]
    elif found:
        return "目前只支持一个等级边界。\n" + HELP
    if level is not None and not 1 <= level <= 40:
        return "等级需为 1～40。\n" + HELP
    difficulties = list(_DIFFICULTY.finditer(text))
    all_difficulties = bool(re.search(r"全难度|所有难度", text))
    if len(difficulties) > 1 or (all_difficulties and difficulties):
        return "可省略难度查看全难度榜，或指定一种难度筛选。\n" + HELP
    difficulty = ""
    if difficulties:
        match = difficulties[0]
        difficulty = _DIFFICULTY_NAMES[re.sub(r"难度$", "", match.group().upper())]
        text = text[:match.start()] + " " + text[match.end():]
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
                  + META_WORD + r"|最高|最低", "", text, flags=re.I)
    term = text.strip(" 的，,。？?！! ").strip()
    subject = None
    if term:
        if term.isdecimal() and 1 <= int(term) < 100000 and not ranking:
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
                    return "请明确歌曲或条件；候选：\n" + "\n".join(f"{s.id} {s.title}" for s in matches) + "\n" + HELP
                return f"未找到歌曲或乐队「{term}」，也可能包含不支持的条件；未忽略任何条件。\n" + HELP
    if subject and subject.kind not in {"song", "band"}:
        return "效率查询只支持歌曲或乐队。\n" + HELP
    if all_difficulties and subject and subject.kind == "song":
        difficulty = "ALL"
    return QuerySpec("efficiency", subject, difficulty, comparison, level, page,
                     str(subject.value) if subject else "", metric=metric, order=order, limit=limit)


@dataclass(frozen=True)
class MetaAnswer:
    text: str
    rows: tuple[MetaRow, ...] = ()
    status: str = "success"
    columns: tuple[str, ...] = ()
    cells: tuple[tuple[str, ...], ...] = ()
    scope: str = ""
    page_notice: str = ""
    notes: tuple[str, ...] = ()
    jackets: tuple[str, ...] = ()


def execute_efficiency(spec, repository) -> MetaAnswer:
    snapshot = repository.song_meta.get()
    if snapshot is None:
        return MetaAnswer("效率数据暂不可用，请稍后重试；歌曲、卡牌查询仍可使用。", status="data_unavailable")
    subject = spec.subject
    difficulty = spec.difficulty or ("EXPERT" if subject and subject.kind == "song" else "")
    if difficulty == "ALL":
        difficulty = ""
    difficulty_label = difficulty or "全难度"
    metric = spec.metric
    if metric not in {"eff", "score"} or spec.order not in {"asc", "desc"} or spec.limit not in {10, 20, 30}:
        return MetaAnswer("不支持此排序口径。\n" + HELP, status="invalid_arguments")
    names = {}
    for song in repository.songs:
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
    eligible = [r for r in mapped if (not difficulty or r.difficulty == difficulty) and r.reference == "reference"
                and getattr(r, metric) is not None]
    # Numeric song ID breaks ties deterministically; no missing value becomes zero.
    eligible.sort(key=lambda r: ((-1 if spec.order == "desc" else 1) * getattr(r, metric), r.song_id,
                                 DIFFICULTIES.index(r.difficulty)))
    scope = f"{difficulty_label}{'（默认）' if not spec.difficulty else ''} · {'eff 每分钟得分效率' if metric == 'eff' else 'score 得分系数'} {'降序' if spec.order == 'desc' else '升序'}"
    assumptions = "模式：上游理想参考；全 PERFECT、Fever、技能×2.5/10秒、曲间30秒。非官方结论或实得分保证。"
    footer = (f"Haneoka分析数据 · 日服 · {'旧缓存（刷新失败或过期）' if snapshot.stale else '有效缓存'}\n"
              f"本机获取：{snapshot.fetched_at}；上游版本：{snapshot.release} / {snapshot.source_version}\n"
              f"覆盖：{len(eligible)} 条{difficulty_label}有效谱面样本；同曲不同难度分别计数，非完整全曲榜。\n{assumptions}")
    if any(r.warnings for r in eligible):
        footer += "\n上游含参数默认值或谱面差异警告，请以来源页说明为准。"
    if subject and subject.kind == "song" and difficulty:
        song = names.get(subject.value, [])
        if len(song) != 1:
            return MetaAnswer("歌曲不存在或 ID 冲突。", status="empty")
        row = next((r for r in mapped if r.song_id == subject.value and r.difficulty == difficulty), None)
        if row is None or getattr(row, metric) is None:
            options = sorted({r.difficulty for r in mapped if r.song_id == subject.value and getattr(r, metric) is not None})
            return MetaAnswer(f"{song[0].title}（{subject.value}）已收录，但 {difficulty} 效率数据缺失、口径未确认或 ID 映射未确认。\n"
                              f"可用难度：{'、'.join(options) or '暂无'}；未自动更换难度。\n" + footer, status="empty")
        if spec.comparison and not _level_matches(row.level, spec.comparison, spec.level):
            return MetaAnswer("该曲不符合等级条件。\n" + footer, status="empty")
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
        return MetaAnswer(text, (row,))
    filtered = [r for r in eligible if (not subject or (subject.kind == "song" and r.song_id == subject.value)
                or (subject.kind == "band" and normalize(names[r.song_id][0].band) == normalize(str(subject.value))))
                and (not spec.comparison or _level_matches(r.level, spec.comparison, spec.level))]
    if subject and subject.kind == "song" and not filtered:
        available = any(r.song_id == subject.value for r in eligible)
        explanation = ("该曲不符合等级条件。" if available else
                       "歌曲已收录，但全难度分析数据缺失、口径未确认或 ID 映射未确认。")
        return MetaAnswer(explanation + "\n" + footer, status="empty")
    pages = max(1, (len(filtered) + spec.limit - 1) // spec.limit)
    subject_label = (names[subject.value][0].title if subject and subject.kind == "song" else
                     str(subject.value) if subject else "全部已映射歌曲")
    table_scope = f"{difficulty_label}{'（默认）' if not spec.difficulty else ''} · {subject_label}"
    if spec.comparison:
        table_scope += f" · 等级{spec.comparison}{spec.level:g}"
    table_scope += f" · {'每分钟得分效率' if metric == 'eff' else '得分系数'}{'从高到低' if spec.order == 'desc' else '从低到高'}"
    if spec.page > pages:
        return MetaAnswer(f"页码超出范围，共 {pages} 页。\n/" + replace(spec, page=1).command_label(), status="empty")
    visible = filtered[(spec.page - 1) * spec.limit:spec.page * spec.limit]
    columns = ("排名", "歌曲", "难度", "等级", "时长", "得分系数", "每分钟得分效率")
    cells = tuple((str(i), names[r.song_id][0].title, r.difficulty,
                   f"{r.level:g}" if r.level is not None else "未知", _duration(r.seconds),
                   _percentage(r.score), _percentage(r.eff))
                  for i, r in enumerate(visible, (spec.page - 1) * spec.limit + 1))
    page_notice = f"筛选内排序 · 第 {spec.page}/{pages} 页 · 共 {len(filtered)} 条谱面"
    if spec.page < pages:
        page_notice += "\n下一页：/" + replace(spec, page=spec.page + 1).command_label()
    metric_note = "得分系数 = score；每分钟得分效率 = eff，均为上游参考系数，百分数显示，不是实际得分。"
    notes = (f"Haneoka分析数据 · 日服 · {'旧缓存（刷新失败或过期）' if snapshot.stale else '有效缓存'} · "
             f"{len(eligible)} 条{difficulty_label}有效谱面样本（非完整全曲榜）",
             metric_note,
             "参考：全 PERFECT、含 Fever、技能×2.5/10秒、曲间30秒；非官方结论或实得分保证。",
             "同曲不同难度分别排名；时长为上游末判定点时长，不含曲间30秒，显示到秒。",
             "本机获取：" + datetime.fromisoformat(snapshot.fetched_at).astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
             + f" · 版本：{snapshot.release}")
    if any(r.warnings for r in eligible):
        notes += ("上游含参数默认值或谱面差异警告，详见来源页。",)
    text = f"[分数表]\n{table_scope}\n"
    text += (" | ".join(columns) + "\n" + "\n".join(" | ".join(row) for row in cells)
             if visible else "没有符合条件的有效效率数据。")
    text += "\n" + page_notice + "\n" + metric_note + "\n" + footer
    return MetaAnswer(text, tuple(visible), "success" if visible else "empty",
                      columns=columns, cells=cells, scope=table_scope, page_notice=page_notice, notes=notes,
                      jackets=tuple(names[r.song_id][0].jacket_url for r in visible))


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
