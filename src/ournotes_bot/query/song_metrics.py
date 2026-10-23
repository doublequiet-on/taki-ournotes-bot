# L3: Deterministic Meta grammar and selection over a captured source snapshot.
# Effects: Injected repository reads; no model, rendering, platform or direct HTTP.
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ..domain.meta_query import (MetaError, MetaFilter, MetaOrder, MetaQuerySpec, MetaResult,
                               MetaRow, MetaCoverage, DIFFICULTIES)
from .entity_lexicon import band_aliases, band_name_variants


BANDS = {1: 'MyGO!!!!!', 2: 'Ave Mujica', 3: '梦限大MewType', 4: 'millsage', 5: '一家Dumb Rock!'}


def parse_meta_question(question, repository):
    text = unicodedata.normalize('NFKC', question).casefold().strip()
    # Card skill durations are not song timing; preserve their established route.
    if re.search(r'成员卡|角色卡|卡牌|留影卡|支援卡|号卡|技能|(?<![a-z])(?:cards?|snap|support)(?![a-z])', text):
        return None
    if not re.search(r'最短|最长|时长|時間|秒|nps|bpm|密度|note.*(?:最多|最少|至少|至多|[<>])|(?:最多|最少).*note|按.*(?:等级|音符)|谱面指标', text):
        return None
    if re.search(r'音频|音源|结算|效率|得分|预测|配队|估计|定数', text):
        raise MetaError('unsupported_field', '这里支持谱面末判定时长、等级、Note 数、NPS 和 BPM；不支持该指标。')
    if re.search(r'台服|港服|韩服|美服|欧服|(?<![a-z])(?:hk|tw|kr|en)(?![a-z])', text):
        raise MetaError('invalid_argument', '歌曲指标按资源区服查询，请指定日服 jp 或国际服 intl。')
    servers = []
    for pattern, server in ((r'日服|(?<![a-z])jp(?![a-z])', 'jp'), (r'国际服|國際服|(?<![a-z])intl(?![a-z])', 'intl')):
        if re.search(pattern, text):
            servers.append(server)
            text = re.sub(pattern, ' ', text)
    if len(servers) > 1:
        raise MetaError('invalid_argument', '请一次查询一个资源区服。')
    text = re.sub(r'^(?:请问|请|帮我|查一下|查询|找出|找一下|看看|\s)+', '', text)
    difficulty = None
    aliases = {'ez':'easy', 'easy':'easy', 'nm':'normal', 'normal':'normal', 'hd':'hard', 'hard':'hard', 'ex':'expert', 'expert':'expert',
               '简单':'easy', '普通':'normal', '困难':'hard', '专家':'expert'}
    diffs = re.findall(r'(?<![a-z])(?:expert|normal|easy|hard|ex|ez|nm|hd)(?![a-z])|简单|普通|困难|专家', text)
    if len(set(aliases[d] for d in diffs)) > 1:
        raise MetaError('invalid_argument', '请一次指定一个难度。')
    if diffs:
        difficulty = aliases[diffs[0]]
        text = re.sub(r'(?<![a-z])(?:expert|normal|easy|hard|ex|ez|nm|hd)(?![a-z])|简单|普通|困难|专家', ' ', text)
    names = {name.casefold(): key for key, name in BANDS.items()}
    names.update({'mygo':1, '夢限大みゅーたいぷ':3, 'mugendai myu-type':3, 'ikka dumb rock!':5})
    for name, target in band_aliases():
        for key, canonical in BANDS.items():
            if band_name_variants(target) & band_name_variants(canonical):
                names[name.casefold()] = key
    bands = []
    # Entity names must be at the start or a token boundary; never infer an unknown band from a title.
    for name in sorted(names, key=len, reverse=True):
        pattern = r'(?<![\w])' + re.escape(name) + r'(?![a-z])'
        match = re.search(pattern, text)
        if match:
            bands.append(names[name])
            text = text[:match.start()] + ' ' + text[match.end():]
    filters, order = [], []
    for match in list(re.finditer(r'(?:歌曲\s*)?id\s*[:=]?\s*([1-9][0-9]{0,15})|(?<!\d)([1-9][0-9]{5})(?!\d)', text)):
        filters.append(MetaFilter('song_id', 'eq', match[1] or match[2]))
    text = re.sub(r'(?:歌曲\s*)?id\s*[:=]?\s*[1-9][0-9]{0,15}|(?<!\d)[1-9][0-9]{5}(?!\d)', ' ', text)
    limits = re.findall(r'前\s*(\d+|[一二两三四五六七八九十]+)\s*(?:首|个|条)?', text)
    text = re.sub(r'前\s*(?:\d+|[一二两三四五六七八九十]+)\s*(?:首|个|条)?', ' ', text)
    def count(value):
        if value.isdigit(): return int(value)
        digits = dict(zip('一二两三四五六七八九', (1,2,2,3,4,5,6,7,8,9)))
        if '十' in value:
            a, b = value.split('十')
            return digits.get(a, 1)*10 + digits.get(b, 0)
        return digits.get(value, 0)
    if len(limits) > 1:
        raise MetaError('invalid_argument', '请只指定一个结果数量。')
    limit = count(limits[0]) if limits else None
    sort_patterns = ((r'最短', 'duration_seconds', 'asc'), (r'最长', 'duration_seconds', 'desc'),
        (r'(?:note\s*数?|音符数?)(?:最多|最高)', 'notes', 'desc'),
        (r'(?:note\s*数?|音符数?)(?:最少|最低)', 'notes', 'asc'),
        (r'(?:nps|密度)(?:最高|最大)', 'nps', 'desc'), (r'(?:nps|密度)(?:最低|最小)', 'nps', 'asc'),
        (r'bpm最高|最高bpm', 'bpm_max', 'desc'), (r'bpm最低|最低bpm', 'bpm_min', 'asc'))
    for pattern, field, direction in sort_patterns:
        pattern = '(?:' + pattern + r')(?!\s*(?:>=|<=|=|至少|至多|不低于|不高于|等于))'
        if re.search(pattern, text):
            order.append(MetaOrder(field, direction)); text = re.sub(pattern, ' ', text)
    fields = {'谱面时长':'duration_seconds','时长':'duration_seconds','等级':'level','lv':'level',
              'note数':'notes','note':'notes','音符数':'notes','nps':'nps','密度':'nps',
              '首bpm':'bpm_first','最低bpm':'bpm_min','最高bpm':'bpm_max','bpm':'bpm_min'}
    for label in sorted(fields, key=len, reverse=True):
        pattern = r'按\s*' + re.escape(label) + r'\s*(升序|降序|从小到大|从大到小)'
        for match in re.finditer(pattern, text):
            order.append(MetaOrder(fields[label], 'asc' if match[1] in ('升序','从小到大') else 'desc'))
        text = re.sub(pattern, ' ', text)
    operator = r'(>=|<=|=|至少|至多|不低于|不高于|等于)'
    for label in sorted(fields, key=len, reverse=True):
        pattern = re.escape(label) + r'\s*' + operator + r'\s*(\d+(?:\.\d+)?)\s*(?:秒|级)?'
        for match in re.finditer(pattern, text):
            op = {'>=':'gte','至少':'gte','不低于':'gte','<=':'lte','至多':'lte','不高于':'lte','=':'eq','等于':'eq'}[match[1]]
            value = float(match[2]); value = int(value) if value.is_integer() else value
            filters.append(MetaFilter(fields[label], op, value))
        text = re.sub(pattern, ' ', text)
    # Natural suffix comparisons, including "EX 27 级以上" and "120 秒以内".
    pattern = r'(\d+(?:\.\d+)?)\s*(秒|级)\s*(以上|以下|以内)'
    for match in re.finditer(pattern, text):
        filters.append(MetaFilter('duration_seconds' if match[2]=='秒' else 'level',
                                 'gte' if match[3]=='以上' else 'lte', float(match[1])))
    text = re.sub(pattern, ' ', text)
    if difficulty:
        pattern = r'(?<!\d)(\d+(?:\.\d+)?)\s*(以上|以下)'
        for match in re.finditer(pattern, text):
            filters.append(MetaFilter('level','gte' if match[2]=='以上' else 'lte',float(match[1])))
        text = re.sub(pattern, ' ', text)
    if limit is None:
        match = re.search(r'(\d+|[一二两三四五六七八九十]+)\s*首', text)
        if match:
            limit = count(match[1]);text = text[:match.start()] + ' ' + text[match.end():]
    # A metric-only detail still requires an explicit difficulty for chart metrics.
    mentioned = {field for label, field in fields.items() if label in text}
    if mentioned - {'duration_seconds'} and not difficulty:
        raise MetaError('invalid_argument', '请指定难度：EASY / NORMAL / HARD / EXPERT（EZ / NM / HD / EX）。')
    text = re.sub('|'.join(re.escape(v) for v in sorted(fields,key=len,reverse=True)), ' ', text)
    text = re.sub(r'请问|帮我|查一下|查询|找出|显示|告诉我|有哪些|是哪一首|是哪首|哪首|哪些|歌曲|谱面指标|谱面|乐队|难度|排序|全部|所有|的|歌|是|多少|和|、|[,，?？。!！\s]', '', text)
    if text:
        raise MetaError('invalid_argument', f'尚不能确认条件“{text[:40]}”。例：家团 EX 27级以上最短的前3首。')
    return MetaQuerySpec(resource_server=servers[0] if servers else 'jp', band_ids=tuple(sorted(set(bands))),
        difficulty=difficulty, filters=tuple(filters), order_by=tuple(order), limit=limit if limit is not None else (1 if order else 10))


def select_meta(spec, snapshot):
    candidates, missing = [], Counter()
    eligible = complete = unknown = 0
    required = set(MetaResult(spec, 'ok').fields) | {o.field for o in spec.order_by}
    for song in snapshot.songs:
        values = {'song_id':song.song_id, 'band_ids':song.band_ids, 'duration_seconds':song.duration_seconds}
        if spec.difficulty:
            values.update(song.charts[DIFFICULTIES.index(spec.difficulty)])
        pending = set()
        excluded = bool(spec.band_ids and song.band_ids is not None and not set(spec.band_ids).intersection(song.band_ids))
        if spec.band_ids and song.band_ids is None:
            pending.add('band_ids')
        for condition in spec.filters:
            value = values.get(condition.field)
            if value is None:
                pending.add(condition.field);continue
            expected = condition.value
            matches = (bool(set(value).intersection(expected)) if condition.field == 'band_ids' else value in expected) if condition.op == 'in' else (
                value == expected if condition.op == 'eq' else value >= expected if condition.op == 'gte' else value <= expected)
            if not matches:
                excluded = True
        if excluded:
            continue
        if pending:
            unknown += 1;missing.update(pending);continue
        eligible += 1
        absent = {f for f in required if values.get(f) is None}
        if absent:
            unknown += 1;missing.update(absent)
        else:
            complete += 1
        candidates.append(MetaRow(song, tuple(values.items())))
    # Stable multi-key sort, unknown always last independently of direction.
    for order in reversed(spec.order_by):
        known = [r for r in candidates if r.value(order.field) is not None]
        absent = [r for r in candidates if r.value(order.field) is None]
        known.sort(key=lambda r: int(r.value(order.field)) if order.field=='song_id' else r.value(order.field),
                   reverse=order.direction=='desc')
        candidates = known + absent
    # No winner if every candidate lacks the ranked metric.
    ranked = tuple(o.field for o in spec.order_by if o.field!='song_id')
    if ranked and not any(all(r.value(f) is not None for f in ranked) for r in candidates):
        candidates = []
    status = 'partial' if unknown else 'ok' if candidates else 'empty'
    return MetaResult(spec, status, snapshot.source, tuple(candidates[:spec.limit]),
                      MetaCoverage(eligible, complete, unknown, tuple(sorted(missing.items()))),
                      source_missing_fields=tuple(sorted(missing)))


def execute_meta(spec, repository):
    try:
        return select_meta(spec, repository.song_metrics.current(spec.resource_server))
    except MetaError as exc:
        return MetaResult(spec, exc.status, warnings=(str(exc),))


def execute_many(specs, repository):
    if not isinstance(specs, (tuple, list)) or not 1 <= len(specs) <= 4 or any(not isinstance(s, MetaQuerySpec) for s in specs):
        raise MetaError('invalid_argument', '一次逻辑查询支持 1–4 个规范查询。')
    snapshots={}
    for server in dict.fromkeys(spec.resource_server for spec in specs):
        try: snapshots[server]=repository.song_metrics.current(server)
        except MetaError as exc: snapshots[server]=exc
    results=[]
    for spec in specs:
        snapshot=snapshots[spec.resource_server]
        results.append(MetaResult(spec,snapshot.status,warnings=(str(snapshot),)) if isinstance(snapshot,MetaError)
                       else select_meta(spec,snapshot))
    return tuple(results)
