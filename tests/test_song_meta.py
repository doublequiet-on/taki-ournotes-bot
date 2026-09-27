"""Synthetic fixtures only; network, QQ and paid models are never used."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from http.client import IncompleteRead
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import handle_command, resolve_command
from ournotes_bot.config import Settings
from ournotes_bot.data import Chart, Song, SongRepository
from ournotes_bot.efficiency_query import parse_efficiency
from ournotes_bot.local_query import parse_local_query
from ournotes_bot.query_capabilities import CAPABILITIES, local_route
from ournotes_bot.query_validation import OutcomeCode, validate_capability_action
from ournotes_bot.song_meta import MetaRepository, REFERENCE, fetch_json, parse_payload, unique_object

STAMP = "2026-09-27T00:00:00+00:00"


def payload(count=13):
    identity = {"schema": "haneoka-resource-release-identity-v1", "server": "jp",
                "releaseId": "r-synthetic", "sourceId": "synthetic-test"}
    songs, meta = {}, {}
    for index in range(count):
        key = str(100001 + index)
        songs[key] = {"musicId": int(key), "musicTitle": ["暗黒天国" if index == 0 else f"合成曲{index}"],
                      "difficulty": [{"difficulty": 3, "difficultyName": "expert", "sortLevel": 25}]}
        meta[key] = {"3": {"chart": {
            "metaStatus": "available", "scoreKind": "chart-relative-factor", "absoluteScoreAvailable": False,
            "reference": dict(REFERENCE), "eff": float(20 - index), "score": float(30 - index),
            "time": 100, "sr": 0.5,
        }}}
    return identity, songs, meta


class SongMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = SongRepository("https://bdon.yatta.moe", self.root / "songs.json")
        self.identity, self.songs, self.meta = payload()
        self.repo.songs = [Song(int(key), row["musicTitle"][0], tuple(row["musicTitle"]), "MyGO!!!!!",
                               "", "", "", "", "", (Chart("EXPERT", 25, 25, 100, ""),))
                           for key, row in self.songs.items()]
        self.repo.song_meta = Mock()
        self.repo.song_meta.get.return_value = self.snapshot()
        self.settings = Settings("", "", self.repo.data_base, self.repo.cache_file, 6,
                                 "", "unused", "https://invalid.example", 0,
                                 ai_quota_file=self.root / "quota.json", ai_metrics_file=self.root / "metrics.json")

    def snapshot(self):
        return parse_payload(self.identity, self.songs, self.meta, STAMP)

    def test_parse_metrics_and_missing_values(self):
        chart = self.meta["100001"]["3"]["chart"]
        chart["eff"] = None
        chart.pop("time")
        result = self.snapshot().rows[0]
        self.assertIsNone(result.eff)
        self.assertIsNone(result.seconds)
        self.assertEqual(result.score, 30)

    def test_reject_invalid_response_empty_incomplete_duplicate_and_nonfinite(self):
        for songs, meta in [("<html>error</html>", self.meta), ({}, {}),
                            ({"items": self.songs, "next": 2}, self.meta),
                            (self.songs, {"100001": self.meta["100001"]})]:
            with self.subTest(songs_type=type(songs)), self.assertRaises(ValueError):
                parse_payload(self.identity, songs, meta, STAMP)
        with self.assertRaises(ValueError):
            json.loads('{"1":{},"1":{}}', object_pairs_hook=unique_object)
        for value in [float("nan"), float("inf"), -1, True, "5", 10 ** 1000]:
            self.meta["100001"]["3"]["chart"]["eff"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.snapshot()

    def test_wrong_server_and_duplicate_difficulty(self):
        self.identity["server"] = "intl"
        with self.assertRaises(ValueError):
            self.snapshot()
        self.identity["server"] = "jp"
        self.songs["100001"]["difficulty"] *= 2
        with self.assertRaises(ValueError):
            self.snapshot()

    def test_json_transport_rejects_html_and_arbitrary_urls(self):
        response = io.BytesIO(b"<html>error</html>")
        response.headers = {"Content-Type": "text/html"}
        with patch("ournotes_bot.song_meta.urlopen", return_value=response), self.assertRaises(ValueError):
            fetch_json("release?projection=identity")
        with self.assertRaises(ValueError):
            fetch_json("https://invalid.example/")

    def test_single_song_name_id_default_and_missing_difficulty(self):
        for command in ["/查效率 暗黒天国", "/查效率 暗黒天国 EX", "/查效率 100001 EX"]:
            text = handle_command(command, self.repo)
            self.assertIn("eff：2000.00%", text)
            self.assertIn("EXPERT", text)
        self.assertIn("默认", handle_command("/查效率 暗黒天国", self.repo))
        self.assertIn("可用难度：EXPERT", handle_command("/查效率 暗黒天国 HARD", self.repo))
        self.assertIn("未找到", handle_command("/查效率 不存在的曲名", self.repo))
        self.meta["100001"]["3"]["chart"]["eff"] = None
        self.repo.song_meta.get.return_value = self.snapshot()
        self.assertIn("已收录", handle_command("/查效率 暗黒天国", self.repo))

    def test_ambiguous_names_do_not_choose_first(self):
        self.repo.songs[1] = replace(self.repo.songs[1], title="暗黒天国", titles=("暗黒天国",))
        text = handle_command("/查效率 暗黒天国", self.repo)
        self.assertIn("候选", text)
        self.assertIn("100001", text)
        self.assertIn("100002", text)
        self.repo.song_meta.get.assert_not_called()

    def test_existing_song_alias_short_id_and_no_expert_fallback(self):
        alias = self.root / "aliases.json"
        alias.write_text(json.dumps({"song": {"合成昵称": "100001"}}), encoding="utf-8")
        with patch("ournotes_bot.entity_lexicon.ALIAS_FILE", alias):
            self.assertEqual(handle_command("/查效率 合成昵称 EX", self.repo), handle_command("/查效率 1 EX", self.repo))
        self.songs["100001"]["difficulty"] = [{"difficulty": 2, "difficultyName": "hard", "sortLevel": 20}]
        self.meta["100001"]["2"] = self.meta["100001"].pop("3")
        self.repo.song_meta.get.return_value = self.snapshot()
        text = handle_command("/查效率 暗黒天国", self.repo)
        self.assertIn("EXPERT", text)
        self.assertIn("可用难度：HARD", text)
        self.assertNotIn("eff：", text)

    def test_ranking_ties_filters_and_pagination_preserve_conditions(self):
        self.meta["100002"]["3"]["chart"]["eff"] = 20
        self.repo.song_meta.get.return_value = self.snapshot()
        first = resolve_command("/查效率 排行 MyGO EX lv<=25 指标=eff 排序=desc", self.repo).meta
        self.assertEqual([r.song_id for r in first.rows[:2]], [100001, 100002])
        next_command = first.text.split("下一页：", 1)[1].splitlines()[0]
        second = resolve_command(next_command, self.repo).meta
        self.assertEqual(len(second.rows), 3)
        self.assertIn("等级<=25", second.text)
        self.assertIn("MyGO!!!!!", second.text)
        self.assertIn("超出范围", handle_command(next_command.replace("页2", "页3"), self.repo))
        self.assertIn("没有符合", handle_command("/查效率 排行 EX lv<25", self.repo))
        ascending = resolve_command("/查效率 排行 EX 排序=asc", self.repo).meta
        self.assertEqual(ascending.rows[0].song_id, 100013)

    def test_mixed_conditions_unknown_metrics_and_mapping_conflicts_are_excluded(self):
        self.meta["100001"]["3"]["chart"]["reference"]["fever"] = False
        self.meta["100002"]["3"]["chart"]["eff"] = None
        self.repo.songs[2] = replace(self.repo.songs[2], title="另一个标题", titles=("另一个标题",))
        self.repo.songs.append(self.repo.songs[3])
        self.repo.song_meta.get.return_value = self.snapshot()
        result = resolve_command("/查效率", self.repo).meta
        self.assertTrue({100001, 100002, 100003, 100004}.isdisjoint(r.song_id for r in result.rows))
        self.assertIn("非完整全曲榜", result.text)
        for query in ["排行 EX HARD", "排行 EX 单人", "排行 EX 指标=nps", "排行 EX 页0", "排行 EX lv<25 lv>20"]:
            result = resolve_command("/查效率 " + query, self.repo).meta
            self.assertFalse(result.rows)

    def test_equivalent_natural_queries_without_ai_or_quota(self):
        parser = AIQueryParser(self.settings)
        pairs = [("暗黒天国的效率怎么样", "暗黒天国"),
                 ("查一下暗黒天国EX的meta", "暗黒天国 EX"),
                 ("哪些歌效率最高", "排行"),
                 ("MyGO的EX效率前十有哪些", "排行 MyGO EX"),
                 ("EXPERT 25级以下的效率榜", "排行 EXPERT lv<=25")]
        with patch.object(parser, "_request", side_effect=AssertionError("paid model called")) as model:
            for natural, direct in pairs:
                with self.subTest(natural=natural):
                    self.assertEqual(parse_local_query(natural, self.repo), parse_efficiency(direct, self.repo, direct=True))
                    self.assertEqual(parser.answer("/问 " + natural, self.repo), handle_command("/查效率 " + direct, self.repo))
            model.assert_not_called()
        self.assertFalse((self.root / "quota.json").exists())

    def test_meta_marker_does_not_capture_an_english_title_fragment(self):
        self.assertIsNone(parse_local_query("metaphor的歌有哪些", self.repo))
        self.assertNotEqual(local_route("metaphor的歌有哪些"), "song.meta")
        self.repo.songs[0] = replace(self.repo.songs[0], title="METAL", titles=("METAL",))
        self.assertIn("METAL", handle_command("/查效率 METAL EX", self.repo))

    def test_cached_plan_reads_new_meta_and_route_stays_bounded(self):
        parser = AIQueryParser(self.settings)
        first = parser.answer("/问 暗黒天国效率怎么样", self.repo)
        self.meta["100001"]["3"]["chart"]["eff"] = 1
        self.repo.song_meta.get.return_value = self.snapshot()
        self.assertNotEqual(first, parser.answer("/问 暗黒天国效率怎么样", self.repo))
        self.assertEqual(local_route("哪些歌效率最高"), "song.meta")
        bad = {"action": "call_tool", "capability": "song.meta", "arguments": {"query": "100002", "eff": 999}}
        self.assertEqual(validate_capability_action(bad, CAPABILITIES["song.meta"], "暗黒天国效率", self.repo).code,
                         OutcomeCode.INVALID_ARGUMENTS)
        for question in ["效率配队推荐", "效率档线预测", "最强效率攻略", "歌曲排行"]:
            self.assertNotIn("[效率榜]", parser.answer("/问 " + question, self.repo))

    def test_text_image_share_result_and_drawing_failure_falls_back(self):
        from ournotes_bot.qq import _prepare_reply
        parser = AIQueryParser(self.settings)
        with patch("ournotes_bot.visuals.render_meta", return_value=b"image") as render:
            reply = _prepare_reply("/查效率 暗黒天国", self.repo, parser)
            self.assertEqual(reply.text, render.call_args.args[0])
            self.repo.song_meta.get.assert_called_once()
        with patch("ournotes_bot.visuals.render_meta", side_effect=ValueError("font")):
            reply = _prepare_reply("/问 暗黒天国效率怎么样", self.repo, parser)
            self.assertIsNone(reply.image)
            self.assertIn("eff：", reply.text)

    def test_cache_refresh_failure_retains_old_and_recovers(self):
        clock = [1790467200.0]
        def fetch(path):
            return copy.deepcopy(self.identity if path.startswith("release") else self.songs if path.startswith("songs?") else self.meta)
        fetcher = Mock(side_effect=fetch)
        repo = MetaRepository(self.root / "meta.json", fetch=fetcher, clock=lambda: clock[0])
        first = repo.get()
        self.assertIsNotNone(first)
        self.assertEqual(fetcher.call_count, 3)
        self.assertEqual(repo.get(), first)
        disk = repo.path.read_bytes()
        clock[0] += 90000
        fetcher.side_effect = OSError("offline")
        self.assertTrue(repo.get().stale)
        self.assertEqual(repo.path.read_bytes(), disk)
        count = fetcher.call_count
        self.assertTrue(repo.get().stale)
        self.assertEqual(fetcher.call_count, count)
        clock[0] += 301
        fetcher.side_effect = fetch
        self.meta["100001"]["3"]["chart"]["eff"] = 1
        self.assertEqual(repo.get().rows[0].eff, 1)
        self.assertFalse(repo.get().stale)
        fresh = MetaRepository(repo.path, fetch=Mock(side_effect=AssertionError("unexpected fetch")), clock=lambda: clock[0])
        self.assertEqual(fresh.get(), repo.get())

    def test_no_cache_and_concurrent_downloads(self):
        absent = MetaRepository(self.root / "absent.json", fetch=Mock(side_effect=OSError("offline")))
        self.repo.song_meta = absent
        self.assertIn("效率数据暂不可用", handle_command("/查效率", self.repo))
        calls = []
        def fetch(path):
            calls.append(path)
            return copy.deepcopy(self.identity if path.startswith("release") else self.songs if path.startswith("songs?") else self.meta)
        cache = MetaRepository(self.root / "parallel.json", fetch=fetch)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: cache.get(), range(4)))
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(result == results[0] for result in results))

    def test_bad_refresh_does_not_replace_valid_cache(self):
        current = [1790467200.0]
        responses = lambda path: copy.deepcopy(self.identity if path.startswith("release") else self.songs if path.startswith("songs?") else self.meta)
        cache = MetaRepository(self.root / "validation.json", fetch=responses, clock=lambda: current[0])
        self.assertIsNotNone(cache.get())
        original = cache.path.read_bytes()
        current[0] += 90000
        self.meta.pop("100002")
        stale = cache.get()
        self.assertTrue(stale.stale)
        self.assertEqual(len(stale.rows), 13)
        self.assertEqual(original, cache.path.read_bytes())

    def test_corrupt_disk_and_truncated_network_return_unavailable(self):
        path = self.root / "corrupt.json"
        path.write_text("[]", encoding="utf-8")
        cache = MetaRepository(path, fetch=Mock(side_effect=IncompleteRead(b"partial")))
        self.assertIsNone(cache.get())
        self.assertEqual(path.read_text(encoding="utf-8"), "[]")


if __name__ == "__main__":
    unittest.main()
