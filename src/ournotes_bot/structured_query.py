"""Execute /问 query conditions without reparsing a generated command string."""

from __future__ import annotations

from dataclasses import dataclass

from .commands import page_notice, page_slice
from .data import Card, Chart, Song, SongRepository, character_identity, localized_text, normalize
from .entity_lexicon import EntityRef
from .i18n import tr


@dataclass(frozen=True)
class QuerySpec:
    intent: str
    subject: EntityRef | None = None
    difficulty: str = ""
    comparison: str = ""
    level: float | None = None
    page: int = 1
    display_name: str = ""

    def query_label(self) -> str:
        parts = [self.display_name] if self.display_name else []
        if self.comparison and self.level is not None:
            parts.append(f"lv{self.comparison}{self.level:g}")
            if self.difficulty:
                parts.append(f"diff={self.difficulty}")
        return " ".join(parts)

    def command_label(self) -> str:
        name = {"song": "查曲", "chart": "查谱面", "card": "查卡"}[self.intent]
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
    chart: tuple[Song, tuple[Chart, ...]] | None = None


def resolve_query(spec: QuerySpec, repository: SongRepository) -> QueryResult:
    """Capture records for both answer text and image within one request."""
    if spec.intent == "song":
        return QueryResult(spec, songs=tuple(songs_for(spec, repository)))
    if spec.intent == "card":
        return QueryResult(spec, cards=tuple(cards_for(spec, repository)))
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


def cards_for(spec: QuerySpec, repository: SongRepository) -> list[Card]:
    subject = spec.subject
    if subject is None:
        return []
    if subject.kind == "card":
        return [card for card in repository.cards if card.id == subject.value]
    if subject.kind == "character":
        matches = [card for card in repository.cards
                   if normalize(character_identity(card.character)) == normalize(character_identity(str(subject.value)))]
    elif subject.kind == "band":
        matches = [card for card in repository.cards
                   if normalize(card.band) == normalize(str(subject.value))]
    else:
        return []
    return sorted(matches, key=lambda card: (-card.rarity, card.id))


def answer_for(spec: QuerySpec, repository: SongRepository, locale: str = "zh",
               result: QueryResult | None = None) -> str:
    selected = result if result is not None else resolve_query(spec, repository)
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
        matches = selected.cards
        if not matches:
            return tr(locale, "not_found_card", query=spec.display_name)
        if spec.subject and spec.subject.kind == "card":
            card = matches[0]
            return tr(locale, "cards") + "\n" + (
                f"{card.id}  {'★' * card.rarity} {localized_text(card, 'character', locale)} · "
                f"{localized_text(card, 'title', locale)}"
            )
        visible = page_slice(matches, spec.page)
        if not visible:
            return page_notice("cards", spec.query_label(), spec.page, len(matches), locale)
        return tr(locale, "cards") + "\n" + "\n".join(
            f"{card.id}  {'★' * card.rarity} {localized_text(card, 'character', locale)} · "
            f"{localized_text(card, 'title', locale)}" for card in visible
        ) + "\n" + page_notice("cards", spec.query_label(), spec.page, len(matches), locale) + "\n" + tr(locale, "next_card")

    raise ValueError("unsupported query intent")
