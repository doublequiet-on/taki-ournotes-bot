# L3
# Input: Explicit region and the configured captured event repository.
# Output: Current/recent activity facts with source, dates and challenge identities.
# Pos: Query / Deterministic activity information; see L2-2.md.
# Effects: Bounded source reads only; no player ranking request, model or QQ calls.
from dataclasses import dataclass

from .event_cutoff_query import SERVER_ALIASES
from ..sources.moenotes_events import SERVERS, SourceError, display_time, zone_label

HELP = "/查活动 [jp/hk/kr/en]：当前／最近活动的名称、时间和挑战歌曲；不提供活动积分预测。"


@dataclass(frozen=True)
class ActivityAnswer:
    text: str
    status: str = "success"


def execute_activity(query, repository):
    tokens = query.strip().lower().split()
    if len(tokens) > 1 or tokens and tokens[0] not in SERVER_ALIASES:
        return ActivityAnswer(HELP, "invalid_arguments")
    server = SERVER_ALIASES[tokens[0]] if tokens else "jp"
    source = repository.event_cutoffs
    try:
        with source.foreground():
            event = source.event(server, source.deadline())
    except SourceError as exc:
        return ActivityAnswer("当前没有可用活动资料。" if exc.code == "not_found" else "活动资料暂不可用，请稍后重试。", "data_unavailable")
    state = {"nowOn": "进行中", "ended": "已结束", "upcoming": "尚未开始", "finished": "已结束"}.get(event.status, event.status)
    lines = [f"[{SERVERS[server][0]} · 活动信息]", f"{event.title}（活动 ID {event.event_id}）",
             f"来源状态：{state}", f"开始：{display_time(event.start_ms, server)}",
             f"结束：{display_time(event.end_ms, server)}", f"时间按{zone_label(server)}显示。", "挑战歌曲："]
    for song in event.songs:
        lines.append(f"{song.title} · 歌曲 ID {song.music_id} · 挑战 ID {song.challenge_id}")
        lines.append(f"查榜：/查榜线 {'hk' if server == 'tw' else server} 歌曲ID={song.music_id}")
    if not event.songs:
        lines.append("当前资料没有挑战歌曲榜。")
    lines.extend(event.notes)
    lines.append("资料来源：" + ("Haneoka（实时观测经 MoeNotes tracker）" if event.source == "haneoka" else event.source))
    return ActivityAnswer("\n".join(lines))
