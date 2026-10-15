# L3
# Input: Explicit region/current request or JP static event name/ID/history.
# Output: Current/recent activity and static event facts shared by text and images.
# Pos: Query / Deterministic activity information; see L2-2.md.
# Effects: Current requests read event metadata only; no ranking, model or QQ calls.
import time
from dataclasses import replace

from ..data import normalize
from .contracts.info import InfoAnswer, InfoRow, InfoView
from .event_cutoff_query import SERVER_ALIASES
from .info_query import (answer, available, card_rows, footer, named_matches,
                         list_view, period, state, text_value, unavailable)
from ..sources.moenotes_events import SERVERS, SourceError, display_time, zone_label

HELP = '/查活动 [jp/hk/kr/en]：当前／最近活动；/查活动 列表；/查活动 历史 [名称]；/查活动 ID或名称（日服）。'


def _related_rows(record, catalog):
    cards = {}
    notes = []
    for field, label in (('bonus_cards', '指定加成卡'), ('reward_cards', '卡牌奖励'), ('pickup_cards', '活动展示卡')):
        for ref in getattr(record, field):
            labels=cards.setdefault(ref,[])
            if label not in labels:labels.append(label)
        if field not in record.available_relations:
            notes.append(label+'关联不完整；这里只列已确认的卡牌。')
    rows=[replace(card_rows((ref,),catalog,'')[0],label=' · '.join(labels)) for ref,labels in sorted(cards.items())]
    if notes:rows.append(InfoRow('关联资料说明',tuple(notes)))
    songs = {song.id: song for song in catalog.songs}
    for ident in dict.fromkeys(record.song_ids):
        song = songs.get(ident)
        rows.append(InfoRow(song.title if song else f'歌曲 ID {ident}',
                            ('活动关联歌曲；不据此判断是否为挑战歌曲。',),
                            song.jacket_url if song else '', f'/查曲 {ident}', '关联歌曲'))
    return tuple(rows)


def _static(query, repository, now):
    mode, term = 'search', query
    if query in {'列表', '全部'}:
        mode, term = 'list', ''
    elif query == '历史' or query.startswith('历史 '):
        mode, term = 'history', query[2:].strip()
    if term and not normalize(term):
        return InfoAnswer(HELP, 'invalid_arguments')
    catalog = repository.capture_catalog()
    if not available(catalog, 'activities'):
        return unavailable('活动')
    records = catalog.activities
    if mode == 'history':
        records = tuple(r for r in records if r.end_at is not None and r.end_at <= now)
    if term:
        records = named_matches(records, term)
    records = tuple(sorted(records, key=lambda r: (r.start_at is not None, r.start_at or 0, r.id), reverse=True))
    notes = footer(catalog)
    if not records:
        return answer((InfoView('日服 · 活动资料', '没有找到符合条件的活动。', footer=(*notes, HELP)),), 'no_results')
    if mode == 'search' and len(records) == 1:
        record = records[0]
        rows = [InfoRow(f'活动 ID {record.id}', (*period(record), f'资料状态：{state(record, now)}'))]
        description = text_value(record.description)
        if description:
            rows.append(InfoRow('活动说明', (description,)))
        rows.extend(_related_rows(record, catalog))
        return answer((InfoView(record.title, '日服 · 活动详情', record.image_url, tuple(rows), notes, 'detail'),))
    rows = (InfoRow(r.title, (f'活动 ID {r.id}', *period(r), f'资料状态：{state(r, now)}'),
                    r.image_url, f'/查活动 {r.id}', image_kind='wide') for r in records)
    title = '日服 · 历史活动' if mode == 'history' else '日服 · 活动列表' if mode == 'list' else '日服 · 活动候选'
    return answer((list_view(title, rows, subtitle=f'共 {len(records)} 个', notes=notes),))


def _current(server, repository):
    source = repository.event_cutoffs
    try:
        with source.foreground():
            event = source.event(server, source.deadline())
    except SourceError as exc:
        return InfoAnswer('当前没有可用活动资料。' if exc.code == 'not_found' else '活动资料暂不可用，请稍后重试。', 'data_unavailable')
    label = {'nowOn': '进行中', 'ended': '已结束', 'upcoming': '尚未开始', 'finished': '已结束'}.get(event.status, event.status)
    rows = [InfoRow(f'{event.title}（活动 ID {event.event_id}）',
                    (f'来源状态：{label}', f'开始：{display_time(event.start_ms, server)}',
                     f'结束：{display_time(event.end_ms, server)}', f'时间按{zone_label(server)}显示。'))]
    for song in event.songs:
        # Only the current source response can establish this challenge identity.
        rows.append(InfoRow(song.title, (f'歌曲 ID {song.music_id} · 挑战 ID {song.challenge_id}',),
                            song.jacket, f"/查榜线 {'hk' if server == 'tw' else server} 歌曲ID={song.music_id}", '挑战歌曲'))
    if not event.songs:
        rows.append(InfoRow('挑战歌曲', ('当前资料没有挑战歌曲榜。',)))
    notes = list(event.notes)
    if server == 'jp' and hasattr(repository, 'capture_catalog'):
        catalog = repository.capture_catalog()
        if available(catalog, 'activities'):
            match = next((record for record in catalog.activities if str(record.id) == event.event_id
                          and record.start_at is not None and record.end_at is not None
                          and round(record.start_at * 1000) == event.start_ms
                          and round(record.end_at * 1000) == event.end_ms), None)
            if match:
                rows.extend(_related_rows(match, catalog))
                notes.extend(footer(catalog))
    notes.append('资料来源：' + ('Haneoka（实时观测经 MoeNotes tracker）' if event.source == 'haneoka' else event.source))
    return answer((InfoView(f'[{SERVERS[server][0]} · 活动信息]', hero_url=event.banner,
                            rows=tuple(rows), footer=tuple(notes), layout='detail'),))


def execute_activity(query, repository, *, now=None):
    query = query.strip()
    tokens = query.lower().split()
    server = 'jp'
    if tokens and tokens[0] in SERVER_ALIASES:
        server = SERVER_ALIASES[tokens[0]]
        query = query.split(None, 1)[1].strip() if len(tokens) > 1 else ''
    if query and not normalize(query):
        return InfoAnswer(HELP, 'invalid_arguments')
    if not query or query in {'当前', '最近'}:
        return _current(server, repository)
    if server != 'jp':
        return InfoAnswer('其他服务器目前只支持当前／最近活动。' + HELP, 'invalid_arguments')
    if not hasattr(repository, 'capture_catalog'):
        return InfoAnswer(HELP, 'invalid_arguments')
    return _static(query, repository, time.time() if now is None else now)
