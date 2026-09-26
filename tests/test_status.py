from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ournotes_bot.commands import handle_command
from ournotes_bot.data import DataError, SongRepository
from ournotes_bot.yatta import BASE


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cache.json"
        repo = SongRepository(BASE, self.path)
        repo.metadata = {
            "source": BASE, "schema": 2, "data_version": "Bearer secret-value",
            "cached_at": "2026-09-25T12:00:00+00:00",
        }
        repo._save_cache()

    def tearDown(self):
        self.temp.cleanup()

    def test_saved_cache_and_stale_fallback_are_distinct(self):
        repo = SongRepository(BASE, self.path)
        repo.load()
        self.assertEqual(repo.cache_state, "cached")
        saved = handle_command("/数据状态", repo)
        self.assertIn("正在响应本次查询", saved)
        self.assertIn("使用已保存缓存", saved)
        self.assertIn("2026-09-25 20:00 UTC+08:00", saved)
        self.assertNotIn("secret-value", saved)
        self.assertNotIn(str(self.path), saved)

        os.utime(self.path, (0, 0))
        with patch.object(repo, "refresh", side_effect=DataError("offline")):
            repo.load()
        self.assertEqual(repo.cache_state, "stale")
        self.assertIn("正在使用旧缓存", handle_command("/数据状态", repo))
        self.assertIn("Using older cache", handle_command("/status", repo))

    def test_live_refresh_failure_and_success_update_status(self):
        repo = SongRepository(BASE, self.path)
        repo.load()
        with patch("ournotes_bot.yatta.fetch_json", side_effect=OSError("offline")):
            with self.assertRaises(DataError):
                repo.refresh()
        self.assertEqual(repo.cache_state, "stale")
        self.assertIn("最近一次同步失败", handle_command("/数据状态", repo))

        with patch("ournotes_bot.yatta.fetch_json", return_value={}), \
             patch("ournotes_bot.yatta.build_data", return_value=([], [])):
            repo.refresh()
        self.assertEqual(repo.cache_state, "fresh")
        self.assertIn("本次运行同步成功", handle_command("/数据状态", repo))
        self.assertIsNotNone(repo.last_successful_sync_at)

    def test_untrusted_timestamp_and_failed_save_do_not_claim_success(self):
        repo = SongRepository(BASE, self.path)
        repo.load()
        previous = repo.last_successful_sync_at
        with patch("ournotes_bot.yatta.fetch_json", return_value={}), \
             patch("ournotes_bot.yatta.build_data", return_value=([], [])), \
             patch.object(repo, "_save_cache", side_effect=OSError("private path")):
            with self.assertRaises(DataError):
                repo.refresh()
        self.assertEqual(repo.cache_state, "unsaved")
        self.assertEqual(repo.last_successful_sync_at, previous)
        self.assertIn("未能保存缓存", handle_command("/数据状态", repo))
        repo.last_successful_sync_at = "C:\\private\\secret"
        reply = handle_command("/数据状态", repo)
        self.assertIn("最近成功同步：未知", reply)
        self.assertNotIn("private", reply)


if __name__ == "__main__":
    unittest.main()
