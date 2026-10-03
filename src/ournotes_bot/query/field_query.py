# L3
# Input: Bounded field/count questions, canonical QuerySpec and captured catalog data.
# Output: Local QueryResult.short_text with explicit status and source limitations.
# Pos: Query / Deterministic field projections; see L2-2.md.
# Effects: Card skills may load one detail; no model, quota, artwork or chart-note fetch.
"""Small factual questions should read only the requested facts."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import replace

from ..data import localized_text, note_text
from ..sources.haneoka.song_traits import COLORS
from .song_identity import exact_songs, unique_candidates, candidate_text, literal_song_match, QueryProblem
from .song_query import parse_filter, execute as filter_songs
from .entity_lexicon import EntityRef

FIELD_LABELS = {"notes": "Note", "level": "等级", "color": "颜色", "missions": "激奏", "difficulties": "已收录难度"}


def parse_field_question(question, repository):
    from ..structured_query import QuerySpec
    text = unicodedata.normalize("NFKC", question).strip().strip("?？。！!")
    count = re.fullmatch(r"(.+?)(?:一共|总共)?(?:有)?(?:多少|几)(首|张)(?:歌曲|曲目|歌|卡牌|卡)?", text)
    if count:
        term, unit = count[1], count[2]
        if unit == "首":
            if not exact_songs(repository, term):
                term = re.sub(r"(?:的)?(?:歌曲|曲目|歌)$", "", term)
            term = "" if term in {"全部", "所有", "全库", ""} else term.rstrip("的 ")
            try:
                shared = parse_filter(term, repository, force=True)
            except ValueError as exc:
                return QueryProblem(str(exc))
            return QuerySpec("song", field="count", song_filter=shared)
        support = bool(re.search(r"SNAP|支援卡", term, re.I))
        term = re.sub(r"(?:的)?(?:成员卡|角色卡|卡牌|卡|SNAP|支援卡)$", "", term, flags=re.I).strip()
        term = "" if term in {"全部", "所有", "全库"} else term
        return QuerySpec("support_card" if support else "card", field="count", card_query=term)
    skill = re.fullmatch(r"(成员卡|角色卡|SNAP|支援卡|卡牌|卡)\s*(?:ID\s*[:=]?)?(\d+)\s*(?:的)?\s*(队长|LIVE|激奏|击奏|支援)?技能(?:效果)?(?:是什么|有哪些)?", text, re.I)
    if not skill:
        reversed_id = re.fullmatch(r"(\d+)号?(?:的)?\s*(成员卡|角色卡|SNAP|支援卡|卡牌|卡)\s*(?:的)?(队长|LIVE|激奏|击奏|支援)?技能(?:效果)?(?:是什么|有哪些)?", text, re.I)
        if reversed_id:
            kind, ident, part = reversed_id[2], reversed_id[1], reversed_id[3]
        else:
            kind = ident = part = None
    else:
        kind, ident, part = skill.groups()
    if ident:
        if kind in {"卡", "卡牌"}:
            return QueryProblem(f"请明确卡牌类型：/问 成员卡 {ident} 技能 或 /问 SNAP {ident} 技能。", "ambiguous")
        support = kind.lower() in {"snap", "支援卡"}
        intent = "support_card" if support else "card"
        skill_kind = {"队长": "leader", "live": "live", "激奏": "gekisou", "击奏": "gekisou", "支援": "live"}.get((part or "").lower(), "")
        return QuerySpec(intent, EntityRef(intent, int(ident)), field="skills", skill_kind=skill_kind)
    patterns = (
        ("notes", r"(.+?)(?:的)?(?:Notes?|物量|音符数)(?:是多少|有多少|多少|是什么)?"),
        ("notes", r"(.+?)(?:有)?多少(?:个)?(?:Notes?|音符)"),
        ("level", r"(.+?)(?:的)?(?:等级|难度等级)(?:是多少|多少|是什么)?"),
        ("level", r"(.+?)(?:是)?几级"),
        ("color", r"(.+?)(?:的)?(?:颜色|属性)(?:是什么|是啥)?"),
        ("missions", r"(.+?)(?:的)?(?:激奏|击奏)(?:类型|顺序)?(?:是什么|是啥|有哪些)?"),
        ("difficulties", r"(.+?)(?:的)?(?:已收录难度|难度有哪些|有哪些难度|收录了哪些难度)"),
    )
    for field, pattern in patterns:
        match = re.fullmatch(pattern, text, re.I)
        if not match:
            continue
        term, difficulty = match[1].rstrip("的 "), ""
        if not exact_songs(repository, term):
            diff = re.search(r"(?<![A-Za-z])(EXPERT|EXP|EX|HARD|HD|NORMAL|NM|EASY|EZ)$", term, re.I)
            if diff:
                from .song_conditions import DIFFICULTY_NAMES
                difficulty, term = DIFFICULTY_NAMES[diff[0].upper()], term[:diff.start()].rstrip("的 ")
        if not term:
            return QueryProblem("请指定歌曲名称或 ID。")
        if not exact_songs(repository, term):
            if re.search(r"[=:<>]", term):
                return QueryProblem("字段短答含无法识别的条件；请使用明确歌曲名称、难度和字段。")
            if term.isdecimal() and 1 <= int(term) < 100000:
                term = str(100000 + int(term))
        return QuerySpec("chart", difficulty=difficulty, display_name=term, field=field)
    return None


def answer_field(spec, repository):
    from ..structured_query import QueryResult
    def result(text, status="success", **records):
        return QueryResult(spec, short_text=text, status=status, **records)
    if spec.field == "count":
        if spec.intent == "song":
            selected = filter_songs(spec.song_filter, repository)
            if selected.status in {"unknown_entity", "data_unavailable"}:
                return result(selected.text(), selected.status)
            count = len({s.id for s in selected.songs})
            label = "已确认匹配" if selected.warning else "符合条件"
            return result(f"{label}的歌曲共 {count} 首。" + ("\n" + selected.warning if selected.warning else ""),
                          "success" if count else "empty", songs=selected.songs)
        from .card_catalog import parse_card_request, execute_card_request
        try:
            req = parse_card_request(spec.card_query or "", repository, support=spec.intent == "support_card")
        except ValueError as exc:
            return result(str(exc), "invalid_arguments")
        if req.card_id is not None:
            req = replace(req, card_id=None, mode="list", filters={**req.filters, "title_ids": (req.card_id,)})
        selected = execute_card_request(req, repository)
        if selected.error:
            return result(selected.error, selected.status)
        count = len({c.id for c in selected.cards})
        return result(f"符合条件的{'SNAP' if spec.intent == 'support_card' else '成员卡'}共 {count} 张。",
                      "success" if count else "empty")
    if spec.field == "skills":
        support = spec.intent == "support_card"
        cards = repository.support_cards if support else repository.cards
        matches = [c for c in cards if c.id == spec.subject.value]
        if len(matches) != 1:
            return result("未找到唯一卡牌，请核对类型和 ID。", "ambiguous" if matches else "unknown_entity")
        if support and spec.skill_kind == "leader":
            return result(f"SNAP ID {matches[0].id}：队长技能不适用。")
        card = (repository.support_card_with_detail if support else repository.card_with_detail)(matches[0])
        names = {"leader": "leaderSkill", "live": "supportSkill" if support else "liveSkill",
                 "gekisou": "gekisouSupportSkill" if support else "gekisouSkill"}
        skills = tuple(s for s in card.skills if not spec.skill_kind or s.kind == names[spec.skill_kind])
        if not skills:
            return result(f"{'SNAP' if support else '成员卡'} ID {card.id}：技能详情暂无资料。", "data_unavailable")
        lines = [f"{'SNAP' if support else '成员卡'} ID {card.id} · {localized_text(card, 'title')}"]
        for skill in skills:
            lines += [f"{localized_text(skill, 'name')}（Lv.5）",
                      localized_text(skill, "description") or "效果暂无资料。"]
        lines.append("来源：Project Yume 公开技能详情；等级为资料口径，不是玩家培养状态。")
        if card.catalog.get("detail_stale"):
            lines.append("使用上次有效详情缓存。")
        return result("\n".join(lines))
    matches = unique_candidates(repository, spec.display_name)
    if len(matches) != 1 or not literal_song_match(repository, spec.display_name, matches[0]):
        return result(candidate_text(matches, spec.difficulty) if matches else f"未找到歌曲「{spec.display_name}」。",
                      "ambiguous" if matches else "unknown_entity")
    song = matches[0]
    charts = tuple(c for c in song.charts if not spec.difficulty or c.difficulty == spec.difficulty)
    prefix = f"{localized_text(song, 'title')}（ID {song.id}）"
    if spec.field in {"notes", "level"}:
        if not charts:
            return result(prefix + "：该难度暂无资料。", "data_unavailable")
        lines = [f"{c.difficulty}：{note_text(c.notes) if spec.field == 'notes' else f'Lv.{c.display_level:g}'}" for c in charts]
        unavailable = spec.field == "notes" and all(c.notes is None for c in charts)
        return result(prefix + "\n" + "\n".join(lines) + "\n来源：Project Yume 公开谱面资料。",
                      "data_unavailable" if unavailable else "success", songs=(song,), chart=(song, charts))
    if spec.field == "difficulties":
        return result(prefix + "\n已收录难度：" + (" / ".join(c.difficulty for c in song.charts) or "暂无资料"),
                      "success" if song.charts else "data_unavailable", songs=(song,))
    traits = song.traits
    value = (COLORS.get(traits.color) if traits and spec.field == "color" else
             " → ".join(traits.missions) if traits and traits.missions else None)
    source = "Haneoka 日服" + (" · 旧缓存" if traits and traits.stale else "")
    if traits and traits.release:
        source += " · " + traits.release
    return result(prefix + f"\n{FIELD_LABELS[spec.field]}：{value or '暂无资料'}\n来源：{source}",
                  "success" if value else "data_unavailable", songs=(song,))
