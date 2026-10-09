"""Offline large-image and upload timeout regressions, no QQ credentials."""
import asyncio
import io
import random
import weakref
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch, AsyncMock, Mock

from PIL import Image, ImageDraw
from botpy.http import BotHttp
from ournotes_bot.rendering.image_output import encode_image, MAX_IMAGE_BYTES, MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS
from ournotes_bot.rendering import image_output
from ournotes_bot import visuals
from ournotes_bot.platforms.qq.qq import _upload_image
from types import SimpleNamespace


class ImageOutputTests(unittest.TestCase):
    def test_oversized_noise_is_bounded_and_remains_complete(self):
        im = Image.frombytes("RGB", (1400, 1000), random.Random(7).randbytes(1400 * 1000 * 3))
        with patch("ournotes_bot.rendering.image_output.MAX_IMAGE_BYTES", 120_000):
            blob = encode_image(im)
        self.assertLessEqual(len(blob), 120_000)
        with Image.open(io.BytesIO(blob)) as result:
            self.assertAlmostEqual(result.width / result.height, 1.4, delta=.01)
            self.assertEqual(result.format, "JPEG")

    def test_long_image_keeps_top_and_bottom_and_caps_dimensions(self):
        im = Image.new("RGB", (1000, 18000), "white")
        im.paste("red", (0, 0, 1000, 1000))
        im.paste("blue", (0, 17000, 1000, 18000))
        blob = encode_image(im)
        self.assertLessEqual(len(blob), MAX_IMAGE_BYTES)
        with Image.open(io.BytesIO(blob)) as result:
            self.assertLessEqual(max(result.size), MAX_IMAGE_EDGE)
            self.assertLessEqual(result.width * result.height, MAX_IMAGE_PIXELS)
            self.assertGreater(result.getpixel((result.width // 2, 10))[0], 240)
            self.assertGreater(result.getpixel((result.width // 2, result.height - 10))[2], 240)

    def test_transparency_is_flattened_on_white(self):
        blob = encode_image(Image.new("RGBA", (80, 80), (255, 0, 0, 0)))
        with Image.open(io.BytesIO(blob)) as result:
            self.assertEqual(result.size, (80, 80))
            self.assertEqual(result.getpixel((40, 40)), (255, 255, 255))

    def test_borrowed_canvas_remains_usable_and_temporary_images_close(self):
        image = Image.new("RGBA", (80, 80), (255, 0, 0, 100))
        created = []
        original_new = Image.new

        def new(*args, **kwargs):
            result = original_new(*args, **kwargs)
            created.append(result)
            return result

        with patch.object(image_output.Image, "new", side_effect=new):
            blob = encode_image(image)
        self.assertTrue(blob)
        self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 100))
        for temporary in created:
            with self.assertRaises(ValueError):
                temporary.getpixel((0, 0))
        image.close()

    def test_resized_canvas_closes_on_success_and_encoding_failure(self):
        original_resize = Image.Image.resize
        for failure in (False, True):
            image = Image.new("RGB", (100, 200), "white")
            created = []

            def resize(*args, **kwargs):
                result = original_resize(*args, **kwargs)
                created.append(result)
                return result

            with self.subTest(failure=failure), \
                 patch.object(image_output, "MAX_IMAGE_EDGE", 100), \
                 patch.object(Image.Image, "resize", side_effect=resize, autospec=True):
                if failure:
                    with patch.object(Image.Image, "save", side_effect=RuntimeError("synthetic")):
                        with self.assertRaisesRegex(RuntimeError, "synthetic"):
                            encode_image(image)
                else:
                    self.assertTrue(encode_image(image))
            self.assertEqual(image.getpixel((0, 0)), (255, 255, 255))
            self.assertTrue(created)
            for temporary in created:
                with self.assertRaises(ValueError):
                    temporary.getpixel((0, 0))
            image.close()

    def test_owned_large_canvas_is_closed_before_allocator_reclamation(self):
        image = Image.new("RGB", (2000, 1000), "white")

        def trim(pad):
            self.assertEqual(pad, 0)
            with self.assertRaises(ValueError):
                image.getpixel((0, 0))
            return 0

        native = Mock(side_effect=trim)
        with patch.object(image_output, "_malloc_trim", return_value=native):
            blob = visuals._bytes(image)
        native.assert_called_once_with(0)
        with Image.open(io.BytesIO(blob)) as result:
            self.assertEqual(result.size, (2000, 1000))

    def test_owned_canvas_closes_when_encoding_fails(self):
        image = Image.new("RGB", (80, 80), "white")
        with patch.object(Image.Image, "save", side_effect=RuntimeError("synthetic")):
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                visuals._bytes(image)
        with self.assertRaises(ValueError):
            image.getpixel((0, 0))

    def test_small_canvas_skips_native_reclamation(self):
        with patch.object(image_output, "_malloc_trim") as load:
            visuals._bytes(Image.new("RGB", (80, 80), "white"))
        load.assert_not_called()

    def test_optional_allocator_failure_does_not_hide_encoded_image(self):
        for native in (None, Mock(side_effect=OSError("unavailable"))):
            with self.subTest(native=native), patch.object(image_output, "_malloc_trim", return_value=native):
                blob = visuals._bytes(Image.new("RGB", (2000, 1000), "white"))
            self.assertTrue(blob)

    def test_platform_without_malloc_trim_does_not_load_a_native_library(self):
        image_output._malloc_trim.cache_clear()
        self.addCleanup(image_output._malloc_trim.cache_clear)
        with patch.object(image_output.sys, "platform", "win32"), patch.object(image_output.ctypes, "CDLL") as load:
            self.assertIsNone(image_output._malloc_trim())
        load.assert_not_called()

    def test_linux_without_exported_trim_symbol_is_supported(self):
        image_output._malloc_trim.cache_clear()
        self.addCleanup(image_output._malloc_trim.cache_clear)
        with patch.object(image_output.sys, "platform", "linux"), \
             patch.object(image_output.ctypes, "CDLL", return_value=SimpleNamespace()):
            self.assertIsNone(image_output._malloc_trim())

    def test_reclamation_waits_until_drawing_handle_leaves_scope(self):
        drawing_handles = []

        @image_output.reclaim_after_render
        def render():
            image = Image.new("RGB", (2000, 1000), "white")
            draw = ImageDraw.Draw(image)
            drawing_handles.append(weakref.ref(draw))
            return visuals._bytes(image)

        def trim(pad):
            self.assertIsNone(drawing_handles[0]())
            return 1

        native = Mock(side_effect=trim)
        with patch.object(image_output, "_malloc_trim", return_value=native):
            self.assertTrue(render())
        native.assert_called_once_with(0)

    def test_nested_rendering_reclaims_once_and_error_does_not_leak_scope(self):
        @image_output.reclaim_after_render
        def inner():
            return visuals._bytes(Image.new("RGB", (2000, 1000), "white"))

        @image_output.reclaim_after_render
        def outer(fail=False):
            blob = inner()
            if fail:
                raise RuntimeError("synthetic")
            return blob

        native = Mock(return_value=1)
        with patch.object(image_output, "_malloc_trim", return_value=native):
            self.assertTrue(outer())
            with self.assertRaisesRegex(RuntimeError, "synthetic"):
                outer(True)
            self.assertTrue(outer())
        self.assertEqual(native.call_count, 3)

    def test_concurrent_rendering_keeps_each_threads_canvas_budget(self):
        ready = Barrier(2)

        @image_output.reclaim_after_render
        def render(width):
            blob = visuals._bytes(Image.new("RGB", (width, 1000), "white"))
            ready.wait(timeout=5)
            return blob

        with patch.object(image_output, "_release_image_memory") as reclaim, ThreadPoolExecutor(max_workers=2) as workers:
            self.assertTrue(all(workers.map(render, (2000, 3000))))
        self.assertEqual(sorted(c.args[0] for c in reclaim.call_args_list), [2000000, 3000000])

    def test_upload_budget_rejects_before_network(self):
        asyncio.run(self._reject())

    async def _reject(self):
        with self.assertRaises(ValueError):
            await _upload_image(None, "fake", b"x" * (MAX_IMAGE_BYTES + 1), True)

    def test_upload_timeout_is_scoped_and_session_reused(self):
        class FakeSdk(BotHttp):
            def __init__(self):
                self.timeout = 5
                self.seen = []
                self.session = object()
                self._session = SimpleNamespace(closed=False, close=AsyncMock())

            async def check_session(self):
                pass

            async def request(self, route, **kwargs):
                await asyncio.sleep(0)
                self.seen.append((self.timeout, self.session, route.path, kwargs["json"]["srv_send_msg"]))
                return {"file_info": "safe"}

        sdk = FakeSdk()
        async def run():
            api = SimpleNamespace(_http=sdk)
            await asyncio.gather(_upload_image(api, "fake1", b"image", True),
                                 _upload_image(api, "fake2", b"image", False))
        asyncio.run(run())
        self.assertEqual(sdk.timeout, 5)
        self.assertEqual(len(sdk.seen), 2)
        self.assertTrue(all(t == 30 and session is sdk.session and send is False for t, session, _, send in sdk.seen))
        sdk._session.close.assert_not_called()
        # An upload cancellation also must not close the SDK-owned session.
        with patch.object(FakeSdk, "request", side_effect=asyncio.CancelledError):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(_upload_image(SimpleNamespace(_http=sdk), "fake", b"image", True))
        sdk._session.close.assert_not_called()
        sdk._session = None  # the synthetic owner has no actual aiohttp loop


if __name__ == "__main__":
    unittest.main()
