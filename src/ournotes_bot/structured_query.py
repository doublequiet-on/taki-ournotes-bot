# L3
# Input: QuerySpec（可选分数表 MetaRequest／榜线专项请求）、SongRepository；answer_for 复用捕获结果。
# Output: resolve_query 返回 QueryResult，保存专项选择供文字／图片消费；answer_for 返回确定性 str。
# Pos: Query / Deterministic 的共享结构化契约与执行入口；见 query/L2-2.md。
# Effects/Dependencies: 调用 query 专项模块及根 commands；详情／效率可经 Data 联网或读写缓存，不绘图或导入 QQ。

"""Execute /问 query conditions without reparsing a generated command string."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .commands import (PAGE_SIZE, _format_card_detail, _format_support_card_detail,
                       filter_card_rarity, page_notice, page_slice, rarity_text)
from .data import Card, Chart, Skill, Song, SongRepository, SupportCard, character_identity, localized_text, normalize, note_text
from .query.entity_lexicon import EntityRef
from .i18n import tr
from .query.efficiency_query import MetaAnswer, execute_efficiency
from .query.card_catalog import CardAnswer, query_cards
from .query.song_query import SongAnswer, SongFilter, parse_filter, execute as execute_song_filter
from .query.event_cutoff_query import CutoffRequest, CutoffAnswer, execute_cutoff
from .sources.haneoka.song_traits import describe as describe_song
from .query.meta_parameters import MetaRequest


@dataclass(frozen=True)
class QuerySpec:
    intent: str
    subject: EntityRef | None = None
    difficulty: str = ""
    comparison: str = ""
    level: float | None = None
    page: int = 1
    display_name: str = ""
    skill_query: str = ""
    skill_kind: str = ""
    rarity: int | None = None
    metric: str = "eff"
    order: str = "desc"
    limit: int = 30
    card_query: str | None = None
    song_query: str | None = None
    cutoff_request: CutoffRequest | None = None
    song_filter: SongFilter | None = None
    field: str = ""
    meta_request: MetaRequest | None = None

    def __post_init__(self):
        if self.card_query is not None:
            from .commands import _split_page
            query, page = _split_page(self.card_query)
            object.__setattr__(self, "card_query", query)
            if self.page == 1 and page != 1:
                object.__setattr__(self, "page", page)

    def query_label(self) -> str:
        if self.song_query is not None:
            return self.song_query
        parts = [self.display_name] if self.display_name else []
        if self.rarity is not None:
            parts.append(f"{self.rarity}星")
        if self.comparison and self.level is not None:
            parts.append(f"lv{self.comparison}{self.level:g}")
            if self.difficulty:
                parts.append(f"diff={self.difficulty}")
        if self.skill_query:
            parts.append(f"技能={self.skill_query}")
        if self.skill_kind:
            kind = {"leader": "队长", "live": "Live", "gekisou": "激奏"}.get(
                self.skill_kind, self.skill_kind
            )
            parts.append(f"技能类型={kind}")
        return " ".join(parts)

    def command_label(self) -> str:
        if self.field:
            if self.field == "count":
                query = self.song_filter.query if self.song_filter else self.card_query or ""
                label = {"song": "歌曲", "card": "成员卡", "support_card": "SNAP"}[self.intent]
                return f"问 {query or '全部'} {label}有多少{'首' if self.intent == 'song' else '张'}"
            if self.field == "skills":
                return f"问 {'SNAP' if self.intent == 'support_card' else '成员卡'} {self.subject.value} 技能"
            from .query.field_query import FIELD_LABELS
            return f"问 {self.display_name} {self.difficulty} {FIELD_LABELS[self.field]}".replace("  ", " ")
        if self.cutoff_request is not None:
            return self.cutoff_request.command_label()
        if self.meta_request is not None:
            return self.meta_request.command_label(limit=self.limit, page=self.page)
        if self.card_query is not None:
            return (("查支援卡 " if self.intent == "support_card" else "查卡 ")
                    + self.card_query + (f" 页{self.page}" if self.page > 1 else ""))
        if self.intent == "efficiency":
            parts = ["查分数表"]
            if self.song_filter:
                parts.append(self.song_filter.query)
            elif self.subject:
                parts.append(str(self.subject.value))
            if self.difficulty and not self.song_filter:
                parts.append("全难度" if self.difficulty == "ALL" else self.difficulty)
            if self.comparison and self.level is not None and not self.song_filter:
                parts.append(f"lv{self.comparison}{self.level:g}")
            if self.limit != 30:
                parts.append(f"前{self.limit}")
            if self.metric != "eff":
                parts.append(f"指标={self.metric}")
            if self.order != "desc":
                parts.append(f"排序={self.order}")
            parts.append(f"页{self.page}")
            return " ".join(parts)
        if self.intent == "card" and (self.skill_query or self.skill_kind):
            subject = f"{self.display_name}的" if self.display_name else ""
            kind = {"leader": "队长", "live": "Live", "gekisou": "激奏"}.get(self.skill_kind, "")
            rarity = f"{self.rarity}星" if self.rarity is not None else ""
            label = f"问 {subject}{rarity}{self.skill_query}{kind}技能的成员卡"
            return label + (f" 页{self.page}" if self.page > 1 else "")
        if self.intent == "support_card" and self.subject is None:
            rarity = f"{self.rarity}星" if self.rarity is not None else ""
            label = f"问 {rarity}支援卡有哪些"
            return label + (f" 页{self.page}" if self.page > 1 else "")
        name = {"song": "查曲", "chart": "查谱面", "card": "查卡",
                "support_card": "查支援卡"}[self.intent]
        query = self.query_label()
        if self.intent == "chart" and self.difficulty:
            query += f" {self.difficulty}"
        if self.page > 1:
            query += f" 页{self.page}"
        return f"{name} {query.strip()}".strip()


@dataclass(frozen=True)
class QueryResult:
    spec: QuerySpec
    songs: tuple[Song, ...] = ()
    cards: tuple[Card, ...] = ()
    support_cards: tuple[SupportCard, ...] = ()
    chart: tuple[Song, tuple[Chart, ...]] | None = None
    meta: MetaAnswer | None = None
    catalog: CardAnswer | None = None
    song_selection: SongAnswer | None = None
    cutoff: CutoffAnswer | None = None
    status: str = "success"
    short_text: str | None = None

    def __post_init__(self):
        if self.short_text is not None:
            return
        for answer in (self.cutoff, self.meta, self.catalog, self.song_selection):
            if answer is not None:
                object.__setattr__(self, "status", answer.status)
                return
        if self.spec.intent == "chart" and self.chart is None:
            object.__setattr__(self, "status", "unknown_entity")
        elif self.spec.intent == "chart" and not self.chart[1]:
            object.__setattr__(self, "status", "data_unavailable")
        elif self.spec.intent != "chart" and not (self.songs or self.cards or self.support_cards):
            object.__setattr__(self, "status", "empty")


def query_page_notice(spec: QuerySpec, total: int, locale: str) -> str:
    uses_ask = ((spec.intent == "card" and (spec.skill_query or spec.skill_kind))
                or (spec.intent == "support_card" and spec.subject is None))
    if not uses_ask:
        kind = {"song": "songs", "card": "cards", "support_card": "support_cards"}[spec.intent]
        return page_notice(kind, spec.query_label(), spec.page, total, locale)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    first = "/" + replace(spec, page=1).command_label()
    if not 1 <= spec.page <= pages:
        return {"zh": f"页码超出范围：共 {pages} 页。发送 {first} 查看首页。",
                "en": f"Page out of range: {pages} page(s). Use {first} for page 1.",
                "ja": f"ページは全{pages}ページです。{first} で1ページ目を表示します。"}[locale]
    summary = {"zh": f"第 {spec.page}/{pages} 页 · 共 {total}张",
               "en": f"Page {spec.page}/{pages} · {total} cards",
               "ja": f"{spec.page}/{pages}ページ · 全{total}枚"}[locale]
    if spec.page < pages:
        next_query = "/" + replace(spec, page=spec.page + 1).command_label()
        return summary + "\n" + {"zh": "下一页：", "en": "Next: ", "ja": "次："}[locale] + next_query
    return summary


def resolve_query(spec: QuerySpec, repository: SongRepository) -> QueryResult:
    """Capture records for both answer text and image within one request."""
    if spec.field:
        from .query.field_query import answer_field
        return answer_field(spec, repository)
    if spec.cutoff_request is not None:
        return QueryResult(spec, cutoff=execute_cutoff(spec.cutoff_request, repository))
    if spec.song_query is not None:
        answer = execute_song_filter(parse_filter(spec.song_query, repository, force=True), repository, spec.page)
        return QueryResult(spec, songs=answer.songs, song_selection=answer)
    if spec.card_query is not None:
        answer = query_cards(f"{spec.card_query} 页{spec.page}", repository, support=spec.intent == "support_card")
        return QueryResult(spec, catalog=answer, cards=answer.cards if spec.intent == "card" else (),
                           support_cards=answer.cards if spec.intent == "support_card" else ())
    if spec.intent == "efficiency":
        return QueryResult(spec, meta=execute_efficiency(spec, repository))
    if spec.intent in {"card", "support_card"} and not (spec.skill_query or spec.skill_kind):
        query = ""
        if spec.rarity is not None:
            query = f"{spec.rarity}星 "
        if spec.subject:
            prefix = {"character": "角色=", "band": "乐队="}.get(spec.subject.kind, "")
            query += prefix + str(spec.subject.value)
        query += f" 页{spec.page}"
        answer = query_cards(query, repository, support=spec.intent == "support_card")
        return QueryResult(spec, catalog=answer, cards=answer.cards if spec.intent == "card" else (),
                           support_cards=answer.cards if spec.intent == "support_card" else ())
    if spec.intent == "song":
        return QueryResult(spec, songs=tuple(songs_for(spec, repository)))
    if spec.intent == "card":
        return QueryResult(spec, cards=tuple(cards_for(spec, repository)))
    if spec.intent == "support_card":
        return QueryResult(spec, support_cards=tuple(support_cards_for(spec, repository)))
    if spec.intent == "chart":
        return QueryResult(spec, chart=chart_for(spec, repository))
    raise ValueError("unsupported query intent")


def songs_for(spec: QuerySpec, repository: SongRepository) -> list[Song]:
    if spec.subject is None:
        songs = list(repository.songs)
    elif spec.subject.kind == "song":
        songs = [song for song in repository.songs if song.id == spec.subject.value]
    elif spec.subject.kind == "band":
        songs = [song for song in repository.songs
                 if normalize(song.band) == normalize(str(spec.subject.value))]
    else:
        return []
    if not spec.comparison or spec.level is None:
        return songs
    compare = {
        ">=": lambda actual: actual >= spec.level,
        ">": lambda actual: actual > spec.level,
        "<=": lambda actual: actual <= spec.level,
        "<": lambda actual: actual < spec.level,
    }[spec.comparison]
    if spec.difficulty:
        return [song for song in songs if any(
            chart.difficulty == spec.difficulty and compare(chart.display_level)
            for chart in song.charts
        )]
    return [song for song in songs if song.charts and compare(
        max(chart.display_level for chart in song.charts)
    )]


def chart_for(spec: QuerySpec, repository: SongRepository) -> tuple[Song, tuple[Chart, ...]] | None:
    if spec.subject is None or spec.subject.kind != "song":
        return None
    matches = [row for row in repository.songs if row.id == spec.subject.value]
    if len(matches) != 1:
        return None
    song = matches[0]
    charts = tuple(chart for chart in song.charts
                   if not spec.difficulty or chart.difficulty == spec.difficulty)
    return song, charts


_SKILL_KINDS = {"leader": "leaderSkill", "live": "liveSkill", "gekisou": "gekisouSkill"}


def matching_skills(card: Card, spec: QuerySpec) -> tuple[Skill, ...]:
    needle = normalize(spec.skill_query)
    expected_kind = _SKILL_KINDS.get(spec.skill_kind, "")
    matches = []
    for skill in card.skills:
        if expected_kind and skill.kind != expected_kind:
            continue
        values = [skill.name, skill.description]
        values.extend(name for fields in skill.localized.values() for name in fields.values())
        if not needle or any(needle in normalize(value) for value in values if value):
            matches.append(skill)
    return tuple(matches)


def cards_for(spec: QuerySpec, repository: SongRepository) -> list[Card]:
    subject = spec.subject
    if subject is None:
        matches = list(repository.cards) if spec.skill_query or spec.skill_kind or spec.rarity is not None else []
    elif subject.kind == "card":
        matches = [card for card in repository.cards if card.id == subject.value]
    elif subject.kind == "character":
        matches = [card for card in repository.cards
                   if normalize(character_identity(card.character)) == normalize(character_identity(str(subject.value)))]
    elif subject.kind == "band":
        matches = [card for card in repository.cards
                   if normalize(card.band) == normalize(str(subject.value))]
    else:
        return []
    if spec.skill_query or spec.skill_kind:
        matches = [card for card in matches if matching_skills(card, spec)]
    return sorted(filter_card_rarity(matches, spec.rarity), key=lambda card: (-card.rarity, card.id))


def support_cards_for(spec: QuerySpec, repository: SongRepository) -> list[SupportCard]:
    subject = spec.subject
    if subject is None:
        matches = list(repository.support_cards)
    elif subject.kind == "support_card":
        matches = [card for card in repository.support_cards if card.id == subject.value]
    elif subject.kind == "character":
        matches = [card for card in repository.support_cards if any(
            normalize(character_identity(name)) == normalize(character_identity(str(subject.value)))
            for name in card.characters
        )]
    else:
        return []
    return sorted(filter_card_rarity(matches, spec.rarity), key=lambda card: (-card.rarity, card.id))


def answer_for(spec: QuerySpec, repository: SongRepository, locale: str = "zh",
               result: QueryResult | None = None) -> str:
    selected = result if result is not None else resolve_query(spec, repository)
    if selected.short_text is not None:
        return selected.short_text
    if selected.cutoff is not None:
        return selected.cutoff.text
    if selected.song_selection is not None:
        return selected.song_selection.text(locale)
    if selected.catalog is not None:
        return selected.catalog.text(locale)
    if spec.intent == "efficiency":
        return selected.meta.text
    if spec.intent == "song":
        matches = selected.songs
        if not matches:
            return tr(locale, "not_found_song", query=spec.query_label())
        visible = page_slice(matches, spec.page)
        if not visible:
            return page_notice("songs", spec.query_label(), spec.page, len(matches), locale)
        return tr(locale, "songs") + "\n" + "\n".join(
            f"{index:02d}. ID {song.id}  {localized_text(song, 'title', locale)} · {localized_text(song, 'band', locale)}  "
            + " / ".join(f"{chart.display_level:g}" for chart in song.charts)
            + "\n" + describe_song(song) for index, song in enumerate(visible, 1)
        ) + "\n" + page_notice("songs", spec.query_label(), spec.page, len(matches), locale) + "\n" + tr(locale, "next_chart")

    if spec.intent == "chart":
        chart = selected.chart
        if chart is None:
            return tr(locale, "not_found_chart", query=spec.display_name)
        song, charts = chart
        return f"[{tr(locale, 'chart')}] {localized_text(song, 'title', locale)}（ID {song.id}）\n{describe_song(song)}\n" + (
            "\n".join(f"{chart.difficulty} Lv.{chart.display_level:g} · {note_text(chart.notes)}" for chart in charts)
            or tr(locale, "no_charts")
        )

    if spec.intent == "card":
        if (spec.skill_query or spec.skill_kind) and not repository.member_skill_index_ready():
            return tr(locale, "skill_index_unavailable")
        matches = selected.cards
        if not matches:
            key = "not_found_skill_card" if spec.skill_query or spec.skill_kind else "not_found_card"
            return tr(locale, key, query=spec.query_label())
        if spec.subject and spec.subject.kind == "card":
            return _format_card_detail(repository.card_with_detail(matches[0]), locale)
        visible = page_slice(matches, spec.page)
        if not visible:
            return query_page_notice(spec, len(matches), locale)
        rows = []
        for index, card in enumerate(visible, 1):
            row = (f"{index:02d}. ID {card.id}  {rarity_text(card.rarity)} {localized_text(card, 'character', locale)} · "
                   f"{localized_text(card, 'title', locale)}")
            if spec.skill_query or spec.skill_kind:
                skill = matching_skills(card, spec)[0]
                description = localized_text(skill, "description", locale)
                summary = localized_text(skill, "name", locale)
                if description:
                    summary += " · " + (description[:70] + "…" if len(description) > 70 else description)
                row += f"\n  ↳ {summary}"
            rows.append(row)
        return tr(locale, "cards") + "\n" + "\n".join(rows) + "\n" + query_page_notice(
            spec, len(matches), locale
        ) + "\n" + tr(locale, "next_card")

    if spec.intent == "support_card":
        matches = selected.support_cards
        if not matches:
            return tr(locale, "not_found_support_card", query=spec.query_label())
        if spec.subject and spec.subject.kind == "support_card":
            return _format_support_card_detail(repository.support_card_with_detail(matches[0]), locale)
        visible = page_slice(matches, spec.page)
        if not visible:
            return query_page_notice(spec, len(matches), locale)
        return tr(locale, "support_cards") + "\n" + "\n".join(
            f"{index:02d}. ID {card.id}  {rarity_text(card.rarity)} {localized_text(card, 'character', locale)} · "
            f"{localized_text(card, 'title', locale)}" for index, card in enumerate(visible, 1)
        ) + "\n" + query_page_notice(spec, len(matches), locale) + "\n" + tr(locale, "next_support_card")

    raise ValueError("unsupported query intent")
