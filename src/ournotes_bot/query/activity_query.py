# L3
# Input: Explicit region/current request or JP static event name/ID/history.
# Output: Current/recent activity and static event facts shared by text and images.
# Pos: Query / Deterministic activity information; see L2-2.md.
# Effects: Current requests read event metadata only; no ranking, model or QQ calls.
import time
import math
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from dataclasses import replace

from ..data import normalize
from ..domain.info_catalog import ACTIVITY_ATTRIBUTES
from .entity_lexicon import band_name_variants, character_name_variants, information_alias_entries, information_alias_targets
from .contracts.info import CalendarTime, InfoAnswer, InfoRow, InfoSchedule, InfoView
from .event_cutoff_query import SERVER_ALIASES
from .info_query import (answer, available, card_rows, footer, named_matches,
                         list_view, period, schedule, state, text_value, unavailable)
from ..sources.moenotes_events import SERVERS, SourceError, display_time, zone_label

HELP = (
    '/查活动：日服活动列表；可直接加 ID 2到5、MyGO、红,蓝 等条件\n'
    '/查活动 ID或名称：日服活动详情\n'
    '/查活动 [jp/hk/kr/en] 当前或最近：实时活动\n'
    '/查活动 [jp/intl] 日程/日程开始/下期'
)
RETIRED_SECTIONS = {'加成', '奖励', '任务', '卡池'}
ACTIVITY_TYPE_NAMES = {1: '挑战演出活动'}
ACTIVITY_TYPE_ALIASES = {
    normalize(name): 1 for name in ('挑战演出', '挑战演出活动', '挑戰演出', '挑戰演出活動', '挑战Live', '挑戰Live',
                                   'Challenge Live', 'Challenge Live event',
                                   'チャレンジライブ', 'チャレンジライブイベント')
}
FILTER_SEPARATOR = re.compile(r'[,，、/／]')
POSITIVE_ID = re.compile(r'[1-9][0-9]{0,15}')
FILTER_MARKER = re.compile(
    r'(?<!\S)(乐队|角色|属性|颜色|类型(?:\s*id)?)(?:\s*[=＝]\s*|\s+|(?=[\d+\-−])|$)'
    r'|(?<!\S)([^\s=＝]+)\s*[=＝]\s*', re.I)
DETAIL_HINT = '加成、奖励、任务和关联卡池已合入活动详情。请用 /查活动 2 或 /查活动 当前。'
NUMERIC_EXPRESSION = re.compile(r'[+\-−]?\s*\d+(?:\s*[-−–—~～至到]\s*[+\-−]?\d+|\.\d+)?')
_ID_FIELD = r'(?:活动\s*)?id'
_ID_NUMBER = r'[+\-−]?[0-9]+(?:\.[0-9]+)?'
ID_CONDITION = re.compile(
    rf'(?<!\S)(?:{_ID_FIELD}\s*(?:=\s*)?)?(?:(?P<low>{_ID_NUMBER})\s*[-−–—~～至到]\s*(?P<high>{_ID_NUMBER})'
    rf'|(?P<op>>=|<=|>|<|≥|≤|大于|小于|至少|至多)\s*(?P<bound>{_ID_NUMBER})'
    rf'|(?P<suffix_bound>{_ID_NUMBER})\s*(?P<suffix>以上|以下))(?=\s|$)'
    rf'|(?<!\S){_ID_FIELD}\s*(?:=\s*)?(?P<single>{_ID_NUMBER})(?=\s|$)', re.I)
ID_FIELD_TOKEN = re.compile(rf'(?<!\S){_ID_FIELD}(?=\s|[=<>≥≤\d+\-−]|$)', re.I)
ID_OPERATORS = {'>=': '>=', '≥': '>=', '以上': '>=', '至少': '>=', '<=': '<=', '≤': '<=',
                '以下': '<=', '至多': '<=', '>': '>', '大于': '>', '<': '<', '小于': '<'}


def _type_label(event_type_id):
    name = ACTIVITY_TYPE_NAMES.get(event_type_id)
    if name:
        return f'{name}（类型 ID {event_type_id}）'
    return '活动类型未获取' if event_type_id is None else f'活动类型 ID {event_type_id}（类型名称未确认）'


def detail_rows(record, section='', *, gacha_ids=(), preview_only=False):
    result = []
    if not section and hasattr(record, 'event_type_id'):
        result.append(InfoRow('活动类型与榜制', (
            _type_label(record.event_type_id),
            '开放：' + '、'.join(record.ranking_modes) if record.ranking_modes else '开放榜制未获取')))
    for group in record.details:
        if section and group.name != section:
            continue
        facts = tuple(f for f in group.facts if f.preview) if preview_only else group.facts
        for fact in facts:
            command = f'/查卡池 {fact.gacha_id}' if fact.gacha_id and fact.gacha_id in gacha_ids else ''
            result.append(InfoRow(f'{group.name} · {fact.title}', fact.lines, image_url=fact.image_url,
                                  command=command, kind='bonus' if group.name == '加成' else 'row',
                                  table=fact.table, image_kind=fact.image_kind, group=fact.group,
                                  reference=fact.reference))
        if not group.complete:
            result.append(InfoRow(group.name, ('关联资料不完整，缺失部分尚未获取。',)))
        elif not group.facts:
            result.append(InfoRow(group.name, ('没有已收录条目。',)))
    if not record.details or section and not any(group.name == section for group in record.details):
        result.append(InfoRow('详情资料', ('当前缓存尚未收录该项明细。',)))
    return tuple(result)


def overview_rows(record, related, *, gacha_ids=()):
    def card_id(row):
        return (('成员卡' if row.reference[0]==2 else '留影卡')+f' ID {row.reference[1]}',) if row.reference else ()
    details = detail_rows(record, gacha_ids=gacha_ids)
    def section_rows(rows, title):
        return tuple(row for row in rows if row.title.startswith(title + ' · ') or row.title == title)
    bonus_rows = section_rows(details, '加成')
    reward_rows = section_rows(details, '奖励')
    previews = section_rows(detail_rows(record, '奖励', preview_only=True), '奖励')
    pool_rows = section_rows(details, '卡池')
    grouped_ids = {id(row) for row in (*bonus_rows, *reward_rows, *pool_rows)}
    preview_rows = set(previews)
    bonus_groups={}
    for section in record.details:
        if section.name!='加成':continue
        for fact in section.facts:bonus_groups.setdefault(fact.group or '其他加成',[]).append(fact)
    summaries=[]
    order={'指定成员卡':0,'指定留影卡':1,'指定乐队':2,'指定角色':3,'指定属性':4}
    for role in sorted(bonus_groups,key=lambda role:order.get(role,5)):
        facts=bonus_groups[role]
        values={}
        for fact in facts:
            for label,low,high in fact.bonus_ranges:
                old=values.get(label,(low,high));values[label]=(min(old[0],low),max(old[1],high))
        labels={'成员卡参数加成':'成员参数','留影卡参数加成':'留影参数','活动道具加成':'活动道具'}
        body=' · '.join(labels.get(label,label)+' '+(f'+{low:g}%' if low==high else f'+{low:g}～{high:g}%')
                        for label,(low,high) in values.items()) or '数值未确认'
        if values and any(len(f.bonus_ranges)<len(f.table) for f in facts):body+='（部分数值未获取）'
        pictures=tuple(dict.fromkeys(f.image_url for f in facts if f.image_url and not f.reference))
        summaries.append(InfoRow(role,(body,),icons=pictures,
            children=tuple(InfoRow(f.title) for f in facts)))
    bonus_notes = tuple(value for row in bonus_rows if row.kind != 'bonus' for value in row.body)
    bonuses = (InfoRow('活动加成', ('完整档位见下方。', *bonus_notes), kind='bonus-summary',
                       group='加成', children=tuple(summaries)),) if summaries else bonus_rows
    full_bonuses = tuple(row for row in bonus_rows if row.kind == 'bonus') if summaries else ()
    reward_groups={}
    for row in previews:
        if row.group:
            reward_groups.setdefault((row.group,row.image_kind),[]).append(row)
    rewards=[replace(row, title='活动奖励') for row in previews if not row.group]
    for (label,_),entries in reward_groups.items():
        unique={}
        for row in entries:
            key=(row.reference,row.title,row.image_url,row.body[:2])
            conditions=('；'.join(row.body[2:]),) if row.body[2:] else ()
            if key in unique:unique[key]=replace(unique[key],body=tuple(dict.fromkeys((*unique[key].body,*conditions))))
            else:unique[key]=replace(row,body=(*row.body[:2],*conditions))
        shown={r.reference for r in related if r.kind=='card' and r.reference}
        cards=[r for r in unique.values() if r.reference in shown]
        other=[r for r in unique.values() if r.reference not in shown]
        if cards:rewards.append(InfoRow(label or '活动奖励',tuple(' · '.join((*card_id(r),*r.body)) for r in cards)))
        if other:rewards.append(InfoRow(label or '活动奖励',kind='gallery',
            children=tuple(replace(r,body=(*r.body,*card_id(r))) for r in other)))
    pools=[]
    for row in pool_rows:
        if row.group=='关联卡池':pools.append(replace(row,kind='recruitment'))
        elif row.group=='卡池UP卡':
            row=replace(row,body=(*row.body,*card_id(row)))
            if pools and pools[-1].kind=='gallery':pools[-1]=replace(pools[-1],children=(*pools[-1].children,row))
            else:pools.append(InfoRow('卡池UP卡',kind='gallery',children=(row,)))
        else:pools.append(row)
    return (*tuple(row for row in details if row.title == '活动类型与榜制'), *bonuses,
            *tuple(row for row in related if row.kind != 'song'), *rewards,
            *tuple(row for row in related if row.kind == 'song'), *pools, *full_bonuses,
            *tuple(row for row in reward_rows if row not in preview_rows),
            *tuple(row for row in details if id(row) not in grouped_ids and row.title != '活动类型与榜制'))


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
                  tags=tuple(tags[label] for label in labels),reference=(ref.resource_type,ref.id)) for ref,labels in sorted(cards.items())]
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


def _target_filter(values, kind, variants):
    names = set()
    for value in values:
        names.update(variants(value))
        for group, target in information_alias_targets(value):
            if group == kind:
                names.update(variants(target))
    return names


def _matched_targets(facts, field, names, variants):
    matches = []
    for fact in facts:
        titles = getattr(fact, field)
        if any(variants(title) & names for title in titles):
            matches.append(titles[0])
    return tuple(dict.fromkeys(matches))


def _short_filter_names(catalog):
    bands, characters = set(), set()
    for person in getattr(catalog, 'characters', ()):
        bands.update(name for name in (person.band, *person.band_names.values()) if name)
        characters.update(name for name in (person.name, *person.names.values()) if name)
    for record in catalog.activities:
        for section in record.details:
            if section.name == '加成':
                for fact in section.facts:
                    bands.update(fact.bonus_band_names)
                    characters.update(fact.bonus_character_names)
    variants = {'band': band_name_variants, 'character': character_name_variants}
    known = {
        'band': {variant for name in bands for variant in band_name_variants(name)},
        'character': {variant for name in characters for variant in character_name_variants(name)},
    }
    # An alias is usable only if its target exists in this captured catalog.
    aliases = {'band': set(), 'character': set()}
    for kind, alias, target in information_alias_entries():
        if variants[kind](target) & known[kind]:
            aliases[kind].add(normalize(alias))
    names = {}
    for kind, field in (('band', '乐队'), ('character', '角色')):
        for name in known[kind] | aliases[kind]:
            if name:
                names.setdefault(name, set()).add(field)
    for name in ACTIVITY_ATTRIBUTES.values():
        for spelling in (name, name[0], name.translate(str.maketrans('红蓝绿黄', '紅藍綠黃')),
                         name[0].translate(str.maketrans('红蓝绿黄', '紅藍綠黃'))):
            names.setdefault(spelling, set()).add('属性')
    for name in ACTIVITY_TYPE_ALIASES:
        names.setdefault(name, set()).add('类型')
    return names


def _filter_prefix(words, names, longest, field=None):
    match = None
    for stop in range(1, len(words) + 1):
        value = normalize(' '.join(words[:stop]))
        parts = FILTER_SEPARATOR.split(value)
        if len(parts[-1]) > longest:
            break
        options = [names.get(part, set()) | ({'类型'} if field == '类型' and POSITIVE_ID.fullmatch(part) else set())
                   for part in parts]
        fields = set.intersection(*options)
        if fields and (field is None or field in fields):
            match = (stop, fields)
    return match


def _read_activity_filters(term, catalog, mode):
    if not term or unicodedata.normalize('NFKC', term).isdecimal():
        return term, {}
    needle = normalize(term)
    if mode != 'list' and any(needle == normalize(name) for record in catalog.activities
                             for name in (record.title, *record.names.values()) if name):
        return term, {}
    markers = tuple(FILTER_MARKER.finditer(term))
    if not markers and mode != 'list' and named_matches(catalog.activities, term):
        return term, {}
    if markers and all(marker[1] is None for marker in markers) and mode == 'search':
        return term, {}
    names = _short_filter_names(catalog)
    longest = max(map(len, names), default=0)
    filters = {}
    title = []

    def add(field, value):
        values = tuple(part.strip() for part in FILTER_SEPARATOR.split(value))
        if field in filters or any(not normalize(part) for part in values):
            raise ValueError('每类条件请写一组值，多选用逗号分隔，例如 红,蓝。')
        filters[field] = values

    def read_bare(words, *, allow_title=False):
        while words:
            match = _filter_prefix(words, names, longest)
            if not match:
                if allow_title and not filters and not FILTER_SEPARATOR.search(words[0]):
                    title.append(words[0])
                    words = words[1:]
                    continue
                raise ValueError(f'无法识别筛选条件「{words[0]}」。')
            stop, fields = match
            if len(fields) != 1:
                raise ValueError(f'「{" ".join(words[:stop])}」可指多个条件，请在前面写明乐队、角色或属性。')
            add(next(iter(fields)), ' '.join(words[:stop]))
            words = words[stop:]

    read_bare(term[:markers[0].start()].split() if markers else term.split(), allow_title=True)
    for marker, next_marker in zip(markers, (*markers[1:], None)):
        if marker[1] is None:
            raise ValueError(f'未知筛选条件「{marker[2]}」。')
        field = ''.join(marker[1].casefold().split()).replace('颜色', '属性').replace('类型id', '类型')
        words = term[marker.end():next_marker.start() if next_marker else len(term)].split()
        match = _filter_prefix(words, names, longest, field)
        stop = match[0] if match else len(words)
        value = ' '.join(words[:stop])
        if not match and FILTER_SEPARATOR.search(value):
            raise ValueError(f'无法识别「{value}」中的全部{field}选项。')
        add(field, value)
        read_bare(words[stop:])
    return ' '.join(title), filters


def _read_activity_id(term):
    text = unicodedata.normalize('NFKC', term)
    type_spans = tuple((marker.start(), marker.end()) for marker in FILTER_MARKER.finditer(text)
                       if marker[1] and marker[1].startswith('类型'))
    matches = tuple(match for match in ID_CONDITION.finditer(text)
                    if not any(start <= match.start() <= end for start, end in type_spans))
    if len(matches) > 1:
        raise ValueError('一次查询请指定一个活动 ID、区间或上下限。')
    protected = (*type_spans, *(match.span() for match in matches))
    if any(not any(start <= marker.start() < end for start, end in protected)
           for marker in ID_FIELD_TOKEN.finditer(text)):
        raise ValueError('活动 ID 条件用正整数，例如 ID 2到5 或 ID>=2。')
    if not matches:
        return term, None

    match = matches[0]
    remainder = (text[:match.start()] + ' ' + text[match.end():]).strip()
    if match['low']:
        low, high = match['low'], match['high']
        if not POSITIVE_ID.fullmatch(low) or not POSITIVE_ID.fullmatch(high) or int(low) > int(high):
            raise ValueError('活动 ID 区间需要两个正整数，且起点不能大于终点。')
        return remainder, (int(low), int(high), f'{low}到{high}')
    number = match['bound'] or match['suffix_bound'] or match['single']
    if not POSITIVE_ID.fullmatch(number):
        raise ValueError('活动 ID 条件需要正整数。')
    value = int(number)
    if match['single']:
        return remainder, (value, value, number)
    operator = ID_OPERATORS[match['op'] or match['suffix']]
    lower = value + (operator == '>') if operator.startswith('>') else None
    upper = value - (operator == '<') if operator.startswith('<') else None
    return remainder, (lower, upper, operator + number)


def _invalid_id_term(term):
    literal = unicodedata.normalize('NFKC', term)
    return not literal.isdecimal() and (normalize(term).isdecimal() or NUMERIC_EXPRESSION.fullmatch(literal))


def _static(query, repository, now):
    mode, term = 'search', query
    head, *tail = query.split(None, 1)
    if head in {'列表', '全部', '历史'}:
        mode = 'history' if head == '历史' else 'list'
        term = tail[0] if tail else ''

    try:
        term, id_filter = _read_activity_id(term)
    except ValueError as exc:
        return InfoAnswer(str(exc), 'invalid_arguments')
    if mode == 'search' and not term and id_filter and id_filter[2].isdecimal():
        term, id_filter = id_filter[2], None
    if _invalid_id_term(term):
        return InfoAnswer('活动 ID 请使用完整正整数，例如 /查活动 2。', 'invalid_arguments')
    if term and not normalize(term):
        return InfoAnswer(HELP, 'invalid_arguments')
    catalog = repository.capture_catalog()
    if not available(catalog, 'activities'):
        return unavailable('活动')
    try:
        term, filters = _read_activity_filters(term, catalog, mode)
    except ValueError as exc:
        return InfoAnswer(str(exc) + '\n' + HELP, 'invalid_arguments')
    if _invalid_id_term(term):
        return InfoAnswer('活动 ID 请使用完整正整数，例如 /查活动 2。', 'invalid_arguments')
    if filters or id_filter:
        mode = 'list' if mode == 'search' else mode
    elif mode == 'list' and term:
        return InfoAnswer(HELP, 'invalid_arguments')
    attributes = set()
    colors = {alias: ident for ident, name in ACTIVITY_ATTRIBUTES.items() for alias in (name, name[0])}
    for value in filters.get('属性', ()):
        color = unicodedata.normalize('NFKC', value).translate(str.maketrans('紅藍綠黃', '红蓝绿黄'))
        if color not in colors:
            return InfoAnswer('未知加成属性，可用红色、蓝色、绿色、黄色、紫色。', 'invalid_arguments')
        attributes.add(colors[color])
    if attributes:
        filters['属性'] = tuple(ACTIVITY_ATTRIBUTES[ident] for ident in sorted(attributes))
    type_ids = set()
    for value in filters.get('类型', ()):
        literal = unicodedata.normalize('NFKC', value)
        type_id = int(literal) if POSITIVE_ID.fullmatch(literal) else ACTIVITY_TYPE_ALIASES.get(normalize(value))
        if type_id is None:
            return InfoAnswer('未知活动类型，可用「挑战演出」或活动类型 ID，例如 类型2。', 'invalid_arguments')
        type_ids.add(type_id)
    if type_ids:
        filters['类型'] = tuple(ACTIVITY_TYPE_NAMES.get(ident, f'ID {ident}') for ident in sorted(type_ids))
    band_names = _target_filter(filters.get('乐队', ()), 'band', band_name_variants)
    character_names = _target_filter(filters.get('角色', ()), 'character', character_name_variants)
    records = catalog.activities
    if mode == 'history':
        records = tuple(r for r in records if r.end_at is not None and r.end_at <= now)
    if id_filter:
        lower, upper, label = id_filter
        records = tuple(record for record in records
                        if (lower is None or lower <= record.id) and (upper is None or record.id <= upper))
    if term:
        records = named_matches(records, term)
    matches = {}
    incomplete = 0
    if filters:
        for record in records:
            if type_ids and record.event_type_id is not None and record.event_type_id not in type_ids:
                continue
            bonus = next((group for group in record.details if group.name == '加成'), None)
            facts = bonus.facts if bonus else ()
            bands = _matched_targets(facts, 'bonus_band_names', band_names, band_name_variants)
            characters = _matched_targets(facts, 'bonus_character_names', character_names, character_name_variants)
            matched_colors = attributes.intersection(fact.bonus_attribute for fact in facts)
            unmatched = {field for field, selected, found in (
                ('乐队', band_names, bands), ('角色', character_names, characters),
                ('属性', attributes, matched_colors),
            ) if selected and not found}
            if bonus and bonus.complete and unmatched - bonus.unresolved_filters:
                continue
            if unmatched or (type_ids and record.event_type_id is None):
                incomplete += 1
                continue
            matches[record.id] = tuple(filter(None, (
                '加成乐队 · ' + '、'.join(bands) if bands else '',
                '加成角色 · ' + '、'.join(characters) if characters else '',
                '加成属性 · ' + '、'.join(ACTIVITY_ATTRIBUTES[color] for color in sorted(matched_colors)) if matched_colors else '',
            )))
        records = tuple(record for record in records if record.id in matches)
    records = tuple(sorted(records, key=lambda r: (r.start_at is not None, r.start_at or 0, r.id), reverse=True))
    notes = footer(catalog)
    filter_labels = [f'{key} {"、".join(values)}' for key, values in filters.items()]
    if id_filter:
        filter_labels.insert(0, f'活动 ID {label}')
    filter_text = ' · '.join(filter_labels)
    coverage = f'另有 {incomplete} 期资料不足，无法判断是否匹配。' if incomplete else ''
    if not records:
        message = '没有找到符合条件的活动。'
        if filter_text:
            message = '\n'.join(filter(None, (filter_text, '没有确认匹配的活动。' if incomplete else message, coverage)))
        return answer((InfoView('日服 · 活动资料', message, footer=(*notes, HELP)),), 'no_results')
    if mode == 'search' and len(records) == 1:
        record = records[0]
        rows = [InfoRow(f'活动 ID {record.id}', (*period(record), f'资料状态：{state(record, now)}'),
                        kind='schedule',schedule=schedule(record,now,'活动'))]
        description = text_value(record.description)
        if description:
            rows.append(InfoRow('活动说明', (description,)))
        rows.extend(_related_rows(record, catalog))
        rows = (rows[0], *overview_rows(record, rows[1:], gacha_ids=_jp_gacha_ids(catalog)))
        return answer((InfoView(record.title, '日服 · 活动详情', record.image_url or record.detail_image_url,
                                rows, notes, 'activity-summary', presentation='activity',
                                visual_footer=footer(catalog, diagnostics=False), hero_logo_url=record.logo_url),))
    rows = (InfoRow(r.title, (f'活动 ID {r.id}', *period(r), f'资料状态：{state(r, now)}'),
                    r.image_url, f'/查活动 {r.id}',
                    image_kind='wide', kind='banner', schedule=schedule(r, now, '活动'),
                    children=((InfoRow('活动类型', (_type_label(r.event_type_id) if r.event_type_id in ACTIVITY_TYPE_NAMES
                                                    else f'活动类型 ID {r.event_type_id}',), kind='fact'),)
                              if r.event_type_id is not None else ())
                    + ((InfoRow('命中的加成', matches[r.id], kind='fact'),) if matches.get(r.id) else ())
                    + _related_rows(r, catalog, include_songs=False)) for r in records)
    title = '日服 · 历史活动' if mode == 'history' else '日服 · 活动列表' if mode == 'list' else '日服 · 活动候选'
    count = f'匹配 {len(records)} 期' if filter_text else f'共 {len(records)} 个'
    subtitle = count + ' · 日本时间 UTC+9'
    visual_notes = footer(catalog, diagnostics=False)
    if filter_text:
        subtitle += '\n' + filter_text
        if coverage:
            subtitle += '\n' + coverage
    return answer((list_view(title, rows, subtitle=subtitle, notes=notes,
                            presentation='activity', visual_footer=visual_notes),))


def _jp_gacha_ids(catalog):
    return {str(gacha.id) for gacha in catalog.gachas} if available(catalog, 'gachas') else set()


def _current(server, repository):
    source = repository.event_cutoffs
    try:
        with source.foreground():
            event = source.event(server, source.deadline())
    except SourceError as exc:
        return InfoAnswer({'not_found': '当前没有可用活动资料。', 'pending': '活动资料尚未就绪，请稍后重试。',
                           'rate_limited': '来源限流，请稍后重试。'}.get(exc.code, '活动资料暂不可用，请稍后重试。'),
                          exc.code if exc.code in {'not_found', 'pending', 'rate_limited'} else 'data_unavailable')
    label = {'nowOn': '进行中', 'ended': '已结束', 'upcoming': '尚未开始', 'finished': '已结束'}.get(event.status, event.status)
    rows = [InfoRow(f'{event.title}（活动 ID {event.event_id}）',
                    (f'来源状态：{label}', f'开始：{display_time(event.start_ms, server)}',
                     f'结束：{display_time(event.end_ms, server)}', f'时间按{zone_label(server)}显示。'),
                    kind='schedule',schedule=InfoSchedule(f'活动 ID {event.event_id}',label,
                        display_time(event.start_ms,server),display_time(event.end_ms,server),zone_label(server), stale=event.stale))]
    for song in event.songs:
        # Only the current source response can establish this challenge identity.
        body = (f'歌曲 ID {song.music_id} · 挑战 ID {song.challenge_id}',)
        if not song.enabled or song.collect_status not in {'collecting', 'finalizing', 'archived'}:
            command = ''
            body += ('单曲榜未开放或尚未采集。',)
        else:
            command = f"/查榜线 {'hk' if server == 'tw' else server} 锁定活动{event.event_id} 歌曲ID={song.music_id}"
        rows.append(InfoRow(song.title, body, song.jacket, command, '挑战歌曲', kind='song'))
    if not event.songs:
        rows.append(InfoRow('挑战歌曲', ('当前资料没有挑战歌曲榜。',)))
    notes = list(event.notes)
    visual_notes = list(event.notes)
    hero = event.banner
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
                    hero = match.image_url or event.banner
                    local_songs={str(song.id):song for song in catalog.songs}
                    for index,song in enumerate(event.songs,1):
                        local=local_songs.get(song.music_id)
                        if local and local.jacket_url:rows[index]=replace(rows[index],image_url=local.jacket_url)
                    rows.extend(_related_rows(match, catalog,
                        challenge_song_ids={song.music_id for song in event.songs}))
                    notes.extend(footer(catalog))
                    visual_notes.extend(footer(catalog,diagnostics=False))
                else:
                    note = '补充资料来源：Haneoka intl（已核对活动身份与时间）'
                    notes.append(note)
                    visual_notes.append(note)
                    if catalog.cache_state == 'stale':
                        note = 'intl 明细暂用旧缓存。'
                        notes.append(note)
                        visual_notes.append(note)
                gacha_ids = _jp_gacha_ids(catalog) if server == 'jp' else ()
                rows = (rows[0], *overview_rows(match, rows[1:], gacha_ids=gacha_ids))
    if not matched:
        rows.append(InfoRow('补充资料', ('尚未取得与该服本期活动身份及时间一致的详情资料。',)))
    notes.append('资料来源：' + ('Haneoka（实时观测经 MoeNotes tracker）' if event.source == 'haneoka' else event.source))
    visual_notes.append(notes[-1])
    return answer((InfoView(event.title, subtitle=f'{SERVERS[server][0]} · 实时活动（当前／最近）', hero_url=hero,
                            rows=tuple(rows), footer=tuple(notes), layout='activity-summary', presentation='activity',
                            visual_footer=tuple(visual_notes)),))


# Calendar resource identity is deliberately separate from the four ranking regions.
CALENDAR_SERVERS = {'jp': 'jp', '日服': 'jp', 'intl': 'intl', 'tw': 'intl',
                    '台服': 'intl', '臺服': 'intl', '国际服': 'intl', '國際服': 'intl',
                    'en': 'intl', '英服': 'intl'}


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
    time_status = 'unavailable'
    exact_time = None
    calendar_source_url = None
    active_tie = len(active) > 1
    recent_tie = len(ended) > 1 and ended[0].end_at == ended[1].end_at
    next_tie = len(future) > 1 and future[0].start_at == future[1].start_at
    if mode == '下期':
        if next_tie:
            lines.append('多期活动同时开始，暂不能唯一确定下期。')
        elif upcoming and not unknown:
            lines.append(f'下一期《{upcoming.title}》将于 {stamp(upcoming.start_at)} 开始。')
            time_status = 'exact'
            exact_time = CalendarTime(upcoming.title, upcoming.start_at)
        else:
            lines.append('当前已读取的资料还不能确定下一期开启时间。')
    elif active_tie:
        lines.append('资料中存在时间重叠的活动，暂不能唯一确定本期。')
    elif active:
        current = active[0]
        time_status = 'exact'
        exact_time = CalendarTime(current.title, current.start_at if mode == '日程开始' else current.end_at)
        if mode == '日程开始':
            lines.append(f'《{current.title}》已于 {stamp(current.start_at)} 开始，目前进行中。')
        else:
            lines.append(f'《{current.title}》正在进行，将于 {stamp(current.end_at)} 结束。')
            if catalog.cache_state == 'fresh' and not unknown:
                lines.append('记得及时清 CP，别留到活动结束啦。')
    else:
        if recent_tie:
            lines.append('多期活动同时结束，暂不能唯一确定最近一期。')
        elif recent:
            lines.append(f'最近一期《{recent.title}》已于 {stamp(recent.end_at)} 结束。')
            time_status = 'related'
        else:
            lines.append('当前资料中没有能确认正在进行或最近结束的活动。')
        if next_tie:
            lines.append('多期活动同时开始，暂不能唯一确定下期。')
        elif upcoming and not unknown:
            lines.append(f'下一期《{upcoming.title}》将于 {stamp(upcoming.start_at)} 开始，目前还没开始。')
            time_status = 'related'
        else:
            lines.append('当前已读取的资料还不能确定下一期开启时间。')
    if unknown:
        lines.append('部分活动时间缺失，无法确认完整日程。')
    # A missing future catalog entry may already have a published notice. Only
    # the source adapter can supply a verified explicit body time, never a model.
    if not upcoming and (mode == '下期' or not active):
        notices = getattr(repository, 'activity_announcements', None)
        if notices is not None:
            announced, notice_note = notices.next(server, now)
            if announced:
                evidence_expires_at = announced.expires_at
                time_status = 'exact' if mode == '下期' else 'related'
                if mode == '下期':
                    exact_time = CalendarTime(announced.title, announced.start_at)
                    calendar_source_url = announced.url
                lines = [line for line in lines if '还不能确定下一期开启时间' not in line]
                lines.append(f'公告预告《{announced.title}》将于 {stamp(announced.start_at)} 开始。')
                lines.append(f'[查看活动公告]({announced.url})')
            if notice_note:
                lines.append(notice_note)
    label = '日服（JP）' if server == 'jp' else '国际服（台服） · intl'
    if catalog.cache_state == 'stale':
        lines.append('当前使用旧缓存，时间可能已有调整。')
    text = '\n'.join((label + ' · 静态活动日程', *lines))
    return InfoAnswer(text, evidence_expires_at=evidence_expires_at,
                      calendar_time_status=time_status, calendar_time=exact_time, calendar_source_url=calendar_source_url)


def execute_activity(query, repository, *, now=None):
    query = query.strip()
    if not query:
        query = '列表'
    tokens = query.lower().split()
    if tokens[-1] in RETIRED_SECTIONS:
        return InfoAnswer(DETAIL_HINT, 'invalid_arguments')
    server = 'jp'
    if len(tokens) == 2 and tokens[0] in {'当前', '最近', '日程', '日程开始', '下期'} and (
            tokens[1] in SERVER_ALIASES or tokens[1] in CALENDAR_SERVERS):
        return InfoAnswer('请把区服放在最前面。\n' + HELP, 'invalid_arguments')
    if tokens == ['intl']:
        return InfoAnswer('intl 是静态日程资料区，请用 /查活动 intl 日程。\n' + HELP, 'invalid_arguments')
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
        return _current(server, repository)
    if server != 'jp':
        return InfoAnswer('其他服务器目前只支持当前／最近活动。' + HELP, 'invalid_arguments')
    if not hasattr(repository, 'capture_catalog'):
        return InfoAnswer(HELP, 'invalid_arguments')
    return _static(query, repository, time.time() if now is None else now)
