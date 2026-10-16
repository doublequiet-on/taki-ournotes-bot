# L3
# Input: Explicit gacha query and one captured JP information catalog.
# Output: Current/history selections and UP card facts shared by text and images.
# Pos: Query / Deterministic gacha information.
# Effects: None; no model, source fetch, account or QQ calls.
import time

from ..data import normalize
from .contracts.info import InfoAnswer, InfoRow, InfoView
from .info_query import (answer, available, card_rows, footer, named_matches, list_view,
                         period, schedule, state, text_value, unavailable)

HELP = '/查卡池：日服当前卡池资料；/查卡池 ID或名称；/查卡池 历史 [名称]。'


def execute_gacha(query, repository, *, now=None):
    query = query.strip()
    mode, term = 'search', query
    if query in {'', '当前', '列表'}:
        mode, term = 'current', ''
    elif query == '历史' or query.startswith('历史 '):
        mode, term = 'history', query[2:].strip()
    if term and not normalize(term):
        return InfoAnswer(HELP, 'invalid_arguments')
    catalog = repository.capture_catalog()
    if not available(catalog, 'gachas'):
        return unavailable('卡池')
    now = time.time() if now is None else now
    records = catalog.gachas
    if mode == 'current':
        records = tuple(r for r in records if r.start_at is not None and r.end_at is not None and r.start_at <= now < r.end_at)
    elif mode == 'history':
        records = tuple(r for r in records if r.end_at is not None and r.end_at <= now)
    if term:
        records = named_matches(records, term)
    records = tuple(sorted(records, key=lambda r: (r.end_at if r.end_at is not None else float('inf'), r.id), reverse=mode == 'history'))
    notes = (*footer(catalog), '部分卡池有账号或展示条件；时间窗内不代表所有玩家当前可抽。')
    visual_notes = (*footer(catalog, diagnostics=False), notes[-1])
    if not records:
        label = '当前时间窗内没有卡池资料。' if mode == 'current' else '没有找到符合条件的卡池。'
        if mode == 'current' and any(r.start_at is None or r.end_at is None for r in catalog.gachas):
            label += '部分卡池开放时间未确认，可按 ID 或名称查询。'
        return answer((InfoView('日服 · 卡池', label, footer=(*notes, HELP)),), 'no_results')
    if mode == 'search' and len(records) == 1:
        record = records[0]
        rows = [InfoRow(f'卡池 ID {record.id}', (*period(record), f'资料状态：{state(record, now)}'),
                        kind='schedule', schedule=schedule(record, now, '卡池'))]
        description = text_value(record.description)
        if description:
            rows.append(InfoRow('卡池说明', (description,)))
        if record.display_condition not in (None, 0):
            rows.append(InfoRow('展示条件', ('该卡池有展示条件；是否符合条件请以游戏内为准。',)))
        rows.extend(card_rows(record.pickup_cards, catalog, 'UP'))
        if record.pickup_cards and 'pickup_cards' not in record.available_relations:
            rows.append(InfoRow('UP 卡资料说明', ('部分 UP 标记未确认；这里只列已明确标记的 UP 卡。',)))
        elif not record.pickup_cards:
            message = '当前资料没有标记 UP 卡。' if 'pickup_cards' in record.available_relations else '当前资料未提供 UP 卡关联。'
            rows.append(InfoRow('UP 卡', (message,)))
        return answer((InfoView(record.title, '日服 · 卡池详情', record.image_url,
                                tuple(rows), notes, 'detail', presentation='gacha', visual_footer=visual_notes),))
    title = '日服 · 历史卡池' if mode == 'history' else '日服 · 当前卡池' if mode == 'current' else '日服 · 卡池候选'
    rows = (InfoRow(r.title, (f'卡池 ID {r.id}', *period(r), f'资料状态：{state(r, now)}'),
                    r.image_url, f'/查卡池 {r.id}', image_kind='wide',kind='banner',schedule=schedule(r,now,'卡池'),
                    children=card_rows(r.pickup_cards,catalog,'UP')) for r in records)
    return answer((list_view(title, rows, subtitle=f'共 {len(records)} 个 · 日本时间 UTC+9', notes=notes,
                            presentation='gacha',visual_footer=visual_notes),))
