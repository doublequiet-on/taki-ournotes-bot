"""Offline large-image and upload timeout regressions, no QQ credentials."""
import asyncio
import io
import random
import unittest
from unittest.mock import patch, AsyncMock

from PIL import Image
from botpy.http import BotHttp
from ournotes_bot.rendering.image_output import encode_image, MAX_IMAGE_BYTES, MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS
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
