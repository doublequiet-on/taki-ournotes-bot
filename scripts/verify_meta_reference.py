# L3
# Input: 固定模型的必要统计快照与独立上游环境生成的参考 JSON。
# Output: 数值、顺序、前沿与120顺序计数的严格离线核验回执；不一致报错退出。
# Pos: Scripts 的分数表对拍入口；见 L2.md 与 docs/META_OPEN.md。
# Effects/Dependencies: 仅读本地文件并运行原创 Python 求值器；不联网、安装上游运行时或读取密钥。
"""Compare independently generated upstream results, never calculate the oracle here."""
from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import replace
from pathlib import Path

from ournotes_bot.query.meta_model import evaluate, frontier
from ournotes_bot.query.meta_parameters import MetaRequest
from ournotes_bot.sources.moenotes_music_data import MusicSnapshot, project

FRONTEND = "d102787016b0f162bb414093f3cfa00058a9000c"


def verify(data, reference, *, sha=None):
    assert reference["frontend"] == FRONTEND and reference["model"] == data["provenance"]["deck"]["commit"], "reference version"
    if sha:
        assert reference["sha"] == sha, "reference snapshot digest"
    snapshot = MusicSnapshot(project(data), reference["sha"], 1, 1)
    assert snapshot.model_supported, "unverified reference model"
    comparisons = orders = frontiers = 0
    attributes = {"rate": "score", "eff": "eff", "need": "need", "chance": "chance", "perHour": "per_hour",
                  "goal": "goal", "length": "seconds", "density": "density", "notes": "notes", "bpm": "bpm",
                  "bpmMax": "bpm_max", "level": "level", "skip": "skip", "successes": "successes"}
    for case in reference["cases"]:
        params = {**case["params"], "ranks": tuple(case["params"]["ranks"]), "skills": tuple(case["params"]["skills"])}
        request = MetaRequest(**params)
        rows = [evaluate(s, c, snapshot, request) for s in snapshot.data["songs"] for c in s["charts"]]
        by_id = {r.score_id: r for r in rows}
        assert set(by_id) == {r["scoreId"] for r in case["rows"]}, "reference candidate set"
        for ref in case["rows"]:
            row = by_id[ref["scoreId"]]
            assert row.song_id == ref["musicId"], "music/chart identity"
            for key, attr in attributes.items():
                expected, actual = ref.get(key), getattr(row, attr)
                if key == "length" and actual is not None:
                    actual *= 1000
                assert (actual is None and expected is None or actual is not None and expected is not None
                        and abs(actual - expected) <= max(1e-9, abs(expected) * 1e-10)), (row.score_id, key, actual, expected)
                comparisons += 1
            assert row.figures is not None
            for actual, expected in zip((row.figures.base, *row.figures.weights), (ref["base"], *ref["weights"])):
                assert abs(actual - expected) <= max(1e-9, abs(expected) * 1e-10), (row.score_id, "figures")
                comparisons += 1
        for mode in ("efficiency", "event"):
            actual = sorted(k for k, v in frontier(rows, mode).items() if not v)
            assert actual == case["frontier" if mode == "efficiency" else "event_frontier"], (mode, "frontier")
            frontiers += 1
        for mode in ("efficiency", "score", "event", "speed", "level", "notes", "longest", "shortest", "skip"):
            for speed in (("density", "bpm", "bpm_max") if mode == "speed" else ("density",)):
                for direction in ("asc", "desc"):
                    # Metrics are independent of order and pagination. Avoid a
                    # repeated skill-order enumeration merely to project sorts.
                    def key(r):
                        value = {"efficiency": r.eff, "score": r.score, "event": r.goal - r.need * 1e-12 if request.power and r.goal is not None and r.need is not None else r.need if not request.power else None,
                                 "speed": {"density": r.density, "bpm": r.bpm, "bpm_max": r.bpm_max}[speed],
                                 "level": r.level * 100000 + r.notes if r.level is not None and r.notes is not None else None, "notes": r.notes,
                                 "longest": r.seconds, "shortest": r.seconds, "skip": r.skip}[mode]
                        return None if value is None else ((-1 if direction == "desc" else 1) * value, r.score_id)
                    actual = [r.score_id for r in sorted((r for r in rows if key(r) is not None), key=key)]
                    expected = case["ordered"][(speed if mode == "speed" else mode) + "_" + direction]
                    assert actual == expected, (mode, speed, direction, "sort")
                    orders += 1
    return {"cases": len(reference["cases"]), "numeric_comparisons": comparisons,
            "order_lists": orders, "frontier_sets": frontiers, "status": "passed"}


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: verify_meta_reference.py snapshot.json independently-generated-reference.json")
    raw = Path(sys.argv[1]).read_bytes()
    print(json.dumps(verify(json.loads(raw), json.loads(Path(sys.argv[2]).read_bytes()),
                            sha=hashlib.sha256(raw).hexdigest())))


if __name__ == "__main__":
    main()
