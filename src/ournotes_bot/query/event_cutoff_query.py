# L3
# Input: direct/NL query text and the server-scoped EventCutoffRepository.
# Output: Captured single-song boards and explicit per-event total-ranking availability.
# Pos: Query / Deterministic challenge-song cutoffs; see L2-2.md.
# Effects: current queries use bounded Data calls/history writes; explicit activities read local history only; no model/QQ.
from __future__ import annotations

import re
import json
import unicodedata
from dataclasses import dataclass, replace

from ..sources.moenotes_events import (BoardSnapshot, EventSnapshot, EventSong, SERVERS, SourceError,
                                      display_time, zone_label, event_ranking_format)
from .ranking_positions import KEY_POSITIONS as NODES, RANK_PART, nearby_window
from ..sources.cutoff_history import HistoryView
from ..domain.query_cancellation import thread_cancel_event
from ..domain.total_rankings import TotalRankingSnapshot

HELP = '/查榜线 [jp/hk/kr/en] [id1或活动="名称"] [单曲或总榜] [50或20-40] [仅数值]；参数顺序不限。总榜可用性取决于来源。id1查活动1的已保存观测，锁定活动1优先查同一期实时榜、换期后查该期已保存观测。裸数字表示排名，无排名看五档趋势，单排名看前后十名，区间看每行分数。'
SERVER_ALIASES = {"jp": "jp", "日服": "jp", "hk": "tw", "国服": "tw", "國服": "tw",
                  "tw": "tw", "台服": "tw", "臺服": "tw",
                  "kr": "kr", "韩服": "kr", "韓服": "kr", "en": "en", "英服": "en", "国际服": "en", "國際服": "en"}
FORBIDDEN = re.compile(r"预测|預測|推荐|推薦|配队|配隊|编成|攻略|代练|代肝|账号|帐号|賬號|最强|怎么打|如何打|抽卡建议")
SONG_SELECTION_REMOVED = '榜线按活动查询，请去掉歌名、歌曲 ID 或序号。'
MARKER = re.compile(r"榜线|榜線|档线|檔線|歌曲榜|(?:总榜|總榜|综合榜|綜合榜).*多少|(?:前\s*[+-]?\d+(?:\.\d+)?|[+-]?\d+(?:\.\d+)?\s*线).*?(?:多少|分)|T\d+.*多少", re.I)
_EVENT_ID_PREFIX = r'id\s*(?:[=:]\s*)?'
_EVENT_ID = re.compile(_EVENT_ID_PREFIX + r'([+-]?\d+(?:\.\d+)?)', re.I)
_LOCKED_EVENT = re.compile(r'锁定活动([+-]?\d+(?:\.\d+)?)')
_TOKEN = re.compile(r'(?:(活动ID|活动名称|活动|活动名稱|活動ID|活動名稱|活動|歌名|歌曲ID)\s*=\s*("(?:\\.|[^"\\])*"|[^\s]+))|'
                    + _EVENT_ID_PREFIX + r'[+-]?\d+(?:\.\d+)?(?=\s|$)|[^\s]+', re.I)
_UNKNOWN_CODES = {'cn', 'us', 'eu', 'gb', 'sg'}


@dataclass(frozen=True, init=False)
class CutoffRequest:
    server: str = "jp"
    ranks: tuple[int, ...] = ()
    error: str = ""
    code: str = "invalid_arguments"
    server_hint: str = ""  # Legacy constructor field; explicit unknown servers are now rejected locally.
    numeric_only: bool = False
    mode: str = "trend"
    target_rank: int | None = None
    range_bounds: tuple[int, ...] = ()
    event_id: str = ""
    event_name: str = ""
    board_scope: str = "all"
    lock_event: bool = False

    def __init__(self, server="jp", rank=None, error="", code="invalid_arguments",
                 server_hint="", *, ranks=(), numeric_only=False, mode=None, target_rank=None,
                 range_bounds=(), event_id="", event_name="", board_scope="all", lock_event=False):
        values = tuple(ranks) + ((rank,) if rank is not None else ())
        mode = mode or ("near" if rank is not None else "discrete" if values else "trend")
        if mode == "near":
            target_rank = target_rank if target_rank is not None else rank
            if type(target_rank) is not int or not 1 <= target_rank <= 100:
                error, values = error or "名次必须为 1～100 的整数。", ()
            else:
                first, last = nearby_window(target_rank, 100)
                values = tuple(range(first, last + 1))
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
        if lock_event and (not event_id or event_name):
            error = error or '锁定活动需要一个明确的活动 ID。'
        if board_scope not in {'all', 'song', 'total'}:
            error = error or '无法识别榜单类型。'
        for name, value in (("server", server), ("ranks", tuple(sorted(set(values)))),
                            ("error", error), ("code", code), ("server_hint", server_hint), ("numeric_only", numeric_only),
                            ("mode", mode), ("target_rank", target_rank), ("range_bounds", tuple(range_bounds)),
                            ("event_id", event_id), ("event_name", event_name),
                            ("board_scope", board_scope), ("lock_event", lock_event)):
            object.__setattr__(self, name, value)

    @property
    def rank(self):
        """Compatibility accessor; canonical internal state is always a rank tuple."""
        return self.target_rank if self.mode == "near" else self.ranks[0] if len(self.ranks) == 1 else None

    def command_label(self):
        parts = ['查榜线', 'hk' if self.server == 'tw' else self.server]
        if self.board_scope != 'all':
            parts.append('总榜' if self.board_scope == 'total' else '单曲')
        if self.event_id:
            parts.append(('锁定活动' if self.lock_event else 'id') + self.event_id)
        if self.event_name:
            parts.append('活动=' + json.dumps(self.event_name, ensure_ascii=False))
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


def _rank_selection(request, text):
    """Extract complete rank groups; never apply the discrete limit to a window."""
    normalized = re.sub(r'[,，]', ' ', text)
    matches = tuple(RANK_PART.finditer(normalized))
    if not matches:
        return request, text.strip()
    remainder = RANK_PART.sub(' ', normalized)
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
    servers, words = [], []
    event_ids, event_names, locked_ids = [], [], []
    numeric_only = False
    scopes = []
    for match in _TOKEN.finditer(text):
        field, value = match.groups()
        token = match[0]
        if field:
            if field.casefold() in {'歌名', '歌曲id'}:
                return CutoffRequest(error=SONG_SELECTION_REMOVED + HELP)
            try:
                value = json.loads(value) if value.startswith('"') else value
            except ValueError:
                return CutoffRequest(error='名称引号不完整，请使用 活动="完整名称"。')
            if not value:
                return CutoffRequest(error='名称或ID不能为空。')
            if field.casefold() in {'活动id', '活動id'}:
                if not value.isdecimal() or int(value) <= 0:
                    return CutoffRequest(error='活动ID必须是正整数。')
                event_ids.append(str(int(value)))
            else:
                if not re.sub(r'[\W_]+', '', value):
                    return CutoffRequest(error='活动名称不能为空或只有标点。')
                event_names.append(value)
        elif token.startswith('锁定活动'):
            locked = _LOCKED_EVENT.fullmatch(token)
            if not locked or not re.fullmatch(r'[1-9][0-9]{0,15}', locked[1]):
                return CutoffRequest(error='请填写要锁定的完整活动 ID，例如 锁定活动1。')
            locked_ids.append(locked[1])
        elif event_id_match := _EVENT_ID.fullmatch(token):
            value = event_id_match[1]
            if not value.isdecimal() or int(value) <= 0:
                return CutoffRequest(error='活动ID必须是正整数，例如 id1。')
            event_ids.append(str(int(value)))
        elif token.casefold() in SERVER_ALIASES:
            servers.append(SERVER_ALIASES[token.casefold()])
        elif re.fullmatch(r'(?:server|服务器)[=:].*', token, re.I) or re.fullmatch(r'\S{1,4}服', token) or token.casefold() in _UNKNOWN_CODES:
            alias = re.sub(r'^(?:server|服务器)[=:]', '', token, flags=re.I).casefold()
            if alias not in SERVER_ALIASES:
                return CutoffRequest(error='未知服务器。仅支持日服（jp）、国服（hk）、韩服（kr）、英服（en）。')
            servers.append(SERVER_ALIASES[alias])
        elif token in {'总榜', '總榜', '综合榜', '綜合榜', '单曲', '單曲'}:
            scopes.append('song' if token in {'单曲', '單曲'} else 'total')
        elif token == '仅数值':
            numeric_only = True
        else:
            words.append(token)
    if len(set(servers)) > 1:
        return CutoffRequest(error='同时指定了不同服务器，请保留一个服务器。')
    if len(set(scopes)) > 1:
        return CutoffRequest(error='不能同时指定总榜和单曲，请保留一种榜单选择。')
    if locked_ids and (event_ids or event_names or len(set(locked_ids)) > 1):
        return CutoffRequest(error='请保留一个活动选择。')
    if len(set(event_ids)) > 1 or len(set(event_names)) > 1:
        return CutoffRequest(error='同时指定了不同活动，请保留一个活动选择。')
    request = CutoffRequest(servers[0] if servers else 'jp', numeric_only=numeric_only,
                            event_id=next(iter(locked_ids or event_ids), ''), event_name=event_names[0] if event_names else '',
                            lock_event=bool(locked_ids),
                            board_scope=scopes[0] if scopes else 'all')
    ranked, remainder = _rank_selection(request, ' '.join(words))
    return replace(ranked, error=SONG_SELECTION_REMOVED + HELP) if remainder and not ranked.error else ranked


def parse_natural_cutoff(question: str) -> CutoffRequest | None:
    text = unicodedata.normalize("NFKC", question).strip().lstrip("/")
    if not MARKER.search(text):
        return None
    if FORBIDDEN.search(text):
        return CutoffRequest(error="只查询已采集的活动歌曲分数；不支持预测、配队、攻略或账号操作。", code="unsupported")
    if re.match(r"^(?:查)?榜[线線](?:\s|$)", text):
        return parse_cutoff(text)
    server = "jp"
    for alias in sorted(SERVER_ALIASES, key=len, reverse=True):
        if text.casefold().startswith(alias) and (not alias.isascii() or len(text) == len(alias) or text[len(alias)].isspace()):
            server, text = SERVER_ALIASES[alias], text[len(alias):].strip()
            break
    # Keep unfamiliar explicit regions visible as an error; never fall back to JP.
    if re.match(r"^(?:\S{1,4}服|server=|服务器[=:])", text, re.I):
        return CutoffRequest(error="未知服务器。仅支持日服（jp）、国服（hk）、韩服（kr）、英服（en）。")
    if re.match(r"^(?:查)?榜[线線](?:\s|$)", text):
        return parse_cutoff(server + ' ' + re.sub(r'^(?:查)?榜[线線]\s*', '', text))
    if re.search(r'积分榜|積分榜|活动积分|活動積分', text):
        return CutoffRequest(server, error='只查询活动挑战分数榜。', code='unsupported')
    # These are qualifiers for a current board. Keep named events and song selectors intact.
    text = re.sub(r'^(?:(?:这期|這期|本期|当前|當前)(?:的)?\s*)?(?:活动|活動)(?:的)?\s*',
                  '', text)
    overview = re.sub(r"[\s?？。的]", "", text).replace("線", "线").replace("檔", "档")
    if re.fullmatch(r"(?:现在|現在|当前|當前|这期|這期|本期)?(?:全部|所有)?(?:活动|活動)?(?:歌曲)?(?:榜线|档线|歌曲榜)(?:现在|現在)?(?:多少|是多少|多少分)?", overview):
        return CutoffRequest(server)
    if re.fullmatch(r"(?:总榜|總榜|综合榜|綜合榜)(?:榜线|档线)?(?:现在|現在)?(?:多少|是多少|多少分)?", overview):
        return CutoffRequest(server, board_scope='total')
    match = re.fullmatch(r"(?:(总榜|總榜|综合榜|綜合榜|歌曲榜|活动歌曲榜|活動歌曲榜|榜线|榜線)(?:的)?\s*)?(?:前\s*([+-]?\d+(?:\.\d+)?)|([+-]?\d+(?:\.\d+)?)\s*[线線]|[tT](\d+))(?:现在|現在)?(?:是)?多少(?:分)?[?？]?", text)
    if match:
        scope = '总榜' if match[1] in {'总榜', '總榜', '综合榜', '綜合榜'} else ''
        number = next(value for value in match.groups()[1:] if value is not None)
        return parse_cutoff(f'{server} {scope} T{number}')
    return CutoffRequest(server, error=SONG_SELECTION_REMOVED + HELP)


def event_status(event: EventSnapshot) -> str:
    return {"feature": "未开始", "nowOn": "进行中", "aggregation": "结算中",
            "result": "已结束（结果公示）", "end": "已结束", "historical": "本地历史观测"}.get(event.status, "状态未知")


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
    history_ranks: tuple[int, ...] = ()
    score_display_limit: int = 30_000_000  # Display policy, never a source validity/game score limit.
    total: TotalRankingSnapshot | None = None
    total_history: HistoryView = HistoryView()
    total_status: str = ''
    historical_result: bool | None = None

    def total_time_label(self, stamp):
        return display_time(stamp,self.event.server)+' '+zone_label(self.event.server)

    @property
    def total_region_label(self):
        return SERVERS[self.event.server][0]

    @property
    def total_text(self):
        board = self.total
        if board is None:
            return ''
        lines = [f'{SERVERS[self.event.server][0]} · {self.event.title} · 综合分数榜', self.rank_label,
                 f'{board.status} · 源采集 {display_time(board.fetched_ms,self.event.server)}']
        for rank in self.ranks:
            value = board.score(rank)
            lines.append(f'T{rank}：' + (str(value) if value is not None else '暂无数据')
                         + f' · ID：{board.player_id(rank) or "未获取"} · {board.player_name(rank) or "未获取"}')
        lines.extend((*board.notes,self.total_history.warning))
        if self.total_history.points:
            lines.append('综合榜历史截至：' + display_time(self.total_history.points[-1].time_ms,self.event.server))
        lines.append(f'来源 {board.source} · 综合榜独立观测；非预测、非确认终榜。')
        return '\n'.join(line for line in lines if line)

    @property
    def ranking_notes(self):
        if self.total is not None:
            return ()
        if self.event and self.request.board_scope == 'all':
            rules = event_ranking_format(self.event)
            return (self.total_status or rules.total_status,) if rules.total_enabled is True else ()
        return ()

    @property
    def historical(self):
        if self.historical_result is not None:
            return self.historical_result
        return bool(self.request.event_id or self.request.event_name)

    @property
    def value_label(self):
        return '最近一次有效观测' if self.historical else '当前值'

    @property
    def capture_label(self):
        return '有效观测时间' if self.historical else '当前源采集'

    @property
    def ranks(self) -> tuple[int, ...]:
        return self.display_ranks or self.request.ranks or NODES

    def song_label(self, board):
        if not self.event.challenge_order_verified:
            return f'挑战ID {board.song.challenge_id} · {board.song.title}'
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
        view = next((view for key, view in self.histories if key == board.song.challenge_id), HistoryView())
        hidden = sum(value is not None and value > self.score_display_limit
                     for point in view.points for value in point.scores)
        current = any(board.score(rank) is not None and board.score(rank) > self.score_display_limit
                      for rank in self.ranks)
        if not hidden and not current:
            return view
        points = tuple(replace(point, scores=tuple(
            None if value is not None and value > self.score_display_limit else value
            for value in point.scores)) for point in view.points)
        warning = (f'异常分数：超过展示阈值 {self.score_display_limit:,} 的历史分数不绘制（{hidden} 项），缺口断线；'
                   '当前值保留并标注，原始记录与来源排名不变。阈值不是游戏理论上限。')
        return replace(view, points=points, warning=' '.join(filter(None, (view.warning, warning))))

    def has_players(self, board):
        return not self.historical or any(board.player_id(r) or board.player_name(r) for r in self.ranks)

    @property
    def text(self) -> str:
        if self.message:
            return self.message
        event = self.event
        if event is None:
            return "活动暂不可用。"
        if not self.boards and self.total is not None:
            return self.total_text
        lines = [f"{SERVERS[event.server][0]} · {event.title} · {event_status(event)}", f"活动 {event.event_id} · {zone_label(event.server)}",
                 f"开始：{display_time(event.start_ms, event.server)}", f"结束：{display_time(event.end_ms, event.server)}", self.rank_label]
        if self.display_ranks and self.display_ranks != self.request.ranks:
            lines.append(f'本页 T{self.ranks[0]}～T{self.ranks[-1]}')
        if self.total is not None:
            lines.extend(('',self.total_text,''))
        for board in self.boards:
            if self.request.mode == 'trend':
                lines.append('')
            lines.extend([self.song_label(board), *song_period_lines(event, board.song)])
            if self.request.mode == 'trend':
                lines.append(" / ".join(f"T{rank}：{self.score_label(board, rank)}" +
                             (f"（ID：{self.id_label(board, rank)}；用户名：{self.name_label(board, rank)}）" if self.has_players(board) else '') for rank in self.ranks))
            lines.extend([f"{board.status} · 源采集 {display_time(board.fetched_ms, event.server)}", *board.notes])
            history = self.history_for(board)
            if history.warning:
                lines.append(history.warning)
            if history.points:
                lines.append("历史记录截至：" + display_time(history.points[-1].time_ms, event.server))
                if len(history.points) == 1:
                    lines.append("仅 1 个历史点，不足以形成曲线。")
        if self.request.mode != 'trend':
            lines.append('列：' + '｜'.join(self.song_label(board).partition(' · ')[0] for board in self.boards)
                         + ('（分数/数字ID/用户名；缺失为未获取）' if any(self.has_players(b) for b in self.boards) else '（分数）'))
            lines.extend(f'T{rank}：' + '｜'.join(f'{self.score_label(board, rank)}/{self.id_label(board, rank)}/{self.table_name(board, rank)}' if self.has_players(board) else self.score_label(board, rank)
                                                for board in self.boards) for rank in self.ranks)
        lines.extend(["", *self.ranking_notes, *event.notes, f"{self.source_label} · 挑战歌曲 Top 100 · 按响应位置，非确认终榜。"])
        return "\n".join(lines)

    @property
    def source_label(self):
        if self.historical:
            return '本地历史（' + '、'.join(sorted({b.source for b in self.boards})) + '）'
        return {'haneoka': 'Haneoka Game Records', 'open': '开放平台', 'tracker': 'MoeNotes'}.get(self.event.source, self.event.source)

    def score_label(self, board, rank):
        value = board.score(rank)
        if value is None:
            return '暂无数据'
        return f'异常分数 {value}' if value > self.score_display_limit else str(value)

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
        return replace(_execute_cutoff(request, repository), score_display_limit=
                       getattr(repository.event_cutoffs, 'score_display_limit', 30_000_000))


def _historical_event(request, repository):
    """Resolve only recorded challenge identities; static song associations never create boards."""
    history = repository.event_cutoffs.history
    totals = getattr(repository.event_cutoffs, 'total_rankings', None)
    if history is None and (totals is None or request.board_scope == 'song'):
        return None, '历史记录未配置。', 'data_unavailable'
    total_events = ()
    if totals is not None and request.board_scope != 'song':
        try:
            total_events = totals.history.events(request.server)
        except SourceError:
            if request.board_scope == 'total':
                raise
    try:
        song_events = history.events(request.server) if history is not None else ()
    except SourceError:
        if not total_events:
            raise
        song_events = ()
    total_ids = {event.event_id for event in total_events}
    events = (*total_events, *(event for event in song_events if event.event_id not in total_ids))
    # The existing JP catalog also supplies names for databases recorded before metadata storage.
    aliases = {}
    if request.server == 'jp' and hasattr(repository, 'capture_catalog'):
        catalog = repository.capture_catalog()
        records = {str(record.id): record for record in catalog.activities}
        songs = {str(song.id): song for song in catalog.songs}
        enriched = []
        for event in events:
            record = records.get(event.event_id)
            if record:
                aliases[event.event_id] = (event.title, *record.names.values())
                event = replace(event, title=record.title,
                                start_ms=event.start_ms if event.start_ms is not None else round(record.start_at * 1000) if record.start_at is not None else None,
                                end_ms=event.end_ms if event.end_ms is not None else round(record.end_at * 1000) if record.end_at is not None else None,
                                banner=event.banner or record.image_url)
            event = replace(event, songs=tuple(replace(song, title=songs[song.music_id].title,
                                jacket=song.jacket or songs[song.music_id].jacket_url,
                                names=tuple(dict.fromkeys((song.title, *song.names,
                                    songs[song.music_id].title, *songs[song.music_id].titles))))
                                if song.music_id in songs else song for song in event.songs))
            enriched.append(event)
        events = tuple(enriched)
    if request.event_name:
        def normalize(value):
            return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value).casefold())
        needle = normalize(request.event_name)
        exact = tuple(e for e in events if any(needle == normalize(n) for n in (e.title, *aliases.get(e.event_id, ())) if n))
        events = exact or tuple(e for e in events if any(needle in normalize(n) for n in (e.title, *aliases.get(e.event_id, ())) if n))
    if request.event_id:
        events = tuple(e for e in events if e.event_id == request.event_id)
    if not events:
        return None, '未找到该服务器指定活动的本地历史；只查询已保存的数据，不补造未采集记录。', 'empty'
    if len(events) > 1:
        return None, f'活动名称匹配 {len(events)} 个活动，请使用活动ID指定（最多列10个）：\n' + '\n'.join(f'id{e.event_id} · {e.title}' for e in events[:10]), 'ambiguous'
    event = events[0]
    return replace(event, notes=('各曲展示最近一次有效观测及其时间；非实时榜、非确认终榜。',)), '', 'success'


def _execute_cutoff(request: CutoffRequest, repository) -> CutoffAnswer:
    if request.error:
        return CutoffAnswer(request, message=request.error, status=request.code)
    if request.server not in SERVERS:
        return CutoffAnswer(request, message="未知服务器。" + HELP, status="invalid_arguments")
    source = repository.event_cutoffs
    deadline = source.deadline()
    historical = bool(request.event_id or request.event_name)
    event = None
    current_error = None
    if request.lock_event:
        try:
            current = source.event(request.server, deadline)
        except SourceError as exc:
            current_error = exc
        else:
            if current.server == request.server and current.event_id == request.event_id and not current.stale:
                event = current
                historical = False
    try:
        if historical:
            event, message, status = _historical_event(request, repository)
            if event is None:
                if current_error and current_error.code != 'not_found':
                    message = '暂时无法确认当前活动。' + message
                return CutoffAnswer(request, message=message, status=status)
        elif event is None:
            event = source.event(request.server, deadline)
    except SourceError as exc:
        if historical:
            return CutoffAnswer(request, message='历史库暂不可用或活动身份冲突，未改查当前活动。', status='data_unavailable')
        message = {'not_found': '该服务器当前没有可用活动。', 'pending': '活动观测尚未就绪，请稍后重试。',
                   'rate_limited': '来源限流，请稍后重试。'}.get(exc.code, '该服务器的活动信息暂不可用，请稍后重试。')
        return CutoffAnswer(request, message=SERVERS[request.server][0] + "：" + message,
                            status=exc.code if exc.code in {'not_found', 'pending', 'rate_limited'} else 'data_unavailable')
    answer = _cutoff_for_event(request, source, event, deadline, historical)
    return replace(answer, historical_result=historical)


def _cutoff_for_event(request, source, event, deadline, historical):
    rules = event_ranking_format(event)
    totals = getattr(source,'total_rankings',None)
    total = None
    total_history = HistoryView()
    total_status = rules.total_status
    include_total = request.board_scope != 'song'
    try_unknown = historical or request.board_scope == 'total' or event.source == 'tracker'
    if include_total and totals is not None and (rules.total_enabled is True or rules.total_enabled is None and try_unknown):
        if historical or totals.connected:
            stop = thread_cancel_event.get()

            def should_stop():
                return (stop is not None and stop.is_set()) or source.monotonic() >= deadline

            total_status = '综合榜来源或历史暂不可用，单曲榜单独查询。'
            try:
                total = (totals.history.latest(event, should_stop=should_stop)
                         if historical else totals.capture(event, deadline))
                if total is None and not historical:
                    total_status = totals.unavailable_status(event)
                if request.mode == 'trend' and not request.numeric_only:
                    total_history = totals.history.read(event, request.ranks or NODES, should_stop=should_stop)
                if total is not None and not any(s is not None for s in total.scores) and not total_history.points:
                    total_status=total.status
                    total=None
            except SourceError as exc:
                total_status = ('综合榜历史查询达到时间预算，请稍后重试。' if exc.code == 'total_history_interrupted'
                                else '综合榜历史暂不可用。' if historical else '综合榜活动身份或快照暂不可用。')
            if stop is not None and stop.is_set():
                return CutoffAnswer(request, event, status='data_unavailable')
    if request.board_scope == 'total':
        if total is not None:
            return CutoffAnswer(request,event,total=total,total_history=total_history,
                                status='success' if total.scores or total_history.points else 'data_unavailable')
        return CutoffAnswer(request, event, message=f'{SERVERS[event.server][0]} · {event.title}\n' + total_status,
                            status='empty' if rules.total_enabled is False else 'data_unavailable')
    if not event.songs:
        if total is not None:
            return CutoffAnswer(request,event,total=total,total_history=total_history,
                                status='success' if total.scores or total_history.points else 'data_unavailable')
        return CutoffAnswer(request, event, message="本活动没有挑战歌曲榜。", status="empty")
    try:
        if historical:
            boards = tuple(source.history.latest(event, song) for song in event.songs) if source.history is not None else ()
        else:
            boards = source.boards(event, event.songs, deadline)
    except SourceError:
        if total is not None and any(total.score(rank) is not None for rank in request.ranks or NODES):
            return CutoffAnswer(request, event, total=total, total_history=total_history, status='success')
        return CutoffAnswer(request, event, message='历史库暂不可用，未改查当前活动。', status='data_unavailable')
    histories = ()
    ranks=request.ranks or NODES
    if (historical and not any(b.score(rank) is not None for b in boards for rank in ranks)
            and not (total and any(total.score(rank) is not None for rank in ranks))):
        details = tuple(dict.fromkeys(n for b in boards for n in (b.song.title + '：' + b.status, *b.notes)))
        return CutoffAnswer(request, event, message='所选排名暂无有效历史分数。\n' + '\n'.join(details), status='data_unavailable')
    history_ranks = NODES if request.mode == 'trend' else ()
    if history_ranks and not request.numeric_only:
        views = []
        for board in boards:
            if not source.history:
                view = HistoryView(warning="历史记录未配置；当前值仍可查询。")
            elif hasattr(source.history, 'read_board'):
                view = source.history.read_board(event, board, history_ranks)
            elif board.provider is not None:
                view = HistoryView(warning='当前provider与旧历史未建立隔离映射，暂不拼接曲线。')
            else:
                view = source.history.read(event, board.song, history_ranks)
            views.append((board.song.challenge_id, view))
        histories = tuple(views)
        if historical:
            histories = tuple((key, replace(view, warning=view.warning.replace('当前值', '最近一次有效观测')
                                            .replace('当前榜线仍可查询', '最近一次有效观测单独显示'))) for key, view in histories)
    answer = CutoffAnswer(request, event, boards, status="success" if any(b.has_values for b in boards) or total and any(v is not None for v in total.scores) else "data_unavailable",
                          histories=histories,history_ranks=history_ranks,total=total,total_history=total_history,total_status=total_status)
    # A real saved history remains useful during an outage. With no current
    # values or history, avoid sending several empty trend/table images.
    if not historical and total is None and not any(b.has_values for b in boards) and not any(view.points for _, view in histories):
        details = tuple(dict.fromkeys(b.status for b in boards))
        return replace(answer, message=f'{SERVERS[event.server][0]} · {event.title}\n'
                       + '本期所选单曲榜暂无可用分数，请稍后重试。\n'
                       + '\n'.join((*answer.ranking_notes, *details)))
    return answer
