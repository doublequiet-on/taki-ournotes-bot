# L3
# Input: Explicit Arena command arguments and an injected public repository.
# Output: Complete immutable InfoAnswer views, shared by text and image replies.
# Pos: Query / deterministic public queries.
# Effects: Calls the injected source; no model, platform or rendering dependencies.
from datetime import datetime, timezone, timedelta
import json
import re
import time
import unicodedata
from dataclasses import replace
from itertools import groupby

from ..domain.public_queries import ArenaRequest, PublicQueryError
from .contracts.info import InfoAnswer, InfoRow, InfoView
from .info_query import answer
from .entity_lexicon import band_aliases, band_name_variants
from .ranking_positions import KEY_POSITIONS, RANK_PART, nearby_window

REGIONS = {'jp': 'jp', '日服': 'jp', 'hk': 'tw', 'tw': 'tw', '国服': 'tw',
           'kr': 'kr', '韩服': 'kr', 'en': 'en', '英服': 'en'}
LABELS = {'jp': '日服', 'tw': '国服', 'en': '英服', 'kr': '韩服'}
BANDS = {1: 'MyGO!!!!!', 2: 'Ave Mujica', 3: '梦限大MewType', 4: 'millsage', 5: '一家Dumb Rock!'}
STAGE_COMMAND = '查激奏舞台'
RANK_COMMAND = '查激奏榜线'
STAGE_MODES = {'总览', '规则', '日程', '曲池', '段位', '奖励', '段位与奖励'}
RANK_MODES = {'榜单', '人数'}
MODE_ALIASES = {'概览': '总览', '排期': '日程', '乐曲': '曲池', '排行榜': '榜单',
                '段位人数': '人数', '人数明细': '人数', '段位人数明细': '人数'}
ARENA_HELP = '/查竞技场 [jp/hk/kr/en] [乐队] [总览/日程/曲池/段位/奖励/榜单/人数] [赛季=N] [排名=1-20]'
STAGE_HELP = '/查激奏舞台 [服] [乐队] [概览/规则/排期/乐曲/段位与奖励] [赛季=N]'
RANK_HELP = '/查激奏榜线 [服] 乐队 [榜单/人数/人数明细] [赛季=N] [50 或 20-40 或 T1 T10 T100] [仅数值]'
MAX_POSITION = 999_999_999


def localized(value):
    if isinstance(value, str):
        return value
    if isinstance(value, (tuple, list)):
        return next((value[i] for i in (3, 2, 0, 1, 4) if i < len(value) and value[i]), '')
    return ''


def stamp(value):
    if type(value) not in (int, float) or value <= 0:
        return '未获取'
    return datetime.fromtimestamp(value / 1000, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def region_tokens(tokens):
    regions = [REGIONS[t.lower()] for t in tokens if t.lower() in REGIONS]
    if len(regions) > 1:
        raise PublicQueryError('invalid_arguments', '请一次指定一个区服。')
    return regions[0] if regions else 'jp', [t for t in tokens if t.lower() not in REGIONS]


def _arena_position_selection(matches):
    points = []
    window = None
    for match in matches:
        first, last = match.groups()
        bounds = [value.lstrip('tT') for value in (first, last) if value is not None]
        if any(not re.fullmatch(r'[1-9][0-9]{0,8}', value) for value in bounds):
            raise PublicQueryError('invalid_arguments', '排名须为 1～999999999 的整数。')
        if last is not None:
            if len(matches) != 1:
                raise PublicQueryError('invalid_arguments', '排名区间不能和其他排名条件同时使用。')
            window = tuple(map(int, bounds))
        else:
            points.append(int(bounds[0]))
    if window is not None:
        first, last = window
        return first, last, ()
    if len(points) == 1:
        if matches[0][1].lower().startswith('t'):
            return points[0], points[0], tuple(points)
        first, last = nearby_window(points[0], MAX_POSITION)
        return first, last, ()
    if any(not match[1].lower().startswith('t') for match in matches):
        raise PublicQueryError('invalid_arguments', '多个排名请写成 T1 T10 T100。')
    positions = tuple(sorted(set(points)))
    if len(positions) > 5:
        raise PublicQueryError('invalid_arguments', '一次最多指定五个不同的 T 位。')
    return positions[0], positions[-1], positions


def _arena_band(term):
    names = {key: band_name_variants(name) for key, name in BANDS.items()}
    for alias, target in band_aliases():
        for key, name in BANDS.items():
            if band_name_variants(target) & band_name_variants(name):
                names[key].update(band_name_variants(alias))
    matches = [key for key, variants in names.items() if band_name_variants(term) & variants]
    return matches[0] if len(matches) == 1 else None


def parse_arena(query, *, command=None):
    query = unicodedata.normalize('NFKC', query).strip()
    if command == RANK_COMMAND and query == '用法':
        raise PublicQueryError('invalid_arguments', RANK_HELP)
    region, tokens = region_tokens(query.split())
    legacy = command is None
    stage = command == STAGE_COMMAND
    help_text = ARENA_HELP if legacy else STAGE_HELP if stage else RANK_HELP
    permitted = STAGE_MODES | RANK_MODES if legacy else STAGE_MODES if stage else RANK_MODES
    normalized_modes = [MODE_ALIASES.get(token, token) for token in tokens]
    modes = [mode for mode in normalized_modes if mode in STAGE_MODES or mode in RANK_MODES]
    if len(modes) > 1:
        raise PublicQueryError('invalid_arguments', '每次只查一种内容。\n' + help_text)
    default_mode = '总览' if legacy or stage else '榜单'
    mode = modes[0] if modes else default_mode
    if mode not in permitted:
        target = RANK_COMMAND if stage else STAGE_COMMAND
        raise PublicQueryError('invalid_arguments', f'「{mode}」请使用 /{target}。')
    season, first, last, ranking = None, 1, 20, ''
    positions, numeric_only = (), False
    population_details = mode == '人数' and (legacy or any(token in {'人数明细', '段位人数明细'} for token in tokens))
    remainder = []
    for token, normalized_mode in zip(tokens, normalized_modes):
        if normalized_mode in permitted:
            continue
        if token.startswith('赛季='):
            if season is not None or not re.fullmatch(r'赛季=[1-9][0-9]{0,8}', token):
                raise PublicQueryError('invalid_arguments', help_text)
            season = int(token[3:])
        elif token.startswith('排名='):
            match = re.fullmatch(r'排名=([1-9][0-9]{0,8})-([1-9][0-9]{0,8})', token)
            if not match or mode != '榜单' or ranking:
                raise PublicQueryError('invalid_arguments', help_text)
            ranking = token
            first, last = map(int, match.groups())
        elif token == '仅数值' and command == RANK_COMMAND:
            numeric_only = True
        else:
            remainder.append(token)
    term = ' '.join(remainder)
    if command == RANK_COMMAND:
        rank_text = term.replace(',', ' ')
        matches = tuple(RANK_PART.finditer(rank_text))
        if matches:
            if mode != '榜单' or ranking:
                raise PublicQueryError('invalid_arguments', '排名与其他排名条件或人数模式冲突。')
            first, last, positions = _arena_position_selection(matches)
            ranking = ' '.join(match[0] for match in matches)
            term = RANK_PART.sub(' ', rank_text).strip()
        elif mode == '榜单' and not modes and not ranking:
            positions = KEY_POSITIONS
            first, last = positions[0], positions[-1]
    if not first <= last < first + 100:
        raise PublicQueryError('invalid_arguments', '排名须位于连续最多 100 个响应位置内。')

    band = _arena_band(term) if term else None
    if term and band is None:
        raise PublicQueryError('invalid_arguments', '无法确认乐队。\n' + help_text)
    if mode in RANK_MODES and band is None:
        game_region = 'hk' if region == 'tw' else region
        selected_mode = '人数明细' if population_details else mode
        suffix = (' ' + selected_mode if modes else '') + (f' 赛季={season}' if season else '')
        suffix += (' ' + ranking if ranking else '') + (' 仅数值' if numeric_only else '')
        examples = '\n'.join(f'/{RANK_COMMAND} {game_region} {name}{suffix}' for name in BANDS.values())
        raise PublicQueryError('invalid_arguments', '请指定乐队：\n' + examples)
    return ArenaRequest(region, band, mode, season, first, last, positions, numeric_only, population_details)


def rule_text(rule, song=None):
    song = song or {}
    attribute = rule.get('musicType') or song.get('musicType', 0)
    attributes = {0: '原曲属性', 1: '红', 2: '蓝', 3: '绿', 4: '黄', 5: '紫', 99: '全属性'}
    original = song.get('arenaMissions', [0, 0, 0])
    missions = rule.get('missions', [0, 0, 0])
    names = {0: '原曲任务', 1: 'COMBO', 2: 'LUCK', 3: 'JUST'}
    return attributes.get(attribute, f'属性{attribute}') + ' · ' + '/'.join(
        names.get(value or original[i], '未确认') for i, value in enumerate(missions))


def inside(row, now):
    return row['startAt'] <= now < row['endAt'] + 1000


def quality_lines(snapshot):
    return ('来源：' + snapshot.source + ' / ' + (snapshot.provider or '未声明'),
            '游戏区：' + (snapshot.game_region or '未声明') + '；资源区：' + (snapshot.resource_server or '未声明'),
            '来源ID：' + (snapshot.source_id or '未获取') + '；快照ID：' + (snapshot.snapshot_id or '未获取'),
            '资源版本：' + (snapshot.release or '未获取') + '；归一化：' + snapshot.normalization_version,
            '时间口径：' + snapshot.time_kind + '；北京时间 UTC+8',
            '来源观测：' + stamp(snapshot.observed_ms),
            '来源参考时钟：' + stamp(snapshot.server_ms),
            '本机接收：' + stamp(snapshot.fetched_at * 1000),
            '新鲜度：' + {'fresh': '新鲜', 'stale': '旧快照', 'unknown': '未知'}.get(snapshot.freshness, '未知'))


def _arena_season(catalog, season_id, now):
    seasons = sorted(catalog['entries'].values(), key=lambda season: season['startAt'])
    if season_id is not None:
        return next((season for season in seasons if season['seasonId'] == season_id), None)
    return (next((season for season in seasons if inside(season, now)), None)
            or next((season for season in reversed(seasons) if season['startAt'] <= now), None)
            or (seasons[0] if seasons else None))


def _arena_period(row):
    return f"{stamp(row['startAt'])} — {stamp(row['endAt'])}"


def _arena_rewards(league, season, name):
    ranks = {rank['id']: localized(rank['name']) for rank in league['ranks']}
    categories = (('dailyRewards', '每日'), ('promotionRewards', '晋级'),
                  ('seasonRewards', '赛季'), ('rankingRewards', '名次'))
    for key, title in categories:
        for reward in league.get(key, season.get(key, [])):
            if reward.get('leagueId') not in (0, league['id']):
                continue
            requirement = ranks.get(reward.get('rankId'), '') or f"区间：{reward.get('from')}—{reward.get('to')}"
            yield InfoRow(name + ' · ' + title, (localized(reward['name']) + f" ×{reward['count']}", requirement),
                          image_url=reward.get('image', ''))


def _arena_stage_rows(request, season, league, now, images, mission_icons):
    name = localized(league['name'])
    mode = request.mode
    if mode in {'段位', '奖励', '段位与奖励'}:
        rows = []
        if mode != '奖励':
            rows.extend(InfoRow(name + ' · ' + localized(rank['name']), (
                f"积分门槛：{rank['points']}", f"下赛季起始积分：{rank['resetPoints']}",
                '开放：' + (stamp(rank['startAt']) if rank['startAt'] else '赛季开始')),
                image_url=images.get('ranks', {}).get(str(rank.get('rank')), '')) for rank in league['ranks'])
        if mode != '段位':
            rows.extend(_arena_rewards(league, season, name))
        return rows

    rules = sorted(league['rules'], key=lambda rule: rule['startAt'])
    if mode == '日程':
        return [InfoRow(name, (_arena_period(rule), rule_text(rule)), icons=mission_icons(rule)) for rule in rules]

    current = next((rule for rule in reversed(rules) if inside(rule, now)), None)
    if mode == '曲池':
        return [InfoRow(localized(song['musicTitle']), (f"歌曲 ID {song['musicId']}", rule_text(current or {}, song)),
                        group=name, image_url=song.get('jacketThumbUrl', ''), command=f"/查曲 {song['musicId']}")
                for song in league['songs'] if song.get('arenaPublishedAt', 0) <= now]

    if not rules and mode == '规则':
        return []
    upcoming_limit = 3 if request.band is not None or mode == '规则' else 1
    upcoming = [rule for rule in rules if rule['startAt'] > now][:upcoming_limit]
    body = ['当前无生效轮换'] if current is None else ['当前规则：' + rule_text(current), _arena_period(current)]
    if upcoming:
        body.append('接下来的轮换')
        body.extend(f'{_arena_period(rule)} · {rule_text(rule)}' for rule in upcoming)
    elif rules:
        body.append('本赛季无后续轮换')
    else:
        body.append('暂无轮换资料')
    return [InfoRow(name, tuple(body), image_url=league.get('image', ''),
                    icons=mission_icons(current) if current else ())]


def _arena_standings(request, rows):
    by_position = {row.get('position', position): row for position, row in enumerate(rows, request.first)}
    positions = request.positions or range(request.first, request.last + 1)
    selected = [(position, by_position.get(position)) for position in positions]
    if not request.positions:
        selected = [(position, row) for position, row in selected if row is not None]
    if not any(row is not None and row.get('point') is not None for _, row in selected):
        return [InfoRow('榜线', ('所查位置没有榜单条目，或积分尚未提供。',))], 'data_unavailable'

    result = []
    for position, row in selected:
        if row is None:
            result.append(InfoRow(f'T{position}', ('该响应位置未获取',)))
            continue
        display_rank, point = row.get('rank'), row.get('point')
        rank_label = display_rank if display_rank is not None else '未知'
        points = f"舞台积分（AP）：{point if point is not None else '未获取'}"
        state = '积分状态：' + (row.get('pointState') or '未声明')
        if request.numeric_only:
            result.append(InfoRow(f'T{position}', (points, state)))
            continue
        name = row['name'] or '未获取昵称'
        result.append(InfoRow(f'T{position} · 显示名次 {rank_label} · {name}', (
            points, '公开ID：' + (row.get('profileId') or '未获取'), f'响应位置：{position}', state),
            kind='standing', position=position, point_state=row.get('pointState'),
            table=((f"显示 {display_rank if display_rank is not None else '?'}", name),),
            game_icon={1: 'ImageRanking1st.png', 2: 'ImageRanking2ng.png', 3: 'ImageRanking3rd.png'}.get(display_rank, '')))
    selection = (' / '.join(f'T{position}' for position in request.positions) if request.positions
                 else f'T{request.first}–T{request.last}')
    result.append(InfoRow('榜线口径', (f'所查位置：{selection}。显示名次可并列或跳号，边界并列可能未收齐。',
        '积分为来源快照。零值可能是协议默认值。')))
    return result, 'success'


def _arena_population(ranks, images, board, details):
    max_rank = board.get('maxRank')
    rows = [InfoRow('联赛上榜人数', (str(max_rank) if max_rank is not None else '未获取',))]
    ends = {tier['threshold']: later['threshold'] - 1 for tier, later in zip(board['tiers'], board['tiers'][1:])}

    def group_key(tier):
        threshold = tier['threshold']
        major = ranks[threshold].get('rank')
        return ('major', major) if not details and type(major) is int else ('division', threshold)

    for (scope, _), members in groupby(board['tiers'], key=group_key):
        group = tuple(members)
        lower, upper = group[0]['threshold'], group[-1]['threshold']
        rank = ranks[lower]
        name = localized(rank['name'])
        if scope == 'major':
            name = re.sub(r'\s*[0-9０-９]+$', '', name) or name
        span = f'{lower}–{ends[upper]}' if upper in ends else f'{lower}+'
        count = sum(tier['count'] for tier in group)
        rows.append(InfoRow(name, (f'舞台积分 {span} · 人数：{count}',),
                            image_url=images.get('ranks', {}).get(str(rank.get('rank')), '')))
    return rows


def _arena_records(request, source, season, league, images, now):
    ranks = {}
    if request.mode == '人数':
        for rank in league['ranks']:
            if rank['startAt'] <= now:
                ranks.setdefault(rank['points'], rank)
        if not ranks and league['ranks']:
            raise PublicQueryError('data_unavailable', '该联赛暂无已开放的段位。')
    thresholds = tuple(sorted(ranks))
    snapshot = source.board(request, season['seasonId'], thresholds)
    board = json.loads(snapshot.body)
    if request.mode == '人数':
        rows, status = _arena_population(ranks, images, board, request.population_details), 'success'
    else:
        rows, status = _arena_standings(request, board['rows'])
    return [*rows, InfoRow('观测时间', quality_lines(snapshot))], status


_STAGE_EMPTY = {
    '规则': '暂无轮换规则。', '日程': '暂无轮换排期。', '曲池': '暂无已公开乐曲。',
    '段位': '暂无段位资料。', '奖励': '暂无奖励资料。', '段位与奖励': '暂无段位与奖励资料。',
}
_STAGE_LABELS = {'总览': '概览', '日程': '排期', '曲池': '乐曲'}


def execute_arena(query, repository, *, now=None, command=None):
    try:
        request = parse_arena(query, command=command)
        source = repository.public_queries
        snapshot = source.arena(request.region)
        catalog = json.loads(snapshot.body)
        images = catalog.get('images', {})
        now = (time.time() if now is None else now) * 1000
        season = _arena_season(catalog, request.season, now)
        if season is None:
            raise PublicQueryError('not_found', '该资料服没有对应赛季。')
        leagues = [league for league in season['leagues'] if request.band is None or league['bandId'] == request.band]
        if not leagues:
            raise PublicQueryError('not_found', '该赛季未收录此乐队联赛。' if request.band else '该赛季暂无联赛资料。')

        season_status = '进行中' if inside(season, now) else '尚未开始' if now < season['startAt'] else '已结束'
        rows = [InfoRow('赛季时间', (f"开始：{stamp(season['startAt'])}", f"结束：{stamp(season['endAt'])}"), season_status)]
        live = request.mode in RANK_MODES
        if live:
            league = leagues[0]
            details, status = _arena_records(request, source, season, league, images, now)
            rows.extend(details)
            section = localized(league['name']) + ' · ' + ('段位人数' if request.mode == '人数' else '榜线')
        else:
            source_snapshot = (repository.capture_catalog().source_snapshot
                               if request.mode in {'总览', '规则', '日程'} else None)

            def mission_icons(rule):
                attribute = source.arena_attribute(request.region, rule['musicType'], source_snapshot, snapshot.release)
                return ((attribute,) if attribute else ()) + tuple(images.get('missions', {}).get(str(value), '')
                                                                 for value in rule['missions'])

            status = 'no_results'
            for league in leagues:
                details = _arena_stage_rows(request, season, league, now, images, mission_icons)
                if details:
                    status = 'success'
                    rows.extend(details)
                else:
                    rows.append(InfoRow(localized(league['name']), (_STAGE_EMPTY[request.mode],)))
            section = _STAGE_LABELS.get(request.mode, request.mode)

        title = f"{LABELS[request.region]} · 激奏{'榜线' if live else '舞台'} · {localized(season['title'])}"
        if request.mode == '规则':
            server = 'jp' if request.region == 'jp' else 'intl'
            url = f'https://haneoka.org/{server}/zh-CN/arena/?region={request.region}'
            rows.append(InfoRow('玩法说明', ('Haneoka 激奏舞台页面的「规则」栏', url)))
        subtitle = f"{section} · 赛季 ID {season['seasonId']} · 北京时间 UTC+8"
        view = InfoView(title, subtitle, rows=tuple(rows), layout='facts',
                        presentation='arena', footer=(f'Haneoka {snapshot.resource} · {snapshot.release}',))
        result = answer((view,), status)
        return replace(result, views=()) if request.numeric_only else result
    except PublicQueryError as exc:
        return InfoAnswer(str(exc) + (f' 建议等待{exc.retry_after}秒。' if exc.retry_after else ''), exc.status)
