# L3
# Input: Explicit region/current request or JP static event name/ID/history.
# Output: Current/recent activity and static event facts shared by text and images.
# Pos: Query / Deterministic activity information; see L2-2.md.
# Effects: Current requests read event metadata only; no ranking, model or QQ calls.
import time
import math
from datetime import datetime, timezone, timedelta
from dataclasses import replace

from ..data import normalize
from .contracts.info import InfoAnswer, InfoRow, InfoSchedule, InfoView
from .event_cutoff_query import SERVER_ALIASES
from .info_query import (answer, available, card_rows, footer, named_matches,
                         list_view, period, schedule, state, text_value, unavailable)
from ..sources.moenotes_events import SERVERS, SourceError, display_time, zone_label

HELP = '/查活动 [jp/hk/kr/en]：当前／最近活动；/查活动 列表；/查活动 历史 [名称]；/查活动 ID或名称（日服）。'


def detail_rows(record, section='', prefix='/查活动'):
    result = []
    if not section and hasattr(record, 'event_type_id'):
        result.append(InfoRow('活动类型与榜制', (
            f'活动类型 ID {record.event_type_id}（类型名称未确认）' if record.event_type_id is not None else '活动类型未获取',
            '开放：' + '、'.join(record.ranking_modes) if record.ranking_modes else '开放榜制未获取')))
    for group in record.details:
        if section and group.name != section:
            continue
        if not section and group.name == '任务':
            continue
        facts = group.facts if section or group.name != '奖励' else tuple(f for f in group.facts if f.preview)
        for fact in facts:
            result.append(InfoRow(f'{group.name} · {fact.title}', fact.lines, image_url=fact.image_url,
                                  kind='bonus' if group.name == '加成' else 'row', table=fact.table))
        if not group.complete:
            result.append(InfoRow(group.name, ('关联资料不完整，缺失部分尚未获取。',)))
        elif not facts:
            result.append(InfoRow(group.name, ('没有已收录条目。' if section else f'完整明细：{prefix} {group.name}',)))
    if not record.details:
        result.append(InfoRow('详情资料', ('当前缓存尚未收录该项明细。',)))
    if not section:
        result.append(InfoRow('详情查询', tuple(prefix + ' ' + name for name in ('加成', '奖励', '任务', '卡池'))))
    return tuple(result)


def overview_rows(record, related, prefix):
    details = detail_rows(record, prefix=prefix)
    groups = tuple(tuple(row for row in details if row.title.startswith(name + ' · ') or row.title == name)
                   for name in ('加成', '奖励', '卡池'))
    grouped = tuple(row for group in groups for row in group)
    return (*tuple(row for row in details if row.title == '活动类型与榜制'), *groups[0],
            *tuple(row for row in related if row.label == '挑战歌曲'), *groups[1],
            *tuple(row for row in related if row.kind != 'song' and row.label != '挑战歌曲'),
            *groups[2], *tuple(row for row in related if row.kind == 'song' and row.label != '挑战歌曲'),
            *tuple(row for row in details if row not in grouped and row.title != '活动类型与榜制'))


def _related_rows(record, catalog, *, challenge_song_ids=(), include_songs=True):
    cards = {}
    notes = []
    for field, label in (('bonus_cards', '指定加成卡'), ('reward_cards', '卡牌奖励'), ('pickup_cards', '活动展示卡')):
        for ref in getattr(record, field):
            labels=cards.setdefault(ref,[])
            if label not in labels:labels.append(label)
        if field not in record.available_relations:
            notes.append(label+'关联不完整；这里只列已确认的卡牌。')
    tags={'指定加成卡':'加成','卡牌奖励':'奖励','活动展示卡':'展示'}
    rows=[replace(card_rows((ref,),catalog,'')[0],label=' · '.join(labels),
                  tags=tuple(tags[label] for label in labels)) for ref,labels in sorted(cards.items())]
    if notes:rows.append(InfoRow('关联资料说明',tuple(notes)))
    if not include_songs:
        return tuple(rows)
    songs = {song.id: song for song in catalog.songs}
    for ident in dict.fromkeys(record.song_ids):
        if str(ident) in challenge_song_ids:
            continue
        song = songs.get(ident)
        rows.append(InfoRow(song.title if song else f'歌曲 ID {ident}',
                            (f'歌曲 ID {ident}',),
                            song.jacket_url if song else '', f'/查曲 {ident}', '关联歌曲',kind='song'))
    return tuple(rows)


def _static(query, repository, now, section=''):
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
        rows = [InfoRow(f'活动 ID {record.id}', (*period(record), f'资料状态：{state(record, now)}'),
                        kind='schedule',schedule=schedule(record,now,'活动'))]
        description = text_value(record.description)
        if description:
            rows.append(InfoRow('活动说明', (description,)))
        rows.extend(_related_rows(record, catalog))
        rows = (rows[:1] + list(detail_rows(record, section, f'/查活动 {record.id}')) if section else
                rows[:1] + list(overview_rows(record, rows[1:], f'/查活动 {record.id}')))
        return answer((InfoView(record.title, '日服 · 活动详情', record.detail_image_url or record.image_url, tuple(rows), notes, 'detail',
                                presentation='activity',visual_footer=footer(catalog,diagnostics=False),
                                hero_kind='event' if record.detail_image_url else 'banner',hero_logo_url=record.logo_url),))
    rows = (InfoRow(r.title, (f'活动 ID {r.id}', *period(r), f'资料状态：{state(r, now)}'),
                    r.image_url, f'/查活动 {r.id}', image_kind='wide',kind='banner',schedule=schedule(r,now,'活动'),
                    children=_related_rows(r,catalog,include_songs=False)) for r in records)
    title = '日服 · 历史活动' if mode == 'history' else '日服 · 活动列表' if mode == 'list' else '日服 · 活动候选'
    return answer((list_view(title, rows, subtitle=f'共 {len(records)} 个 · 日本时间 UTC+9', notes=notes,
                            presentation='activity',visual_footer=footer(catalog,diagnostics=False)),))


def _current(server, repository, section=''):
    source = repository.event_cutoffs
    try:
        with source.foreground():
            event = source.event(server, source.deadline())
    except SourceError as exc:
        return InfoAnswer('当前没有可用活动资料。' if exc.code == 'not_found' else '活动资料暂不可用，请稍后重试。', 'data_unavailable')
    label = {'nowOn': '进行中', 'ended': '已结束', 'upcoming': '尚未开始', 'finished': '已结束'}.get(event.status, event.status)
    rows = [InfoRow(f'{event.title}（活动 ID {event.event_id}）',
                    (f'来源状态：{label}', f'开始：{display_time(event.start_ms, server)}',
                     f'结束：{display_time(event.end_ms, server)}', f'时间按{zone_label(server)}显示。'),
                    kind='schedule',schedule=InfoSchedule(f'活动 ID {event.event_id}',label,
                        display_time(event.start_ms,server),display_time(event.end_ms,server),zone_label(server)))]
    for song in event.songs:
        # Only the current source response can establish this challenge identity.
        rows.append(InfoRow(song.title, (f'歌曲 ID {song.music_id} · 挑战 ID {song.challenge_id}',),
                            song.jacket, f"/查榜线 {'hk' if server == 'tw' else server} 歌曲ID={song.music_id}", '挑战歌曲',kind='song'))
    if not event.songs:
        rows.append(InfoRow('挑战歌曲', ('当前资料没有挑战歌曲榜。',)))
    notes = list(event.notes)
    visual_notes = list(event.notes)
    hero, hero_kind, logo = event.banner, 'banner', ''
    matched = False
    if hasattr(repository, 'capture_catalog'):
        catalog = repository.capture_catalog()
        calendar = getattr(catalog, 'activity_calendars', {}).get('jp' if server == 'jp' else 'intl')
        records = catalog.activities if server == 'jp' else calendar.records if calendar else ()
        if records:
            match = next((record for record in records if str(record.id) == event.event_id
                          and record.start_at is not None and record.end_at is not None
                          and round(record.start_at * 1000) == event.start_ms
                          and round(record.end_at * 1000) == event.end_ms), None)
            if match:
                matched = True
                if server == 'jp':
                    hero,hero_kind,logo=(match.detail_image_url,'event',match.logo_url) if match.detail_image_url else (event.banner,'banner','')
                    rows.extend(_related_rows(match, catalog,
                        challenge_song_ids={song.music_id for song in event.songs}))
                    notes.extend(footer(catalog))
                    visual_notes.extend(footer(catalog,diagnostics=False))
                else:
                    visual_notes.append('补充资料来源：Haneoka intl（已核对活动身份与时间）')
                prefix = '/查活动 ' + ('hk' if server == 'tw' else server)
                rows = (rows[:1] + list(detail_rows(match, section, prefix)) if section else
                        rows[:1] + list(overview_rows(match, rows[1:], prefix)))
    if not matched:
        missing = InfoRow(section or '补充资料', ('尚未取得与该服本期活动身份及时间一致的详情资料。',))
        rows = rows[:1] + [missing] if section else rows + [missing]
    notes.append('资料来源：' + ('Haneoka（实时观测经 MoeNotes tracker）' if event.source == 'haneoka' else event.source))
    visual_notes.append(notes[-1])
    return answer((InfoView(event.title, subtitle=f'{SERVERS[server][0]} · 当前／最近活动', hero_url=hero,
                            rows=tuple(rows), footer=tuple(notes), layout='detail',presentation='activity',
                            visual_footer=tuple(visual_notes),hero_kind=hero_kind,hero_logo_url=logo),))


# Calendar resource identity is deliberately separate from the four ranking regions.
CALENDAR_SERVERS = {'jp': 'jp', '日服': 'jp', 'intl': 'intl', 'tw': 'intl',
                    '台服': 'intl', '国际服': 'intl', 'en': 'intl', '英服': 'intl'}


def _calendar(server, mode, repository, now):
    catalog = repository.capture_catalog()
    calendar = getattr(catalog, 'activity_calendars', {}).get(server)
    if calendar is None:
        # Older JP publications remain usable; never borrow JP dates for intl.
        if server != 'jp' or not available(catalog, 'activities'):
            return unavailable('活动日程')
        records = catalog.activities
    else:
        records = calendar.records
    zone = timezone(timedelta(hours=8))
    def stamp(value):
        return datetime.fromtimestamp(value, zone).strftime('%Y-%m-%d %H:%M:%S') + '（北京时间 UTC+8）'
    valid = [r for r in records if all(type(v) in (int, float) and math.isfinite(v)
             for v in (r.start_at, r.end_at)) and r.start_at < r.end_at]
    active = [r for r in valid if r.start_at <= now < r.end_at]
    ended = sorted((r for r in valid if r.end_at <= now), key=lambda r: r.end_at, reverse=True)
    future = sorted((r for r in valid if r.start_at > now), key=lambda r: r.start_at)
    unknown = len(valid) != len(records)
    recent = ended[0] if ended else None
    upcoming = future[0] if future else None
    lines = []
    evidence_expires_at = None
    ambiguous = len(active) > 1 or (len(ended) > 1 and ended[0].end_at == ended[1].end_at) or (
        len(future) > 1 and future[0].start_at == future[1].start_at)
    if ambiguous:
        lines.append('资料中存在时间重叠或同一时间的多期活动，暂不能唯一确定本期／下期。')
    elif mode == '下期':
        if upcoming and not unknown:
            lines.append(f'下一期《{upcoming.title}》将于 {stamp(upcoming.start_at)} 开始。')
        else:
            lines.append('当前已读取的资料还不能确定下一期开启时间。')
    elif active:
        current = active[0]
        if mode == '日程开始':
            lines.append(f'《{current.title}》已于 {stamp(current.start_at)} 开始，目前进行中。')
        else:
            lines.append(f'《{current.title}》正在进行，将于 {stamp(current.end_at)} 结束。')
            if catalog.cache_state == 'fresh' and not unknown:
                lines.append('记得及时清 CP，别留到活动结束啦。')
    else:
        if recent:
            lines.append(f'最近一期《{recent.title}》已于 {stamp(recent.end_at)} 结束。')
        else:
            lines.append('当前资料中没有能确认正在进行或最近结束的活动。')
        if upcoming and not unknown:
            lines.append(f'下一期《{upcoming.title}》将于 {stamp(upcoming.start_at)} 开始，目前还没开始。')
        else:
            lines.append('当前已读取的资料还不能确定下一期开启时间。')
    if unknown:
        lines.append('部分活动时间缺失，无法确认完整日程。')
    # A missing future catalog entry may already have a published notice. Only
    # the source adapter can supply a verified explicit body time, never a model.
    if not ambiguous and not upcoming and (not active or mode == '下期'):
        notices = getattr(repository, 'activity_announcements', None)
        if notices is not None:
            announced, notice_note = notices.next(server, now)
            if announced:
                evidence_expires_at = announced.expires_at
                lines = [line for line in lines if '还不能确定下一期开启时间' not in line]
                lines.append(f'公告预告《{announced.title}》将于 {stamp(announced.start_at)} 开始。')
                lines.append(f'[查看活动公告]({announced.url})')
            if notice_note:
                lines.append(notice_note)
    label = '日服' if server == 'jp' else '国际服（台服）'
    if catalog.cache_state == 'stale':
        lines.append('当前使用旧缓存，时间可能已有调整。')
    text = '\n'.join((label + ' · 活动日程', *lines))
    return InfoAnswer(text, evidence_expires_at=evidence_expires_at)


def execute_activity(query, repository, *, now=None):
    query = query.strip()
    parts = query.rsplit(None, 1)
    section = parts[-1] if parts and parts[-1] in {'加成', '奖励', '任务', '卡池'} else ''
    if section:
        query = parts[0] if len(parts) > 1 else ''
    tokens = query.lower().split()
    server = 'jp'
    if tokens and tokens[-1] in {'日程', '日程开始', '下期'}:
        if len(tokens) == 1 or len(tokens) == 2 and tokens[0] in CALENDAR_SERVERS:
            resource = CALENDAR_SERVERS[tokens[0]] if len(tokens) == 2 else 'jp'
            return _calendar(resource, tokens[-1], repository, time.time() if now is None else now)
        return InfoAnswer(HELP, 'invalid_arguments')
    if tokens and tokens[0] in SERVER_ALIASES:
        server = SERVER_ALIASES[tokens[0]]
        query = query.split(None, 1)[1].strip() if len(tokens) > 1 else ''
    if query and not normalize(query):
        return InfoAnswer(HELP, 'invalid_arguments')
    if not query or query in {'当前', '最近'}:
        return _current(server, repository, section)
    if server != 'jp':
        return InfoAnswer('其他服务器目前只支持当前／最近活动。' + HELP, 'invalid_arguments')
    if not hasattr(repository, 'capture_catalog'):
        return InfoAnswer(HELP, 'invalid_arguments')
    return _static(query, repository, time.time() if now is None else now, section)
