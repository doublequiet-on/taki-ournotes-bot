from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.config import Settings
from ournotes_bot.data import Chart, Song, SongRepository
from ournotes_bot.sources.chart_data import ChartDataError, load_chart_data, load_chart_score
from ournotes_bot.sources.haneoka import chart_data as source
from ournotes_bot.visuals import render_chart


IDENTITY = {"schema": "haneoka-resource-release-identity-v1", "server": "jp",
            "releaseId": "r-" + "a" * 20, "sourceId": "v10059-test"}
SCORE = {"meta": {"version": 100}, "score": {
    "events": {"bpm": [{"t": 0, "bpm": 182.0}, {"t": 1920, "bpm": 120.5}],
               "sig": [{"t": 0, "sig": [4, 4]}], "skill": [0, 960], "fever": [0, 960, 2880]},
    "notes": [{"t": 0, "pos": 0, "size": 6},
              {"type": "flick", "t": 480, "pos": 12, "size": 6, "dir": "left"},
              {"type": "long", "node": [{"t": 960, "pos": 0, "size": 6},
                                         {"t": 1920, "pos": 6, "size": 3, "curve": True},
                                         {"t": 2400, "pos": "auto"},
                                         {"t": 2880, "pos": 12, "size": 6, "type": "flick", "dir": "right"}]},
              {"type": "guide", "node": [{"t": 0, "pos": 0, "size": 1},
                                          {"t": 1920, "pos": 23, "size": 1},
                                          {"t": 7680, "pos": 12, "size": 6}]}]}}


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Responses:
    def __init__(self):
        self.identity = copy.deepcopy(IDENTITY)
        self.song = {"musicId": 100014, "musicTitle": ["テスト", "Test", "", "测试曲", ""],
                     "jacketUrl": "/assets/jp/Assets/AddressableResources/Image/Jacket/jkt_test.png",
                     "difficulty": [{"difficulty": index, "difficultyName": name.lower(),
                                     "playLevel": 6 + index, "noteCount": 20 + index, "scoreId": 543210 + index,
                                     "file": source.ASSET_PREFIX + f"returned_path/chart_{index}.bytes"}
                                    for index, name in enumerate(source.DIFFICULTIES)]}
        self.raw = encoded(SCORE)
        self.requests = []
        self.bad_header_at = None

    def __call__(self, url, limit, timeout):
        self.requests.append((url, limit, timeout))
        headers = {"x-haneoka-release-id": self.identity["releaseId"],
                   "x-haneoka-source-id": self.identity["sourceId"]}
        if len(self.requests) == self.bad_header_at:
            headers["x-haneoka-source-id"] = "wrong-source"
        if url == source.API + "release?projection=identity":
            return encoded(self.identity), headers
        if url == source.API + f"songs/100014?release={self.identity['releaseId']}":
            return encoded(self.song), headers
        if url.startswith(source.ORIGIN + source.ASSET_PREFIX + "returned_path/chart_"):
            return self.raw, headers
        raise AssertionError("Unexpected URL: " + url)


class HaneokaChartTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.charts = tuple(Chart(name, 6 + index, float(6 + index), 20 + index, "ignored-old-path")
                            for index, name in enumerate(source.DIFFICULTIES))
        self.song = Song(100014, "测试曲", ("テスト", "测试曲"), "MyGO!!!!!", "", "", "", "",
                         "https://bdon.yatta.moe/Resources/en/Assets/jkt_test.webp", self.charts)
        self.chart = self.charts[-1]
        self.responses = Responses()
        self.now = 100_000
        patches = ExitStack()
        self.addCleanup(patches.close)
        self.fetch = patches.enter_context(patch.object(source, "public_get", side_effect=self.responses))
        patches.enter_context(patch.object(source.time, "time", side_effect=lambda: self.now))

    def load(self, chart=None):
        return load_chart_data(self.song, chart or self.chart, self.root, source="haneoka")

    def path(self):
        return self.root / source.CACHE_NAME / "100014_EXPERT.json"

    def test_four_difficulties_use_returned_paths_and_preserve_entire_score(self):
        for index, chart in enumerate(self.charts):
            with self.subTest(difficulty=chart.difficulty):
                result = self.load(chart)
                self.assertEqual(result.score, SCORE["score"])
                self.assertEqual(result.release_id, IDENTITY["releaseId"])
                self.assertEqual(result.source_id, IDENTITY["sourceId"])
                self.assertEqual(result.cache_state, "fresh")
                self.assertEqual(self.responses.requests[-1][0], source.ORIGIN + source.ASSET_PREFIX
                                 + f"returned_path/chart_{index}.bytes?release={IDENTITY['releaseId']}")
        self.assertEqual(self.fetch.call_count, 12)
        self.assertTrue(all(url.startswith(source.ORIGIN + "/") and 0 < timeout <= source.TIMEOUT
                            for url, _, timeout in self.responses.requests))
        self.assertEqual(load_chart_score(self.song, self.chart, self.root, source="haneoka"), SCORE["score"])
        self.assertEqual(self.fetch.call_count, 12)

    def test_fresh_cache_is_offline_and_expired_cache_is_explicitly_stale(self):
        self.load()
        original = self.path().read_bytes()
        self.fetch.side_effect = OSError("offline")
        self.assertEqual(self.load().cache_state, "cached")
        self.assertEqual(self.fetch.call_count, 3)
        self.now += source.CHART_CACHE_TTL
        result = self.load()
        self.assertEqual(result.cache_state, "stale")
        self.assertIn("旧缓存", result.notice("zh"))
        self.assertEqual(self.path().read_bytes(), original)

    def test_release_change_replaces_only_a_complete_valid_snapshot(self):
        old = self.load()
        self.now += source.CHART_CACHE_TTL + 1
        self.responses.identity.update(releaseId="r-" + "b" * 20, sourceId="v10060-test")
        self.responses.raw = encoded({"meta": {"version": 100}, "score": {**SCORE["score"], "newField": [1, 2]}})
        result = self.load()
        self.assertNotEqual(result.release_id, old.release_id)
        self.assertEqual(result.source_id, "v10060-test")
        self.assertEqual(result.score["newField"], [1, 2])
        self.assertTrue(all("release=r-" + "b" * 20 in url for url, _, _ in self.responses.requests[-2:]))
        self.assertEqual(self.load().cache_state, "cached")

    def test_failed_new_release_keeps_old_snapshot_and_original_age(self):
        old = self.load()
        original = self.path().read_bytes()
        self.now += source.CHART_CACHE_TTL + 1
        self.responses.identity["releaseId"] = "r-" + "b" * 20
        self.responses.raw = b'not a chart'
        result = self.load()
        self.assertEqual((result.release_id, result.score, result.cache_state),
                         (old.release_id, old.score, "stale"))
        self.assertEqual(self.path().read_bytes(), original)

    def test_identity_headers_are_required_at_every_step(self):
        for step in (1, 2, 3):
            with self.subTest(step=step):
                self.responses.requests.clear()
                self.responses.bad_header_at = step
                with self.assertRaises(ChartDataError):
                    self.load()
                self.assertFalse(self.path().exists())
                self.assertEqual(len(self.responses.requests), step)

    def test_wrong_server_release_or_source_is_rejected(self):
        for field, value in (("server", "intl"), ("releaseId", "latest"), ("sourceId", "")):
            with self.subTest(field=field):
                self.responses.identity = {**IDENTITY, field: value}
                with self.assertRaises(ChartDataError):
                    self.load()
        self.assertFalse(self.path().exists())

    def test_song_and_difficulty_conflicts_are_rejected_before_asset_fetch(self):
        original = copy.deepcopy(self.responses.song)
        changes = [("musicId", 100015), ("musicId", True), ("musicTitle", ["another song"]),
                   ("jacketUrl", "/assets/jp/wrong.png"), ("difficulty", [])]
        for field, value in changes:
            with self.subTest(field=field, value=value):
                self.responses.song = {**original, field: value}
                self.responses.requests.clear()
                with self.assertRaises(ChartDataError):
                    self.load()
                self.assertEqual(len(self.responses.requests), 2)
        for field, value in (("difficulty", True), ("difficultyName", "hard"), ("scoreId", None),
                             ("scoreId", True), ("playLevel", 99), ("noteCount", 99), ("noteCount", False)):
            with self.subTest(field=field, value=value):
                self.responses.song = copy.deepcopy(original)
                self.responses.song["difficulty"][-1][field] = value
                with self.assertRaises(ChartDataError):
                    self.load()
        self.responses.song = copy.deepcopy(original)
        self.responses.song["difficulty"].append(copy.deepcopy(original["difficulty"][-1]))
        with self.assertRaises(ChartDataError):
            self.load()

    def test_unknown_notes_are_not_treated_as_zero(self):
        self.responses.song["difficulty"][-1]["noteCount"] = None
        result = self.load()
        self.assertEqual(result.score, SCORE["score"])
        self.assertIsNone(json.loads(self.path().read_bytes())["song"]["difficulty"][0]["noteCount"])

    def test_untrusted_asset_paths_cannot_escape_the_fixed_server(self):
        for path in ("https://evil.invalid/chart.bytes", "//evil.invalid/a", "/assets/intl/chart.bytes",
                     source.ASSET_PREFIX + "../chart.bytes", source.ASSET_PREFIX + "%2e%2e/chart.bytes",
                     source.ASSET_PREFIX + "chart.bytes?release=other", source.ASSET_PREFIX + "chart.bytes#other",
                     source.ASSET_PREFIX + "a\\chart.bytes"):
            with self.subTest(path=path):
                self.responses.song["difficulty"][-1]["file"] = path
                with self.assertRaises(ChartDataError):
                    self.load()
        self.assertFalse(self.path().exists())

    def test_unsupported_request_and_source_do_not_make_requests(self):
        for chart in (replace(self.chart, difficulty="SPECIAL"), replace(self.chart, level=99)):
            with self.assertRaises(ChartDataError):
                self.load(chart)
        with self.assertRaises(ChartDataError):
            load_chart_score(self.song, self.chart, self.root, source="typo")
        self.fetch.assert_not_called()

    def test_malformed_or_oversized_chart_is_not_cached(self):
        invalid = [b'{"meta":{"version":101},"score":{"notes":[{}],"events":{}}}',
                   b'{"meta":{"version":100},"score":{"notes":[],"events":{}}}',
                   b'{"meta":{"version":100},"score":{"notes":[{}],"events":[]}}',
                   b'{"meta":{"version":100,"version":100},"score":{"notes":[{}],"events":{}}}',
                   encoded(SCORE).replace(b"182.0", b"NaN"), encoded(SCORE).replace(b"182.0", b"1e400"),
                   b" " * (source.MAX_CHART_BYTES + 1)]
        for raw in invalid:
            with self.subTest(prefix=raw[:45]):
                self.responses.raw = raw
                with self.assertRaises(ChartDataError):
                    self.load()
                self.assertFalse(self.path().exists())

    def test_cache_corruption_or_foreign_identity_cannot_be_used_offline(self):
        self.load()
        original = json.loads(self.path().read_bytes())
        variants = [{**original, "sha256": "0" * 64}, {**original, "source": "moenotes"},
                    {**original, "fetched_at": self.now + 1}, {**original, "fetched_at": True},
                    {**original, "fetched_at": 10**400},
                    {**original, "raw_chart": "bad base64!"},
                    {**original, "identity": {**IDENTITY, "server": "intl"}},
                    {**original, "song": {**original["song"], "musicId": 100015}}]
        self.fetch.side_effect = OSError("offline")
        for saved in variants:
            with self.subTest(keys=saved.keys()):
                self.path().write_bytes(encoded(saved))
                with self.assertRaises(ChartDataError):
                    self.load()
        self.path().write_bytes(b" " * (source.MAX_CACHE_BYTES + 1))
        with self.assertRaises(ChartDataError):
            self.load()

    def test_changed_local_identity_cannot_reuse_a_fresh_cache(self):
        self.load()
        changed = replace(self.chart, notes=self.chart.notes + 1)
        self.song = replace(self.song, charts=(*self.charts[:-1], changed))
        self.chart = changed
        self.fetch.side_effect = OSError("offline")
        with self.assertRaises(ChartDataError):
            self.load()

    def test_write_failure_returns_fresh_data_and_preserves_previous_file(self):
        self.load()
        original = self.path().read_bytes()
        self.now += source.CHART_CACHE_TTL + 1
        self.responses.identity["releaseId"] = "r-" + "b" * 20
        with patch.object(source.os, "replace", side_effect=PermissionError("read only")):
            result = self.load()
        self.assertEqual(result.cache_state, "unsaved")
        self.assertEqual(result.release_id, "r-" + "b" * 20)
        self.assertNotIn("旧缓存", result.notice("zh"))
        self.assertEqual(self.path().read_bytes(), original)
        self.assertEqual(list(self.path().parent.glob("*.tmp")), [])

    def test_cache_and_network_are_isolated_from_legacy_source(self):
        legacy = self.root / "0014_0014_03.json"
        legacy.write_bytes(encoded(SCORE))
        self.fetch.side_effect = OSError("offline")
        with patch("ournotes_bot.sources.chart_data.urlopen", side_effect=AssertionError("legacy network")):
            with self.assertRaises(ChartDataError):
                self.load()
        self.assertEqual(legacy.read_bytes(), encoded(SCORE))
        self.fetch.side_effect = self.responses
        self.load()
        self.assertEqual(legacy.read_bytes(), encoded(SCORE))
        with patch("ournotes_bot.sources.chart_data.urlopen", side_effect=AssertionError("warm cache")):
            self.assertEqual(load_chart_score(self.song, self.chart, self.root), SCORE["score"])

    def test_concurrent_same_chart_requests_share_the_completed_cache(self):
        started, proceed = threading.Event(), threading.Event()
        def fetch(url, limit, timeout):
            if not self.responses.requests:
                started.set()
                self.assertTrue(proceed.wait(5))
            return self.responses(url, limit, timeout)
        self.fetch.side_effect = fetch
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.load)
            self.assertTrue(started.wait(5))
            second = pool.submit(self.load)
            proceed.set()
            results = [first.result(), second.result()]
        self.assertEqual([result.cache_state for result in results], ["fresh", "cached"])
        self.assertEqual(self.fetch.call_count, 3)

    def test_renderer_is_identical_for_equal_inputs_and_only_adds_source_footer(self):
        actual = self.load()
        with patch("ournotes_bot.visuals._asset", return_value=None):
            expected = render_chart(self.song, self.charts, "zh", SCORE["score"], "EXPERT")
            rendered = render_chart(self.song, self.charts, "zh", actual.score, "EXPERT")
            annotated = render_chart(self.song, self.charts, "zh", actual.score, "EXPERT",
                                     source_notice=replace(actual, cache_state="stale").notice("zh"))
        self.assertEqual(rendered, expected)
        self.assertNotEqual(annotated, expected)
        from PIL import Image
        with Image.open(io.BytesIO(annotated)) as image, Image.open(io.BytesIO(expected)) as baseline:
            self.assertEqual(image.size, baseline.size)
        self.assertLessEqual(len(annotated), 1_500_000)


class TransportTests(unittest.TestCase):
    def test_redirects_and_oversized_streams_are_rejected(self):
        class Response:
            status = 302
            headers = {}
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                pass
            @property
            def content(self):
                return self
            async def iter_chunked(self, size):
                yield b"12345"
        class Session(Response):
            def get(self, url, **kwargs):
                self.request_options = kwargs
                return self
        session = Session()
        with patch("aiohttp.ClientSession", return_value=session) as create:
            with self.assertRaises(ChartDataError):
                source.public_get(source.API + "release?projection=identity", 4, 1)
            self.assertFalse(session.request_options["allow_redirects"])
            self.assertFalse(create.call_args.kwargs["trust_env"])
            self.assertEqual(create.call_args.kwargs["timeout"].total, 1)
            session.status = 200
            with self.assertRaises(ChartDataError):
                source.public_get(source.API + "release?projection=identity", 4, 1)


class ChartConfigurationTests(unittest.TestCase):
    def test_source_is_explicit_and_does_not_change_other_sources(self):
        with patch("ournotes_bot.config.load_dotenv"), patch.dict(os.environ, {}, clear=True):
            self.assertEqual(Settings.from_env().chart_source, "moenotes")
            os.environ["OURNOTES_CHART_SOURCE"] = "haneoka"
            settings = Settings.from_env()
            self.assertEqual((settings.chart_source, settings.meta_source, settings.cutoff_source),
                             ("haneoka", "moenotes", "tracker"))
            os.environ["OURNOTES_CHART_SOURCE"] = "typo"
            with self.assertRaises(ValueError):
                Settings.from_env()
        with tempfile.TemporaryDirectory() as directory:
            repo = SongRepository("", Path(directory) / "cache.json", chart_source="haneoka")
            self.assertEqual(repo.chart_source, "haneoka")
            self.assertEqual(repo.meta_source, "moenotes")
            with self.assertRaises(ValueError):
                SongRepository("", Path(directory) / "cache.json", chart_source="typo")


if __name__ == "__main__":
    unittest.main()
