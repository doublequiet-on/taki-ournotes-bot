from __future__ import annotations
from ournotes_bot.query.card_catalog import query_cards

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from ournotes_bot.ai_query import AIQueryParser
from ournotes_bot.commands import card_matches, song_matches, support_card_matches
from ournotes_bot.config import Settings
from ournotes_bot.data import Card, Chart, Song, SongRepository, SupportCard
from ournotes_bot.platforms.qq.qq import PreparedReply, _deliver_reply, _prepare_reply, run_bot
from ournotes_bot.structured_query import songs_for
from ournotes_bot.sources.yatta import BASE


class ReplyPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = SongRepository(BASE, Path(self.temp.name) / "cache.json")
        self.repo.songs = [Song(
            id=100001, title="迷星叫", titles=("迷星叫",), band="MyGO!!!!!",
            composer="", lyricist="", arranger="", start_at="", jacket_url="",
            charts=(Chart("EXPERT", 25, 25.0, 768, ""),),
            localized={"title": {"zh": "迷星叫"}},
        )]
        self.repo.cards = [Card(
            id=1, asset_id=1, title="我们现在就在这里", character="高松灯", band="MyGO!!!!!",
            rarity=2, card_type=0, performance=0, technic=0, visual=0, start_at="",
            skill_name="", full_url="", thumbnail_url="", localized={},
        )]
        self.repo.support_cards = [SupportCard(
            id=1, title="并肩前行", character="高松灯", characters=("高松灯",),
            rarity=3, card_type=5, performance=0, technic=0, visual=0, start_at="",
            full_url="", thumbnail_url="", localized={},
        )]
        self.repo.metadata = {"cached_at": "test"}
        settings = Settings("", "", BASE, self.repo.cache_file, 6,
                            "test-key", "test-model", "https://ai.example", 100)
        self.parser = AIQueryParser(settings)

    def tearDown(self):
        self.temp.cleanup()

    def test_direct_and_natural_text_images_share_one_selection(self):
        with patch("ournotes_bot.commands.song_matches", wraps=song_matches) as select, \
             patch("ournotes_bot.platforms.qq.qq.render_song_list", return_value=b"image") as render:
            result = _prepare_reply("/查曲 迷星叫", self.repo, self.parser)
        self.assertEqual(result.image, b"image")
        self.assertIn("100001", result.text)
        self.assertEqual(select.call_count, 1)
        self.assertEqual([song.id for song in render.call_args.args[0]], [100001])

        with patch("ournotes_bot.structured_query.songs_for", wraps=songs_for) as select, \
             patch.object(self.parser, "_request", side_effect=AssertionError("AI should not be called")), \
             patch("ournotes_bot.platforms.qq.qq.render_song_list", return_value=b"natural image") as render:
            result = _prepare_reply("/问 MyGO的歌有哪些", self.repo, self.parser)
        self.assertEqual(result.image, b"natural image")
        self.assertIn("100001", result.text)
        self.assertEqual(select.call_count, 1)
        self.assertEqual([song.id for song in render.call_args.args[0]], [100001])

    def test_chart_and_card_text_images_share_one_selection(self):
        from ournotes_bot.query.song_identity import unique_candidates
        with patch("ournotes_bot.query.song_identity.unique_candidates", wraps=unique_candidates) as select, \
             patch("ournotes_bot.platforms.qq.qq.load_chart_score", return_value={"notes": []}), \
             patch("ournotes_bot.platforms.qq.qq.render_chart", return_value=b"chart") as render:
            result = _prepare_reply("/查谱面 100001 EXPERT", self.repo, self.parser)
        self.assertIn("768 Notes", result.text)
        self.assertEqual(result.image, b"chart")
        self.assertEqual(select.call_count, 1)
        self.assertEqual(render.call_args.args[0].id, 100001)

        with patch("ournotes_bot.commands.query_cards", wraps=query_cards) as select, \
             patch("ournotes_bot.platforms.qq.qq.render_card_list", return_value=b"cards") as render:
            result = _prepare_reply("/查卡 高松灯", self.repo, self.parser)
        self.assertIn("我们现在就在这里", result.text)
        self.assertEqual(result.image, b"cards")
        self.assertEqual(select.call_count, 1)
        self.assertEqual([card.id for card in render.call_args.args[0]], [1])

        with patch("ournotes_bot.commands.query_cards", wraps=query_cards) as select, \
             patch("ournotes_bot.platforms.qq.qq.render_support_card_list", return_value=b"support cards") as render:
            result = _prepare_reply("/查支援卡 高松灯", self.repo, self.parser)
        self.assertIn("并肩前行", result.text)
        self.assertEqual(result.image, b"support cards")
        self.assertEqual(select.call_count, 1)
        self.assertEqual([card.id for card in render.call_args.args[0]], [1])

    def test_ai_timeout_returns_safe_text_without_image(self):
        with patch.object(self.parser, "_request", side_effect=TimeoutError("Bearer secret-value")):
            result = _prepare_reply("/问 展示迷星叫 EXPERT 完整谱面资料", self.repo, self.parser)
        self.assertIn("自然语言解析暂不可用", result.text)
        self.assertIsNone(result.image)
        self.assertNotIn("secret-value", result.text)

    def test_answer_and_render_failures_do_not_leak_raw_errors(self):
        with patch("ournotes_bot.platforms.qq.qq.handle_command", side_effect=RuntimeError("Bearer secret-value")), \
             self.assertLogs("ournotes_bot.qq", level="ERROR") as log:
            result = _prepare_reply("/查曲 迷星叫", self.repo, self.parser)
        self.assertEqual(result.text, "查询暂时失败，请稍后重试。")
        self.assertIsNone(result.image)
        self.assertNotIn("secret-value", " ".join(log.output))

        with patch("ournotes_bot.platforms.qq.qq.render_song_list", side_effect=RuntimeError("Bearer secret-value")), \
             self.assertLogs("ournotes_bot.qq", level="WARNING") as log:
            result = _prepare_reply("/查曲 迷星叫", self.repo, self.parser)
        self.assertIn("迷星叫", result.text)
        self.assertIsNone(result.image)
        self.assertNotIn("secret-value", " ".join(log.output))

    @staticmethod
    def message(*, upload_error=None, send_error=None):
        http = SimpleNamespace(request=AsyncMock(
            side_effect=upload_error, return_value={"file_info": "fake-upload"},
        ))
        api = SimpleNamespace(
            _http=http,
            post_group_message=AsyncMock(side_effect=send_error),
            post_c2c_message=AsyncMock(side_effect=send_error),
        )
        return SimpleNamespace(id="fake-message", _api=api)

    def test_upload_failure_sends_only_one_text_fallback(self):
        message = self.message(upload_error=TimeoutError("Bearer secret-value"))
        with self.assertLogs("ournotes_bot.qq", level="WARNING") as log:
            asyncio.run(_deliver_reply(message, "fake-group", True, PreparedReply("安全文字", b"image")))
        send = message._api.post_group_message
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.kwargs["msg_type"], 0)
        self.assertEqual(send.await_args.kwargs["content"], "安全文字")
        self.assertNotIn("secret-value", " ".join(log.output))

    def test_missing_upload_receipt_also_falls_back_to_text(self):
        message = self.message()
        message._api._http.request.return_value = {"error": "Bearer secret-value"}
        with self.assertLogs("ournotes_bot.qq", level="WARNING") as log:
            asyncio.run(_deliver_reply(message, "fake-group", True, PreparedReply("安全文字", b"image")))
        self.assertEqual(message._api.post_group_message.await_count, 1)
        self.assertEqual(message._api.post_group_message.await_args.kwargs["msg_type"], 0)
        self.assertNotIn("secret-value", " ".join(log.output))

    def test_private_media_success_uses_one_media_message(self):
        message = self.message()
        asyncio.run(_deliver_reply(message, "fake-user", False, PreparedReply("安全文字", b"image")))
        send = message._api.post_c2c_message
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.kwargs["msg_type"], 7)
        self.assertEqual(send.await_args.kwargs["openid"], "fake-user")
        self.assertNotIn("content", send.await_args.kwargs)

    def test_media_send_timeout_is_not_blindly_retried_as_text(self):
        message = self.message(send_error=TimeoutError("Bearer secret-value"))
        with self.assertLogs("ournotes_bot.qq", level="ERROR") as log:
            asyncio.run(_deliver_reply(message, "fake-group", True, PreparedReply("安全文字", b"image")))
        send = message._api.post_group_message
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.kwargs["msg_type"], 7)
        self.assertIn("结果不确定", " ".join(log.output))
        self.assertNotIn("secret-value", " ".join(log.output))

    def test_final_text_failure_is_logged_without_retry(self):
        message = self.message(send_error=RuntimeError("Bearer secret-value"))
        with self.assertLogs("ournotes_bot.qq", level="ERROR") as log:
            asyncio.run(_deliver_reply(message, "fake-user", False, PreparedReply("安全文字")))
        send = message._api.post_c2c_message
        self.assertEqual(send.await_count, 1)
        self.assertEqual(send.await_args.kwargs["msg_type"], 0)
        self.assertEqual(send.await_args.kwargs["openid"], "fake-user")
        self.assertNotIn("secret-value", " ".join(log.output))


class CatalogRefreshScheduleTests(unittest.IsolatedAsyncioTestCase):
    async def check_schedule(self, start, durations, expected, *, fail_first=False):
        from datetime import datetime

        clock = [datetime.fromisoformat(start).timestamp()]
        waits = []
        instances = []

        class FakeClient:
            def __init__(self, **kwargs):
                self.robot = SimpleNamespace(name="Taki")
                self._song_traits_task = Mock(done=Mock(return_value=False))
                instances.append(self)

            def run(self, **kwargs):
                pass

        async def sleep(seconds):
            waits.append(seconds)
            if len(waits) == len(expected):
                raise asyncio.CancelledError
            clock[0] += seconds

        def refresh():
            clock[0] += durations.pop(0)
            if fail_first and repository.refresh.call_count == 1:
                raise OSError("synthetic refresh failure")

        async def to_thread(func):
            return func()

        repository = SimpleNamespace(meta_source="haneoka", songs=[], refresh=Mock(side_effect=refresh),
                                     event_cutoffs=SimpleNamespace(history=True))
        with tempfile.TemporaryDirectory() as folder:
            settings = Settings("test-app", "", BASE, Path(folder)/"cache.json", 6,
                                qq_gateway_host="", update_notices=False)
            with patch("botpy.Client", FakeClient), \
                 patch("ournotes_bot.platforms.qq.qq.time", SimpleNamespace(time=lambda: clock[0])), \
                 patch("ournotes_bot.platforms.qq.qq.asyncio.sleep", side_effect=sleep), \
                 patch("ournotes_bot.platforms.qq.qq.asyncio.to_thread", side_effect=to_thread):
                run_bot("test-app", "", repository, settings)
                client = instances[-1]
                await client.on_ready()
                task = client._refresh_task
                await client.on_ready()
                self.assertIs(client._refresh_task, task)
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(waits, expected)
        self.assertEqual(repository.refresh.call_count, len(expected) - 1)

    async def test_refresh_duration_does_not_shift_half_hour_slots(self):
        await self.check_schedule("2026-10-09T14:12:00+08:00", [60, 1900], [1080, 1740, 1700])

    async def test_boundary_and_midnight_wait_for_next_slot(self):
        for start, delay in (("2026-10-09T14:00:00+08:00", 1800),
                             ("2026-10-09T14:30:00+08:00", 1800),
                             ("2026-10-09T23:59:59.500+08:00", .5)):
            with self.subTest(start=start):
                await self.check_schedule(start, [0], [delay, 1800])

    async def test_failed_refresh_resumes_at_next_boundary(self):
        with self.assertLogs("ournotes_bot.qq", level="ERROR"):
            await self.check_schedule("2026-10-09T14:12:00+08:00", [60, 0],
                                      [1080, 1740, 1800], fail_first=True)


class SDKStartupLoopTests(unittest.TestCase):
    def setUp(self):
        policy = asyncio.get_event_loop_policy()
        self.addCleanup(asyncio.set_event_loop_policy, policy)
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings("test-app", "", BASE, Path(self.temp.name)/"cache.json", 6,
                                 qq_gateway_host="", update_notices=False)
        self.repository = SimpleNamespace(meta_source="haneoka", event_cutoffs=SimpleNamespace(history=True))
        self.seen = []

    def start_without_login(self, *, fail=False):
        def run(client, **kwargs):
            self.seen.append(client.loop)
            self.assertFalse(client.loop.is_closed())
            self.assertIs(asyncio.get_event_loop(), client.loop)
            client.loop.run_until_complete(asyncio.sleep(0))
            if fail:
                raise RuntimeError("synthetic SDK failure")

        # Keep the actual SDK constructor. Block network/login and SDK log files.
        with patch("botpy.Client.run", autospec=True, side_effect=run) as run_mock, \
             patch("botpy.Client.start", side_effect=AssertionError("login forbidden")), \
             patch("botpy.logging.configure_logging"), \
             patch("aiohttp.ClientSession", side_effect=AssertionError("network forbidden")):
            run_bot("test-app", "", self.repository, self.settings)
            run_mock.assert_called_once()

    def test_actual_sdk_starts_after_asyncio_run_and_releases_owned_loop(self):
        asyncio.run(asyncio.sleep(0))
        self.start_without_login()
        self.assertTrue(self.seen[0].is_closed())
        with self.assertRaises(RuntimeError):
            asyncio.get_event_loop()

    def test_actual_sdk_replaces_a_closed_current_loop(self):
        closed = asyncio.new_event_loop()
        asyncio.set_event_loop(closed)
        closed.close()
        self.start_without_login()
        self.assertIsNot(self.seen[0], closed)
        self.assertTrue(self.seen[0].is_closed())

    def test_actual_sdk_preserves_an_existing_usable_loop(self):
        existing = asyncio.new_event_loop()
        self.addCleanup(existing.close)
        asyncio.set_event_loop(existing)
        self.start_without_login()
        self.assertIs(self.seen[0], existing)
        self.assertFalse(existing.is_closed())
        self.assertIs(asyncio.get_event_loop(), existing)

    def test_sdk_failure_releases_only_the_owned_loop(self):
        asyncio.set_event_loop(None)
        with self.assertRaisesRegex(RuntimeError, "synthetic SDK failure"):
            self.start_without_login(fail=True)
        self.assertTrue(self.seen[0].is_closed())
        with self.assertRaises(RuntimeError):
            asyncio.get_event_loop()


class UpdateNoticeLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_music_data_worker_is_wired_only_for_moenotes_and_stops_on_exit(self):
        instances = []
        class FakeClient:
            def __init__(self, **kwargs):
                self.robot = SimpleNamespace(name="Taki")
                self.api = SimpleNamespace()
                instances.append(self)
            def run(self, **kwargs):
                pass

        with tempfile.TemporaryDirectory() as folder:
            settings = Settings("test-app", "", BASE, Path(folder)/"cache.json", 6,
                                qq_gateway_host="", update_notices=False)
            repository = SimpleNamespace(meta_source="moenotes", music_data=Mock(),
                                         refresh_song_traits=Mock(), event_cutoffs=SimpleNamespace(history=True))
            with patch("botpy.Client", FakeClient), patch("ournotes_bot.sources.moenotes_music_data.MusicDataRefresher") as factory:
                run_bot("test-app", "", repository, settings)
                factory.assert_called_once_with(repository.music_data)
                factory.return_value.stop.assert_called_once()
                client = instances[-1]
                try:
                    await client.on_ready()
                    await client.on_ready()
                    self.assertEqual(factory.return_value.start.call_count, 2)
                    factory.assert_called_once()
                finally:
                    client._refresh_task.cancel()
                    client._song_traits_task.cancel()
                    await asyncio.gather(client._refresh_task, client._song_traits_task, return_exceptions=True)
                repository.meta_source = "haneoka"
                factory.reset_mock()
                run_bot("test-app", "", repository, settings)
                factory.assert_not_called()

    async def test_ready_reconnect_events_and_disable_switch(self):
        instances = []
        class FakeClient:
            def __init__(self, **kwargs):
                self.robot = SimpleNamespace(name="Taki")
                self.api = SimpleNamespace()
                instances.append(self)

            def run(self, **kwargs):
                pass

        with tempfile.TemporaryDirectory() as folder:
            settings = Settings("test-app", "", BASE, Path(folder) / "cache.json", 6, qq_gateway_host="")
            notifier = Mock()
            notifier.run = AsyncMock(side_effect=lambda api: asyncio.sleep(0))
            # Keep the worker alive so a second READY cannot create a duplicate.
            stopped = asyncio.Event()
            async def worker(api):
                await stopped.wait()
            notifier.run.side_effect = worker
            with patch("botpy.Client", FakeClient), patch("ournotes_bot.platforms.qq.qq.UpdateNotifier", return_value=notifier):
                run_bot("test-app", "", SimpleNamespace(meta_source="haneoka", refresh_song_traits=Mock(), event_cutoffs=SimpleNamespace(history=None)), settings)
                client = instances[-1]
                try:
                    await client.on_ready()
                    await asyncio.sleep(0)
                    first = client._notice_task
                    first_traits = client._song_traits_task
                    await client.on_ready()
                    self.assertIs(first, client._notice_task)
                    self.assertIs(first_traits, client._song_traits_task)
                    notifier.run.assert_awaited_once_with(client.api)
                    event = SimpleNamespace(group_openid="group", timestamp=123)
                    for name, expected in (("add_robot", "add"), ("del_robot", "remove"),
                                           ("msg_receive", "allow"), ("msg_reject", "reject")):
                        await getattr(client, "on_group_" + name)(event)
                        notifier.observe.assert_called_with("group", event=expected, timestamp=123)
                finally:
                    client._refresh_task.cancel()
                    client._notice_task.cancel()
                    client._song_traits_task.cancel()
                    await asyncio.gather(client._refresh_task, client._notice_task, client._song_traits_task, return_exceptions=True)
            with patch("botpy.Client", FakeClient), patch("ournotes_bot.platforms.qq.qq.UpdateNotifier") as constructor:
                settings = Settings("test-app", "", BASE, Path(folder) / "cache.json", 6,
                                    qq_gateway_host="", update_notices=False)
                run_bot("test-app", "", SimpleNamespace(meta_source="haneoka", refresh_song_traits=Mock(), event_cutoffs=SimpleNamespace(history=None)), settings)
                client = instances[-1]
                await client.on_ready()
                self.assertFalse(hasattr(client, "_notice_task"))
                constructor.assert_not_called()
                client._refresh_task.cancel()
                client._song_traits_task.cancel()
                await asyncio.gather(client._refresh_task, client._song_traits_task, return_exceptions=True)


class EasterEggTests(unittest.TestCase):
    def test_original_image_without_repository_or_ai(self):
        import hashlib
        from ournotes_bot.platforms.qq.qq import _image_reply
        for content in ("/查卡 947", "查卡 947", "／查卡 947", "<@!12345> /查卡  947", " /查卡\t947 "):
            with self.subTest(content=content), patch(
                "ournotes_bot.platforms.qq.qq.resolve_command", side_effect=AssertionError("normal query called")
            ), patch("ournotes_bot.platforms.qq.qq.catalog_version", side_effect=AssertionError("catalog called")):
                reply = _prepare_reply(content, None, None)
                self.assertEqual(hashlib.sha256(reply.image).hexdigest(),
                                 "ed23f7f33a54c01b5b636cb312f606662d177cfb3d74cdb459d0b6c1315aa81c")
                self.assertEqual(_image_reply(content, None), reply.image)
                self.assertIsNone(reply.context)

    def test_other_queries_keep_the_normal_route(self):
        from ournotes_bot.platforms.qq.qq import _easter_egg_reply
        for content in ("/查卡 1", "/查卡 9470", "/查卡 0947", "/查卡 947 SSR",
                        "/查卡面 947", "/查角色卡 947", "/card 947", "/查支援卡 947",
                        "/问 查卡 947", "/查卡947", "/查卡 947\n/查曲 MyGO"):
            with self.subTest(content=content):
                self.assertIsNone(_easter_egg_reply(content))
        with patch("ournotes_bot.platforms.qq.qq.resolve_command", side_effect=RuntimeError("normal route")) as resolve:
            reply = _prepare_reply("/查卡 947 SSR", None, None)
        resolve.assert_called_once()
        self.assertIsNone(reply.image)

    def test_missing_asset_does_not_fall_through_to_card_query(self):
        with patch("ournotes_bot.platforms.qq.qq.files") as resources, patch(
            "ournotes_bot.platforms.qq.qq.resolve_command", side_effect=AssertionError("normal query called")
        ):
            resources.return_value.joinpath.return_value.read_bytes.side_effect = FileNotFoundError()
            reply = _prepare_reply("/查卡 947", None, None)
        self.assertIn("彩蛋图片暂时不可用", reply.text)
        self.assertIsNone(reply.image)

    def test_media_delivery_and_upload_fallback(self):
        reply = _prepare_reply("/查卡 947", None, None)
        for group in (True, False):
            with self.subTest(group=group):
                message = ReplyPipelineTests.message()
                asyncio.run(_deliver_reply(message, "target", group, reply))
                send = message._api.post_group_message if group else message._api.post_c2c_message
                self.assertEqual(send.await_count, 1)
                self.assertEqual(send.await_args.kwargs["msg_type"], 7)
                self.assertNotIn("content", send.await_args.kwargs)
        message = ReplyPipelineTests.message(upload_error=TimeoutError())
        asyncio.run(_deliver_reply(message, "target", True, reply))
        self.assertEqual(message._api.post_group_message.await_count, 1)
        self.assertEqual(message._api.post_group_message.await_args.kwargs["content"], reply.text)


if __name__ == "__main__":
    unittest.main()
