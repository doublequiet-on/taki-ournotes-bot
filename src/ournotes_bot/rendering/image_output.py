# L3
# Input: 内部 Pillow Image.Image、可选视图尺寸／质量档；close_input 明确是否接管画布，字节上限共享。
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
from dataclasses import dataclass

from PIL import Image

MAX_IMAGE_BYTES = 3_000_000  # base64 <= 4 MB before small JSON envelope
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


class ImageBudgetError(ValueError):
    """The native layout cannot fit its configured dimensions or byte budget."""


@dataclass(frozen=True)
class EncodingAttempt:
    format: str
    parameters: tuple
    bytes: int | None
    failure: str | None = None


@dataclass(frozen=True)
class EncodedImage:
    data: bytes
    format: str
    mime: str
    size: tuple[int, int]
    attempts: tuple[EncodingAttempt, ...]


def check_geometry(size, *, max_pixels=None, max_edge=None):
    width, height = size
    if max(size) > (MAX_IMAGE_EDGE if max_edge is None else max_edge) or width * height > (MAX_IMAGE_PIXELS if max_pixels is None else max_pixels):
        raise ImageBudgetError("Readable canvas exceeds the dimension budget")


def encode_image(image: Image.Image, *, close_input: bool = False,
                 preserve_size: bool = False, max_pixels: int | None = None,
                 max_edge: int | None = None, quality_steps: tuple[int, ...] = (95, 90, 88),
                 strategy: str = "legacy") -> bytes:
    """Compatible bytes API; PNG-first requires an explicit strategy argument."""
    return encode_image_result(image, close_input=close_input, preserve_size=preserve_size,
                               max_pixels=max_pixels, max_edge=max_edge,
                               quality_steps=quality_steps, strategy=strategy).data


def encode_image_result(image: Image.Image, *, close_input: bool = False,
                 preserve_size: bool = False, max_pixels: int | None = None,
                 max_edge: int | None = None, quality_steps: tuple[int, ...] = (95, 90, 88),
                 strategy: str = "legacy") -> EncodedImage:
    """Borrow by default; per-render dimensions never relax the shared byte cap."""
    original = image.size
    working = image
    attempts = []
    def result(blob, format, size):
        return EncodedImage(blob, format, "image/png" if format == "PNG" else "image/jpeg", size, tuple(attempts))
    try:
        if strategy not in {"legacy", "png-first"}:
            raise ValueError("Unknown encoding strategy")
        if strategy == "png-first" and not preserve_size:
            raise ValueError("PNG-first requires a native-size canvas")
        if preserve_size:
            check_geometry(original, max_pixels=max_pixels, max_edge=max_edge)
        if strategy == "png-first":
            with io.BytesIO() as out:
                try:
                    image.save(out, format="PNG")
                except OSError as exc:
                    attempts.append(EncodingAttempt("PNG", (), None, type(exc).__name__))
                    raise
                blob = out.getvalue()
            attempts.append(EncodingAttempt("PNG", (), len(blob), "byte_budget" if len(blob) > MAX_IMAGE_BYTES else None))
            if len(blob) <= MAX_IMAGE_BYTES:
                return result(blob, "PNG", original)
            del blob
        if image.mode != "RGB":
            working = Image.new("RGB", image.size, "white")
            if "A" in image.getbands():
                with image.getchannel("A") as alpha:
                    working.paste(image, mask=alpha)
            else:
                working.paste(image)
        factor = min(1, (MAX_IMAGE_EDGE if max_edge is None else max_edge) / max(working.size),
                     math.sqrt((MAX_IMAGE_PIXELS if max_pixels is None else max_pixels) / (working.width * working.height)))
        if preserve_size and factor < 1:
            raise ImageBudgetError("Readable canvas exceeds the dimension budget")
        if factor < 1:
            resized = working.resize((max(1, int(working.width * factor)), max(1, int(working.height * factor))), Image.Resampling.LANCZOS)
            if working is not image:
                working.close()
            working = resized
        for attempt in range(8):
            for quality, subsampling in (tuple((q, 0) for q in quality_steps) if preserve_size else ((95, 0), (88, 0), (82, 1), (76, 1))):
                with io.BytesIO() as out:
                    optimized = True
                    try:
                        working.save(out, format="JPEG", quality=quality, subsampling=subsampling, optimize=True)
                    except OSError as exc:
                        attempts.append(EncodingAttempt("JPEG", (("quality",quality),("subsampling",subsampling),("optimize",True)), None, type(exc).__name__))
                        # Some libjpeg builds under-allocate the optimized buffer.
                        out.seek(0)
                        out.truncate(0)
                        optimized = False
                        try:
                            working.save(out, format="JPEG", quality=quality, subsampling=subsampling, optimize=False)
                        except OSError as retry_exc:
                            attempts.append(EncodingAttempt("JPEG", (("quality",quality),("subsampling",subsampling),("optimize",False)), None, type(retry_exc).__name__))
                            raise
                    blob = out.getvalue()
                attempts.append(EncodingAttempt("JPEG", (("quality",quality),("subsampling",subsampling),("optimize",optimized)), len(blob), "byte_budget" if len(blob) > MAX_IMAGE_BYTES else None))
                if len(blob) <= MAX_IMAGE_BYTES:
                    logger.debug("图片编码 %sx%s -> %sx%s quality=%d bytes=%d", *original, *working.size, quality, len(blob))
                    return result(blob, "JPEG", working.size)
                last_size = len(blob)
                del blob
            if preserve_size:
                raise ImageBudgetError("Readable canvas exceeds the byte budget")
            # Scaling is uniform; never crop content, drop rows or split sends.
            ratio = min(.85, math.sqrt(MAX_IMAGE_BYTES / last_size) * .95)
            resized = working.resize((max(1, int(working.width * ratio)), max(1, int(working.height * ratio))), Image.Resampling.LANCZOS)
            if working is not image:
                working.close()
            working = resized
        raise ValueError("Image could not fit the output budget")
    except Exception as exc:
        exc.encoding_attempts = tuple(attempts)
        raise
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
