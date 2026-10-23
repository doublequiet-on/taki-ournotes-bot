# L3: Immutable song Meta contracts and field registry; no I/O or platform types.
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class Field:
    label: str
    scope: str
    kind: str
    operators: tuple[str, ...]
    sortable: bool = True


FIELDS = MappingProxyType({
    'song_id': Field('歌曲 ID', 'song', 'id', ('eq', 'in')),
    'band_ids': Field('乐队', 'song', 'ids', ('in',), False),
    'duration_seconds': Field('谱面时长（末判定）', 'song', 'number', ('eq', 'gte', 'lte')),
    'level': Field('等级', 'chart', 'number', ('eq', 'gte', 'lte')),
    'notes': Field('Note 数', 'chart', 'integer', ('eq', 'gte', 'lte')),
    'nps': Field('NPS', 'chart', 'number', ('eq', 'gte', 'lte')),
    'bpm_first': Field('首 BPM', 'chart', 'number', ('eq', 'gte', 'lte')),
    'bpm_min': Field('最低 BPM', 'chart', 'number', ('eq', 'gte', 'lte')),
    'bpm_max': Field('最高 BPM', 'chart', 'number', ('eq', 'gte', 'lte')),
})
DIFFICULTIES = ('easy', 'normal', 'hard', 'expert')


class MetaError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class MetaFilter:
    field: str
    op: str
    value: str | float | int | tuple


@dataclass(frozen=True)
class MetaOrder:
    field: str
    direction: str = 'asc'


@dataclass(frozen=True)
class MetaQuerySpec:
    version: int = 1
    resource_server: str = 'jp'
    entity: str = 'song'
    band_ids: tuple[int, ...] = ()
    difficulty: str | None = None
    filters: tuple[MetaFilter, ...] = ()
    order_by: tuple[MetaOrder, ...] = ()
    limit: int = 10

    def __post_init__(self):
        if (type(self.version) is not int or self.version != 1 or self.entity != 'song'
                or self.resource_server not in ('jp', 'intl') or self.difficulty not in (None, *DIFFICULTIES)
                or type(self.limit) is not int or not 1 <= self.limit <= 50):
            raise MetaError('invalid_argument', '区服支持 jp / intl，难度支持 EASY / NORMAL / HARD / EXPERT，数量为 1–50。')
        if (not isinstance(self.band_ids, tuple) or len(self.band_ids) > 100
                or any(type(v) is not int or v <= 0 for v in self.band_ids)
                or not isinstance(self.filters, tuple) or len(self.filters) > 8
                or not isinstance(self.order_by, tuple) or len(self.order_by) > 3):
            raise MetaError('invalid_argument', '最多 8 个条件、3 个排序项（含 ID 稳定排序）。')
        for condition in self.filters:
            if not isinstance(condition, MetaFilter) or condition.field not in FIELDS:
                raise MetaError('unsupported_field', '不支持该查询字段。')
            field = FIELDS[condition.field]
            if condition.op not in field.operators:
                raise MetaError('invalid_argument', '字段不支持该比较方式。')
            values = condition.value if condition.op == 'in' else (condition.value,)
            if not isinstance(values, tuple) or not 1 <= len(values) <= 100:
                raise MetaError('invalid_argument', 'in 需要非空的有界值列表。')
            for value in values:
                valid = (isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,15}', value)
                         if field.kind == 'id' else type(value) is int and value > 0
                         if field.kind in ('integer', 'ids') else
                         type(value) in (int, float) and math.isfinite(value) and value >= 0)
                if not valid:
                    raise MetaError('invalid_argument', '字段值类型或范围不正确。')
        seen = set()
        for order in self.order_by:
            if not isinstance(order, MetaOrder) or order.field not in FIELDS or not FIELDS[order.field].sortable:
                raise MetaError('unsupported_field', '不支持该排序字段。')
            if order.direction not in ('asc', 'desc') or order.field in seen:
                raise MetaError('invalid_argument', '排序方向或重复字段不正确。')
            seen.add(order.field)
        if 'song_id' in seen:
            if self.order_by[-1] != MetaOrder('song_id'):
                raise MetaError('invalid_argument', '歌曲 ID 必须是最后一项升序排序。')
        elif len(self.order_by) == 3:
            raise MetaError('invalid_argument', '请为歌曲 ID 稳定排序保留一项。')
        else:
            object.__setattr__(self, 'order_by', self.order_by + (MetaOrder('song_id'),))
        used = {c.field for c in self.filters} | {o.field for o in self.order_by}
        if self.difficulty is None and any(FIELDS[f].scope == 'chart' for f in used):
            raise MetaError('invalid_argument', '请指定难度：EASY / NORMAL / HARD / EXPERT（EZ / NM / HD / EX）。')

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) - set(cls.__dataclass_fields__):
            raise MetaError('invalid_argument', '未知的 Meta 查询参数。')
        data = dict(value)
        try:
            for key, record in (('filters', MetaFilter), ('order_by', MetaOrder)):
                items = data.get(key, [])
                if not isinstance(items, list):
                    raise TypeError(key)
                data[key] = tuple(record(**({**item, 'value': tuple(item['value'])}
                    if key == 'filters' and isinstance(item.get('value'), list) else item)) for item in items)
            if 'band_ids' in data:
                if not isinstance(data['band_ids'], list):
                    raise TypeError('band_ids')
                data['band_ids'] = tuple(data['band_ids'])
            return cls(**data)
        except (TypeError, KeyError, AttributeError) as exc:
            raise MetaError('invalid_argument', 'Meta 查询参数结构不正确。') from exc


@dataclass(frozen=True)
class MetaSource:
    server: str
    release_id: str
    source_id: str
    checked_at: float


@dataclass(frozen=True)
class MetaSong:
    song_id: str
    title: str
    band_ids: tuple[int, ...] | None
    band: str
    jacket: str
    duration_seconds: float | None
    charts: tuple[tuple[tuple[str, float | int | None], ...], ...]
    published_at: tuple = ()
    warnings: tuple[str, ...] = ()
    audio_duration_ms: float | None = None


@dataclass(frozen=True)
class MetaSnapshot:
    source: MetaSource
    songs: tuple[MetaSong, ...]


@dataclass(frozen=True)
class MetaRow:
    song: MetaSong
    values: tuple[tuple[str, object], ...]

    def value(self, field):
        return dict(self.values).get(field)


@dataclass(frozen=True)
class MetaCoverage:
    eligible: int = 0
    complete: int = 0
    unknown: int = 0
    missing_fields: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class MetaResult:
    spec: MetaQuerySpec
    status: str
    source: MetaSource | None = None
    rows: tuple[MetaRow, ...] = ()
    coverage: MetaCoverage = MetaCoverage()
    warnings: tuple[str, ...] = ()
    source_missing_fields: tuple[str, ...] = ()

    @property
    def fields(self):
        return tuple(dict.fromkeys(o.field for o in self.spec.order_by if o.field != 'song_id')) + tuple(
            f for f in ('duration_seconds', 'level', 'notes', 'nps', 'bpm_first', 'bpm_min', 'bpm_max')
            if f not in {o.field for o in self.spec.order_by} and (f == 'duration_seconds' or self.spec.difficulty))

    def text(self, start=0, stop=None):
        if self.source is None:
            return '\n'.join(self.warnings) or '歌曲指标来源暂不可用，请稍后重试。'
        source = self.source
        heading = ('已知数据中 · ' if self.status == 'partial' else '') + ('日服' if source.server == 'jp' else '国际服')
        if self.spec.difficulty:
            heading += ' · ' + self.spec.difficulty.upper()
        output = [heading]
        for index, row in enumerate(self.rows[start:stop], start + 1):
            output.append(f'{index}. {row.song.title} · ID {row.song.song_id}\n' + ' · '.join(
                f'{FIELDS[f].label} {format_value(f, row.value(f))}' for f in self.fields))
        if not self.rows:
            output.append('没有符合全部条件的歌曲。' if self.status == 'empty' else '资料存在缺口，暂不能确定结果。')
        c = self.coverage
        output.append(f'覆盖：符合条件 {c.eligible} 首，指标完整 {c.complete} 首，待确认 {c.unknown} 首。')
        if c.missing_fields:
            output.append('缺失：' + '、'.join(f'{FIELDS[f].label} {n}' for f, n in c.missing_fields))
        output.extend(self.warnings)
        output.append(f'Haneoka · {source.server} · {source.release_id}')
        return '\n'.join(output)


def format_value(field, value):
    if value is None:
        return '未知'
    if field == 'duration_seconds':
        return f'{value:.3f}'.rstrip('0').rstrip('.') + ' 秒'
    return f'{value:g}' if type(value) in (int, float) else str(value)
