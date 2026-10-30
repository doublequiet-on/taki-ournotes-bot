# L3
# Input: Explicit regions, immutable public requests and injected bounded HTTP.
# Output: Identity-checked immutable Arena/player snapshots and typed failures.
# Pos: Sources / Haneoka public query reads, independent of catalog publication.
# Effects: Anonymous HTTPS reads; bounded memory cache, no collection or credentials.
from collections import OrderedDict
from functools import partial
import json
import math
import re
from threading import RLock
import time
from urllib.parse import urlencode, urlsplit, parse_qs

from ...domain.public_queries import PublicQueryError, PublicSnapshot
from ..errors import ChartDataError
from ..json_validation import decode_json
from .identity import release_identity
from .transport import ORIGIN, public_response
from .catalog_assets import asset_url


def integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise PublicQueryError('invalid_source', '来源整数格式异常。')
    return value


def objects(value):
    if not isinstance(value, list) or len(value) > 20000 or any(not isinstance(row, dict) for row in value):
        raise PublicQueryError('invalid_source', '来源列表格式异常。')
    return value


def localized_value(value):
    if not isinstance(value, (str, list)) or (isinstance(value, list) and
            (len(value) > 10 or any(not isinstance(part, str) for part in value))):
        raise PublicQueryError('invalid_source', '来源名称格式异常。')


def timestamp(value):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)
                              or not 0 <= value <= 253402300799999):
        raise PublicQueryError('invalid_source', '来源时间格式异常。')


def rewards(value):
    for reward in objects(value):
        localized_value(reward.get('name'))
        for key in ('count', 'leagueId', 'rankId', 'from', 'to'):
            integer(reward.get(key))


def arena_document(value):
    for season in value['entries'].values():
        if not isinstance(season, dict):
            raise PublicQueryError('invalid_source', '赛季格式异常。')
        integer(season.get('seasonId'), minimum=1)
        localized_value(season.get('title'))
        start, end = integer(season.get('startAt')), integer(season.get('endAt'))
        if start >= end:
            raise PublicQueryError('invalid_source', '赛季时间异常。')
        timestamp(start)
        timestamp(end)
        for key in ('dailyRewards', 'seasonRewards', 'rankingRewards'):
            rewards(season.get(key, []))
        for league in objects(season.get('leagues')):
            integer(league.get('bandId'), minimum=1)
            integer(league.get('id'), minimum=1)
            localized_value(league.get('name'))
            rewards(league.get('promotionRewards', []))
            for rule in objects(league.get('rules')):
                if integer(rule.get('startAt')) >= integer(rule.get('endAt')):
                    raise PublicQueryError('invalid_source', '轮换时间异常。')
                if not isinstance(rule.get('missions'), list) or len(rule['missions']) != 3:
                    raise PublicQueryError('invalid_source', '轮换任务格式异常。')
                for mission in rule['missions']:
                    integer(mission)
                integer(rule.get('musicType'))
                timestamp(rule['startAt'])
                timestamp(rule['endAt'])
            for song in objects(league.get('songs')):
                integer(song.get('musicId'), minimum=1)
                localized_value(song.get('musicTitle'))
                integer(song.get('musicType'))
                if not isinstance(song.get('arenaMissions'), list) or len(song['arenaMissions']) != 3:
                    raise PublicQueryError('invalid_source', '歌曲激奏任务格式异常。')
                for mission in song['arenaMissions']:
                    integer(mission)
                integer(song.get('arenaPublishedAt'))
                timestamp(song['arenaPublishedAt'])
            for rank in objects(league.get('ranks')):
                localized_value(rank.get('name'))
                for key in ('id', 'points', 'resetPoints', 'startAt'):
                    integer(rank.get(key))


class PublicQueries:
    def __init__(self, *, fetch=None, clock=time.time):
        self.fetch, self.clock = fetch if fetch is not None else partial(public_response, header_retry=False), clock
        self.cache, self.lock = OrderedDict(), RLock()
        self.cooldowns = OrderedDict()

    def read(self, path, *, ttl=60):
        with self.lock:
            cached = self.cache.get(path)
            if cached and 0 <= self.clock() - cached.fetched_at < ttl:
                self.cache.move_to_end(path)
                return cached
            failure = self.cooldowns.get(path)
            if failure and failure[0] > self.clock():
                raise PublicQueryError(failure[1], failure[2], max(1, int(failure[0] - self.clock())))
            try:
                status, body, headers = self.fetch(ORIGIN + path, 4_000_000, 25)
            except (ChartDataError, OSError) as exc:
                raise PublicQueryError('data_unavailable', '公开资料读取失败，请稍后重试。') from exc
            if status != 200:
                retry = headers.get('retry-after', '')
                retry = int(retry) if re.fullmatch(r'[0-9]{1,6}', retry) else None
                code, message = {
                    202: ('pending', '统计尚未就绪，请稍后重新查询。'),
                    404: ('not_found', '该服没有找到对应的公开资料。'),
                    429: ('rate_limited', '来源限流，请稍后重新查询。'),
                }.get(status, ('data_unavailable', '来源暂不可用，请稍后重试。'))
                if retry is not None and status in (202, 429):
                    self.cooldowns[path] = (self.clock() + retry, code, message)
                    self.cooldowns.move_to_end(path)
                    while len(self.cooldowns) > 128:
                        self.cooldowns.popitem(last=False)
                raise PublicQueryError(code, message, retry)
            try:
                value = decode_json(body)
                if not isinstance(value, dict):
                    raise ValueError('Expected object')
            except (ValueError, UnicodeError) as exc:
                raise PublicQueryError('invalid_source', '来源资料格式异常。') from exc
            snapshot = PublicSnapshot(body, self.clock(), path)
            self.cache[path] = snapshot
            self.cooldowns.pop(path, None)
            self.cache.move_to_end(path)
            while len(self.cache) > 128 or sum(len(s.body) for s in self.cache.values()) > 16_000_000:
                self.cache.popitem(last=False)
            return snapshot

    def arena(self, region):
        server = 'jp' if region == 'jp' else 'intl'
        try:
            identity = release_identity(decode_json(self.read(
                f'/api/v1/servers/{server}/release?projection=identity').body), server)
        except ChartDataError as exc:
            raise PublicQueryError('invalid_source', '竞技场资料版本身份异常。') from exc
        path = f'/api/v1/servers/{server}/arena?' + urlencode({'release': identity['releaseId'], 'projection': 3})
        snapshot = self.read(path, ttl=1800)
        value = decode_json(snapshot.body)
        if value.get('schema') != 'haneoka-arena-v1' or value.get('identity') != {
                key: identity[key] for key in ('server', 'releaseId', 'sourceId')}:
            raise PublicQueryError('invalid_source', '竞技场资料版本不一致。')
        if not isinstance(value.get('entries'), dict):
            raise PublicQueryError('invalid_source', '竞技场目录不完整。')
        arena_document(value)
        def artwork(path):
            try:
                return asset_url(urlsplit(path).path, identity['releaseId']) if isinstance(path,str) else ''
            except ValueError:
                return ''
        images=value.get('images',{})
        value['images']={group:{key:artwork(path) for key,path in rows.items()}
                         for group,rows in images.items() if group in {'ranks','missions'} and isinstance(rows,dict)} if isinstance(images,dict) else {}
        for season in value['entries'].values():
            for key in ('dailyRewards','seasonRewards','rankingRewards'):
                for reward in season.get(key,[]):reward['image']=artwork(reward.get('image'))
            for league in season['leagues']:
                for reward in league.get('promotionRewards',[]):reward['image']=artwork(reward.get('image'))
                for row, field in [(league, 'image'), *((s, 'jacketThumbUrl') for s in league['songs'])]:
                    path = row.get(field, '')
                    try:
                        row[field] = asset_url(urlsplit(path).path, identity['releaseId']) if isinstance(path, str) else ''
                    except ValueError:
                        row[field] = ''
        return PublicSnapshot(json.dumps(value, ensure_ascii=False).encode(), snapshot.fetched_at, server, identity['releaseId'])

    def board(self, request, season, thresholds=()):
        operation = 'population' if request.mode == '人数' else 'ranking'
        query = {'thresholds': ','.join(map(str, thresholds))} if thresholds else {'start': request.first}
        path = (f'/api/v1/game/records/{request.region}/arena/seasons/{season}/leagues/{request.band}/'
                + operation + '?' + urlencode(query))
        snapshot = self.read(path, ttl=1800 if thresholds else 60)
        value = decode_json(snapshot.body)
        if (value.get('region') != request.region or str(value.get('seasonId')) != str(season)
                or str(value.get('bandId')) != str(request.band)):
            raise PublicQueryError('invalid_source', '竞技场榜单身份不一致。')
        timestamp(value.get('observedAtMs'))
        if value.get('provider') is not None and not isinstance(value['provider'], str):
            raise PublicQueryError('invalid_source', '榜单来源格式异常。')
        if request.mode == '人数':
            if value.get('maxRank') is not None:
                integer(value['maxRank'])
            tiers = objects(value.get('tiers'))
            if tuple(row.get('threshold') for row in tiers) != thresholds:
                raise PublicQueryError('invalid_source', '段位门槛与请求不一致。')
            for row in tiers:
                integer(row.get('count'))
        else:
            for row in objects(value.get('rows')):
                rank = integer(row.get('rank'), minimum=1)
                integer(row.get('point'))
                if not request.first <= rank < request.first + 100 or not isinstance(row.get('name'), str):
                    raise PublicQueryError('invalid_source', '排名窗口异常。')
                if row.get('profileId') is not None and not isinstance(row['profileId'], str):
                    raise PublicQueryError('invalid_source', '公开玩家ID格式异常。')
        return snapshot

    def player(self, request):
        snapshot = self.read(f'/api/v1/game/records/{request.region}/players/{request.profile_id}')
        value = decode_json(snapshot.body)
        if (value.get('region') != request.region or value.get('profileId') != request.profile_id
                or not isinstance(value.get('profile'), dict)):
            raise PublicQueryError('invalid_source', '玩家资料身份不一致。')
        profile = value['profile']
        for observed in (value.get('fetchedAtMs'), value.get('serverTimeMs'), profile.get('lastUpdatedAtMs')):
            timestamp(observed)
        if profile.get('name') is not None and not isinstance(profile['name'], str):
            raise PublicQueryError('invalid_source', '玩家昵称格式异常。')
        for key in ('level', 'rankExp', 'totalFavorite'):
            if profile.get(key) is not None:
                integer(profile[key])
        favorite = profile.get('favoriteMemberCard')
        if favorite is not None and not isinstance(favorite, dict):
            raise PublicQueryError('invalid_source', '展示卡格式异常。')
        if favorite is not None:
            for key in ('cardId', 'exp', 'awakeCount', 'cardRank', 'liveSkillLevel', 'performanceSkillLevel'):
                if favorite.get(key) is not None:
                    integer(favorite[key], minimum=1 if key == 'cardId' else 0)
        exact = profile.get('totalFavoriteExact')
        if exact is not None and (not isinstance(exact, str) or not re.fullmatch(r'[0-9]{1,19}', exact)):
            raise PublicQueryError('invalid_source', '获赞数格式异常。')
        card = profile.get('profileCard')
        if card is not None:
            if not isinstance(card, dict):
                raise PublicQueryError('invalid_source', '名片格式异常。')
            if card.get('name') is not None and not isinstance(card['name'], str):
                raise PublicQueryError('invalid_source', '名片名称格式异常。')
            if card.get('pages') is not None:
                pages = objects(card['pages'])
                if len({integer(p.get('page'), minimum=1) for p in pages}) != len(pages):
                    raise PublicQueryError('invalid_source', '名片页码重复。')
                for page in pages:
                    for field in ('imageUrl', 'downloadUrl'):
                        if page.get(field) is not None and not isinstance(page[field], str):
                            raise PublicQueryError('invalid_source', '名片图片路径格式异常。')
            elif not isinstance(card.get('thumbnailUrls', []), list) or any(not isinstance(s, str) for s in card.get('thumbnailUrls', [])):
                raise PublicQueryError('invalid_source', '名片预览格式异常。')
        return snapshot

    def player_art(self, request, card_id, snapshot):
        server = 'jp' if request.region == 'jp' else 'intl'
        if not snapshot:
            return ''
        identity = snapshot.get('identities', {}).get(server)
        row = snapshot.get('documents', {}).get(server, {}).get('cards', {}).get(str(card_id))
        if not identity or not row or str(row.get('cardId')) != str(card_id):
            return ''
        images = row.get('images', {})
        path = images.get('thumbnail') if isinstance(images, dict) else None
        if not path:
            return ''
        try:
            return asset_url(path, identity['releaseId'])
        except ValueError:
            return ''

    def arena_attribute(self, region, value, snapshot, release):
        server='jp' if region=='jp' else 'intl'
        if not snapshot or snapshot.get('identities',{}).get(server,{}).get('releaseId')!=release:
            return ''
        path=snapshot.get('documents',{}).get(server,{}).get('ui-marks',{}).get(f'sp_icon_live_music_type_{value}.png')
        if not isinstance(path,str) or not path.startswith('runtime/'):
            return ''
        try:
            return asset_url(f'/runtime/{server}/'+path.removeprefix('runtime/'),release)
        except ValueError:
            return ''

    def namecard(self, request, page):
        path = page.get('downloadUrl')
        if not isinstance(path, str):
            raise PublicQueryError('data_unavailable', '未获取可下载的名片图片。')
        url = urlsplit(path)
        expected = f'/api/v1/game/records/{request.region}/players/{request.profile_id}/cards/{page["page"]}/download'
        query = parse_qs(url.query, keep_blank_values=True)
        file = query.get('file', [])
        if (url.scheme or url.netloc or url.fragment or url.path != expected or set(query) != {'file'}
                or len(file) != 1 or not re.fullmatch(re.escape(request.profile_id) + r'_[A-Za-z0-9_\-]{1,200}', file[0])):
            raise PublicQueryError('invalid_source', '名片图片身份不一致。')
        try:
            status, body, headers = self.fetch(ORIGIN + path, 8_000_000, 20)
        except (ChartDataError, OSError) as exc:
            raise PublicQueryError('data_unavailable', '名片图片暂不可用。') from exc
        if status != 200 or not headers.get('content-type', '').split(';')[0] in {'image/png', 'image/jpeg', 'image/webp'}:
            raise PublicQueryError('data_unavailable', '名片图片暂不可用。')
        return path, body
