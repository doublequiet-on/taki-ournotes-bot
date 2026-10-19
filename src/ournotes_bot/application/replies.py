# L3: Shared captured reply preparation, pagination and bounded workers.
# Input: Plain commands, repository/parser and backend-owned contexts.
# Output: PreparedReply/DeliveryCursor; no SDK, upload or send dependency.
# Effects: Existing business/source reads and rendering; card-947 remains a packaged static asset.
from __future__ import annotations
import asyncio
import logging
import re
import time
from functools import partial
from dataclasses import dataclass, replace
from importlib.resources import files
from ..ai_query import AIQueryParser, is_ai_request
from ..sources.chart_data import ChartDataError, load_chart_data, load_chart_score
from ..commands import (CommandResult, handle_command, locale_for, page_notice,
                       page_slice, resolve_command, split_commands)
from ..config import QQ_PASSIVE_REPLY_LIMIT, REPLY_ORDER_TIMEOUT_SECONDS
from ..data import SongRepository
from ..structured_query import QueryResult, query_page_notice
from ..query.continuation import (QueryContext, catalog_version,
                                  capture_context, execute_followup, parse_operation)
from ..visuals import (render_card, render_card_list, render_chart, render_song_list,
                      render_support_card, render_support_card_list)



logger = logging.getLogger("ournotes_bot.replies")


def describe(content: str) -> str:
    """Text for the log. Direct commands are shown; /问 bodies stay out of it."""
    cleaned = (content or "").strip()
    if is_ai_request(cleaned):
        return f"/问（{len(cleaned)} 字，正文不记录）"
    return cleaned[:60]


def chart_image(song, charts, locale: str, *, source="moenotes") -> bytes:
    preview = next((chart for chart in charts if chart.difficulty == "EXPERT"), charts[-1] if charts else None)
    score = None
    notice = ""
    if preview:
        try:
            if source == "moenotes":
                score = load_chart_score(song, preview)
            else:
                result = load_chart_data(song, preview, source=source)
                score, notice = result.score, result.notice(locale)
        except ChartDataError:
            logger.warning("音符谱面暂不可用：歌曲 %s，难度 %s", song.id, preview.difficulty)
    if notice:
        return render_chart(song, charts, locale, score, preview.difficulty if preview else None,
                            source_notice=notice)
    return render_chart(song, charts, locale, score, preview.difficulty if preview else None)


@dataclass(frozen=True)
class PreparedReply:
    text: str
    image: bytes | None = None
    pages: tuple[PreparedReply, ...] = ()
    complete_text: bool = False
    context: QueryContext | DeliveryCursor | None = None
    budget_label: str = "查榜线"
    fallback_texts: tuple[str, ...] = ()
    business_status: str | None = None

    @property
    def passive_slots(self):
        return max(1, len(self.fallback_texts))


@dataclass(frozen=True)
class DeliveryCursor:
    query_context: QueryContext | None
    remaining: tuple[PreparedReply, ...]
    sent: int
    total: int
    uncertain: bool = False

    @property
    def retained_bytes(self):
        return sum(len(p.image or b'') + len(p.text.encode('utf-8')) for p in self.remaining)


def fallback_cursor(reply):
    previous=reply.context if isinstance(reply.context,DeliveryCursor) else None
    query=previous.query_context if previous else reply.context
    chunks=text_chunks(reply.text)
    pages=tuple(PreparedReply(t,context=query if i==len(chunks)-1 else None) for i,t in enumerate(chunks))
    pages+=previous.remaining if previous else ()
    return DeliveryCursor(None,pages,0,len(pages))


def text_chunks(text,limit=1700):
    return tuple(text[i:i+limit] for i in range(0,len(text),limit)) or ('',)


def expand_replies(replies: list[PreparedReply], reserve: int = 0) -> list[PreparedReply]:
    """Budget a single ordered captured batch, including multi-command overflow."""
    pages=[]
    base=replies[0].context if len(replies)==1 and isinstance(replies[0].context,DeliveryCursor) else None
    for reply in replies:
        for part in reply.pages or (reply,):
            if part.business_status is None and reply.business_status is not None:
                part = replace(part, business_status=reply.business_status)
            if part.fallback_texts:
                part=replace(part,text='\n'.join(part.fallback_texts),fallback_texts=())
            if part.image is None and len(part.text)>1800:
                chunks=text_chunks(part.text)
                pages.extend(PreparedReply(t,context=part.context if i==len(chunks)-1 else None,
                                           business_status=part.business_status) for i,t in enumerate(chunks))
            else:
                pages.append(part)
    available=max(1,QQ_PASSIVE_REPLY_LIMIT-reserve)
    prior=base.sent if base else 0
    total=base.total if base else len(pages)
    keep=bool(base or len(pages)>available or any(r.pages for r in replies))
    expanded=[]
    for offset,page in enumerate(pages[:available]):
        if keep:
            query=(page.context if isinstance(page.context,QueryContext) else base.query_context if base else None) if len(replies)==1 else None
            cursor=DeliveryCursor(query,tuple(pages[offset+1:]),prior+offset+1,total)
            suffix=f'\n结果 {cursor.sent}/{total}'+('；/继续 发送剩余结果。' if cursor.remaining else '')
            page=replace(page,text=page.text+suffix,context=cursor)
        expanded.append(page)
    return expanded


class QueryGate:
    """Bound in-flight preparation, including callers waiting for a worker."""

    def __init__(self, concurrency: int, queue_limit: int, *, redact: bool = False) -> None:
        if concurrency < 1 or queue_limit < 0:
            raise ValueError("Invalid query gate limits")
        self._workers = asyncio.Semaphore(concurrency)
        self._capacity = concurrency + queue_limit
        self._pending = 0
        self._lock = asyncio.Lock()
        self.redact = redact

    async def prepare(self, content: str, repository: SongRepository,
                      ai_parser: AIQueryParser) -> PreparedReply | None:
        function = partial(prepare_reply, log_content=False) if self.redact else prepare_reply
        return await self._run(function, content, repository, ai_parser)

    async def prepare_followup(self, context, operation, repository):
        return await self._run(prepare_followup, context, operation, repository)

    async def _run(self, function, *args):
        async with self._lock:
            if self._pending >= self._capacity:
                return PreparedReply("当前查询较多，请稍后重试。")
            self._pending += 1
        try:
            async with self._workers:
                work = asyncio.create_task(asyncio.to_thread(function, *args))
                try:
                    return await asyncio.shield(work)
                except asyncio.CancelledError:
                    # to_thread cannot stop a running worker; keep its slot occupied.
                    try:
                        await work
                    finally:
                        raise
        finally:
            async with self._lock:
                self._pending -= 1


class ReplySequencer:
    """Let replies leave in arrival order even though they finish out of order.

    QueryGate leaves the gate the moment a worker frees up, so a slow query
    started first can be overtaken by a faster query that arrived later. In a
    chat that reads as a mix-up: the answer to question two shows up before the
    answer to question one.

    Each inbound message takes a ticket when it arrives and waits for its turn
    before sending. Preparation is untouched, so throughput is unchanged; only
    the sending order is serialized.

    Ordering costs tail latency: a fast query that arrives after a slow one has
    to wait. Set `enabled=False` to trade order back for latency — a ticket is
    then None and every method is a no-op, so callers need no branching.

    Tickets are always released by the caller, and the wait is bounded, so a
    wedged batch degrades to unordered behaviour instead of blocking every
    later reply forever.
    """

    def __init__(self, timeout: float = REPLY_ORDER_TIMEOUT_SECONDS, enabled: bool = True) -> None:
        self._timeout = timeout
        self._enabled = enabled
        self._issued = 0
        self._serving = 0
        self._condition = asyncio.Condition()

    async def issue(self) -> int | None:
        """Take a ticket, or None when ordering is switched off."""
        if not self._enabled:
            return None
        async with self._condition:
            ticket = self._issued
            self._issued += 1
            return ticket

    async def wait_turn(self, ticket: int | None) -> None:
        if ticket is None:
            return
        async with self._condition:
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._serving >= ticket),
                    timeout=self._timeout,
                )
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning("等待发送顺序超时 %ss；本次提前发送，避免后续回复被阻塞",
                               f"{self._timeout:g}")

    async def release(self, ticket: int | None) -> None:
        if ticket is None:
            return
        async with self._condition:
            self._serving = max(self._serving, ticket + 1)
            self._condition.notify_all()


async def prepare_commands(commands: list[str], gate: "QueryGate", repository: SongRepository,
                           ai_parser: AIQueryParser, limit: int) -> tuple[list["PreparedReply"], int]:
    """Prepare several commands concurrently and return replies in asked order.

    Preparation is bounded by `gate`, so a burst queues instead of swamping the
    machine, while `gather` keeps the results aligned with `commands` — the
    delivery order therefore follows what the user asked for rather than which
    query happened to finish first. One failing command cannot drop the others.

    Returns (replies, overflow) where overflow counts commands beyond `limit`.
    """
    selected = commands[:limit]
    async def prepare_one(command):
        if len(commands) > 1 and parse_operation(command) is not None:
            return PreparedReply("连续查询请单独发送；同一条消息中的新查询和续查不会按依赖链执行。")
        return await gate.prepare(command, repository, ai_parser)
    results = await asyncio.gather(
        *(prepare_one(command) for command in selected),
        return_exceptions=True,
    )
    replies: list[PreparedReply] = []
    for result in results:
        if isinstance(result, BaseException):
            logger.error("查询结果准备失败；错误类型=%s", type(result).__name__)
            continue
        if result:
            replies.append(result)
    overflow = max(0, len(commands) - limit)
    return expand_replies(replies, reserve=int(bool(overflow))), overflow


def image_from_result(result: QueryResult | CommandResult | None,
                       repository: SongRepository, locale: str) -> bytes | None:
    if isinstance(result, QueryResult) and result.spec.field:
        return None
    if result is not None and result.catalog is not None:
        answer = result.catalog
        if answer.error or not answer.cards:
            return None
        req = answer.request
        if req.mode == "art":
            from ..rendering.card_visuals import art
            return art(answer.cards[0])
        if req.mode == "detail":
            render = render_support_card if req.support else render_card
            return render(answer.cards[0], locale)
        render = render_support_card_list if req.support else render_card_list
        return render(answer.visible, req.query, locale, answer.footer) if answer.visible else None
    if result is not None and result.song_selection is not None:
        answer = result.song_selection
        visible = page_slice(answer.songs, answer.page)
        return render_song_list(visible, answer.request.query, locale, answer.footer) if visible else None
    if isinstance(result, QueryResult):
        spec = result.spec
        if spec.intent == "song":
            songs = result.songs
            visible = page_slice(songs, spec.page)
            return render_song_list(visible, spec.query_label(), locale,
                                    page_notice("songs", spec.query_label(), spec.page, len(songs), locale)) if visible else None
        if spec.intent == "chart":
            chart = result.chart
            return chart_image(chart[0], chart[1], locale, source=repository.chart_source) if chart else None
        if spec.intent == "card":
            if (spec.skill_query or spec.skill_kind) and not repository.member_skill_index_ready():
                return None
            cards = result.cards
            if not cards:
                return None
            if spec.subject and spec.subject.kind == "card":
                return render_card(repository.card_with_detail(cards[0]), locale)
            visible = page_slice(cards, spec.page)
            return render_card_list(visible, spec.query_label(), locale,
                                    query_page_notice(spec, len(cards), locale)) if visible else None
        if spec.intent == "support_card":
            cards = result.support_cards
            if not cards:
                return None
            if spec.subject and spec.subject.kind == "support_card":
                return render_support_card(repository.support_card_with_detail(cards[0]), locale)
            visible = page_slice(cards, spec.page)
            return render_support_card_list(
                visible, spec.query_label(), locale,
                query_page_notice(spec, len(cards), locale),
            ) if visible else None
        return None
    if result is None:
        return None
    kind, query, difficulty = result.parsed
    if kind == "songs":
        songs = result.songs
        visible = page_slice(songs, int(difficulty))
        return render_song_list(visible, query, locale, page_notice(kind, query, int(difficulty), len(songs), locale)) if visible else None
    if kind == "chart":
        songs = result.songs
        if not songs:
            return None
        charts = tuple(chart for chart in songs[0].charts if difficulty is None or chart.difficulty == difficulty)
        return chart_image(songs[0], charts, locale, source=repository.chart_source)
    if kind == "cards":
        cards = result.cards
        if not cards:
            return None
        if query.isdigit() and cards[0].id == int(query):
            return render_card(repository.card_with_detail(cards[0]), locale)
        visible = page_slice(cards, int(difficulty))
        return render_card_list(visible, query, locale, page_notice(kind, query, int(difficulty), len(cards), locale)) if visible else None
    if kind == "support_cards":
        cards = result.support_cards
        if not cards:
            return None
        if query.isdigit() and cards[0].id == int(query):
            return render_support_card(repository.support_card_with_detail(cards[0]), locale)
        visible = page_slice(cards, int(difficulty))
        return render_support_card_list(
            visible, query, locale, page_notice(kind, query, int(difficulty), len(cards), locale)
        ) if visible else None
    return None


def easter_egg_reply(content: str) -> PreparedReply | None:
    """Route only the complete card-947 command, independently of game queries."""
    commands = split_commands(content)
    if len(commands) != 1 or re.fullmatch(r"查卡\s+947", commands[0]) is None:
        return None
    try:
        image = files("ournotes_bot").joinpath("platforms/qq/assets/card-947.jpg").read_bytes()
    except OSError as exc:
        logger.warning("彩蛋图片读取失败；错误类型=%s", type(exc).__name__)
        return PreparedReply("彩蛋图片暂时不可用，请稍后重试。")
    return PreparedReply("彩蛋图片发送失败，请稍后重试。", image)


def image_reply(content: str, repository: SongRepository, ai_parser: AIQueryParser | None = None) -> bytes | None:
    """Compatibility helper for local previews; live replies use prepare_reply."""
    easter_egg = easter_egg_reply(content)
    if easter_egg is not None:
        return easter_egg.image
    if ai_parser and is_ai_request(content):
        _, result = ai_parser.answer_with_plan(content, repository)
    else:
        result = resolve_command(content, repository)
    return image_from_result(result, repository, locale_for(content))


def prepare_reply(content: str, repository: SongRepository,
                   ai_parser: AIQueryParser, *, log_content: bool = True) -> PreparedReply | None:
    easter_egg = easter_egg_reply(content)
    if easter_egg is not None:
        return easter_egg
    started = time.monotonic()

    def elapsed() -> float:
        return (time.monotonic() - started) * 1000

    try:
        before = catalog_version(repository)
        if is_ai_request(content):
            reply, result = ai_parser.answer_with_plan(content, repository)
        else:
            result = resolve_command(content, repository)
            reply = handle_command(content, repository, resolved=result)
    except Exception as exc:
        logger.error("查询结果生成失败；错误类型=%s 耗时=%.0fms", type(exc).__name__, elapsed())
        return PreparedReply("查询暂时失败，请稍后重试。")
    if not reply:
        logger.info("未识别为查询，不回复；内容=%s", describe(content) if log_content else '[redacted]')
        return None
    candidate = capture_context(result, repository, catalog_before=before, complete=True)
    prepared = render_prepared(reply, result, repository, locale_for(content), candidate)
    prepared = replace(prepared, business_status=getattr(result, 'status', None))
    logger.info("查询完成；内容=%s 文本=%d字 图片=%s 耗时=%.0fms",
                describe(content) if log_content else '[redacted]', len(reply),
                f"{len(prepared.image):,}B" if prepared.image else "无", elapsed())
    return prepared


def prepare_followup(context, operation, repository):
    from ..structured_query import answer_for
    if isinstance(context,DeliveryCursor):
        if context.uncertain:
            return PreparedReply('上一张图片的发送回执不确定，暂不自动重发，请重新查询。')
        if operation.kind == 'continue':
            return (PreparedReply('',pages=context.remaining,context=context) if context.remaining
                    else PreparedReply('本次查询的图片已经全部发送。',context=context.query_context))
        context=context.query_context
        if context is None:
            return PreparedReply('此结果支持 /继续；选择卡牌或歌曲请先查询对应列表。')
    if operation.kind == 'continue':
        return PreparedReply('本次查询的图片已经全部发送。',context=context)
    try:
        outcome = execute_followup(context, operation, repository)
        if isinstance(outcome, str):
            return PreparedReply(outcome)
        result, candidate = outcome
        if isinstance(result, QueryResult):
            text = answer_for(result.spec, repository, result=result)
        else:
            text = handle_command("/查曲", repository, resolved=result)
        return render_prepared(text, result, repository, "zh", candidate)
    except Exception as exc:
        logger.error("续查准备失败；错误类型=%s", type(exc).__name__)
        return PreparedReply("查询暂时失败，请稍后重试。")


def visual_asset(url,size):
    from ..visuals import load_visual_asset
    return load_visual_asset(url,size)


def complete_list_reply(result,repository,locale,candidate):
    if result is None or result.status != 'success':
        return None
    if isinstance(result,QueryResult) and result.spec.field:
        return None
    cards=();songs=();label=''
    if result.catalog is not None:
        answer=result.catalog
        if answer.request.mode != 'list':return None
        cards=answer.cards;label=answer.request.query
    elif result.song_selection is not None:
        songs=result.song_selection.songs;label=result.song_selection.request.query
    elif isinstance(result,QueryResult):
        if result.spec.intent == 'chart' or (result.spec.subject and result.spec.subject.kind in {'song','card','support_card'}):return None
        cards=result.cards or result.support_cards;songs=result.songs;label=result.spec.query_label()
    elif result.parsed[0] in {'songs','cards','support_cards'}:
        kind,label,_=result.parsed
        if kind!='songs' and label.isdigit():return None
        cards=result.cards or result.support_cards;songs=result.songs
    else:return None
    page=(result.catalog.request.page if result.catalog else result.song_selection.page if result.song_selection else
          result.spec.page if isinstance(result,QueryResult) else int(result.parsed[2] or 1))
    if page<1 or page>max(1,(len(cards or songs)+15)//16):return None
    if cards:
        from ..rendering.catalog_layout import render_list
        characters=result.catalog.request.filters.get('character',()) if result.catalog else ()
        bands=result.catalog.request.filters.get('band',()) if result.catalog else ()
        parts,records,ranges=render_list(cards,label,locale,load=visual_asset,characters=characters,bands=bands)
    elif songs:
        from ..rendering.song_layout import render_list
        parts,ranges=render_list(songs,label,locale,load=visual_asset);records=tuple(songs)
    else:return None
    if candidate is not None and not candidate.complete:
        candidate=capture_context(result,repository,catalog_before=candidate.version[0],complete=True)
    if candidate is not None:
        # Preserve the original difficulty/selection metadata while aligning IDs.
        by_id={v.entity_id:v for v in candidate.visible}
        candidate=replace(candidate,visible=tuple(by_id[r.id] for r in records))
    pages=[]
    from ..data import localized_text
    for part,(start,stop) in zip(parts,ranges):
        text='\n'.join(f'{i+1}. ID {r.id} '+localized_text(r,'title',locale)+' · '+
                       (localized_text(r,'character',locale)+'\n'+'\n'.join(localized_text(skill,'description',locale) for skill in r.skills)
                        if hasattr(r,'skills') else localized_text(r,'band',locale)+' · '+' / '.join(f'{ch.difficulty} {ch.display_level:g} ({ch.notes if ch.notes is not None else "?"} Note)' for ch in r.charts))
                       for i,r in enumerate(records[start:stop],start))
        total=len({r.id for r in records})
        text+=f'\n共 {total} 条，本图 {start+1}–{stop}。'
        if len(records)>total:
            text+='\n多人留影按相关角色分别展示，同一卡牌不重复计入总数；本图区间为展示位置。'
        if cards:
            from ..query.card_catalog import catalog_notice
            text += catalog_notice(records)
        ctx=replace(candidate,visible=candidate.visible[:stop]) if candidate else None
        pages.append(PreparedReply(text,part.image,context=ctx))
    return pages[0] if len(pages)==1 else PreparedReply('',pages=tuple(pages),context=candidate)


def detail_card(result, repository):
    if result is None or result.status!='success':return None
    card=None
    if result.catalog is not None:
        return result.catalog.cards[0] if result.catalog.request.mode=='detail' else None
    elif isinstance(result,QueryResult):
        if result.spec.field or not result.spec.subject:return None
        if result.spec.intent=='card' and result.cards:card=repository.card_with_detail(result.cards[0])
        if result.spec.intent=='support_card' and result.support_cards:card=repository.support_card_with_detail(result.support_cards[0])
    elif result.parsed[0] in {'cards','support_cards'} and result.parsed[1].isdigit():
        if result.cards:card=repository.card_with_detail(result.cards[0])
        elif result.support_cards:card=repository.support_card_with_detail(result.support_cards[0])
    return card


def complete_detail_reply(result,repository,locale,candidate):
    card = detail_card(result, repository)
    if card is None:return None
    from ..query.card_catalog import detail_text
    from ..rendering.card_detail_layout import render
    text = detail_text(card, locale)
    parts = render(card, locale, load=visual_asset)
    pages = tuple(PreparedReply(text, p.image, context=candidate) for p in parts)
    return pages[0] if len(pages) == 1 else PreparedReply(text, pages=pages, context=candidate)


def complete_chart_reply(result,repository,locale,candidate):
    if result is None:return None
    if isinstance(result,QueryResult):
        if result.spec.field or result.spec.intent!='chart' or result.chart is None:return None
        song,charts=result.chart
    else:
        if result.parsed[0]!='chart' or not result.songs:return None
        song=result.songs[0];difficulty=result.parsed[2]
        charts=tuple(c for c in song.charts if difficulty is None or c.difficulty==difficulty)
    from ..application.reply_preparation import prepare_chart_reply
    from ..rendering.chart_layout import render
    prepared=prepare_chart_reply(song,charts,repository.chart_source,locale,
                                 load_score=load_chart_score,load_data=load_chart_data)
    pages=render(prepared.song,prepared.charts,prepared.score,prepared.preview,prepared.locale,
                 load=visual_asset,notice=prepared.notice)
    text=prepared.text
    replies=tuple(PreparedReply(text,image,context=candidate) for image in pages)
    return replies[0] if len(replies)==1 else PreparedReply('',pages=replies,context=candidate)


def song_detail_reply(result,repository,locale,candidate):
    if result is None or len(result.songs)!=1 or result.catalog or result.song_selection:return None
    from ..data import normalize
    song=result.songs[0]
    if isinstance(result,QueryResult):
        if result.spec.intent!='song' or result.spec.field or not result.spec.subject or result.spec.subject.kind!='song':return None
    elif result.parsed[0]!='songs' or (result.parsed[1]!=str(song.id) and normalize(result.parsed[1]) not in {normalize(t) for t in song.titles+(song.title,)}):return None
    from ..application.reply_preparation import prepare_song_reply
    from ..rendering.song_detail_layout import render
    prepared=prepare_song_reply(song,repository.chart_source,locale,
                                load_score=load_chart_score,load_data=load_chart_data)
    return PreparedReply(prepared.text,render(prepared.song,prepared.score,prepared.locale,
                         load=visual_asset,band_logos=prepared.band_logos),context=candidate)



def render_prepared(reply, result, repository, locale, candidate=None):
    from ..rendering.haneoka_theme import game_assets
    from ..sources.haneoka.game_assets import asset_bytes
    from ..config import runtime_data_dir
    with game_assets(lambda name: asset_bytes(name, runtime_data_dir() / 'game-assets')):
        return render_prepared_inner(reply, result, repository, locale, candidate)


def render_prepared_inner(reply, result, repository, locale, candidate=None):
    try:
        if isinstance(result, CommandResult) and (result.info is not None or result.activity is not None):
            from ..rendering.info_layout import render_info
            answer = result.info if result.info is not None else result.activity
            pages = tuple(PreparedReply(page.text, page.image, complete_text=True)
                          for page in render_info(answer, load=visual_asset))
            if pages:
                return pages[0] if len(pages) == 1 else PreparedReply(reply, pages=pages, complete_text=True)
            return PreparedReply(answer.text, complete_text=True)
        full = None if result is not None and result.cutoff is not None else (song_detail_reply(result, repository, locale, candidate) or
                complete_list_reply(result, repository, locale, candidate) or
                complete_detail_reply(result, repository, locale, candidate) or
                complete_chart_reply(result, repository, locale, candidate))
        if full is not None:
            return full
        if result is not None and result.cutoff is not None:
            from ..rendering.event_cutoff_visuals import load_artwork
            from ..rendering.ranking_layout import render
            from ..sources.haneoka.ranking_art import cards_for, event_marks, bands_for
            answer=result.cutoff
            source=getattr(repository,'_catalog_repository',None)
            saved=source.saved if source else None
            from ..data import Card, SupportCard
            from ..sources.moenotes_events import SERVERS, display_time, zone_label
            records=cards_for(saved,answer.event.server) if answer.event and not answer.request.numeric_only else ({},{})
            catalogs=tuple({key:cls(**value) for key,value in rows.items()} for cls,rows in zip((Card,SupportCard),records))
            assets=load_artwork(repository.event_cutoffs,answer.event,(answer.event.banner,*(b.song.jacket for b in answer.boards))) if answer.event and not answer.request.numeric_only else {}
            try:
                pages=render(answer,assets=assets,load=visual_asset,catalogs=catalogs,song_bands=bands_for(saved,answer.event.server) if answer.event else {},bonus_marks=event_marks(saved,answer.event) if answer.event else (),display_time=display_time,server_label=SERVERS[answer.event.server][0] if answer.event else "",timezone_label=zone_label(answer.event.server) if answer.event else "")
            finally:
                for asset in assets.values():
                    if asset is not None:asset.close()
            if len(pages) == 1:
                return PreparedReply(pages[0].text, pages[0].image, complete_text=True)
            if pages:
                return PreparedReply(reply, pages=tuple(PreparedReply(page.text, page.image) for page in pages))
            return PreparedReply(reply, complete_text=True)
        image = image_from_result(result, repository, locale)
    except Exception as exc:
        logger.warning("图片生成失败，改用文字；错误类型=%s", type(exc).__name__)
        image = None
        reply, candidate = complete_fallback(reply, result, locale, candidate, repository)
    if (image is None and result is not None and result.catalog is not None
            and result.catalog.request.mode == "art" and not result.catalog.error):
        reply += "\n卡面图片暂不可用，请稍后重试。"
    is_info = isinstance(result, CommandResult) and (result.info is not None or result.activity is not None)
    return PreparedReply(reply, image, complete_text=is_info or result is not None and result.cutoff is not None,
                         context=candidate)


def complete_fallback(reply, result, locale, candidate, repository):
    """A failed renderer must not restore the former first-page truncation."""
    if isinstance(result, CommandResult):
        answer = result.info if result.info is not None else result.activity
        if answer is not None:
            return answer.text, candidate
    if result is not None and result.cutoff is not None:
        return result.cutoff.text, candidate
    if result is None or result.status != 'success':
        return reply, candidate
    if isinstance(result, QueryResult) and result.spec.field:
        return reply, candidate
    if result.cutoff is not None:
        return result.cutoff.text, candidate
    card = detail_card(result, repository)
    if card is not None:
        from ..query.card_catalog import detail_text
        return detail_text(card, locale), candidate
    from ..data import localized_text
    cards = result.cards or result.support_cards
    songs = result.songs
    if result.catalog is not None:
        if result.catalog.request.mode == 'art':
            return reply, candidate
        cards = result.catalog.cards
    if result.song_selection is not None:
        songs = result.song_selection.songs
    if cards:
        from ..rendering.catalog_layout import ordered
        records = ordered(cards)
        rows = [f'{i}. ID {card.id} '+localized_text(card,'title',locale)+' · '+
                localized_text(card,'character',locale)+'\n'+'\n'.join(
                    localized_text(skill,'name',locale)+'：'+localized_text(skill,'description',locale)
                    for skill in card.skills) for i,card in enumerate(records,1)]
    elif songs:
        records = songs
        rows = [f'{i}. ID {song.id} '+localized_text(song,'title',locale)+' · '+
                localized_text(song,'band',locale)+'\n'+' / '.join(
                    f'{chart.difficulty} {chart.display_level:g} · {chart.notes if chart.notes is not None else "?"} Note'
                    for chart in song.charts) for i,song in enumerate(records,1)]
    else:
        return reply, candidate
    if candidate is not None:
        choices = {choice.entity_id:choice for choice in candidate.visible}
        candidate = replace(candidate,visible=tuple(choices[r.id] for r in records))
    from ..query.card_catalog import catalog_notice
    return '\n\n'.join(rows)+f'\n共 {len(records)} 条。'+(catalog_notice(records) if cards else ''), candidate


