# L3
# Input: Explicit Arena/player command arguments and an injected public repository.
# Output: Complete immutable InfoAnswer views, shared by text and image replies.
# Pos: Query / deterministic public queries.
# Effects: Calls the injected source; no model, platform or rendering dependencies.
from datetime import datetime, timezone, timedelta
import json
import re
import time
import unicodedata
from dataclasses import replace

from ..domain.public_queries import ArenaRequest, PlayerRequest, PublicQueryError
from .contracts.info import InfoAnswer, InfoRow, InfoView
from .info_query import answer
from .entity_lexicon import band_aliases, band_name_variants

REGIONS = {'jp': 'jp', '日服': 'jp', 'hk': 'tw', 'tw': 'tw', '国服': 'tw',
           'kr': 'kr', '韩服': 'kr', 'en': 'en', '英服': 'en'}
LABELS = {'jp': '日服', 'tw': '国服', 'en': '英服', 'kr': '韩服'}
BANDS = {1: 'MyGO!!!!!', 2: 'Ave Mujica', 3: '梦限大MewType', 4: 'millsage', 5: '一家Dumb Rock!'}
MODES = {'总览', '日程', '曲池', '段位', '奖励', '榜单', '人数'}
ARENA_HELP = '/查竞技场 [jp/hk/kr/en] [乐队] [总览/日程/曲池/段位/奖励/榜单/人数] [赛季=N] [排名=1-20]'
PLAYER_HELP = '/查玩家 玩家ID [jp/hk/kr/en] [名片 [页N]]'


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


def parse_arena(query):
    query = unicodedata.normalize('NFKC', query).strip()
    region, tokens = region_tokens(query.split())
    modes = [t for t in tokens if t in MODES]
    if len(modes) > 1:
        raise PublicQueryError('invalid_arguments', ARENA_HELP)
    mode = modes[0] if modes else '总览'
    season, first, last, ranked = None, 1, 20, False
    remainder = []
    for token in tokens:
        if token in MODES:
            continue
        if token.startswith('赛季='):
            if season is not None or not re.fullmatch(r'赛季=[1-9][0-9]{0,8}', token):
                raise PublicQueryError('invalid_arguments', ARENA_HELP)
            season = int(token[3:])
        elif token.startswith('排名='):
            match = re.fullmatch(r'排名=([1-9][0-9]{0,8})-([1-9][0-9]{0,8})', token)
            if not match or mode != '榜单' or ranked:
                raise PublicQueryError('invalid_arguments', ARENA_HELP)
            ranked = True
            first, last = map(int, match.groups())
            if not first <= last < first + 100:
                raise PublicQueryError('invalid_arguments', '排名须为连续区间，一次最多100名。')
        else:
            remainder.append(token)
    band = None
    if remainder:
        term = ' '.join(remainder)
        names = {key: band_name_variants(name) for key, name in BANDS.items()}
        for alias, target in band_aliases():
            for key, name in BANDS.items():
                if band_name_variants(target) & band_name_variants(name):
                    names[key].update(band_name_variants(alias))
        matches = [key for key, variants in names.items() if band_name_variants(term) & variants]
        if len(matches) != 1:
            raise PublicQueryError('invalid_arguments', '无法确认乐队。' + ARENA_HELP)
        band = matches[0]
    return ArenaRequest(region, band, mode, season, first, last)


def parse_player(query):
    region, tokens = region_tokens(unicodedata.normalize('NFKC', query).split())
    if not tokens:
        raise PublicQueryError('invalid_arguments', PLAYER_HELP)
    ident = tokens.pop(0)
    pattern = r'[0-9]{1,19}' if region == 'jp' else {'tw': '2', 'en': '3', 'kr': '4'}[region] + r'[0-9]{10}'
    if not re.fullmatch(pattern, ident) or not 0 < int(ident) <= 9223372036854775807:
        raise PublicQueryError('invalid_arguments', '请输入该服有效的公开玩家ID。' + PLAYER_HELP)
    card, page = False, None
    if tokens:
        if tokens.pop(0) != '名片':
            raise PublicQueryError('invalid_arguments', PLAYER_HELP)
        card = True
        if tokens:
            match = re.fullmatch(r'页([1-9][0-9]{0,2})', tokens.pop(0))
            if not match:
                raise PublicQueryError('invalid_arguments', PLAYER_HELP)
            page = int(match[1])
    if tokens:
        raise PublicQueryError('invalid_arguments', PLAYER_HELP)
    return PlayerRequest(str(int(ident)), region, card, page)


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


def execute_arena(query, repository, *, now=None):
    try:
        request = parse_arena(query)
        snapshot = repository.public_queries.arena(request.region)
        catalog = json.loads(snapshot.body)
        images=catalog.get('images',{})
        source_snapshot=repository.capture_catalog().source_snapshot
        def mission_icons(rule):
            if not rule:return ()
            attribute=repository.public_queries.arena_attribute(request.region,rule.get('musicType'),source_snapshot,snapshot.release)
            return ((attribute,) if attribute else ())+tuple(images.get('missions',{}).get(str(value),'') for value in rule.get('missions',[]))
        now = (time.time() if now is None else now) * 1000
        seasons = sorted(catalog['entries'].values(), key=lambda s: s['startAt'])
        season = (next((s for s in seasons if s['seasonId'] == request.season), None) if request.season else
                  next((s for s in seasons if inside(s, now)), None) or
                  next((s for s in reversed(seasons) if s['startAt'] <= now), None) or (seasons[0] if seasons else None))
        if season is None:
            raise PublicQueryError('not_found', '该资料服没有对应赛季。')
        leagues = [b for b in season['leagues'] if request.band is None or b['bandId'] == request.band]
        if request.band is not None and not leagues:
            raise PublicQueryError('not_found', '该赛季未收录此乐队联赛。')
        rows = [InfoRow('赛季时间', (f"开始：{stamp(season['startAt'])}", f"结束：{stamp(season['endAt'])}",
                     '进行中' if inside(season, now) else '尚未开始' if now < season['startAt'] else '已结束'))]
        if request.mode in {'榜单', '人数'} and request.band is None:
            return InfoAnswer('请指定乐队：\n' + '\n'.join(
                f'/查竞技场 {"hk" if request.region == "tw" else request.region} {name} {request.mode}' for name in BANDS.values()), 'invalid_arguments')
        for league in leagues:
            name = localized(league['name'])
            rules = sorted(league['rules'], key=lambda r: r['startAt'])
            rule = next((r for r in reversed(rules) if inside(r, now)), None)
            if request.mode == '总览':
                upcoming = next((r for r in rules if r['startAt'] > now), None)
                rows.append(InfoRow(name, (rule_text(rule) if rule else '当前无生效轮换',
                    '下次轮换：' + (stamp(upcoming['startAt']) if upcoming else '本赛季无后续轮换')), image_url=league.get('image', ''),icons=mission_icons(rule)))
            elif request.mode == '日程':
                rows.extend(InfoRow(name, (stamp(r['startAt']) + ' — ' + stamp(r['endAt']), rule_text(r)),icons=mission_icons(r)) for r in rules)
            elif request.mode == '曲池':
                rows.extend(InfoRow(localized(s['musicTitle']), (f"歌曲 ID {s['musicId']}",
                    rule_text(rule or {}, s)), image_url=s.get('jacketThumbUrl', ''), command=f"/查曲 {s['musicId']}")
                    for s in league['songs'] if s.get('arenaPublishedAt', 0) <= now)
            elif request.mode == '段位':
                rows.extend(InfoRow(name + ' · ' + localized(r['name']), (f"积分门槛：{r['points']}",
                    f"下赛季起始积分：{r['resetPoints']}", '开放：' + (stamp(r['startAt']) if r['startAt'] else '赛季开始')),
                    image_url=images.get('ranks',{}).get(str(r.get('rank')),'')) for r in league['ranks'])
            elif request.mode == '奖励':
                ranks = {r['id']: localized(r['name']) for r in league['ranks']}
                for key, title in (('dailyRewards', '每日'), ('promotionRewards', '晋级'), ('seasonRewards', '赛季'), ('rankingRewards', '名次')):
                    for reward in league.get(key, season.get(key, [])):
                        if reward.get('leagueId') not in (0, league['id']):
                            continue
                        rows.append(InfoRow(name + ' · ' + title, (localized(reward['name']) + f" ×{reward['count']}",
                            ranks.get(reward.get('rankId'), '') or f"区间：{reward.get('from')}—{reward.get('to')}"),image_url=reward.get('image','')))
            else:
                thresholds = tuple(sorted({r['points'] for r in league['ranks']})) if request.mode == '人数' else ()
                board = json.loads(repository.public_queries.board(request, season['seasonId'], thresholds).body)
                if request.mode == '榜单':
                    rows.extend(InfoRow(f"第{r['rank']}名 · {r['name']}", (f"积分：{r['point']}",
                        '公开ID：' + (r.get('profileId') or '未获取')), kind='standing',
                        table=((f"第{r['rank']}名",r['name']),),game_icon={1:'ImageRanking1st.png',2:'ImageRanking2ng.png',3:'ImageRanking3rd.png'}.get(r['rank'],'')) for r in board['rows'] if request.first <= r['rank'] <= request.last)
                else:
                    rows.append(InfoRow('联赛上榜人数', (str(board.get('maxRank')) if board.get('maxRank') is not None else '未获取',)))
                    labels = {r['points']: localized(r['name']) for r in league['ranks']}
                    rank_images={r['points']:images.get('ranks',{}).get(str(r.get('rank')),'') for r in league['ranks']}
                    rows.extend(InfoRow(labels.get(r['threshold'], str(r['threshold'])), (f"人数：{r['count']}",),image_url=rank_images.get(r['threshold'],'')) for r in board['tiers'])
                rows.append(InfoRow('观测时间', (stamp(board.get('observedAtMs')), '来源：' + board.get('provider', '未获取'))))
        return answer((InfoView(f"{LABELS[request.region]} · 竞技场 · {localized(season['title'])}",
            request.mode + ' · 北京时间 UTC+8', rows=tuple(rows), layout='facts', presentation='arena', footer=(f'Haneoka {snapshot.resource} · {snapshot.release}',)),))
    except PublicQueryError as exc:
        return InfoAnswer(str(exc) + (f' 建议等待{exc.retry_after}秒。' if exc.retry_after else ''), exc.status)


def execute_player(query, repository):
    try:
        request = parse_player(query)
        snapshot = repository.public_queries.player(request)
        data = json.loads(snapshot.body)
        profile = data['profile']
        catalog = repository.capture_catalog()
        level = profile.get('level')
        if level is None and type(profile.get('rankExp')) is int:
            source = catalog.source_snapshot or {}
            resource = 'jp' if request.region == 'jp' else 'intl'
            ranks = source.get('documents', {}).get(resource, {}).get('progression', {}).get('playerRanks', [])
            eligible = [r['rank'] for r in ranks if type(r.get('rank')) is int and type(r.get('exp')) is int and r['exp'] <= profile['rankExp']]
            level = max(eligible) if eligible else None
        rows = [InfoRow('公开玩家 ID', (request.profile_id,)),
                InfoRow('等级／经验', (f"等级：{level if level is not None else '未获取'}",
                    f"经验：{profile.get('rankExp') if profile.get('rankExp') is not None else '未获取'}"), kind='metric'),
                InfoRow('获赞', (str(profile.get('totalFavoriteExact') if profile.get('totalFavoriteExact') is not None
                    else profile.get('totalFavorite')) if profile.get('totalFavoriteExact') is not None or profile.get('totalFavorite') is not None else '未获取',), kind='metric')]
        favorite = profile.get('favoriteMemberCard')
        if favorite:
            rows.append(InfoRow('展示角色卡', tuple(f'{label}：{favorite.get(key) if favorite.get(key) is not None else "未获取"}'
                for key, label in (('cardId', '卡牌ID'), ('exp', '经验'), ('awakeCount', '特训'), ('cardRank', '专精'),
                                  ('liveSkillLevel', 'LIVE技能'), ('performanceSkillLevel', '激奏技能'))),
                image_url=repository.public_queries.player_art(request, favorite.get('cardId'), catalog.source_snapshot), image_kind='portrait', kind='showcase',
                game_icon=f"CardRank{favorite['cardRank']}.png" if type(favorite.get('cardRank')) is int and 0<=favorite['cardRank']<=5 else ''))
        card = profile.get('profileCard')
        if card:
            pages = card.get('pages') or [{'page': i + 1, 'imageUrl': url} for i, url in enumerate(card.get('thumbnailUrls', []))]
            rows.append(InfoRow('名片', (card.get('name') or '未命名名片', f'共{len(pages)}页',
                f'/查玩家 {request.profile_id} {"hk" if request.region == "tw" else request.region} 名片')))
        else:
            pages = []
        selected = None
        media = ()
        if request.namecard:
            selected = next((p for p in pages if p['page'] == request.page), None) if request.page else next((p for p in pages if p.get('imageUrl')), None)
            if selected is None:
                raise PublicQueryError('not_found', '该名片页不存在。' if request.page else '未获取公开名片。')
            if not selected.get('imageUrl'):
                raise PublicQueryError('no_results', '该名片页为空。')
            rows = [InfoRow('名片页', (f"第{selected['page']}页",))]
        else:
            selected = next((p for p in pages if p.get('downloadUrl')), None)
        if selected:
            try:
                media = (repository.public_queries.namecard(request, selected),)
            except PublicQueryError as exc:
                rows.append(InfoRow('名片图片', (str(exc),)))
        rows.append(InfoRow('资料时间', (f"来源更新：{stamp(profile.get('lastUpdatedAtMs'))}",
            f"来源采集：{stamp(data.get('fetchedAtMs'))}", *(['来源标记为旧缓存'] if data.get('stale') else []))))
        result = answer((InfoView(f"{LABELS[request.region]} · {profile.get('name') or '未获取昵称'}",
            '公开玩家资料', hero_url=media[0][0] if media else '', rows=tuple(rows), layout='facts', presentation='player', footer=('资料来源：Haneoka',)),))
        return replace(result, captured_images=media,
                       original_image=media[0][1] if request.namecard and media else None)
    except PublicQueryError as exc:
        return InfoAnswer(str(exc) + (f' 建议等待{exc.retry_after}秒。' if exc.retry_after else ''), exc.status)
