# L3
# Input: One captured catalog and validated information records.
# Output: Shared labels, card references and complete text/image views.
# Pos: Query / Information helpers; no source or platform I/O.
# Effects: None.
from datetime import datetime, timezone, timedelta

from ..data import normalize
from .contracts.info import InfoAnswer, InfoRow, InfoSchedule, InfoView, info_text

JP_ZONE = timezone(timedelta(hours=9))


def text_value(values):
    return values.get('zh') or values.get('ja') or values.get('en') or next((v for v in values.values() if v), '')


def available(catalog, resource):
    return catalog.metadata.get('info_catalog_available', {}).get(resource) is True


def unavailable(label):
    return InfoAnswer(f'{label}资料暂不可用，请稍后重试。', 'data_unavailable')


def moment(value):
    return datetime.fromtimestamp(value, JP_ZONE).strftime('%Y-%m-%d %H:%M') if value is not None else '未确认'


def period(record):
    return (f'开始：{moment(record.start_at)}', f'结束：{moment(record.end_at)}', '时间按日本时间（UTC+9）显示。')


def state(record, now):
    if record.end_at is not None and record.end_at <= now:
        return '已结束'
    if record.start_at is not None and record.start_at > now:
        return '尚未开始'
    if record.start_at is not None and record.end_at is not None and record.start_at <= now < record.end_at:
        return '时间窗内'
    return '开放时间未确认'


def schedule(record, now, kind):
    return InfoSchedule(f'{kind} ID {record.id}', state(record, now), moment(record.start_at), moment(record.end_at))


def footer(catalog, *, diagnostics=True):
    notes = ['资料来源：Haneoka 日服']
    if diagnostics:
        notes.append(f"资料版本：{catalog.metadata.get('data_version', '未确认')}")
    fetched = catalog.metadata.get('cached_at') or catalog.last_successful_sync_at
    if fetched and diagnostics:
        notes.append(f'本机获取：{fetched}')
    if catalog.cache_state == 'stale':
        notes.append('当前使用旧缓存；开放状态仅按该缓存中的时间判断。')
    elif catalog.cache_state == 'unsaved':
        notes.append('本次资料尚未保存到本机缓存。')
    return tuple(notes)


def answer(views, status='success'):
    views = tuple(views)
    return InfoAnswer(info_text(views), status, views)


def list_view(title, rows, *, subtitle='', notes=(), presentation='', visual_footer=None):
    # The renderer owns actual height/byte budgets; queries retain the full list.
    return InfoView(title, subtitle, rows=tuple(rows), footer=tuple(notes),
                    presentation=presentation, visual_footer=visual_footer)


def named_matches(records, query):
    needle = normalize(query)
    if not needle:
        raise ValueError('Information name matching requires a nonempty normalized term')
    if needle.isdecimal():
        return tuple(r for r in records if needle == str(r.id))
    exact = tuple(r for r in records if needle in {normalize(r.title), *(normalize(n) for n in r.names.values() if n)})
    if exact:
        return exact
    return tuple(r for r in records if any(needle in normalize(n) for n in (r.title, *r.names.values()) if n))


def card_rows(refs, catalog, label):
    lookup = {(2, card.id): card for card in catalog.cards}
    lookup.update({(3, card.id): card for card in catalog.support_cards})
    result = []
    seen = set()
    for ref in sorted(refs, key=lambda ref: (ref.resource_type, ref.id)):
        identity = (ref.resource_type, ref.id)
        if identity in seen:
            continue
        seen.add(identity)
        card = lookup.get(identity)
        kind = '成员卡' if ref.resource_type == 2 else '留影卡'
        command = f"/查卡 {ref.id}" if ref.resource_type == 2 else f"/查留影卡 {ref.id}"
        if card is None:
            result.append(InfoRow(f'{kind} ID {ref.id}', ('当前快照缺少卡牌详情。',), command=command,
                                  label=label, kind='card', group=kind,
                                  image_kind='wide' if ref.resource_type == 3 else 'square'))
        else:
            result.append(InfoRow(f'{kind} {card.id} · {card.title}', (card.character,),
                                  card.thumbnail_url, command, label, card.rarity,
                                  image_kind='wide' if ref.resource_type==3 else 'square',
                                  attribute_url=card.catalog.get('attribute_icon',''),kind='card',group=kind,
                                  display_title=card.title))
    return tuple(result)
