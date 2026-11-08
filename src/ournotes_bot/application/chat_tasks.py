# L3: Short-lived, actor-local research plans and host-observed field coverage.
# Effects: Bounded memory only; delivery ACK publishes a plan, never source facts.
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass, field
import time
import re

from ..query.entity_lexicon import EntityRef, find_anchor
from ..structured_query import QuerySpec, resolve_query


FIELDS = ('summary', 'skills', 'attributes', 'color', 'missions', 'notes', 'level', 'difficulties')
RESOURCES = {'song': 'songs', 'card': 'cards', 'support_card': 'support_cards'}


@dataclass
class ResearchTask:
    goal: str = ''
    requirements: list = field(default_factory=list)
    entities: dict = field(default_factory=dict)
    targets: list = field(default_factory=list)
    readings: dict = field(default_factory=dict)
    knowledge: bool = False
    source_messages: dict = field(default_factory=dict)
    updated: float = 0
    revision: int = 0
    delivery: str = ''
    transition: str = ''

    def coverage(self):
        rows = []
        for item in self.requirements:
            missing = [f'{ref}/{name}' for ref in item['entity_refs'] for name in item['fields']
                       if name not in self.readings.get(ref, ()) and
                       (not item.get('difficulty') or f"{name}:{item['difficulty']}" not in self.readings.get(ref, ()))]
            if item['fields'] and not item['entity_refs']: missing.append('entity')
            if item['knowledge'] and not self.knowledge: missing.append('knowledge')
            rows.append(dict(label=item['label'], complete=not missing, missing=missing))
        status = ('completed' if rows and all(row['complete'] for row in rows) else
                  'partial' if self.readings or self.knowledge else 'waiting')
        return dict(status=status, items=rows)

    def context(self):
        return dict(goal=self.goal, requirements=self.requirements, entities=list(self.entities.values()),
                    targets=[self.entities[ref] for ref in self.targets],
                    coverage=self.coverage(), note='上轮任务记录；对象身份可续用，字段事实本轮重新读取，当前用户修改优先。')


class TaskContexts:
    def __init__(self, *, clock=time.monotonic, window=900, capacity=1024):
        self.clock, self.window, self.capacity = clock, window, capacity
        self.tasks = OrderedDict()
        self.paused = {}
        self.topic_cutoffs = {}
        self.generations = OrderedDict()

    @staticmethod
    def key(request):
        return tuple(request.scope.values()), request.user_id

    def sweep(self):
        now = self.clock()
        for tasks in (self.tasks, self.paused):
            for key, task in list(tasks.items()):
                if now - task.updated >= self.window: tasks.pop(key, None)
        for key, (_, _, updated) in list(self.topic_cutoffs.items()):
            if now - updated >= self.window: self.topic_cutoffs.pop(key, None)

    def topic_cutoff(self, request, valid_source, transition):
        if transition == 'switch': return request.message_id, request.timestamp_ms
        if transition == 'resume': return None
        key = self.key(request)
        cutoff = self.topic_cutoffs.get(key)
        if cutoff is None: return None
        mid, stamp, updated = cutoff
        if self.clock() - updated >= self.window or not valid_source(mid, stamp):
            self.topic_cutoffs.pop(key, None)
            return None
        return mid, stamp

    def begin(self, request, valid_source, *, inherit=True, transition=''):
        self.sweep()
        key = self.key(request)
        source = self.paused if transition == 'resume' and key in self.paused else self.tasks
        previous = source.get(key) if inherit and transition != 'switch' else None
        # ChatStore.read accepts 48 sources. Start a fresh plan after a long
        # exchange instead of silently dropping the provenance of old choices.
        if previous and len(previous.source_messages) >= 40:
            source.pop(key, None); previous = None
        if previous and not all(valid_source(mid, stamp) for mid, stamp in previous.source_messages.items()):
            source.pop(key, None); previous = None
        generation = self.generations.get(key, 0) + 1
        self.generations[key] = generation
        self.generations.move_to_end(key)
        while len(self.generations) > self.capacity:
            old, _ = self.generations.popitem(last=False)
            self.tasks.pop(old, None); self.paused.pop(old, None)
            self.topic_cutoffs.pop(old, None)
        task = deepcopy(previous) if previous else ResearchTask()
        task.revision = generation
        task.transition = transition
        task.readings = {}; task.knowledge = False
        task.source_messages[request.message_id] = request.timestamp_ms
        return task

    def commit(self, request, task, delivery):
        key = self.key(request)
        if self.generations.get(key) != task.revision: return False
        now = self.clock()
        if task.transition == 'switch':
            self.topic_cutoffs[key] = (request.message_id, request.timestamp_ms, now)
            previous = self.tasks.pop(key, None)
            if previous: self.paused[key] = previous
        elif task.transition == 'resume':
            self.topic_cutoffs.pop(key, None)
        elif key in self.topic_cutoffs:
            mid, stamp, updated = self.topic_cutoffs[key]
            if now - updated < self.window: self.topic_cutoffs[key] = (mid, stamp, now)
            else: self.topic_cutoffs.pop(key, None)
        if not task.goal: return False
        if task.transition == 'resume': self.paused.pop(key, None)
        saved = deepcopy(task)
        retained = set(saved.targets) | {ref for item in saved.requirements for ref in item['entity_refs']}
        saved.entities = {ref: identity for ref, identity in saved.entities.items() if ref in retained}
        saved.readings = {ref: values for ref, values in saved.readings.items() if ref in retained}
        saved.updated = now; saved.delivery = delivery
        self.tasks[key] = saved; self.tasks.move_to_end(key)
        while len(self.tasks) > self.capacity: self.tasks.popitem(last=False)
        return True

    def clear(self, scope, user_id=None, *, clear_topic=True):
        scope = tuple(scope.values()) if isinstance(scope, dict) else scope
        for key in list(self.generations):
            if key[0] == scope and (user_id is None or key[1] == user_id):
                self.generations[key] += 1
                self.tasks.pop(key, None); self.paused.pop(key, None)
                if clear_topic: self.topic_cutoffs.pop(key, None)

    def status(self, scope):
        self.sweep()
        scope = tuple(scope.values()) if isinstance(scope, dict) else scope
        statuses = [task.coverage()['status'] for key, task in self.tasks.items() if key[0] == scope]
        return dict(active_tasks=len(statuses), waiting_tasks=statuses.count('waiting'),
                    partial_tasks=statuses.count('partial'), completed_tasks=statuses.count('completed'),
                    window_seconds=self.window)


def task_transition(question):
    text = re.sub(r'^(?:taki(?![a-z0-9_])|立希)[\s，,、：:]*', '', question.strip(), flags=re.I)
    if re.match(r'^(?:我们)?(?:先|来)?(?:换(?:一个|个)?(?:话题|问题)|聊点别的)', text):
        return 'switch'
    if re.match(r'^(?:我们)?(?:先)?(?:回到|接着聊)(?:刚才|上一个|之前)(?:的)?(?:话题|问题)', text):
        return 'resume'
    return ''


def entity_ref(kind, ident):
    return f'{kind}:{ident}'


def explicit_entities(repository, text):
    """Reuse the query lexicon for identities literally named in this request."""
    identities = {}
    support = bool(re.search(r'SNAP|留影卡|支援卡', text, re.I))
    intents = ('song', 'support_card' if support else 'card')
    for part in re.split(r'和|与|跟|、|，|,|\n|比较|对比|换成|改成|\bvs\.?\b', text, flags=re.I)[:24]:
        if not part.strip(): continue
        for intent in intents:
            anchor = find_anchor(intent, part, repository, explicit_card_ids=True)
            if anchor.entity is None or anchor.entity.kind not in RESOURCES: continue
            kind, ident = anchor.entity.kind, anchor.entity.value
            row = next((row for row in getattr(repository, RESOURCES[kind]) if row.id == ident), None)
            if row is not None:
                ref = entity_ref(kind, ident)
                identities[ref] = dict(ref=ref, kind=kind, id=ident, name=row.title)
    return identities if len(identities) <= 4 else {}


def read_entity_fields(repository, identity, fields, difficulty=''):
    """Use the deterministic query entry for an identity already issued by the host."""
    kind, ident = identity['kind'], identity['id']
    values = {}; incomplete = set()
    if kind in ('card', 'support_card'):
        result = resolve_query(QuerySpec(kind, EntityRef(kind, ident)), repository)
        rows = result.catalog.cards if result.catalog else result.cards or result.support_cards
        card = next((row for row in rows if row.id == ident), None)
        if card:
            values['summary'] = dict(id=ident, title=card.title, character=card.character, rarity=card.rarity)
            values['color'] = card.card_type
            if card.catalog.get('source') == 'haneoka' or any((card.performance, card.technic, card.visual)):
                scale = 100 if kind == 'support_card' else 1
                values['attributes'] = dict(performance=card.performance / scale, technic=card.technic / scale,
                    visual=card.visual / scale, unit='percent' if scale == 100 else 'points', state='满级基础值')
            if card.skills:
                values['skills'] = [dict(kind=s.kind, name=s.name, description=s.description,
                    localized=s.localized, levels=card.catalog.get('skill_levels', [])[index]
                    if index < len(card.catalog.get('skill_levels', [])) else []) for index, s in enumerate(card.skills)]
                if not card.catalog.get('skill_index_complete', card.catalog.get('detail_loaded', False)): incomplete.add('skills')
    else:
        for name in fields:
            if name in ('skills', 'attributes'): continue
            spec = QuerySpec('song', EntityRef('song', ident)) if name == 'summary' else QuerySpec(
                'chart', difficulty=difficulty, display_name=str(ident), field=name)
            result = resolve_query(spec, repository)
            if result.status != 'success': continue
            song = next((row for row in result.songs if row.id == ident), None)
            if song is None and result.chart: song = result.chart[0]
            if song is None: continue
            if name == 'summary': values[name] = dict(id=ident, title=song.title, band=song.band)
            elif name in ('level', 'notes'):
                values[name] = {chart.difficulty: (chart.display_level if name == 'level' else chart.notes)
                                for chart in result.chart[1] if name != 'notes' or chart.notes is not None}
                if name == 'notes' and any(chart.notes is None for chart in result.chart[1]): incomplete.add(name)
            elif name == 'difficulties': values[name] = [chart.difficulty for chart in song.charts]
            elif song.traits: values[name] = getattr(song.traits, name)
    selected = {name: values[name] for name in fields if name in values}
    missing = [name for name in fields if name not in selected or name in incomplete]
    return dict(entity_ref=entity_ref(kind, ident), fields=selected, missing_fields=missing,
                difficulty=difficulty,status='ok' if not missing else 'partial' if selected else 'not_available')
