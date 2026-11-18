# L3
# Input: Character name, verified alias, ID or explicit filters and one captured catalog.
# Output: Band-grouped directory or profile with exact membership card counts.
# Pos: Query / Deterministic character information.
# Effects: Reads the established local alias registry; no model or source calls.
import re
import unicodedata

from ..data import CHARACTER_GROUPS, character_identity, normalize, resolve_character_alias
from .contracts.info import InfoAnswer, InfoRow, InfoView
from .entity_lexicon import (information_alias_targets, information_character_aliases,
                             character_name_variants, band_name_variants)
from .info_query import answer, available, footer, text_value, unavailable

HELP = '/查角色：查看目录；/查角色 名称、昵称或ID；/查角色 乐队=MyGO 担当=吉他。'
SINGLE_LATIN_LETTER = re.compile(r'[a-z]')
FILTER_FIELD = re.compile(r'(?:^|\s)(乐队|担当)\s*[=＝]')
PARTS = {
    normalize(term): label
    for label, terms in {
        '主唱': ('vo', 'vocal', '主唱', 'ボーカル'),
        '吉他': ('gt', 'guitar', '吉他', 'ギター'),
        '贝斯': ('ba', 'bass', '贝斯', '貝斯', 'ベース'),
        '鼓': ('dr', 'drums', '鼓', '架子鼓', 'ドラム'),
        '键盘': ('key', 'keyboard', '键盘', '鍵盤', 'キーボード'),
        'DJ': ('dj',),
        '操控': ('mp', 'manipulator', '操控', 'マニピュレーター'),
    }.items()
    for term in terms
}
FIELD_LABELS = {
    'nickname': '别名', 'englishName': '英文名', 'bandPart': '乐队担当',
    'voiceActor': '配音', 'catchCopy': '角色介绍', 'description': '简介',
    'height': '身高', 'constellation': '星座', 'school': '学校',
    'schoolClass': '班级', 'favoriteFood': '喜欢的食物', 'hatedFood': '不喜欢的食物', 'hobby': '兴趣',
}


def _display_name(person):
    return person.name.rsplit(' / ', 1)[-1] if person.band_id == 2 else person.name


def _names(person):
    values = (person.name, *person.names.values(), *person.fields.get('nickname', {}).values(),
              *person.fields.get('englishName', {}).values())
    return {normalize(part) for value in values if value for part in (value, *value.split('/')) if part.strip()}


def _identities(person):
    return {character_identity(n) for n in (person.name, *person.names.values()) if n}


def _band_names(person):
    return {variant for n in (person.band, *person.band_names.values()) if n for variant in band_name_variants(n)}


def _band_members(people, query):
    needle = normalize(query)
    found = tuple(p for p in people if needle in _band_names(p))
    if found:
        return found
    targets = {normalize(target) for kind, target in information_alias_targets(query) if kind == 'band'}
    found = tuple(p for p in people if targets.intersection(_band_names(p)))
    if found or SINGLE_LATIN_LETTER.fullmatch(needle):
        return found
    return tuple(p for p in people if any(needle in name for name in _band_names(p)))


def _directory(people, catalog, *, candidate=False, notes=()):
    people = tuple(sorted(people, key=lambda p: (p.band_id if p.band_id is not None else 999, p.order, p.id)))
    bands = {p.band for p in people}
    title = '角色候选' if candidate else '角色目录'
    if len(bands) == 1:
        title += ' · ' + next(iter(bands))
    rows = (InfoRow(_display_name(p), (f'角色 ID {p.id}',), p.face_url or p.image_url,
                    f'/查角色 {p.id}', label=' · '.join(v for v in (p.band if candidate else '',text_value(p.fields.get('bandPart', {}))) if v),
                    group='' if candidate else p.band,
                    image_kind='square' if p.face_url else 'portrait') for p in people)
    return answer((InfoView(title, f'按乐队排列 · 共 {len(people)} 位', rows=tuple(rows), footer=(*footer(catalog), *notes),
                            layout='list' if candidate else 'directory',presentation='character'),))


def _card_count(records, ident):
    matches = {card.id for card in records if ident in card.catalog.get('character_ids', ())}
    complete = all(card.catalog.get('character_links_complete') is True for card in records)
    return f'{len(matches)} 张' if complete else f'至少 {len(matches)} 张（部分卡牌角色关联未确认）'


def _detail(person, catalog):
    display = _display_name(person)
    rows = [InfoRow('基本资料', (f'角色 ID {person.id}', f'乐队：{person.band}'),kind='fact')]
    prose=[]
    if display != person.name:
        rows.append(InfoRow('艺名', (person.name.rsplit(' / ', 1)[0],),kind='fact'))
    aliases = _common_aliases(person, catalog.characters)
    if aliases:
        rows.append(InfoRow('常用查名', (' / '.join(aliases),), kind='fact'))
    if person.birthday:
        month, day = person.birthday
        rows.append(InfoRow('生日', (f'{month}月{day}日',),kind='fact'))
    for key, label in FIELD_LABELS.items():
        value = text_value(person.fields.get(key, {}))
        if value:
            (prose if key in ('catchCopy','description') else rows).append(InfoRow(label, (value,),
                kind='prose' if key in ('catchCopy','description') else 'fact'))
    rows.extend(prose)
    rows.extend((InfoRow('成员卡', (_card_count(catalog.cards, person.id),), command=f'/查卡 角色ID={person.id}',kind='links'),
                 InfoRow('留影卡', (_card_count(catalog.support_cards, person.id), '含多人留影卡；每张卡只计一次。'),
                          command=f'/查留影卡 角色ID={person.id}',kind='links')))
    return answer((InfoView(display, f'日服 · {person.band}', person.image_url or person.face_url,
                            tuple(rows), footer(catalog), 'character',presentation='character'),))


def _character_target(person, target):
    return normalize(target) in _names(person) or character_identity(target) in _identities(person)


def _match_text(query, needle, people):
    exact = tuple(p for p in people if needle in _names(p))
    if exact:
        return exact, 'character'
    builtin = resolve_character_alias(query)
    if builtin:
        found = tuple(p for p in people if builtin.display in _identities(p))
        if found:
            return found, 'character'
    bands = tuple(p for p in people if needle in {normalize(p.band), *(normalize(n) for n in p.band_names.values() if n)})
    if bands:
        return bands, 'band'
    character_aliases, band_aliases = set(), set()
    for kind, target in information_alias_targets(query):
        if kind == 'character':
            character_aliases.update(p.id for p in people if _character_target(p, target))
        else:
            band_aliases.update(p.id for p in people if normalize(target) in _band_names(p))
    if character_aliases or band_aliases:
        found = tuple(p for p in people if p.id in character_aliases | band_aliases)
        return found, ('mixed' if character_aliases else 'band') if band_aliases else 'character'
    if not SINGLE_LATIN_LETTER.fullmatch(needle):
        found = tuple(p for p in people if any(needle in n for n in _names(p))
                      or (re.fullmatch(r"[\u3400-\u9fff]", needle)
                          and any(needle in n for name in (p.name, *p.names.values()) for n in character_name_variants(name))))
        if found:
            return found, 'character'
        found = tuple(p for p in people if any(needle in normalize(n) for n in (p.band, *p.band_names.values()) if n))
        if found:
            return found, 'band'
    return (), 'character'


def _common_aliases(person, people):
    identities = _identities(person)
    candidates = [alias for identity, _, names in CHARACTER_GROUPS if identity in identities for alias in names]
    candidates.extend(alias for alias, target in information_character_aliases() if _character_target(person, target))
    seen = _names(person)
    result = []
    for alias in candidates:
        needle = normalize(alias)
        if needle in seen or needle.isdecimal() or len(alias.splitlines()) != 1 or FILTER_FIELD.match(alias.strip()):
            continue
        seen.add(needle)
        if _match_text(alias, needle, people) == ((person,), 'character'):
            result.append(alias)
            if len(result) == 5:
                break
    return tuple(result)


def _no_results(catalog, message, *, notes=()):
    return answer((InfoView('角色目录', message, footer=(*footer(catalog), *notes, HELP)),), 'no_results')


def _part(term):
    return PARTS.get(normalize(term).rstrip('.'))


def _filter_directory(query, people, catalog):
    fields = list(FILTER_FIELD.finditer(query))
    filters = {}
    for index, field in enumerate(fields):
        end = fields[index + 1].start() if index + 1 < len(fields) else len(query)
        value = query[field.end():end].strip()
        if field[1] in filters or not normalize(value):
            return InfoAnswer(HELP, 'invalid_arguments')
        filters[field[1]] = value
    role = None
    if '担当' in filters:
        role = _part(filters['担当'])
        if role is None:
            return InfoAnswer('担当可用主唱、吉他、贝斯、鼓、键盘、DJ 或操控。'+HELP, 'invalid_arguments')
    if '乐队' in filters:
        people = _band_members(people, filters['乐队'])
        if not people:
            return _no_results(catalog, '没有找到这个乐队。')
    if role is None:
        return _directory(people, catalog)
    found = []
    missing = False
    for person in people:
        roles = {_part(part) for value in person.fields.get('bandPart', {}).values() if value
                 for part in unicodedata.normalize('NFKC', value).split('&')}
        missing |= not roles or None in roles
        if role in roles:
            found.append(person)
    notes = ('部分角色的乐队担当未确认，结果仅含已确认的角色。',) if missing else ()
    if found:
        return _directory(found, catalog, notes=notes)
    return _no_results(catalog, '没有找到已确认符合该担当的角色。', notes=notes)


def execute_character(query, repository):
    query = query.strip()
    needle = normalize(query)
    if query and (not needle or (needle.isdecimal() and not unicodedata.normalize('NFKC', query).isdecimal())):
        return InfoAnswer(HELP, 'invalid_arguments')
    catalog = repository.capture_catalog()
    if not available(catalog, 'characters'):
        return unavailable('角色')
    people = catalog.characters
    if not people:
        return answer((InfoView('角色目录', '当前资料没有角色记录。', footer=footer(catalog)),), 'no_results')
    if not query:
        return _directory(people, catalog)
    if FILTER_FIELD.match(query):
        return _filter_directory(query, people, catalog)
    if needle.isdecimal():
        found = tuple(p for p in people if needle == str(p.id))
        if not found:
            return _no_results(catalog, '没有找到这个角色 ID。')
        kind = 'character'
    else:
        found, kind = _match_text(query, needle, people)
    if not found:
        return _no_results(catalog, '没有找到符合条件的角色。')
    if len(found) == 1 and kind == 'character':
        return _detail(found[0], catalog)
    return _directory(found, catalog, candidate=kind != 'band')
