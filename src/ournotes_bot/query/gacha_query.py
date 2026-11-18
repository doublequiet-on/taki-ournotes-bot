# L3
# Input: Explicit gacha query and one captured JP information catalog.
# Output: Catalog selections, UP cards and confirmed activity links for text and images.
# Pos: Query / Deterministic gacha information.
# Effects: None; no model, source fetch, account or QQ calls.
import re
import time
import unicodedata

from ..data import normalize
from .contracts.info import InfoAnswer, InfoRow, InfoView
from .info_query import (answer, available, card_rows, footer, named_matches, list_view,
                         period, schedule, state, text_value, unavailable)

HELP = ('/查卡池 [列表/当前/最近/历史]：日服卡池资料\n'
        '/查卡池 ID或名称：卡池详情；例如 /查卡池 12\n'
        '/查卡池 历史 名称、/查卡池 ID 2到12、/查卡池 分类=星钻：筛选卡池')
MODES = {'列表': 'list', '全部': 'list', '当前': 'current', '最近': 'recent', '历史': 'history'}
TITLES = {'list': '卡池列表', 'current': '当前卡池', 'recent': '当前／最近卡池',
          'history': '历史卡池', 'search': '卡池候选'}
CATEGORY_NAMES = {'stars': '星钻招募', 'ticket': '票券招募', 'ad': '广告招募',
                  'pass': '通行证招募', 'bonus': 'T.G.W 特典', 'other': '其他'}
CATEGORY_ALIASES = {normalize(alias): key for key, label in CATEGORY_NAMES.items()
                    for alias in (key, label, label.removesuffix('招募'))}
CATEGORY_FILTER = re.compile(r'^分类\s*[=＝]\s*(?P<value>[^\s]+)(?:\s+(?P<term>.*))?$')
OTHER_SERVERS = {'hk', 'tw', 'cn', 'kr', 'en', 'intl', '国服', '國服', '港服',
                 '台服', '臺服', '韩服', '韓服', '英服', '国际服', '國際服'}
POSITIVE_ID = r'[1-9][0-9]{0,15}'
ID_FILTER = re.compile(
    rf'(?:(?:卡池\s*)?id\s*=?\s*)?(?P<first>{POSITIVE_ID})'
    rf'(?:\s*[-–—~至到]\s*(?P<last>{POSITIVE_ID}))?', re.I)
ID_FIELD = re.compile(r'^(?:卡池\s*)?id(?=\s|[=\d+\-−]|$)', re.I)


def _id_filter(term):
    literal = unicodedata.normalize('NFKC', term)
    match = ID_FILTER.fullmatch(literal)
    if match:
        low = int(match['first'])
        high = int(match['last'] or match['first'])
        if low > high or high > 2**53 - 1:
            raise ValueError('卡池 ID 区间须按从小到大填写，且 ID 须在资料范围内。')
        return low, high, match['last'] is not None
    if (ID_FIELD.match(literal) or normalize(term).isdecimal()
            or re.fullmatch(r'[+\-−]?\s*\d+(?:\.\d+|\s*[~至到]\s*[+\-−]?\d+)?', literal)):
        raise ValueError('卡池 ID 请用正整数或区间，例如 /查卡池 12 或 /查卡池 ID 2到12。')
    return None


def _pickup_status(record):
    count = len(set(record.pickup_cards))
    if 'pickup_cards' in record.available_relations:
        return f'已确认 UP 卡 {count} 张' if count else '当前资料没有标记 UP 卡。'
    if count:
        return f'已确认 UP 卡 {count} 张，部分 UP 标记未确认。'
    if 'featured_cards' in record.available_relations:
        return '已收录展示卡，UP 标记未确认。'
    return '当前资料未提供 UP 卡关联。'


def _pool_flags(record):
    flags = []
    if record.limited is not None:
        flags.append('限定标记：' + ('是' if record.limited else '否'))
    if record.display_condition not in (None, 0):
        flags.append('有展示条件，资格以游戏内为准。')
    return tuple(flags)


def _related_activities(record, catalog):
    if not available(catalog, 'activities'):
        return ()
    needle = str(record.id)
    return tuple(InfoRow(f'关联活动 · {event.title}', (f'活动 ID {event.id}', *period(event)),
                         command=f'/查活动 {event.id}', kind='links')
                 for event in catalog.activities
                 if any(fact.gacha_id == needle for section in event.details if section.name == '卡池'
                        for fact in section.facts))


def _detail(record, catalog, now, notes, visual_notes):
    rows = [InfoRow(f'卡池 ID {record.id}', (*period(record), f'资料状态：{state(record, now)}'),
                    kind='schedule', schedule=schedule(record, now, '卡池'))]
    flags = _pool_flags(record)
    if record.category or flags:
        category = CATEGORY_NAMES.get(record.category)
        facts = (f'卡池分类：{category}',) if category else (
            (f'来源分类：{record.category}',) if record.category else ())
        rows.append(InfoRow('卡池类型与条件', (*facts, *flags)))
    description = text_value(record.description)
    if description:
        rows.append(InfoRow('卡池说明', (description,)))
    if not record.pickup_cards or 'pickup_cards' not in record.available_relations:
        rows.append(InfoRow('UP 卡资料', (_pickup_status(record),)))
    rows.extend(card_rows(record.pickup_cards, catalog, 'UP'))
    rows.extend(_related_activities(record, catalog))
    return answer((InfoView(record.title, '日服 · 卡池详情', record.image_url, tuple(rows),
                            notes, 'detail', presentation='gacha', visual_footer=visual_notes),))


def _list_row(record, now):
    category = CATEGORY_NAMES.get(record.category)
    summary = ' · '.join(filter(None, (category, _pickup_status(record))))
    return InfoRow(record.title, (f'卡池 ID {record.id}', *period(record), f'资料状态：{state(record, now)}'),
                   record.image_url, f'/查卡池 {record.id}', image_kind='wide', kind='banner',
                   schedule=schedule(record, now, '卡池'),
                   children=(InfoRow('分类与 UP', (summary, *_pool_flags(record)), kind='fact'),))


def execute_gacha(query, repository, *, now=None):
    tokens = query.strip().split(None, 1)
    region = unicodedata.normalize('NFKC', tokens[0]).casefold() if tokens else ''
    if region in OTHER_SERVERS:
        return InfoAnswer('卡池查询目前提供日服资料。\n' + HELP, 'invalid_arguments')
    if region in {'jp', '日服'}:
        tokens = tokens[1].split(None, 1) if len(tokens) == 2 else []
    head = tokens[0] if tokens else '列表'
    mode = MODES.get(head, 'search')
    term = ' '.join(tokens[1:] if head in MODES else tokens)
    if mode == 'recent' and term:
        return InfoAnswer(HELP, 'invalid_arguments')
    category = ''
    if re.match(r'^分类\s*[=＝]', term):
        condition = CATEGORY_FILTER.fullmatch(term)
        category = CATEGORY_ALIASES.get(normalize(condition['value']), '') if condition else ''
        if not category:
            return InfoAnswer('卡池分类可用星钻、票券、广告、通行证、bonus 或其他。', 'invalid_arguments')
        term = condition['term'] or ''
        if mode == 'search':
            mode = 'list'
    try:
        id_filter = _id_filter(term) if term else None
    except ValueError as exc:
        return InfoAnswer(str(exc), 'invalid_arguments')
    if term and not normalize(term):
        return InfoAnswer(HELP, 'invalid_arguments')

    catalog = repository.capture_catalog()
    if not available(catalog, 'gachas'):
        return unavailable('卡池')
    now = time.time() if now is None else now
    records = catalog.gachas
    if mode in {'current', 'recent'}:
        records = tuple(r for r in records if state(r, now) == '时间窗内')
        if mode == 'recent' and not records:
            ended = tuple(r for r in catalog.gachas if r.end_at is not None and r.end_at <= now)
            latest_end = max((r.end_at for r in ended), default=None)
            records = tuple(r for r in ended if r.end_at == latest_end)
    elif mode == 'history':
        records = tuple(r for r in records if r.end_at is not None and r.end_at <= now)
    if id_filter:
        low, high, is_range = id_filter
        records = tuple(r for r in records if low <= r.id <= high)
        if is_range and mode == 'search':
            mode = 'list'
    unknown_category = 0
    if category:
        unclassified = tuple(r for r in records if r.category not in CATEGORY_NAMES)
        unknown_category = len(named_matches(unclassified, term) if term and not id_filter else unclassified)
        records = tuple(r for r in records if r.category == category)
    if term and not id_filter:
        records = named_matches(records, term)
    records = tuple(sorted(records, key=lambda r: (r.start_at is not None, r.start_at or 0, r.id),
                           reverse=True))
    time_note = '展示条件与实际抽取资格以游戏内为准。'
    notes = (*footer(catalog), time_note)
    visual_notes = (*footer(catalog, diagnostics=False), time_note)
    unknown = sum(r.end_at is None or (mode in {'current', 'recent'} and r.start_at is None)
                  for r in catalog.gachas)
    if mode in {'current', 'recent', 'history'} and unknown:
        coverage = f'另有 {unknown} 个卡池的开放时间资料不完整，可按 ID 或名称查询。'
        notes += (coverage,)
        visual_notes += (coverage,)
    if unknown_category:
        coverage = f'另有 {unknown_category} 个卡池的分类未确认。'
        notes += (coverage,)
        visual_notes += (coverage,)
    if not records:
        label = '当前时间窗内没有符合条件的卡池。' if mode == 'current' else '没有找到符合条件的卡池。'
        return answer((InfoView('日服 · 卡池', label, footer=(*notes, HELP)),), 'no_results')
    if mode == 'search' and len(records) == 1:
        return _detail(records[0], catalog, now, notes, visual_notes)

    rows = (_list_row(record, now) for record in records)
    subtitle = f'共 {len(records)} 个 · 日本时间 UTC+9'
    if category:
        subtitle += f' · {CATEGORY_NAMES[category]}'
    if id_filter and is_range:
        subtitle += f' · ID {low}到{high}'
    return answer((list_view(f'日服 · {TITLES[mode]}', rows, subtitle=subtitle, notes=notes,
                             presentation='gacha', visual_footer=visual_notes),))
