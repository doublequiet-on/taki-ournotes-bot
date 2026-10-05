"""Synthetic contract tests: no network, credentials, game assets or model calls."""
import copy
import asyncio
import gzip
import hashlib
import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from itertools import permutations
from pathlib import Path
from unittest.mock import Mock, patch

from ournotes_bot.commands import resolve_command
from ournotes_bot.data import SongRepository
from ournotes_bot.query.efficiency_query import parse_efficiency, execute_efficiency
from ournotes_bot.query.meta_model import evaluate, figures, dominates, ORDERS
from ournotes_bot.query.meta_parameters import MetaRequest
from ournotes_bot.sources.moenotes_music_data import (MusicSnapshot, MusicDataRepository, project, decode_json,
                                                     MODEL_COMMIT, MODEL_FORMAT, FORMAT, TTL, MAX_STALE, fetch_snapshot)
from ournotes_bot.sources.moenotes_events import SourceError


def payload(count=12):
    kind = {"id": 0, "effectType": 2000, "durationMs": 5000, "skillTargetIds": [],
            **{k: 0 for k in ("skillConditionGroup", "skillReleaseConditionGroup", "effectLimitCount",
                              "effectExecuteLimitCount", "effectExecuteLimitResetConditionGroup")}}
    data = {"format": FORMAT, "provenance": {"region": "tw", "master": {"version": "synthetic"},
             "deck": {"commit": MODEL_COMMIT, "format": MODEL_FORMAT}},
            "bands": [{"id": 1, "name": {"ja": "MyGO!!!!!"}}, {"id": 2, "name": {"ja": "Ave Mujica"}}],
            "deck": {"model": {"power": 1000}, "kinds": [kind]}, "songs": []}
    for i in range(count):
        music_id = 100001 + i if i < count - 1 else 100107
        s = {"id": music_id, "title": {"ja": "迷星叫" if i == 0 else f"合成曲{i}"}, "bandIds": [1],
             "musicType": 1 + i % 5, "gekisouMissions": [1, 2, 3], "jacket": "jkt_synthetic",
             "composer": {"en": "Synthetic Composer"}, "bgm": {"length": {"durationMs": 100000 + i * 1000}},
             "scoreRanks": [{"rank": g, "requiredScore": n * 100, "battleRequiredScore": n * 200} for n, g in enumerate(("D", "C", "B", "A", "S", "SS"))], "charts": []}
        for n, d in enumerate(("easy", "normal", "hard", "expert")):
            seed = {"score": 1000 + 10 * i + 100 * n, "scorePerfect": 900 + 10 * i + 100 * n,
                    "weights": [[.1, .2, .3, .4, .5]],
                    "ranges": [{"rangeScore": 100, "rangeScorePerfect": 90, "rankBonus": 200} for _ in range(3)],
                    "rangeWeights": [[[.01, .02, .03] for _ in range(5)]]}
            c = {"scoreId": music_id * 100 + n, "difficulty": d, "displayLevel": 9 + n * 5.5,
                 "notes": {"judged": 100 + n}, "bpm": {"main": 100, "max": 200},
                 "firstNoteMs": 1000, "lastJudgedNoteMs": 51000, "musicLengthMs": 80000,
                 "deck": {"skip": 0.25, "seeds": [seed], "offSeeds": [{"score": 500, "weights": [[.02] * 5]}],
                          "ranges": [{"mission": 3, "rankBonusPercents": [200, 160, 120, 80, 40]} for _ in range(3)]}}
            s["charts"].append(c)
        data["songs"].append(s)
    return data


def make_snapshot(data=None, *, now=1000):
    data = data or payload()
    raw = json.dumps(data, ensure_ascii=False).encode()
    return MusicSnapshot(project(data), hashlib.sha256(raw).hexdigest(), now, now)


class MusicSourceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1000]
        self.data = payload()
        self.fetch = Mock(return_value=(200, json.dumps(self.data).encode(), {"etag": '"v1"', "last-modified": "HTTP publication"}))
        self.repo = MusicDataRepository(Path(self.tmp.name) / "new.json", fetch=self.fetch, clock=lambda: self.now[0])

    def test_compact_projection_and_identity_validation(self):
        self.data["replay"] = ["unused"] * 100
        self.data["songs"][0]["charts"][0]["gekisouAptitude"] = {"unused": True}
        compact = project(self.data)
        self.assertNotIn("replay", compact)
        self.assertNotIn("gekisouAptitude", compact["songs"][0]["charts"][0])
        for mutate in (lambda d: d.update(format="future"), lambda d: d["provenance"].update(region="jp"),
                       lambda d: d["songs"].append(d["songs"][0]), lambda d: d["songs"][0]["charts"].append(d["songs"][0]["charts"][0])):
            bad = copy.deepcopy(self.data)
            mutate(bad)
            with self.assertRaises(ValueError):
                project(bad)
        for raw in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e400}'):
            with self.assertRaises(ValueError):
                decode_json(raw)
        data = payload()
        data["songs"][0]["title"] = {"ja": 123, "en": "Valid fallback"}
        self.assertEqual(make_snapshot(data).songs()[0].title, "Valid fallback")
        from ournotes_bot.sources.moenotes_music_data import number
        self.assertIsNone(number(10**500))

    def test_conditional_refresh_stale_limit_and_atomic_cache(self):
        s = self.repo.get()
        self.now[0] += TTL
        self.fetch.return_value = 304, b"", {}
        current = self.repo.get()
        self.assertEqual(current.version, s.version)
        self.assertEqual(current.fetched_at, s.fetched_at)
        self.assertEqual(current.checked_at, self.now[0])
        self.assertEqual(self.fetch.call_args.args[0], {"If-None-Match": '"v1"'})
        self.now[0] += TTL
        self.fetch.side_effect = OSError("offline")
        self.assertTrue(self.repo.get().stale)
        self.now[0] += MAX_STALE
        self.assertIsNone(self.repo.get())
        self.assertTrue(self.repo.path.exists())
        self.assertFalse(list(self.repo.path.parent.glob("*.tmp")))

    def test_single_refresh_concurrency_cache_corruption_write_failure(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            snapshots = list(pool.map(lambda _: self.repo.get(), range(4)))
        self.assertEqual(self.fetch.call_count, 1)
        self.assertEqual(len({s.version for s in snapshots}), 1)
        self.repo.path.write_text('{"schema":1}', encoding="utf8")
        other = MusicDataRepository(self.repo.path, fetch=self.fetch, clock=lambda: self.now[0])
        with patch.object(other, "_save", side_effect=OSError("disk")):
            self.assertTrue(other.get().unsaved)

    def test_anonymous_transport_wire_decoded_limits_and_gzip_integrity(self):
        class Stream:
            async def iter_chunked(self, size):
                for start in range(0, len(body[0]), 11):
                    yield body[0][start:start + 11]
        class Response:
            status = 200
            headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}
            content = Stream()
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
        response = Response()
        class Session:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            def get(self, url, **kwargs):
                calls.append((url, kwargs))
                return response
        raw, body, calls = b'{"value":1}', [gzip.compress(b'{"value":1}')], []
        with patch("aiohttp.ClientSession", return_value=Session()) as session:
            self.assertEqual(fetch_snapshot({"If-None-Match": '"v1"'}, 1)[1], raw)
            self.assertFalse(calls[-1][1]["allow_redirects"])
            self.assertEqual(set(calls[-1][1]["headers"]), {"Accept-Encoding", "If-None-Match"})
            self.assertFalse(session.call_args.kwargs["auto_decompress"])
            for encoded, wire, decoded, error in ((body[0][:-2], 1000, 1000, "music_gzip"),
                (body[0] * 2, 1000, 1000, "music_gzip"), (body[0], 10, 1000, "music_wire_limit"),
                (gzip.compress(b"x" * 5000), 1000, 100, "music_body_limit")):
                body[0] = encoded
                with patch("ournotes_bot.sources.moenotes_music_data.WIRE_LIMIT", wire), patch("ournotes_bot.sources.moenotes_music_data.BODY_LIMIT", decoded):
                    with self.assertRaises(SourceError) as caught:
                        fetch_snapshot({}, 1)
                    self.assertEqual(caught.exception.code, error)
            response.status = 304
            self.assertEqual(fetch_snapshot({}, 1)[:2], (304, b""))

    def test_cancellation_does_not_publish_or_keep_refresh_lock(self):
        self.fetch.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            self.repo.get()
        self.assertIsNone(self.repo.peek())
        self.assertFalse(self.repo.path.exists())
        self.fetch.side_effect = None
        self.assertIsNotNone(self.repo.get())


class MetaModelTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = make_snapshot()
        self.song = self.snapshot.data["songs"][0]
        self.chart = self.song["charts"][0]

    def test_hand_calculated_units_and_scenes(self):
        req = MetaRequest()
        r = evaluate(self.song, self.chart, self.snapshot, req)
        self.assertAlmostEqual(r.score, 2.5)
        self.assertAlmostEqual(r.eff, 2.5 * 60 / 130)
        self.assertEqual(r.density, 2)
        self.assertEqual((r.bpm, r.bpm_max, r.skip), (100, 200, .25))
        r = evaluate(self.song, self.chart, self.snapshot, replace(req, scene="free", skills=(150,) * 5))
        self.assertAlmostEqual(r.score, .65)
        r = evaluate(self.song, self.chart, self.snapshot, replace(req, ranks=(2, 3, 4), great=100, skills=(0,) * 5))
        self.assertAlmostEqual(r.score, .608)
        r = evaluate(self.song, self.chart, self.snapshot, replace(req, just=0, skills=(0,) * 5))
        self.assertAlmostEqual(r.score, .9)

    def test_missing_is_unknown_basic_facts_and_real_zero_survive(self):
        c = copy.deepcopy(self.chart)
        c.pop("deck")
        c["notes"]["judged"] = 0
        r = evaluate(self.song, c, self.snapshot, MetaRequest(ranking="notes"))
        self.assertEqual(r.metric, 0)
        self.assertIsNone(r.score)
        c["notes"]["judged"] = True
        self.assertIsNone(evaluate(self.song, c, self.snapshot, MetaRequest(ranking="notes")).metric)
        unsupported = copy.deepcopy(self.snapshot.data)
        unsupported["provenance"]["deck"]["commit"] = "unknown"
        self.assertIsNone(figures(c, make_snapshot(unsupported), MetaRequest()))
        c["notes"] = {}
        self.assertIsNone(evaluate(self.song, c, self.snapshot, MetaRequest(ranking="level")).metric)
        for field, bad in (("scoreRanks", None), ("bgm", "invalid")):
            data = payload()
            data["songs"][0][field] = bad
            s = make_snapshot(data)
            self.assertEqual(evaluate(s.data["songs"][0], s.data["songs"][0]["charts"][0], s, MetaRequest(ranking="notes")).metric, 100)
        data = payload()
        data["deck"]["model"] = "invalid"
        s = make_snapshot(data)
        self.assertEqual(evaluate(s.data["songs"][0], s.data["songs"][0]["charts"][0], s, MetaRequest(ranking="notes")).metric, 100)

    def test_deadline_stops_complete_model_and_frontier(self):
        from ournotes_bot.query.meta_model import frontier
        with self.assertRaises(TimeoutError):
            evaluate(self.song, self.chart, self.snapshot, MetaRequest(), deadline=0)
        row = evaluate(self.song, self.chart, self.snapshot, MetaRequest())
        with self.assertRaises(TimeoutError):
            frontier((row,), "efficiency", deadline=0)

    def test_lengths_fallback_density_and_partial_scene(self):
        song = copy.deepcopy(self.song)
        song["bgm"] = {}
        r = evaluate(song, self.chart, self.snapshot, MetaRequest())
        self.assertEqual((r.seconds, r.length_source, r.density), (80, "chart", 2))
        self.assertTrue(r.warnings)
        c = copy.deepcopy(self.chart)
        c["deck"]["unplayable"] = "four fever ranges"
        self.assertIsNone(figures(c, self.snapshot, MetaRequest()))
        self.assertIsNotNone(figures(c, self.snapshot, MetaRequest(scene="free")))
        c = copy.deepcopy(self.chart)
        c["deck"]["positions"] = 6
        self.assertIsNone(figures(c, self.snapshot, MetaRequest()))
        self.assertIsNone(evaluate(self.song, c, self.snapshot, MetaRequest(ranking="skip")).metric)
        c["deck"]["seeds"][0]["weights"][0][0] = None
        c["deck"]["unplayable"] = None
        self.assertIsNone(figures(c, self.snapshot, MetaRequest()))

    def test_six_grades_rooms_and_120_identity_orders(self):
        self.assertEqual(len(ORDERS), 120)
        for scene in ("battle", "free"):
            for people in range(1, 6):
                for grade in ("D", "C", "B", "A", "S", "SS"):
                    req = MetaRequest(ranking="event", scene=scene, target=grade, people=people, power=400, skills=(0, 50, 100, 100, 150))
                    r = evaluate(self.song, self.chart, self.snapshot, req)
                    self.assertIsNotNone(r.need)
                    rates = [r.figures.base + sum(v / 100 * w for v, w in zip(order, r.figures.weights)) for order in permutations(req.skills)]
                    target = 0 if grade == "D" else r.need * r.score
                    expected = sum(400 * value >= target for value in rates)
                    self.assertEqual(r.successes, expected)
                    self.assertEqual(r.chance, expected / 120)
        r = evaluate(self.song, self.chart, self.snapshot, MetaRequest(ranking="event"))
        self.assertIsNone(r.chance)

    def test_frontier_endpoints_and_all_grades(self):
        r = evaluate(self.song, self.chart, self.snapshot, MetaRequest())
        worse = replace(r, score_id=r.score_id + 1, seconds=2 * r.seconds)
        self.assertTrue(dominates(r, worse, "efficiency"))
        self.assertFalse(dominates(worse, r, "efficiency"))
        self.assertFalse(dominates(r, r, "efficiency"))
        self.assertTrue(dominates(r, worse, "event"))
        self.assertFalse(dominates(r, replace(worse, thresholds=(None,) * 6), "event"))


class MetaQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = SongRepository("https://bdon.yatta.moe", Path(self.tmp.name) / "cache.json")
        self.snapshot = make_snapshot(now=time.time())
        self.repo.music_data._snapshot = self.snapshot
        self.repo.music_data.get = Mock(return_value=self.snapshot)

    def answer(self, params=""):
        return resolve_command("/查分数表 " + params, self.repo).meta

    def test_default_mixed_30_new_only_and_exact_ex(self):
        a = self.answer()
        self.assertEqual((a.status, a.total, len(a.rows)), ("success", 48, 30))
        all_rows = (*a.rows, *self.answer("页2").rows)
        self.assertEqual({r.difficulty for r in all_rows}, {"EASY", "NORMAL", "HARD", "EXPERT"})
        self.assertEqual(len(self.answer("100107").rows), 1)
        self.assertEqual(self.answer("100107").rows[0].difficulty, "EXPERT")
        self.assertFalse(self.answer("100107").cells, "precise one-chart query keeps the existing text card")
        multi = self.answer("迷星叫 HD,EX")
        self.assertEqual(len(multi.rows), 2)
        self.assertEqual(len(multi.cells), 2, "explicit multiple difficulties retain the list table")
        self.assertEqual(self.repo.songs, [])
        data = payload()
        unique = next(s for s in data["songs"] if s["id"] == 100107)
        unique["charts"] = [c for c in unique["charts"] if c["difficulty"].upper() != "EXPERT"]
        self.snapshot = make_snapshot(data, now=time.time())
        self.repo.music_data._snapshot = self.snapshot
        self.repo.music_data.get.return_value = self.snapshot
        missing = self.answer("100107")
        self.assertEqual(missing.status, "data_unavailable")
        self.assertFalse(missing.rows)
        self.assertIn("请求谱面未收录：EXPERT", missing.text)
        self.assertIn("已收录难度：EASY、NORMAL、HARD", missing.text)

    def test_free_group_permutations_roundtrip_and_conflicts(self):
        chunks = ["前10", "技能=全150", "自由", "HD,EX", "MyGO"]
        expected = parse_efficiency("/查分数表 " + " ".join(chunks), self.repo, direct=True)
        for groups in permutations(chunks):
            spec = parse_efficiency("/查分数表 " + " ".join(groups), self.repo, direct=True)
            self.assertEqual(spec, expected)
        again = parse_efficiency("/" + expected.command_label(), self.repo, direct=True)
        self.assertEqual(expected, again)
        for text in ("自由 Just=80", "自由 激奏排名=3", "技能=100,100", "技能=151,100,100,100,100", "Great=1.2",
                     "Just=NaN", "Great=true", "额外耗时=Infinity", "前沿 单局", "Notes 场景=自由", "人数=5", "lv=25",
                     "lv<20 lv>20", "EX 未知=1", "自由 激奏", "效率 指标=score", "目标=X 活动", "综合力=2147483648 活动"):
            with self.subTest(text=text):
                self.assertEqual(self.answer(text).status, "invalid_arguments")

    def test_single_card_has_readable_facts_and_keeps_diagnostics_out_of_picture(self):
        from ournotes_bot import visuals
        answer = self.answer("迷星叫 Notes")
        self.assertTrue(answer.text.startswith("迷星叫（100001）"))
        self.assertIn("判定Note：103", answer.text)
        for unwanted in ("序号 |", "scoreId", "正文SHA", "HTTP发布头", "每页", "单局 ", "seed样本"):
            self.assertNotIn(unwanted, answer.text)
        self.assertIn(self.snapshot.version, "\n".join(answer.diagnostics))
        with patch.object(visuals, "_canvas", wraps=visuals._canvas) as canvas:
            visuals.render_meta(answer)
        self.assertEqual([call.args[2] for call in canvas.call_args_list], [answer.title, answer.title])
        efficiency = self.answer("迷星叫")
        self.assertEqual(efficiency.text.count("分/综合力/分钟："), 1)
        self.assertIn("相对所选比较池最高效率", efficiency.text)

    def test_duration_fallback_is_present_in_captured_image_notes(self):
        data = payload()
        data["songs"][0]["bgm"] = {}
        snapshot = make_snapshot(data, now=time.time())
        self.repo.music_data._snapshot = snapshot
        self.repo.music_data.get.return_value = snapshot
        answer = self.answer("迷星叫 HD,EX")
        self.assertTrue(all(cell[4] == "80.0秒*" for cell in answer.cells))
        self.assertIn("* 本页第1、2条：时长回退为谱面长度。", answer.notes)
        data = payload()
        for chart in data["songs"][0]["charts"]:
            chart.pop("musicLengthMs")
        snapshot = make_snapshot(data, now=time.time())
        self.repo.music_data._snapshot = snapshot
        self.repo.music_data.get.return_value = snapshot
        self.assertIn("* 本页第1、2条：时长回退为BGM长度。", self.answer("迷星叫 HD,EX 时长=谱面").notes)

    def test_readable_summary_preserves_all_applicable_conditions(self):
        spec = parse_efficiency("/查分数表 HD,EX 激奏排名=1,2,3 Great=5 Just=90 技能=150,130,120,100,100 "
                                "时长=谱面 额外耗时=600 活动 目标=S 综合力=300000 人数=3 "
                                "前沿=否 排序=asc 颜色=红 lv<=25 搜索=迷", self.repo, direct=True)
        summary = spec.meta_request.display_summary()
        for text in ("HD/EX", "活动升序", "1/2/3", "Great 5%", "Just 90%", "150/130/120/100/100%",
                     "时长 谱面", "曲外 600秒", "目标 S", "综合力 300000", "3人同分", "不限前沿", "红", "25", "搜索：迷"):
            self.assertIn(text, summary)
        self.assertEqual(parse_efficiency("/" + spec.command_label(), self.repo, direct=True), spec)
        basic = self.answer("Notes")
        self.assertNotIn("技能", basic.scope)
        self.assertNotIn("准度", "\n".join(basic.notes))

    def test_quoted_search_keeps_spaces_and_punctuation_through_roundtrip(self):
        import shlex
        for value in ("迷星叫", "A / B", "100, 200", "A = B", "  合成  ", "it's a song", 'a "quoted" title'):
            with self.subTest(value=value):
                command = "/查分数表 搜索=" + shlex.quote(value) + " HD,EX 前10"
                spec = parse_efficiency(command, self.repo, direct=True)
                self.assertEqual(spec.meta_request.search, value)
                self.assertEqual(parse_efficiency("/" + spec.command_label(), self.repo, direct=True), spec)
                natural = parse_efficiency("分数表 搜索=" + shlex.quote(value) + " HD,EX 前10", self.repo)
                self.assertEqual(natural, spec)
        self.assertEqual(self.answer('搜索="迷星叫"').total, 4)
        self.assertEqual(self.answer('"迷星叫"').rows[0].song_id, 100001)
        self.assertEqual(self.answer('搜索=""').status, "invalid_arguments")

    def test_modes_sort_search_pool_and_legacy_filters(self):
        for mode in ("效率", "单局", "活动", "速度", "等级", "Notes", "最长", "最短", "跳过得分"):
            a = self.answer(mode)
            self.assertEqual(a.status, "success", a.text)
            values = [r.metric for r in a.rows]
            self.assertEqual(values, sorted(values, reverse=mode not in {"活动", "最短"}))
        self.assertEqual(self.answer("搜索='Synthetic Composer'").total, 48)
        self.assertEqual(self.answer("颜色=红 属性=蓝 激奏=包含全部JUST/COMBO HD,EX lv<=25").total, 6)
        self.assertEqual(self.answer("100001 颜色=蓝").status, "empty")
        self.assertEqual(self.answer("速度=最大BPM").rows[0].metric, 200)

    def test_new_view_entities_numeric_names_and_verified_aliases(self):
        from ournotes_bot.query import entity_lexicon
        data = payload()
        data["songs"][1]["title"] = {"ja": "37"}
        data["songs"][2]["title"] = {"ja": "Notes"}
        data["songs"][3]["title"] = {"ja": "EX 50 活动"}
        data["songs"][4]["title"] = {"ja": "重名"}
        data["songs"][5]["title"] = {"ja": "重名"}
        data["songs"][6]["bandIds"] = [1, 2]
        snapshot = make_snapshot(data, now=time.time())
        self.repo.music_data._snapshot = snapshot
        self.repo.music_data.get.return_value = snapshot
        aliases = {"song": {"昵称带技能": "迷星叫", "过时昵称": "已删除名称"}, "band": {"狗团": "MyGO!!!!!"}}
        with patch.object(entity_lexicon, "_aliases", return_value=aliases):
            for name, music_id in (("37", 100002), ("Notes", 100003), ('"EX 50 活动"', 100004), ("昵称带技能", 100001), ("107", 100107)):
                a = self.answer(name)
                self.assertEqual(a.status, "success", a.text)
                self.assertEqual(a.rows[0].song_id, music_id)
            self.assertEqual(self.answer("过时昵称").status, "unknown_entity")
            self.assertEqual(self.answer("重名").status, "ambiguous")
            self.assertIn("100005", self.answer("重名").text)
            self.assertEqual(self.answer("乐队='Ave Mujica'").total, 4)
            self.assertEqual(self.answer("狗团").total, 48)

    def test_all_task_modes_unknown_coverage_decimal_and_frontier_search(self):
        for task, count in (("JUST", 48), ("纯JUST", 0), ("混合", 48),
                            ("COMBO/LUCK/JUST", 48), ("包含全部JUST/COMBO", 48)):
            a = self.answer("激奏=" + task)
            self.assertEqual(a.total, count, a.text)
        q = parse_efficiency("/查分数表 额外耗时=1.13 技能=99.99,150,0,100,100", self.repo, direct=True)
        self.assertEqual(q.meta_request.overhead_ms, 1130)
        self.assertEqual(q.meta_request.skills[0], 99.99)
        a = self.answer("前沿")
        song_id = a.rows[0].song_id
        self.assertEqual(self.answer(f"前沿 搜索={song_id}").rows, tuple(r for r in a.rows if r.song_id == song_id))
        data = payload()
        data["songs"][0].pop("musicType")
        data["songs"][1]["scoreRanks"] = [data["songs"][1]["scoreRanks"][-1]]
        snapshot = make_snapshot(data, now=time.time())
        self.repo.music_data._snapshot = snapshot
        self.repo.music_data.get.return_value = snapshot
        unknown = self.answer("颜色=红")
        self.assertEqual((unknown.unknown_songs, unknown.unknown_rows), (1, 4))
        self.assertFalse(any(r.song_id == 100002 for r in self.answer("活动 前沿").rows))
        self.assertEqual(self.answer("Notes lv<20").status, "success")

    def test_result_lru_concurrent_queries_and_captured_expiry(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            answers = list(pool.map(lambda i: self.answer(f"Great={i % 80}"), range(80)))
        self.assertTrue(all(a.status == "success" for a in answers))
        self.assertLessEqual(len(self.repo.music_data._results), 64)
        spec = parse_efficiency("/查分数表", self.repo, direct=True)
        with patch.object(self.repo.music_data, "clock", return_value=self.snapshot.checked_at + MAX_STALE + 1):
            self.assertEqual(execute_efficiency(spec, self.repo).status, "data_unavailable")

    def test_complete_continuation_source_version_and_identity(self):
        from ournotes_bot.query.continuation import capture_context, execute_followup, Operation, CHANGED
        result = resolve_command("/查分数表 HD,EX 前10 技能=全150 自由 搜索=合成", self.repo)
        context = capture_context(result, self.repo)
        self.assertIsNotNone(context)
        page, next_context = execute_followup(context, Operation("page", 1), self.repo)
        self.assertEqual(page.spec.meta_request, context.query.meta_request)
        selected, _ = execute_followup(context, Operation("select", 2), self.repo)
        self.assertEqual(selected.meta.rows[0].score_id, context.visible[1].score_id)
        self.assertEqual(selected.spec.meta_request.skills, (150,) * 5)
        self.repo.music_data._snapshot = replace(self.snapshot, body_sha="changed")
        self.assertEqual(execute_followup(context, Operation("page", 1), self.repo), CHANGED)

    def test_deterministic_ask_and_same_capture(self):
        from ournotes_bot.ai_query import AIQueryParser
        from ournotes_bot.config import Settings
        parser = AIQueryParser(Settings("", "", self.repo.data_base, self.repo.cache_file, 6))
        with patch.object(parser, "_request", side_effect=AssertionError("model called")):
            text, result = parser.answer_with_plan("/问 前10 技能=全150 自由 HD,EX MyGO 分数表", self.repo)
        self.assertEqual(result.meta.rows, self.answer("前10 技能=全150 自由 HD,EX MyGO").rows)
        self.assertEqual(text, result.meta.text)
        for natural, direct in [("迷星叫的效率怎么样", "迷星叫"), ("查一下迷星叫EX的meta", "迷星叫 EX"),
                                ("MyGO的EX效率前十有哪些", "MyGO EX 前10"), ("全难度的分数表", ""),
                                ("每分钟得分效率最高的前30首歌曲", ""), ("EXPERT 25级以下的效率榜", "EXPERT lv<=25")]:
            with self.subTest(natural=natural), patch.object(parser, "_request", side_effect=AssertionError("model called")):
                t, result = parser.answer_with_plan("/问 " + natural, self.repo)
                self.assertIsNotNone(result.meta, t)
                self.assertEqual(result.meta.rows, self.answer(direct).rows)

    def test_image_and_full_upload_fallback_share_capture_and_budget(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from ournotes_bot.ai_query import AIQueryParser
        from ournotes_bot.config import Settings
        from ournotes_bot.platforms.qq.qq import _prepare_reply, _expand_replies, _deliver_reply, PreparedReply
        parser = AIQueryParser(Settings("", "", self.repo.data_base, self.repo.cache_file, 6))
        with patch("ournotes_bot.platforms.qq.qq._image_from_result", return_value=b"synthetic-image"):
            prepared = _prepare_reply("/查分数表", self.repo, parser)
        before = self.repo.music_data.get.call_count
        expanded = _expand_replies([prepared])
        self.assertEqual(len(expanded), 1)
        reply = expanded[0]
        self.assertEqual("".join(reply.fallback_texts), prepared.text)
        self.assertLessEqual(reply.passive_slots, 5)
        self.assertTrue(all(len(s) <= 1800 for s in reply.fallback_texts))
        api = SimpleNamespace(post_group_message=AsyncMock(return_value={"id": "receipt"}))
        message = SimpleNamespace(_api=api, id="inbound")
        with patch("ournotes_bot.platforms.qq.qq._upload_image", side_effect=TimeoutError):
            outcome = asyncio.run(_deliver_reply(message, "target", True, reply))
        self.assertTrue(outcome.confirmed)
        self.assertEqual([c.kwargs["msg_seq"] for c in api.post_group_message.call_args_list], list(range(1, len(reply.fallback_texts) + 1)))
        self.assertEqual("".join(c.kwargs["content"] for c in api.post_group_message.call_args_list), prepared.text)
        self.assertEqual(self.repo.music_data.get.call_count, before)
        failed = _expand_replies([replace(prepared, text="x" * 10000)])[0]
        self.assertIsNone(failed.context)
        self.assertIsNone(failed.image)
        self.assertIn("超过回复预算", failed.text)
        api.post_group_message = AsyncMock(side_effect=[{"id": "receipt"}, None])
        with patch("ournotes_bot.platforms.qq.qq._upload_image", side_effect=TimeoutError):
            outcome = asyncio.run(_deliver_reply(message, "target", True, reply))
        self.assertFalse(outcome.confirmed)


if __name__ == "__main__":
    unittest.main()
