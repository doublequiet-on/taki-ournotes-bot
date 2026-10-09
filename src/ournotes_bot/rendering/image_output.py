# L3
# Input: 内部 Pillow Image.Image；close_input 明确是否接管画布。
# Output: encode_image 返回预算内 JPEG bytes；无法满足预算时抛 ValueError；接管的画布在成功／失败后关闭。
# Pos: Rendering / Core 的统一整图编码器；见 L2-2-Core.md。
# Effects/Dependencies: CPU／内存缩放与编码；临时图像及时关闭，Linux 大图在绘图作用域结束后尝试归还空闲页；不可用时降级；不落盘或上传图片。

"""Transport-independent image encoding budget for every query renderer.

These are Taki's conservative upload budgets, not claims about QQ hard limits.
Keep the whole image and prefer JPEG quality reduction before further resizing.
"""
import io
import logging
import math
import ctypes
import sys
import threading
from functools import lru_cache, wraps

from PIL import Image

MAX_IMAGE_BYTES = 1_500_000  # base64 <= 2 MB before small JSON envelope
MAX_IMAGE_EDGE = 8192
MAX_IMAGE_PIXELS = 12_000_000
RECLAIM_MIN_PIXELS = 2_000_000
logger = logging.getLogger("ournotes_bot.image_output")
_render_state = threading.local()


@lru_cache(maxsize=1)
def _malloc_trim():
    if sys.platform != "linux":
        return None
    try:
        trim = ctypes.CDLL(None).malloc_trim
        trim.argtypes = [ctypes.c_size_t]
        trim.restype = ctypes.c_int
        return trim
    except (AttributeError, OSError):
        return None


def _release_image_memory(pixels: int) -> None:
    if pixels < RECLAIM_MIN_PIXELS:
        return
    try:
        trim = _malloc_trim()
        if trim is not None:
            trim(0)
    except Exception:
        # Reclamation is optional and must never hide an image or its error.
        pass


def reclaim_after_render(render):
    """Wait for drawing handles to leave scope before reclaiming large canvases."""
    @wraps(render)
    def wrapped(*args, **kwargs):
        depth = getattr(_render_state, "depth", 0)
        if not depth:
            _render_state.pixels = 0
        _render_state.depth = depth + 1
        try:
            return render(*args, **kwargs)
        finally:
            _render_state.depth = depth
            if not depth:
                pixels = _render_state.pixels
                _render_state.pixels = 0
                _release_image_memory(pixels)
    return wrapped


def encode_image(image: Image.Image, *, close_input: bool = False) -> bytes:
    """Borrow the input by default; owned renderer canvases are always released."""
    original = image.size
    working = image
    try:
        if image.mode != "RGB":
            working = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                with image.getchannel("A") as alpha:
                    working.paste(image, mask=alpha)
            else:
                working.paste(image)
        factor = min(1, MAX_IMAGE_EDGE / max(working.size), math.sqrt(MAX_IMAGE_PIXELS / (working.width * working.height)))
        if factor < 1:
            resized = working.resize((max(1, int(working.width * factor)), max(1, int(working.height * factor))), Image.Resampling.LANCZOS)
            if working is not image:
                working.close()
            working = resized
        for attempt in range(8):
            for quality, subsampling in ((95, 0), (88, 0), (82, 1), (76, 1)):
                with io.BytesIO() as out:
                    try:
                        working.save(out, format="JPEG", quality=quality, subsampling=subsampling, optimize=True)
                    except OSError:
                        # Some libjpeg builds under-allocate the optimized buffer.
                        out.seek(0)
                        out.truncate(0)
                        working.save(out, format="JPEG", quality=quality, subsampling=subsampling, optimize=False)
                    blob = out.getvalue()
                if len(blob) <= MAX_IMAGE_BYTES:
                    logger.debug("图片编码 %sx%s -> %sx%s quality=%d bytes=%d", *original, *working.size, quality, len(blob))
                    return blob
            # Scaling is uniform; never crop content, drop rows or split sends.
            ratio = min(.85, math.sqrt(MAX_IMAGE_BYTES / len(blob)) * .95)
            resized = working.resize((max(1, int(working.width * ratio)), max(1, int(working.height * ratio))), Image.Resampling.LANCZOS)
            if working is not image:
                working.close()
            working = resized
        raise ValueError("Image could not fit the output budget")
    finally:
        if working is not image:
            working.close()
        if close_input:
            image.close()
            pixels = original[0] * original[1]
            if getattr(_render_state, "depth", 0):
                _render_state.pixels = max(_render_state.pixels, pixels)
            else:
                _release_image_memory(pixels)
