"""Execute /问 query conditions without reparsing a generated command string."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .commands import (PAGE_SIZE, _format_card_detail, _format_support_card_detail,
                       filter_card_rarity, page_notice, page_slice, rarity_text)
from .data import Card, Chart, Skill, Song, SongRepository, SupportCard, character_identity, localized_text, normalize
from .entity_lexicon import EntityRef
from .i18n import tr
from .efficiency_query import MetaAnswer, execute_efficiency


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

    def query_label(self) -> str:
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
        if self.intent == "efficiency":
            parts = ["查分数表"]
            if self.subject:
                parts.append(str(self.subject.value))
            if self.difficulty:
                parts.append("全难度" if self.difficulty == "ALL" else self.difficulty)
            if self.comparison and self.level is not None:
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
    if spec.intent == "efficiency":
        return QueryResult(spec, meta=execute_efficiency(spec, repository))
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
    song = next((row for row in repository.songs if row.id == spec.subject.value), None)
    if song is None:
        return None
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
            f"{song.id}  {localized_text(song, 'title', locale)} · {localized_text(song, 'band', locale)}  "
            + " / ".join(f"{chart.display_level:g}" for chart in song.charts)
            for song in visible
        ) + "\n" + page_notice("songs", spec.query_label(), spec.page, len(matches), locale) + "\n" + tr(locale, "next_chart")

    if spec.intent == "chart":
        chart = selected.chart
        if chart is None:
            return tr(locale, "not_found_chart", query=spec.display_name)
        song, charts = chart
        return f"[{tr(locale, 'chart')}] {localized_text(song, 'title', locale)}（ID {song.id}）\n" + (
            "\n".join(f"{chart.difficulty} Lv.{chart.display_level:g} · {chart.notes} Notes" for chart in charts)
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
        for card in visible:
            row = (f"{card.id}  {rarity_text(card.rarity)} {localized_text(card, 'character', locale)} · "
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
            f"{card.id}  {rarity_text(card.rarity)} {localized_text(card, 'character', locale)} · "
            f"{localized_text(card, 'title', locale)}" for card in visible
        ) + "\n" + query_page_notice(spec, len(matches), locale) + "\n" + tr(locale, "next_support_card")

    raise ValueError("unsupported query intent")
