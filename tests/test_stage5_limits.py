from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.natural_query.ai_quota import BEIJING, DailyQuota, QuotaUnavailable
from ournotes_bot.config import Settings
from ournotes_bot.data import Chart, Song, SongRepository
from ournotes_bot.platforms.qq.qq import PreparedReply, QueryGate


class DailyQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ai-quota.json"
        self.now = datetime(2026, 9, 25, 15, 59, tzinfo=timezone.utc)

    def tearDown(self):
        self.temp.cleanup()

    def quota(self):
        return DailyQuota(self.path, lambda: self.now)

    def test_concurrent_reservations_do_not_exceed_limit_and_survive_restart(self):
        quota = self.quota()
        with ThreadPoolExecutor(max_workers=12) as executor:
            granted = list(executor.map(lambda _: quota.reserve(5), range(30)))
        self.assertEqual(sum(granted), 5)
        self.assertFalse(self.quota().reserve(5))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["used"], 5)

    def test_beijing_midnight_rollover_and_clock_rollback(self):
        quota = self.quota()
        self.assertTrue(quota.reserve(1))
        self.now += timedelta(minutes=2)
        self.assertTrue(self.quota().reserve(1))
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["day"], "2026-09-26")
        self.now -= timedelta(days=1)
        with self.assertRaises(QuotaUnavailable):
            self.quota().reserve(1)

    def test_corrupt_or_disappearing_record_fails_closed(self):
        self.path.write_text('{"schema":1,"day":"bad","used":0}', encoding="utf-8")
        with self.assertRaises(QuotaUnavailable):
            self.quota().reserve(10)
        self.path.write_text('{not json', encoding="utf-8")
        with self.assertRaises(QuotaUnavailable):
            self.quota().reserve(10)
        self.path.unlink()
        quota = self.quota()
        self.assertTrue(quota.reserve(10))
        self.path.unlink()
        with self.assertRaises(QuotaUnavailable):
            quota.reserve(10)

    def test_failed_provider_attempt_is_charged_but_local_queries_work(self):
        repository = SongRepository("https://bdon.yatta.moe", Path(self.temp.name) / "cache.json")
        repository.songs = [Song(
            id=100001, title="迷星叫", titles=("迷星叫",), band="MyGO!!!!!",
            composer="", lyricist="", arranger="", start_at="", jacket_url="",
            charts=(Chart("EXPERT", 25, 25.0, 768, ""),), localized={},
        )]
        settings = Settings("", "", repository.data_base, repository.cache_file, 6,
                            "test-key", "test-model", "https://ai.example", 1,
                            ai_quota_file=self.path)
        parser = AIQueryParser(settings)
        with patch.object(parser, "_request", side_effect=TimeoutError("secret")) as request:
            self.assertIn("暂不可用", parser.answer("/问 展示迷星叫 EXPERT 完整谱面资料", repository))
            self.assertIn("额度", parser.answer("/问 列出迷星叫 EXPERT 完整谱面资料", repository))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["used"], 1)
        self.assertIn("迷星叫", parser.answer("/问 MyGO的歌有哪些", repository))
        self.assertIn("迷星叫", parser.answer("/查曲 迷星叫", repository))
        self.path.write_text('{not json', encoding="utf-8")
        broken_parser = AIQueryParser(settings)
        with patch.object(broken_parser, "_request") as request:
            self.assertIn("额度记录不可用", broken_parser.answer("/问 展示迷星叫 EXPERT 完整谱面资料", repository))
        request.assert_not_called()
        self.assertIn("迷星叫", AIQueryParser(settings).answer("/问 MyGO的歌有哪些", repository))
        no_ai = AIQueryParser(Settings("", "", repository.data_base, repository.cache_file, 6))
        self.assertIn("迷星叫", no_ai.answer("/查曲 迷星叫", repository))
        self.assertIn("迷星叫", no_ai.answer("/问 MyGO的歌有哪些", repository))
        self.assertIn("尚未配置 AI", no_ai.answer("/问 展示迷星叫 EXPERT 完整谱面资料", repository))

    def test_configured_limits_and_quota_path(self):
        with patch("ournotes_bot.config.load_dotenv"), patch.dict(os.environ, {
            "OURNOTES_CACHE_FILE": str(Path(self.temp.name) / "cache.json"),
            "OURNOTES_AI_QUOTA_FILE": str(self.path),
            "OURNOTES_QUERY_CONCURRENCY": "3",
            "OURNOTES_QUERY_QUEUE_LIMIT": "0",
        }):
            settings = Settings.from_env()
        self.assertEqual((settings.query_concurrency, settings.query_queue_limit), (3, 0))
        self.assertEqual(settings.ai_quota_file, self.path)


class QueryGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_bounded_workers_queue_and_request_owned_images(self):
        gate = QueryGate(2, 1)
        release = threading.Event()
        lock = threading.Lock()
        state = {"active": 0, "peak": 0, "started": 0}

        def prepare(content, repository, parser):
            with lock:
                state["active"] += 1
                state["started"] += 1
                state["peak"] = max(state["peak"], state["active"])
            release.wait(timeout=5)
            with lock:
                state["active"] -= 1
            return PreparedReply(content, content.encode())

        with patch("ournotes_bot.platforms.qq.qq._prepare_reply", side_effect=prepare):
            tasks = [asyncio.create_task(gate.prepare(str(i), None, None)) for i in range(2)]
            try:
                for _ in range(100):
                    if state["started"] == 2:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(state["started"], 2)
                queued = asyncio.create_task(gate.prepare("2", None, None))
                for _ in range(100):
                    if gate._pending == 3:
                        break
                    await asyncio.sleep(0.01)
                self.assertEqual(gate._pending, 3)
                busy = await gate.prepare("3", None, None)
                self.assertIn("查询较多", busy.text)
                self.assertEqual(state["started"], 2)
            finally:
                release.set()
            results = await asyncio.gather(*tasks, queued)
        self.assertEqual(state["peak"], 2)
        self.assertEqual([(item.text, item.image) for item in results],
                         [(str(i), str(i).encode()) for i in range(3)])

    async def test_cancelled_request_keeps_slot_until_worker_finishes(self):
        gate = QueryGate(1, 0)
        started = threading.Event()
        release = threading.Event()

        def prepare(content, repository, parser):
            started.set()
            release.wait(timeout=5)
            return PreparedReply(content)

        with patch("ournotes_bot.platforms.qq.qq._prepare_reply", side_effect=prepare):
            task = asyncio.create_task(gate.prepare("first", None, None))
            try:
                for _ in range(100):
                    if started.is_set():
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(started.is_set())
                task.cancel()
                await asyncio.sleep(0)
                busy = await gate.prepare("second", None, None)
                self.assertIn("查询较多", busy.text)
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
