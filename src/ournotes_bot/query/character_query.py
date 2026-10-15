# L3
# Input: Character name, verified alias, ID or band and one captured catalog.
# Output: Band-grouped directory or profile with exact membership card counts.
# Pos: Query / Deterministic character information.
# Effects: Reads the established local alias registry; no model or source calls.
import re

from ..data import character_identity, normalize, resolve_character_alias
from .contracts.info import InfoAnswer, InfoRow, InfoView
from .entity_lexicon import information_alias_targets
from .info_query import answer, available, footer, text_value, unavailable

HELP = '/查角色：按乐队列出角色；/查角色 名称、昵称或ID；/查角色 乐队名或乐队=MyGO。'
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
    return {normalize(n) for n in (person.band, *person.band_names.values()) if n}


def _band_members(people, query):
    needle = normalize(query)
    found = tuple(p for p in people if needle in _band_names(p))
    if found:
        return found
    targets = {normalize(target) for kind, target in information_alias_targets(query) if kind == 'band'}
    found = tuple(p for p in people if targets.intersection(_band_names(p)))
    return found or tuple(p for p in people if any(needle in name for name in _band_names(p)))


def _directory(people, catalog, *, candidate=False):
    people = tuple(sorted(people, key=lambda p: (p.band_id if p.band_id is not None else 999, p.order, p.id)))
    bands = {p.band for p in people}
    title = '角色候选' if candidate else '角色目录'
    if len(bands) == 1:
        title += ' · ' + next(iter(bands))
    rows = (InfoRow(_display_name(p), (f'角色 ID {p.id}',), p.face_url or p.image_url,
                    f'/查角色 {p.id}', label=' · '.join(v for v in (p.band if candidate else '',text_value(p.fields.get('bandPart', {}))) if v),
                    group='' if candidate else p.band,
                    image_kind='square' if p.face_url else 'portrait') for p in people)
    return answer((InfoView(title, f'按乐队排列 · 共 {len(people)} 位', rows=tuple(rows), footer=footer(catalog),
                            layout='list' if candidate else 'directory'),))


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
    if person.birthday:
        month, day = person.birthday
        rows.append(InfoRow('生日', (f'{month}月{day}日',),kind='fact'))
    for key, label in FIELD_LABELS.items():
        value = text_value(person.fields.get(key, {}))
        if value:
            (prose if key in ('catchCopy','description') else rows).append(InfoRow(label, (value,),
                kind='prose' if key in ('catchCopy','description') else 'fact'))
    rows.extend(prose)
    rows.extend((InfoRow('成员卡', (_card_count(catalog.cards, person.id),), command=f'/查卡 {display}',kind='links'),
                 InfoRow('留影卡', (_card_count(catalog.support_cards, person.id), '含多人留影卡；每张卡只计一次。'),
                         command=f'/查留影卡 {display}',kind='links')))
    return answer((InfoView(display, f'日服 · {person.band}', person.image_url or person.face_url,
                            tuple(rows), footer(catalog), 'character'),))


def execute_character(query, repository):
    query = query.strip()
    if query and not normalize(query):
        return InfoAnswer(HELP, 'invalid_arguments')
    catalog = repository.capture_catalog()
    if not available(catalog, 'characters'):
        return unavailable('角色')
    people = catalog.characters
    if not people:
        return answer((InfoView('角色目录', '当前资料没有角色记录。', footer=footer(catalog)),), 'no_results')
    if not query:
        return _directory(people, catalog)
    band_filter = re.match(r'^乐队\s*[=＝]\s*(.*)$', query)
    if band_filter:
        term = band_filter[1].strip()
        if not normalize(term):
            return InfoAnswer(HELP, 'invalid_arguments')
        found = _band_members(people, term)
        if found:
            return _directory(found, catalog)
        return answer((InfoView('角色目录', '没有找到这个乐队。', footer=(*footer(catalog), HELP)),), 'no_results')
    needle = normalize(query)
    exact = tuple(p for p in people if needle == str(p.id)) if needle.isdecimal() else tuple(p for p in people if needle in _names(p))
    if exact:
        return _detail(exact[0], catalog) if len(exact) == 1 else _directory(exact, catalog, candidate=True)
    if needle.isdecimal():
        return answer((InfoView('角色目录', '没有找到这个角色 ID。', footer=(*footer(catalog), HELP)),), 'no_results')
    builtin = resolve_character_alias(query)
    if builtin:
        found = tuple(p for p in people if builtin.display in _identities(p))
        if found:
            return _detail(found[0], catalog) if len(found) == 1 else _directory(found, catalog, candidate=True)
    bands = tuple(p for p in people if needle in {normalize(p.band), *(normalize(n) for n in p.band_names.values() if n)})
    if bands:
        return _directory(bands, catalog)
    character_aliases, band_aliases = set(), set()
    for kind, target in information_alias_targets(query):
        if kind == 'character':
            character_aliases.update(p.id for p in people if normalize(target) in _names(p)
                                     or character_identity(target) in _identities(p))
        else:
            band_aliases.update(p.id for p in people if normalize(target) in
                                {normalize(p.band), *(normalize(n) for n in p.band_names.values() if n)})
    if character_aliases or band_aliases:
        found = tuple(p for p in people if p.id in character_aliases | band_aliases)
        if len(found) == 1 and not band_aliases:
            return _detail(found[0], catalog)
        return _directory(found, catalog, candidate=bool(character_aliases))
    found = tuple(p for p in people if any(needle in n for n in _names(p)))
    if not found:
        found = tuple(p for p in people if any(needle in normalize(n) for n in (p.band, *p.band_names.values()) if n))
        if found:
            return _directory(found, catalog)
    if found:
        return _detail(found[0], catalog) if len(found) == 1 else _directory(found, catalog, candidate=True)
    return answer((InfoView('角色目录', '没有找到符合条件的角色。', footer=(*footer(catalog), HELP)),), 'no_results')
