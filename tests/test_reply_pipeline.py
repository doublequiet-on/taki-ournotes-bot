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
        with patch.object(self.repo, "search", wraps=self.repo.search) as select, \
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
            result = _prepare_reply("/问 迷星叫EX物量", self.repo, self.parser)
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


class UpdateNoticeLifecycleTests(unittest.IsolatedAsyncioTestCase):
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
                run_bot("test-app", "", SimpleNamespace(refresh_song_traits=Mock()), settings)
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
                run_bot("test-app", "", SimpleNamespace(refresh_song_traits=Mock()), settings)
                client = instances[-1]
                await client.on_ready()
                self.assertFalse(hasattr(client, "_notice_task"))
                constructor.assert_not_called()
                client._refresh_task.cancel()
                client._song_traits_task.cancel()
                await asyncio.gather(client._refresh_task, client._song_traits_task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()
