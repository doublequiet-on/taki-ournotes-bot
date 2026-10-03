# L3
# Input: direct/NL query text and the server-scoped EventCutoffRepository.
# Output: CutoffRequest / event-scoped CutoffAnswer and period labels, shared by text and images.
# Pos: Query / Deterministic challenge-song cutoffs; see L2-2.md.
# Effects: alias-file reads and bounded Data calls; no model, quota or QQ dependency.
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from ..sources.moenotes_events import (BoardSnapshot, EventSnapshot, EventSong, SERVERS, SourceError,
                                      display_time, zone_label)
from .entity_lexicon import scoped_song_matches
from ..sources.cutoff_history import HistoryView

NODES = (1, 2, 3, 10, 100)
HELP = "/查榜线 [jp/hk/kr/en] [歌名] [T1 T10 T37] [仅数值]；最多五个不同排名（1～100），多个排名须各带 T；省略名次显示 T1/2/3/10/100；例如 /查榜线 100。"
SERVER_ALIASES = {"jp": "jp", "日服": "jp", "hk": "tw", "国服": "tw", "國服": "tw",
                  "tw": "tw", "台服": "tw", "臺服": "tw",
                  "kr": "kr", "韩服": "kr", "韓服": "kr", "en": "en", "英服": "en", "国际服": "en", "國際服": "en"}
FORBIDDEN = re.compile(r"预测|預測|推荐|推薦|配队|配隊|编成|攻略|代练|代肝|账号|帐号|賬號|最强|怎么打|如何打|抽卡建议")
MARKER = re.compile(r"榜线|榜線|档线|檔線|歌曲榜|(?:前\s*[+-]?\d+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?\s*线).*?(?:多少|分)|T\d+.*多少", re.I)
_RANK = re.compile(r"(?:(?:[tT])?)([+-]?\d+(?:\.\d+)?)$")


@dataclass(frozen=True, init=False)
class CutoffRequest:
    server: str = "jp"
    query: str = ""
    ranks: tuple[int, ...] = ()
    error: str = ""
    code: str = "invalid_arguments"
    server_hint: str = ""  # Ambiguous two-letter prefix; resolve the whole entity before rejecting it.
    numeric_only: bool = False

    def __init__(self, server="jp", query="", rank=None, error="", code="invalid_arguments",
                 server_hint="", *, ranks=(), numeric_only=False):
        values = tuple(ranks) + ((rank,) if rank is not None else ())
        if any(type(r) is not int or not 1 <= r <= 100 for r in values):
            error, values = "名次必须为 1～100 的整数。", ()
        elif len(set(values)) > 5:
            error, values = "一次最多指定五个不同排名。", ()
        for name, value in (("server", server), ("query", query), ("ranks", tuple(sorted(set(values)))),
                            ("error", error), ("code", code), ("server_hint", server_hint), ("numeric_only", numeric_only)):
            object.__setattr__(self, name, value)

    @property
    def rank(self):
        """Compatibility accessor; canonical internal state is always a rank tuple."""
        return self.ranks[0] if len(self.ranks) == 1 else None


def parse_cutoff(text: str) -> CutoffRequest:
    text = unicodedata.normalize("NFKC", text).strip().lstrip("/")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^(?:查)?榜线(?:\s|$)", "", text).strip()
    first, _, rest = text.partition(" ")
    server = SERVER_ALIASES.get(first.casefold())
    if server:
        return CutoffRequest(server, rest.strip())
    if re.match(r"^(?:\S{1,4}服(?:\s|$)|server=|服务器[=:])", text, re.I):
        return CutoffRequest(error="未知服务器。仅支持日服（jp）、国服（hk）、韩服（kr）、英服（en）。")
    hint = first.casefold() if re.fullmatch(r"[a-z]{2}", first, re.I) else ""
    return CutoffRequest(query=text, server_hint=hint)


def parse_natural_cutoff(question: str) -> CutoffRequest | None:
    text = unicodedata.normalize("NFKC", question).strip().lstrip("/")
    if not MARKER.search(text):
        return None
    if FORBIDDEN.search(text):
        return CutoffRequest(error="只查询已采集的活动歌曲分数；不支持预测、配队、攻略或账号操作。", code="unsupported")
    if re.match(r"^(?:查)?榜线(?:\s|$)", text):
        return parse_cutoff(text)
    server = "jp"
    explicit_server = False
    for alias in sorted(SERVER_ALIASES, key=len, reverse=True):
        if text.casefold().startswith(alias) and (not alias.isascii() or len(text) == len(alias) or text[len(alias)].isspace()):
            server, text = SERVER_ALIASES[alias], text[len(alias):].strip()
            explicit_server = True
            break
    # Keep unfamiliar explicit regions visible as an error; never fall back to JP.
    if re.match(r"^(?:\S{1,4}服|server=|服务器[=:])", text, re.I):
        return CutoffRequest(error="未知服务器。仅支持日服（jp）、国服（hk）、韩服（kr）、英服（en）。")
    first = text.split(None, 1)[0] if text else ""
    hint = first.casefold() if not explicit_server and re.fullmatch(r"[a-z]{2}", first, re.I) else ""
    overview = re.sub(r"[\s?？。的]", "", text)
    if re.fullmatch(r"(?:现在|当前|这期|本期)?(?:全部|所有)?(?:歌曲)?(?:榜线|档线|歌曲榜)(?:现在)?(?:多少|是多少|多少分)?", overview):
        return CutoffRequest(server)
    match = re.fullmatch(r"(.+?)(?:的)?(?:前\s*([+-]?\d+(?:\.\d+)?)|([+-]?\d+(?:\.\d+)?)\s*线|[tT](\d+))(?:现在)?(?:是)?多少(?:分)?[?？]?", text)
    if match:
        number = next(value for value in match.groups()[1:] if value is not None)
        return CutoffRequest(server, match[1].rstrip("的 ") + " T" + number, server_hint=hint)
    match = re.fullmatch(r"(.+?)(?:的)?(?:榜线|档线)(?:现在)?(?:是)?多少(?:分)?[?？]?", text)
    if match:
        return CutoffRequest(server, match[1].rstrip("的 "), server_hint=hint)
    return CutoffRequest(server, error="未能确认歌曲和名次条件。" + HELP)


def event_status(event: EventSnapshot) -> str:
    return {"feature": "未开始", "nowOn": "进行中", "aggregation": "结算中",
            "result": "已结束（结果公示）", "end": "已结束"}.get(event.status, "状态未知")


def song_period_lines(event: EventSnapshot, song: EventSong) -> tuple[str, ...]:
    if (song.effective_start_ms, song.effective_end_ms) == (event.start_ms, event.end_ms):
        return ()
    zone = zone_label(event.server)
    return (f"本曲榜单有效开始：{display_time(song.effective_start_ms, event.server)} {zone}",
            f"本曲榜单有效结束：{display_time(song.effective_end_ms, event.server)} {zone}")


@dataclass(frozen=True)
class CutoffAnswer:
    request: CutoffRequest
    event: EventSnapshot | None = None
    boards: tuple[BoardSnapshot, ...] = ()
    message: str = ""
    status: str = "success"
    histories: tuple[tuple[str, HistoryView], ...] = ()

    @property
    def ranks(self) -> tuple[int, ...]:
        return self.request.ranks or NODES

    def history_for(self, board):
        return next((view for key, view in self.histories if key == board.song.challenge_id), HistoryView())

    @property
    def text(self) -> str:
        if self.message:
            return self.message
        event = self.event
        if event is None:
            return "活动暂不可用。"
        lines = [f"{SERVERS[event.server][0]} · {event.title} · {event_status(event)}", f"活动 {event.event_id} · {zone_label(event.server)}",
                 f"开始：{display_time(event.start_ms, event.server)}", f"结束：{display_time(event.end_ms, event.server)}"]
        for board in self.boards:
            lines.extend(["", board.song.title, *song_period_lines(event, board.song),
                          " / ".join(f"T{rank}：{board.score(rank) if board.score(rank) is not None else '暂无数据'}" for rank in self.ranks),
                          f"{board.status} · 源采集 {display_time(board.fetched_ms, event.server)}",
                          *board.notes])
            history = self.history_for(board)
            if history.warning:
                lines.append(history.warning)
            if history.points:
                lines.append("历史末次观测：" + display_time(history.points[-1].time_ms, event.server))
                if len(history.points) == 1:
                    lines.append("仅 1 个历史点，不足以形成曲线。")
        lines.extend(["", *event.notes, "来源：MoeNotes（非官方）· 活动挑战歌曲 Top 100",
                      "分数按来源响应位置；不代表预测或确认终榜。"])
        return "\n".join(lines)


def execute_cutoff(request: CutoffRequest, repository) -> CutoffAnswer:
    with repository.event_cutoffs.foreground():
        return _execute_cutoff(request, repository)


def _execute_cutoff(request: CutoffRequest, repository) -> CutoffAnswer:
    if request.error:
        return CutoffAnswer(request, message=request.error, status=request.code)
    if request.server not in SERVERS:
        return CutoffAnswer(request, message="未知服务器。" + HELP, status="invalid_arguments")
    source = repository.event_cutoffs
    deadline = source.deadline()
    try:
        event = source.event(request.server, deadline)
    except SourceError as exc:
        message = "该服务器当前没有可用活动。" if exc.code == "not_found" else "该服务器的活动信息暂不可用，请稍后重试。"
        return CutoffAnswer(request, message=SERVERS[request.server][0] + "：" + message, status="data_unavailable")
    if not event.songs:
        return CutoffAnswer(request, event, message="本活动没有挑战歌曲榜。", status="empty")
    query, ranks = request.query.strip(), request.ranks
    full_catalog = event.catalog or tuple((s.music_id, s.names + (s.title,)) for s in event.songs)
    event_ids = {s.music_id for s in event.songs}
    catalog = tuple((key, names) for key, names in full_catalog if key in event_ids)
    matches = scoped_song_matches(query, catalog) if query else ()
    outside = scoped_song_matches(query, full_catalog) if query and not matches else ()
    if not matches and not outside and re.search(r"(?:^|\s)仅数值$", query):
        query = re.sub(r"(?:^|\s)仅数值$", "", query).strip()
        request = replace(request, numeric_only=True)
        matches = scoped_song_matches(query, catalog) if query else ()
        outside = scoped_song_matches(query, full_catalog) if query and not matches else ()
    if query and not matches and not outside and not ranks:
        tokens = list(re.finditer(r"[^\s,，]+", query))
        suffix = []
        boundary = len(query)
        for token in reversed(tokens):
            if not _RANK.fullmatch(token[0]):
                break
            suffix.insert(0, token[0])
            boundary = token.start()
            head = query[:boundary].rstrip(" ,，")
            if head and scoped_song_matches(head, full_catalog):
                break
        if suffix:
            if len(suffix) > 1 and any(not v.lower().startswith("t") for v in suffix):
                return CutoffAnswer(request, event, message="多个排名必须各自带 T，例如 T1 T10 T37。", status="invalid_arguments")
            raw = [v.lstrip("tT") for v in suffix]
            if any(not re.fullmatch(r"\d+", value) or not 1 <= int(value) <= 100 for value in raw):
                return CutoffAnswer(request, event, message="名次必须为 1～100 的整数。", status="invalid_arguments")
            ranks, query = tuple(sorted({int(v) for v in raw})), query[:boundary].rstrip(" ,，")
            if len(ranks) > 5:
                return CutoffAnswer(request, event, message="一次最多指定五个不同排名。", status="invalid_arguments")
            matches = scoped_song_matches(query, catalog) if query else ()
            outside = scoped_song_matches(query, full_catalog) if query and not matches else ()
    request = replace(request, query=query, ranks=ranks)
    songs = event.songs
    if query:
        if not matches and not outside:
            matches = scoped_song_matches(query, catalog, partial=True)
        if len(matches) > 1:
            names = {key: titles[0] if titles else key for key, titles in catalog}
            return CutoffAnswer(request, event, message="歌曲名称有歧义，请补充完整名称；名次可写 T37：\n" + "\n".join(names[key] for key in matches[:5]), status="ambiguous")
        if not matches:
            if outside:
                return CutoffAnswer(request, event, message="这首歌不是本期活动的挑战歌曲。", status="empty")
            if request.server_hint:
                return CutoffAnswer(request, event, message="未知服务器。仅支持日服（jp）、国服（hk）、韩服（kr）、英服（en）。", status="invalid_arguments")
            if not event.catalog:
                return CutoffAnswer(request, event, message="该服务器歌曲元数据暂不可用，无法核对名称；请稍后重试或使用已知曲目 ID。", status="data_unavailable")
            return CutoffAnswer(request, event, message=f"该服务器未找到歌曲「{query}」。请使用完整歌名或已配置别名。", status="unknown_entity")
        songs = tuple(s for s in songs if s.music_id == matches[0])
        if not songs:
            return CutoffAnswer(request, event, message="这首歌不是本期活动的挑战歌曲。", status="empty")
    boards = source.boards(event, songs, deadline)
    histories = ()
    if not request.numeric_only:
        histories = tuple((b.song.challenge_id, source.history.read(event, b.song, ranks or NODES) if source.history
                           else HistoryView(warning="历史记录未配置；当前值仍可查询。")) for b in boards)
    return CutoffAnswer(request, event, boards, status="success" if any(b.scores for b in boards) else "data_unavailable", histories=histories)
