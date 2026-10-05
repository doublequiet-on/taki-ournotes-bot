# L3
# Input: 验证快照、MetaRequest、完整比较池。
# Output: 有限模型的单位分数、排行值、活动顺序计数及支配关系；缺值保持 None。
# Pos: Query / Deterministic 的分数表数学求值；见 L2-2.md。
# Effects/Dependencies: 纯 Python 数学，无 I/O、播放器或 QQ；使用许可明确的统计合同。
"""Original finite evaluator of the published ournotes-deck statistical model.

The seed mean is not the game's native expectation. Accuracy interpolation is
approximate; activity means equal-score players, not event-point prediction.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from itertools import permutations

from ..sources.moenotes_music_data import number

EVALUATOR_VERSION = "taki-meta/1"
ORDERS = tuple(permutations(range(5)))
GRADES = ("D", "C", "B", "A", "S", "SS")


@dataclass(frozen=True)
class Figures:
    base: float
    weights: tuple[float, ...]
    seeds: int
    base_range: tuple[float, float]


@dataclass(frozen=True)
class EvaluatedRow:
    song_id: int
    score_id: int
    titles: tuple[str, ...]
    difficulty: str
    level: float | None
    seconds: float | None
    length_source: str
    score: float | None
    eff: float | None
    notes: int | None
    density: float | None
    bpm: float | None
    bpm_max: float | None
    skip: float | None
    need: float | None
    chance: float | None
    successes: int | None
    per_hour: float | None
    goal: float | None
    metric: float | None
    figures: Figures | None
    thresholds: tuple[float | None, ...]
    warnings: tuple[str, ...] = ()


def plain_kind(snapshot):
    if not snapshot.model_supported:
        return None
    kinds = snapshot.data["deck"].get("kinds", [])
    if not isinstance(kinds, list):
        return None
    hits = [k.get("id") for k in kinds if isinstance(k, dict) and k.get("effectType") == 2000
            and k.get("durationMs") == 5000 and k.get("skillTargetIds") == []
            and all(type(k.get(f)) is int and k[f] == 0 for f in
                    ("skillConditionGroup", "skillReleaseConditionGroup", "effectLimitCount",
                     "effectExecuteLimitCount", "effectExecuteLimitResetConditionGroup"))]
    return hits[0] if len(hits) == 1 and type(hits[0]) is int and hits[0] >= 0 else None


def _array(value, size):
    return isinstance(value, list) and len(value) == size and all(number(v) is not None for v in value)


def figures(chart, snapshot, request, *, deadline=None):
    kind = plain_kind(snapshot)
    model = snapshot.data["deck"].get("model")
    power = number(model.get("power"), minimum=1) if isinstance(model, dict) else None
    deck = chart.get("deck", {})
    if "positions" in deck and (type(deck["positions"]) is not int or deck["positions"] != 5):
        return None
    if kind is None or power is None or request.scene == "battle" and deck.get("unplayable"):
        return None
    seeds = deck.get("offSeeds" if request.scene == "free" else "seeds")
    if not isinstance(seeds, list) or not seeds:
        return None
    bases, weight_samples = [], []
    scale = 1 - 0.2 * request.great / 100
    for index, seed in enumerate(seeds):
        if deadline is not None and index % 64 == 0 and time.monotonic() >= deadline:
            raise TimeoutError("meta evaluation deadline")
        w = seed.get("weights")
        score = number(seed.get("score"))
        if not isinstance(w, list) or kind >= len(w) or not _array(w[kind], 5) or score is None:
            return None
        weights = list(w[kind])
        if request.scene == "battle" and (request.just != 100 or request.ranks != (1, 1, 1)):
            ranges, measured = deck.get("ranges"), seed.get("ranges")
            rw = seed.get("rangeWeights")
            if (not isinstance(ranges, list) or len(ranges) != 3 or not isinstance(measured, list) or len(measured) != 3
                    or not isinstance(rw, list) or kind >= len(rw) or not isinstance(rw[kind], list)
                    or len(rw[kind]) != 5 or not all(_array(a, 3) for a in rw[kind])):
                return None
            pct, original, perfect, bonuses = [], [], [], []
            for r, m in zip(ranges, measured):
                if not isinstance(r, dict) or not isinstance(m, dict):
                    return None
                p = r.get("rankBonusPercents")
                sj, bonus = number(m.get("rangeScore")), number(m.get("rankBonus"))
                sp = number(m.get("rangeScorePerfect"))
                if sp is None and r.get("mission") in (1, 2):
                    sp = sj
                if not _array(p, 5) or sj is None or bonus is None or request.just != 100 and sp is None:
                    return None
                pct.append(p)
                original.append(sj)
                perfect.append(sp)
                bonuses.append(bonus)
            just = request.just / 100
            partial = request.just != 100
            remainder = score - sum(bonuses)
            remainder_perfect = remainder
            if partial:
                score_perfect = number(seed.get("scorePerfect"))
                if score_perfect is None:
                    return None
                remainder_perfect = score_perfect - sum(math.trunc(sp * p[0] / 100) for sp, p in zip(perfect, pct))
            ranges_adjusted = [sp + just * (sj - sp) if partial else sj for sp, sj in zip(perfect, original)]
            score = remainder_perfect + just * (remainder - remainder_perfect) if partial else remainder
            score += sum(math.trunc(v * p[rank - 1] / 100) for v, p, rank in zip(ranges_adjusted, pct, request.ranks))
            for k in range(5):
                for i in range(3):
                    selected, initial = pct[i][request.ranks[i] - 1], pct[i][0]
                    ratio = ranges_adjusted[i] / original[i] if original[i] > 0 else 1
                    weights[k] += ((selected - initial) / 100 + (ratio - 1) * (1 + selected / 100)) * rw[kind][k][i]
        bases.append(score * scale / power)
        weight_samples.append(tuple(x * scale for x in weights))
    base = sum(bases) / len(bases)
    means = tuple(sum(w[k] for w in weight_samples) / len(seeds) for k in range(5))
    if not math.isfinite(base) or base < 0 or any(not math.isfinite(w) or w < 0 for w in means):
        return None
    return Figures(base, means, len(seeds), (min(bases), max(bases)))


def length(song, chart, requested):
    bgm = song.get("bgm", {}).get("length", {})
    values = {"bgm": number(bgm.get("durationMs", bgm.get("lengthMs")), minimum=1),
              "chart": number(chart.get("musicLengthMs"), minimum=1)}
    actual = requested if values[requested] is not None else "chart" if requested == "bgm" else "bgm"
    return values[actual], actual


def threshold(song, grade, request):
    ranks = song.get("scoreRanks", [])
    if not isinstance(ranks, list):
        return None
    hits = [r for r in ranks if isinstance(r, dict) and r.get("rank") == grade]
    if len(hits) != 1:
        return None
    v = number(hits[0].get("requiredScore" if request.scene == "free" else "battleRequiredScore"))
    if v is None or request.scene == "free":
        return v
    n = request.people
    return math.trunc(math.sqrt(5 / n) * v * n) / n


def evaluate(song, chart, snapshot, request, *, deadline=None):
    from ..sources.moenotes_music_data import text_values
    f = figures(chart, snapshot, request, deadline=deadline)
    score = f.base + sum(request.skills) / 500 * sum(f.weights) if f else None
    ms, actual = length(song, chart, request.duration)
    seconds = ms / 1000 if ms is not None else None
    per_hour = 3600000 / (ms + request.overhead_ms) if ms is not None else None
    eff = score * 60000 / (ms + request.overhead_ms) if score is not None and ms is not None else None
    notes = chart.get("notes", {}).get("judged")
    notes = notes if type(notes) is int and notes >= 0 else None
    first, last = number(chart.get("firstNoteMs")), number(chart.get("lastJudgedNoteMs"))
    density = notes * 1000 / (last - first) if notes is not None and first is not None and last is not None and last > first else None
    level = number(chart.get("displayLevel", chart.get("level")))
    bpm, bpm_max = number(chart.get("bpm", {}).get("main")), number(chart.get("bpm", {}).get("max"))
    deck = chart.get("deck", {})
    skip = number(deck.get("skip")) if f is not None else None
    thresholds = tuple(threshold(song, g, request) for g in GRADES)
    target = thresholds[GRADES.index(request.target)]
    need = 0 if target == 0 and f is not None else target / score if target is not None and score is not None and score > 0 else None
    count = chance = goal = None
    if request.power and f and target is not None:
        factors = tuple(x / 100 for x in request.skills)
        count = sum(request.power * (f.base + sum(factors[i] * f.weights[p[i]] for i in range(5))) >= target for p in ORDERS)
        chance = count / len(ORDERS)
        goal = chance * per_hour if per_hour is not None else None
    metric = {"efficiency": eff, "score": score, "event": (goal - need * 1e-12 if request.power and goal is not None and need is not None else need if not request.power else None),
              "speed": {"density": density, "bpm": bpm, "bpm_max": bpm_max}[request.speed],
              "level": level * 100000 + notes if level is not None and notes is not None else None,
              "notes": notes, "longest": ms, "shortest": ms, "skip": skip}[request.ranking]
    warnings = (f"时长回退为{'谱面' if actual == 'chart' else 'BGM'}",) if ms is not None and actual != request.duration else ()
    return EvaluatedRow(song["id"], chart["scoreId"], text_values(song["title"]), chart["difficulty"], level,
                        seconds, actual, score, eff, notes, density, bpm, bpm_max, skip, need, chance, count,
                        per_hour, goal, metric, f, thresholds, warnings)


def dominates(a, b, mode):
    if not a.figures or not b.figures or a.seconds is None or b.seconds is None:
        return False
    af, bf = a.figures, b.figures
    strict = False
    if mode == "event":
        if any(v is None for v in (*a.thresholds, *b.thresholds)) or a.seconds > b.seconds:
            return False
        strict = a.seconds < b.seconds
    for skill in (0, 1.5):
        sa, sb = af.base + skill * sum(af.weights), bf.base + skill * sum(bf.weights)
        tolerance = 1e-12 * max(1, abs(sa), abs(sb))
        if mode == "event":
            for ta, tb in zip(a.thresholds, b.thresholds):
                # D's 0 threshold is always attainable; it imposes no score bound.
                if ta == tb == 0:
                    continue
                va, vb = sa / ta if ta else math.inf, sb / tb if tb else math.inf
                tolerance = 1e-12 * max(1, abs(va)) if math.isfinite(va) else 0
                if va < vb - tolerance:
                    return False
                strict |= va > vb + tolerance
        else:
            for va, vb in ((sa, sb), (sa / a.seconds, sb / b.seconds)):
                if va < vb - tolerance:
                    return False
                strict |= va > vb + tolerance
    return strict


def frontier(rows, mode, *, deadline=None):
    result = {}
    for b in rows:
        if b.figures is None or b.seconds is None or mode == "event" and any(t is None for t in b.thresholds):
            continue
        dominated_by = []
        for index, a in enumerate(rows):
            if deadline is not None and index % 64 == 0 and time.monotonic() >= deadline:
                raise TimeoutError("meta frontier deadline")
            if a.score_id != b.score_id and dominates(a, b, mode):
                dominated_by.append(a.score_id)
        result[b.score_id] = tuple(dominated_by)
    return result
