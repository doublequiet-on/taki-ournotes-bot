# L3
# Input: direct/NL query text and the server-scoped EventCutoffRepository.
# Output: CutoffRequest/CutoffAnswer with stable song numbers and same-row score/digital-ID/username projections.
# Pos: Query / Deterministic challenge-song cutoffs; see L2-2.md.
# Effects: alias-file reads and bounded Data calls/history writes; history reads only for trends; no model/QQ.
from __future__ import annotations

import re
import json
import unicodedata
from dataclasses import dataclass, replace

from ..sources.moenotes_events import (BoardSnapshot, EventSnapshot, EventSong, SERVERS, SourceError,
                                      display_time, zone_label)
from .entity_lexicon import scoped_song_matches
from ..sources.cutoff_history import HistoryView

NODES = (1, 2, 3, 10, 100)
HELP = '/查榜线 [jp/hk/kr/en] [歌曲1或歌名] [50或20-40] [仅数值]；参数顺序不限。无排名看五档趋势，单排名看前后十名，区间看每行分数、数字ID与用户名；例如 /查榜线 100 hk 歌曲一。'
SERVER_ALIASES = {"jp": "jp", "日服": "jp", "hk": "tw", "国服": "tw", "國服": "tw",
                  "tw": "tw", "台服": "tw", "臺服": "tw",
                  "kr": "kr", "韩服": "kr", "韓服": "kr", "en": "en", "英服": "en", "国际服": "en", "國際服": "en"}
FORBIDDEN = re.compile(r"预测|預測|推荐|推薦|配队|配隊|编成|攻略|代练|代肝|账号|帐号|賬號|最强|怎么打|如何打|抽卡建议")
MARKER = re.compile(r"榜线|榜線|档线|檔線|歌曲榜|(?:前\s*[+-]?\d+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?\s*线).*?(?:多少|分)|T\d+.*多少", re.I)
_NUMBER = r'[tT]?[+-]?\d+(?:\.\d+)?'
_RANK_PART = re.compile(rf'(?<!\S)({_NUMBER})(?:\s*[-—–~～到]\s*({_NUMBER}))?(?!\S)')
_SONG_NUMBER = re.compile(r'(?:歌曲|歌)([\d零〇一二两三四五六七八九十百+-]+(?:\.\d+)?)$|第([\d零〇一二两三四五六七八九十百+-]+(?:\.\d+)?)首$')
_TOKEN = re.compile(r'(?:(歌名|歌曲ID)\s*=\s*("(?:\\.|[^"\\])*"|[^\s]+))|[^\s]+', re.I)
_UNKNOWN_CODES = {'cn', 'us', 'eu', 'gb', 'sg'}


@dataclass(frozen=True, init=False)
class CutoffRequest:
    server: str = "jp"
    query: str = ""
    ranks: tuple[int, ...] = ()
    error: str = ""
    code: str = "invalid_arguments"
    server_hint: str = ""  # Legacy constructor field; explicit unknown servers are now rejected locally.
    numeric_only: bool = False
    mode: str = "trend"
    target_rank: int | None = None
    range_bounds: tuple[int, ...] = ()
    song_number: int | None = None
    song_id: str = ""
    literal_query: bool = False
    extra_queries: tuple[str, ...] = ()

    def __init__(self, server="jp", query="", rank=None, error="", code="invalid_arguments",
                 server_hint="", *, ranks=(), numeric_only=False, mode=None, target_rank=None,
                 range_bounds=(), song_number=None, song_id="", literal_query=False, extra_queries=()):
        values = tuple(ranks) + ((rank,) if rank is not None else ())
        mode = mode or ("near" if rank is not None else "discrete" if values else "trend")
        if mode == "near":
            target_rank = target_rank if target_rank is not None else rank
            if type(target_rank) is not int or not 1 <= target_rank <= 100:
                error, values = error or "名次必须为 1～100 的整数。", ()
            else:
                values = tuple(range(max(1, target_rank - 10), min(100, target_rank + 10) + 1))
        elif mode == "range":
            if len(range_bounds) != 2 or any(type(r) is not int or not 1 <= r <= 100 for r in range_bounds):
                error, values = error or "区间两端必须为 1～100 的整数。", ()
            elif range_bounds[0] > range_bounds[1]:
                error, values = error or "排名区间不能倒序，请将较小排名写在前面。", ()
            else:
                values = tuple(range(range_bounds[0], range_bounds[1] + 1))
        elif mode == "discrete":
            if any(type(r) is not int or not 1 <= r <= 100 for r in values):
                error, values = error or "名次必须为 1～100 的整数。", ()
            elif len(set(values)) > 5:
                error, values = error or "离散排名一次最多指定五个不同排名。", ()
        elif mode != "trend":
            error, values = error or "无法识别排名模式。", ()
        if song_number is not None and (type(song_number) is not int or song_number < 1):
            error = error or "歌曲编号必须为正整数，例如 歌曲1。"
        for name, value in (("server", server), ("query", query), ("ranks", tuple(sorted(set(values)))),
                            ("error", error), ("code", code), ("server_hint", server_hint), ("numeric_only", numeric_only),
                            ("mode", mode), ("target_rank", target_rank), ("range_bounds", tuple(range_bounds)),
                            ("song_number", song_number), ("song_id", song_id), ("literal_query", literal_query),
                            ("extra_queries", tuple(extra_queries))):
            object.__setattr__(self, name, value)

    @property
    def rank(self):
        """Compatibility accessor; canonical internal state is always a rank tuple."""
        return self.target_rank if self.mode == "near" else self.ranks[0] if len(self.ranks) == 1 else None

    def command_label(self):
        parts = ['查榜线', 'hk' if self.server == 'tw' else self.server]
        if self.song_number is not None:
            parts.append(f'歌曲{self.song_number}')
        if self.song_id:
            parts.append(f'歌曲ID={self.song_id}')
        for name in ((self.query,) if self.query else ()) + self.extra_queries:
            parts.append('歌名=' + json.dumps(name, ensure_ascii=False))
        if self.mode == 'near':
            parts.append(str(self.target_rank))
        elif self.mode == 'range':
            parts.append(f'{self.range_bounds[0]}-{self.range_bounds[1]}')
        elif self.mode == 'discrete':
            # A one-point programmatic discrete request has an exact-range spelling.
            parts.append(' '.join(f'T{r}' for r in self.ranks) if len(self.ranks) > 1 else f'{self.ranks[0]}-{self.ranks[0]}')
        if self.numeric_only:
            parts.append('仅数值')
        return ' '.join(parts)


def _song_integer(raw):
    if re.fullmatch(r'\d+', raw):
        return int(raw)
    if not re.fullmatch(r'[零〇一二两三四五六七八九]|[一二两三四五六七八九]?十[一二三四五六七八九]?|[一二两三四五六七八九]百(?:[零〇]?[一二三四五六七八九]|[一二三四五六七八九]?十[一二三四五六七八九]?)?', raw):
        raise ValueError('invalid song number')
    digits = dict(zip('零〇一二两三四五六七八九', (0, 0, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
    total, digit = 0, 0
    for char in raw:
        if char in digits:
            digit = digits[char]
        elif char in '十百':
            total += (digit or 1) * (10 if char == '十' else 100)
            digit = 0
        else:
            raise ValueError('invalid song number')
    return total + digit


def _rank_selection(request, text):
    """Extract complete rank groups; never apply the discrete limit to a window."""
    normalized = re.sub(r'[,，]', ' ', text)
    matches = tuple(_RANK_PART.finditer(normalized))
    if not matches:
        return request, text.strip()
    remainder = _RANK_PART.sub(' ', normalized)
    remainder = re.sub(r'\s+', ' ', remainder).strip()
    ranges, points = [], []
    for match in matches:
        first, last = match.groups()
        raw = [first.lstrip('tT')] + ([last.lstrip('tT')] if last is not None else [])
        if any(not value.isdecimal() or not 1 <= int(value) <= 100 for value in raw):
            return replace(request, error='名次必须为 1～100 的整数；区间不能越界。'), remainder
        if last is not None:
            ranges.append(tuple(map(int, raw)))
        else:
            points.append((int(raw[0]), first.lower().startswith('t')))
    if ranges and points or len(set(ranges)) > 1:
        return replace(request, error='排名条件冲突，请只保留一个附近排名或一个区间。'), remainder
    if ranges:
        result = replace(request, mode='range', range_bounds=ranges[0], ranks=(), target_rank=None)
    else:
        values = tuple(sorted({point[0] for point in points}))
        if len(values) > 1 and not all(point[1] for point in points):
            return replace(request, error='多个排名请使用 T1 T10 T37；单个数字表示前后十名。'), remainder
        result = (replace(request, mode='near', target_rank=values[0], ranks=(), range_bounds=()) if len(values) == 1
                  else replace(request, mode='discrete', ranks=values, target_rank=None, range_bounds=()))
    if request.mode != 'trend' and (request.mode, request.ranks, request.target_rank) != (result.mode, result.ranks, result.target_rank):
        return replace(request, error='排名条件冲突，请只保留一组排名。'), remainder
    return result, remainder


def parse_cutoff(text: str) -> CutoffRequest:
    text = unicodedata.normalize("NFKC", text).strip().lstrip("/")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^(?:查)?榜[线線](?:\s|$)", "", text).strip()
    servers, numbers, ids, names, words = [], [], [], [], []
    numeric_only = False
    for match in _TOKEN.finditer(text):
        field, value = match.groups()
        token = match[0]
        if field:
            try:
                value = json.loads(value) if value.startswith('"') else value
            except ValueError:
                return CutoffRequest(error='歌名引号不完整，请使用 歌名="完整名称"。')
            if not value:
                return CutoffRequest(error='歌名或歌曲ID不能为空。')
            if field.casefold() == '歌曲id':
                if not value.isdecimal():
                    return CutoffRequest(error='歌曲ID必须是完整的数字ID。')
                ids.append(str(int(value)))
            else:
                names.append(value)
        elif token.casefold() in SERVER_ALIASES:
            servers.append(SERVER_ALIASES[token.casefold()])
        elif re.fullmatch(r'(?:server|服务器)[=:].*', token, re.I) or re.fullmatch(r'\S{1,4}服', token) or token.casefold() in _UNKNOWN_CODES:
            alias = re.sub(r'^(?:server|服务器)[=:]', '', token, flags=re.I).casefold()
            if alias not in SERVER_ALIASES:
                return CutoffRequest(error='未知服务器。仅支持日服（jp）、国服（hk）、韩服（kr）、英服（en）。')
            servers.append(SERVER_ALIASES[alias])
        elif token == '仅数值':
            numeric_only = True
        elif _SONG_NUMBER.fullmatch(token):
            try:
                groups = _SONG_NUMBER.fullmatch(token).groups()
                numbers.append(_song_integer(next(x for x in groups if x is not None)))
            except ValueError:
                return CutoffRequest(error='歌曲编号必须为正整数，例如 歌曲1。')
        else:
            words.append(token)
    if len(set(servers)) > 1:
        return CutoffRequest(error='同时指定了不同服务器，请保留一个服务器。')
    if len(set(numbers)) > 1 or len(set(ids)) > 1:
        return CutoffRequest(error='同时指定了不同歌曲，请保留一个歌曲选择。')
    request = CutoffRequest(servers[0] if servers else 'jp', song_number=numbers[0] if numbers else None,
                            song_id=ids[0] if ids else '', numeric_only=numeric_only)
    remaining = ' '.join(words)
    ranked, query = _rank_selection(request, remaining)
    if names:
        if query:
            return replace(request, error='歌名之外有未识别参数，请检查歌曲选择或排名写法。')
        return replace(ranked, query=names[0], extra_queries=tuple(dict.fromkeys(names[1:])), literal_query=True)
    # Catalog names containing numbers are resolved against the captured event first.
    return replace(request, query=remaining) if query else ranked


def parse_natural_cutoff(question: str) -> CutoffRequest | None:
    text = unicodedata.normalize("NFKC", question).strip().lstrip("/")
    if not MARKER.search(text):
        return None
    if FORBIDDEN.search(text):
        return CutoffRequest(error="只查询已采集的活动歌曲分数；不支持预测、配队、攻略或账号操作。", code="unsupported")
    if re.match(r"^(?:查)?榜线(?:\s|$)", text):
        return parse_cutoff(text)
    server = "jp"
    for alias in sorted(SERVER_ALIASES, key=len, reverse=True):
        if text.casefold().startswith(alias) and (not alias.isascii() or len(text) == len(alias) or text[len(alias)].isspace()):
            server, text = SERVER_ALIASES[alias], text[len(alias):].strip()
            break
    # Keep unfamiliar explicit regions visible as an error; never fall back to JP.
    if re.match(r"^(?:\S{1,4}服|server=|服务器[=:])", text, re.I):
        return CutoffRequest(error="未知服务器。仅支持日服（jp）、国服（hk）、韩服（kr）、英服（en）。")
    overview = re.sub(r"[\s?？。的]", "", text)
    if re.fullmatch(r"(?:现在|当前|这期|本期)?(?:全部|所有)?(?:歌曲)?(?:榜线|档线|歌曲榜)(?:现在)?(?:多少|是多少|多少分)?", overview):
        return CutoffRequest(server)
    match = re.fullmatch(r"(.+?)(?:的)?(?:前\s*([+-]?\d+(?:\.\d+)?)|([+-]?\d+(?:\.\d+)?)\s*线|[tT](\d+))(?:现在)?(?:是)?多少(?:分)?[?？]?", text)
    if match:
        number = next(value for value in match.groups()[1:] if value is not None)
        return parse_cutoff(server + ' ' + match[1].rstrip('的 ') + ' T' + number)
    match = re.fullmatch(r"(.+?)(?:的)?(?:榜线|档线)(?:现在)?(?:是)?多少(?:分)?[?？]?", text)
    if match:
        return parse_cutoff(server + ' ' + match[1].rstrip('的 '))
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
    display_ranks: tuple[int, ...] = ()  # A page projection of the same captured boards.

    @property
    def ranks(self) -> tuple[int, ...]:
        return self.display_ranks or self.request.ranks or NODES

    def song_label(self, board):
        number = next((i for i, song in enumerate(self.event.songs, 1)
                       if (song.challenge_id, song.music_id) == (board.song.challenge_id, board.song.music_id)), None)
        title = board.song.title
        if not title or title == f'歌曲 {board.song.music_id}':
            title = f'名称未获取（曲目 {board.song.music_id}）'
        return f'歌曲{number if number is not None else "?"} · {title}'

    @property
    def rank_label(self):
        request = self.request
        if request.mode == 'trend':
            return '关键排名 T1 / T2 / T3 / T10 / T100 · 观测趋势'
        if request.mode == 'near':
            return f'T{request.target_rank} 附近 · T{request.ranks[0]}～T{request.ranks[-1]}'
        if request.mode == 'range':
            return f'排名区间 T{request.ranks[0]}～T{request.ranks[-1]}'
        return '指定排名 ' + ' / '.join(f'T{rank}' for rank in request.ranks)

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
                 f"开始：{display_time(event.start_ms, event.server)}", f"结束：{display_time(event.end_ms, event.server)}", self.rank_label]
        if self.display_ranks and self.display_ranks != self.request.ranks:
            lines.append(f'本页 T{self.ranks[0]}～T{self.ranks[-1]}')
        for board in self.boards:
            if self.request.mode == 'trend':
                lines.append('')
            lines.extend([self.song_label(board), *song_period_lines(event, board.song)])
            if self.request.mode == 'trend':
                lines.append(" / ".join(f"T{rank}：{self.score_label(board, rank)}（ID：{self.id_label(board, rank)}；用户名：{self.name_label(board, rank)}）" for rank in self.ranks))
            lines.extend([f"{board.status} · 源采集 {display_time(board.fetched_ms, event.server)}", *board.notes])
            history = self.history_for(board)
            if history.warning:
                lines.append(history.warning)
            if history.points:
                lines.append("历史末次观测：" + display_time(history.points[-1].time_ms, event.server))
                if len(history.points) == 1:
                    lines.append("仅 1 个历史点，不足以形成曲线。")
        if self.request.mode != 'trend':
            lines.append('列：' + '｜'.join(self.song_label(board).partition(' · ')[0] for board in self.boards)
                         + '（分数/数字ID/用户名；缺失为未获取）')
            lines.extend(f'T{rank}：' + '｜'.join(f'{self.score_label(board, rank)}/{self.id_label(board, rank)}/{self.table_name(board, rank)}'
                                                for board in self.boards) for rank in self.ranks)
        lines.extend(["", *event.notes, "MoeNotes（非官方）· 挑战歌曲Top100 · 按响应位置，非预测或确认终榜。"])
        return "\n".join(lines)

    @staticmethod
    def score_label(board, rank):
        return str(board.score(rank)) if board.score(rank) is not None else '暂无数据'

    @staticmethod
    def id_label(board, rank):
        return board.player_id(rank) or '未获取'

    @staticmethod
    def name_label(board, rank):
        return board.player_name(rank) or '未获取'

    def table_name(self, board, rank):
        name = self.name_label(board, rank)
        return json.dumps(name, ensure_ascii=False) if any(c in name for c in '/｜"') else name


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
    query = request.query.strip()
    full_catalog = event.catalog or tuple((s.music_id, s.names + (s.title,)) for s in event.songs)
    event_ids = {s.music_id for s in event.songs}
    catalog = tuple((key, names) for key, names in full_catalog if key in event_ids)
    names = list(((query,) if query else ()) + request.extra_queries)
    if query and not request.literal_query:
        # Protect longest complete catalog names/aliases, including internal numbers.
        # Only complete word spans are removed; unknown remainder stays a condition.
        protected, remaining = [], query
        while remaining:
            tokens = tuple(re.finditer(r'\S+', remaining))
            found = None
            for length in range(len(tokens), 0, -1):
                for first in range(len(tokens) - length + 1):
                    start, end = tokens[first].start(), tokens[first + length - 1].end()
                    candidate = remaining[start:end]
                    if re.fullmatch(r'[tT]?[+-]?\d+(?:\.\d+)?', candidate):
                        continue
                    if scoped_song_matches(candidate, full_catalog):
                        found = (start, end, candidate)
                        break
                if found:
                    break
            if not found:
                break
            start, end, name = found
            protected.append(name)
            remaining = (remaining[:start] + ' ' + remaining[end:]).strip()
        request, leftover = _rank_selection(request, remaining)
        if request.error:
            return CutoffAnswer(request, event, message=request.error, status=request.code)
        if protected and leftover:
            return CutoffAnswer(request, event, message='歌曲之外有未识别参数，请检查歌曲选择或排名写法。', status='invalid_arguments')
        names = protected or ([leftover] if leftover else [])
        query = names[0] if names else ''
    request = replace(request, query=query, extra_queries=tuple(names[1:]), literal_query=bool(query))
    selected = []
    if request.song_number is not None:
        if request.song_number > len(event.songs):
            return CutoffAnswer(request, event, message=f'本服本期有 {len(event.songs)} 首挑战歌曲，请使用 歌曲1～歌曲{len(event.songs)}。', status='invalid_arguments')
        selected.append(event.songs[request.song_number - 1].music_id)
    if request.song_id:
        if request.song_id not in event_ids:
            return CutoffAnswer(request, event, message='这首歌不是本期活动的挑战歌曲。', status='empty')
        selected.append(request.song_id)
    for name in names:
        matches = scoped_song_matches(name, catalog)
        outside = scoped_song_matches(name, full_catalog) if not matches else ()
        if not matches and not outside:
            matches = scoped_song_matches(name, catalog, partial=True)
        if len(matches) > 1:
            labels = {key: titles[0] if titles else key for key, titles in catalog}
            return CutoffAnswer(request, event, message='歌曲名称有歧义，请补充完整名称或歌曲编号：\n' + '\n'.join(labels[key] for key in matches[:5]), status='ambiguous')
        if not matches:
            if outside:
                return CutoffAnswer(request, event, message='这首歌不是本期活动的挑战歌曲。', status='empty')
            if not event.catalog:
                return CutoffAnswer(request, event, message='该服务器歌曲元数据暂不可用，无法核对名称；请稍后重试或使用 歌曲N／歌曲ID=… 。', status='data_unavailable')
            return CutoffAnswer(request, event, message=f'该服务器未找到歌曲「{name}」。请使用完整歌名或已配置别名。', status='unknown_entity')
        selected.append(matches[0])
    if len(set(selected)) > 1:
        return CutoffAnswer(request, event, message='同时指定了不同歌曲，请保留一个歌曲选择。', status='invalid_arguments')
    songs = tuple(song for song in event.songs if not selected or song.music_id == selected[0])
    if request.song_number is not None:
        # An ordinal selects the exact challenge entry, even when a music ID repeats.
        songs = (event.songs[request.song_number - 1],)
    elif selected and len(songs) > 1:
        choices = '\n'.join(f'歌曲{i} · {song.title}' for i, song in enumerate(event.songs, 1)
                            if song.music_id == selected[0])
        return CutoffAnswer(request, event, message='本期同一曲目有多个挑战榜，请使用歌曲编号指定：\n' + choices, status='ambiguous')
    if names:
        # Collapse equivalent names only after the exact challenge entry is confirmed.
        request = replace(request, query=songs[0].title, extra_queries=(), literal_query=True)
    boards = source.boards(event, songs, deadline)
    histories = ()
    if request.mode == 'trend' and not request.numeric_only:
        histories = tuple((b.song.challenge_id, source.history.read(event, b.song, NODES) if source.history
                           else HistoryView(warning="历史记录未配置；当前值仍可查询。")) for b in boards)
    return CutoffAnswer(request, event, boards, status="success" if any(b.scores for b in boards) else "data_unavailable", histories=histories)
